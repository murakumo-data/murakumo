"""Small, safe edits to a model: add, change, rename or remove sources, entities, columns and relationships.

The CLI's ``murakumo add``, ``rename`` and ``remove`` commands and the canvas API both use these.
Every function changes the in-memory model only; the caller saves and validates.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

import sqlglot
from pydantic import BaseModel, ValidationError
from sqlglot import exp

from murakumo.generate.plan import EXPRESSION_DIALECT
from murakumo.ir.model import (
    Attribute,
    Cardinality,
    Classification,
    Entity,
    EntityKind,
    JoinKey,
    KeyRole,
    Layer,
    Relationship,
    SourceSystem,
    SourceType,
)
from murakumo.project import Project


class EditError(Exception):
    pass


def _problem(exc: ValidationError) -> EditError:
    problem = exc.errors()[0]
    where = ".".join(map(str, problem["loc"]))
    message = problem["msg"].removeprefix("Value error, ")
    return EditError(f"{where}: {message}" if where else message)


def _changed(obj: BaseModel, changes: dict[str, Any], allowed: set[str]) -> Any:
    """A validated copy of ``obj`` with ``changes`` applied, so a bad value changes nothing."""
    unknown = set(changes) - allowed
    if unknown:
        raise EditError(f"cannot change {', '.join(sorted(unknown))} here")
    data = obj.model_dump(by_alias=True)
    data.update(changes)
    try:
        return type(obj).model_validate(data)
    except ValidationError as exc:
        raise _problem(exc) from None


def add_source(
    project: Project,
    name: str,
    type: SourceType = SourceType.DATABASE,
    connection: str | None = None,
    description: str | None = None,
) -> SourceSystem:
    if project.model.source(name):
        raise EditError(f"source {name!r} already exists")
    source = SourceSystem(name=name, type=type, connection=connection, description=description)
    project.model.sources.append(source)
    return source


def add_entity(
    project: Project,
    name: str,
    layer: Layer = Layer.RAW,
    kind: EntityKind = EntityKind.TABLE,
    source: str | None = None,
    domain: str | None = None,
    description: str | None = None,
) -> Entity:
    if project.model.entity(name):
        raise EditError(f"entity {name!r} already exists")
    if (
        source
        and not project.model.source(source)
        and not any(s.name.lower() == source.lower() for s in project.manifest.sources)
    ):
        raise EditError(f"source {source!r} does not exist; add it first with `murakumo add source {source}`")
    entity = Entity(name=name, layer=layer, kind=kind, source=source, domain=domain, description=description)
    project.model.entities.append(entity)
    return entity


def _entity(project: Project, name: str) -> Entity:
    entity = project.model.entity(name)
    if entity is None:
        raise EditError(f"entity {name!r} does not exist")
    return entity


def add_column(
    project: Project,
    entity_name: str,
    name: str,
    type: str,
    primary_key: bool = False,
    nullable: bool | None = None,
    description: str | None = None,
    pii: bool = False,
    classification: Classification | None = None,
) -> Attribute:
    entity = _entity(project, entity_name)
    if entity.attribute(name):
        raise EditError(f"{entity.name} already has a column {name!r}")
    try:
        column = Attribute(
            name=name,
            type=type,
            key=KeyRole.PRIMARY if primary_key else None,
            nullable=(not primary_key) if nullable is None else nullable,
            description=description,
            pii=pii,
            classification=classification,
        )
    except ValidationError as exc:
        raise _problem(exc) from None
    entity.attributes = [*entity.attributes, column]
    return column


def add_relationship(
    project: Project,
    from_entity: str,
    to_entity: str,
    keys: list[tuple[str, str]],
    cardinality: Cardinality = Cardinality.MANY_TO_ONE,
    name: str | None = None,
    optional: bool = False,
    role: str | None = None,
) -> Relationship:
    source, target = _entity(project, from_entity), _entity(project, to_entity)
    if not keys:
        raise EditError("a relationship needs at least one join, such as --on customer_id=id")
    for left, right in keys:
        if source.attribute(left) is None:
            raise EditError(f"{source.name} has no column {left!r}")
        if target.attribute(right) is None:
            raise EditError(f"{target.name} has no column {right!r}")
    if name and any((r.name or "").lower() == name.lower() for r in project.model.relationships):
        raise EditError(f"relationship {name!r} already exists")
    relationship = Relationship(
        name=name,
        from_=source.name,
        to=target.name,
        cardinality=cardinality,
        optional=optional,
        role=role,
        keys=[JoinKey(from_=left, to=right) for left, right in keys],
    )
    project.model.relationships.append(relationship)
    return relationship


def remove_entity(project: Project, name: str) -> list[Relationship]:
    """Remove an entity and the relationships that use it. Returns the removed relationships."""
    entity = _entity(project, name)
    project.model.entities.remove(entity)
    lowered = entity.name.lower()
    removed = [r for r in project.model.relationships if lowered in (r.from_.lower(), r.to.lower())]
    project.model.relationships = [r for r in project.model.relationships if r not in removed]
    return removed


def remove_column(project: Project, entity_name: str, name: str) -> Attribute:
    entity = _entity(project, entity_name)
    column = entity.attribute(name)
    if column is None:
        raise EditError(f"{entity.name} has no column {name!r}")
    entity.attributes = [a for a in entity.attributes if a is not column]
    return column


# Changing and renaming -------------------------------------------------------------------------

ENTITY_FIELDS = {"description", "layer", "kind", "source", "domain", "owner", "history", "load", "watermark", "tags"}
COLUMN_FIELDS = {"description", "type", "nullable", "key", "pii", "classification", "unit", "example", "business_term"}
RELATIONSHIP_FIELDS = {"name", "cardinality", "optional", "role", "description", "keys"}


def update_entity(project: Project, name: str, changes: dict[str, Any]) -> Entity:
    """Change an entity's settings. Renaming goes through ``rename_entity``, which fixes references."""
    entity = _entity(project, name)
    if changes.get("source") and not project.model.source(changes["source"]):
        if not any(s.name.lower() == changes["source"].lower() for s in project.manifest.sources):
            raise EditError(f"source {changes['source']!r} does not exist")
    updated = _changed(entity, changes, ENTITY_FIELDS)
    if updated.watermark and not updated.attribute(updated.watermark):
        raise EditError(f"{entity.name} has no column {updated.watermark!r} to use as the watermark")
    entities = project.model.entities
    entities[entities.index(entity)] = updated
    return updated


