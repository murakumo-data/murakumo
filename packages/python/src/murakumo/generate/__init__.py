"""Generation: compile the model into dbt, DDL and (later) every other artefact.

``generate`` is pure: it returns file contents and never touches disk. ``write`` puts them under
``generated/`` and records what it wrote in ``generated/.murakumo-files.json``, so a later run
removes only files Murakumo created itself. Nothing outside ``generated/`` is ever written.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from murakumo import __version__
from murakumo.diagnostics import Diagnostic
from murakumo.generate import dbt, ddl
from murakumo.generate.plan import Plan, build_plan
from murakumo.generate.targets import Target, target_for
from murakumo.ir.model import Entity, Layer
from murakumo.manifest import Warehouse
from murakumo.project import Project

GENERATED_DIR = "generated"
INDEX_FILE = ".murakumo-files.json"


@dataclass
class Output:
    plan: Plan
    files: dict[str, str] = field(default_factory=dict)  # path under generated/ -> content
    owners: dict[str, list[UUID]] = field(default_factory=dict)  # path -> the entities it is generated from
    ddl: dict[UUID, str] = field(default_factory=dict)  # entity -> its CREATE TABLE statement

    @property
    def diagnostics(self) -> list[Diagnostic]:
        return self.plan.diagnostics


def generate(project: Project, warehouse: Warehouse | str | None = None) -> Output:
    target: Target = target_for(warehouse or project.manifest.targets.warehouse.type)
    plan = build_plan(project, target)
    output = Output(plan)
    for path, content in dbt.render(plan).items():
        output.files[f"dbt/{path}"] = content
    statements = ddl.DDL(plan)
    for path, content in ddl.render(plan, statements).items():
        output.files[f"ddl/{target.warehouse.value}/{path}"] = content
    output.ddl = statements.blocks
    output.owners = _owners(plan)
    return output


def _owners(plan: Plan) -> dict[str, list[UUID]]:
    owners: dict[str, list[UUID]] = {}

    def own(path: str, entity: Entity | None) -> None:
        if entity is not None and entity.id not in owners.setdefault(path, []):
            owners[path].append(entity.id)

    for group in plan.sources:
        for table in group.tables:
            own(f"dbt/models/staging/{group.name}/_{group.name}__sources.yml", table.entity)
            own(f"ddl/{plan.target.warehouse.value}/raw.sql", table.entity)
    for model in plan.models:
        own(f"dbt/models/{model.folder}/{model.name}.sql", model.entity)
        own(f"dbt/models/{model.folder}/_{model.folder.rsplit('/', 1)[-1]}__models.yml", model.entity)
        if model.layer not in ("staging", "intermediate") and model.entity.layer is not Layer.RAW:
            own(f"ddl/{plan.target.warehouse.value}/marts.sql", model.entity)
    for snapshot in plan.snapshots:
        own("dbt/snapshots/_snapshots.yml", snapshot.entity)
    return owners


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _previous(folder: Path) -> dict[str, str]:
    index = folder / INDEX_FILE
    try:
        return json.loads(index.read_text(encoding="utf-8")).get("files", {})
    except (OSError, ValueError):
        return {}


@dataclass
class Changes:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    edited_by_hand: list[str] = field(default_factory=list)

    @property
    def any(self) -> bool:
        return bool(self.added or self.changed or self.removed)


def compare(output: Output, root: Path) -> Changes:
    """What writing ``output`` would change under ``generated/``."""
    folder = root / GENERATED_DIR
    previous = _previous(folder)
    changes = Changes()
    for path, content in output.files.items():
        target = folder / path
        if not target.exists():
            changes.added.append(path)
            continue
        current = target.read_text(encoding="utf-8")
        if current != content:
            changes.changed.append(path)
            if path in previous and _digest(current) != previous[path]:
                changes.edited_by_hand.append(path)
    changes.removed = sorted(p for p in previous if p not in output.files and (folder / p).exists())
    return changes


def write(output: Output, root: Path) -> Changes:
    folder = root / GENERATED_DIR
    changes = compare(output, root)
    for path in changes.removed:
        (folder / path).unlink()
    for path in [*changes.added, *changes.changed]:
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output.files[path], encoding="utf-8")
    for path in sorted({str(Path(p).parent) for p in changes.removed}, key=len, reverse=True):
        directory = folder / path
        while directory != folder and directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
            directory = directory.parent
    index = {
        "generated_by": f"murakumo {__version__}",
        "files": {path: _digest(content) for path, content in sorted(output.files.items())},
    }
    (folder / INDEX_FILE).write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    return changes


__all__ = ["Changes", "Output", "compare", "generate", "write"]
