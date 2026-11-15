"""``verify`` and ``build`` on the warehouses reached through an ordinary database connection:
PostgreSQL, Redshift, Databricks, BigQuery, Fabric and Synapse.

DuckDB and Snowflake have backends of their own (``verify.py``), which load files the warehouse's
fastest way. These load through plain INSERT statements instead: rows are read on this computer
(by DuckDB, for files) and sent in batches. That is slower than a bulk load, and right for what
``verify`` does (a few hundred made-up rows) and for a first build; a platform's real loads go
through its ingestion (Auto Loader, COPY, load jobs), which Murakumo generates separately.

Every schema a verify run creates starts with a unique prefix, so a run never touches other
tables, and they are dropped afterwards unless kept.

Each warehouse reads its connection from the same variables its generated dbt profile reads.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from murakumo import loading
from murakumo.generate.plan import quote_identifier
from murakumo.generate.targets import target_for
from murakumo.importers.database import SourceUnavailable
from murakumo.importers.files import READERS, file_format
from murakumo.ir.model import Entity
from murakumo.manifest import Warehouse
from murakumo.project import Project

BATCH_ROWS = 500


def statements(sql: str) -> list[str]:
    """A script's statements: split at a semicolon that ends a line, comments-only pieces dropped."""
    out = []
    for piece in re.split(r";[ \t]*(?:\n|$)", sql):
        body = "\n".join(line for line in piece.splitlines() if not line.strip().startswith("--")).strip()
        if body:
            out.append(body)
    return out


