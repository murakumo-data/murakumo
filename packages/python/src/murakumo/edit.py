"""Small, safe edits to a model: add or remove sources, entities, columns and relationships.

The CLI's ``murakumo add`` and ``murakumo remove`` commands use these, and so will the canvas.
Every function changes the in-memory model only; the caller saves and validates.
"""

from __future__ import annotations

from pydantic import ValidationError

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
        problem = exc.errors()[0]
        raise EditError(
            f"{'.'.join(map(str, problem['loc']))}: {problem['msg'].removeprefix('Value error, ')}"
        ) from None
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


def parse_join(text: str) -> tuple[str, str]:
    """``customer_id=id`` -> ("customer_id", "id")."""
    left, sep, right = text.partition("=")
    if not sep or not left.strip() or not right.strip():
        raise EditError(f"cannot read join {text!r}; write from_column=to_column")
    return left.strip(), right.strip()
