"""Schedules: which tables are refreshed when.

Schedules are listed in murakumo.yaml under ``orchestration.schedules``. Each has a cron time and
a selection of tables:

    domain:sales      every entity in the sales domain
    layer:marts       every entity in the marts layer
    source:tpch_erp   every raw table of that source
    fct_trip          one entity
    +fct_trip         ... and everything it is built from
    tpch_erp+ ...     a trailing + adds everything built from it

A schedule with no selection refreshes everything no other schedule picks. A project with no
schedules is refreshed once a day, at 06:00 in the project's time zone.

Each schedule becomes a dbt selector (``generated/dbt/selectors.yml``) that names its models,
so every orchestrator runs the same ``dbt build --selector <schedule>``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from murakumo.cron import describe, next_runs
from murakumo.ir.model import Entity, Layer
from murakumo.manifest import Schedule
from murakumo.manifest_edit import ManifestEditError, read_manifest, write_section
from murakumo.project import Project

DEFAULT = Schedule(name="daily", cron="0 6 * * *", description="Everything, once a day (no schedules are set)")


class ScheduleError(Exception):
    pass


@dataclass
class Resolved:
    schedule: Schedule
    entities: list[Entity]
    implicit: bool = False  # the default schedule of a project with none

    @property
    def catch_all(self) -> bool:
        return not self.schedule.select


def effective(project: Project) -> tuple[list[Schedule], bool]:
    """The project's schedules, or the default one; and whether it is the default."""
    schedules = project.manifest.orchestration.schedules
    return (list(schedules), False) if schedules else ([DEFAULT], True)


def _dependencies(project: Project) -> dict[str, set[str]]:
    """Entity -> the entities it is built from (lower-case names), from its mappings."""
    from murakumo.lineage import mapping_reads

    upstream: dict[str, set[str]] = defaultdict(set)
    for entity in project.model.entities:
        for mapping in entity.mappings:
            for source, _ in mapping_reads(project, mapping):
                if source.lower() != entity.name.lower():
                    upstream[entity.name.lower()].add(source.lower())
    return upstream


