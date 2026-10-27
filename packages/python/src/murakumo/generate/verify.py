"""``murakumo verify``: prove the generated project builds and its tests pass, on DuckDB.

Steps, all in a temporary folder so the project's own ``generated/`` is never touched:
1. generate the project for DuckDB, whatever the manifest's warehouse is;
2. create the raw tables from the generated DDL;
3. load them: synthetic rows that follow the model, or real files given with ``--data``;
4. run ``dbt build``: every model, snapshot and test;
5. run it again, so incremental models and snapshots take their update paths too.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from murakumo.generate import generate
from murakumo.generate.plan import quote_identifier
from murakumo.generate.sample import sample_rows
from murakumo.importers.database import SourceUnavailable
from murakumo.importers.files import READERS, file_format
from murakumo.ir.model import Entity, Layer
from murakumo.manifest import Warehouse
from murakumo.project import Project


@dataclass
class NodeResult:
    name: str
    kind: str  # model, test, snapshot, seed
    status: str  # success, pass, fail, error, warn, skipped
    message: str | None = None
    failures: int | None = None


@dataclass
class VerifyResult:
    nodes: list[NodeResult] = field(default_factory=list)
    loaded: dict[str, int] = field(default_factory=dict)  # raw table -> rows
    folder: Path | None = None
    error: str | None = None
    rerun: bool | None = None  # whether the second build passed; None when it did not run

    @property
    def ok(self) -> bool:
        passed = all(n.status in ("success", "pass", "warn") for n in self.nodes)
        return self.error is None and passed and self.rerun is not False

    def count(self, kind: str, *statuses: str) -> int:
        return sum(n.kind == kind and (not statuses or n.status in statuses) for n in self.nodes)


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


def _load(connection, schema: str, entity: Entity, rows: list[dict]) -> None:
    columns = [a.name for a in entity.attributes]
    names = ", ".join(quote_identifier(c, "duckdb") for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    table = f"{quote_identifier(schema, 'duckdb')}.{quote_identifier(entity.name, 'duckdb')}"
    connection.executemany(
        f"insert into {table} ({names}, _ingested_at, _batch_id) values ({placeholders}, now(), 'verify')",
        [[row.get(c) for c in columns] for row in rows],
    )


def _load_file(connection, schema: str, entity: Entity, location: str, limit: int | None) -> int:
    """Insert a real file's rows into a raw table, matching columns by name."""
    reader = READERS[file_format(location)]
    table = f"{quote_identifier(schema, 'duckdb')}.{quote_identifier(entity.name, 'duckdb')}"
    columns = ", ".join(quote_identifier(a.name, "duckdb") for a in entity.attributes)
    limit_sql = f" limit {int(limit)}" if limit else ""
    connection.execute(
        f"insert into {table} ({columns}, _ingested_at, _batch_id) "
        f"select {columns}, now(), 'verify' from {reader}(?){limit_sql}",
        [location],
    )
    return connection.execute(f"select count(*) from {table}").fetchone()[0]


def _release_dbt() -> None:
    """Close the connections dbt keeps open in this process, so the database file is free."""
    try:
        from dbt.adapters.duckdb.connections import DuckDBConnectionManager
        from dbt.adapters.factory import cleanup_connections, reset_adapters

        # dbt-duckdb keeps one database handle per process; close it, not just forget it.
        environment = getattr(DuckDBConnectionManager, "_ENV", None)
        if environment is not None:
            environment.close()
        DuckDBConnectionManager.close_all_connections()
        cleanup_connections()
        reset_adapters()
    except Exception:  # noqa: BLE001 - best effort; a newer dbt may have moved these
        pass


def verify(
    project: Project,
    rows: int = 200,
    data: dict[str, str] | None = None,
    limit: int | None = None,
    keep: Path | None = None,
    rerun: bool = True,
) -> VerifyResult:
    try:
        import duckdb
        from dbt.cli.main import dbtRunner
    except ImportError:
        raise SourceUnavailable("verify needs dbt and DuckDB: pip install 'murakumo[verify]'") from None

    output = generate(project, Warehouse.DUCKDB)
    result = VerifyResult()
    data = {k.lower(): v for k, v in (data or {}).items()}

    with _workspace(keep) as folder:
        result.folder = folder if keep else None
        project_dir = folder / "dbt"
        for path, content in output.files.items():
            target = folder / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        database = folder / "verify.duckdb"

        connection = duckdb.connect(str(database))
        try:
            connection.execute((folder / "ddl" / "duckdb" / "raw.sql").read_text(encoding="utf-8"))
            raw = [e for e in project.model.entities if e.layer is Layer.RAW]
            synthetic = sample_rows(project.model, raw, rows=rows)
            planned = {t.entity.name: g.schema for g in output.plan.sources for t in g.tables}
            for entity in raw:
                schema = planned[entity.name]
                key = entity.name.lower()
                location = data.get(key) or data.get(f"{(entity.source or '').lower()}.{key}")
                if location:
                    result.loaded[entity.name] = _load_file(connection, schema, entity, location, limit)
                else:
                    _load(connection, schema, entity, synthetic[entity.name])
                    result.loaded[entity.name] = len(synthetic[entity.name])
        except duckdb.Error as exc:
            result.error = f"loading raw tables failed: {str(exc).splitlines()[0]}"
            return result
        finally:
            connection.close()

        environment = project.manifest.targets.environments[0] if project.manifest.targets.environments else "dev"
        saved = {
            k: os.environ.get(k) for k in ("MURAKUMO_DUCKDB_PATH", "MURAKUMO_SCHEMA", "DBT_SEND_ANONYMOUS_USAGE_STATS")
        }
        os.environ.update(
            MURAKUMO_DUCKDB_PATH=str(database), MURAKUMO_SCHEMA="main", DBT_SEND_ANONYMOUS_USAGE_STATS="false"
        )
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

        if run.exception is not None:
            result.error = f"dbt could not run: {run.exception}"
            return result
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
        if not result.nodes and not run.success:
            result.error = "dbt build failed before running any node; run with --keep to inspect the project"
    return result
