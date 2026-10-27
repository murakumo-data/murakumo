"""Introspect a live database: tables, columns, types, primary keys, foreign keys and comments.

Uses ``information_schema`` for DuckDB and PostgreSQL, and Snowflake's catalogue plus ``SHOW
... KEYS`` for Snowflake (see ``snowflake.py``). Only metadata is read, never rows, and
connections are opened read-only where the driver allows it.

Connection strings:
    duckdb:///path/to/file.duckdb   or just  path/to/file.duckdb
    postgresql://user@host:5432/db  (put the password in PGPASSWORD or ~/.pgpass, not in the URL)
    snowflake://ACCOUNT/DATABASE    the rest, including the key file, from SNOWFLAKE_* variables
    env:NAME                        read the connection string from an environment variable
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from murakumo.diagnostics import warning
from murakumo.importers.base import SourceImport, fk_relationship, safe_name, unique_names
from murakumo.importers.typemap import sql_to_logical
from murakumo.ir.model import Attribute, Entity, KeyRole, SourceSystem, SourceType

SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast", "pg_internal"}


class SourceUnavailable(Exception):
    """The database could not be reached or the driver is not installed."""


@dataclass
class Column:
    schema: str
    table: str
    name: str
    position: int
    data_type: str
    nullable: bool
    comment: str | None = None


def resolve(url: str) -> str:
    if url.startswith("env:"):
        name = url[4:]
        value = os.environ.get(name)
        if not value:
            raise SourceUnavailable(f"environment variable {name} is not set")
        return value
    return url


def redact(url: str) -> str:
    """A connection string safe to store in the model: env: references kept, passwords and query removed."""
    if url.startswith("env:") or "://" not in url:
        return url
    parts = urlsplit(url)
    if not parts.password and not parts.query:
        return url
    if parts.password:
        user = f"{parts.username}@" if parts.username else ""
        port = f":{parts.port}" if parts.port else ""
        parts = parts._replace(netloc=f"{user}{parts.hostname or ''}{port}")
    return urlunsplit(parts._replace(query=""))


def dialect_of(url: str) -> str:
    if url.startswith(("postgresql://", "postgres://")):
        return "postgres"
    if url.startswith("snowflake://"):
        return "snowflake"
    if url.startswith("duckdb:") or url.endswith((".duckdb", ".db")) or url == ":memory:":
        return "duckdb"
    raise SourceUnavailable(
        f"unsupported connection string {redact(url)!r}; use duckdb:///file.duckdb, postgresql:// or snowflake://"
    )


def connect(url: str) -> Any:
    real = resolve(url)
    dialect = dialect_of(real)
    if dialect == "duckdb":
        try:
            import duckdb
        except ImportError:
            raise SourceUnavailable("DuckDB support is not installed: pip install 'murakumo[duckdb]'") from None
        path = real.removeprefix("duckdb:").removeprefix("///").removeprefix("//") or ":memory:"
        try:
            return duckdb.connect(path, read_only=path != ":memory:")
        except duckdb.Error as exc:
            raise SourceUnavailable(f"cannot open {path}: {exc}") from None
    try:
        import psycopg
    except ImportError:
        raise SourceUnavailable("PostgreSQL support is not installed: pip install 'murakumo[postgres]'") from None
    try:
        connection = psycopg.connect(real, connect_timeout=10)
        connection.read_only = True
        return connection
    except psycopg.Error as exc:
        raise SourceUnavailable(f"cannot connect to {redact(real)}: {str(exc).strip()}") from None


def _rows(connection: Any, sql: str) -> list[tuple]:
    cursor = connection.cursor()
    cursor.execute(sql)
    return list(cursor.fetchall())


def _columns(connection: Any, dialect: str) -> list[Column]:
    extra = ", udt_name" if dialect == "postgres" else ", NULL"
    rows = _rows(
        connection,
        "select table_schema, table_name, column_name, ordinal_position, data_type, is_nullable,"
        f" character_maximum_length, numeric_precision, numeric_scale{extra}"
        " from information_schema.columns order by table_schema, table_name, ordinal_position",
    )
    columns = []
    for schema, table, name, position, data_type, nullable, length, precision, scale, udt in rows:
        if schema in SYSTEM_SCHEMAS:
            continue
        text = str(data_type)
        lowered = text.lower()
        if dialect == "postgres":
            if lowered in ("numeric", "decimal") and precision is not None:
                text = f"DECIMAL({precision},{scale or 0})"
            elif lowered in ("character varying", "character") and length is not None:
                text = f"VARCHAR({length})"
            elif lowered == "array" and udt:
                text = f"{str(udt).lstrip('_')}[]"
            elif lowered == "user-defined" and udt:
                text = str(udt)
        columns.append(Column(schema, table, name, int(position), text, str(nullable).upper() == "YES"))
    return columns


def _constraints(connection: Any) -> tuple[dict, list]:
    """Primary keys per table, and foreign keys as (schema, table, columns, ref schema, ref table, ref columns)."""
    rows = _rows(
        connection,
        "select tc.constraint_schema, tc.constraint_name, tc.constraint_type, kcu.table_schema, kcu.table_name,"
        " kcu.column_name, kcu.ordinal_position"
        " from information_schema.table_constraints tc"
        " join information_schema.key_column_usage kcu"
        "   on kcu.constraint_schema = tc.constraint_schema and kcu.constraint_name = tc.constraint_name"
        "  and kcu.table_name = tc.table_name"
        " where tc.constraint_type in ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')"
        " order by tc.constraint_schema, tc.constraint_name, kcu.ordinal_position",
    )
    by_name: dict[tuple[str, str], dict[str, Any]] = {}
    for c_schema, c_name, c_type, schema, table, column, _pos in rows:
        entry = by_name.setdefault(
            (c_schema, c_name), {"type": c_type, "schema": schema, "table": table, "columns": []}
        )
        entry["columns"].append(column)

    primary: dict[tuple[str, str], list[str]] = defaultdict(list)
    for entry in by_name.values():
        if entry["type"] == "PRIMARY KEY":
            primary[(entry["schema"], entry["table"])] = entry["columns"]

    foreign = []
    references = _rows(
        connection,
        "select constraint_schema, constraint_name, unique_constraint_schema, unique_constraint_name"
        " from information_schema.referential_constraints",
    )
    for c_schema, c_name, u_schema, u_name in references:
        fk, target = by_name.get((c_schema, c_name)), by_name.get((u_schema, u_name))
        if fk and target and len(fk["columns"]) == len(target["columns"]):
            foreign.append(
                (fk["schema"], fk["table"], fk["columns"], target["schema"], target["table"], target["columns"])
            )
    return primary, foreign


def _comments(connection: Any, dialect: str) -> tuple[dict, dict]:
    """Table and column comments, where the database has them. Best effort: missing is fine."""
    tables: dict[tuple[str, str], str] = {}
    columns: dict[tuple[str, str, str], str] = {}
    try:
        if dialect == "duckdb":
            for schema, table, comment in _rows(
                connection, "select schema_name, table_name, comment from duckdb_tables()"
            ):
                if comment:
                    tables[(schema, table)] = comment
            for schema, table, column, comment in _rows(
                connection, "select schema_name, table_name, column_name, comment from duckdb_columns()"
            ):
                if comment:
                    columns[(schema, table, column)] = comment
        else:
            for schema, table, column, comment in _rows(
                connection,
                "select n.nspname, c.relname, a.attname, col_description(c.oid, a.attnum)"
                " from pg_class c join pg_namespace n on n.oid = c.relnamespace"
                " join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped"
                " where c.relkind in ('r', 'p', 'v', 'm', 'f')",
            ):
                if comment:
                    columns[(schema, table, column)] = comment
            for schema, table, comment in _rows(
                connection,
                "select n.nspname, c.relname, obj_description(c.oid, 'pg_class')"
                " from pg_class c join pg_namespace n on n.oid = c.relnamespace"
                " where c.relkind in ('r', 'p', 'v', 'm', 'f')",
            ):
                if comment:
                    tables[(schema, table)] = comment
    except Exception:  # noqa: BLE001 - comments are optional extras
        return {}, {}
    return tables, columns


def _snowflake(url: str) -> tuple[list[Column], dict, list, dict, dict]:
    from murakumo.importers import snowflake

    try:
        database = snowflake.settings(url).get("database")
        if not database:
            raise SourceUnavailable(
                "name the database: snowflake://ACCOUNT/DATABASE, snowflake:///DATABASE with "
                "SNOWFLAKE_ACCOUNT set, or set SNOWFLAKE_DATABASE"
            )
        connection = snowflake.connect(url)
        try:
            rows, primary, foreign, table_comments, column_comments = snowflake.read_catalogue(connection, database)
        finally:
            connection.close()
    except snowflake.SnowflakeUnavailable as exc:
        raise SourceUnavailable(str(exc)) from None
    columns = [
        Column(schema, table, name, position, data_type, nullable)
        for schema, table, name, position, data_type, nullable in rows
    ]
    return columns, primary, foreign, table_comments, column_comments


def introspect(url: str, source: str, schemas: list[str] | None = None) -> SourceImport:
    """Read a database's structure into a SourceImport. Raises SourceUnavailable."""
    real = resolve(url)
    dialect = dialect_of(real)
    if dialect == "snowflake":
        columns, primary, foreign, table_comments, column_comments = _snowflake(real)
    else:
        connection = connect(url)
        try:
            columns = _columns(connection, dialect)
            primary, foreign = _constraints(connection)
            table_comments, column_comments = _comments(connection, dialect)
        finally:
            connection.close()

    wanted = {s.lower() for s in schemas} if schemas else None
    if wanted is not None:
        columns = [c for c in columns if c.schema.lower() in wanted]

    result = SourceImport(source=SourceSystem(name=source, type=SourceType.DATABASE, connection=redact(url)))
    if not columns:
        found = "no tables" if wanted is None else f"no tables in schema(s) {', '.join(sorted(wanted))}"
        result.diagnostics.append(warning("MK404", f"{found} found in {redact(url)}"))
        return result

    tables: dict[tuple[str, str], list[Column]] = defaultdict(list)
    for column in columns:
        tables[(column.schema, column.table)].append(column)
    # Tables keep their own names; the schema is added only when two schemas share a table name.
    counts: dict[str, int] = defaultdict(int)
    for _schema, table in tables:
        counts[table.lower()] += 1

    def entity_name(schema: str, table: str) -> str:
        return safe_name(f"{schema}__{table}" if counts[table.lower()] > 1 else table, "table")

    for (schema, table), table_columns in tables.items():
        pk = {c.lower() for c in primary.get((schema, table), [])}
        attributes = []
        for column in table_columns:
            logical, exact = sql_to_logical(column.data_type)
            fields: dict[str, Any] = {
                "name": safe_name(column.name),
                "type": logical,
                "source_type": column.data_type,
                "nullable": column.nullable,
            }
            if column.name.lower() in pk:
                fields["key"] = KeyRole.PRIMARY
            comment = column_comments.get((schema, table, column.name))
            if comment:
                fields["description"] = comment
            if not exact:
                result.diagnostics.append(
                    warning("MK404", f"{table}.{column.name}: unknown type {column.data_type!r}; using string")
                )
            attributes.append(Attribute(**fields))
        fields = {"name": entity_name(schema, table), "source": source, "attributes": attributes}
        if (schema, table) in table_comments:
            fields["description"] = table_comments[(schema, table)]
        if wanted is None or len(wanted) > 1:
            fields["properties"] = {"schema": schema}
        result.entities.append(Entity(**fields))

    relationships = []
    for schema, table, cols, ref_schema, ref_table, ref_cols in foreign:
        if (schema, table) not in tables or (ref_schema, ref_table) not in tables:
            continue
        nullable = any(c.nullable for c in tables[(schema, table)] if c.name in cols)
        relationships.append(
            fk_relationship(
                entity_name(schema, table),
                entity_name(ref_schema, ref_table),
                [(safe_name(a), safe_name(b)) for a, b in zip(cols, ref_cols, strict=True)],
                nullable,
            )
        )
    result.relationships = unique_names(relationships)
    return result