def _closure(start: set[str], links: dict[str, set[str]]) -> set[str]:
    seen, queue = set(start), list(start)
    while queue:
        for nxt in links.get(queue.pop(), set()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def select(project: Project, item: str) -> set[str]:
    """The entities (lower-case names) one selection item picks. Raises ScheduleError."""
    model = project.model
    parents = item.startswith("+")
    children = item.endswith("+")
    core = item.strip("+").strip()
    method, sep, value = core.partition(":")
    if not sep:
        method, value = "entity", core
    lowered = value.lower()
    if method == "domain":
        if project.manifest.domain(value) is None:
            raise ScheduleError(f"{item}: there is no domain {value!r}")
        picked = {e.name.lower() for e in model.entities if (e.domain or "").lower() == lowered}
    elif method == "layer":
        if lowered not in {layer.value for layer in Layer}:
            raise ScheduleError(
                f"{item}: there is no layer {value!r}; use raw, staging, intermediate, marts or semantic"
            )
        picked = {e.name.lower() for e in model.entities if e.layer.value == lowered}
    elif method == "source":
        known = {s.name.lower() for s in model.sources} | {s.name.lower() for s in project.manifest.sources}
        if lowered not in known:
            raise ScheduleError(f"{item}: there is no source {value!r}")
        picked = {e.name.lower() for e in model.entities if (e.source or "").lower() == lowered}
    elif method == "entity":
        if model.entity(value) is None:
            raise ScheduleError(f"{item}: there is no entity {value!r}")
        picked = {lowered}
    else:
        raise ScheduleError(f"{item}: use domain:, layer:, source: or an entity name")
    upstream = _dependencies(project)
    result = set(picked)
    if parents:
        result |= _closure(picked, upstream)
    if children:
        downstream: dict[str, set[str]] = defaultdict(set)
        for entity, sources in upstream.items():
            for source in sources:
                downstream[source].add(entity)
        result |= _closure(picked, downstream)
    return result


def resolve(project: Project) -> list[Resolved]:
    """Each schedule with the entities it refreshes. Raises ScheduleError for a bad selection."""
    schedules, implicit = effective(project)
    by_name = {e.name.lower(): e for e in project.model.entities}
    picked: dict[str, set[str]] = {}
    for schedule in schedules:
        names: set[str] = set()
        for item in schedule.select:
            names |= select(project, item)
        picked[schedule.name] = names
    chosen = set().union(*picked.values()) if picked else set()
    result = []
    for schedule in schedules:
        names = picked[schedule.name] if schedule.select else set(by_name) - chosen
        entities = [by_name[n] for n in sorted(names) if n in by_name]
        result.append(Resolved(schedule, entities, implicit))
    return result


def problems(project: Project) -> list[tuple[str, str, str]]:
    """(code, level, message) for schedules that cannot work, or leave tables out."""
    out: list[tuple[str, str, str]] = []
    schedules = project.manifest.orchestration.schedules
    names = [s.name.lower() for s in schedules]
    for name in sorted({n for n in names if names.count(n) > 1}):
        out.append(("MK602", "error", f"schedule name {name!r} is used more than once"))
    catch_alls = [s.name for s in schedules if not s.select]
    if len(catch_alls) > 1:
        out.append(
            ("MK603", "error", f"only one schedule can leave select empty (refresh the rest): {', '.join(catch_alls)}")
        )
    for schedule in schedules:
        for item in schedule.select:
            try:
                select(project, item)
            except ScheduleError as exc:
                out.append(("MK601", "error", f"schedule {schedule.name}: {exc}"))
    if schedules and not catch_alls and not any(code == "MK601" for code, _, _ in out):
        covered = {e.name for r in resolve(project) for e in r.entities}
        missing = [e.name for e in project.model.entities if e.name not in covered]
        if missing:
            shown = ", ".join(missing[:5]) + (" and more" if len(missing) > 5 else "")
            out.append(
                ("MK604", "warning", f"no schedule refreshes {shown}; add one with an empty select for the rest")
            )
    return out


def summary(project: Project) -> dict[str, Any]:
    """Schedules as the canvas shows them, with their times in words and their next runs."""
    timezone = project.manifest.orchestration.timezone
    try:
        resolved = resolve(project)
    except ScheduleError:
        resolved = None
    schedules, implicit = effective(project)
    rows = []
    for index, schedule in enumerate(schedules):
        entry = resolved[index] if resolved else None
        rows.append(
            {
                "name": schedule.name,
                "cron": schedule.cron,
                "select": schedule.select,
                "description": schedule.description,
                "when": describe(schedule.cron, timezone),
                "next_runs": [run.isoformat(timespec="minutes") for run in next_runs(schedule.cron, timezone)],
                "entities": [e.name for e in entry.entities] if entry else [],
                "rest": not schedule.select,
            }
        )
    orchestrator = project.manifest.generation.orchestrator.value
    return {
        "timezone": timezone,
        "implicit": implicit,
        "schedules": rows,
        "orchestrator": orchestrator,
        "pipelines": f"generated/orchestration/{orchestrator}/",
    }


def dbt_models(plan, entities: list[Entity]) -> list[str]:
    """The dbt models and snapshots generated for these entities."""
    models: dict[str, list[str]] = defaultdict(list)
    for model in plan.models:
        models[model.entity.name.lower()].append(model.name)
    for snapshot in plan.snapshots:
        if snapshot.entity is not None:
            models[snapshot.entity.name.lower()].append(snapshot.name)
    return sorted({name for e in entities for name in models.get(e.name.lower(), [])})


def dbt_selectors(project: Project, plan) -> dict[str, Any]:
    """``selectors.yml``: one selector per schedule, naming the dbt models of its entities."""
    timezone = project.manifest.orchestration.timezone
    selectors = []
    resolved_all = resolve(project)
    for entry in resolved_all:
        names = dbt_models(plan, entry.entities)
        selector: dict[str, Any] = {
            "name": entry.schedule.name,
            "description": entry.schedule.description or describe(entry.schedule.cron, timezone),
        }
        # Each schedule runs the tests whose tables it builds (cautious): a relationship test whose
        # other table belongs to another schedule would otherwise fail before that table exists.
        # The schedule that refreshes the rest runs every relationship test, so all still run.
        union: list[dict[str, Any]] = [
            {"method": "fqn", "value": name, "indirect_selection": "cautious"} for name in names
        ]
        if entry.catch_all and len(resolved_all) > 1:
            union.append({"method": "test_name", "value": "relationships"})
        if not union:  # dbt needs a definition; this one selects nothing
            union = [{"method": "fqn", "value": "murakumo_selects_nothing"}]
        selector["definition"] = {"union": union}
        selectors.append(selector)
    return {"selectors": selectors}


# Editing ----------------------------------------------------------------------------------------


def _entries(schedules: list[Schedule]) -> list[dict[str, Any]]:
    entries = []
    for schedule in schedules:
        entry: dict[str, Any] = {"name": schedule.name, "cron": schedule.cron}
        if schedule.select:
            entry["select"] = schedule.select
        if schedule.description:
            entry["description"] = schedule.description
        entries.append(entry)
    return entries


def _write(project: Project, schedules: list[Schedule] | None = None, timezone: str | None = None) -> None:
    """Write the schedules, or the time zone, keeping the rest of murakumo.yaml as it is."""
    current = project.manifest.orchestration
    has_section = isinstance(read_manifest(project.root).get("orchestration"), dict)
    try:
        if not has_section:
            value = {"timezone": timezone or current.timezone, "schedules": _entries(schedules or current.schedules)}
            project.manifest = write_section(project.root, ("orchestration",), value)
        elif timezone is not None:
            project.manifest = write_section(project.root, ("orchestration", "timezone"), timezone)
        else:
            project.manifest = write_section(project.root, ("orchestration", "schedules"), _entries(schedules or []))
    except ManifestEditError as exc:
        raise ScheduleError(str(exc)) from None


def _validated(project: Project, schedule: Schedule) -> None:
    for item in schedule.select:
        select(project, item)


def add_schedule(
    project: Project, name: str, cron: str, select_items: list[str] | None = None, description: str | None = None
) -> Schedule:
    if project.manifest.orchestration.schedule(name):
        raise ScheduleError(f"schedule {name!r} already exists")
    try:
        schedule = Schedule(name=name, cron=cron, select=select_items or [], description=description or None)
    except ValueError as exc:
        raise ScheduleError(_message(exc)) from None
    _validated(project, schedule)
    current = project.manifest.orchestration.schedules
    if not schedule.select and any(not s.select for s in current):
        raise ScheduleError("another schedule already refreshes the rest; give this one a selection")
    _write(project, [*current, schedule])
    return schedule


def update_schedule(project: Project, name: str, changes: dict[str, Any]) -> Schedule:
    schedule = project.manifest.orchestration.schedule(name)
    if schedule is None:
        raise ScheduleError(f"schedule {name!r} does not exist")
    unknown = set(changes) - {"name", "cron", "select", "description"}
    if unknown:
        raise ScheduleError(f"cannot change {', '.join(sorted(unknown))} on a schedule")
    data = schedule.model_dump() | changes
    if data.get("description") == "":
        data["description"] = None
    try:
        updated = Schedule(**data)
    except ValueError as exc:
        raise ScheduleError(_message(exc)) from None
    others = [s for s in project.manifest.orchestration.schedules if s is not schedule]
    if updated.name.lower() != schedule.name.lower() and any(s.name.lower() == updated.name.lower() for s in others):
        raise ScheduleError(f"schedule {updated.name!r} already exists")
    if not updated.select and any(not s.select for s in others):
        raise ScheduleError("another schedule already refreshes the rest; give this one a selection")
    _validated(project, updated)
    _write(project, [updated if s is schedule else s for s in project.manifest.orchestration.schedules])
    return updated


def remove_schedule(project: Project, name: str) -> Schedule:
    schedule = project.manifest.orchestration.schedule(name)
    if schedule is None:
        raise ScheduleError(f"schedule {name!r} does not exist")
    _write(project, [s for s in project.manifest.orchestration.schedules if s is not schedule])
    return schedule


def set_timezone(project: Project, timezone: str) -> str:
    from murakumo.cron import CronError, zone

    try:
        zone(timezone)
    except CronError as exc:
        raise ScheduleError(str(exc)) from None
    _write(project, None, timezone)
    return timezone


def _message(exc: ValueError) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        problem = errors()[0]
        return str(problem["msg"]).removeprefix("Value error, ")
    return str(exc)


__all__ = [
    "DEFAULT",
    "Resolved",
    "ScheduleError",
    "add_schedule",
    "dbt_selectors",
    "effective",
    "problems",
    "remove_schedule",
    "resolve",
    "select",
    "set_timezone",
    "summary",
    "update_schedule",
]
