"""Exploring the data before modelling it: what is loaded, a table's rows before it is built, and
the questions worth asking a table first.

Everything here reads, and is kept cheap on purpose:

- ``status`` asks the warehouse's metadata which raw tables exist and how many rows they hold;
  no table is scanned (DuckDB counts, which is free on this computer; Snowflake's ``SHOW TABLES``
  needs no running warehouse);
- ``preview`` runs a model's SQL as one query, before anything is built: the models it reads are
  folded in as CTEs, down to the raw tables, and each raw table is read as a sample of its first
  rows, so a preview costs the same on a billion rows as on a thousand. ``full`` reads every row,
  after ``estimate`` has said what that costs;
- ``suggested`` writes questions from the model itself (its keys, dates, categories and joins),
  from templates: no AI, nothing to run until someone runs one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import sqlglot

from murakumo.ir.model import Entity, KeyRole
from murakumo.ir.types import parse_type
from murakumo.manifest import Warehouse
from murakumo.project import Project

SAMPLE = 10_000  # rows read from each raw table by a preview, a profile or an overlap check
PREVIEW_ROWS = 100

_REF = re.compile(r"ref\('([^']+)'\)")
_SOURCE = re.compile(r"source\('([^']+)',\s*'([^']+)'\)")


class ExploreError(Exception):
    pass


# Where each table is -----------------------------------------------------------------------------


@dataclass
class Located:
    entity: str
    layer: str
    schema: str  # as placed in the environment
    name: str  # the relation's own name (a model's name, which may differ from its entity's)
    relation: str  # qualified and quoted, ready for FROM


def _final(chosen, model):
    """Of an entity's models, the one that builds it: the one named after it, else the last (the
    plan lists models in the order they build, so an SCD's versions come before its table)."""
    if chosen is not None and chosen.name == chosen.entity.name:
        return chosen
    return model


class Places:
    """Where every raw table and model of the project is, in one environment."""

    def __init__(self, project: Project, environment: str | None = None):
        from murakumo.environments import EnvError, placement_for
        from murakumo.generate.plain import LAYER_SCHEMAS
        from murakumo.generate.plan import build_plan, is_history
        from murakumo.generate.targets import target_for

        self.project = project
        self.warehouse = project.manifest.targets.warehouse.type
        self.target = target_for(self.warehouse)
        self.dialect = self.target.dialect
        self.plan = build_plan(project, self.target)
        try:
            self.placement = placement_for(project, environment, self.warehouse, None)
        except EnvError as exc:
            raise ExploreError(str(exc)) from None
        self.by_entity: dict[str, Located] = {}
        self.groups = {g.name: g.schema for g in self.plan.sources}
        for group in self.plan.sources:
            for table in group.tables:
                name = table.entity.name
                self.by_entity[name.lower()] = self._locate(name, "raw", group.schema, name)
        self.models: dict[str, Any] = {}  # entity -> the model that builds it
        for model in self.plan.models:
            if is_history(model):
                continue
            self.models[model.entity.name.lower()] = _final(self.models.get(model.entity.name.lower()), model)
        for key, model in self.models.items():
            schema = LAYER_SCHEMAS.get(model.layer, model.layer)
            self.by_entity.setdefault(key, self._locate(model.entity.name, model.layer, schema, model.name))

    def _locate(self, entity: str, layer: str, schema: str, name: str) -> Located:
        placed = self.placement.schema(schema)
        return Located(entity, layer, placed, name, self.where(placed, name))

    def quote(self, name: str) -> str:
        from murakumo.generate.plan import quote_identifier

        return quote_identifier(name, self.dialect)

    def where(self, schema: str, name: str) -> str:
        qualified = f"{self.quote(schema)}.{self.quote(name)}"
        if self.warehouse is Warehouse.SNOWFLAKE and self.placement.database:
            qualified = f"{self.placement.database}.{qualified}"
        return qualified

    def get(self, entity: str) -> Located:
        found = self.by_entity.get(entity.lower())
        if found is None:
            raise ExploreError(f"no table {entity!r} in the model")
        return found

    def sql(self, duckdb_sql: str) -> str:
        """A statement written in DuckDB's SQL, in this warehouse's."""
        if self.dialect == "duckdb":
            return duckdb_sql
        return sqlglot.transpile(duckdb_sql, read="duckdb", write=self.dialect)[0]

    def sample(self, relation: str, rows: int = SAMPLE, columns: str = "*") -> str:
        """A FROM item that reads at most ``rows`` rows of a table, in this warehouse's SQL. On
        BigQuery, where LIMIT does not lower the bytes billed, a 1% block sample comes first."""
        if self.dialect == "tsql":
            return f"(select top {rows} {columns} from {relation})"
        if self.warehouse is Warehouse.BIGQUERY:
            return f"(select {columns} from {relation} tablesample system (1 percent) limit {rows})"
        return f"(select {columns} from {relation} limit {rows})"


