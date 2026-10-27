"""What every importer produces, and how it is merged into a project.

Importers describe a source as it is now: a source system, its tables and the foreign keys
between them. Merging compares that with the model and updates it in place, so that:

- objects that already exist keep their ids (a re-import is an update, not a replacement);
- only the fields an importer knows about are overwritten; anything a person added by hand,
  such as tests, tags, partitioning or custom properties, is kept;
- columns that disappeared from the source are removed, because raw tables mirror their source;
- whole tables that disappeared are only reported, unless ``prune`` is set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from murakumo.diagnostics import Diagnostic, error
from murakumo.ir.model import Attribute, Entity, JoinKey, Relationship, SourceSystem
from murakumo.project import Project


@dataclass
class SourceImport:
    source: SourceSystem
    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    # A partial import describes some of a source's tables (one file, one API endpoint), so
    # tables it does not mention are not reported as missing or pruned.
    partial: bool = False


@dataclass
class MergeReport:
    source: str
    added_source: bool = False
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed_columns: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    added_relationships: list[str] = field(default_factory=list)
    removed_relationships: list[str] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added_source
            or self.added
            or self.changed
            or self.removed_columns
            or self.removed
            or self.added_relationships
            or self.removed_relationships
        )


def safe_name(text: str, fallback: str = "column") -> str:
    """Turn any source name into a valid IR name: ``Trip Distance (mi)`` -> ``Trip_Distance_mi``."""
    name = re.sub(r"[^A-Za-z0-9_]+", "_", text.strip()).strip("_")
    if not name:
        return fallback
    return f"_{name}" if name[0].isdigit() else name[:255]


def _update(existing: Any, incoming: Any, label: str, changes: list[str], skip: set[str]) -> None:
    """Copy the fields the importer set from ``incoming`` onto ``existing``, recording changes."""
    for name in sorted(incoming.model_fields_set - skip - {"id"}):
        new, old = getattr(incoming, name), getattr(existing, name)
        if new != old:
            setattr(existing, name, new)
            changes.append(f"{label}: {name} {_show(old)} -> {_show(new)}")


def _show(value: Any) -> str:
    if value is None or value == [] or value == {}:
        return "(none)"
    text = getattr(value, "value", value)
    text = str(text)
    return text if len(text) <= 40 else text[:37] + "..."


def _key(rel: Relationship) -> tuple:
    return (
        rel.from_.lower(),
        rel.to.lower(),
        tuple(sorted((k.from_.lower(), k.to.lower()) for k in rel.keys)),
    )


def merge(project: Project, imported: SourceImport, prune: bool = False) -> MergeReport:
    model = project.model
    source_name = imported.source.name
    report = MergeReport(source=source_name, diagnostics=list(imported.diagnostics))

    existing_source = model.source(source_name)
    if existing_source is None:
        model.sources.append(imported.source)
        report.added_source = True
    else:
        _update(existing_source, imported.source, f"source {source_name}", report.changed, skip={"name"})

    seen: set[str] = set()
    for incoming in imported.entities:
        current = model.entity(incoming.name)
        seen.add(incoming.name.lower())
        if current is None:
            model.entities.append(incoming)
            report.added.append(incoming.name)
            continue
        if (current.source or "").lower() != source_name.lower():
            owner = f"source {current.source}" if current.source else f"the {current.layer} layer"
            report.diagnostics.append(
                error("MK401", f"{incoming.name} already exists in {owner}; not overwritten by {source_name}")
            )
            continue

        _update(current, incoming, incoming.name, report.changed, skip={"name", "attributes"})
        attributes: list[Attribute] = []
        incoming_names = {a.name.lower() for a in incoming.attributes}
        for column in incoming.attributes:
            old = current.attribute(column.name)
            if old is None:
                attributes.append(column)
                report.changed.append(f"{incoming.name}: new column {column.name} ({column.type})")
            else:
                _update(old, column, f"{incoming.name}.{column.name}", report.changed, skip=set())
                attributes.append(old)
        for column in current.attributes:
            if column.name.lower() not in incoming_names:
                report.removed_columns.append(f"{incoming.name}.{column.name}")
        if [a.id for a in attributes] != [a.id for a in current.attributes]:
            current.attributes = attributes

    for entity in [e for e in model.entities if (e.source or "").lower() == source_name.lower()]:
        if entity.name.lower() not in seen and not imported.partial:
            if prune:
                model.entities.remove(entity)
                report.removed.append(entity.name)
            else:
                report.missing.append(entity.name)

    source_tables = {e.name.lower() for e in model.entities if (e.source or "").lower() == source_name.lower()}
    existing = {_key(r): r for r in model.relationships}
    incoming_keys = set()
    for rel in imported.relationships:
        key = _key(rel)
        incoming_keys.add(key)
        if key not in existing:
            model.relationships.append(rel)
            existing[key] = rel
            report.added_relationships.append(_describe(rel))
    if prune and not imported.partial:
        for rel in list(model.relationships):
            key = _key(rel)
            inside = rel.from_.lower() in source_tables and rel.to.lower() in source_tables
            if inside and key not in incoming_keys:
                model.relationships.remove(rel)
                report.removed_relationships.append(_describe(rel))
        removed = {n.lower() for n in report.removed}
        for rel in list(model.relationships):
            if rel.from_.lower() in removed or rel.to.lower() in removed:
                model.relationships.remove(rel)
                report.removed_relationships.append(_describe(rel))
    return report


def _describe(rel: Relationship) -> str:
    keys = ", ".join(f"{k.from_} = {k.to}" for k in rel.keys)
    return f"{rel.from_} -> {rel.to} ({keys})"


def fk_relationship(from_table: str, to_table: str, pairs: list[tuple[str, str]], nullable: bool) -> Relationship:
    """A many-to-one relationship for a foreign key, named after the table and its columns."""
    columns = "_".join(p[0] for p in pairs)
    return Relationship(
        name=safe_name(f"{from_table}__{columns}", "relationship")[:255],
        from_=from_table,
        to=to_table,
        optional=nullable,
        keys=[JoinKey(from_=a, to=b) for a, b in pairs],
    )


def unique_names(relationships: list[Relationship]) -> list[Relationship]:
    """Add the target table to names that clash: lineitem__l_partkey -> lineitem__l_partkey__part."""
    counts: dict[str, int] = {}
    for rel in relationships:
        counts[(rel.name or "").lower()] = counts.get((rel.name or "").lower(), 0) + 1
    for rel in relationships:
        if rel.name and counts[rel.name.lower()] > 1:
            rel.name = safe_name(f"{rel.name}__{rel.to}", "relationship")[:255]
    return relationships
