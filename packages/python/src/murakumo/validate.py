"""Model rules that span objects: references, uniqueness and modelling good practice.

Field-level checks (types, allowed values, unknown keys) already happen while loading.
Errors block generation; warnings are advice. Each rule has a stable code so teams can
look it up and, later, silence it per object.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from murakumo.diagnostics import Diagnostic, error, warning
from murakumo.ir.model import (
    Cardinality,
    Classification,
    Entity,
    EntityKind,
    History,
    KeyRole,
    Layer,
    LoadPattern,
)
from murakumo.project import METRICS_FILE, RELATIONSHIPS_FILE, Project

SNAKE_CASE = re.compile(r"^[a-z_][a-z0-9_]*$")
# partition_by accepts a column or a transform of one: day(pickup_at), bucket(16, customer_id).
PARTITION_EXPR = re.compile(r"^\s*(?:[a-z_]+\(\s*(?:\d+\s*,\s*)?)?([A-Za-z_][A-Za-z0-9_]*)\s*\)?\s*$")


def _duplicates(names: Iterable[str]) -> list[str]:
    counts = Counter(n.lower() for n in names)
    return sorted(n for n, c in counts.items() if c > 1)


def validate_project(project: Project) -> list[Diagnostic]:
    model = project.model
    manifest = project.manifest
    root = project.root
    rel_file = root / RELATIONSHIPS_FILE
    out: list[Diagnostic] = []

    def file_of(obj_id) -> Path | None:
        return project.origins.get(obj_id)

    # Uniqueness ---------------------------------------------------------------------------------

    for name in _duplicates(e.name for e in model.entities):
        files = [file_of(e.id) for e in model.entities if e.name.lower() == name]
        listed = ", ".join(str(f.relative_to(root)) if f and f.is_relative_to(root) else "?" for f in files)
        out.append(error("MK101", f"entity name {name!r} is used more than once ({listed})"))
    for name in _duplicates(s.name for s in model.sources):
        out.append(error("MK104", f"source name {name!r} is used more than once"))
    for name in _duplicates(m.name for m in model.metrics):
        out.append(error("MK105", f"metric name {name!r} is used more than once", root / METRICS_FILE))
    for name in _duplicates(r.name for r in model.relationships if r.name):
        out.append(error("MK106", f"relationship name {name!r} is used more than once", rel_file))

    # Every raw table gets a generated staging model (stg_<source>__<table>); a table of the same
    # name would be a second dbt model by that name, which dbt refuses.
    from murakumo.generate.plan import snake

    pattern = manifest.conventions.naming.get("staging", "stg_{source}__{entity}")
    staged = {
        pattern.format(source=snake(e.source or "raw"), entity=snake(e.name), name=snake(e.name)).lower(): e
        for e in model.entities
        if e.layer is Layer.RAW
    }
    for entity in model.entities:
        raw = staged.get(entity.name.lower()) if entity.layer is not Layer.RAW else None
        if raw is not None:
            out.append(
                error(
                    "MK107",
                    f"{entity.name} is the name of the staging model generated for {raw.name}; "
                    f"name it differently (int_{snake(raw.name)}, say), or map its columns in {raw.name}'s "
                    "staging instead",
                    file_of(entity.id),
                )
            )

    # Someone's own schema (on a shared warehouse) holds every layer, so table names must not repeat
    # across layers: a raw table, a model and an audit table of the same name would collide there.
    if manifest.collaboration.personal_schemas and manifest.targets.warehouse.type.value != "duckdb":
        audit_tables = {"run_log", "model_runs", "dq_results", "row_counts", "daily_summary", "murakumo_migrations"}
        owners: dict[str, list[str]] = {}
        for entity in model.entities:
            owners.setdefault(entity.name.lower(), []).append(f"{entity.layer.value} table {entity.name}")
        for name, raw in staged.items():
            owners.setdefault(name, []).append(f"the staging model of {raw.name}")
        for name in audit_tables:
            if name in owners:
                owners[name].append("an audit table")
        for name, who in owners.items():
            if len(who) > 1:
                out.append(
                    warning(
                        "MK108",
                        f"{' and '.join(who)} share the name {name}; in someone's own schema (one per person) "
                        "they would be the same table: rename one",
                    )
                )

    ids: list[str] = [str(o.id) for o in (*model.sources, *model.entities, *model.relationships, *model.metrics)]
    ids += [str(a.id) for e in model.entities for a in e.attributes]
    for dup in _duplicates(ids):
        out.append(error("MK103", f"id {dup} is used by more than one object; ids must be unique"))

    declared_sources = {s.name.lower() for s in model.sources} | {s.name.lower() for s in manifest.sources}
    declared_domains = {d.name.lower() for d in manifest.project.domains}

    def absent(name: str) -> bool:
        """True when an entity does not exist, and not merely because its file failed to load."""
        return model.entity(name) is None and name.lower() not in project.unloaded

    # Entities -----------------------------------------------------------------------------------

    for entity in model.entities:
        file = file_of(entity.id)
        out.extend(_check_entity(entity, file, declared_sources, declared_domains, manifest.conventions.case))

        for mapping in entity.mappings:
            where = f"mappings[{mapping.target}]"
            if entity.attribute(mapping.target) is None:
                out.append(
                    error("MK206", f"mapping target {mapping.target!r} is not a column of {entity.name}", file, where)
                )
            for ref in mapping.sources:
                source_entity, column = ref.split(".", 1)
                target = model.entity(source_entity)
                if absent(source_entity):
                    out.append(error("MK207", f"mapping source entity {source_entity!r} does not exist", file, where))
                elif target is not None and target.attribute(column) is None:
                    out.append(error("MK207", f"mapping source column {ref!r} does not exist", file, where))

    # Relationships ------------------------------------------------------------------------------

    for index, rel in enumerate(model.relationships):
        where = f"relationships[{rel.name or index}]"
        source, target = model.entity(rel.from_), model.entity(rel.to)
        for side, name, found in (("from", rel.from_, source), ("to", rel.to, target)):
            if found is None and absent(name):
                out.append(error("MK204", f"{side} entity {name!r} does not exist", rel_file, where))
        if source is None or target is None:
            continue

        missing = False
        for key in rel.keys:
            left, right = source.attribute(key.from_), target.attribute(key.to)
            if left is None:
                out.append(error("MK205", f"{source.name} has no column {key.from_!r}", rel_file, where))
            if right is None:
                out.append(error("MK205", f"{target.name} has no column {key.to!r}", rel_file, where))
            if left is None or right is None:
                missing = True
                continue
            if left.logical_type.family != right.logical_type.family:
                out.append(
                    warning(
                        "MK301",
                        f"join {source.name}.{left.name} ({left.type}) = {target.name}.{right.name} ({right.type}) "
                        "compares different types; generated joins will need a cast",
                        rel_file,
                        where,
                    )
                )
        if missing:
            continue

        # The one side of a relationship must be identified by its join keys.
        one_side = {
            Cardinality.MANY_TO_ONE: target,
            Cardinality.ONE_TO_MANY: source,
            Cardinality.ONE_TO_ONE: target,
        }.get(rel.cardinality)
        if one_side is not None:
            keys = {(k.to if one_side is target else k.from_).lower() for k in rel.keys}
            identifying = {a.name.lower() for a in one_side.attributes if a.key in (KeyRole.PRIMARY, KeyRole.NATURAL)}
            if not identifying or not identifying <= keys:
                out.append(
                    warning(
                        "MK302",
                        f"the one side ({one_side.name}) is joined on {', '.join(sorted(keys))}, "
                        "which is not its primary or natural key",
                        rel_file,
                        where,
                    )
                )

    # Schedules ----------------------------------------------------------------------------------

    from murakumo.schedules import problems as schedule_problems

    manifest_file = root / "murakumo.yaml"
    for code, level, message in schedule_problems(project):
        out.append((error if level == "error" else warning)(code, message, manifest_file, "orchestration"))

    # The stack ----------------------------------------------------------------------------------

    from murakumo.stack import check as stack_check

    for level, part, message in stack_check(project):
        if level == "error":
            out.append(error("MK701", message, manifest_file, f"stack.{part}"))
        elif "is not a raw table" in message:
            out.append(warning("MK702", message, manifest_file, "sources"))
    warehouse = manifest.targets.warehouse
    if warehouse.type.value == "snowflake":
        for layer, days in warehouse.time_travel_days.items():
            if layer in warehouse.transient_layers and days > 1:
                out.append(
                    warning(
                        "MK703",
                        f"{layer} tables are transient, which keep at most 1 day of time travel, not {days}: "
                        f"take {layer} out of transient_layers, or set its days to 0 or 1",
                        manifest_file,
                        "targets.warehouse",
                    )
                )

    # Metrics ------------------------------------------------------------------------------------

    for metric in model.metrics:
        if absent(metric.entity):
            out.append(
                error(
                    "MK208", f"entity {metric.entity!r} does not exist", root / METRICS_FILE, f"metrics[{metric.name}]"
                )
            )

    return out


def _check_entity(
    entity: Entity,
    file: Path | None,
    declared_sources: set[str],
    declared_domains: set[str],
    case: str,
) -> list[Diagnostic]:
    out: list[Diagnostic] = []

    for name in _duplicates(a.name for a in entity.attributes):
        out.append(error("MK102", f"column {name!r} appears more than once", file))

    if entity.source and entity.source.lower() not in declared_sources:
        out.append(
            error(
                "MK201",
                f"source {entity.source!r} is not declared; add model/sources/{entity.source}.yaml "
                "or list it under sources in murakumo.yaml",
                file,
                "source",
            )
        )
    if entity.domain and entity.domain.lower() not in declared_domains:
        out.append(error("MK202", f"domain {entity.domain!r} is not listed under project.domains", file, "domain"))

    def check_columns(field: str, columns: Iterable[str]) -> None:
        for column in columns:
            if entity.attribute(column) is None:
                out.append(error("MK203", f"{column!r} is not a column of {entity.name}", file, field))

    check_columns("grain", entity.grain)
    check_columns("cluster_by", entity.cluster_by)
    if entity.watermark:
        check_columns("watermark", [entity.watermark])
    for expression in entity.partition_by:
        match = PARTITION_EXPR.match(expression)
        if match is None:
            out.append(error("MK203", f"cannot read partition expression {expression!r}", file, "partition_by"))
        else:
            check_columns("partition_by", [match.group(1)])
    for index, rule in enumerate(entity.tests):
        check_columns(f"tests[{index}]", rule.columns)

    if not entity.attributes:
        out.append(warning("MK305", "entity has no columns", file))
    if entity.kind is EntityKind.FACT and not entity.grain:
        out.append(warning("MK303", "a fact table should declare its grain (the columns that make a row unique)", file))
    if entity.kind is EntityKind.CUMULATIVE:
        if entity.cumulative is None:
            out.append(
                error("MK310", "a cumulative table needs `cumulative: {date: entity.column}`", file, "cumulative")
            )
        if not entity.grain:
            out.append(error("MK312", "a cumulative table needs a grain: the key it accumulates per", file, "grain"))
    if entity.kind is EntityKind.DIMENSION and not entity.primary_key:
        out.append(warning("MK304", "a dimension should have a primary key", file))
    if entity.load is LoadPattern.INCREMENTAL and not entity.watermark and entity.layer is Layer.RAW:
        out.append(warning("MK308", "an incremental load needs a watermark column to know what is new", file, "load"))
    if entity.incremental_by == "watermark" and entity.load is LoadPattern.INCREMENTAL and not entity.watermark:
        out.append(
            warning(
                "MK313", "incremental_by: watermark needs a watermark column; it will rebuild in full", file, "load"
            )
        )
    if entity.history in (History.SCD2, History.SCD6) and not any(
        a.key in (KeyRole.PRIMARY, KeyRole.NATURAL) for a in entity.attributes
    ):
        out.append(warning("MK309", f"{entity.history} history needs a primary or natural key to track", file))
    if case == "snake" and entity.layer is not Layer.RAW and not SNAKE_CASE.match(entity.name):
        out.append(warning("MK307", f"entity name {entity.name!r} is not snake_case", file, "name"))

    for attribute in entity.attributes:
        where = f"attributes[{attribute.name}]"
        if attribute.key is KeyRole.PRIMARY and attribute.nullable:
            out.append(warning("MK311", "primary key column is nullable; set nullable: false", file, where))
        if attribute.pii and attribute.classification in (None, Classification.PUBLIC, Classification.INTERNAL):
            out.append(warning("MK306", "PII column should be classified confidential or restricted", file, where))
        if case == "snake" and entity.layer is not Layer.RAW and not SNAKE_CASE.match(attribute.name):
            out.append(warning("MK307", f"column name {attribute.name!r} is not snake_case", file, where))
        for index, rule in enumerate(attribute.tests):
            if rule.columns:
                out.append(
                    error(
                        "MK203",
                        "a column test applies to its own column; remove columns",
                        file,
                        f"{where}.tests[{index}]",
                    )
                )

    return out
