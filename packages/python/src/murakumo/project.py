"""Reading and writing a project on disk: the manifest plus the model as YAML.

Layout under the project root::

    murakumo.yaml               the manifest
    model/sources/*.yaml        one source system per file
    model/entities/**/*.yaml    one entity per file, in any folder structure
    model/relationships.yaml    every relationship, in one list
    model/metrics.yaml          every metric, in one list

Loading never stops at the first bad file: every problem becomes a diagnostic, and the
objects that did parse are still returned, so ``murakumo validate`` can report everything at once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

import yaml
from pydantic import BaseModel, ValidationError

from murakumo.diagnostics import Diagnostic, error, warning
from murakumo.ir.model import Entity, Metric, Model, Relationship, SourceSystem
from murakumo.manifest import MANIFEST_FILE, Manifest

MODEL_DIR = "model"
SOURCES_DIR = f"{MODEL_DIR}/sources"
ENTITIES_DIR = f"{MODEL_DIR}/entities"
RELATIONSHIPS_FILE = f"{MODEL_DIR}/relationships.yaml"
METRICS_FILE = f"{MODEL_DIR}/metrics.yaml"

T = TypeVar("T", bound=BaseModel)


@dataclass
class Project:
    root: Path
    manifest: Manifest
    model: Model = field(default_factory=Model)
    # Which file each top-level object came from, so saving keeps the user's folder structure.
    origins: dict[UUID, Path] = field(default_factory=dict)


@dataclass
class LoadResult:
    project: Project | None
    diagnostics: list[Diagnostic]
    # Objects that had no id in YAML and were given one while loading.
    missing_ids: int = 0


def find_root(start: Path) -> Path | None:
    """The nearest folder at or above ``start`` that holds a manifest."""
    start = start.resolve()
    for folder in (start, *start.parents):
        if (folder / MANIFEST_FILE).is_file():
            return folder
    return None


# Loading ----------------------------------------------------------------------------------------


def _read_yaml(path: Path, diagnostics: list[Diagnostic]) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else None
        problem = getattr(exc, "problem", None) or str(exc)
        diagnostics.append(error("MK001", f"invalid YAML: {problem}", path, where))
    except OSError as exc:
        diagnostics.append(error("MK001", f"cannot read file: {exc.strerror}", path))
    return None


def _format_loc(loc: tuple[int | str, ...], data: Any) -> str:
    """Turn a Pydantic error location into a readable path, using names instead of list indexes."""
    parts: list[str] = []
    node = data
    for item in loc:
        if isinstance(item, int):
            label: Any = item
            node = node[item] if isinstance(node, list) and item < len(node) else None
            if isinstance(node, dict) and node.get("name"):
                label = node["name"]
            if parts:
                parts[-1] += f"[{label}]"
            else:
                parts.append(f"[{label}]")
        else:
            node = node.get(item) if isinstance(node, dict) else None
            parts.append(str(item))
    return ".".join(parts)


def _parse(model: type[T], data: Any, path: Path, diagnostics: list[Diagnostic], where: str = "") -> T | None:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        for err in exc.errors():
            loc = _format_loc(err["loc"], data)
            location = ".".join(p for p in (where, loc) if p) or None
            message = err["msg"].removeprefix("Value error, ")
            diagnostics.append(error("MK002", message, path, location))
    return None


def _count_missing_ids(data: Any) -> int:
    """Objects in raw YAML that should carry an id but do not."""
    if not isinstance(data, dict):
        return 0
    missing = 0 if "id" in data else 1
    for attribute in data.get("attributes") or []:
        if isinstance(attribute, dict) and "id" not in attribute:
            missing += 1
    return missing


def _load_list_file(
    path: Path, key: str, model: type[T], diagnostics: list[Diagnostic]
) -> tuple[list[tuple[T, int]], int]:
    """Load ``key: [...]`` from a file. Returns parsed objects and the count of missing ids."""
    data = _read_yaml(path, diagnostics)
    if data is None:
        return [], 0
    if not isinstance(data, dict) or not isinstance(data.get(key, []), list):
        diagnostics.append(error("MK003", f"expected a mapping with a {key!r} list", path))
        return [], 0
    unknown = set(data) - {key}
    if unknown:
        diagnostics.append(error("MK003", f"unexpected top-level keys: {', '.join(sorted(unknown))}", path))
    parsed: list[tuple[T, int]] = []
    missing = 0
    for index, item in enumerate(data.get(key) or []):
        label = item.get("name") if isinstance(item, dict) else None
        obj = _parse(model, item, path, diagnostics, f"{key}[{label or index}]")
        if obj is not None:
            parsed.append((obj, index))
            missing += _count_missing_ids(item)
    return parsed, missing


def load_project(root: Path) -> LoadResult:
    root = root.resolve()
    diagnostics: list[Diagnostic] = []
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        diagnostics.append(error("MK000", f"no {MANIFEST_FILE} here; run `murakumo init` to create a project", root))
        return LoadResult(None, diagnostics)

    raw_manifest = _read_yaml(manifest_path, diagnostics)
    if raw_manifest is None and not diagnostics:
        diagnostics.append(error("MK003", "the manifest is empty", manifest_path))
    manifest = _parse(Manifest, raw_manifest, manifest_path, diagnostics) if raw_manifest is not None else None
    if manifest is None:
        return LoadResult(None, diagnostics)

    project = Project(root=root, manifest=manifest)
    missing = 0

    for path in sorted((root / SOURCES_DIR).glob("*.yaml")) if (root / SOURCES_DIR).is_dir() else []:
        data = _read_yaml(path, diagnostics)
        source = _parse(SourceSystem, data, path, diagnostics) if data is not None else None
        if source is not None:
            project.model.sources.append(source)
            project.origins[source.id] = path
            missing += _count_missing_ids(data)

    entities_dir = root / ENTITIES_DIR
    for path in sorted(entities_dir.rglob("*.yaml")) if entities_dir.is_dir() else []:
        data = _read_yaml(path, diagnostics)
        entity = _parse(Entity, data, path, diagnostics) if data is not None else None
        if entity is not None:
            project.model.entities.append(entity)
            project.origins[entity.id] = path
            missing += _count_missing_ids(data)

    for key, filename, model, target in (
        ("relationships", RELATIONSHIPS_FILE, Relationship, project.model.relationships),
        ("metrics", METRICS_FILE, Metric, project.model.metrics),
    ):
        path = root / filename
        if path.is_file():
            parsed, file_missing = _load_list_file(path, key, model, diagnostics)
            target.extend(obj for obj, _ in parsed)
            missing += file_missing

    model_dir = root / MODEL_DIR
    known = {root / RELATIONSHIPS_FILE, root / METRICS_FILE}
    for path in sorted(model_dir.rglob("*.yml")) if model_dir.is_dir() else []:
        diagnostics.append(warning("MK004", "ignored: model files must end in .yaml", path))
    for path in sorted(model_dir.glob("*.yaml")) if model_dir.is_dir() else []:
        if path not in known:
            diagnostics.append(warning("MK004", "ignored: not a known model file", path))

    if missing:
        diagnostics.append(
            warning(
                "MK005",
                f"{missing} object(s) have no id; run `murakumo validate --fix` to write stable ids",
                root / MODEL_DIR,
            )
        )
    return LoadResult(project, diagnostics, missing)


# Saving -----------------------------------------------------------------------------------------


class _Dumper(yaml.SafeDumper):
    """Block-style YAML, multi-line strings as literal blocks, lists indented under their key."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _represent_str)

