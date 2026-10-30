"""Impact reports: what a model change reaches, found by walking the column lineage.

For each change in a typed diff, the report lists the columns built from what changed (directly
or further down), the tables they belong to, the tests on them, the metrics and schedules that
use those tables, and the generated files and migrations the change brings. A removed or renamed
column is followed in the model as it was, since the new one no longer has it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from murakumo.ir.model import Model
from murakumo.lineage import Lineage, _walk, build_lineage
from murakumo.modeldiff import Change, diff, worst
from murakumo.project import Project


@dataclass
class Impact:
    change: Change
    columns: list[str] = field(default_factory=list)  # entity.column built from what changed
    tables: list[str] = field(default_factory=list)  # every table reached, including the changed one
    tests: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    schedules: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "change": self.change.as_dict(),
            "columns": self.columns,
            "tables": self.tables,
            "tests": self.tests,
            "metrics": self.metrics,
            "schedules": self.schedules,
        }


@dataclass
class Report:
    impacts: list[Impact]
    files: list[str] = field(default_factory=list)  # generated files that change
    migration: list[str] = field(default_factory=list)  # the next migration's changes

    @property
    def worst(self) -> str | None:
        return worst([i.change for i in self.impacts])

    def as_dict(self) -> dict[str, Any]:
        return {
            "worst": self.worst,
            "impacts": [i.as_dict() for i in self.impacts],
            "files": self.files,
            "migration": self.migration,
        }


def _start(change: Change, model: Model, lineage: Lineage) -> list[tuple[str, str]]:
    """The columns a change starts from, in the model the lineage was built from."""
    entity = model.entity(change.detail.get("from", change.entity)) or model.entity(change.entity)
    if entity is None:
        return []
    if change.column:
        name = change.detail.get("from", change.column) if change.kind == "column_renamed" else change.column
        attribute = entity.attribute(name) or entity.attribute(change.column)
        return [(entity.name, attribute.name)] if attribute else []
    return [(entity.name, a.name) for a in entity.attributes]


def _tests(model: Model, columns: set[tuple[str, str]], tables: set[str]) -> list[str]:
    out: set[str] = set()
    for entity in model.entities:
        for attribute in entity.attributes:
            if (entity.name, attribute.name) not in columns:
                continue
            for rule in attribute.tests:
                out.add(f"{rule.type.value} on {entity.name}.{attribute.name}")
            if attribute.key and attribute.key.value == "primary":
                out.add(f"unique, not_null on {entity.name}.{attribute.name}")
        if entity.name in tables:
            out.update(f"{rule.type.value} on {entity.name}" for rule in entity.tests)
    for rel in model.relationships:
        for key in rel.keys:
            if (rel.from_, key.from_) in columns or (rel.to, key.to) in columns:
                out.add(f"relationship {rel.from_}.{key.from_} -> {rel.to}.{key.to}")
    return sorted(out)


def report(project: Project, old: Model, new: Model, changes: list[Change] | None = None) -> Report:
    """The impact of going from ``old`` to ``new`` (``project`` holds ``new``)."""
    from murakumo.schedules import ScheduleError, resolve

    changes = diff(old, new) if changes is None else changes
    lineages = {"old": build_lineage(_with(project, old)), "new": build_lineage(project)}
    try:
        refreshed = {e.name: entry.schedule.name for entry in resolve(project) for e in entry.entities}
    except ScheduleError:
        refreshed = {}
    impacts = []
    for change in changes:
        gone = change.kind in ("column_removed", "entity_removed", "column_renamed", "entity_renamed")
        model, lineage = (old, lineages["old"]) if gone else (new, lineages["new"])
        start = _start(change, model, lineage)
        reached, _ = _walk(lineage, start, up=False)
        downstream = sorted(f"{e}.{c}" for e, c in reached if (e, c) not in start)
        tables = sorted({e for e, _ in reached} | {change.entity})
        columns = set(reached)
        metrics = sorted(m.name for m in new.metrics if m.entity in tables)
        schedules = sorted({refreshed[t] for t in tables if t in refreshed})
        impacts.append(Impact(change, downstream, tables, _tests(model, columns, set(tables)), metrics, schedules))
    result = Report(impacts)
    try:
        from murakumo.generate import compare, generate
        from murakumo.migrate import plan_migration

        changed = compare(generate(project), project.root)
        result.files = sorted([*changed.added, *changed.changed, *changed.removed])
        pending = plan_migration(project)
        if not pending.baseline:
            result.migration = [c.summary for c in pending.changes]
    except Exception:  # noqa: BLE001 - a model with errors still has an impact worth showing
        pass
    return result


def _with(project: Project, model: Model) -> Project:
    """The project with another version of its model, to follow lineage that no longer exists."""
    from dataclasses import replace

    return replace(project, model=model)


def markdown(result: Report, against: str) -> str:
    """The report for a pull request's summary page."""
    if not result.impacts:
        return f"### Model changes since `{against}`\n\nNone.\n"
    icons = {"additive": "🟢", "compatible": "🟡", "breaking": "🔴"}
    lines = [f"### Model changes since `{against}`", ""]
    if result.worst == "breaking":
        lines += ["> **Breaking changes**: something that reads these tables may fail or change meaning.", ""]
    lines += ["| | Change | Reaches |", "| --- | --- | --- |"]
    for impact in result.impacts:
        reach = []
        others = [t for t in impact.tables if t != impact.change.entity]
        if impact.columns:
            reach.append(f"{len(impact.columns)} column(s) in {', '.join(others) or impact.change.entity}")
        if impact.tests:
            reach.append(f"{len(impact.tests)} test(s)")
        if impact.schedules:
            reach.append("schedule " + ", ".join(impact.schedules))
        summary = impact.change.summary.replace("|", "\\|")
        lines.append(
            f"| {icons[impact.change.cls]} {impact.change.cls} | {summary} | {'; '.join(reach) or 'only itself'} |"
        )
    if result.migration:
        lines += ["", "**Migration needed:** " + "; ".join(result.migration)]
    if result.files:
        lines += ["", f"{len(result.files)} generated file(s) change."]
    return "\n".join(lines) + "\n"


__all__ = ["Impact", "Report", "markdown", "report"]
