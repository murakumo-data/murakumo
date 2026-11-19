"""Data contracts in the Open Data Contract Standard (ODCS v3.0.2).

A contract says what a table holds and promises: its columns and types, which may be empty, its
key, its quality rules and how fresh it is. Murakumo writes one for every raw table (the contract
with whoever sends the data) and every modelled table (the contract with whoever uses it), from
the model, into ``generated/contracts/``. A contract someone sends is read back with
``murakumo import odcs``, and ``murakumo contract check`` says whether the latest load kept to it.

Every rule is also kept as a custom property (``murakumoRule``), so a contract read back gives
the same rules exactly; other tools read the ODCS fields (``required``, ``unique``,
``logicalTypeOptions``, ``quality``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from murakumo.ir.model import (
    Attribute,
    Classification,
    ContractTerms,
    Entity,
    KeyRole,
    Layer,
    QualityRule,
    RuleType,
)
from murakumo.ir.types import TypeName
from murakumo.project import Project

API_VERSION = "v3.0.2"
RULE_PROPERTY = "murakumoRule"

_LOGICAL = {
    "integer": (TypeName.SMALLINT, TypeName.INTEGER, TypeName.BIGINT),
    "number": (TypeName.FLOAT, TypeName.DOUBLE, TypeName.DECIMAL, TypeName.MONEY),
    "boolean": (TypeName.BOOLEAN,),
    "date": (TypeName.DATE, TypeName.TIMESTAMP, TypeName.TIMESTAMP_TZ, TypeName.TIME),
    "array": (TypeName.ARRAY,),
    "object": (TypeName.STRUCT, TypeName.JSON),
}


def logical_type(attribute: Attribute) -> str:
    name = attribute.logical_type.name
    return next((logical for logical, names in _LOGICAL.items() if name in names), "string")


def _rule_data(rule: QualityRule) -> dict:
    return rule.model_dump(mode="json", exclude_defaults=True, exclude={"id"})


def _renamed(sql: str, names: dict[str, str], dialect: str) -> str:
    """``sql`` with its columns renamed: a rule written for staging (location_id) read on the raw
    table (LocationID)."""
    import sqlglot
    from sqlglot import exp

    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.ParseError:
        return sql
    for column in tree.find_all(exp.Column):
        found = names.get(column.name.lower())
        if found:
            column.set("this", exp.to_identifier(found, quoted=found != found.lower()))
    return tree.sql(dialect=dialect)


def _quality(rule: QualityRule, table: str, column: str | None, kept: QualityRule | None = None) -> dict:
    """One rule as an ODCS quality entry: a library rule where ODCS has one, else SQL that counts
    the rows breaking it (it must be 0)."""
    base: dict[str, Any] = {"severity": "error" if rule.severity.value == "error" else "warning"}
    if rule.description:
        base["description"] = rule.description
    base["customProperties"] = [{"property": RULE_PROPERTY, "value": _rule_data(kept or rule)}]
    where = None
    match rule.type:
        case RuleType.UNIQUE:
            return {"type": "library", "rule": "duplicateCount", "mustBe": 0, "dimension": "uniqueness", **base}
        case RuleType.ROW_COUNT:
            bounds = _bounds(rule)
            return {"type": "library", "rule": "rowCount", **bounds, "dimension": "completeness", **base}
        case RuleType.NOT_NULL:
            where = f"{column} is null"
        case RuleType.ACCEPTED_VALUES:
            listed = ", ".join("'" + str(v).replace("'", "''") + "'" for v in rule.values or [])
            where = f"{column} is not null and {column} not in ({listed})"
        case RuleType.RANGE:
            parts = [f"{column} < {rule.min}"] if rule.min is not None else []
            parts += [f"{column} > {rule.max}"] if rule.max is not None else []
            where = f"{column} is not null and ({' or '.join(parts)})"
        case RuleType.CUSTOM_SQL:
            where = f"not ({rule.sql})"
        case RuleType.UNIQUE_COMBINATION:
            columns = ", ".join(rule.columns)
            query = (
                f"select count(*) from (select {columns} from {table} group by {columns} having count(*) > 1) as dupes"
            )
            return {"type": "sql", "query": query, "mustBe": 0, "dimension": "uniqueness", **base}
    if where is not None:
        dimension = "completeness" if rule.type is RuleType.NOT_NULL else "conformity"
        return {
            "type": "sql",
            "query": f"select count(*) from {table} where {where}",
            "mustBe": 0,
            "dimension": dimension,
            **base,
        }
    # regex, length, null share, distinct count, recent: described, with the rule kept exactly.
    text = _described(rule)
    return {
        "type": "text",
        "description": rule.description or text,
        **{k: v for k, v in base.items() if k != "description"},
    }


def _described(rule: QualityRule) -> str:
    match rule.type:
        case RuleType.REGEX:
            return f"Values match {rule.pattern}."
        case RuleType.LENGTH:
            return f"Text is {_between(rule)} characters long."
        case RuleType.NULL_SHARE:
            return f"At most {(rule.max or 0):.0%} of values are empty."
        case RuleType.DISTINCT_COUNT:
            return f"There are {_between(rule)} distinct values."
        case RuleType.RECENT:
            return f"The newest value is at most {rule.max_age_hours:g} hours old."
        case RuleType.FRESHNESS:
            return f"Loaded at least every {rule.max_age_hours:g} hours."
    return rule.type.value


def _bounds(rule: QualityRule) -> dict:
    if rule.min is not None and rule.max is not None:
        return {"mustBeBetween": [rule.min, rule.max]}
    if rule.min is not None:
        return {"mustBeGreaterOrEqualTo": rule.min}
    return {"mustBeLessOrEqualTo": rule.max}


def _between(rule: QualityRule) -> str:
    if rule.min is not None and rule.max is not None:
        return f"{rule.min:g} to {rule.max:g}"
    return f"at least {rule.min:g}" if rule.min is not None else f"at most {rule.max:g}"


def document(
    project: Project,
    entity: Entity,
    physical: dict[str, str],
    table: str,
    columns: dict[str, str],
    quote=lambda name: name,
    dialect: str | None = None,
) -> dict:
    """The ODCS contract of ``entity``. ``table`` is where it is built (schema.name), ``columns``
    each attribute's name there and ``physical`` each one's type there."""
    terms = entity.contract or ContractTerms()
    properties = []
    primary = entity.primary_key
    for attribute in entity.attributes:
        column = columns[attribute.name.lower()]
        prop: dict[str, Any] = {"name": column, "logicalType": logical_type(attribute)}
        if attribute.description:
            prop["description"] = attribute.description
        prop["physicalType"] = physical[attribute.name.lower()]
        if column != attribute.name:
            prop["businessName"] = attribute.name
        prop["required"] = not attribute.nullable or attribute.key is KeyRole.PRIMARY
        if attribute.key is KeyRole.PRIMARY:
            prop["primaryKey"] = True
            prop["primaryKeyPosition"] = primary.index(attribute) + 1
            prop["unique"] = len(primary) == 1
        if attribute.classification is not None:
            prop["classification"] = attribute.classification.value
        if attribute.pii:
            prop["criticalDataElement"] = True
            prop.setdefault("tags", []).append("pii")
        if attribute.example:
            prop["examples"] = [attribute.example]
        options = _options(attribute)
        if options:
            prop["logicalTypeOptions"] = options
        quality = [_quality(rule, table, quote(column)) for rule in _rules(attribute)]
        if quality:
            prop["quality"] = quality
        properties.append(prop)
    names = {a.name.lower(): columns[a.name.lower()] for a in entity.attributes}
    raw_names: dict[str, str] = {}
    if entity.layer is Layer.RAW and dialect:
        # A raw table's rules use staging's names (snake_case); its contract reads the raw table.
        from murakumo.generate.plan import snake

        raw_names = {snake(a.name): a.name for a in entity.attributes}
        names.update(raw_names)
    table_rules = []
    for rule in entity.tests:
        shown = rule.model_copy(
            update={
                "columns": [quote(names.get(c.lower(), c)) for c in rule.columns],
                "sql": _renamed(rule.sql, raw_names, dialect) if rule.sql and raw_names else rule.sql,
            }
        )
        table_rules.append((shown, rule))
    if entity.freshness is not None and entity.freshness.error_after_hours:
        fresh = QualityRule(type="freshness", max_age_hours=entity.freshness.error_after_hours)
        table_rules.append((fresh, fresh))
    obj: dict[str, Any] = {
        "name": entity.name,
        "physicalName": table,
        "physicalType": "table",
        "logicalType": "object",
    }
    if entity.description:
        obj["description"] = entity.description
    if entity.grain:
        obj["dataGranularityDescription"] = "One row per " + ", ".join(entity.grain) + "."
    obj["properties"] = properties
    if table_rules:
        obj["quality"] = [_quality(shown, table, None, kept) for shown, kept in table_rules]
    contract: dict[str, Any] = {
        "apiVersion": API_VERSION,
        "kind": "DataContract",
        "id": str(entity.id),
        "name": entity.name,
        "version": terms.version,
        "status": terms.status,
    }
    if entity.domain:
        contract["domain"] = entity.domain
    contract["dataProduct"] = project.manifest.project.name
    if entity.description:
        contract["description"] = {"purpose": entity.description}
    contract["schema"] = [obj]
    sla = []
    freshness = entity.freshness
    if freshness is None and entity.source:
        freshness = getattr(project.model.source(entity.source), "freshness", None)
    if freshness is not None and (freshness.error_after_hours or freshness.warn_after_hours):
        hours = freshness.error_after_hours or freshness.warn_after_hours
        sla.append({"property": "latency", "value": hours, "unit": "h", "element": f"{entity.name}._ingested_at"})
    if sla:
        contract["slaProperties"] = sla
    tags = list(entity.tags)
    if entity.layer is Layer.RAW:
        tags.append("source")
    if tags:
        contract["tags"] = tags
    custom = [{"property": "enforce", "value": terms.enforce}, {"property": "layer", "value": entity.layer.value}]
    if entity.source:
        custom.append({"property": "source", "value": entity.source})
    if entity.owner:
        custom.append({"property": "owner", "value": entity.owner})
    contract["customProperties"] = custom
    return contract


def _rules(attribute: Attribute) -> list[QualityRule]:
    rules = []
    if attribute.accepted_values:
        rules.append(QualityRule(type="accepted_values", values=sorted(attribute.accepted_values)))
    return rules + list(attribute.tests)


def _options(attribute: Attribute) -> dict:
    options: dict[str, Any] = {}
    logical = attribute.logical_type
    if logical.name is TypeName.STRING and logical.params:
        options["maxLength"] = logical.params[0]
    for rule in attribute.tests:
        if rule.type is RuleType.REGEX and rule.pattern:
            options["pattern"] = rule.pattern
        elif rule.type is RuleType.LENGTH:
            if rule.min is not None:
                options["minLength"] = int(rule.min)
            if rule.max is not None:
                options["maxLength"] = int(rule.max)
        elif rule.type is RuleType.RANGE:
            if rule.min is not None:
                options["minimum"] = rule.min
            if rule.max is not None:
                options["maximum"] = rule.max
    return options


def render(project: Project, plan) -> dict[str, str]:
    """A contract per raw table and per modelled table, under contracts/."""
    if project.manifest.contracts.standard != "odcs":
        return {}
    from murakumo.generate.plain import LAYER_SCHEMAS
    from murakumo.generate.plan import is_history, quote_identifier, snake

    def quote(name: str) -> str:
        return quote_identifier(name, plan.target.dialect)

    files = {}
    header = "# Generated by Murakumo from the model (ODCS v3.0.2). Change the model, then run `murakumo generate`.\n"
    for group in plan.sources:
        for raw in group.tables:
            entity = raw.entity
            physical = {a.name.lower(): plan.target.physical(a.logical_type) for a in entity.attributes}
            columns = {a.name.lower(): a.name for a in entity.attributes}
            table = f"{quote(group.schema)}.{quote(entity.name)}"
            body = document(project, entity, physical, table, columns, quote, plan.target.dialect)
            files[f"contracts/{entity.name}.odcs.yaml"] = header + _dump(body)
    for model in plan.models:
        entity = model.entity
        if entity.layer is Layer.RAW or is_history(model) or model.name != entity.name:
            continue
        built = {c.name.lower(): c for c in model.columns}
        physical, columns = {}, {}
        for attribute in entity.attributes:
            column = built.get(attribute.name.lower()) or built.get(snake(attribute.name))
            columns[attribute.name.lower()] = column.name if column else attribute.name
            physical[attribute.name.lower()] = (
                column.data_type if column else plan.target.physical(attribute.logical_type)
            )
        schema = LAYER_SCHEMAS.get(model.layer, model.layer)
        body = document(project, entity, physical, f"{quote(schema)}.{quote(model.name)}", columns, quote)
        files[f"contracts/{entity.name}.odcs.yaml"] = header + _dump(body)
    return files


def _dump(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110)


# Reading a contract ------------------------------------------------------------------------------


_TYPES = {
    "integer": "bigint",
    "number": "double",
    "boolean": "boolean",
    "date": "date",
    "array": "array<string>",
    "object": "json",
}


@dataclass
class Imported:
    entity: str
    added: list[str] = field(default_factory=list)  # columns
    rules: int = 0
    different: list[str] = field(default_factory=list)  # what the contract says and the model does not
    new: bool = False


def read(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from None
    if (
        not isinstance(data, dict)
        or data.get("kind") != "DataContract"
        or not str(data.get("apiVersion", "")).startswith("v3")
    ):
        raise ValueError(f"{path.name} is not an ODCS v3 data contract (kind: DataContract, apiVersion: v3...)")
    if not data.get("schema"):
        raise ValueError(f"{path.name} has no schema: nothing to import")
    return data


def _type_of(prop: dict) -> str:
    physical = str(prop.get("physicalType") or "").lower()
    if physical:
        from murakumo.importers.typemap import sql_to_logical

        found, known = sql_to_logical(physical)
        if known:
            return found
    if prop.get("logicalType") == "date" and "time" in physical:
        return "timestamp"
    return _TYPES.get(str(prop.get("logicalType")), "string")


def _rule_from(entry: dict, column: str | None) -> QualityRule | None:
    """A rule from a quality entry: exactly, from the custom property Murakumo writes, or from the
    ODCS library rules other tools write."""
    for prop in entry.get("customProperties") or []:
        if prop.get("property") == RULE_PROPERTY and isinstance(prop.get("value"), dict):
            try:
                return QualityRule(**prop["value"])
            except ValueError:
                return None
    severity = "warn" if str(entry.get("severity", "")).lower().startswith("warn") else "error"
    rule = entry.get("rule") or entry.get("metric")
    if rule in ("duplicateCount", "duplicateValues") and column:
        return QualityRule(type="unique", severity=severity)
    if rule in ("nullValues", "nullCount") and column:
        return QualityRule(type="not_null", severity=severity)
    if rule == "rowCount":
        low = entry.get("mustBeGreaterOrEqualTo", entry.get("mustBeGreaterThan"))
        high = entry.get("mustBeLessOrEqualTo", entry.get("mustBeLessThan"))
        if entry.get("mustBeBetween"):
            low, high = entry["mustBeBetween"]
        if low is not None or high is not None:
            return QualityRule(type="row_count", min=low, max=high, severity=severity)
    valid = entry.get("validValues") or (entry.get("arguments") or {}).get("validValues")
    if rule in ("validValues", "invalidValues") and valid and column:
        return QualityRule(type="accepted_values", values=[str(v) for v in valid], severity=severity)
    pattern = (entry.get("arguments") or {}).get("pattern")
    if rule == "invalidValues" and pattern and column:
        return QualityRule(type="regex", pattern=pattern, severity=severity)
    return None


def apply(project: Project, data: dict, source: str | None = None) -> list[Imported]:
    """Bring a contract into the model: a raw table per schema object (under ``source``), its
    columns, keys and rules. A table already in the model gains what it lacks; what differs is
    reported, not changed."""
    from murakumo import edit
    from murakumo.ir.model import SourceSystem, SourceType

    results = []
    custom = {p.get("property"): p.get("value") for p in data.get("customProperties") or [] if isinstance(p, dict)}
    source = source or custom.get("source") or data.get("dataProduct") or data.get("domain") or "contracts"
    source = str(source).replace("-", "_").replace(" ", "_").lower()
    if not project.model.source(source):
        project.model.sources.append(
            SourceSystem(name=source, type=SourceType.MANUAL, description="From a data contract.")
        )
    for obj in data["schema"]:
        name = str(obj.get("name") or "").strip()
        if not name:
            continue
        entity = project.model.entity(name)
        result = Imported(name, new=entity is None)
        if entity is None:
            entity = edit.add_entity(project, name, layer="raw", source=source)
            entity.description = obj.get("description")
        terms = ContractTerms(
            version=str(data.get("version") or "1.0.0"),
            status=str(data.get("status") or "active")
            if data.get("status") in ("proposed", "draft", "active", "deprecated", "retired")
            else "active",
            enforce=str(custom.get("enforce") or "block") if custom.get("enforce") in ("block", "warn") else "block",
        )
        entity.contract = terms
        for prop in obj.get("properties") or []:
            column = str(prop.get("name") or "").strip()
            if not column:
                continue
            kind = _type_of(prop)
            attribute = entity.attribute(column)
            if attribute is None:
                attribute = edit.add_column(
                    project, entity.name, column, kind, primary_key=bool(prop.get("primaryKey"))
                )
                result.added.append(column)
            elif attribute.logical_type.family != Attribute(name="x", type=kind).logical_type.family:
                result.different.append(f"{column}: the contract says {kind}, the model {attribute.type}")
            required = bool(prop.get("required")) or bool(prop.get("primaryKey"))
            if required and attribute.nullable:
                attribute.nullable = False
            if prop.get("description") and not attribute.description:
                attribute.description = prop["description"]
            if prop.get("classification") in {c.value for c in Classification}:
                attribute.classification = Classification(prop["classification"])
            if prop.get("examples") and not attribute.example:
                attribute.example = str(prop["examples"][0])
            options = prop.get("logicalTypeOptions") or {}
            rules = [r for r in (_rule_from(q, column) for q in prop.get("quality") or []) if r]
            have = {(r.type, r.pattern, r.min, r.max) for r in rules}
            if options.get("pattern") and not any(r.type is RuleType.REGEX for r in rules):
                rules.append(QualityRule(type="regex", pattern=options["pattern"]))
            if (options.get("minLength") is not None or ("maxLength" in options and kind == "string")) and not any(
                r.type is RuleType.LENGTH for r in rules
            ):
                if options.get("minLength") is not None:
                    rules.append(QualityRule(type="length", min=options.get("minLength"), max=options.get("maxLength")))
            if (options.get("minimum") is not None or options.get("maximum") is not None) and not any(
                r.type is RuleType.RANGE for r in rules
            ):
                rules.append(QualityRule(type="range", min=options.get("minimum"), max=options.get("maximum")))
            if prop.get("unique") and not prop.get("primaryKey") and not any(r.type is RuleType.UNIQUE for r in rules):
                rules.append(QualityRule(type="unique"))
            del have
            existing = {r.model_dump_json(exclude={"id"}) for r in attribute.tests}
            for rule in rules:
                if (
                    rule.type is RuleType.ACCEPTED_VALUES
                    and attribute.accepted_values
                    and set(attribute.accepted_values) == set(rule.values or [])
                ):
                    continue
                if rule.model_dump_json(exclude={"id"}) not in existing:
                    attribute.tests.append(rule)
                    result.rules += 1
        existing = {r.model_dump_json(exclude={"id"}) for r in entity.tests}
        for entry in obj.get("quality") or []:
            rule = _rule_from(entry, None)
            if rule is None or rule.type is RuleType.FRESHNESS:
                continue
            if rule.model_dump_json(exclude={"id"}) not in existing:
                entity.tests.append(rule)
                result.rules += 1
        results.append(result)
    return results


# Checking the latest load against the contract ---------------------------------------------------


@dataclass
class Breach:
    table: str
    level: str  # error | warn
    what: str  # schema | type | rows | rules | late
    message: str


def check(
    project: Project,
    environment: str | None = None,
    prefix: str | None = None,
    only: list[str] | None = None,
    loading: bool = False,
) -> dict[str, Any]:
    """Whether each raw table's latest load kept to its contract: every column it promises, of a
    type that fits; no rows set aside or rules failed beyond what the rules allow; on time."""
    from datetime import UTC, datetime

    from murakumo.environments import EnvError, connect, placement_for
    from murakumo.generate.plan import build_plan
    from murakumo.generate.targets import target_for
    from murakumo.manifest import Warehouse

    warehouse = project.manifest.targets.warehouse.type
    plan = build_plan(project, target_for(warehouse))
    try:
        placement = placement_for(project, environment, warehouse, prefix)
        connection = connect(project, read_only=True, database=placement.database, environment=placement.environment)
    except EnvError as exc:
        return {"checked": False, "reason": str(exc), "tables": [], "breaches": []}
    qualifier = "" if warehouse is Warehouse.DUCKDB else f"{connection.database}."
    breaches: list[Breach] = []
    checked = []
    try:
        schemas = connection.schemas()
        for group in plan.sources:
            for raw in group.tables:
                entity = raw.entity
                if only is not None and entity.name not in only:
                    continue
                schema = placement.schema(group.schema)
                if schema.lower() not in schemas or entity.name.lower() not in {
                    t.lower() for t in connection.tables(schema)
                }:
                    continue
                checked.append(entity.name)
                enforce = (entity.contract or ContractTerms()).enforce
                level = "error" if enforce == "block" else "warn"
                columns = _columns(connection, schema, entity.name)
                for attribute in entity.attributes:
                    found = columns.get(attribute.name.lower())
                    if found is None:
                        # Staging reads every column it promises: a missing one breaks the build either way.
                        required = not attribute.nullable or attribute.key is KeyRole.PRIMARY
                        breaches.append(
                            Breach(
                                entity.name,
                                level,
                                "schema",
                                f"{attribute.name} is missing; the contract promises it"
                                + (" and that it is never empty" if required else ""),
                            )
                        )
                    elif not _fits(attribute, found):
                        breaches.append(
                            Breach(
                                entity.name,
                                level,
                                "type",
                                f"{attribute.name} is {found}; the contract says {attribute.type}",
                            )
                        )
                extra = sorted(
                    set(columns)
                    - {a.name.lower() for a in entity.attributes}
                    - {"_ingested_at", "_batch_id", "_dlt_load_id", "_dlt_id"}
                )
                if extra:
                    breaches.append(
                        Breach(
                            entity.name,
                            "warn",
                            "schema",
                            f"not in the contract: {', '.join(extra)} (kept in raw, not passed on)",
                        )
                    )
                table = f"{qualifier}{schema}.{entity.name}"
                latest = (
                    connection.execute(
                        f"select _batch_id, count(*), max(_ingested_at) from {table} "
                        "group by _batch_id order by max(_ingested_at) desc limit 1",
                        fetch=True,
                    )
                    if "_batch_id" in columns
                    else []
                )
                if latest:
                    batch, rows, arrived = latest[0]
                    for rule in entity.tests:
                        if rule.type is RuleType.ROW_COUNT and (
                            (rule.min is not None and rows < rule.min) or (rule.max is not None and rows > rule.max)
                        ):
                            breaches.append(
                                Breach(
                                    entity.name,
                                    "error" if rule.severity.value == "error" else "warn",
                                    "rows",
                                    f"the latest load brought {rows:,} rows; the contract says {_between(rule)}",
                                )
                            )
                    # While loading, the quarantine table is not rebuilt yet: it is counted after the build.
                    aside = 0 if loading else _set_aside(connection, plan, entity, placement, qualifier, batch)
                    if aside:
                        share = aside / rows if rows else 0
                        tolerance = project.manifest.quality.monitoring.rejected_tolerance
                        breaches.append(
                            Breach(
                                entity.name,
                                "error" if share > tolerance and enforce == "block" else "warn",
                                "rules",
                                f"{aside:,} of the latest load's {rows:,} rows broke a quarantine rule: set aside",
                            )
                        )
                    hours = entity.freshness.error_after_hours if entity.freshness else None
                    if hours and arrived is not None:
                        when = arrived if getattr(arrived, "tzinfo", None) else arrived.replace(tzinfo=UTC)
                        age = (datetime.now(UTC) - when).total_seconds() / 3600
                        if age > hours:
                            breaches.append(
                                Breach(
                                    entity.name,
                                    level,
                                    "late",
                                    f"last loaded {age:.0f} hours ago; the contract says every {hours:g}",
                                )
                            )
    finally:
        connection.close()
    return {
        "checked": True,
        "tables": checked,
        "breaches": [b.__dict__ for b in sorted(breaches, key=lambda b: (b.level != "error", b.table, b.what))],
    }


def _columns(connection, schema: str, table: str) -> dict[str, str]:
    from murakumo.manifest import Warehouse

    if connection.warehouse is Warehouse.SNOWFLAKE:
        rows = connection.execute(f"describe table {connection.database}.{schema}.{table}", fetch=True)
        return {str(r[0]).lower(): str(r[1]).lower() for r in rows}
    catalog = "and table_catalog = current_database()" if connection.warehouse is Warehouse.DUCKDB else ""
    rows = connection.execute(
        "select column_name, data_type from information_schema.columns "
        f"where lower(table_schema) = '{schema.lower()}' and lower(table_name) = '{table.lower()}' {catalog}",
        fetch=True,
    )
    return {str(r[0]).lower(): str(r[1]).lower() for r in rows}


_FAMILY_WORDS = {
    "integer": ("int", "number(38,0)", "decimal(38,0)", "numeric"),
    "number": ("int", "float", "double", "real", "decimal", "numeric", "number", "money"),
    "boolean": ("bool", "bit"),
    "date": ("date", "time"),
    "timestamp": ("time", "date"),
    "string": ("char", "text", "string", "varchar", "uuid", "json", "variant"),
}


def _fits(attribute: Attribute, physical: str) -> bool:
    """Whether a column's type in the warehouse can hold what the contract promises. Raw tables
    kept as text hold anything; staging converts it."""
    physical = physical.lower()
    if any(word in physical for word in ("char", "text", "string", "variant")):
        return True
    family = attribute.logical_type.family
    words = _FAMILY_WORDS.get(family)
    return True if words is None else any(word in physical for word in words)


def _set_aside(connection, plan, entity: Entity, placement, prefix: str, batch) -> int:
    from murakumo.generate.plan import snake

    name = next((m.name for m in plan.models if m.entity is entity and m.extra.get("side") == "quarantined"), None)
    if name is None:
        return 0
    del snake
    schema = placement.schema("staging")
    try:
        rows = connection.execute(
            f"select count(*) from {prefix}{schema}.{name} where _batch_id = '{str(batch).replace(chr(39), '')}'",
            fetch=True,
        )
    except Exception:  # noqa: BLE001 - not built yet
        return 0
    return int(rows[0][0]) if rows else 0


__all__ = ["API_VERSION", "Breach", "Imported", "apply", "check", "document", "read", "render"]
