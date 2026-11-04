"""``murakumo verify``: prove the generated project builds and its tests pass.

Steps, in a temporary folder so the project's own ``generated/`` is never touched:
1. generate the project for the verify warehouse (DuckDB by default, or Snowflake);
2. create the raw tables from the generated DDL;
3. load them: synthetic rows that follow the model, real files, or Snowflake tables (``--data``);
4. run ``dbt build``: every model, snapshot and test;
5. run it again, so incremental models and snapshots take their update paths too.

On DuckDB everything lives in a throwaway database file. On Snowflake every schema the run
creates starts with a unique prefix (``verify_20261027_131005_raw_tpch_erp``), so a run never
touches other tables in the database, and the schemas are dropped afterwards unless kept.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from murakumo import loading
from murakumo.generate import generate
from murakumo.generate.ddl import DDL
from murakumo.generate.plan import quote_identifier
from murakumo.generate.sample import sample_rows
from murakumo.generate.targets import target_for
from murakumo.importers.database import SourceUnavailable
from murakumo.importers.files import READERS, file_format
from murakumo.ir.model import Entity, Layer
from murakumo.manifest import Warehouse
from murakumo.project import Project

SUPPORTED = (Warehouse.DUCKDB, Warehouse.SNOWFLAKE)


@dataclass
class NodeResult:
    name: str
    kind: str  # model, test, snapshot, seed
    status: str  # success, pass, fail, error, warn, skipped
    message: str | None = None
    failures: int | None = None


@dataclass
class VerifyResult:
    warehouse: str = "duckdb"
    nodes: list[NodeResult] = field(default_factory=list)
    loaded: dict[str, int] = field(default_factory=dict)  # raw table -> rows
    rejected: dict[str, tuple[int, str | None]] = field(default_factory=dict)  # raw table -> rows refused, why
    folder: Path | None = None
    location: str | None = None  # where the built tables are, when kept
    error: str | None = None
    rerun: bool | None = None  # whether the second build passed; None when it did not run

    @property
    def ok(self) -> bool:
        passed = all(n.status in ("success", "pass", "warn") for n in self.nodes)
        return self.error is None and passed and self.rerun is not False

    def count(self, kind: str, *statuses: str) -> int:
        return sum(n.kind == kind and (not statuses or n.status in statuses) for n in self.nodes)


class LoadError(Exception):
    pass


@contextmanager
def _workspace(keep: Path | None) -> Iterator[Path]:
    if keep is not None:
        if keep.exists():
            shutil.rmtree(keep)
        keep.mkdir(parents=True)
        yield keep
        return
    folder = Path(tempfile.mkdtemp(prefix="murakumo-verify-"))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


# DuckDB ---------------------------------------------------------------------------------------


class DuckDBBackend:
    warehouse = Warehouse.DUCKDB
    prefix = ""
    batch = "verify"

    def __init__(self, project: Project, folder: Path, path: Path | None = None):
        import duckdb

        self.duckdb = duckdb
        self.raw_text = project.manifest.modelling.raw_columns == "text"  # read every column as text
        self.path = path or folder / "verify.duckdb"
        self.connection = duckdb.connect(str(self.path))

    def table(self, schema: str, entity: Entity) -> str:
        return f"{quote_identifier(schema, 'duckdb')}.{quote_identifier(entity.name, 'duckdb')}"

    def run_ddl(self, sql: str) -> None:
        self.connection.execute(sql)

    def load_rows(self, schema: str, entity: Entity, rows: list[dict]) -> int:
        columns = [a.name for a in entity.attributes]
        names = ", ".join(quote_identifier(c, "duckdb") for c in columns)
        placeholders = ", ".join("?" for _ in columns)
        self.connection.executemany(
            f"insert into {self.table(schema, entity)} ({names}, _ingested_at, _batch_id) "
            f"values ({placeholders}, now(), '{self.batch}')",
            [[row.get(c) for c in columns] for row in rows],
        )
        return len(rows)

    def load_location(self, schema: str, entity: Entity, location: str, limit: int | None, mode: str = "append") -> int:
        """Load a file: its rows added (append), or merged by key, the latest of each kept (incremental)."""
        if location.startswith("snowflake:"):
            raise LoadError(f"{entity.name}: Snowflake tables can only be loaded with --warehouse snowflake")
        check_columns(entity, location)
        fmt = file_format(location)
        reader = READERS[fmt]
        columns = ", ".join(quote_identifier(a.name, "duckdb") for a in entity.attributes)
        limit_sql = f" limit {int(limit)}" if limit else ""
        table = self.table(schema, entity)
        new = f"murakumo_new_{entity.name.lower()}"
        # CSV rows DuckDB cannot read are set aside and counted, instead of failing the whole load.
        rejects = f"murakumo_rejects_{entity.name.lower()}"
        duck = target_for(Warehouse.DUCKDB)
        types = ", ".join(
            f"'{a.name}': '{'VARCHAR' if self.raw_text else duck.physical(a.logical_type)}'" for a in entity.attributes
        )
        options = (
            f", types = {{{types}}}, store_rejects = true, rejects_table = '{rejects}', rejects_scan = '{rejects}_scan'"
        )
        self.connection.execute(
            f"create or replace temp table {new} as select {columns}, now() as _ingested_at, "
            f"'{self.batch}' as _batch_id from {reader}(?{options if fmt == 'csv' else ''}){limit_sql}",
            [location],
        )
        arrived = self.connection.execute(f"select count(*) from {new}").fetchone()[0]
        key = [quote_identifier(k, "duckdb") for k in loading.keys(entity)]
        if mode == "incremental" and key:
            latest = quote_identifier(entity.watermark, "duckdb") if entity.watermark else "_ingested_at"
            listed = ", ".join(key)
            self.connection.execute(f"delete from {table} where ({listed}) in (select {listed} from {new})")
            self.connection.execute(
                f"insert into {table} ({columns}, _ingested_at, _batch_id) select {columns}, _ingested_at, _batch_id "
                f"from {new} qualify row_number() over (partition by {listed} order by {latest} desc nulls last) = 1"
            )
        else:
            self.connection.execute(
                f"insert into {table} ({columns}, _ingested_at, _batch_id) "
                f"select {columns}, _ingested_at, _batch_id from {new}"
            )
        self.connection.execute(f"drop table {new}")
        after = self.rows_in(schema, entity)
        rejected, reason = 0, None
        if fmt == "csv":
            rejected, reason = self.connection.execute(
                f"select count(*), any_value(error_type || ': ' || coalesce(error_message, '')) from {rejects}"
            ).fetchone()
        self.last_load = (arrived, rejected, reason)
        return after

    def truncate(self, schema: str, entity: Entity) -> None:
        self.connection.execute(f"delete from {self.table(schema, entity)}")

    def loaded_shas(self, audit: str, table: str) -> set[str]:
        """The files already loaded into a table, by content."""
        self.run_ddl(loading.loaded_files_sql("duckdb", audit))
        rows = self.connection.execute(f"select sha256 from {audit}.loaded_files where table_name = ?", [table])
        return {r[0] for r in rows.fetchall()}

    def record_file(self, audit: str, table: str, path: str, size: int, sha: str, rows: int) -> None:
        self.run_ddl(loading.record_file_sql(audit, table, path, size, sha, rows, self.batch))

    def rows_in(self, schema: str, entity: Entity) -> int:
        return self.connection.execute(f"select count(*) from {self.table(schema, entity)}").fetchone()[0]

    def record_load(self, audit: str, source: str, entity: Entity, counts: tuple[int, int, str | None]) -> None:
        self.run_ddl(record_load_sql("duckdb", audit, self.batch, source, entity.name, counts))

    def finish_loading(self) -> None:
        self.connection.close()

    def dbt_environment(self) -> dict[str, str]:
        return {"MURAKUMO_DUCKDB_PATH": str(self.path), "MURAKUMO_SCHEMA": "main"}

    def describe(self) -> str:
        return str(self.path)

    def cleanup(self) -> None:
        pass


# Snowflake ------------------------------------------------------------------------------------


class SnowflakeBackend:
    """Raw tables in a prefixed schema; rows arrive through each table's own stage.

    Files go up as they are (Snowflake reads Parquet and CSV natively) and synthetic rows as CSV,
    so a Snowflake run needs no DuckDB, except to cut a file down for ``--limit``.
    """

    warehouse = Warehouse.SNOWFLAKE
    cleanup_failed = False
    batch = "verify"

    def __init__(self, project: Project, folder: Path, prefix: str | None = None, database: str | None = None):
        from murakumo.generate.dbt import warehouse_database
        from murakumo.importers import snowflake

        self.folder = folder
        self.prefix = datetime.now().strftime("verify_%Y%m%d_%H%M%S_") if prefix is None else prefix
        self.database = database or os.environ.get("SNOWFLAKE_DATABASE") or warehouse_database(project.manifest)
        try:
            self.connection = snowflake.connect("snowflake://", database=self.database)
        except snowflake.SnowflakeUnavailable as exc:
            raise SourceUnavailable(str(exc)) from None
        cursor = self.connection.cursor()
        try:
            found = cursor.execute(f"show databases like '{self.database.upper()}'").fetchall()
        finally:
            cursor.close()
        if not found:
            self.connection.close()
            raise SourceUnavailable(
                f"Snowflake database {self.database} does not exist or this role cannot see it; "
                "set SNOWFLAKE_DATABASE to the database to build in"
            )

    def table(self, schema: str, entity: Entity) -> str:
        return f"{quote_identifier(schema, 'snowflake')}.{quote_identifier(entity.name, 'snowflake')}"

    def run_ddl(self, sql: str) -> None:
        for cursor in self.connection.execute_string(sql):
            cursor.close()

    def _copy_file(self, schema: str, entity: Entity, path: Path, fmt: str) -> int:
        """Upload a local file to the table's stage and COPY it in, matching columns by name."""
        table = self.table(schema, entity)
        # A table's own stage, written @schema.%table for a table outside the current schema.
        stage = f"@{quote_identifier(schema, 'snowflake')}.%{quote_identifier(entity.name, 'snowflake')}"
        if fmt == "parquet":
            file_format = "type = parquet"
        elif fmt == "csv":
            file_format = (
                "type = csv parse_header = true field_optionally_enclosed_by = '\"' "
                "null_if = ('') empty_field_as_null = true error_on_column_count_mismatch = false"
            )
        else:
            file_format = "type = json strip_outer_array = true"
        cursor = self.connection.cursor()
        try:
            cursor.execute(f"put 'file://{path}' {stage} overwrite = true")
            cursor.execute(
                f"copy into {table} from {stage} file_format = ({file_format}) "
                "match_by_column_name = case_insensitive purge = true"
            )
            cursor.execute(
                f"update {table} set _ingested_at = current_timestamp(), _batch_id = '{self.batch}' "
                "where _batch_id is null"
            )
            return cursor.execute(f"select count(*) from {table}").fetchone()[0]
        finally:
            cursor.close()

    def load_rows(self, schema: str, entity: Entity, rows: list[dict]) -> int:
        names = [a.name for a in entity.attributes]
        path = self.folder / f"{schema}.{entity.name}.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(names)
            for row in rows:
                writer.writerow(["" if row.get(n) is None else _csv_value(row.get(n)) for n in names])
        try:
            return self._copy_file(schema, entity, path, "csv")
        finally:
            path.unlink(missing_ok=True)

    def load_location(self, schema: str, entity: Entity, location: str, limit: int | None, mode: str = "append") -> int:
        before = self.rows_in(schema, entity)
        total = self._load_location(schema, entity, location, limit)
        if mode == "incremental" and loading.keys(entity):
            self._merge_by_key(schema, entity)
            arrived = self._batch_rows(schema, entity)
        else:
            arrived = max(total - before, 0)
        self.last_load = (arrived, 0, None)
        return self.rows_in(schema, entity)

    def _batch_rows(self, schema: str, entity: Entity) -> int:
        cursor = self.connection.cursor()
        try:
            return cursor.execute(
                f"select count(*) from {self.table(schema, entity)} where _batch_id = '{self.batch}'"
            ).fetchone()[0]
        finally:
            cursor.close()

    def _merge_by_key(self, schema: str, entity: Entity) -> None:
        """This batch's rows replace older rows with the same key; within it, the latest of each key."""
        table = self.table(schema, entity)
        listed = ", ".join(quote_identifier(k, "snowflake") for k in loading.keys(entity))
        latest = quote_identifier(entity.watermark, "snowflake") if entity.watermark else "_ingested_at"
        keep = f"{quote_identifier(schema, 'snowflake')}.murakumo_keep_{entity.name.lower()}"
        batch = f"_batch_id = '{self.batch}'"
        for statement in (
            f"delete from {table} where _batch_id <> '{self.batch}' and ({listed}) in "
            f"(select {listed} from {table} where {batch})",
            f"create or replace temporary table {keep} as select * from {table} where {batch} "
            f"qualify row_number() over (partition by {listed} order by {latest} desc nulls last) = 1",
            f"delete from {table} where {batch}",
            f"insert into {table} select * from {keep}",
            f"drop table {keep}",
        ):
            self.run_ddl(statement)

    def truncate(self, schema: str, entity: Entity) -> None:
        self.run_ddl(f"truncate table {self.table(schema, entity)}")

    def loaded_shas(self, audit: str, table: str) -> set[str]:
        self.run_ddl(loading.loaded_files_sql("snowflake", f"{self.database}.{audit}"))
        cursor = self.connection.cursor()
        try:
            rows = cursor.execute(
                f"select sha256 from {self.database}.{audit}.loaded_files where table_name = %s", (table,)
            ).fetchall()
            return {r[0] for r in rows}
        finally:
            cursor.close()

    def record_file(self, audit: str, table: str, path: str, size: int, sha: str, rows: int) -> None:
        self.run_ddl(loading.record_file_sql(f"{self.database}.{audit}", table, path, size, sha, rows, self.batch))

    def _load_location(self, schema: str, entity: Entity, location: str, limit: int | None) -> int:
        limit_sql = f" limit {int(limit)}" if limit else ""
        if location.startswith("snowflake:"):
            source = location.removeprefix("snowflake:")
            table = self.table(schema, entity)
            columns = ", ".join(quote_identifier(a.name, "snowflake") for a in entity.attributes)
            cursor = self.connection.cursor()
            try:
                cursor.execute(
                    f"insert into {table} ({columns}, _ingested_at, _batch_id) "
                    f"select {columns}, current_timestamp(), '{self.batch}' from {source}{limit_sql}"
                )
                return cursor.execute(f"select count(*) from {table}").fetchone()[0]
            finally:
                cursor.close()
        fmt = file_format(location)
        if location.startswith(("http://", "https://")):
            downloaded = self.folder / Path(location.split("?", 1)[0]).name
            urllib.request.urlretrieve(location, downloaded)  # noqa: S310 - the user named this URL
            location = str(downloaded)
        path = Path(location).resolve()
        check_columns(entity, str(path))
        if limit:
            path = self._cut(path, fmt, limit)
            fmt = "parquet"
        return self._copy_file(schema, entity, path, fmt)

    def _cut(self, path: Path, fmt: str, limit: int) -> Path:
        """The first ``limit`` rows of a file, as Parquet."""
        import duckdb

        target = self.folder / f"{path.stem}.limit.parquet"
        connection = duckdb.connect()
        try:
            connection.execute(f"copy (select * from {READERS[fmt]}(?) limit {int(limit)}) to '{target}'", [str(path)])
        finally:
            connection.close()
        return target

    def record_load(self, audit: str, source: str, entity: Entity, counts: tuple[int, int, str | None]) -> None:
        """Record a load in ``audit``: the audit schema, or someone's own schema."""
        self.run_ddl(record_load_sql("snowflake", f"{self.database}.{audit}", self.batch, source, entity.name, counts))

    def rows_in(self, schema: str, entity: Entity) -> int:
        cursor = self.connection.cursor()
        try:
            return cursor.execute(f"select count(*) from {self.table(schema, entity)}").fetchone()[0]
        finally:
            cursor.close()

    def finish_loading(self) -> None:
        pass

    def dbt_environment(self) -> dict[str, str]:
        variables = {"SNOWFLAKE_DATABASE": self.database, "MURAKUMO_SCHEMA_PREFIX": self.prefix}
        if self.prefix:
            variables["MURAKUMO_SCHEMA"] = f"{self.prefix}default"
        return variables

    def describe(self) -> str:
        if not self.prefix:
            return f"Snowflake database {self.database}"
        return f"{self.database}.{self.prefix.upper()}* schemas"

    def cleanup(self) -> None:
        """Drop every schema this run created. Never raises: a failed cleanup must not hide the result."""
        cursor = self.connection.cursor()
        try:
            cursor.execute(f"show schemas like '{self.prefix.upper()}%' in database {self.database}")
            for row in cursor.fetchall():
                cursor.execute(f"drop schema if exists {self.database}.{row[1]} cascade")
        except Exception:  # noqa: BLE001
            self.cleanup_failed = True
        finally:
            cursor.close()
            self.connection.close()


