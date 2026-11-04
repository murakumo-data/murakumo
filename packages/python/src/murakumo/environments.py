"""Where a build goes: an environment's database, and the schemas in it.

Each shared environment (dev, stage, prod) can have a database of its own: ``TAXI_DEV``,
``TAXI_STAGE``, ``TAXI_PROD`` on Snowflake, or a DuckDB file each. Its schemas are the project's:
``raw_<source>``, ``staging``, ``marts``, ``snapshots``, ``audit``...

Everyone's own builds (the ``personal`` environment, what ``murakumo build`` builds by default) go
to one schema per person, named after them, in a database for that (``MURAKUMO.ANA``), as dbt
does it: nobody overwrites anybody else's work. A DuckDB file is already personal, so it keeps
the usual schemas.

A pull request's preview goes beside an environment's schemas under a prefix (``pr_42_staging``).

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


@dataclass(frozen=True)
class Placement:
    """Where one build goes: a database (None: the project's default) and its schemas, either the
    usual ones under a prefix or, for someone's own builds, one schema for everything."""

    environment: str
    database: str | None = None
    prefix: str = ""
    single: str | None = None

    def schema(self, name: str) -> str:
        """Where a project schema (staging, raw_nyc_tlc, audit...) is in this placement."""
        return self.single or f"{self.prefix}{name}"

    @property
    def shared(self) -> bool:
        """The environment's own schemas: what deploys build, and what is never dropped."""
        return not self.prefix and not self.single

    def dbt_variables(self) -> dict[str, str]:
        return {"MURAKUMO_SCHEMA_PREFIX": self.prefix, "MURAKUMO_SINGLE_SCHEMA": self.single or ""}

    def describe(self) -> str:
        """In words: "your own copy: the schema ana in MURAKUMO", "prod: the schemas under pr_1_ in TAXI_PROD"."""
        who = "your own copy" if self.environment == "personal" else self.environment
        where = (
            f"the schema {self.single}"
            if self.single
            else f"the schemas under {self.prefix}"
            if self.prefix
            else "the usual schemas"
        )
        return f"{who}: {where}" + (f" in {self.database}" if self.database else "")


def personal_environment(project: Project) -> str:
    return project.manifest.collaboration.personal_environment


def database_for(project: Project, environment: str, warehouse: Warehouse) -> str | None:
    """An environment's database: its own, the personal database, or None for the project's default."""
    manifest = project.manifest
    if environment == personal_environment(project):
        if warehouse is not Warehouse.DUCKDB and manifest.collaboration.personal_database:
            return manifest.collaboration.personal_database
        return None
    env = manifest.targets.environment(environment)
    return env.database if env else None


def placement_for(
    project: Project, environment: str | None, warehouse: Warehouse, explicit: str | None = None
) -> Placement:
    """Where a build of ``environment`` goes (everyone's own by default), under ``explicit`` if given."""
    personal = personal_environment(project)
    name = environment or personal
    if name != personal and project.manifest.targets.environment(name) is None:
        raise EnvError(
            f"no environment {name!r} in murakumo.yaml; it has {', '.join(project.manifest.targets.names)} "
            f"(and {personal}, everyone's own)"
        )
    if name != personal:
        name = project.manifest.targets.environment(name).name
    database = database_for(project, name, warehouse)
    if explicit is not None:
        return Placement(name, database, prefix=_checked(explicit))
    settings = project.manifest.collaboration
    if name == personal and settings.personal_schemas and warehouse is not Warehouse.DUCKDB:
        return Placement(name, database, single=developer())
    return Placement(name, database)


def prefix_for(project: Project, environment: str, warehouse: Warehouse, explicit: str | None = None) -> str:
    """The schema prefix of a build (empty for someone's own schema: see ``placement_for``)."""
    return placement_for(project, environment, warehouse, explicit).prefix


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


def role_for(environment: str | None) -> str | None:
    """The Snowflake role an environment's builds use: SNOWFLAKE_ROLE_<ENV>, as the dbt profiles read
    it (else SNOWFLAKE_ROLE, the connection's default)."""
    return os.environ.get(f"SNOWFLAKE_ROLE_{environment.upper()}") if environment else None


def connect(
    project: Project,
    warehouse: Warehouse | None = None,
    read_only: bool = False,
    database: str | None = None,
    environment: str | None = None,
) -> Connection:
    """A connection to the project's warehouse, in ``database`` (a placement's) or the default one.
    ``read_only`` lets the canvas read a DuckDB file without taking the lock a build needs."""
    warehouse = Warehouse(warehouse or project.manifest.targets.warehouse.type)
    if warehouse is Warehouse.DUCKDB:
        import duckdb

        from murakumo.generate.verify import _duckdb_path

        path = _duckdb_path(project, database)
        if read_only and not path.exists():
            raise EnvError(f"{path.name} does not exist yet: build the project first")
        try:
            return Connection(warehouse, duckdb.connect(str(path), read_only=read_only))
        except duckdb.IOException as exc:
            raise EnvError(f"{path.name} is in use by another process (a build?); try again when it ends") from exc
    if warehouse is Warehouse.SNOWFLAKE:
        from murakumo.generate.dbt import warehouse_database
        from murakumo.importers import snowflake

        database = database or os.environ.get("SNOWFLAKE_DATABASE") or warehouse_database(project.manifest)
        try:
            role = role_for(environment)
            extra = {"role": role} if role else {}
            return Connection(warehouse, snowflake.connect("snowflake://", database=database, **extra), database)
        except snowflake.SnowflakeUnavailable as exc:
            raise EnvError(str(exc)) from None
    raise EnvError(f"environments work on DuckDB and Snowflake for now, not {warehouse.value}")


def _qualified(connection: Connection, schema: str) -> str:
    return schema if connection.warehouse is Warehouse.DUCKDB else f"{connection.database}.{schema}"


# What a prefix holds, cloning and dropping ------------------------------------------------------


def describe(project: Project, connection: Connection, placement: Placement | str) -> list[dict[str, Any]]:
    """Each schema a placement uses, whether it exists, and how many tables it holds."""
    if isinstance(placement, str):  # a prefix, as before 0.7
        placement = Placement("", prefix=placement)
    present = connection.schemas()
    names = (
        [placement.single.lower()]
        if placement.single
        else [placement.schema(s).lower() for s in project_schemas(project, connection.warehouse)]
    )
    return [
        {"schema": name, "exists": name in present, "tables": len(connection.tables(name)) if name in present else 0}
        for name in names
    ]


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


def clone_into(project: Project, connection: Connection, into: Placement, source: Placement) -> list[str]:
    """Copy an environment's tables into someone's own schema (``into.single``), from another
    database if need be: zero-copy table clones on Snowflake, copies on DuckDB."""
    if not into.single:
        raise EnvError("clone_into copies into someone's own schema; use clone for a prefix")
    origin_db = source.database
    warehouse = connection.warehouse
    target = _qualified(connection, into.single.lower())
    attached = ""
    if warehouse is Warehouse.DUCKDB and origin_db:
        from murakumo.generate.verify import _duckdb_path

        connection.execute(f"attach '{_duckdb_path(project, origin_db)}' as murakumo_source (read_only)")
        attached = "murakumo_source."
    connection.execute(f"create schema if not exists {target}")
    copied = []
    try:
        for schema in project_schemas(project, warehouse):
            name = source.schema(schema).lower()
            if warehouse is Warehouse.SNOWFLAKE:
                database = origin_db or connection.database
                rows = (
                    connection.execute(f"show tables in schema {database}.{name}", fetch=True)
                    if _exists(connection, database, name)
                    else []
                )
                tables = [r[1] for r in rows]
                for table in tables:
                    connection.execute(f'create or replace table {target}."{table}" clone {database}.{name}."{table}"')
            else:
                rows = connection.execute(
                    f"select table_name from {attached}information_schema.tables where table_schema = '{name}' "
                    "and table_type = 'BASE TABLE'",
                    fetch=True,
                )
                tables = [r[0] for r in rows]
                for table in tables:
                    connection.execute(
                        f'create or replace table {target}."{table}" as select * from {attached}{name}."{table}"'
                    )
            copied += [f"{name}.{t} -> {into.single.lower()}.{t}" for t in tables]
    finally:
        if attached:
            connection.execute("detach murakumo_source")
    return copied


def _exists(connection: Connection, database: str, schema: str) -> bool:
    rows = connection.execute(f"show schemas like '{schema.upper()}' in database {database}", fetch=True)
    return bool(rows)


def drop(project: Project, connection: Connection, placement: Placement | str) -> list[str]:
    """Drop a placement's schemas: a prefix's, or someone's own schema. Never an environment's own."""
    if isinstance(placement, str):
        placement = Placement("", prefix=_checked(placement))
    if placement.shared:
        raise EnvError("name the prefix to drop; an environment's own schemas are never dropped this way")
    present = connection.schemas()
    names = (
        [placement.single]
        if placement.single
        else [placement.schema(s) for s in project_schemas(project, connection.warehouse)]
    )
    dropped = []
    for name in (n.lower() for n in names):
        if name in present:
            connection.execute(f"drop schema if exists {_qualified(connection, name)} cascade")
            dropped.append(name)
    return dropped


# Setting the environments up -------------------------------------------------------------------


def provision(project: Project) -> str:
    """What an administrator runs once so the environments exist: on Snowflake, a database per
    environment and one for everyone's own schemas, a role to deploy each environment, and a role
    for the people who work on the project. On DuckDB, nothing: each file appears on first build."""
    from murakumo.generate.dbt import warehouse_database

    manifest = project.manifest
    warehouse = manifest.targets.warehouse.type
    name = manifest.project.name.upper()
    if warehouse is Warehouse.DUCKDB:
        files = [e.database or "the project's file" for e in manifest.targets.environments]
        return "-- Nothing to run on DuckDB: each environment is a file, created by its first build:\n" + "".join(
            f"--   {e.name}: {f}\n" for e, f in zip(manifest.targets.environments, files, strict=True)
        )
    if warehouse is not Warehouse.SNOWFLAKE:
        return (
            f"-- Provisioning is written for Snowflake for now; on {warehouse.value}, create the databases by hand.\n"
        )
    compute = os.environ.get("SNOWFLAKE_WAREHOUSE", "TRANSFORMING").upper()
    default = warehouse_database(manifest).upper()
    personal = (manifest.collaboration.personal_database or default).upper()
    developer_role = f"{name}_DEVELOPER"
    lines = [
        f"-- Environments for {manifest.project.name}. Run once, as a role that can create databases and roles",
        "-- (SYSADMIN for the databases, SECURITYADMIN for the roles and grants, or ACCOUNTADMIN for both).",
        "-- Each statement can run again safely.",
        "",
        f"create warehouse if not exists {compute}"
        " warehouse_size = xsmall auto_suspend = 60 initially_suspended = true;",
        "",
        "-- Everyone's own schemas: each person builds into one named after them.",
        f"create database if not exists {personal};",
        f"create role if not exists {developer_role};",
        f"grant usage on warehouse {compute} to role {developer_role};",
        f"grant usage, create schema on database {personal} to role {developer_role};",
        f"grant role {developer_role} to role sysadmin;",
    ]
    environment_roles = []
    for env in manifest.targets.environments:
        database = (env.database or default).upper()
        role = f"{name}_DEPLOYER_{env.name.upper()}"
        environment_roles.append((env.name, role))
        lines += [
            "",
            f"-- {env.name}: deployed by CI" + (f" when {env.branch} changes" if env.branch else "") + ".",
            f"create database if not exists {database};",
            f"create role if not exists {role};",
            f"grant usage on warehouse {compute} to role {role};",
            f"grant all on database {database} to role {role};",
            f"grant all on future schemas in database {database} to role {role};",
            f"grant role {role} to role sysadmin;",
            "-- Developers read it, to copy it into their own schema or a pull request's.",
            f"grant usage on database {database} to role {developer_role};",
            f"grant usage on future schemas in database {database} to role {developer_role};",
            f"grant usage on all schemas in database {database} to role {developer_role};",
            f"grant select on all tables in database {database} to role {developer_role};",
            f"grant select on future tables in database {database} to role {developer_role};",
        ]
    lines += [
        "",
        "-- Then give each person the developer role, and CI each environment's role:",
        f"-- grant role {developer_role} to user <their user>;",
        *(
            f"-- grant role {role} to user <the CI user>;   (CI sets SNOWFLAKE_ROLE_{env.upper()}={role})"
            for env, role in environment_roles
        ),
        f"-- People set SNOWFLAKE_ROLE_PERSONAL={developer_role}.",
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "EnvError",
    "Placement",
    "clone",
    "clone_into",
    "connect",
    "database_for",
    "describe",
    "developer",
    "drop",
    "placement_for",
    "prefix_for",
    "project_schemas",
    "provision",
]
