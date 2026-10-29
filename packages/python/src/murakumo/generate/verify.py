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

from murakumo.generate import generate
from murakumo.generate.ddl import DDL
from murakumo.generate.plan import quote_identifier
from murakumo.generate.sample import sample_rows
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

    def load_location(self, schema: str, entity: Entity, location: str, limit: int | None) -> int:
        if location.startswith("snowflake:"):
            raise LoadError(f"{entity.name}: Snowflake tables can only be loaded with --warehouse snowflake")
        check_columns(entity, location)
        reader = READERS[file_format(location)]
        columns = ", ".join(quote_identifier(a.name, "duckdb") for a in entity.attributes)
        limit_sql = f" limit {int(limit)}" if limit else ""
        table = self.table(schema, entity)
        self.connection.execute(
            f"insert into {table} ({columns}, _ingested_at, _batch_id) "
            f"select {columns}, now(), '{self.batch}' from {reader}(?){limit_sql}",
            [location],
        )
        return self.connection.execute(f"select count(*) from {table}").fetchone()[0]

    def rows_in(self, schema: str, entity: Entity) -> int:
        return self.connection.execute(f"select count(*) from {self.table(schema, entity)}").fetchone()[0]

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

    def __init__(self, project: Project, folder: Path, prefix: str | None = None):
        from murakumo.generate.dbt import warehouse_database
        from murakumo.importers import snowflake

        self.folder = folder
        self.prefix = datetime.now().strftime("verify_%Y%m%d_%H%M%S_") if prefix is None else prefix
        self.database = os.environ.get("SNOWFLAKE_DATABASE") or warehouse_database(project.manifest)
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

    def load_location(self, schema: str, entity: Entity, location: str, limit: int | None) -> int:
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
                    if location:
                        result.loaded[entity.name] = backend.load_location(schema, entity, location, limit)
                    else:
                        result.loaded[entity.name] = backend.load_rows(schema, entity, synthetic[entity.name])
            except LoadError as exc:
                result.error = str(exc)
                return result
            except Exception as exc:  # noqa: BLE001 - any driver error while loading
                result.error = "loading raw tables failed: " + " ".join(str(exc).split())
                return result
            finally:
                backend.finish_loading()

            environment = project.manifest.targets.environments[0] if project.manifest.targets.environments else "dev"
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


def _build_backend(project: Project, warehouse: Warehouse, folder: Path, prefix: str):
    if warehouse is Warehouse.SNOWFLAKE:
        backend = SnowflakeBackend(project, folder, prefix=prefix)
    else:
        configured = project.manifest.targets.warehouse.database or ""
        name = configured if configured.endswith((".duckdb", ".db")) else f"{project.manifest.project.name}.duckdb"
        backend = DuckDBBackend(project, folder, path=(project.root / name).resolve())
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
    schema_prefix: str = "",
    schedule: str | None = None,
) -> BuildResult:
    """Build the project into its real warehouse and keep everything: the raw landing tables
    (created if missing), and every dbt model, snapshot and test in the environment's schemas.

    Raw tables are loaded only when asked: ``data`` from files or tables, or ``sample`` synthetic
    rows into tables that are still empty. Otherwise dbt builds on whatever ingestion has landed.
    ``schema_prefix`` puts every schema beside the usual ones (for tests), as verify does.
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

    environments = project.manifest.targets.environments or ["dev"]
    environment = environment or environments[0]
    if environment not in environments:
        raise SourceUnavailable(f"no environment {environment!r} in murakumo.yaml; it has {', '.join(environments)}")

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

        backend = _build_backend(project, warehouse, folder, schema_prefix)
        try:
            try:
                backend.run_ddl(DDL(output.plan, schema_prefix=schema_prefix).raw())
                raw = [e for e in project.model.entities if e.layer is Layer.RAW]
                synthetic = sample_rows(project.model, raw, rows=sample) if sample else {}
                planned = {t.entity.name: g.schema for g in output.plan.sources for t in g.tables}
                for entity in raw:
                    schema = schema_prefix + planned[entity.name]
                    key = entity.name.lower()
                    location = data.get(key) or data.get(f"{(entity.source or '').lower()}.{key}")
                    if location:
                        result.loaded[entity.name] = backend.load_location(schema, entity, location, limit)
                    elif sample and backend.rows_in(schema, entity) == 0:
                        result.loaded[entity.name] = backend.load_rows(schema, entity, synthetic[entity.name])
                    elif sample:
                        result.skipped.append(entity.name)
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
                variables["MURAKUMO_SCHEMA_PREFIX"] = schema_prefix
            run = _run_dbt(folder / "dbt", environment, variables, False, result, selector=schedule)
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