def _need(*names: str) -> list[str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise SourceUnavailable(f"set {', '.join(missing)} to reach the warehouse")
    return [os.environ[n] for n in names]


class SqlBackend:
    warehouse: Warehouse
    param = "%s"  # the driver's placeholder
    driver = ""  # what to pip install, for the message when it is missing
    cascade = " cascade"
    cleanup_failed = False
    batch = "verify"
    last_load: tuple | None = None

    def __init__(self, project: Project, folder: Path, prefix: str | None = None, database: str | None = None):
        from murakumo.generate.dbt import warehouse_database

        self.project = project
        self.folder = folder
        self.target = target_for(self.warehouse)
        self.dialect = self.target.dialect
        self.raw_text = project.manifest.modelling.raw_columns == "text"
        self.prefix = datetime.now().strftime("verify_%Y%m%d_%H%M%S_") if prefix is None else prefix
        self.database = database or warehouse_database(project.manifest).lower()
        try:
            self.connection = self.connect()
        except ImportError:
            raise SourceUnavailable(f"{self.warehouse.value} needs its driver: pip install {self.driver}") from None
        except SourceUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - each driver has its own errors
            raise SourceUnavailable(
                f"cannot connect to {self.warehouse.value}: " + " ".join(str(exc).split())[:300]
            ) from None

    # each warehouse ---------------------------------------------------------------------------

    def connect(self):
        raise NotImplementedError

    def dbt_environment(self) -> dict[str, str]:
        variables = {"MURAKUMO_SCHEMA_PREFIX": self.prefix}
        if self.prefix:
            variables["MURAKUMO_SCHEMA"] = f"{self.prefix}default"
        return variables

    def schemas(self) -> list[str]:
        rows = self.execute("select schema_name from information_schema.schemata", fetch=True)
        return [r[0] for r in rows]

    def create_schema(self, name: str) -> str:
        return f"create schema if not exists {self.q(name)}"

    def create_table(self, schema: str, name: str, columns: str) -> str:
        return f"create table if not exists {self.q(schema)}.{name} ({columns})"

    def drop_schema(self, name: str) -> None:
        self.execute(f"drop schema if exists {self.q(name)}{self.cascade}")

    # shared -----------------------------------------------------------------------------------

    def q(self, name: str) -> str:
        return quote_identifier(name, self.dialect)

    def table(self, schema: str, entity: Entity) -> str:
        return f"{self.q(schema)}.{self.q(entity.name)}"

    def execute(self, sql: str, parameters: list | None = None, fetch: bool = False, many: bool = False):
        cursor = self.connection.cursor()
        try:
            if many:
                cursor.executemany(sql, parameters)
            elif parameters is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, parameters)
            return cursor.fetchall() if fetch else None
        finally:
            cursor.close()

    def run_ddl(self, sql: str) -> None:
        for statement in statements(sql):
            self.execute(statement)

    def _value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict | list):
            return json.dumps(value, default=str)
        if self.raw_text:
            return value if isinstance(value, str) else str(value)
        if isinstance(value, bool | int | float | str | Decimal | date | datetime | time | bytes):
            return value
        return str(value)

    def _insert(self, schema: str, entity: Entity, rows: list[list]) -> None:
        names = ", ".join(self.q(a.name) for a in entity.attributes)
        marks = ", ".join(self.param for _ in entity.attributes)
        sql = (
            f"insert into {self.table(schema, entity)} ({names}, _ingested_at, _batch_id) "
            f"values ({marks}, current_timestamp, '{self.batch}')"
        )
        for start in range(0, len(rows), BATCH_ROWS):
            chunk = [[self._value(v) for v in row] for row in rows[start : start + BATCH_ROWS]]
            self.execute(sql, chunk, many=True)

    def load_rows(self, schema: str, entity: Entity, rows: list[dict]) -> int:
        columns = [a.name for a in entity.attributes]
        self._insert(schema, entity, [[row.get(c) for c in columns] for row in rows])
        return len(rows)

    def load_location(self, schema: str, entity: Entity, location: str, limit: int | None, mode: str = "append") -> int:
        """Read a file on this computer and insert its rows: added (append), or replacing the rows
        with the same key, the latest of each key kept (incremental)."""
        import duckdb

        from murakumo.generate.verify import LoadError, check_columns

        if location.startswith("snowflake:"):
            raise LoadError(f"{entity.name}: Snowflake tables can only be loaded with --warehouse snowflake")
        check_columns(entity, location)
        columns = ", ".join(quote_identifier(a.name, "duckdb") for a in entity.attributes)
        keys = loading.keys(entity)
        latest = ""
        if mode == "incremental" and keys:
            listed = ", ".join(quote_identifier(k, "duckdb") for k in keys)
            order = quote_identifier(entity.watermark, "duckdb") if entity.watermark else "1"
            latest = f" qualify row_number() over (partition by {listed} order by {order} desc nulls last) = 1"
        reader = READERS[file_format(location)]
        connection = duckdb.connect()
        try:
            rows = connection.execute(
                f"select {columns} from {reader}(?){latest}" + (f" limit {int(limit)}" if limit else ""), [location]
            ).fetchall()
        finally:
            connection.close()
        if latest and rows:
            positions = [[a.name for a in entity.attributes].index(k) for k in keys]
            where = " and ".join(f"{self.q(k)} = {self.param}" for k in keys)
            self.execute(
                f"delete from {self.table(schema, entity)} where {where}",
                [[self._value(row[p]) for p in positions] for row in rows],
                many=True,
            )
        self._insert(schema, entity, [list(r) for r in rows])
        self.last_load = (len(rows), 0, None)
        return self.rows_in(schema, entity)

    def truncate(self, schema: str, entity: Entity) -> None:
        self.execute(f"delete from {self.table(schema, entity)}")

    def rows_in(self, schema: str, entity: Entity) -> int:
        return self.execute(f"select count(*) from {self.table(schema, entity)}", fetch=True)[0][0]

    def _audit(self, audit: str, name: str, columns: list[tuple[str, str]]) -> str:
        """Make sure an audit table is there; its qualified name."""
        types = {"text": self.target.physical_name("string"), "number": self.target.physical_name("bigint")}
        types["time"] = self.target.physical_name("timestamp")
        self.execute(self.create_schema(audit))
        self.execute(self.create_table(audit, name, ", ".join(f"{c} {types[t]}" for c, t in columns)))
        return f"{self.q(audit)}.{name}"

    def loaded_shas(self, audit: str, table: str) -> set[str]:
        files = self._audit(audit, "loaded_files", LOADED_FILES)
        rows = self.execute(f"select sha256 from {files} where table_name = {self.param}", [table], fetch=True)
        return {r[0] for r in rows}

    def record_file(self, audit: str, table: str, path: str, size: int, sha: str, rows: int) -> None:
        files = self._audit(audit, "loaded_files", LOADED_FILES)
        marks = ", ".join([self.param] * 6)
        self.execute(
            f"insert into {files} values ({marks}, current_timestamp)", [table, path, size, sha, rows, self.batch]
        )

    def record_load(self, audit: str, source: str, entity: Entity, counts: tuple[int, int, str | None]) -> None:
        ingested, rejected, reason = counts
        counted = self._audit(audit, "row_counts", ROW_COUNTS)
        marks = ", ".join([self.param] * 7)
        self.execute(
            f"insert into {counted} values ({marks}, current_timestamp)",
            [self.batch, source, entity.name, self.batch, int(ingested), int(rejected), reason and reason[:1000]],
        )

    def finish_loading(self) -> None:
        commit = getattr(self.connection, "commit", None)
        if commit is not None:
            commit()

    def describe(self) -> str:
        where = f"{self.warehouse.value} database {self.database}"
        return f"{where}, schemas {self.prefix}*" if self.prefix else where

    def cleanup(self) -> None:
        """Drop every schema this run created. Never raises: a failed cleanup must not hide the result."""
        try:
            if self.prefix:
                for name in self.schemas():
                    if name.lower().startswith(self.prefix.lower()):
                        self.drop_schema(name)
                self.finish_loading()
        except Exception:  # noqa: BLE001
            self.cleanup_failed = True
        finally:
            try:
                self.connection.close()
            except Exception:  # noqa: BLE001
                pass


