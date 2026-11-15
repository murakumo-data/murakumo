"""Slim CI: check only what a change reaches.

``murakumo verify --changed-since origin/main`` builds and tests the tables that changed since
that commit, everything built from them, and what they are built from (a scratch warehouse is
empty, so a table's sources are built first). Tables on other branches of the model are left out,
which is most of a large project on most pull requests.

What changed is read from the model, not from files: a renamed column, a new mapping or a changed
test each name the tables they reach, through lineage (``impact.py``). A change to murakumo.yaml
can reach anything (naming, the warehouse, a modelling switch), so then everything is built.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from murakumo.project import MANIFEST_FILE, Project


class SlimError(Exception):
    pass


@dataclass
class Selection:
    against: str
    everything: bool = False  # build it all: the reason is in ``why``
    why: str = ""
    tables: list[str] = field(default_factory=list)  # changed, and reached by the changes
    models: list[str] = field(default_factory=list)  # their dbt models and snapshots

    @property
    def nothing(self) -> bool:
        return not self.everything and not self.models

    @property
    def select(self) -> str | None:
        """dbt's --select: each model with everything it is built from."""
        return None if self.everything else " ".join(f"+{name}" for name in self.models)


def changed_since(project: Project, ref: str) -> Selection:
    from murakumo import gitsync, impact
    from murakumo.generate.plan import build_plan
    from murakumo.generate.targets import target_for
    from murakumo.schedules import dbt_models

    try:
        w = gitsync.where(project)
        if not gitsync._exists(w, ref):
            raise SlimError(f"no commit {ref!r} here: fetch it first (git fetch), or name a branch that exists")
        changed = gitsync._git(w.root, "diff", "--quiet", ref, "--", w.path(MANIFEST_FILE), check=False).returncode != 0
        old = gitsync.model_at(project, w, ref)
    except gitsync.GitError as exc:
        raise SlimError(str(exc)) from None
    if changed:
        return Selection(ref, everything=True, why=f"{MANIFEST_FILE} changed, which can reach every table")
    if not old.entities:
        return Selection(ref, everything=True, why=f"the project has no model at {ref}")
    result = impact.report(project, old, project.model)
    reached = {table for found in result.impacts for table in found.tables}
    entities = [e for e in project.model.entities if e.name in reached]
    plan = build_plan(project, target_for(project.manifest.targets.warehouse.type))
    models = dbt_models(plan, entities)
    # A changed raw table has no model of its own: its staging model stands for it.
    staging = {m.entity.name: m.name for m in plan.models if m.layer == "staging"}
    models = sorted(set(models) | {staging[e.name] for e in entities if e.name in staging})
    return Selection(ref, tables=sorted(e.name for e in entities), models=models)


__all__ = ["Selection", "SlimError", "changed_since"]
