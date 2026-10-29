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
    Mapping,
    Relationship,
    SourceSystem,
    SourceType,
)
from murakumo.manifest import Domain
from murakumo.manifest_edit import ManifestEditError, write_section
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


def set_mapping(
    project: Project, entity_name: str, target: str, sources: list[str], expression: str | None = None
) -> Mapping | None:
    """Say how a column is derived: from ``entity.column`` sources, a SQL expression, or both.

    No sources and no expression removes the mapping. Returns the mapping, or None if removed.
    """
    entity = _entity(project, entity_name)
    column = entity.attribute(target)
    if column is None:
        raise EditError(f"{entity.name} has no column {target!r}")
    sources = [s.strip() for s in sources if s.strip()]
    expression = (expression or "").strip() or None
    for ref in sources:
        source_name, _, source_column = ref.partition(".")
        source = project.model.entity(source_name) if source_column else None
        if source is None:
            raise EditError(f"source {ref!r} must be entity.column, with an entity that exists")
        if source.attribute(source_column) is None:
            raise EditError(f"{source.name} has no column {source_column!r}")
    if expression:
        try:
            sqlglot.parse_one(expression, read=EXPRESSION_DIALECT)
        except sqlglot.errors.ParseError as exc:
            raise EditError(f"cannot read the expression: {str(exc).splitlines()[0]}") from None
    mappings = list(entity.mappings)
    index = next((i for i, m in enumerate(mappings) if m.target.lower() == target.lower()), None)
    if not sources and not expression:
        if index is not None:
            del mappings[index]
            entity.mappings = mappings
        return None
    try:
        mapping = Mapping(target=column.name, sources=sources, expression=expression)
    except ValidationError as exc:
        raise _problem(exc) from None
    if index is not None:
        mappings[index] = mapping
    else:
        # A new mapping goes before the first one for a later column, so the YAML follows the table.
        order = {a.name.lower(): i for i, a in enumerate(entity.attributes)}
        here = order[column.name.lower()]
        index = next((i for i, m in enumerate(mappings) if order.get(m.target.lower(), len(order)) > here), None)
        mappings.insert(len(mappings) if index is None else index, mapping)
    entity.mappings = mappings
    return mapping


def add_calculated_column(
    project: Project, entity_name: str, name: str, expression: str, type: str | None = None
) -> Attribute:
    """Add a column worked out by a SQL expression: the column, and its mapping, in one step.

    The mapping's sources are the columns the expression reads; the type defaults to the one the
    expression returns, when that can be told.
    """
    from murakumo.expressions import check_expression

    entity = _entity(project, entity_name)
    if entity.layer is Layer.RAW:
        raise EditError(f"{entity.name} is a raw table: its columns come from the source, not from expressions")
    check = check_expression(project, expression, target=entity.name)
    if check.error:
        raise EditError(f"cannot read the expression: {check.error}")
    if check.problems:
        raise EditError("; ".join(check.problems))
    column_type = type or check.type
    if not column_type:
        raise EditError("cannot tell what type the expression returns; give the column a type")
    column = add_column(project, entity.name, name, column_type)
    set_mapping(project, entity.name, column.name, check.references, expression)
    return column


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


# Column order -----------------------------------------------------------------------------------


def order_columns(project: Project, entity_name: str, names: list[str]) -> Entity:
    """Put an entity's columns in the given order. ``names`` must list every column once.

    Mappings follow the columns, so the YAML reads in the same order as the table.
    """
    entity = _entity(project, entity_name)
    wanted = [n.lower() for n in names]
    have = [a.name.lower() for a in entity.attributes]
    if sorted(wanted) != sorted(have):
        missing = sorted(set(have) - set(wanted))
        extra = sorted(set(wanted) - set(have))
        if len(set(wanted)) != len(wanted):
            raise EditError("each column can appear only once in the new order")
        problem = [f"missing {', '.join(missing)}" if missing else "", f"unknown {', '.join(extra)}" if extra else ""]
        raise EditError(f"the new order must list every column of {entity.name}: " + "; ".join(p for p in problem if p))
    position = {name: i for i, name in enumerate(wanted)}
    entity.attributes = sorted(entity.attributes, key=lambda a: position[a.name.lower()])
    entity.mappings = sorted(entity.mappings, key=lambda m: position.get(m.target.lower(), len(position)))
    return entity


