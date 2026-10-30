"""Migrations: turn model changes into ALTER statements for the tables that keep data.

dbt rebuilds views and tables on every run, so a model change reaches them by itself. Three kinds
of table keep data between runs and need their schema changed in place:

- raw landing tables, which ingestion writes into (``generated/ddl/<warehouse>/raw.sql``);
- incremental models, which dbt only appends to;
- the snapshots behind SCD2 dimensions, which hold every version.

``migrations/<warehouse>/schema.json`` records those tables as of the last migration, by id. The
next migration compares the model with it, so renaming a column becomes ``RENAME COLUMN`` instead
of a dropped column and an empty new one. Statements that would lose data are written commented
out, for a person to decide. Migration files are numbered, never rewritten, and committed with
the model change that caused them::

    migrations/snowflake/schema.json
    migrations/snowflake/0001_initial.sql
    migrations/snowflake/0002_rename_lineitem_l_discount.sql
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from murakumo.generate import ddl, generate
from murakumo.generate.plan import LANDING_COLUMNS, quote_identifier
from murakumo.generate.targets import Target, target_for
from murakumo.ir.types import parse_type
from murakumo.manifest import Warehouse
from murakumo.project import Project

MIGRATIONS_DIR = "migrations"
SCHEMA_FILE = "schema.json"
_NUMBERED = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


@dataclass
class ColumnState:
    id: str
    name: str
    type: str


@dataclass
class TableState:
    id: str
    kind: str  # raw | incremental | snapshot
    schema: str
    name: str
    columns: list[ColumnState]

    def column(self, id: str) -> ColumnState | None:
        return next((c for c in self.columns if c.id == id), None)


@dataclass
class Change:
    summary: str  # one line, for people
    slug: str  # for the file name
    sql: list[str]  # statements; lines starting with -- are left for a person to decide
    destructive: bool = False


@dataclass
class Pending:
    warehouse: Warehouse
    number: int
    baseline: bool  # no migrations yet: this one creates the tables
    changes: list[Change]
    tables: dict[str, TableState]
    sql: str = ""
    title: str | None = None  # a name given by the person writing it
    notes: list[str] = field(default_factory=list)  # things a migration cannot do, for people

    @property
    def name(self) -> str:
        if self.title:
            return _slug(self.title)[:60] or "change"
        if self.baseline:
            return "initial"
        slug = self.changes[0].slug[:50].rstrip("_") if self.changes else "change"
        if len(self.changes) > 1:
            slug += f"_and_{len(self.changes) - 1}_more"
        return slug

    @property
    def file_name(self) -> str:
        return f"{self.number:04d}_{self.name}.sql"


# The tables as the model describes them ---------------------------------------------------------


def current_tables(project: Project, warehouse: Warehouse | str | None = None) -> tuple[dict[str, TableState], dict]:
    """The stateful tables the model generates, and the CREATE statements for raw tables and schemas.

    Statements are keyed by table id, and by ``schema:<name>`` for schemas.
    """
    output = generate(project, warehouse)
    plan = output.plan
    target = plan.target
    tables: dict[str, TableState] = {}
    creates: dict[str, str] = {}

    schemas = ddl.DDL(plan)
    for group in plan.sources:
        creates[f"schema:{group.schema}"] = schemas.schema(group.schema).rstrip("\n")
        for table in group.tables:
            entity = table.entity
            columns = [ColumnState(str(a.id), a.name, target.physical(a.logical_type)) for a in entity.attributes]
            columns += [
                ColumnState(f"{entity.id}:{name}", name, target.physical(parse_type(logical)))
                for name, logical in LANDING_COLUMNS.items()
            ]
            tables[str(entity.id)] = TableState(str(entity.id), "raw", group.schema, entity.name, columns)
            creates[str(entity.id)] = output.ddl.get(entity.id, "")

    def column_id(entity, name: str) -> str:
        attribute = entity.attribute(name)
        return str(attribute.id) if attribute is not None else f"{entity.id}:{name}"

    for model in plan.models:
        if model.materialized == "incremental":
            entity = model.entity
            columns = [ColumnState(column_id(entity, c.name), c.name, c.data_type) for c in model.columns]
            tables[str(entity.id)] = TableState(str(entity.id), "incremental", model.layer, model.name, columns)
    for snapshot in plan.snapshots:
        entity = snapshot.entity
        if entity is None:
            continue
        columns = [ColumnState(column_id(entity, c.name), c.name, c.data_type) for c in snapshot.columns]
        key = f"{entity.id}:snapshot"
        tables[key] = TableState(key, "snapshot", "snapshots", snapshot.name, columns)
    return dict(sorted(tables.items(), key=lambda t: (t[1].kind, t[1].schema, t[1].name))), creates


# Recorded state ---------------------------------------------------------------------------------


def folder(root: Path, warehouse: Warehouse | str) -> Path:
    return root / MIGRATIONS_DIR / Warehouse(warehouse).value


def load_state(root: Path, warehouse: Warehouse | str) -> dict[str, TableState] | None:
    path = folder(root, warehouse) / SCHEMA_FILE
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        t["id"]: TableState(t["id"], t["kind"], t["schema"], t["name"], [ColumnState(**c) for c in t["columns"]])
        for t in data.get("tables", [])
    }


def _save_state(root: Path, warehouse: Warehouse, tables: dict[str, TableState], number: int) -> None:
    data = {
        "warehouse": warehouse.value,
        "migration": number,
        "tables": [
            {
                "id": t.id,
                "kind": t.kind,
                "schema": t.schema,
                "name": t.name,
                "columns": [{"id": c.id, "name": c.name, "type": c.type} for c in t.columns],
            }
            for t in tables.values()
        ],
    }
    path = folder(root, warehouse) / SCHEMA_FILE
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _last_number(root: Path, warehouse: Warehouse) -> int:
    path = folder(root, warehouse)
    numbers = [int(m.group(1)) for p in path.glob("*.sql") if (m := _NUMBERED.match(p.name))] if path.is_dir() else []
    return max(numbers, default=0)


# Statements -------------------------------------------------------------------------------------


class Dialect:
    """ALTER statements differ more between warehouses than CREATE ones do."""

    def __init__(self, target: Target):
        self.target = target
        self.dialect = target.dialect
        self.tsql = target.warehouse in (Warehouse.FABRIC, Warehouse.SYNAPSE)

    def q(self, name: str) -> str:
        return quote_identifier(name, self.dialect)

    def table(self, t: TableState) -> str:
        return f"{self.q(t.schema)}.{self.q(t.name)}"

    # Warehouses where a statement can skip a table that does not exist yet.
    IF_EXISTS = (Warehouse.DUCKDB, Warehouse.SNOWFLAKE, Warehouse.POSTGRES, Warehouse.BIGQUERY)

    def alter(self, t: TableState) -> str:
        """``ALTER TABLE``, or ``ALTER TABLE IF EXISTS`` for a table dbt builds: on a new environment
        dbt has not created it yet, and its first run will create it with the new shape."""
        if t.kind != "raw" and self.target.warehouse in self.IF_EXISTS:
            return f"ALTER TABLE IF EXISTS {self.table(t)}"
        return f"ALTER TABLE {self.table(t)}"

    def guard(self, t: TableState, statement: str) -> list[str]:
        """The statement, with a note where the warehouse cannot skip a table dbt has not built yet."""
        if t.kind != "raw" and self.target.warehouse not in self.IF_EXISTS:
            return [f"-- Only where dbt has already built {t.name}; on a new environment, skip this.", statement]
        return [statement]

    def rename_table(self, old: TableState, new_name: str) -> str:
        if self.tsql:
            return f"EXEC sp_rename '{old.schema}.{old.name}', '{new_name}';"
        if self.target.warehouse in (Warehouse.SNOWFLAKE, Warehouse.DATABRICKS):
            return f"{self.alter(old)} RENAME TO {self.q(old.schema)}.{self.q(new_name)};"
        return f"{self.alter(old)} RENAME TO {self.q(new_name)};"

    def add_column(self, t: TableState, c: ColumnState) -> str:
        keyword = "ADD" if self.tsql else "ADD COLUMN"
        return f"{self.alter(t)} {keyword} {self.q(c.name)} {c.type};"

    def drop_column(self, t: TableState, c: ColumnState) -> str:
        return f"{self.alter(t)} DROP COLUMN {self.q(c.name)};"

    def rename_column(self, t: TableState, old: str, new: str) -> str:
        if self.tsql:
            return f"EXEC sp_rename '{t.schema}.{t.name}.{old}', '{new}', 'COLUMN';"
        return f"{self.alter(t)} RENAME COLUMN {self.q(old)} TO {self.q(new)};"

    def change_type(self, t: TableState, c: ColumnState) -> str:
        if self.tsql:
            return f"{self.alter(t)} ALTER COLUMN {self.q(c.name)} {c.type};"
        if self.target.warehouse in (Warehouse.SNOWFLAKE, Warehouse.BIGQUERY):
            return f"{self.alter(t)} ALTER COLUMN {self.q(c.name)} SET DATA TYPE {c.type};"
        return f"{self.alter(t)} ALTER COLUMN {self.q(c.name)} TYPE {c.type};"


def _slug(*parts: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", "_".join(parts).lower()).strip("_")


def diff(
    old: dict[str, TableState], new: dict[str, TableState], target: Target, creates: dict[str, str], project: Project
) -> list[Change]:
    """The changes that turn the ``old`` tables into the ``new`` ones."""
    sql = Dialect(target)
    changes: list[Change] = []
    entity_ids = {str(e.id) for e in project.model.entities}
    schemas = {t.schema for t in old.values() if t.kind == "raw"}

    for key, table in new.items():
        if key in old:
            continue
        if table.kind == "raw":
            if table.schema not in schemas:
                schemas.add(table.schema)
                statement = creates.get(f"schema:{table.schema}", "")
                changes.append(Change(f"create schema {table.schema}", _slug("schema", table.schema), [statement]))
            create = creates.get(key, "").rstrip("\n")
            changes.append(Change(f"create {table.schema}.{table.name}", _slug("create", table.name), [create]))
        elif table.kind == "snapshot" and old:  # after the first migration: a table started keeping history
            entity = next((e.name for e in project.model.entities if str(e.id) == key.split(":")[0]), table.name)
            changes.append(
                Change(
                    f"{entity} starts keeping history (SCD2) in {table.schema}.{table.name}",
                    _slug("history", entity),
                    [
                        f"-- dbt creates {sql.table(table)} on its next run, recording every current row of",
                        f"-- {entity} as its first version, valid from that run. Earlier history is not known.",
                    ],
                )
            )
        else:
            changes.append(
                Change(
                    f"{table.kind} {table.schema}.{table.name} is new",
                    _slug("new", table.name),
                    [f"-- dbt creates {sql.table(table)} on its next run."],
                )
            )

    for key, before in old.items():
        if key in new:
            continue
        if key.split(":")[0] in entity_ids:  # the entity is still there, but dbt now rebuilds it
            changes.append(
                Change(
                    f"{before.schema}.{before.name} no longer keeps data between runs",
                    _slug("untrack", before.name),
                    [
                        f"-- {sql.table(before)} is no longer {before.kind}; drop it once nothing reads it:",
                        f"-- DROP TABLE {sql.table(before)};",
                    ],
                    destructive=True,
                )
            )
        else:
            changes.append(
                Change(
                    f"drop {before.schema}.{before.name}",
                    _slug("drop", before.name),
                    [
                        f"-- {before.name} was removed from the model. Uncomment to drop it and its data:",
                        f"-- DROP TABLE {sql.table(before)};",
                    ],
                    destructive=True,
                )
            )

    for key, after in new.items():
        before = old.get(key)
        if before is None:
            continue
        current = before
        if before.schema != after.schema:
            changes.append(
                Change(
                    f"{before.name} moved from {before.schema} to {after.schema}",
                    _slug("move", after.name),
                    [
                        f"-- {after.name} now lands in {sql.q(after.schema)} instead of {sql.q(before.schema)}.",
                        f"-- Create it with generated/ddl/{target.warehouse.value}/raw.sql and copy the data across.",
                    ],
                    destructive=True,
                )
            )
            continue
        if before.name != after.name:
            changes.append(
                Change(
                    f"rename {before.schema}.{before.name} to {after.name}",
                    _slug("rename", before.name),
                    sql.guard(before, sql.rename_table(before, after.name)),
                )
            )
            current = TableState(before.id, before.kind, before.schema, after.name, before.columns)
        dbt_managed = after.kind != "raw"
        for column in after.columns:
            previous = current.column(column.id)
            if previous is None:
                statement = sql.add_column(current, column)
                if dbt_managed:
                    statement = f"-- dbt adds it on its next run: {statement}"
                changes.append(
                    Change(f"{after.name}: add {column.name}", _slug("add", after.name, column.name), [statement])
                )
                continue
            if previous.name != column.name:
                changes.append(
                    Change(
                        f"{after.name}: rename {previous.name} to {column.name}",
                        _slug("rename", after.name, previous.name),
                        sql.guard(current, sql.rename_column(current, previous.name, column.name)),
                    )
                )
            if previous.type.lower() != column.type.lower():
                changes.append(
                    Change(
                        f"{after.name}: {column.name} becomes {column.type} (was {previous.type})",
                        _slug("retype", after.name, column.name),
                        [
                            f"-- Check that existing values fit {column.type} first.",
                            *sql.guard(current, sql.change_type(current, column)),
                        ],
                    )
                )
        for column in current.columns:
            if after.column(column.id) is None:
                changes.append(
                    Change(
                        f"{after.name}: drop {column.name}",
                        _slug("drop", after.name, column.name),
                        [
                            f"-- {column.name} was removed from the model. Uncomment to drop it and its data:",
                            f"-- {sql.drop_column(current, column)}",
                        ],
                        destructive=True,
                    )
                )
    return changes


def order_notes(old: dict[str, TableState] | None, new: dict[str, TableState]) -> list[str]:
    """Tables whose columns are now in a different order than the warehouse has them.

    Warehouses cannot reorder columns in place, so migrations leave the order alone; this says so.
    """
    notes = []
    for key, after in new.items():
        before = (old or {}).get(key)
        if before is None:
            continue
        kept = {c.id for c in after.columns}
        was = [c.id for c in before.columns if c.id in kept]
        now = [c.id for c in after.columns if c.id in {c.id for c in before.columns}]
        if was != now:
            notes.append(
                f"{after.schema}.{after.name}: new column order. Existing tables keep their order, "
                "because warehouses cannot reorder columns in place; new environments get the new one."
            )
    return notes


def render(pending: Pending) -> str:
    lines = [
        f"-- Migration {pending.number:04d} for {pending.warehouse.value}: {pending.name}",
        "-- Generated by Murakumo from changes to the model. Review it, then apply it before running dbt.",
    ]
    if pending.baseline:
        lines.append("-- The landing tables as they are now. Safe to run where they already exist.")
    if any(c.destructive for c in pending.changes):
        lines.append("-- Statements that would lose data are commented out: uncomment the ones you want.")
    for change in pending.changes:
        lines += ["", f"-- {change.summary}", *change.sql]
    return "\n".join(lines) + "\n"


# Entry points -----------------------------------------------------------------------------------


def plan_migration(project: Project, warehouse: Warehouse | str | None = None) -> Pending:
    """The next migration: its number, its changes and its SQL. No changes means nothing to write."""
    chosen = Warehouse(warehouse or project.manifest.targets.warehouse.type)
    tables, creates = current_tables(project, chosen)
    old = load_state(project.root, chosen)
    baseline = old is None
    changes = diff(old or {}, tables, target_for(chosen), creates, project)
    if baseline:  # the first migration only creates landing schemas and tables; dbt creates the rest
        changes = [c for c in changes if not c.slug.startswith("new_")]
    pending = Pending(chosen, _last_number(project.root, chosen) + 1, baseline, changes, tables)
    pending.notes = order_notes(old, tables)
    pending.sql = render(pending) if changes or baseline else ""
    return pending


def write_migration(project: Project, pending: Pending, name: str | None = None) -> Path | None:
    """Write the migration and record the new state. Returns the file, or None if nothing changed."""
    if not pending.changes and not pending.baseline:
        return None
    if name:
        pending.title = name
    path = folder(project.root, pending.warehouse)
    path.mkdir(parents=True, exist_ok=True)
    file = path / pending.file_name
    pending.sql = render(pending)
    file.write_text(pending.sql, encoding="utf-8")
    _save_state(project.root, pending.warehouse, pending.tables, pending.number)
    return file


# Applying ---------------------------------------------------------------------------------------

APPLIED = "murakumo_migrations"  # in the audit schema: which migration files have run


def prefixed(sql: str, prefix: str, dialect: str, schemas: set[str]) -> list[str]:
    """The statements of a migration, with the project's schemas moved under ``prefix``."""
    import sqlglot
    from sqlglot import exp

    statements = []
    for tree in sqlglot.parse(sql, read=dialect):
        if tree is None:
            continue
        if prefix:
            if isinstance(tree, exp.Create) and str(tree.args.get("kind", "")).upper() == "SCHEMA":
                table = tree.this
                if table.name.lower() in schemas:
                    table.set("this", exp.to_identifier(prefix + table.name))
            for table in tree.find_all(exp.Table):
                if table.db and table.db.lower() in schemas:
                    table.set("db", exp.to_identifier(prefix + table.db))
        statements.append(tree.sql(dialect=dialect))
    return statements