LOADED_FILES = [
    ("table_name", "text"),
    ("path", "text"),
    ("size", "number"),
    ("sha256", "text"),
    ("rows_ingested", "number"),
    ("batch_id", "text"),
    ("loaded_at", "time"),
]
ROW_COUNTS = [
    ("run_id", "text"),
    ("source", "text"),
    ("table_name", "text"),
    ("batch_id", "text"),
    ("rows_ingested", "number"),
    ("rows_rejected", "number"),
    ("reason", "text"),
    ("loaded_at", "time"),
]


class PostgresBackend(SqlBackend):
    """DB_HOST, DB_USER, DB_PASSWORD; DB_PORT (5432) and DB_NAME (the project's database) if they differ."""

    warehouse = Warehouse.POSTGRES
    driver = "'murakumo[postgres]' dbt-postgres"

    def connect(self):
        import psycopg

        host, user, password = _need("DB_HOST", "DB_USER", "DB_PASSWORD")
        self.database = os.environ.get("DB_NAME") or self.database
        return psycopg.connect(
            host=host,
            port=int(os.environ.get("DB_PORT", "5432")),
            user=user,
            password=password,
            dbname=self.database,
            autocommit=True,
        )


class RedshiftBackend(SqlBackend):
    """DB_HOST, DB_USER, DB_PASSWORD; DB_PORT (5439) and DB_NAME if they differ."""

    warehouse = Warehouse.REDSHIFT
    driver = "redshift-connector dbt-redshift"

    def connect(self):
        import redshift_connector

        host, user, password = _need("DB_HOST", "DB_USER", "DB_PASSWORD")
        self.database = os.environ.get("DB_NAME") or self.database
        connection = redshift_connector.connect(
            host=host,
            port=int(os.environ.get("DB_PORT", "5439")),
            user=user,
            password=password,
            database=self.database,
        )
        connection.autocommit = True
        return connection


