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
    sql: str | None = None  # SQL to paste into the warehouse, shown as its own block
    sql_where: str | None = None  # where and as whom to run it
    docs: list[str] = field(default_factory=list)  # links to read more
    numbered: bool = False  # steps are done in order, one after the other

    def as_dict(self) -> dict[str, Any]:
        return {
            "area": self.area,
            "title": self.title,
            "status": self.status.value,
            "detail": self.detail,
            "steps": self.steps,
            "sql": self.sql,
            "sql_where": self.sql_where,
            "docs": self.docs,
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
    if os.environ.get("MURAKUMO_LAUNCHER") == "npm":  # started by the npm package, through uvx
        settings = []
        if extras:
            current = [e for e in os.environ.get("MURAKUMO_EXTRAS", "").split(",") if e.strip()]
            settings.append(f'MURAKUMO_EXTRAS="{",".join(sorted(set(current + extras)))}"')
        if packages:
            current = os.environ.get("MURAKUMO_WITH", "").split()
            settings.append(f'MURAKUMO_WITH="{" ".join(sorted(set(current + packages)))}"')
        return f"export {' '.join(settings)}   # then run murakumo again; add it to ~/.zshrc to keep it"
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
                    "Create it with the SQL below, then run `murakumo setup --connect` again.",
                    "Or build in a database you already have: export SNOWFLAKE_DATABASE=<its name>",
                ],
                sql=(
                    "use role accountadmin;\n"
                    f"create database if not exists {database};\n"
                    f"grant usage, create schema on database {database} to role {role};"
                ),
                sql_where="In Snowsight: Projects → Worksheets → + → SQL Worksheet. Paste, select all, then Run.",
                docs=[f"{DOCS}/snowflake.md"],
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


def _local_orchestrator(orchestrator: Orchestrator, root: Path, adapter: str) -> list[str]:
    """Step by step, to run the orchestrator on this machine and point it at the generated dbt project."""
    dbt_dir = root / "generated" / "dbt"
    venv = f"python3 -m venv ~/{orchestrator.value}-env && source ~/{orchestrator.value}-env/bin/activate"
    if orchestrator is Orchestrator.AIRFLOW:
        return [
            "Install Docker Desktop and open it once, so it is running (the Astro CLI runs Airflow in Docker).",
            "brew install astro",
            "mkdir -p ~/airflow-local && cd ~/airflow-local && astro dev init",
            f"echo astronomer-cosmos >> requirements.txt && echo {adapter} >> requirements.txt",
            "astro dev start   # Airflow opens at http://localhost:8080",
            f"Copy {dbt_dir} into ~/airflow-local/dags/dbt and run it with a Cosmos DbtDag (see the Cosmos docs).",
        ]
    if orchestrator is Orchestrator.DAGSTER:
        return [
            f"{venv}   # Dagster in its own environment",
            f"pip install dagster dagster-webserver dagster-dbt {adapter}",
            f"cd ~ && dagster-dbt project scaffold --project-name orchestration --dbt-project-dir {dbt_dir}",
            "cd ~/orchestration && dagster dev   # Dagster opens at http://localhost:3000",
        ]
    return [
        f"{venv}   # Prefect in its own environment",
        f"pip install prefect prefect-dbt {adapter}",
        "prefect server start   # Prefect opens at http://localhost:4200; leave it running",
        f"In a new terminal, write a flow that runs `dbt build` in {dbt_dir} with prefect-dbt (see its docs).",
    ]


_ORCHESTRATOR_DOCS: dict[Orchestrator, list[str]] = {
    Orchestrator.AIRFLOW: [
        "https://www.astronomer.io/docs/astro/cli/install-cli",
        "https://astronomer.github.io/astronomer-cosmos/",
        "https://airflow.apache.org/docs/apache-airflow/stable/installation/index.html",
        "https://docs.docker.com/desktop/",
    ],
    Orchestrator.DAGSTER: [
        "https://docs.dagster.io/getting-started/installation",
        "https://docs.dagster.io/integrations/libraries/dbt",
    ],
    Orchestrator.PREFECT: [
        "https://docs.prefect.io/v3/get-started/install",
        "https://docs.prefect.io/integrations/prefect-dbt",
    ],
    Orchestrator.DATABRICKS_WORKFLOWS: ["https://docs.databricks.com/aws/en/jobs/dbt"],
    Orchestrator.SNOWFLAKE_TASKS: [
        "https://docs.snowflake.com/en/user-guide/data-engineering/dbt-projects-on-snowflake",
        "https://docs.snowflake.com/en/user-guide/tasks-intro",
    ],
    Orchestrator.AWS_GLUE: ["https://docs.aws.amazon.com/glue/latest/dg/what-is-glue.html"],
    Orchestrator.AWS_STEP_FUNCTIONS: ["https://docs.aws.amazon.com/step-functions/latest/dg/welcome.html"],
    Orchestrator.GCP_COMPOSER: ["https://cloud.google.com/composer/docs"],
    Orchestrator.GCP_WORKFLOWS: ["https://cloud.google.com/workflows/docs"],
    Orchestrator.AZURE_DATA_FACTORY: ["https://learn.microsoft.com/en-us/azure/data-factory/"],
}