def apply_migrations(project: Project, connection, prefix: str = "", mark_only: bool = False) -> list[str]:
    """Run the migration files this warehouse (under ``prefix``) has not run yet, in order.

    Each one is recorded in ``<prefix>audit.murakumo_migrations``. ``mark_only`` records them without
    running them, for tables that were brought up to date by hand.
    """
    from murakumo.environments import project_schemas

    warehouse = connection.warehouse
    files = sorted(p for p in folder(project.root, warehouse).glob("*.sql") if _NUMBERED.match(p.name))
    audit = f"{prefix}audit"
    record = f"{audit}.{APPLIED}" if warehouse is Warehouse.DUCKDB else f"{connection.database}.{audit}.{APPLIED}"
    schema_name = audit if warehouse is Warehouse.DUCKDB else f"{connection.database}.{audit}"
    connection.execute(f"create schema if not exists {schema_name}")
    connection.execute(
        f"create table if not exists {record} (number integer, name varchar, applied_at timestamp, by_hand boolean)"
    )
    done = {row[0] for row in connection.execute(f"select number from {record}", fetch=True)}
    schemas = {s.lower() for s in project_schemas(project, warehouse)}
    dialect = target_for(warehouse).dialect
    applied = []
    for path in files:
        number = int(path.name[:4])
        if number in done:
            continue
        if not mark_only:
            for statement in prefixed(path.read_text(encoding="utf-8"), prefix, dialect, schemas):
                connection.execute(statement)
        connection.execute(
            f"insert into {record} values ({number}, '{path.stem}', current_timestamp, {str(mark_only).lower()})"
        )
        applied.append(path.name)
    return applied


__all__ = [
    "Change",
    "Pending",
    "apply_migrations",
    "current_tables",
    "load_state",
    "plan_migration",
    "prefixed",
    "write_migration",
]