def record_load_sql(dialect: str, audit: str, batch: str, source: str, table: str, counts: tuple) -> str:
    """Statements that record one load in <audit>.row_counts, creating the table the first time."""
    ingested, rejected, reason = counts
    text, stamp, number = (
        ("varchar", "timestamp", "bigint") if dialect == "duckdb" else ("varchar", "timestamp_ntz", "number")
    )

    def literal(value: str | None) -> str:
        return "null" if value is None else "'" + str(value)[:1000].replace("'", "''") + "'"

    return (
        f"create schema if not exists {audit};\n"
        f"create table if not exists {audit}.row_counts (run_id {text}, source {text}, table_name {text}, "
        f"batch_id {text}, rows_ingested {number}, rows_rejected {number}, reason {text}, loaded_at {stamp});\n"
        f"insert into {audit}.row_counts values ({literal(batch)}, {literal(source)}, {literal(table)}, "
        f"{literal(batch)}, {int(ingested)}, {int(rejected)}, {literal(reason)}, current_timestamp);\n"
    )


def check_columns(entity: Entity, location: str) -> None:
    """Fail clearly when a data file lacks a column the model expects, before loading anything."""
    import duckdb

    reader = READERS[file_format(location)]
    try:
        found = [
            row[0] for row in duckdb.connect().execute(f"describe select * from {reader}(?)", [location]).fetchall()
        ]
    except duckdb.Error as exc:
        raise LoadError(f"{entity.name}: cannot read {location}: {str(exc).splitlines()[0]}") from None
    present = {name.lower() for name in found}
    missing = [a.name for a in entity.attributes if a.name.lower() not in present]
    if missing:
        raise LoadError(
            f"{entity.name}: {Path(location).name} has no column {', '.join(missing)}, which the model expects. "
            f"The file has: {', '.join(found)}. Raw columns must match the source: rename them in the model "
            "to match (`murakumo rename column`), or load a file that has them."
        )