# Which raw tables hold data ------------------------------------------------------------------------


def status(project: Project, environment: str | None = None) -> dict[str, Any]:
    """Each raw table: whether it exists in the warehouse, and its rows when the metadata says; and
    whether each model is built.

    ``{"tables": {name: {"exists": bool, "rows": int | None}}, "built": {name: bool}, "reason": str | None}``. Asks only
    the catalog, never a table: free on DuckDB and Snowflake, a metadata query elsewhere."""
    from murakumo.environments import EnvError, connect

    places = Places(project, environment)
    raw = {k: v for k, v in places.by_entity.items() if v.layer == "raw"}
    models = {k: v for k, v in places.by_entity.items() if v.layer != "raw"}
    empty = {v.entity: {"exists": False, "rows": None} for v in raw.values()}
    unbuilt = {v.entity: False for v in models.values()}
    try:
        connection = connect(
            project,
            read_only=True,
            database=places.placement.database,
            environment=places.placement.environment,
        )
    except EnvError as exc:
        return {"tables": empty, "built": unbuilt, "reason": str(exc), "warehouse": places.warehouse.value}
    tables: dict[str, dict[str, Any]] = {}
    built: dict[str, bool] = {}
    try:
        present = connection.schemas()
        counts: dict[tuple[str, str], int | None] = {}
        for schema in sorted({v.schema.lower() for v in raw.values()}):
            if schema not in present:
                continue
            counts.update(_row_counts(connection, places, schema))
        for located in raw.values():
            key = (located.schema.lower(), located.name.lower())
            tables[located.entity] = {"exists": key in counts, "rows": counts.get(key)}
        existing: set[tuple[str, str]] = set()
        for schema in sorted({v.schema.lower() for v in models.values()}):
            if schema in present:
                existing |= _relations_in(connection, places, schema)
        for located in models.values():
            built[located.entity] = (located.schema.lower(), located.name.lower()) in existing
    except Exception as exc:  # noqa: BLE001 - any driver error: say so, as not loaded
        reason = " ".join(str(exc).split())[:300]
        return {"tables": empty, "built": unbuilt, "reason": reason, "warehouse": places.warehouse.value}
    finally:
        connection.close()
    return {"tables": tables, "built": built, "reason": None, "warehouse": places.warehouse.value}


def _relations_in(connection, places: Places, schema: str) -> set[tuple[str, str]]:
    """The tables and views in one schema, from the catalog."""
    if places.warehouse is Warehouse.DUCKDB:
        rows = connection.execute(
            "select table_name from information_schema.tables "
            f"where table_catalog = current_database() and lower(table_schema) = '{schema}'",
            fetch=True,
        )
        return {(schema, str(r[0]).lower()) for r in rows}
    if places.warehouse is Warehouse.SNOWFLAKE:
        rows = connection.execute(f"show objects in schema {connection.database}.{schema}", fetch=True)
        return {(schema, str(r[1]).lower()) for r in rows}
    return {(schema, name.lower()) for name in connection.tables(schema)}