_FIRST_KEYS = ("id", "name")


def _ordered(data: Any) -> Any:
    """Put id and name first in every mapping; the rest keep their model order."""
    if isinstance(data, dict):
        first = {k: data[k] for k in _FIRST_KEYS if k in data}
        rest = {k: v for k, v in data.items() if k not in first}
        return {k: _ordered(v) for k, v in {**first, **rest}.items()}
    if isinstance(data, list):
        return [_ordered(v) for v in data]
    return data


def to_data(obj: BaseModel) -> dict[str, Any]:
    """An object as plain data, without default values, ids always kept."""
    data = obj.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    _restore_ids(obj, data)
    return _ordered(data)


def _restore_ids(obj: Any, data: Any) -> None:
    """exclude_defaults drops generated ids; put them back, recursively."""
    if isinstance(obj, BaseModel) and isinstance(data, dict):
        if "id" in type(obj).model_fields:
            data["id"] = str(obj.id)
        for name, info in type(obj).model_fields.items():
            key = info.alias or name
            if key in data:
                _restore_ids(getattr(obj, name), data[key])
    elif isinstance(obj, list) and isinstance(data, list):
        for item, item_data in zip(obj, data, strict=False):
            _restore_ids(item, item_data)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_yaml(data), encoding="utf-8")


def entity_path(root: Path, entity: Entity) -> Path:
    """Where a new entity goes: grouped by source for raw tables, by layer otherwise."""
    folder = entity.source if entity.layer.value == "raw" and entity.source else entity.layer.value
    return root / ENTITIES_DIR / folder / f"{entity.name}.yaml"


def save_manifest(root: Path, manifest: Manifest) -> None:
    data = manifest.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    data = {"version": manifest.version, **data}
    _write(root / MANIFEST_FILE, data)


def save_model(project: Project, prune: bool = False) -> list[Path]:
    """Write every model object to disk. Objects keep the file they were loaded from.

    With ``prune``, source and entity files that no longer hold an object (a deleted entity, for
    example) are removed. Only prune a project that loaded without errors: a file that failed to
    parse holds no object either. Returns the files written.
    """
    root = project.root
    written: list[Path] = []

    for source in project.model.sources:
        path = project.origins.get(source.id) or root / SOURCES_DIR / f"{source.name}.yaml"
        _write(path, to_data(source))
        project.origins[source.id] = path
        written.append(path)

    for entity in project.model.entities:
        path = project.origins.get(entity.id) or entity_path(root, entity)
        _write(path, to_data(entity))
        project.origins[entity.id] = path
        written.append(path)

    for key, filename, items in (
        ("relationships", RELATIONSHIPS_FILE, project.model.relationships),
        ("metrics", METRICS_FILE, project.model.metrics),
    ):
        path = root / filename
        if items:
            _write(path, {key: [to_data(item) for item in items]})
            written.append(path)
        elif path.exists():
            path.unlink()

    if not prune:
        return written
    keep = set(written)
    for folder in (root / SOURCES_DIR, root / ENTITIES_DIR):
        for stale in folder.rglob("*.yaml") if folder.is_dir() else []:
            if stale not in keep:
                stale.unlink()
    return written
