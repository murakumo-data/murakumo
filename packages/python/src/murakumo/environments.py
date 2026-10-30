"""Environments of one's own: personal and pull-request schemas beside the shared ones.

Every schema a project builds (``raw_<source>``, ``staging``, ``marts``, ``snapshots``, ``audit``...)
can carry a prefix. The shared, production schemas have none. A developer's ``dev`` builds go to
``dev_<name>_*`` on a shared warehouse, so people never overwrite each other's work; a pull
request's preview goes to ``pr_<number>_*``. A DuckDB file is already personal, so it gets no
prefix unless asked.

``clone`` copies the shared schemas into a prefix: zero-copy clones on Snowflake (instant, and no
extra storage until rows change), table copies on DuckDB. Applying a branch's new migrations to a
clone, then building and testing there, shows what they will do to production before they reach it.
"""

from __future__ import annotations

import getpass
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Any

from murakumo.manifest import Warehouse
from murakumo.project import Project

LAYER_SCHEMAS = ["staging", "intermediate", "marts", "semantic", "snapshots", "audit"]


class EnvError(Exception):
    pass


def developer() -> str:
    """This person's name for schemas: MURAKUMO_DEVELOPER, git's user name, or the login name."""
    name = os.environ.get("MURAKUMO_DEVELOPER")
    if not name:
        try:
            name = subprocess.run(
                ["git", "config", "user.name"], capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            name = ""
    name = name or getpass.getuser()
    first = name.strip().split()[0] if name.strip() else "me"
    return re.sub(r"[^a-z0-9]+", "_", first.lower()).strip("_") or "me"


def prefix_for(project: Project, environment: str, warehouse: Warehouse, explicit: str | None = None) -> str:
    """The schema prefix a build uses: the one asked for, else a personal one for the personal environment."""
    if explicit is not None:
        return _checked(explicit)
    settings = project.manifest.collaboration
    personal = settings.personal_environment
    if settings.personal_schemas and environment == personal and warehouse is not Warehouse.DUCKDB:
        return f"{personal}_{developer()}_"
    return ""


def _checked(prefix: str) -> str:
    if prefix and not re.fullmatch(r"[a-z][a-z0-9_]*_", prefix.lower()):
        raise EnvError(f"{prefix!r} is not a schema prefix: use letters, digits and _, ending in _")
    return prefix.lower()


def project_schemas(project: Project, warehouse: Warehouse) -> list[str]:
    """The unprefixed schemas the project builds: its raw schemas, then the layers."""
    from murakumo.generate.plan import build_plan
    from murakumo.generate.targets import target_for

    plan = build_plan(project, target_for(warehouse))
    return [*sorted({g.schema for g in plan.sources}), *LAYER_SCHEMAS]


# Warehouse access -------------------------------------------------------------------------------


@dataclass
class Connection:
    """The few statements environments need, on DuckDB or Snowflake."""

    warehouse: Warehouse
    raw: Any
    database: str | None = None

    def execute(self, sql: str, fetch: bool = False) -> list[tuple]:
        if self.warehouse is Warehouse.DUCKDB:
            result = self.raw.execute(sql)
            return result.fetchall() if fetch else []
        cursor = self.raw.cursor()
        try:
            cursor.execute(sql)
            return cursor.fetchall() if fetch else []
        finally:
            cursor.close()

    def schemas(self) -> set[str]:
        if self.warehouse is Warehouse.DUCKDB:
            rows = self.execute(
                "select schema_name from information_schema.schemata where catalog_name = current_database()",
                fetch=True,
            )
        else:
            rows = [(r[1],) for r in self.execute(f"show schemas in database {self.database}", fetch=True)]
        return {r[0].lower() for r in rows}

    def tables(self, schema: str) -> list[str]:
        if self.warehouse is Warehouse.DUCKDB:
            rows = self.execute(
                f"select table_name from information_schema.tables where table_schema = '{schema}' "
                "and table_type = 'BASE TABLE' and table_catalog = current_database()",
                fetch=True,
            )
            return [r[0] for r in rows]
        return [r[1] for r in self.execute(f"show tables in schema {self.database}.{schema}", fetch=True)]

    def close(self) -> None:
        self.raw.close()


def connect(project: Project, warehouse: Warehouse | None = None) -> Connection:
    warehouse = Warehouse(warehouse or project.manifest.targets.warehouse.type)
    if warehouse is Warehouse.DUCKDB:
        import duckdb

        from murakumo.generate.verify import _duckdb_path

        return Connection(warehouse, duckdb.connect(str(_duckdb_path(project))))
    if warehouse is Warehouse.SNOWFLAKE:
        from murakumo.generate.dbt import warehouse_database
        from murakumo.importers import snowflake

        database = os.environ.get("SNOWFLAKE_DATABASE") or warehouse_database(project.manifest)
        try:
            return Connection(warehouse, snowflake.connect("snowflake://", database=database), database)
        except snowflake.SnowflakeUnavailable as exc:
            raise EnvError(str(exc)) from None
    raise EnvError(f"environments work on DuckDB and Snowflake for now, not {warehouse.value}")


def _qualified(connection: Connection, schema: str) -> str:
    return schema if connection.warehouse is Warehouse.DUCKDB else f"{connection.database}.{schema}"


# What a prefix holds, cloning and dropping ------------------------------------------------------


def describe(project: Project, connection: Connection, prefix: str) -> list[dict[str, Any]]:
    present = connection.schemas()
    rows = []
    for schema in project_schemas(project, connection.warehouse):
        name = f"{prefix}{schema}".lower()
        rows.append(
            {
                "schema": name,
                "exists": name in present,
                "tables": len(connection.tables(name)) if name in present else 0,
            }
        )
    return rows


def clone(project: Project, connection: Connection, prefix: str, source: str = "") -> list[str]:
    """Copy the schemas under ``source`` (the shared ones by default) to ``prefix``. Returns what was copied."""
    prefix, source = _checked(prefix), _checked(source)
    if not prefix or prefix == source:
        raise EnvError("clone into a prefix of its own, such as pr_42_")
    present = connection.schemas()
    copied = []
    for schema in project_schemas(project, connection.warehouse):
        origin, target = f"{source}{schema}".lower(), f"{prefix}{schema}".lower()
        if origin not in present:
            continue
        if connection.warehouse is Warehouse.SNOWFLAKE:
            connection.execute(
                f"create or replace schema {_qualified(connection, target)} clone {_qualified(connection, origin)}"
            )
        else:
            connection.execute(f"drop schema if exists {target} cascade")
            connection.execute(f"create schema {target}")
            for table in connection.tables(origin):
                connection.execute(f'create table {target}."{table}" as select * from {origin}."{table}"')
        copied.append(f"{origin} -> {target}")
    return copied


def drop(project: Project, connection: Connection, prefix: str) -> list[str]:
    """Drop every project schema under ``prefix``. Never the shared ones: the prefix is required."""
    prefix = _checked(prefix)
    if not prefix:
        raise EnvError("name the prefix to drop; the shared schemas are never dropped this way")
    present = connection.schemas()
    dropped = []
    for schema in project_schemas(project, connection.warehouse):
        name = f"{prefix}{schema}".lower()
        if name in present:
            connection.execute(f"drop schema if exists {_qualified(connection, name)} cascade")
            dropped.append(name)
    return dropped


__all__ = ["EnvError", "clone", "connect", "describe", "developer", "drop", "prefix_for", "project_schemas"]
