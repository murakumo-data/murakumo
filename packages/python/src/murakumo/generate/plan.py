"""Turn the model into a build plan: which dbt models exist, what each selects, and its tests.

The plan is warehouse-aware but template-free; emitters render it. Planning never raises on a
bad model: problems become diagnostics (MK5xx) and the affected model is skipped, so one broken
mapping does not stop the rest of the project from generating.

Layers:
- every raw entity gets a staging view: snake_case names, casts to the logical type, and the
  audit columns ``_source_system`` and ``_record_hash``;
- an entity in a modelled layer is built from its mappings. Its first source entity drives the
  query and the others are joined through the model's relationships;
- SCD2 dimensions go through an intermediate view and a dbt snapshot;
- incremental entities with a watermark only process new rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from murakumo.diagnostics import Diagnostic, error, warning
from murakumo.generate.targets import Target
from murakumo.ir.model import (
    Attribute,
    Entity,
    History,
    KeyRole,
    Layer,
    LoadPattern,
    QualityRule,
    Relationship,
    RuleType,
)
from murakumo.ir.types import parse_type
from murakumo.project import Project

# Columns the ingestion layer writes into every raw table, and staging passes through.
LANDING_COLUMNS = {"_ingested_at": "timestamp_tz", "_batch_id": "string"}
SNAPSHOT = "snp_{name}"
# Mapping expressions are written in DuckDB's SQL, the engine `murakumo verify` runs locally,
# and translated to each warehouse's dialect by SQLGlot.
EXPRESSION_DIALECT = "duckdb"
SCD2_PREP = "int_{name}__versions"


def snake(name: str) -> str:
    """``PULocationID`` -> ``pu_location_id``; ``VendorID`` -> ``vendor_id``; snake stays snake."""
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"_+", "_", text).strip("_").lower() or name.lower()


@dataclass
class Test:
    """A dbt data test on a column (``column`` set) or a model."""

    name: str  # unique, not_null, accepted_values, relationships, murakumo_range, ...
    column: str | None = None
    args: dict = field(default_factory=dict)
    severity: str = "error"


@dataclass
class Column:
    name: str
    sql: str  # the select expression, already in the target dialect
    data_type: str  # physical type
    description: str | None = None
    tests: list[Test] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


@dataclass
class Join:
    alias: str
    relation: str  # a ref() or source() call
    on: str


@dataclass
class DbtModel:
    name: str
    layer: str  # staging | intermediate | marts | ...
    folder: str  # path under models/
    entity: Entity
    relation: str  # what the FROM clause reads: {{ source(...) }} or {{ ref(...) }}
    alias: str
    columns: list[Column]
    materialized: str = "view"
    joins: list[Join] = field(default_factory=list)
    where: str | None = None
    incremental_filter: str | None = None
    unique_key: list[str] = field(default_factory=list)
    tests: list[Test] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    contract: bool = False
    description: str | None = None


@dataclass
class Snapshot:
    name: str
    relation: str
    unique_key: list[str]
    description: str | None = None


@dataclass
class SourceTable:
    entity: Entity
    columns: list[tuple[str, str, str | None]]  # quoted name, physical type, description


@dataclass
class SourceGroup:
    name: str
    schema: str
    description: str | None
    tables: list[SourceTable]
    freshness: dict | None = None


@dataclass
class Plan:
    project: Project
    target: Target
    sources: list[SourceGroup] = field(default_factory=list)
    models: list[DbtModel] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def model(self, name: str) -> DbtModel | None:
        return next((m for m in self.models if m.name == name), None)


class _Skip(Exception):
    """An entity cannot be planned; the diagnostic is already recorded."""


class Planner:
    def __init__(self, project: Project, target: Target):
        self.project = project
        self.model = project.model
        self.target = target
        self.conventions = project.manifest.conventions
        self.plan = Plan(project=project, target=target)
        self.contracts = "marts" in project.manifest.contracts.enforce_on

    # names ------------------------------------------------------------------------------------

    def quote(self, name: str) -> str:
        return quote_identifier(name, self.target.dialect)

    def staging_name(self, entity: Entity) -> str:
        pattern = self.conventions.naming.get("staging", "stg_{source}__{entity}")
        source, name = snake(entity.source or "raw"), snake(entity.name)
        return pattern.format(source=source, entity=name, name=name).lower()

    def raw_schema(self, source: str) -> str:
        return f"raw_{snake(source)}"

    def relation(self, entity: Entity) -> tuple[str, dict[str, str]]:
        """How a downstream model reads an entity, and how each of its columns is named there."""
        if entity.layer is Layer.RAW:
            names = {a.name.lower(): snake(a.name) for a in entity.attributes}
            return f"{{{{ ref('{self.staging_name(entity)}') }}}}", names
        names = {a.name.lower(): a.name for a in entity.attributes}
        return f"{{{{ ref('{entity.name}') }}}}", names

    def file(self, entity: Entity):
        return self.project.origins.get(entity.id)

    def updated_at(self) -> Column:
        """When the row was last built. Cast, because ``current_timestamp`` has a different type per
        warehouse (Snowflake: TIMESTAMP_LTZ) and enforced contracts compare types exactly."""
        data_type = self.target.physical_name("timestamp_tz")
        return Column("_updated_at", self.cast("current_timestamp", data_type), data_type)

    def physical(self, attribute: Attribute) -> str:
        return self.target.physical(attribute.logical_type)

    def cast(self, sql: str, data_type: str) -> str:
        return f"cast({sql} as {data_type})"

    # tests ------------------------------------------------------------------------------------

    def column_tests(self, entity: Entity, attribute: Attribute, column: str, history: bool = False) -> list[Test]:
        tests: list[Test] = []
        # A column is unique on its own only when it is the whole primary key; composite keys
        # get one murakumo_unique_combination test on the model instead.
        if attribute.key is KeyRole.PRIMARY and len(entity.primary_key) == 1 and not history:
            tests.append(Test("unique", column))
        if not attribute.nullable:
            tests.append(Test("not_null", column))
        if attribute.accepted_values:
            tests.append(Test("accepted_values", column, {"values": sorted(attribute.accepted_values)}))
        for rule in attribute.tests:
            tests.extend(self.rule_tests(rule, column))
        return _dedupe(tests)

    def rule_tests(self, rule: QualityRule, column: str | None, names: dict[str, str] | None = None) -> list[Test]:
        severity = rule.severity.value
        columns = [names.get(c.lower(), c) if names else c for c in rule.columns]
        match rule.type:
            case RuleType.UNIQUE:
                return [Test("unique", column, severity=severity)] if column else []
            case RuleType.NOT_NULL:
                return [Test("not_null", column, severity=severity)] if column else []
            case RuleType.ACCEPTED_VALUES:
                return [Test("accepted_values", column, {"values": rule.values or []}, severity)] if column else []
            case RuleType.RANGE:
                args = {k: v for k, v in (("min_value", rule.min), ("max_value", rule.max)) if v is not None}
                return [Test("murakumo_range", column, args, severity)] if column else []
            case RuleType.UNIQUE_COMBINATION:
                return [Test("murakumo_unique_combination", None, {"columns": columns}, severity)]
            case RuleType.CUSTOM_SQL:
                return [Test("murakumo_expression", column, {"expression": rule.sql}, severity)]
            case RuleType.FRESHNESS:
                return []  # freshness belongs to sources.yml, handled there
        return []

    # staging ----------------------------------------------------------------------------------

    def plan_staging(self, entity: Entity) -> DbtModel:
        alias = "source"
        columns: list[Column] = []
        for attribute in entity.attributes:
            name = snake(attribute.name)
            data_type = self.physical(attribute)
            columns.append(
                Column(
                    name=name,
                    sql=self.cast(f"{alias}.{self.quote(attribute.name)}", data_type),
                    data_type=data_type,
                    description=attribute.description,
                    tests=self.column_tests(entity, attribute, name),
                    meta=_meta(attribute),
                )
            )
        for name, logical in LANDING_COLUMNS.items():
            columns.append(Column(name, f"{alias}.{name}", self.target.physical(parse_type(logical))))
        columns.append(
            Column(
                "_source_system",
                f"'{entity.source}'",
                self.target.physical_name("string"),
                "Source system the row came from.",
            )
        )
        columns.append(
            Column(
                "_record_hash",
                f"{{{{ murakumo_record_hash({_quoted_list(self, entity)}, '{alias}') }}}}",
                self.target.physical_name("string"),
                "Hash of every source column, to detect changed rows.",
            )
        )
        tests = []
        names = {a.name.lower(): snake(a.name) for a in entity.attributes}
        for rule in entity.tests:
            tests.extend(self.rule_tests(rule, None, names))
        pk = [snake(a.name) for a in entity.primary_key]
        if len(pk) > 1:
            tests.append(Test("murakumo_unique_combination", None, {"columns": pk}))
        return DbtModel(
            name=self.staging_name(entity),
            layer="staging",
            folder=f"staging/{snake(entity.source or 'raw')}",
            entity=entity,
            relation=f"{{{{ source('{snake(entity.source or 'raw')}', '{entity.name}') }}}}",
            alias=alias,
            columns=columns,
            materialized="view",
            tests=tests,
            description=entity.description,
        )

    # modelled layers --------------------------------------------------------------------------

    def plan_modelled(self, entity: Entity) -> list[DbtModel]:
        if not entity.mappings:
            self.plan.diagnostics.append(
                warning("MK504", f"{entity.name} has no mappings, so there is nothing to generate", self.file(entity))
            )
            raise _Skip
        referenced = self.referenced_entities(entity)
        base = referenced[0]
        aliases = {base.name.lower(): _alias(base.name, set())}
        relation, _ = self.relation(base)
        joins = self.joins(entity, base, referenced[1:], aliases)

        columns: list[Column] = []
        mapped = {m.target.lower(): m for m in entity.mappings}
        history = entity.history in (History.SCD2, History.SCD6)
        for attribute in entity.attributes:
            data_type = self.physical(attribute)
            mapping = mapped.get(attribute.name.lower())
            if mapping is None:
                self.plan.diagnostics.append(
                    warning(
                        "MK503",
                        f"{entity.name}.{attribute.name} has no mapping; it will be null",
                        self.file(entity),
                        f"attributes[{attribute.name}]",
                    )
                )
                sql = "null"
            else:
                sql = self.mapping_sql(entity, mapping, aliases)
            columns.append(
                Column(
                    name=attribute.name,
                    sql=self.cast(sql, data_type),
                    data_type=data_type,
                    description=attribute.description,
                    tests=self.column_tests(entity, attribute, attribute.name, history=history),
                    meta=_meta(attribute),
                )
            )
        columns.append(self.updated_at())

        names = {a.name.lower(): a.name for a in entity.attributes}
        tests: list[Test] = []
        for rule in entity.tests:
            tests.extend(self.rule_tests(rule, None, names))
        unique_columns = list(entity.grain) or [a.name for a in entity.primary_key]
        if len(unique_columns) > 1 and not history:
            tests.append(Test("murakumo_unique_combination", None, {"columns": unique_columns}))

        incremental = entity.load is LoadPattern.INCREMENTAL and not history
        model = DbtModel(
            name=entity.name,
            layer=entity.layer.value,
            folder=f"{entity.layer.value}/{snake(entity.domain or 'core')}",
            entity=entity,
            relation=relation,
            alias=aliases[base.name.lower()],
            columns=columns,
            joins=joins,
            materialized="incremental" if incremental else "table",
            unique_key=list(entity.grain) or [a.name for a in entity.primary_key],
            tests=tests,
            contract=self.contracts and entity.layer is Layer.MARTS and not history,
            description=entity.description,
            config=self.warehouse_config(entity),
        )
        if incremental and entity.watermark:
            watermark = mapped.get(entity.watermark.lower())
            target_column = entity.attribute(entity.watermark)
            if watermark is not None and target_column is not None:
                floor = self.cast("'1900-01-01'", self.physical(target_column))
                model.incremental_filter = (
                    f"{self.mapping_sql(entity, watermark, aliases)} > "
                    f"(select coalesce(max({self.quote(entity.watermark)}), {floor}) from {{{{ this }}}})"
                )
        if not history:
            return [model]
        return self.scd2(entity, model)

    def scd2(self, entity: Entity, versions: DbtModel) -> list[DbtModel]:
        """SCD2: the mapped rows as a view, a snapshot that keeps every version, and the dimension on top."""
        keys = [a.name for a in entity.primary_key if a.key is KeyRole.PRIMARY] or [
            a.name for a in entity.attributes if a.key is KeyRole.NATURAL
        ]
        if not keys:
            self.plan.diagnostics.append(
                error("MK505", f"{entity.name} keeps scd2 history but has no primary or natural key", self.file(entity))
            )
            raise _Skip
        versions.name = SCD2_PREP.format(name=entity.name)
        versions.layer = "intermediate"
        versions.folder = f"intermediate/{snake(entity.domain or 'core')}"
        versions.materialized = "view"
        versions.contract = False
        versions.tests = []
        versions.columns = [c for c in versions.columns if c.name != "_updated_at"]
        for column in versions.columns:  # one row per key before the snapshot adds versions
            column.tests = [Test("unique", column.name)] if column.name in keys and len(keys) == 1 else []
        snapshot = Snapshot(
            name=SNAPSHOT.format(name=entity.name),
            relation=f"ref('{versions.name}')",
            unique_key=keys,
            description=f"Every version of {entity.name}.",
        )
        self.plan.snapshots.append(snapshot)

        timestamp = self.target.physical_name("timestamp")
        columns = [
            Column(c.name, f"versions.{self.quote(c.name)}", c.data_type, c.description, meta=c.meta)
            for c in versions.columns
        ]
        for column, attribute in zip(columns, entity.attributes, strict=False):
            column.tests = self.column_tests(entity, attribute, column.name, history=True)
        columns += [
            Column(
                "valid_from", self.cast("versions.dbt_valid_from", timestamp), timestamp, "When this version began."
            ),
            Column(
                "valid_to", self.cast("versions.dbt_valid_to", timestamp), timestamp, "When it ended; null if current."
            ),
            Column(
                "is_current",
                "versions.dbt_valid_to is null",
                self.target.physical_name("boolean"),
                "True for the latest version.",
            ),
            self.updated_at(),
        ]
        dimension = DbtModel(
            name=entity.name,
            layer=entity.layer.value,
            folder=f"{entity.layer.value}/{snake(entity.domain or 'core')}",
            entity=entity,
            relation=f"{{{{ ref('{snapshot.name}') }}}}",
            alias="versions",
            columns=columns,
            materialized="table",
            tests=[Test("murakumo_unique_combination", None, {"columns": [*keys, "valid_from"]})],
            contract=self.contracts and entity.layer is Layer.MARTS,
            description=entity.description,
            config=self.warehouse_config(entity),
        )
        return [versions, dimension]

    def referenced_entities(self, entity: Entity) -> list[Entity]:
        """The entities an entity's mappings read, in first-use order."""
        seen: dict[str, Entity] = {}
        for mapping in entity.mappings:
            for ref in mapping.sources:
                name = ref.split(".", 1)[0].lower()
                found = self.model.entity(name)
                if found is None:
                    self.plan.diagnostics.append(
                        error("MK207", f"mapping source entity {name!r} does not exist", self.file(entity))
                    )
                    raise _Skip
                seen.setdefault(name, found)
        if not seen:
            self.plan.diagnostics.append(
                error(
                    "MK506",
                    f"{entity.name}: no mapping names a source column, so there is no table to select from",
                    self.file(entity),
                )
            )
            raise _Skip
        return list(seen.values())

    def joins(self, entity: Entity, base: Entity, others: list[Entity], aliases: dict[str, str]) -> list[Join]:
        joins = []
        for other in others:
            candidates = [r for r in self.model.relationships if _connects(r, base, other)]
            if len(candidates) != 1:
                problem = "no relationship" if not candidates else f"{len(candidates)} relationships"
                self.plan.diagnostics.append(
                    error(
                        "MK501",
                        f"{entity.name} reads {base.name} and {other.name}, but there is {problem} between them; "
                        "the join needs exactly one",
                        self.file(entity),
                    )
                )
                raise _Skip
            rel = candidates[0]
            alias = _alias(other.name, set(aliases.values()))
            aliases[other.name.lower()] = alias
            _, base_names = self.relation(base)
            relation, other_names = self.relation(other)
            pairs = []
            for key in rel.keys:
                if rel.from_.lower() == base.name.lower():
                    left, right = key.from_, key.to
                else:
                    left, right = key.to, key.from_
                pairs.append(
                    f"{aliases[base.name.lower()]}.{self.quote(base_names[left.lower()])} = "
                    f"{alias}.{self.quote(other_names[right.lower()])}"
                )
            joins.append(Join(alias, relation, " and ".join(pairs)))
        return joins

    def mapping_sql(self, entity: Entity, mapping, aliases: dict[str, str]) -> str:
        """The mapping as SQL over the staged columns, in the target dialect."""
        sources = [ref.split(".", 1) for ref in mapping.sources]
        resolved: dict[str, tuple[str, str]] = {}  # lower column or entity.column -> (alias, column)
        for entity_name, column in sources:
            source_entity = self.model.entity(entity_name)
            _, names = self.relation(source_entity)
            if column.lower() not in names:
                self.plan.diagnostics.append(
                    error("MK207", f"mapping source column {entity_name}.{column} does not exist", self.file(entity))
                )
                raise _Skip
            target = (aliases[source_entity.name.lower()], names[column.lower()])
            resolved[f"{entity_name.lower()}.{column.lower()}"] = target
            resolved.setdefault(column.lower(), target)

        if not mapping.expression:
            if len(sources) != 1:
                self.plan.diagnostics.append(
                    error(
                        "MK502",
                        f"{entity.name}.{mapping.target} maps {len(sources)} columns; combine them with an expression",
                        self.file(entity),
                    )
                )
                raise _Skip
            alias, column = next(iter(resolved.values()))
            return f"{alias}.{self.quote(column)}"

        try:
            tree = sqlglot.parse_one(mapping.expression, read=EXPRESSION_DIALECT)
        except sqlglot.errors.ParseError as exc:
            self.plan.diagnostics.append(
                error("MK507", f"{entity.name}.{mapping.target}: cannot parse expression: {exc}", self.file(entity))
            )
            raise _Skip from None

        def rewrite(node: exp.Expression) -> exp.Expression:
            if not isinstance(node, exp.Column):
                return node
            key = f"{node.table.lower()}.{node.name.lower()}" if node.table else node.name.lower()
            if key not in resolved:
                return node  # a function argument or a column the user qualified themselves
            alias, column = resolved[key]
            return exp.column(column, table=alias, quoted=not re.fullmatch(r"[a-z_][a-z0-9_]*", column))

        return _text_concat_arguments(tree.transform(rewrite)).sql(dialect=self.target.dialect)

    def warehouse_config(self, entity: Entity) -> dict:
        config: dict = {}
        if entity.partition_by and self.target.dialect in ("databricks", "bigquery"):
            config["partition_by"] = entity.partition_by
        if entity.cluster_by and self.target.dialect in ("snowflake", "databricks", "bigquery"):
            config["cluster_by"] = entity.cluster_by
        return config

    # relationships ----------------------------------------------------------------------------

    def relationship_tests(self) -> None:
        """A dbt relationships test for each single-column relationship between generated models."""
        built = {m.entity.name.lower(): m for m in self.plan.models if m.layer != "intermediate"}
        for rel in self.model.relationships:
            child, parent = built.get(rel.from_.lower()), built.get(rel.to.lower())
            if child is None or parent is None or len(rel.keys) != 1:
                continue
            key = rel.keys[0]
            child_names = self.relation(child.entity)[1]
            parent_names = self.relation(parent.entity)[1]
            column = child_names.get(key.from_.lower())
            parent_column = parent_names.get(key.to.lower())
            if column is None or parent_column is None:
                continue
            for target in child.columns:
                if target.name == column:
                    target.tests.append(
                        Test("relationships", column, {"to": f"ref('{parent.name}')", "field": parent_column})
                    )

    # sources ----------------------------------------------------------------------------------

    def plan_sources(self, raw: list[Entity]) -> None:
        by_source: dict[str, list[Entity]] = {}
        for entity in raw:
            by_source.setdefault(entity.source or "raw", []).append(entity)
        for name in sorted(by_source):
            system = self.model.source(name)
            freshness = None
            if system and system.freshness:
                freshness = freshness_config(system.freshness)
            tables = [
                SourceTable(entity, [(self.quote(a.name), self.physical(a), a.description) for a in entity.attributes])
                for entity in sorted(by_source[name], key=lambda e: e.name.lower())
            ]
            self.plan.sources.append(
                SourceGroup(
                    name=snake(name),
                    schema=self.raw_schema(name),
                    description=system.description if system else None,
                    tables=tables,
                    freshness=freshness,
                )
            )

    # run --------------------------------------------------------------------------------------

    def run(self) -> Plan:
        entities = sorted(self.model.entities, key=lambda e: e.name.lower())
        raw = [e for e in entities if e.layer is Layer.RAW]
        self.plan_sources(raw)
        for entity in raw:
            self.plan.models.append(self.plan_staging(entity))
        for entity in [e for e in entities if e.layer is not Layer.RAW]:
            try:
                self.plan.models.extend(self.plan_modelled(entity))
            except _Skip:
                continue
        self.relationship_tests()
        return self.plan


