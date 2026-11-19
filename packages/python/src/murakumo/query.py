"""Read-only queries from the canvas: preview any table at any stage, and the data behind it.

A query runs on one of these:

- ``warehouse``: the project's warehouse, in an environment (your own by default): raw tables,
  staging, quarantine, marts, snapshots, the audit tables;
- ``source:<name>``: a source database Murakumo fetches from (its connection's URL variable);
- ``files``: DuckDB on this computer, for files wherever they are: Parquet, CSV, JSON, Excel,
  Iceberg and Delta tables, in a folder or in S3, Google Cloud Storage or Azure (with the
  credentials this computer already has), and what Murakumo landed for REST, SFTP and Kafka sources;
- ``lake``: the lakehouse's Iceberg catalog (``targets.lakehouse``), attached as the builds do.

Only one statement that reads is run: SELECT (with WITH, UNION...), DESCRIBE, SHOW, EXPLAIN. The
result is cut at ``limit`` rows, the query at ``timeout`` seconds. On DuckDB files the connection
itself is read-only; elsewhere it is a session that is never committed. Masking and row-access
policies apply as for anyone using the warehouse with the same role.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as clock
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlglot
from sqlglot import exp

from murakumo.manifest import Warehouse
from murakumo.project import Project

LIMIT = 1000
MAX_LIMIT = 10_000
TIMEOUT = 30

_READS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Describe, exp.Subquery, exp.Values)
_WORDS = re.compile(r"^\s*(show|describe|desc|explain|summarize|pragma\s+table_info)\b", re.IGNORECASE)
_WRITES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Copy,
    exp.Command,
    exp.Set,
    exp.Use,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Grant,
    exp.TruncateTable,
    exp.Pragma,
)
_DANGEROUS_FUNCTIONS = {"pg_sleep", "query", "query_table", "system$cancel_all_queries"}


class QueryError(Exception):
    pass


@dataclass
class Result:
    engine: str
    columns: list[str]
    types: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: int
    sql: str  # what ran, with its limit

    def as_dict(self) -> dict:
        return self.__dict__


@dataclass
class Target:
    """Something to query, with a query that previews it."""

    label: str
    engine: str
    stage: str  # source | landed | raw | staging | quarantine | intermediate | marts | snapshot | lake | audit
    sql: str
    entity: str | None = None
    note: str | None = None

    def as_dict(self) -> dict:
        return self.__dict__


# Read-only ---------------------------------------------------------------------------------------


def dialect_for(engine: str, project: Project) -> str:
    if engine in ("files", "lake"):
        return "duckdb"
    if engine.startswith("source:"):
        url = _source_url(project, engine.split(":", 1)[1])
        scheme = url.split(":", 1)[0].split("+", 1)[0].lower()
        return {
            "postgresql": "postgres",
            "postgres": "postgres",
            "mysql": "mysql",
            "mariadb": "mysql",
            "mssql": "tsql",
            "sqlite": "sqlite",
            "oracle": "oracle",
            "snowflake": "snowflake",
            "redshift": "redshift",
            "bigquery": "bigquery",
            "duckdb": "duckdb",
            "databricks": "databricks",
        }.get(scheme, "postgres")
    from murakumo.generate.targets import target_for

    return target_for(project.manifest.targets.warehouse.type).dialect


def read_only(sql: str, dialect: str) -> tuple[str, exp.Expression | None]:
    """The statement if it only reads, else QueryError. Returns it without a trailing semicolon,
    and its parsed form (None for SHOW, DESCRIBE and friends, which some dialects do not parse)."""
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise QueryError("Type a query: a SELECT, or DESCRIBE a table.")
    quiet = logging.getLogger("sqlglot")
    level = quiet.level
    quiet.setLevel(logging.ERROR)  # SHOW and friends parse as commands, with a warning nobody needs
    try:
        statements = [s for s in sqlglot.parse(text, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as exc:
        if _WORDS.match(text) and ";" not in text:
            return text, None
        raise QueryError(f"Could not read the query: {str(exc).splitlines()[0]}") from None
    finally:
        quiet.setLevel(level)
    if len(statements) != 1:
        raise QueryError("Run one statement at a time.")
    tree = statements[0]
    if isinstance(tree, exp.Command) and _WORDS.match(text) and ";" not in text:
        return text, None  # SHOW TABLES and friends, as the warehouse reads them
    if any(isinstance(node, _WRITES) for node in tree.walk()):
        raise QueryError("The console only reads: SELECT, DESCRIBE, SHOW. Change data through the model and a build.")
    if not isinstance(tree, (*_READS, exp.Show)) and not _WORDS.match(text):
        raise QueryError("The console only reads: SELECT, DESCRIBE, SHOW. Change data through the model and a build.")
    for function in tree.find_all(exp.Anonymous, exp.Func):
        name = (function.name if isinstance(function, exp.Anonymous) else function.sql_name()).lower()
        if name in _DANGEROUS_FUNCTIONS:
            raise QueryError(f"{name}() is not allowed in the console.")
    if isinstance(tree, exp.Select) and tree.args.get("into"):
        raise QueryError("SELECT ... INTO writes a table: the console only reads.")
    return text, tree


def limited(tree: exp.Expression | None, text: str, dialect: str, limit: int) -> str:
    """The query, returning at most ``limit`` + 1 rows (one more, to tell that there were more)."""
    if tree is None or not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Values)):
        return text
    wrapped = exp.select("*").from_(exp.paren(tree.copy()).as_("murakumo_preview")).limit(limit + 1)
    return wrapped.sql(dialect=dialect)


def _plain(value: Any) -> Any:
    """A value JSON can carry, as the warehouse shows it."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value) if value == value.to_integral_value() or abs(value) < 10**15 else str(value)
    if isinstance(value, (datetime, date, clock)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return str(value)


# Running -----------------------------------------------------------------------------------------


def run(
    project: Project,
    sql: str,
    engine: str = "warehouse",
    environment: str | None = None,
    limit: int = LIMIT,
    timeout: int = TIMEOUT,
) -> Result:
    limit = max(1, min(int(limit or LIMIT), MAX_LIMIT))
    timeout = max(1, min(int(timeout or TIMEOUT), 600))
    dialect = dialect_for(engine, project)
    text, tree = read_only(sql, dialect)
    query = limited(tree, text, dialect, limit)
    started = time.monotonic()
    if engine == "warehouse":
        columns, types, rows = _on_warehouse(project, query, environment, timeout)
    elif engine.startswith("source:"):
        columns, types, rows = _on_source(project, engine.split(":", 1)[1], query, timeout)
    elif engine in ("files", "lake"):
        columns, types, rows = _on_duckdb(project, query, timeout, lake=engine == "lake")
    else:
        raise QueryError(f"unknown engine {engine!r}: warehouse, files, lake or source:<name>")
    elapsed = int((time.monotonic() - started) * 1000)
    truncated = len(rows) > limit
    return Result(
        engine=engine,
        columns=columns,
        types=types,
        rows=[[_plain(v) for v in row] for row in rows[:limit]],
        truncated=truncated,
        elapsed_ms=elapsed,
        sql=query,
    )


def _with_timeout(work, timeout: int, cancel=None):
    """Run ``work`` and give up after ``timeout`` seconds (calling ``cancel`` to stop it, if given)."""
    outcome: dict[str, Any] = {}

    def target():
        try:
            outcome["value"] = work()
        except BaseException as exc:  # noqa: BLE001 - handed to the caller
            outcome["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        if cancel is not None:
            try:
                cancel()
            except Exception:  # noqa: BLE001 - best effort
                pass
        raise QueryError(f"The query ran for more than {timeout} seconds and was stopped. Add a filter or a limit.")
    if "error" in outcome:
        error = outcome["error"]
        if isinstance(error, QueryError):
            raise error
        raise QueryError(" ".join(str(error).split())[:1000]) from None
    return outcome["value"]


def _describe(description) -> tuple[list[str], list[str]]:
    columns = [str(d[0]) for d in description or []]
    types = [str(d[1]) if len(d) > 1 and d[1] is not None else "" for d in description or []]
    return columns, types


def _on_warehouse(project: Project, query: str, environment: str | None, timeout: int):
    from murakumo.environments import EnvError, connect, placement_for

    warehouse = project.manifest.targets.warehouse.type
    try:
        placement = placement_for(project, environment, warehouse, None)
        connection = connect(project, read_only=True, database=placement.database, environment=placement.environment)
    except EnvError as exc:
        raise QueryError(str(exc)) from None
    raw = connection.raw
    try:
        if warehouse is Warehouse.DUCKDB:

            def work():
                result = raw.execute(query)
                return (*_describe(result.description), result.fetchmany(MAX_LIMIT + 1))

            return _with_timeout(work, timeout, cancel=raw.interrupt)
        cursor = raw.cursor()
        if warehouse is Warehouse.SNOWFLAKE:
            cursor.execute(f"alter session set statement_timeout_in_seconds = {timeout}")
        elif warehouse in (Warehouse.POSTGRES, Warehouse.REDSHIFT):
            cursor.execute(f"set statement_timeout = {timeout * 1000}")
            if warehouse is Warehouse.POSTGRES:
                cursor.execute("set transaction read only")

        def work():
            cursor.execute(query)
            return (*_describe(cursor.description), cursor.fetchmany(MAX_LIMIT + 1))

        try:
            return _with_timeout(work, timeout, cancel=getattr(cursor, "cancel", None))
        finally:
            try:
                raw.rollback()
            except Exception:  # noqa: BLE001 - nothing to roll back
                pass
            cursor.close()
    finally:
        connection.close()


def _source_url(project: Project, name: str) -> str:
    declaration = next((d for d in project.manifest.sources if d.name.lower() == name.lower()), None)
    connection = getattr(declaration, "connection", None)
    if connection is None or getattr(connection, "kind", None) != "database":
        raise QueryError(f"{name} is not a source database Murakumo connects to")
    url = os.environ.get(connection.url_env)
    if not url:
        raise QueryError(f"Set {connection.url_env} to {name}'s connection URL to query it (murakumo setup says how).")
    return url


def _on_source(project: Project, name: str, query: str, timeout: int):
    url = _source_url(project, name)
    try:
        import sqlalchemy
    except ImportError:
        raise QueryError("Querying a source database needs SQLAlchemy: pip install sqlalchemy") from None
    engine = sqlalchemy.create_engine(url, pool_pre_ping=False)
    try:
        with engine.connect() as connection:
            if engine.dialect.name == "postgresql":
                connection.exec_driver_sql(f"set statement_timeout = {timeout * 1000}")
                connection.exec_driver_sql("set transaction read only")

            def work():
                result = connection.exec_driver_sql(query)
                columns = list(result.keys())
                return columns, ["" for _ in columns], [list(r) for r in result.fetchmany(MAX_LIMIT + 1)]

            try:
                return _with_timeout(work, timeout)
            finally:
                connection.rollback()
    except sqlalchemy.exc.SQLAlchemyError as exc:
        raise QueryError(" ".join(str(exc.orig if getattr(exc, "orig", None) else exc).split())[:1000]) from None
    finally:
        engine.dispose()


def _cloud(connection, query: str) -> None:
    """Sign DuckDB in to the cloud storage a query reads, with this computer's own credentials."""
    lowered = query.lower()
    if any(scheme in lowered for scheme in ("s3://", "gs://", "gcs://", "az://", "abfss://", "https://", "http://")):
        connection.execute("install httpfs; load httpfs")
    if "s3://" in lowered:
        endpoint = os.environ.get("LAKE_S3_ENDPOINT") or os.environ.get("AWS_ENDPOINT_URL")
        if endpoint:
            host = endpoint.split("://", 1)[-1]
            ssl = "false" if endpoint.startswith("http://") or os.environ.get("LAKE_S3_SSL") == "false" else "true"
            connection.execute(
                "create or replace secret murakumo_s3 (type s3, key_id ?, secret ?, endpoint ?, url_style 'path', "
                "use_ssl ?, region ?)",
                [
                    os.environ.get("LAKE_S3_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID", "test"),
                    os.environ.get("LAKE_S3_SECRET") or os.environ.get("AWS_SECRET_ACCESS_KEY", "test"),
                    host,
                    ssl,
                    os.environ.get("LAKE_S3_REGION") or os.environ.get("AWS_REGION", "us-east-1"),
                ],
            )
        else:
            connection.execute("install aws; load aws")
            connection.execute("create or replace secret murakumo_s3 (type s3, provider credential_chain)")
    if "gs://" in lowered or "gcs://" in lowered:
        connection.execute("create or replace secret murakumo_gcs (type gcs, provider credential_chain)")
    if "az://" in lowered or "abfss://" in lowered:
        connection.execute("install azure; load azure")
        account = os.environ.get("AZURE_STORAGE_ACCOUNT")
        if os.environ.get("AZURE_STORAGE_CONNECTION_STRING"):
            connection.execute(
                "create or replace secret murakumo_az (type azure, connection_string ?)",
                [os.environ["AZURE_STORAGE_CONNECTION_STRING"]],
            )
        elif account:
            connection.execute(
                "create or replace secret murakumo_az (type azure, provider credential_chain, account_name ?)",
                [account],
            )
    if "iceberg_scan" in lowered:
        connection.execute("install iceberg; load iceberg")
    if "delta_scan" in lowered:
        connection.execute("install delta; load delta")
    if "read_xlsx" in lowered or ".xlsx" in lowered:
        connection.execute("install excel; load excel")


def _attach_lake(project: Project, connection) -> None:
    from murakumo.generate.lakehouse import ALIAS, settings
    from murakumo.manifest import TableFormat

    lakehouse = settings(project)
    if lakehouse is None or lakehouse.table_format is not TableFormat.ICEBERG:
        raise QueryError("The project has no Iceberg lakehouse (targets.lakehouse) to query.")
    uri = os.environ.get("LAKE_CATALOG_URI") or lakehouse.catalog_uri or "http://localhost:8181/api/catalog"
    connection.execute("install httpfs; load httpfs; install iceberg; load iceberg")
    connection.execute(
        "create or replace secret murakumo_lake_s3 (type s3, key_id ?, secret ?, endpoint ?, url_style 'path', "
        "use_ssl ?, region ?)",
        [
            os.environ.get("LAKE_S3_KEY_ID", "test"),
            os.environ.get("LAKE_S3_SECRET", "test"),
            os.environ.get("LAKE_S3_ENDPOINT", "localhost:4566"),
            os.environ.get("LAKE_S3_SSL", "false"),
            os.environ.get("LAKE_S3_REGION", "us-east-1"),
        ],
    )
    connection.execute(
        "create or replace secret murakumo_lake (type iceberg, client_id ?, client_secret ?, oauth2_server_uri ?, "
        "oauth2_scope 'PRINCIPAL_ROLE:ALL')",
        [
            os.environ.get("LAKE_CLIENT_ID", "root"),
            os.environ.get("LAKE_CLIENT_SECRET", "secret"),
            os.environ.get("LAKE_OAUTH_URI", f"{uri}/v1/oauth/tokens"),
        ],
    )
    warehouse = os.environ.get("LAKE_WAREHOUSE") or lakehouse.warehouse or project.manifest.project.name
    connection.execute(
        f"attach '{warehouse}' as {ALIAS} (type iceberg, endpoint '{uri}', access_delegation_mode 'none', "
        "read_only true)"
    )


def _on_duckdb(project: Project, query: str, timeout: int, lake: bool = False):
    import duckdb

    connection = duckdb.connect(":memory:")
    try:
        # Paths in the query are read from the project folder, as a build reads them.
        connection.execute(f"set file_search_path = '{str(project.root).replace(chr(39), chr(39) * 2)}'")
        try:
            if lake:
                _attach_lake(project, connection)
            _cloud(connection, query)
        except duckdb.Error as exc:
            raise QueryError(f"Could not reach the storage: {' '.join(str(exc).split())[:600]}") from None

        def work():
            result = connection.execute(query)
            return (*_describe(result.description), result.fetchmany(MAX_LIMIT + 1))

        return _with_timeout(work, timeout, cancel=connection.interrupt)
    finally:
        connection.close()


# What there is to query ----------------------------------------------------------------------------


def _quoted(name: str, dialect: str) -> str:
    from murakumo.generate.plan import quote_identifier

    return quote_identifier(name, dialect)


def _reader(location: str, fmt: str | None = None) -> str:
    from murakumo.importers.files import file_format

    try:
        fmt = fmt or file_format(location)
    except ValueError:
        fmt = "csv"
    escaped = location.replace("'", "''")
    return {
        "parquet": f"read_parquet('{escaped}')",
        "csv": f"read_csv('{escaped}')",
        "tsv": f"read_csv('{escaped}', delim = '\\t')",
        "json": f"read_json_auto('{escaped}')",
        "jsonl": f"read_json_auto('{escaped}', format = 'newline_delimited')",
        "xlsx": f"read_xlsx('{escaped}')",
    }.get(fmt, f"read_csv('{escaped}')")


def targets(project: Project, environment: str | None = None) -> list[Target]:
    """Every table there is to query, at every stage, each with a query that previews it."""
    from murakumo.environments import EnvError, placement_for
    from murakumo.generate.plain import LAYER_SCHEMAS, SNAPSHOT_SCHEMA
    from murakumo.generate.plan import build_plan
    from murakumo.generate.targets import target_for
    from murakumo.ingest import landing_folder

    warehouse = project.manifest.targets.warehouse.type
    target = target_for(warehouse)
    dialect = target.dialect
    plan = build_plan(project, target)
    try:
        placement = placement_for(project, environment, warehouse, None)
    except EnvError:
        placement = None

    def where(schema: str, name: str) -> str:
        actual = placement.schema(schema) if placement else schema
        qualified = f"{_quoted(actual, dialect)}.{_quoted(name, dialect)}"
        if warehouse is Warehouse.SNOWFLAKE and placement and placement.database:
            qualified = f"{placement.database}.{qualified}"
        return qualified

    def preview(relation: str) -> str:
        if dialect == "tsql":
            return f"select top 100 * from {relation}"
        return f"select * from {relation} limit 100"

    found: list[Target] = []
    for group in plan.sources:
        for table in group.tables:
            name = table.entity.name
            found.append(Target(f"{name} (raw)", "warehouse", "raw", preview(where(group.schema, name)), name))
    for model in plan.models:
        stage = "quarantine" if model.extra.get("side") == "quarantined" else model.layer
        schema = LAYER_SCHEMAS.get(model.layer, model.layer)
        label = model.name if model.name == model.entity.name else f"{model.name} ({stage})"
        note = "Rows set aside by a quarantine rule, with the rule each failed." if stage == "quarantine" else None
        found.append(Target(label, "warehouse", stage, preview(where(schema, model.name)), model.entity.name, note))
    for snapshot in plan.snapshots:
        entity = snapshot.entity.name if snapshot.entity else None
        found.append(
            Target(
                f"{snapshot.name} (snapshot)",
                "warehouse",
                "snapshot",
                preview(where(SNAPSHOT_SCHEMA, snapshot.name)),
                entity,
            )
        )
    for audit in ("run_log", "row_counts", "dq_results"):
        found.append(Target(f"audit.{audit}", "warehouse", "audit", preview(where("audit", audit))))

    # The sources themselves, where they are.
    for declaration in project.manifest.sources:
        connection = declaration.connection
        kind = getattr(connection, "kind", None)
        if kind == "database":
            for query in connection.tables:
                source_sql = query.source.strip()
                sql = (
                    f"select * from ({source_sql}) as source limit 100"
                    if source_sql.lower().startswith("select")
                    else f"select * from {source_sql} limit 100"
                )
                found.append(
                    Target(
                        f"{declaration.name}: {query.source}", f"source:{declaration.name}", "source", sql, query.table
                    )
                )
        elif kind == "files":
            for fileset in connection.files:
                location = f"{connection.location.rstrip('/')}/{fileset.pattern}"
                sql = f"select * from {_reader(location, fileset.format)} limit 100"
                found.append(Target(f"{declaration.name}: {fileset.pattern}", "files", "source", sql, fileset.table))
        if kind in ("rest", "sftp", "kafka", "protobuf", "database", "files"):
            for table in declaration.tables():
                folder = landing_folder(project, declaration.name, table)
                pattern = folder if folder.endswith(".jsonl") else f"{folder}/*.jsonl"
                sql = f"select * from read_json_auto('{pattern}', union_by_name = true) limit 100"
                found.append(
                    Target(
                        f"{table} (landed)",
                        "files",
                        "landed",
                        sql,
                        table,
                        "What Murakumo fetched and has not necessarily loaded yet.",
                    )
                )

    from murakumo.generate.lakehouse import ALIAS, settings

    lakehouse = settings(project)
    if lakehouse is not None and lakehouse.table_format.value == "iceberg":
        for model in plan.models:
            if model.layer in lakehouse.layers and model.extra.get("side") != "quarantined":
                schema = LAYER_SCHEMAS.get(model.layer, model.layer)
                actual = placement.schema(schema) if placement else schema
                sql = f"select * from {ALIAS}.{actual}.{model.name} limit 100"
                found.append(Target(f"{model.name} (Iceberg)", "lake", "lake", sql, model.entity.name))
    return found


def engines(project: Project) -> list[dict]:
    """Where queries can run, for the canvas's picker."""
    from murakumo.generate.lakehouse import settings

    found = [
        {"engine": "warehouse", "label": f"Warehouse ({project.manifest.targets.warehouse.type.value})"},
        {"engine": "files", "label": "Files and storage (DuckDB here)"},
    ]
    lakehouse = settings(project)
    if lakehouse is not None and lakehouse.table_format.value == "iceberg":
        found.append({"engine": "lake", "label": "Iceberg catalog"})
    for declaration in project.manifest.sources:
        if getattr(declaration.connection, "kind", None) == "database":
            found.append({"engine": f"source:{declaration.name}", "label": f"Source database: {declaration.name}"})
    return found


__all__ = ["LIMIT", "QueryError", "Result", "Target", "engines", "limited", "read_only", "run", "targets"]
