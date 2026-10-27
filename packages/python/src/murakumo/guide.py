"""``murakumo setup``: what this project needs on this machine, checked, with the next step for each.

The manifest says what the project builds on: a warehouse, sources to import, an orchestrator.
Each of those needs something installed, a variable set, a key file, or a connection that works.
This module turns the manifest into a checklist, checks what it can, and says exactly what to do
about the rest. Nothing here changes the machine; it only looks.

``connect=True`` also opens the connections (warehouse and sources), which can take a few
seconds and, on a cloud warehouse, wakes it up.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from murakumo.generate import compare, generate
from murakumo.generate.dbt import profiles
from murakumo.generate.plan import build_plan
from murakumo.generate.targets import target_for
from murakumo.manifest import Orchestrator, Warehouse
from murakumo.migrate import MIGRATIONS_DIR
from murakumo.project import Project

DOCS = "https://github.com/murakumo-data/murakumo/blob/main/docs"


class Status(StrEnum):
    OK = "ok"  # done
    TODO = "todo"  # needed before the project can build; `setup --check` fails
    WARN = "warn"  # works, but worth fixing
    INFO = "info"  # for your information, or not needed yet


@dataclass
class Check:
    area: str
    title: str
    status: Status
    detail: str | None = None
    steps: list[str] = field(default_factory=list)  # commands or instructions, in order

    def as_dict(self) -> dict[str, Any]:
        return {
            "area": self.area,
            "title": self.title,
            "status": self.status.value,
            "detail": self.detail,
            "steps": self.steps,
        }


# How to install things into the environment murakumo itself runs in ------------------------------


def installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def install_command(extras: list[str], packages: list[str]) -> str:
    """The command that adds ``extras`` and ``packages`` to the environment murakumo runs in."""
    extras = sorted(set(extras))
    requirement = f'"murakumo[{",".join(extras)}]"' if extras else "murakumo"
    if "/uv/tools/" in sys.prefix.replace("\\", "/"):  # installed with `uv tool install`
        withs = "".join(f" --with {p}" for p in packages)
        return f"uv tool install --force {requirement}{withs}"
    if installed("pip"):
        return f"pip install {requirement} {' '.join(packages)}".rstrip()
    return f"uv pip install {requirement} {' '.join(packages)}".rstrip()  # a uv-made venv has no pip


# Warehouse ----------------------------------------------------------------------------------------

_ENV_VAR = re.compile(r"env_var\('([A-Z0-9_]+)'(\s*,\s*'([^']*)')?\)")


def profile_variables(project: Project, warehouse: Warehouse) -> tuple[list[str], dict[str, str]]:
    """The variables the generated profiles.yml reads: those it needs, and those with a default."""
    plan = build_plan(project, target_for(warehouse))
    required: list[str] = []
    optional: dict[str, str] = {}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, str):
            for match in _ENV_VAR.finditer(value):
                name, has_default, default = match.group(1), match.group(2), match.group(3)
                if name.startswith("MURAKUMO_"):
                    continue
                if has_default is None and name not in required:
                    required.append(name)
                elif has_default is not None:
                    optional.setdefault(name, default or "")

    walk(profiles(plan))
    return required, optional


_WAREHOUSE_EXTRAS = {
    Warehouse.DUCKDB: (["duckdb", "verify"], "duckdb"),
    Warehouse.SNOWFLAKE: (["snowflake", "verify"], "snowflake.connector"),
    Warehouse.POSTGRES: (["postgres"], "psycopg"),
}

_SETUP_DOCS = {
    Warehouse.SNOWFLAKE: f"{DOCS}/snowflake.md",
}


def warehouse_checks(project: Project, connect: bool) -> list[Check]:
    warehouse = project.manifest.targets.warehouse.type
    target = target_for(warehouse)
    area = f"Warehouse: {warehouse.value}"
    checks: list[Check] = []

    adapter = target.adapter
    adapter_module = f"dbt.adapters.{adapter.removeprefix('dbt-')}"
    extras, driver = _WAREHOUSE_EXTRAS.get(warehouse, (["verify"], None))
    missing_packages = [] if installed(adapter_module) else [adapter]
    if not installed("dbt") and adapter not in missing_packages:
        missing_packages.append(adapter)
    driver_missing = driver is not None and not installed(driver)
    if missing_packages or driver_missing:
        checks.append(
            Check(
                area,
                f"Install dbt and the {warehouse.value} adapter",
                Status.TODO,
                f"dbt builds the generated project; {adapter} lets it talk to {warehouse.value}.",
                [install_command(extras, [p for p in missing_packages if p != "dbt-duckdb"])],
            )
        )
    else:
        checks.append(Check(area, f"dbt and {adapter} are installed", Status.OK))

    if warehouse is Warehouse.DUCKDB:
        database = project.manifest.targets.warehouse.database or f"{project.manifest.project.name}.duckdb"
        checks.append(
            Check(
                area,
                "DuckDB runs locally: no account or credentials needed",
                Status.OK,
                f"The database is the file {database}, created on the first build.",
            )
        )
        return checks

    required, optional = profile_variables(project, warehouse)
    if warehouse is Warehouse.SNOWFLAKE:
        # Key-pair login needs the key path; password login works too, but is not recommended.
        if os.environ.get("SNOWFLAKE_PASSWORD") and not os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
            optional.pop("SNOWFLAKE_PRIVATE_KEY_PATH", None)
        else:
            required.append("SNOWFLAKE_PRIVATE_KEY_PATH")
            optional.pop("SNOWFLAKE_PRIVATE_KEY_PATH", None)
        optional.pop("SNOWFLAKE_AUTHENTICATOR", None)
    missing = [name for name in required if not os.environ.get(name)]
    guide = _SETUP_DOCS.get(warehouse)
    if missing:
        steps = [f'export {name}="..."' for name in missing]
        steps.append("Put the exports in ~/.zshrc (or your shell's profile) and open a new terminal.")
        if guide:
            steps.insert(0, f"First-time setup, step by step: {guide}")
        checks.append(
            Check(
                area,
                f"Set {len(missing)} connection variable(s)",
                Status.TODO,
                "The generated profiles.yml reads these; none of them goes into the project.",
                steps,
            )
        )
    else:
        checks.append(Check(area, f"Connection variables are set ({', '.join(required)})", Status.OK))
    if optional:
        defaults = ", ".join(f"{k} (default {v or 'empty'})" for k, v in sorted(optional.items()))
        checks.append(Check(area, "Optional variables", Status.INFO, defaults))

    key = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
    if warehouse is Warehouse.SNOWFLAKE and key:
        path = Path(key).expanduser()
        if not path.is_file():
            checks.append(
                Check(
                    area,
                    "The private key file does not exist",
                    Status.TODO,
                    str(path),
                    [f"Create it as in {DOCS}/snowflake.md, step 1, or fix SNOWFLAKE_PRIVATE_KEY_PATH."],
                )
            )
        elif os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            checks.append(
                Check(
                    area,
                    "The private key file can be read by other users",
                    Status.WARN,
                    str(path),
                    [f"chmod 600 {path}"],
                )
            )
        else:
            checks.append(Check(area, "The private key file exists and is private", Status.OK, str(path)))

    if connect and warehouse is Warehouse.SNOWFLAKE and not missing:
        database = os.environ.get("SNOWFLAKE_DATABASE") or optional.get("SNOWFLAKE_DATABASE", "")
        checks.extend(_connect_snowflake(area, database))
    elif connect and not missing:
        checks.append(
            Check(
                area,
                "Connection test",
                Status.INFO,
                f"Murakumo cannot test {warehouse.value} connections yet; `dbt debug` in generated/dbt can.",
            )
        )
    return checks


def _connect_snowflake(area: str, database: str) -> list[Check]:
    try:
        from murakumo.importers.snowflake import SnowflakeUnavailable, connect
    except ImportError:
        return [Check(area, "Connection test skipped: the Snowflake driver is not installed", Status.INFO)]
    try:
        connection = connect()
        try:
            cursor = connection.cursor()
            user, role, warehouse = cursor.execute(
                "select current_user(), current_role(), current_warehouse()"
            ).fetchone()
            visible = {row[1].upper() for row in cursor.execute("show databases").fetchall()}
        finally:
            connection.close()
    except SnowflakeUnavailable as exc:
        steps = [f"Check the variables against {DOCS}/snowflake.md, step 3."]
        return [Check(area, "Cannot connect to Snowflake", Status.TODO, str(exc), steps)]
    except Exception as exc:  # noqa: BLE001 - any driver error is the answer here
        return [Check(area, "Cannot connect to Snowflake", Status.TODO, str(exc).splitlines()[0])]
    checks = [Check(area, f"Connected to Snowflake as {user}", Status.OK, f"role {role}, warehouse {warehouse}")]
    if database and database.upper() not in visible:
        checks.append(
            Check(
                area,
                f"Database {database} does not exist, or {role} cannot see it",
                Status.TODO,
                "dbt builds into it; the raw landing tables go there too.",
                [
                    f"In a Snowsight worksheet, as ACCOUNTADMIN: create database if not exists {database};",
                    f"grant usage, create schema on database {database} to role {role};",
                    "Or point the project at an existing one: export SNOWFLAKE_DATABASE=...",
                ],
            )
        )
    elif database:
        checks.append(Check(area, f"Database {database} is there", Status.OK))
    return checks


# Sources ------------------------------------------------------------------------------------------

_DRIVERS = {
    "postgres": ("psycopg", "postgres"),
    "snowflake": ("snowflake.connector", "snowflake"),
    "duckdb": ("duckdb", "duckdb"),
}


def source_checks(project: Project, connect: bool) -> list[Check]:
    from murakumo.importers.database import dialect_of

    checks: list[Check] = []
    root = project.root
    for declaration in project.manifest.sources:
        area = f"Source: {declaration.name}"
        if not declaration.from_:
            checks.append(Check(area, "No from: line: add its tables by hand or on the canvas", Status.INFO))
            continue
        scheme, _, target = declaration.from_.partition(":")
        if scheme in ("csv", "excel", "file", "openapi", "json") and "://" not in target:
            path = (root / target) if not Path(target).is_absolute() else Path(target)
            if path.exists():
                checks.append(Check(area, f"{scheme} file found", Status.OK, target))
            else:
                checks.append(
                    Check(
                        area,
                        f"{scheme} file not found",
                        Status.TODO,
                        str(path),
                        [f"Put the file at {target}, or change the from: line in murakumo.yaml."],
                    )
                )
            if scheme in ("file", "json") and not installed("duckdb"):
                checks.append(
                    Check(area, "Reading files needs DuckDB", Status.TODO, None, [install_command(["duckdb"], [])])
                )
            continue
        if scheme in ("file", "openapi", "json"):
            checks.append(Check(area, f"Reads {target}", Status.INFO, "Fetched when you run `murakumo import`."))
            continue
        if scheme != "introspect":
            checks.append(
                Check(
                    area,
                    f"Unknown from: scheme {scheme!r}",
                    Status.TODO,
                    declaration.from_,
                    ["Use csv:, excel:, introspect:, file:, openapi: or json:."],
                )
            )
            continue
        url = target
        if url.startswith("env:"):
            variable = url.removeprefix("env:")
            if not os.environ.get(variable):
                checks.append(
                    Check(
                        area,
                        f"Set {variable}",
                        Status.TODO,
                        "The connection string for this source, kept out of the project.",
                        [
                            f'export {variable}="postgresql://user:password@host:5432/database"',
                            "Use a read-only user: importing only reads the catalogue.",
                        ],
                    )
                )
                continue
            url = os.environ[variable]
        try:
            dialect = dialect_of(url)
        except Exception:  # noqa: BLE001
            checks.append(
                Check(
                    area,
                    "Cannot tell what database this is",
                    Status.TODO,
                    None,
                    ["Start the URL with postgresql://, snowflake:// or duckdb:///."],
                )
            )
            continue
        module, extra = _DRIVERS.get(dialect, (None, None))
        if module and not installed(module):
            checks.append(
                Check(area, f"Install the {dialect} driver", Status.TODO, None, [install_command([extra], [])])
            )
            continue
        checks.append(Check(area, f"{dialect} connection configured", Status.OK))
        if connect:
            checks.append(_connect_source(area, url))
    return checks


def _connect_source(area: str, url: str) -> Check:
    from murakumo.importers.database import SourceUnavailable, introspect

    try:
        imported = introspect(url, "check")
    except SourceUnavailable as exc:
        return Check(area, "Cannot connect", Status.TODO, str(exc))
    except Exception as exc:  # noqa: BLE001
        return Check(area, "Cannot connect", Status.TODO, str(exc).splitlines()[0])
    return Check(area, f"Connected: {len(imported.entities)} table(s) visible", Status.OK)


# Orchestrator -------------------------------------------------------------------------------------

_ORCHESTRATORS: dict[Orchestrator, tuple[str | None, list[str]]] = {
    Orchestrator.AIRFLOW: (
        "airflow",
        [
            "Easiest locally: the Astro CLI, which runs Airflow in Docker. Install Docker Desktop, then:",
            "brew install astro && astro dev init && astro dev start   # http://localhost:8080",
            "Or with pip, in its own virtual environment, using Airflow's constraints file. Set AIRFLOW_VERSION "
            "to the latest release from https://airflow.apache.org/docs/apache-airflow/stable/installation/, then:",
            'pip install "apache-airflow==$AIRFLOW_VERSION" --constraint "https://raw.githubusercontent.com/apache/'
            f'airflow/constraints-$AIRFLOW_VERSION/constraints-{sys.version_info.major}.{sys.version_info.minor}.txt"',
            "To run the generated dbt project from Airflow today: pip install astronomer-cosmos",
            "Managed Airflow (Amazon MWAA, Google Cloud Composer, Astronomer) runs dbt the same way.",
        ],
    ),
    Orchestrator.DAGSTER: ("dagster", ["pip install dagster dagster-webserver dagster-dbt", "dagster dev"]),
    Orchestrator.PREFECT: ("prefect", ["pip install prefect prefect-dbt", "prefect server start"]),
}


def orchestrator_checks(project: Project) -> list[Check]:
    orchestrator = project.manifest.generation.orchestrator
    area = f"Orchestrator: {orchestrator.value}"
    note = (
        "Murakumo does not generate pipelines for it yet; that comes in a later release. Until then, "
        "schedule `dbt build` in generated/dbt (after applying new migrations) with what you have."
    )
    command, steps = _ORCHESTRATORS.get(orchestrator, (None, []))
    if command is None:
        return [Check(area, f"{orchestrator.value} runs in the cloud: nothing to install here", Status.INFO, note)]
    if shutil.which(command) or (command == "airflow" and shutil.which("astro")):
        return [Check(area, f"{orchestrator.value} is installed", Status.OK, note)]
    return [Check(area, f"{orchestrator.value} is not installed (not needed yet)", Status.INFO, note, steps)]


# Everything else ----------------------------------------------------------------------------------


def tool_checks(project: Project) -> list[Check]:
    from murakumo.server import canvas_files

    checks: list[Check] = []
    area = "Tools"
    version = sys.version_info
    if version < (3, 11):
        checks.append(
            Check(
                area,
                f"Python {version.major}.{version.minor} is too old",
                Status.TODO,
                None,
                ["Install Python 3.11 or newer."],
            )
        )
    else:
        checks.append(Check(area, f"Python {version.major}.{version.minor}", Status.OK))
    if installed("fastapi") and installed("uvicorn") and canvas_files() is not None:
        checks.append(Check(area, "The canvas is ready: murakumo canvas", Status.OK))
    elif installed("fastapi") and installed("uvicorn"):
        checks.append(
            Check(
                area,
                "The canvas is not built",
                Status.WARN,
                None,
                ["cd apps/canvas && npm ci && npm run build   # from the Murakumo repository"],
            )
        )
    else:
        checks.append(
            Check(area, "Install the canvas to model visually", Status.WARN, None, [install_command(["canvas"], [])])
        )
    if shutil.which("git") is None:
        checks.append(
            Check(area, "Install git to version the project", Status.WARN, None, ["https://git-scm.com/downloads"])
        )
    return checks


def project_checks(project: Project) -> list[Check]:
    area = "Project"
    root = project.root
    checks: list[Check] = []
    if not any((folder / ".git").exists() for folder in (root, *root.parents)):
        checks.append(
            Check(
                area,
                "Not a git repository yet",
                Status.WARN,
                "The model, generated code and migrations are meant to be reviewed in pull requests.",
                [f"cd {root} && git init && git add . && git commit -m 'Start {project.manifest.project.name}'"],
            )
        )
    else:
        checks.append(Check(area, "In a git repository", Status.OK))

    if not project.model.entities:
        checks.append(
            Check(
                area,
                "The model is empty",
                Status.INFO,
                None,
                [
                    "murakumo import ...   # from a dictionary, database, file or API",
                    "murakumo canvas       # or draw it",
                ],
            )
        )
    else:
        try:
            changes = compare(generate(project), root)
        except Exception:  # noqa: BLE001 - validation reports model problems
            changes = None
        if changes is not None and changes.any:
            checks.append(
                Check(area, "generated/ is out of date with the model", Status.INFO, None, ["murakumo generate"])
            )
        elif changes is not None:
            checks.append(Check(area, "generated/ is up to date", Status.OK))
    if not (root / MIGRATIONS_DIR).is_dir():
        checks.append(
            Check(
                area,
                "Migrations not started",
                Status.INFO,
                "Start them before the landing tables hold data you want to keep.",
                ["murakumo migrate"],
            )
        )
    lakehouse = project.manifest.targets.lakehouse
    if lakehouse is not None:
        checks.append(
            Check(
                area,
                f"Lakehouse ({lakehouse.table_format.value}) settings are not used yet",
                Status.INFO,
                "Murakumo generates for the warehouse only in this release.",
            )
        )
    return checks


def run_checks(project: Project, connect: bool = False) -> list[Check]:
    return [
        *tool_checks(project),
        *warehouse_checks(project, connect),
        *source_checks(project, connect),
        *orchestrator_checks(project),
        *project_checks(project),
    ]


__all__ = ["Check", "Status", "install_command", "profile_variables", "run_checks"]