def _text_concat_arguments(tree: exp.Expression) -> exp.Expression:
    """Cast concat and concat_ws arguments to text, as DuckDB does implicitly.

    Other warehouses need it explicit: SQLGlot keeps DuckDB's null handling by wrapping each
    argument in ``coalesce(x, '')``, and on Snowflake that fails for a number column.
    """
    for node in list(tree.find_all(exp.Concat, exp.ConcatWs)):
        arguments = node.expressions
        start = 1 if isinstance(node, exp.ConcatWs) else 0  # the separator stays as it is
        node.set(
            "expressions",
            arguments[:start]
            + [a if isinstance(a, exp.Literal) and a.is_string else exp.cast(a, "VARCHAR") for a in arguments[start:]],
        )
    return tree


def quote_identifier(name: str, dialect: str) -> str:
    """Quote identifiers that are not plain lower snake_case, in the target's dialect."""
    if re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        return name
    return exp.to_identifier(name, quoted=True).sql(dialect=dialect)


def build_plan(project: Project, target: Target) -> Plan:
    return Planner(project, target).run()


def _connects(rel: Relationship, a: Entity, b: Entity) -> bool:
    pair = {rel.from_.lower(), rel.to.lower()}
    return pair == {a.name.lower(), b.name.lower()}


def _alias(name: str, taken: set[str]) -> str:
    base = snake(name)[:30] or "t"
    alias, n = base, 2
    while alias in taken:
        alias, n = f"{base}_{n}", n + 1
    return alias


def _dedupe(tests: list[Test]) -> list[Test]:
    seen, out = set(), []
    for test in tests:
        key = (test.name, test.column, repr(sorted(test.args.items())))
        if key not in seen:
            seen.add(key)
            out.append(test)
    return out


def _meta(attribute: Attribute) -> dict:
    meta: dict = {}
    if attribute.pii:
        meta["pii"] = True
    if attribute.classification:
        meta["classification"] = attribute.classification.value
    if attribute.unit:
        meta["unit"] = attribute.unit
    if attribute.business_term:
        meta["business_term"] = attribute.business_term
    return meta


def freshness_config(freshness) -> dict | None:
    out = {}
    if freshness.warn_after_hours:
        out["warn_after"] = {"count": freshness.warn_after_hours, "period": "hour"}
    if freshness.error_after_hours:
        out["error_after"] = {"count": freshness.error_after_hours, "period": "hour"}
    return out or None


def _quoted_list(planner: Planner, entity: Entity) -> str:
    return "[" + ", ".join(repr(planner.quote(a.name)) for a in entity.attributes) + "]"