_CLOUD_ORCHESTRATORS: dict[Orchestrator, list[str]] = {
    Orchestrator.DATABRICKS_WORKFLOWS: [
        "In your Databricks workspace: Workflows → Create job → add a task of type dbt.",
        "Point the task at the generated/dbt folder of this project's git repository.",
    ],
    Orchestrator.SNOWFLAKE_TASKS: [
        "In Snowsight: Projects → dbt Projects → create one from this project's git repository (generated/dbt).",
        "Schedule it with a task: create task ... schedule = 'USING CRON 0 6 * * * UTC' as execute dbt project ...",
    ],
    Orchestrator.AWS_GLUE: [
        "In the AWS console: Glue → ETL jobs → a Python shell job that installs dbt and runs `dbt build`.",
        "Give its IAM role read access to the repository or the S3 copy of generated/dbt.",
    ],
    Orchestrator.AWS_STEP_FUNCTIONS: [
        "Run `dbt build` in a container task (ECS or Fargate) built from generated/dbt.",
        "In Step Functions, create a state machine that starts that task on a schedule (EventBridge).",
    ],
    Orchestrator.GCP_COMPOSER: [
        "In Google Cloud: Composer → Create environment (managed Airflow).",
        "Add astronomer-cosmos to its PyPI packages, and copy generated/dbt into its dags/ bucket.",
    ],
    Orchestrator.GCP_WORKFLOWS: [
        "Run `dbt build` as a Cloud Run job built from generated/dbt.",
        "In Workflows, create a workflow that runs the job; schedule it with Cloud Scheduler.",
    ],
    Orchestrator.AZURE_DATA_FACTORY: [
        "Run `dbt build` in an Azure Container Apps job built from generated/dbt.",
        "In Data Factory, create a pipeline that starts the job on a schedule trigger.",
    ],
}


def orchestrator_checks(project: Project) -> list[Check]:
    orchestrator = project.manifest.generation.orchestrator
    area = f"Orchestrator: {orchestrator.value}"
    note = (
        "Not needed to build: `murakumo build` runs the project by hand. An orchestrator runs it on a "
        "schedule. Murakumo does not generate its pipelines yet; until then, schedule `dbt build` in "
        "generated/dbt with the steps below."
    )
    docs = _ORCHESTRATOR_DOCS.get(orchestrator, [])
    if orchestrator in _CLOUD_ORCHESTRATORS:
        return [
            Check(
                area,
                f"{orchestrator.value} runs in the cloud: nothing to install on this machine",
                Status.INFO,
                note,
                _CLOUD_ORCHESTRATORS[orchestrator],
                docs=docs,
                numbered=True,
            )
        ]
    command = {Orchestrator.AIRFLOW: "airflow", Orchestrator.DAGSTER: "dagster", Orchestrator.PREFECT: "prefect"}[
        orchestrator
    ]
    if shutil.which(command) or (command == "airflow" and shutil.which("astro")):
        return [Check(area, f"{orchestrator.value} is installed", Status.OK, note, docs=docs)]
    adapter = target_for(project.manifest.targets.warehouse.type).adapter
    steps = _local_orchestrator(orchestrator, project.root, adapter)
    return [
        Check(
            area,
            f"{orchestrator.value} is not installed (not needed yet)",
            Status.INFO,
            note,
            steps,
            docs=docs,
            numbered=True,
        )
    ]


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
    checks += git_checks()
    return checks


def _run(*command: str) -> str | None:
    """A command's output, or None when it is missing or fails."""
    import subprocess

    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def git_checks() -> list[Check]:
    area = "Tools"
    # On a Mac, /usr/bin/git exists before the developer tools do, and only offers to install them.
    if sys.platform == "darwin" and _run("xcode-select", "-p") is None:
        return [
            Check(
                area,
                "Install the macOS command line developer tools (git comes with them)",
                Status.WARN,
                "Git keeps the history of the model, generated code and migrations.",
                [
                    "xcode-select --install",
                    "A window opens: click Install, accept, and wait for it to finish (a few minutes).",
                    "Then run `murakumo setup` again.",
                ],
                numbered=True,
            )
        ]
    if shutil.which("git") is None:
        return [
            Check(area, "Install git to version the project", Status.WARN, None, docs=["https://git-scm.com/downloads"])
        ]
    if not _run("git", "config", "user.name") or not _run("git", "config", "user.email"):
        return [
            Check(
                area,
                "Tell git who you are",
                Status.WARN,
                "Git puts your name and email on every commit, and refuses to commit without them.",
                [
                    'git config --global user.name "Your Name"',
                    'git config --global user.email "you@example.com"',
                ],
                numbered=True,
            )
        ]
    return [Check(area, "git is installed and knows who you are", Status.OK)]


def project_checks(project: Project) -> list[Check]:
    area = "Project"
    root = project.root
    checks: list[Check] = []
    repository = next((folder for folder in (root, *root.parents) if (folder / ".git").exists()), None)
    if repository is None:
        checks.append(
            Check(
                area,
                "Not a git repository yet",
                Status.WARN,
                "The model, generated code and migrations are meant to be reviewed in pull requests.",
                [f"cd {root} && git init && git add . && git commit -m 'Start {project.manifest.project.name}'"],
            )
        )
    elif repository == root:
        checks.append(Check(area, "In a git repository", Status.OK))
    else:
        checks.append(
            Check(
                area,
                f"Inside your existing git repository ({repository})",
                Status.OK,
                "Commit the project there, with your usual branches and pull requests; no separate repository needed.",
            )
        )

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
                Check(
                    area,
                    "generated/ is out of date with the model",
                    Status.INFO,
                    "generated/ holds the code written from the model (dbt and SQL). Normal for a new project, "
                    "and after every model change: write it again, then review the diff.",
                    ["murakumo generate"],
                )
            )
        elif changes is not None:
            checks.append(Check(area, "generated/ is up to date", Status.OK))
    if not (root / MIGRATIONS_DIR).is_dir():
        checks.append(
            Check(
                area,
                "Migrations not started",
                Status.INFO,
                "Migrations are the SQL that changes tables already holding data. Start them before the raw "
                "tables hold data you want to keep; not needed while you are trying things out.",
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