def _csv_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


# dbt ------------------------------------------------------------------------------------------


def _release_dbt() -> None:
    """Close the connections dbt keeps open in this process, so database files are free."""
    try:
        from dbt.adapters.factory import cleanup_connections, reset_adapters

        # dbt-duckdb keeps one database handle per process; close it, not just forget it. Only
        # when dbt loaded it: importing it here would load DuckDB into a Snowflake run, and
        # DuckDB then prints "nanobind: leaked ..." warnings at exit.
        if "dbt.adapters.duckdb.connections" in sys.modules:
            manager = sys.modules["dbt.adapters.duckdb.connections"].DuckDBConnectionManager
            environment = getattr(manager, "_ENV", None)
            if environment is not None:
                environment.close()
            manager.close_all_connections()
        cleanup_connections()
        reset_adapters()
    except Exception:  # noqa: BLE001 - best effort; a newer dbt may have moved these
        pass


def _run_dbt(
    project_dir: Path,
    environment: str,
    variables: dict[str, str],
    rerun: bool,
    result: VerifyResult,
    selector: str | None = None,
    select: str | None = None,
    dbt_vars: dict[str, str] | None = None,
):
    from dbt.cli.main import dbtRunner

    variables = {**variables, "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    saved = {k: os.environ.get(k) for k in variables}
    os.environ.update(variables)
    arguments = [
        "build",
        "--project-dir",
        str(project_dir),
        "--profiles-dir",
        str(project_dir),
        "--target",
        environment,
        "--quiet",
        "--no-use-colors",
        *(["--selector", selector] if selector else []),
        *(["--select", select] if select else []),
        *(["--vars", json.dumps(dbt_vars)] if dbt_vars else []),
    ]
    try:
        run = dbtRunner().invoke(arguments)
        if rerun and run.exception is None and run.success:
            # The second build takes the incremental and snapshot-update paths, which a
            # first build never runs; on unchanged data it must also change nothing.
            second = dbtRunner().invoke(arguments)
            result.rerun = second.success
            if not second.success:
                run = second
    finally:
        _release_dbt()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return run


def _collect(run, result: VerifyResult) -> None:
    for node_result in getattr(run.result, "results", None) or []:
        node = node_result.node
        result.nodes.append(
            NodeResult(
                name=getattr(node, "name", node.unique_id),
                kind=node.resource_type.value if hasattr(node.resource_type, "value") else str(node.resource_type),
                status=str(getattr(node_result.status, "value", node_result.status)),
                message=node_result.message,
                failures=node_result.failures,
            )
        )


def verify(
    project: Project,
    rows: int = 200,
    data: dict[str, str] | None = None,
    limit: int | None = None,
    keep: Path | None = None,
    rerun: bool = True,
    warehouse: Warehouse | str = Warehouse.DUCKDB,
    keep_tables: bool = False,
) -> VerifyResult:
    warehouse = Warehouse(warehouse)
    if warehouse not in SUPPORTED:
        raise SourceUnavailable(f"verify runs on {' or '.join(w.value for w in SUPPORTED)}, not {warehouse.value}")
    try:
        import dbt.cli.main  # noqa: F401

        if warehouse is Warehouse.DUCKDB:
            import dbt.adapters.duckdb  # noqa: F401
    except ImportError:
        raise SourceUnavailable("verify needs dbt and DuckDB: pip install 'murakumo[verify]'") from None
    if warehouse is Warehouse.SNOWFLAKE:
        try:
            import dbt.adapters.snowflake  # noqa: F401
        except ImportError:
            raise SourceUnavailable("verify on Snowflake needs dbt-snowflake: pip install dbt-snowflake") from None

    output = generate(project, warehouse)
    result = VerifyResult(warehouse=warehouse.value)
    data = {k.lower(): v for k, v in (data or {}).items()}

    with _workspace(keep) as folder:
        result.folder = folder if keep else None
        for path, content in output.files.items():
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        backend = (SnowflakeBackend if warehouse is Warehouse.SNOWFLAKE else DuckDBBackend)(project, folder)
        try:
            try:
                backend.run_ddl(DDL(output.plan, schema_prefix=backend.prefix).raw())
                raw = [e for e in project.model.entities if e.layer is Layer.RAW]
                synthetic = sample_rows(project.model, raw, rows=rows)
                planned = {t.entity.name: g.schema for g in output.plan.sources for t in g.tables}
                for entity in raw:
                    schema = backend.prefix + planned[entity.name]
                    key = entity.name.lower()
                    location = data.get(key) or data.get(f"{(entity.source or '').lower()}.{key}")
                    before = backend.rows_in(schema, entity)
                    backend.last_load = None
                    if location:
                        result.loaded[entity.name] = backend.load_location(schema, entity, location, limit)
                    else:
                        result.loaded[entity.name] = backend.load_rows(schema, entity, synthetic[entity.name])
                    if backend.last_load and backend.last_load[1]:
                        result.rejected[entity.name] = backend.last_load[1:]
                    if project.manifest.quality.run_log:
                        counts = backend.last_load or (result.loaded[entity.name] - before, 0, None)
                        backend.record_load(f"{backend.prefix}audit", entity.source or "raw", entity, counts)
            except LoadError as exc:
                result.error = str(exc)
                return result
            except Exception as exc:  # noqa: BLE001 - any driver error while loading
                result.error = "loading raw tables failed: " + " ".join(str(exc).split())
                return result
            finally:
                backend.finish_loading()

            environment = project.manifest.targets.names[0]
            run = _run_dbt(folder / "dbt", environment, backend.dbt_environment(), rerun, result)
        finally:
            if keep_tables:
                result.location = backend.describe()
                if hasattr(backend, "connection") and warehouse is Warehouse.SNOWFLAKE:
                    backend.connection.close()
            else:
                backend.cleanup()
                if warehouse is Warehouse.DUCKDB and keep:
                    result.location = backend.describe()

        if run.exception is not None:
            result.error = f"dbt could not run: {run.exception}"
            return result
        _collect(run, result)
        if not result.nodes and not run.success:
            result.error = "dbt build failed before running any node; run with --keep to inspect the project"
    return result


# build ----------------------------------------------------------------------------------------


@dataclass
class BuildResult(VerifyResult):
    skipped: list[str] = field(default_factory=list)  # raw tables that already had rows, so got no sample
    files: dict[str, tuple[int, int]] = field(default_factory=dict)  # raw table -> (files loaded, already loaded)
    modes: dict[str, str] = field(default_factory=dict)  # raw table -> how it took the new rows


def _load_new_files(backend, schema: str, audit: str, entity: Entity, location: str, limit, result) -> tuple | None:
    """Load what is new in a location into a raw table, the table's way (full, append, incremental).

    Returns the counts to record (rows that arrived, rejected, why), or None when nothing was new."""
    mode = loading.mode(entity)
    result.modes[entity.name] = mode
    files = loading.expand(location)
    if not files:
        raise LoadError(f"{entity.name}: no data files in {location}")
    known = backend.loaded_shas(audit, entity.name)
    seen = [(f, None if loading.remote(f) else loading.fingerprint(f)) for f in files]
    new = [(f, sha) for f, sha in seen if sha is None or sha not in known]
    result.files[entity.name] = (len(new), len(seen) - len(new))
    if not new:
        return None
    if mode == "full":
        backend.truncate(schema, entity)
        new = seen  # a full load is every file given, once anything changed
    arrived, rejected, reasons = 0, 0, []
    for path, sha in new:
        backend.last_load = None
        backend.load_location(schema, entity, path, limit, mode)
        rows, refused, why = backend.last_load or (0, 0, None)
        arrived, rejected = arrived + rows, rejected + refused
        if why:
            reasons.append(why)
        if sha is not None:
            backend.record_file(audit, entity.name, path, Path(path).stat().st_size, sha, rows)
    if rejected:
        result.rejected[entity.name] = (rejected, reasons[0] if reasons else None)
    return arrived, rejected, reasons[0] if reasons else None


def _duckdb_path(project: Project, database: str | None = None) -> Path:
    """A DuckDB file: an environment's own (``database``), else the project's (the manifest's
    database, or <project>.duckdb beside it)."""
    configured = database or project.manifest.targets.warehouse.database or ""
    if database and not database.endswith((".duckdb", ".db")):
        configured = f"{database}.duckdb"
    name = configured if configured.endswith((".duckdb", ".db")) else f"{project.manifest.project.name}.duckdb"
    return (project.root / name).resolve()


def _build_backend(project: Project, warehouse: Warehouse, folder: Path, prefix: str, database: str | None = None):
    if warehouse is Warehouse.SNOWFLAKE:
        backend = SnowflakeBackend(project, folder, prefix=prefix, database=database)
    else:
        backend = DuckDBBackend(project, folder, path=_duckdb_path(project, database))
        backend.prefix = prefix
    backend.batch = datetime.now().strftime("build_%Y%m%d_%H%M%S")
    return backend


def build(
    project: Project,
    warehouse: Warehouse | str | None = None,
    environment: str | None = None,
    data: dict[str, str] | None = None,
    limit: int | None = None,
    sample: int = 0,
    schema_prefix: str | None = None,
    schedule: str | None = None,
    select: str | None = None,
    dbt_vars: dict[str, str] | None = None,
) -> BuildResult:
    """Build the project into its real warehouse and keep everything: the raw landing tables
    (created if missing), and every dbt model, snapshot and test in the environment's schemas.

    Raw tables are loaded only when asked: ``data`` from files or tables, or ``sample`` synthetic
    rows into tables that are still empty. Otherwise dbt builds on whatever ingestion has landed.
    ``environment`` is a shared one (dev, stage, prod: its database and schemas) or, by default,
    everyone's own (one schema per person, on a shared warehouse). ``schema_prefix`` puts every
    schema beside the environment's own, as a pull request's preview does.
    ``schedule`` builds only what that schedule refreshes, through its dbt selector.
    """
    warehouse = Warehouse(warehouse or project.manifest.targets.warehouse.type)
    if warehouse not in SUPPORTED:
        raise SourceUnavailable(
            f"build runs on {' or '.join(w.value for w in SUPPORTED)} for now; for {warehouse.value}, "
            "run `dbt build` in generated/dbt"
        )
    try:
        import dbt.cli.main  # noqa: F401

        if warehouse is Warehouse.DUCKDB:
            import dbt.adapters.duckdb  # noqa: F401
        else:
            import dbt.adapters.snowflake  # noqa: F401
    except ImportError:
        package = "'murakumo[verify]'" if warehouse is Warehouse.DUCKDB else "dbt-snowflake"
        raise SourceUnavailable(f"build needs dbt for {warehouse.value}: pip install {package}") from None

    from murakumo.environments import EnvError, placement_for

    try:
        placement = placement_for(project, environment, warehouse, schema_prefix)
    except EnvError as exc:
        raise SourceUnavailable(str(exc)) from None
    environment = placement.environment

    if schedule is not None:
        from murakumo.schedules import effective

        names = [s.name for s in effective(project)[0]]
        match = next((n for n in names if n.lower() == schedule.lower()), None)
        if match is None:
            raise SourceUnavailable(f"no schedule {schedule!r}; the project has {', '.join(names)}")
        schedule = match

    output = generate(project, warehouse)
    result = BuildResult(warehouse=warehouse.value)
    data = {k.lower(): v for k, v in (data or {}).items()}

    with _workspace(None) as folder:
        for path, content in output.files.items():
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

        backend = _build_backend(project, warehouse, folder, placement.prefix, placement.database)
        try:
            try:
                backend.run_ddl(DDL(output.plan, schema_prefix=placement.prefix, single=placement.single).raw())
                raw = [e for e in project.model.entities if e.layer is Layer.RAW]
                synthetic = sample_rows(project.model, raw, rows=sample) if sample else {}
                planned = {t.entity.name: g.schema for g in output.plan.sources for t in g.tables}
                audit = placement.schema("audit")
                for entity in raw:
                    schema = placement.schema(planned[entity.name])
                    key = entity.name.lower()
                    location = data.get(key) or data.get(f"{(entity.source or '').lower()}.{key}")
                    before = backend.rows_in(schema, entity)
                    backend.last_load = None
                    if location:
                        loaded = _load_new_files(backend, schema, audit, entity, location, limit, result)
                        if loaded is None:
                            continue  # nothing new in it
                        backend.last_load = loaded
                        result.loaded[entity.name] = loaded[0]  # the rows that arrived
                    elif sample and before == 0:
                        result.loaded[entity.name] = backend.load_rows(schema, entity, synthetic[entity.name])
                    elif sample:
                        result.skipped.append(entity.name)
                    if backend.last_load and backend.last_load[1]:
                        result.rejected[entity.name] = backend.last_load[1:]
                    if entity.name in result.loaded and project.manifest.quality.run_log:
                        counts = backend.last_load or (result.loaded[entity.name] - before, 0, None)
                        backend.record_load(placement.schema("audit"), entity.source or "raw", entity, counts)
            except LoadError as exc:
                result.error = str(exc)
                return result
            except Exception as exc:  # noqa: BLE001 - any driver error while loading
                result.error = "loading raw tables failed: " + " ".join(str(exc).split())
                return result
            finally:
                backend.finish_loading()
            variables = backend.dbt_environment()
            if warehouse is Warehouse.DUCKDB:
                variables.pop("MURAKUMO_SCHEMA")  # the environment's own schema, as in profiles.yml
            variables.update(placement.dbt_variables())
            if placement.single:
                variables["MURAKUMO_SCHEMA"] = placement.single
            variables["MURAKUMO_SCHEDULE"] = schedule or ""  # recorded in audit.run_log
            run = _run_dbt(
                folder / "dbt",
                environment,
                variables,
                False,
                result,
                selector=schedule,
                select=select,
                dbt_vars=dbt_vars,
            )
        finally:
            result.location = backend.describe()
            if warehouse is Warehouse.SNOWFLAKE:
                backend.connection.close()

        if run.exception is not None:
            result.error = f"dbt could not run: {run.exception}"
            return result
        _collect(run, result)
        if not result.nodes and not run.success:
            result.error = "dbt build failed before running any node"
    return result