def update_column(project: Project, entity_name: str, name: str, changes: dict[str, Any]) -> Attribute:
    entity = _entity(project, entity_name)
    column = entity.attribute(name)
    if column is None:
        raise EditError(f"{entity.name} has no column {name!r}")
    if changes.get("key") == KeyRole.PRIMARY.value and "nullable" not in changes:
        changes = {**changes, "nullable": False}
    updated = _changed(column, changes, COLUMN_FIELDS)
    entity.attributes = [updated if a is column else a for a in entity.attributes]
    return updated


def _relationship(project: Project, id: UUID | str) -> Relationship:
    relationship = next((r for r in project.model.relationships if str(r.id) == str(id)), None)
    if relationship is None:
        raise EditError(f"relationship {id} does not exist")
    return relationship


def update_relationship(project: Project, id: UUID | str, changes: dict[str, Any]) -> Relationship:
    relationship = _relationship(project, id)
    updated = _changed(relationship, changes, RELATIONSHIP_FIELDS)
    source, target = _entity(project, updated.from_), _entity(project, updated.to)
    for key in updated.keys:
        if source.attribute(key.from_) is None:
            raise EditError(f"{source.name} has no column {key.from_!r}")
        if target.attribute(key.to) is None:
            raise EditError(f"{target.name} has no column {key.to!r}")
    if updated.name and any(
        (r.name or "").lower() == updated.name.lower() for r in project.model.relationships if r is not relationship
    ):
        raise EditError(f"relationship {updated.name!r} already exists")
    relationships = project.model.relationships
    relationships[relationships.index(relationship)] = updated
    return updated


def remove_relationship(project: Project, id: UUID | str) -> Relationship:
    relationship = _relationship(project, id)
    project.model.relationships.remove(relationship)
    return relationship


def _check_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or len(name) > 255:
        raise EditError(f"{name!r} is not a valid name: use letters, digits and underscores, not starting with a digit")