def _row_counts(connection, places: Places, schema: str) -> dict[tuple[str, str], int | None]:
    """{(schema, table): rows or None} for the tables in one schema, from the catalog."""
    warehouse = places.warehouse
    if warehouse is Warehouse.DUCKDB:
        rows = connection.execute(
            "select schema_name, table_name, estimated_size from duckdb_tables() "
            f"where database_name = current_database() and lower(schema_name) = '{schema}'",
            fetch=True,
        )
        found = {(s.lower(), t.lower()): None for s, t, _ in rows}
        for s, t, _ in rows:  # the estimate can lag; counting a local table is free and exact
            counted = connection.execute(f"select count(*) from {places.where(s, t)}", fetch=True)
            found[(s.lower(), t.lower())] = int(counted[0][0])
        return found
    if warehouse is Warehouse.SNOWFLAKE:
        found = {}
        for row in connection.execute(f"show tables in schema {connection.database}.{schema}", fetch=True):
            found[(schema, str(row[1]).lower())] = int(row[7]) if len(row) > 7 and row[7] is not None else None
        return found
    return {(schema, name.lower()): None for name in connection.tables(schema)}


# A table before it is built -------------------------------------------------------------------------


def preview_sql(
    project: Project, entity: str, environment: str | None = None, sample: int | None = SAMPLE
) -> tuple[str, list[str]]:
    """One query that returns a table's rows as its model would build them, and the raw tables it
    reads. Upstream models are folded in as CTEs, so nothing needs to be built first; each raw
    table is read as a sample of ``sample`` rows (every row when ``sample`` is None)."""
    from murakumo.generate import dbt
    from murakumo.generate.plain import Plain
    from murakumo.generate.plan import is_history

    places = Places(project, environment)
    located = places.get(entity)
    if located.layer == "raw":
        source = places.sample(located.relation, sample) if sample else located.relation
        return f"select * from {source} as raw_rows", [located.entity]

    files = dict(dbt.render(places.plan).items())
    models = {m.name: m for m in places.plan.models if not is_history(m)}
    snapshots = {s.name: s for s in places.plan.snapshots}

    def cte(name: str) -> str:
        return places.quote(f"__{name}")

    class Inline(Plain):
        def relation(self, name: str) -> str:
            return cte(name)

        def source(self, group: str, table: str) -> str:
            return cte(f"raw_{table}")

    inline = Inline(places.plan, files)
    order: list[str] = []
    bodies: dict[str, str] = {}
    raws: dict[str, str] = {}

    def reads(text: str, path: tuple[str, ...]) -> None:
        for group, table in _SOURCE.findall(text):
            raws.setdefault(table, places.where(places.placement.schema(places.groups[group]), table))
        for ref in _REF.findall(text):
            visit(ref, path)

    def visit(name: str, path: tuple[str, ...] = ()) -> None:
        if name in order:
            return
        if name in path:
            raise ExploreError(f"{name} reads itself through {' → '.join(path)}")
        if name in snapshots:
            # A snapshot's history exists only once built: preview it as its first run, where every
            # row is the current version.
            snapshot = snapshots[name]
            reads(snapshot.relation, (*path, name))
            upstream = _REF.search(snapshot.relation)
            relation = cte(upstream.group(1)) if upstream else snapshot.relation
            bodies[name] = places.sql(
                f"select *, md5(cast(random() as varchar)) as dbt_scd_id, current_timestamp as dbt_updated_at, "
                f"current_timestamp as dbt_valid_from, cast(null as timestamp) as dbt_valid_to from {relation}"
            )
            order.append(name)
            return
        if name not in models:
            return
        model = models[name]
        reads(files.get(f"models/{model.folder}/{model.name}.sql", ""), (*path, name))
        bodies[name] = inline.select(model)
        order.append(name)

    target = places.models.get(entity.lower())
    if target is None:
        raise ExploreError(f"{entity} has no model to preview")
    visit(target.name)
    parts = []
    for table, relation in raws.items():
        body = f"select * from {places.sample(relation, sample)} as s" if sample else f"select * from {relation}"
        parts.append(f"{cte('raw_' + table)} as (\n{body}\n)")
    for name in order:
        parts.append(f"{cte(name)} as (\n{bodies[name]}\n)")
    sql = "with " + ",\n".join(parts) + f"\nselect * from {cte(target.name)}"
    raw_entities = [places.by_entity[t.lower()].entity if t.lower() in places.by_entity else t for t in raws]
    return sql, raw_entities