def move_column(
    project: Project, entity_name: str, name: str, after: str | None = None, before: str | None = None
) -> Entity:
    """Move one column after or before another; with neither, to the start."""
    entity = _entity(project, entity_name)
    column = entity.attribute(name)
    if column is None:
        raise EditError(f"{entity.name} has no column {name!r}")
    if after and before:
        raise EditError("give either after or before, not both")
    anchor_name = after or before
    names = [a.name for a in entity.attributes if a is not column]
    if anchor_name is None:
        index = 0
    else:
        anchor = entity.attribute(anchor_name)
        if anchor is None:
            raise EditError(f"{entity.name} has no column {anchor_name!r}")
        if anchor is column:
            raise EditError("a column cannot move next to itself")
        index = names.index(anchor.name) + (1 if after else 0)
    names.insert(index, column.name)
    return order_columns(project, entity.name, names)


# Domains ----------------------------------------------------------------------------------------
# Domains are listed in murakumo.yaml under project.domains, so these write the manifest straight
# away (keeping its comments); entities that change are saved by the caller as usual.


def _domain_yaml(domain: Domain) -> str | dict[str, Any]:
    data = domain.model_dump(exclude_none=True)
    return data["name"] if set(data) == {"name"} else data


def _save_domains(project: Project, domains: list[Domain]) -> None:
    try:
        project.manifest = write_section(project.root, ("project", "domains"), [_domain_yaml(d) for d in domains])
    except ManifestEditError as exc:
        raise EditError(str(exc)) from None


def _domain(project: Project, name: str) -> Domain:
    domain = project.manifest.domain(name)
    if domain is None:
        raise EditError(f"domain {name!r} is not listed under project.domains")
    return domain


def domain_usage(project: Project, name: str) -> list[Entity]:
    return [e for e in project.model.entities if (e.domain or "").lower() == name.lower()]


def add_domain(project: Project, name: str, owner: str | None = None, description: str | None = None) -> Domain:
    _check_name(name)
    if project.manifest.domain(name):
        raise EditError(f"domain {name!r} already exists")
    domain = Domain(name=name, owner=owner or None, description=description or None)
    _save_domains(project, [*project.manifest.project.domains, domain])
    return domain


def update_domain(project: Project, name: str, changes: dict[str, Any]) -> Domain:
    """Change a domain's owner or description; a new ``name`` renames it, and every entity in it."""
    domain = _domain(project, name)
    unknown = set(changes) - {"name", "owner", "description"}
    if unknown:
        raise EditError(f"cannot change {', '.join(sorted(unknown))} on a domain")
    new_name = changes.get("name") or domain.name
    if new_name != domain.name:
        _check_name(new_name)
        if new_name.lower() != domain.name.lower() and project.manifest.domain(new_name):
            raise EditError(f"domain {new_name!r} already exists")
    data = domain.model_dump() | {k: (v or None) for k, v in changes.items()} | {"name": new_name}
    updated = Domain(**data)
    for entity in domain_usage(project, domain.name):
        entity.domain = updated.name
    _save_domains(project, [updated if d is domain else d for d in project.manifest.project.domains])
    return updated


def remove_domain(project: Project, name: str) -> Domain:
    domain = _domain(project, name)
    used = domain_usage(project, domain.name)
    if used:
        names = ", ".join(e.name for e in used[:5]) + (" and more" if len(used) > 5 else "")
        raise EditError(f"domain {domain.name} is used by {names}; move them to another domain first")
    _save_domains(project, [d for d in project.manifest.project.domains if d is not domain])
    return domain


def parse_join(text: str) -> tuple[str, str]:
    """``customer_id=id`` -> ("customer_id", "id")."""
    left, sep, right = text.partition("=")
    if not sep or not left.strip() or not right.strip():
        raise EditError(f"cannot read join {text!r}; write from_column=to_column")
    return left.strip(), right.strip()