class DatabricksBackend(SqlBackend):
    """DATABRICKS_HOST, DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN; the catalog is the project's database."""

    warehouse = Warehouse.DATABRICKS
    param = "?"
    driver = "databricks-sql-connector dbt-databricks"

    def connect(self):
        from databricks import sql

        host, path, token = _need("DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN")
        self.database = os.environ.get("DATABRICKS_CATALOG") or self.database
        connection = sql.connect(
            server_hostname=host.removeprefix("https://").rstrip("/"), http_path=path, access_token=token
        )
        cursor = connection.cursor()
        try:
            cursor.execute(f"use catalog {quote_identifier(self.database, 'databricks')}")
        finally:
            cursor.close()
        return connection

    def dbt_environment(self) -> dict[str, str]:
        return {**super().dbt_environment(), "DATABRICKS_CATALOG": self.database}


class BigQueryBackend(SqlBackend):
    """BIGQUERY_PROJECT, and Google's application-default credentials (`gcloud auth application-default login`)."""

    warehouse = Warehouse.BIGQUERY
    driver = "google-cloud-bigquery dbt-bigquery"

    def connect(self):
        from google.cloud import bigquery
        from google.cloud.bigquery import dbapi

        (project,) = _need("BIGQUERY_PROJECT")
        self.database = project
        self.client = bigquery.Client(project=project)
        return dbapi.connect(self.client)

    def schemas(self) -> list[str]:
        return [d.dataset_id for d in self.client.list_datasets()]

    def truncate(self, schema: str, entity: Entity) -> None:
        self.execute(f"delete from {self.table(schema, entity)} where true")  # BigQuery wants a WHERE


class TsqlBackend(SqlBackend):
    """FABRIC_SERVER (or SYNAPSE_SERVER), and a service principal: AZURE_TENANT_ID, AZURE_CLIENT_ID,
    AZURE_CLIENT_SECRET. Needs Microsoft's ODBC Driver 18 for SQL Server."""

    warehouse = Warehouse.FABRIC
    param = "?"
    driver = "pyodbc dbt-fabric"
    cascade = ""
    server_variable = "FABRIC_SERVER"

    def connect(self):
        import pyodbc

        server, _tenant, client, secret = _need(
            self.server_variable, "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"
        )
        return pyodbc.connect(
            "Driver={ODBC Driver 18 for SQL Server};"
            f"Server={server},1433;Database={self.database};Encrypt=yes;"
            f"Authentication=ActiveDirectoryServicePrincipal;UID={client};PWD={secret}",
            autocommit=True,
        )

    def create_schema(self, name: str) -> str:
        return f"if not exists (select 1 from sys.schemas where name = '{name}') exec('create schema {self.q(name)}')"

    def create_table(self, schema: str, name: str, columns: str) -> str:
        return f"if object_id('{schema}.{name}') is null create table {self.q(schema)}.{name} ({columns})"

    def drop_schema(self, name: str) -> None:
        found = self.execute(
            "select table_name, table_type from information_schema.tables where table_schema = ?", [name], fetch=True
        )
        for table, kind in found:
            self.execute(f"drop {'view' if kind == 'VIEW' else 'table'} {self.q(name)}.{self.q(table)}")
        self.execute(f"drop schema {self.q(name)}")


class SynapseBackend(TsqlBackend):
    warehouse = Warehouse.SYNAPSE
    driver = "pyodbc dbt-synapse"
    server_variable = "SYNAPSE_SERVER"


BACKENDS: dict[Warehouse, type[SqlBackend]] = {
    Warehouse.POSTGRES: PostgresBackend,
    Warehouse.REDSHIFT: RedshiftBackend,
    Warehouse.DATABRICKS: DatabricksBackend,
    Warehouse.BIGQUERY: BigQueryBackend,
    Warehouse.FABRIC: TsqlBackend,
    Warehouse.SYNAPSE: SynapseBackend,
}

__all__ = ["BACKENDS", "SqlBackend", "statements"]
