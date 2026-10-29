"""Typed model diffs: what changed between two versions of a model, and how risky each change is.

Objects are matched by their stable ids, so a rename is a rename, not a drop and an add. Each
change gets a class, as in the project brief's change table:

- ``additive``: nothing that reads the model breaks (a new table or nullable column, a widened
  type, nullability loosened, a new relationship);
- ``compatible``: nothing breaks, but data must be rebuilt or backfilled (a rename, a changed
  mapping, a history type change such as SCD1 to SCD2);
- ``breaking``: something that reads the model may fail or change meaning (a dropped table or
  column, a narrowed type, nullability tightened, a key or grain change).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from murakumo.ir.model import Entity, Model
from murakumo.ir.types import TypeName, parse_type

ADDITIVE, COMPATIBLE, BREAKING = "additive", "compatible", "breaking"
_ORDER = {ADDITIVE: 0, COMPATIBLE: 1, BREAKING: 2}
_INTEGER_RANK = {TypeName.SMALLINT: 1, TypeName.INTEGER: 2, TypeName.BIGINT: 3}


@dataclass
class Change:
    kind: str  # entity_added, column_renamed, type_changed, ...
    cls: str  # additive | compatible | breaking
    entity: str  # the entity's name, as it is after the change (before, if it was removed)
    summary: str  # one line, for people
    column: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "class": self.cls,
            "entity": self.entity,
            "column": self.column,
            "summary": self.summary,
            "detail": self.detail,
        }


def widens(old: str, new: str) -> bool | None:
    """True if ``new`` holds every value of ``old``; False if it may not; None if they are the same."""
    a, b = parse_type(old), parse_type(new)
    if a == b:
        return None
    if b.family == "text" and b.name is not TypeName.UUID:
        if a.family == "text":  # string(20) -> string(50) or string: wider; shorter: narrower
            return not b.params or (bool(a.params) and b.params[0] >= a.params[0])
        return True  # anything can be written as text
    if a.name in _INTEGER_RANK and b.name in _INTEGER_RANK:
        return _INTEGER_RANK[b.name] >= _INTEGER_RANK[a.name]
    if a.family == "integer" and b.name in (TypeName.DECIMAL, TypeName.DOUBLE, TypeName.MONEY):
        return True
    if a.name is TypeName.DECIMAL and b.name is TypeName.DECIMAL:
        pa = a.params or (38, 9)
        pb = b.params or (38, 9)
        sa, sb = (pa[1] if len(pa) > 1 else 0), (pb[1] if len(pb) > 1 else 0)
        return sb >= sa and (pb[0] - sb) >= (pa[0] - sa)
    if a.name is TypeName.FLOAT and b.name is TypeName.DOUBLE:
        return True
    if a.name is TypeName.DATE and b.family == "timestamp":
        return True
    if a.name is TypeName.TIMESTAMP and b.name is TypeName.TIMESTAMP_TZ:
        return True
    return False


def _by_id(items) -> dict[str, Any]:
    return {str(item.id): item for item in items}


def _entity_changes(old: Entity, new: Entity) -> list[Change]:
    name = new.name
    out: list[Change] = []
    if old.name != new.name:
        out.append(
            Change("entity_renamed", COMPATIBLE, name, f"{old.name} renamed to {new.name}", detail={"from": old.name})
        )
    if old.layer != new.layer:
        out.append(Change("layer_changed", BREAKING, name, f"{name} moved from {old.layer.value} to {new.layer.value}"))
    if old.history != new.history:
        cls = COMPATIBLE if new.history.value in ("scd2", "snapshot") else BREAKING
        out.append(
            Change(
                "history_changed",
                cls,
                name,
                f"{name}: history {old.history.value} -> {new.history.value}",
                detail={"from": old.history.value, "to": new.history.value},
            )
        )
    if [g.lower() for g in old.grain] != [g.lower() for g in new.grain]:
        out.append(
            Change(
                "grain_changed",
                BREAKING,
                name,
                f"{name}: grain {', '.join(old.grain) or '(none)'} -> {', '.join(new.grain) or '(none)'}",
            )
        )
    if old.load != new.load or (old.watermark or "") != (new.watermark or ""):
        out.append(Change("load_changed", COMPATIBLE, name, f"{name}: load {old.load.value} -> {new.load.value}"))
    if old.kind != new.kind:
        out.append(Change("kind_changed", COMPATIBLE, name, f"{name}: kind {old.kind.value} -> {new.kind.value}"))
    if (old.domain or "") != (new.domain or ""):
        out.append(
            Change(
                "domain_changed", ADDITIVE, name, f"{name}: domain {old.domain or '(none)'} -> {new.domain or '(none)'}"
            )
        )

    old_columns, new_columns = _by_id(old.attributes), _by_id(new.attributes)
    for key, column in new_columns.items():
        before = old_columns.get(key)
        if before is None:
            required = not column.nullable and column.default is None
            cls = BREAKING if required and new.layer.value != "raw" else ADDITIVE
            out.append(
                Change("column_added", cls, name, f"{name}: new column {column.name} ({column.type})", column.name)
            )
            continue
        if before.name != column.name:
            out.append(
                Change(
                    "column_renamed",
                    COMPATIBLE,
                    name,
                    f"{name}: column {before.name} renamed to {column.name}",
                    column.name,
                    {"from": before.name},
                )
            )
        wider = widens(before.type, column.type)
        if wider is not None:
            out.append(
                Change(
                    "type_changed",
                    ADDITIVE if wider else BREAKING,
                    name,
                    f"{name}.{column.name}: {before.type} -> {column.type} ({'wider' if wider else 'narrower'})",
                    column.name,
                    {"from": before.type, "to": column.type, "wider": wider},
                )
            )
        if before.nullable and not column.nullable:
            out.append(
                Change("nullability_tightened", BREAKING, name, f"{name}.{column.name} is now not null", column.name)
            )
        elif not before.nullable and column.nullable:
            out.append(
                Change("nullability_loosened", ADDITIVE, name, f"{name}.{column.name} may now be null", column.name)
            )
        if (before.key.value if before.key else None) != (column.key.value if column.key else None):
            out.append(
                Change(
                    "key_changed",
                    BREAKING,
                    name,
                    f"{name}.{column.name}: key {before.key.value if before.key else 'none'} -> "
                    f"{column.key.value if column.key else 'none'}",
                    column.name,
                )
            )
    for key, column in old_columns.items():
        if key not in new_columns:
            out.append(Change("column_removed", BREAKING, name, f"{name}: column {column.name} removed", column.name))

    old_maps = {m.target.lower(): m for m in old.mappings}
    for mapping in new.mappings:
        before = old_maps.get(mapping.target.lower())
        renamed_from = next(
            (c.detail["from"] for c in out if c.kind == "column_renamed" and c.column == mapping.target), None
        )
        before = before or (old_maps.get(renamed_from.lower()) if renamed_from else None)
        if before is None:
            if not any(c.kind == "column_added" and c.column == mapping.target for c in out):
                out.append(
                    Change("mapping_changed", COMPATIBLE, name, f"{name}.{mapping.target}: now mapped", mapping.target)
                )
            continue
        if before.sources != mapping.sources or (before.expression or "") != (mapping.expression or ""):
            out.append(
                Change("mapping_changed", COMPATIBLE, name, f"{name}.{mapping.target}: mapping changed", mapping.target)
            )
    return out


def _relationship_key(rel) -> str:
    return f"{rel.from_} -> {rel.to}" + (f" ({rel.role})" if rel.role else "")


def diff(old: Model, new: Model) -> list[Change]:
    """Every change from ``old`` to ``new``, most severe first, in a stable order."""
    changes: list[Change] = []
    old_entities, new_entities = _by_id(old.entities), _by_id(new.entities)
    for key, entity in new_entities.items():
        before = old_entities.get(key)
        if before is None:
            changes.append(
                Change(
                    "entity_added",
                    ADDITIVE,
                    entity.name,
                    f"new {entity.layer.value} table {entity.name} ({len(entity.attributes)} columns)",
                )
            )
        else:
            changes += _entity_changes(before, entity)
    for key, entity in old_entities.items():
        if key not in new_entities:
            changes.append(Change("entity_removed", BREAKING, entity.name, f"table {entity.name} removed"))

    old_rels, new_rels = _by_id(old.relationships), _by_id(new.relationships)
    for key, rel in new_rels.items():
        before = old_rels.get(key)
        if before is None:
            changes.append(
                Change("relationship_added", ADDITIVE, rel.from_, f"new relationship {_relationship_key(rel)}")
            )
        elif before.model_dump() != rel.model_dump():
            changes.append(
                Change("relationship_changed", COMPATIBLE, rel.from_, f"relationship {_relationship_key(rel)} changed")
            )
    for key, rel in old_rels.items():
        if key not in new_rels:
            changes.append(
                Change("relationship_removed", COMPATIBLE, rel.from_, f"relationship {_relationship_key(rel)} removed")
            )

    old_sources = {s.name.lower() for s in old.sources}
    for source in new.sources:
        if source.name.lower() not in old_sources:
            changes.append(Change("source_added", ADDITIVE, source.name, f"new source {source.name}"))
    return sorted(changes, key=lambda c: (-_ORDER[c.cls], c.entity.lower(), c.column or "", c.kind))


def worst(changes: list[Change]) -> str | None:
    return max((c.cls for c in changes), key=_ORDER.__getitem__, default=None)


__all__ = ["ADDITIVE", "BREAKING", "COMPATIBLE", "Change", "diff", "widens", "worst"]