def _rename_in_expression(text: str, entity: str, column: str | None, new: str, bare: bool = False) -> str:
    """Rename ``entity.column`` (or the qualifier ``entity.``, when column is None) in a SQL expression.

    With ``bare``, an unqualified ``column`` is renamed too: the mapping reads it from that entity.
    """
    try:
        tree = sqlglot.parse_one(text, read=EXPRESSION_DIALECT)
    except sqlglot.errors.ParseError:
        return text  # left for validation and generate to report
    changed = False

    def rewrite(node: exp.Expression) -> exp.Expression:
        nonlocal changed
        if not isinstance(node, exp.Column):
            return node
        if bare and not node.table and column and node.name.lower() == column.lower():
            changed = True
            return exp.column(new)
        if node.table.lower() != entity.lower():
            return node
        if column is None:
            changed = True
            return exp.column(node.name, table=new, quoted=node.this.quoted)
        if node.name.lower() == column.lower():
            changed = True
            return exp.column(new, table=node.table)
        return node

    result = tree.transform(rewrite)
    return result.sql(dialect=EXPRESSION_DIALECT) if changed else text


def rename_entity(project: Project, name: str, new_name: str) -> Entity:
    """Rename an entity and every reference to it: relationships, mappings and metrics.

    The id stays the same. If the entity's file is named after it, the file is renamed too
    (save with ``prune`` to remove the old one).
    """
    entity = _entity(project, name)
    _check_name(new_name)
    if new_name.lower() != entity.name.lower() and project.model.entity(new_name):
        raise EditError(f"entity {new_name!r} already exists")
    old = entity.name
    entity.name = new_name
    lowered = old.lower()

    for rel in project.model.relationships:
        if rel.from_.lower() == lowered:
            rel.from_ = new_name
        if rel.to.lower() == lowered:
            rel.to = new_name
    for other in project.model.entities:
        for mapping in other.mappings:
            mapping.sources = [
                f"{new_name}.{ref.split('.', 1)[1]}" if ref.split(".", 1)[0].lower() == lowered else ref
                for ref in mapping.sources
            ]
            if mapping.expression:
                mapping.expression = _rename_in_expression(mapping.expression, old, None, new_name)
    for metric in project.model.metrics:
        if metric.entity.lower() == lowered:
            metric.entity = new_name

    path = project.origins.get(entity.id)
    if path is not None and path.stem == old:
        project.origins[entity.id] = path.with_name(f"{new_name}.yaml")
    return entity


def _rename_list(names: list[str], old: str, new: str) -> list[str]:
    return [new if n.lower() == old.lower() else n for n in names]


def rename_column(project: Project, entity_name: str, name: str, new_name: str) -> Attribute:
    """Rename a column and every reference to it: keys, grain, tests, mappings and relationships."""
    entity = _entity(project, entity_name)
    column = entity.attribute(name)
    if column is None:
        raise EditError(f"{entity.name} has no column {name!r}")
    _check_name(new_name)
    if new_name.lower() != column.name.lower() and entity.attribute(new_name):
        raise EditError(f"{entity.name} already has a column {new_name!r}")
    old = column.name
    column.name = new_name

    entity.grain = _rename_list(entity.grain, old, new_name)
    entity.cluster_by = _rename_list(entity.cluster_by, old, new_name)
    entity.partition_by = _rename_list(entity.partition_by, old, new_name)
    if entity.watermark and entity.watermark.lower() == old.lower():
        entity.watermark = new_name
    for rule in [*entity.tests, *(t for a in entity.attributes for t in a.tests)]:
        rule.columns = _rename_list(rule.columns, old, new_name)
    for mapping in entity.mappings:
        if mapping.target.lower() == old.lower():
            mapping.target = new_name

    reference = f"{entity.name}.{old}".lower()
    for other in project.model.entities:
        for mapping in other.mappings:
            reads = any(r.lower() == reference for r in mapping.sources)
            mapping.sources = [f"{entity.name}.{new_name}" if r.lower() == reference else r for r in mapping.sources]
            if mapping.expression:
                mapping.expression = _rename_in_expression(mapping.expression, entity.name, old, new_name, reads)
    for rel in project.model.relationships:
        for key in rel.keys:
            if rel.from_.lower() == entity.name.lower() and key.from_.lower() == old.lower():
                key.from_ = new_name
            if rel.to.lower() == entity.name.lower() and key.to.lower() == old.lower():
                key.to = new_name
    return column


def parse_join(text: str) -> tuple[str, str]:
    """``customer_id=id`` -> ("customer_id", "id")."""
    left, sep, right = text.partition("=")
    if not sep or not left.strip() or not right.strip():
        raise EditError(f"cannot read join {text!r}; write from_column=to_column")
    return left.strip(), right.strip()