def preview(
    project: Project,
    entity: str,
    environment: str | None = None,
    full: bool = False,
    limit: int = PREVIEW_ROWS,
    timeout: int = 30,
) -> dict[str, Any]:
    """A table's first rows before it is built: on samples of its raw tables, or on all of them."""
    from murakumo import query

    sql, reads = preview_sql(project, entity, environment, None if full else SAMPLE)
    try:
        result = query.run(project, sql, "warehouse", environment, limit, timeout)
    except query.QueryError as exc:
        message = str(exc)
        if re.search(r"does not exist|not found|no such table|catalog error|unknown table", message, re.I):
            message = (
                f"{entity} reads {', '.join(reads)}, which are not all in the warehouse yet. Load the raw "
                f"tables first (Build ▸ Load raw data, or murakumo build --raw-only). {message}"
            )
        raise ExploreError(message) from None
    return {
        **result.as_dict(),
        "sampled": not full,
        "sample": None if full else SAMPLE,
        "reads": reads,
    }


def estimate(project: Project, entity: str, environment: str | None = None) -> dict[str, Any]:
    """What previewing a table on all its data would read, before it runs."""
    from murakumo import query

    warehouse = project.manifest.targets.warehouse.type
    sql, reads = preview_sql(project, entity, environment, None)
    if warehouse is Warehouse.DUCKDB:
        return {"known": True, "free": True, "text": "Runs on this computer: no cost.", "reads": reads}
    if warehouse is Warehouse.SNOWFLAKE:
        try:
            plan = query.run(project, f"explain using tabular {sql}", "warehouse", environment, 1000, 30)
        except query.QueryError as exc:
            return {"known": False, "text": f"No estimate: {exc}", "reads": reads}
        columns = [c.lower() for c in plan.columns]
        total = 0
        for row in plan.rows:
            record = dict(zip(columns, row, strict=False))
            if record.get("operation") == "GlobalStats":
                total = int(record.get("bytesassigned") or 0)
        return {
            "known": True,
            "bytes": total,
            "text": f"Reads about {_size(total)} on Snowflake, on the connection's warehouse.",
            "reads": reads,
        }
    return {
        "known": False,
        "text": f"No estimate on {warehouse.value}; it runs under the query time limit.",
        "reads": reads,
    }


def _size(count: int) -> str:
    size = float(count)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{count} bytes"


# Questions worth asking a table ----------------------------------------------------------------------


def _family(column) -> str:
    try:
        return parse_type(column.type).family
    except ValueError:
        return "text"


def values(project: Project, entity_name: str, column: str, environment: str | None = None) -> dict[str, Any]:
    """What a column holds, on a sample of its table: how many rows have a value, and its most
    common values. One small query, on the warehouse."""
    from murakumo import query

    entity = project.model.entity(entity_name)
    if entity is None or not any(a.name == column for a in entity.attributes):
        raise ExploreError(f"no column {column} in {entity_name}")
    places = Places(project, environment)
    located = places.get(entity.name)
    q = places.quote(column)
    sample = places.sample(located.relation, SAMPLE, q)
    try:
        counts = query.run(
            project, f"select count(*) as n, count({q}) as filled from {sample} as s", "warehouse", environment, 1, 60
        ).rows[0]
        common = query.run(
            project,
            f"select cast({q} as varchar) as v, count(*) as n from {sample} as s where {q} is not null "
            "group by 1 order by 2 desc, 1 limit 8",
            "warehouse",
            environment,
            8,
            60,
        ).rows
    except query.QueryError as exc:
        raise ExploreError(f"{entity.name} is not loaded yet: {exc}") from None
    return {
        "rows": int(counts[0] or 0),
        "filled": int(counts[1] or 0),
        "common": [{"value": str(v)[:120], "count": int(n)} for v, n in common],
        "sample": SAMPLE,
    }


def suggested(project: Project, entity_name: str, environment: str | None = None) -> list[dict[str, str]]:
    """Queries to start from, written from what the model knows of the table: its key, dates,
    categories and joins. Each is {title, plain, sql}, in the warehouse's SQL."""
    places = Places(project, environment)
    located = places.get(entity_name)
    entity: Entity | None = project.model.entity(located.entity)
    if entity is None:
        raise ExploreError(f"no table {entity_name!r} in the model")
    q = places.quote
    rel = located.relation
    columns = entity.attributes
    keys = [c for c in columns if c.key is KeyRole.PRIMARY] or [c for c in columns if c.name in entity.grain]
    dates = [c for c in columns if _family(c) in ("date", "timestamp")]
    categories = [
        c
        for c in columns
        if _family(c) == "text"
        and (c.accepted_values or re.search(r"(status|type|kind|category|country|state|segment|channel)$", c.name))
        and not c.pii
    ]
    from murakumo.relations import canonical

    numbers = [
        c
        for c in columns
        if _family(c) in ("integer", "number") and c.key is None and canonical(located.entity, c.name) is None
    ]
    found: list[dict[str, str]] = []

    def add(title: str, plain: str, sql: str) -> None:
        found.append({"title": title, "plain": plain, "sql": places.sql(sql)})

    add("First rows", "What the rows look like.", f"select * from {rel} limit 100")
    add("How many rows", "The number of rows in the table.", f"select count(*) as row_count from {rel}")
    if dates:
        day = dates[0]
        add(
            f"Rows per day, by {day.name}",
            "Whether data arrives every day, and how much.",
            f"select cast({q(day.name)} as date) as day, count(*) as row_count from {rel} "
            "group by 1 order by 1 desc limit 60",
        )
        add(
            f"Oldest and newest {day.name}",
            "The period the table covers.",
            f"select min({q(day.name)}) as oldest, max({q(day.name)}) as newest from {rel}",
        )
    if columns:
        counts = ", ".join(f"count(*) - count({q(c.name)}) as {q(c.name + '_empty')}" for c in columns[:40])
        add("Empty values per column", "How many rows have nothing in each column.", f"select {counts} from {rel}")
    for category in categories[:2]:
        add(
            f"Most common {category.name}",
            f"The values of {category.name}, most frequent first.",
            f"select {q(category.name)}, count(*) as row_count from {rel} group by 1 order by 2 desc limit 25",
        )
    if numbers:
        measure = numbers[0]
        add(
            f"Range of {measure.name}",
            f"The smallest, largest and average {measure.name}.",
            f"select min({q(measure.name)}) as smallest, max({q(measure.name)}) as largest, "
            f"avg({q(measure.name)}) as average from {rel}",
        )
    if keys:
        key = ", ".join(q(k.name) for k in keys)
        add(
            "Duplicate keys",
            f"Keys that appear more than once: the table should have one row per {', '.join(k.name for k in keys)}.",
            f"select {key}, count(*) as copies from {rel} group by {key} having count(*) > 1 "
            "order by copies desc limit 100",
        )
    for relationship in project.model.relationships:
        if relationship.from_.lower() != located.entity.lower():
            continue
        try:
            other = places.get(relationship.to)
        except ExploreError:
            continue
        pairs = [(k.from_, k.to) for k in relationship.keys]
        condition = " and ".join(f"o.{q(b)} = t.{q(a)}" for a, b in pairs)
        first = pairs[0][0]
        add(
            f"{', '.join(a for a, _ in pairs)} with no match in {relationship.to}",
            f"Rows whose {', '.join(a for a, _ in pairs)} is not in {relationship.to}: these would be lost in a join.",
            f"select t.* from {rel} t where t.{q(first)} is not null and not exists "
            f"(select 1 from {other.relation} o where {condition}) limit 100",
        )
    return found


__all__ = ["SAMPLE", "ExploreError", "Places", "estimate", "preview", "preview_sql", "status", "suggested"]
