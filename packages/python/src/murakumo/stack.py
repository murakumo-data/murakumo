"""The stack: the tools a data platform is built from, chosen in one place.

Each part of the stack is one choice: the warehouse, how data is fetched from the sources
(extract), how it is loaded into the warehouse (load), where fetched files land, the transform, the
orchestrator and CI. Most live in murakumo.yaml already (``targets.warehouse.type``,
``generation.orchestrator``); the rest are in its ``stack:`` section. ``murakumo stack`` shows them
together, ``murakumo stack set`` changes them, and ``check`` says when two choices do not go
together (Snowpipe loads into Snowflake, not BigQuery).

Every change is recorded in the decisions log, ``model/decisions.yaml``: what changed, from what,
when, by whom and why. The pipeline documentation prints it, so whoever rebuilds the platform
knows why it is built the way it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from murakumo.manifest import Orchestrator, Warehouse
from murakumo.project import DECISIONS_FILE, Project


class StackError(Exception):
    pass


@dataclass(frozen=True)
class Part:
    key: str
    title: str
    question: str
    path: tuple[str, ...]  # where it lives in murakumo.yaml
    options: dict[str, str]  # value -> what it means; empty: free text


GENERATED_ORCHESTRATORS = {"airflow", "gcp_composer", "dagster", "prefect", "databricks_workflows", "snowflake_tasks"}

PARTS = [
    Part(
        "warehouse",
        "Warehouse",
        "Where the data is stored and the SQL runs.",
        ("targets", "warehouse", "type"),
        {
            "duckdb": "DuckDB, on this computer: no account needed",
            "snowflake": "Snowflake",
            "databricks": "Databricks",
            "bigquery": "Google BigQuery",
            "redshift": "Amazon Redshift",
            "postgres": "PostgreSQL",
            "fabric": "Microsoft Fabric",
            "synapse": "Azure Synapse",
        },
    ),
    Part(
        "extract",
        "Extract",
        "What fetches the data from the sources (APIs, SFTP, databases, files) into the landing area.",
        ("stack", "extract"),
        {
            "python": "Python scripts, one per source: run anywhere, by the orchestrator or `murakumo ingest`",
            "lambda": "AWS Lambda: the same scripts as functions, on a schedule, landing in S3",
            "glue": "AWS Glue jobs: Spark for databases, Python shell for the rest, landing in S3",
            "none": "Something else fetches it (Fivetran, Airbyte, the source itself)",
        },
    ),
    Part(
        "landing",
        "Landing",
        "Where fetched files land before they are loaded: a folder, or s3://bucket/prefix.",
        ("stack", "landing"),
        {},
    ),
    Part(
        "load",
        "Load",
        "What loads the landed files into the warehouse's raw tables.",
        ("stack", "load"),
        {
            "build": "Each build loads the new files (`murakumo build --data`, or the orchestrator's load step)",
            "snowpipe": "Snowpipe: Snowflake loads each file as it lands in S3",
            "autoloader": "Databricks Auto Loader: a streaming job picks up each new file",
        },
    ),
    Part(
        "transform",
        "Transform",
        "What turns the raw tables into the model.",
        ("generation", "transform"),
        {"dbt": "dbt: SQL models, tests and snapshots"},
    ),
    Part(
        "python",
        "Python",
        "Also write the transformation as Python, for teams that run Spark or Polars.",
        ("generation", "python_engine"),
        {"none": "No", "polars": "Polars", "pyspark": "PySpark"},
    ),
    Part(
        "orchestrator",
        "Orchestrator",
        "What runs the schedules: fetch, load, build, test.",
        ("generation", "orchestrator"),
        {
            "airflow": "Apache Airflow",
            "dagster": "Dagster",
            "prefect": "Prefect",
            "databricks_workflows": "Databricks Workflows",
            "snowflake_tasks": "Snowflake tasks",
            "gcp_composer": "Google Cloud Composer (managed Airflow)",
            "aws_step_functions": "AWS Step Functions (a script to start from)",
            "aws_glue": "AWS Glue workflows (a script to start from)",
            "azure_data_factory": "Azure Data Factory (a script to start from)",
            "gcp_workflows": "Google Cloud Workflows (a script to start from)",
        },
    ),
    Part(
        "ci",
        "CI",
        "What checks each pull request and deploys each environment.",
        ("stack", "ci"),
        {"github": "GitHub Actions", "gitlab": "GitLab CI", "none": "None"},
    ),
]
PART = {p.key: p for p in PARTS}


def _get(data: Any, path: tuple[str, ...]) -> Any:
    for key in path:
        data = getattr(data, key)
    return getattr(data, "value", data)


def current(project: Project) -> dict[str, str]:
    return {p.key: str(_get(project.manifest, p.path)) for p in PARTS}


def check(project: Project) -> list[tuple[str, str, str]]:
    """What does not go together, as (level, part, message): level is error or warning."""
    s = current(project)
    cloud = "://" in s["landing"]
    problems: list[tuple[str, str, str]] = []

    def say(level: str, part: str, message: str) -> None:
        problems.append((level, part, message))

    if s["load"] == "snowpipe":
        if s["warehouse"] != "snowflake":
            say("error", "load", f"Snowpipe loads into Snowflake; the warehouse is {s['warehouse']}")
        if not s["landing"].startswith("s3://"):
            say("error", "landing", "Snowpipe picks files up from a stage: land them in s3://bucket/prefix")
    if s["load"] == "autoloader":
        if s["warehouse"] != "databricks":
            say("error", "load", f"Auto Loader runs on Databricks; the warehouse is {s['warehouse']}")
        if not cloud:
            say("error", "landing", "Auto Loader reads cloud storage: land files in s3://, abfss:// or gs://")
    if s["extract"] in {"lambda", "glue"} and not s["landing"].startswith("s3://"):
        say(
            "error",
            "landing",
            f"{PART['extract'].options[s['extract']].split(':')[0]} lands files in S3: s3://bucket/prefix",
        )
    if s["load"] == "build" and cloud and s["warehouse"] == "duckdb":
        say("warning", "landing", "DuckDB reads s3:// with its httpfs extension and your AWS credentials")
    if s["transform"] != "dbt":
        say("warning", "transform", f"Murakumo writes dbt; {s['transform']} is not generated yet")
    if s["orchestrator"] not in GENERATED_ORCHESTRATORS:
        say(
            "warning",
            "orchestrator",
            f"for {s['orchestrator']}, Murakumo writes a script to start from, not a pipeline",
        )
    if s["orchestrator"] == "snowflake_tasks" and s["warehouse"] != "snowflake":
        say("error", "orchestrator", "Snowflake tasks run in Snowflake")
    if s["orchestrator"] == "databricks_workflows" and s["warehouse"] != "databricks":
        say("warning", "orchestrator", "Databricks Workflows run dbt against Databricks most simply")
    connected = [d for d in project.manifest.sources if d.connection is not None]
    if s["extract"] != "none" and not connected:
        say("warning", "extract", "no source has a connection yet, so there is nothing to fetch: add one to a source")
    raw = {e.name for e in project.model.entities if e.layer.value == "raw"}
    for declaration in connected:
        for table in declaration.tables():
            if table not in raw:
                say("warning", "extract", f"{declaration.name} fills {table}, which is not a raw table in the model")
    return problems


def _write(root: Path, path: tuple[str, ...], value: Any) -> None:
    from murakumo.manifest_edit import ManifestEditError, write_section

    for depth in range(len(path), 0, -1):  # the deepest section that exists takes the rest as a mapping
        nested: Any = value
        for key in reversed(path[depth:]):
            nested = {key: nested}
        try:
            write_section(root, path[:depth], nested)
            return
        except ManifestEditError:
            if depth == 1:
                raise


def set_parts(project: Project, values: dict[str, str], why: str | None = None) -> list[str]:
    """Change parts of the stack in murakumo.yaml, keeping its comments; each change is recorded
    in the decisions log. Returns what changed."""
    from murakumo.manifest_edit import ManifestEditError
    from murakumo.scaffold import configure_project

    before = current(project)
    for key, value in values.items():
        part = PART.get(key)
        if part is None:
            raise StackError(f"there is no {key} in the stack; the parts are {', '.join(PART)}")
        if part.options and value not in part.options:
            raise StackError(f"{key} is one of {', '.join(part.options)}, not {value}")
        if not value.strip():
            raise StackError(f"{key} needs a value")
    changed: list[str] = []
    root = project.root
    try:
        for key, value in values.items():
            if before[key] == value:
                continue
            if key == "warehouse":
                configure_project(root, warehouse=Warehouse(value))
            elif key == "orchestrator":
                configure_project(root, orchestrator=Orchestrator(value))
            else:
                _write(root, PART[key].path, value)
            changed.append(f"{key}: {before[key]} → {value}")
            record(root, f"stack.{key}", before[key], value, why)
    except (ValueError, ManifestEditError) as exc:
        raise StackError(str(exc)) from None
    return changed


def set_connection(project: Project, source: str, connection: dict[str, Any] | None, why: str | None = None) -> str:
    """Give a source in murakumo.yaml a connection (or take it away, with None); adds the source if
    it is not listed yet. Returns what changed."""
    from murakumo.manifest_edit import ManifestEditError, read_manifest, write_section

    data = read_manifest(project.root)
    sources = list(data.get("sources") or [])
    entry = next((s for s in sources if isinstance(s, dict) and str(s.get("name", "")).lower() == source.lower()), None)
    if entry is None:
        if connection is None:
            raise StackError(f"no source {source} in murakumo.yaml")
        entry = {"name": source}
        sources.append(entry)
    before = entry.get("connection")
    if connection is None:
        entry.pop("connection", None)
    else:
        entry["connection"] = connection
    try:
        write_section(project.root, ("sources",), sources)
    except ManifestEditError as exc:
        raise StackError(str(exc)) from None
    kind = (connection or before or {}).get("kind", "")
    what = f"source {source}: {'no connection' if connection is None else kind + ' connection'}"
    was = (before or {}).get("kind")
    if connection is not None and was == kind:
        record(project.root, f"sources.{source}.connection changed", why=why)
    else:
        record(project.root, f"sources.{source}.connection", was, kind if connection else None, why)
    return what


def parse_assignments(items: list[str]) -> dict[str, Any]:
    """``base_url=https://... auth.type=bearer endpoints.0.table=hourly`` as a nested mapping.
    Values are read as JSON when they can be (numbers, true, lists), else as text."""
    import json

    result: dict[str, Any] = {}
    for item in items:
        key, sep, raw = item.partition("=")
        if not sep or not key:
            raise StackError(f"write {item!r} as key=value")
        try:
            value: Any = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        parts = key.split(".")
        node: Any = result
        for depth, part in enumerate(parts):
            last = depth == len(parts) - 1
            nxt: Any = value if last else ([] if parts[depth + 1].isdigit() else {})
            if isinstance(node, list):
                index = int(part)
                while len(node) <= index:
                    node.append({} if not last else None)
                if last:
                    node[index] = value
                elif not isinstance(node[index], (dict, list)) or node[index] == {}:
                    node[index] = node[index] if node[index] else nxt
                node = node[index]
            else:
                if last:
                    node[part] = value
                else:
                    node = node.setdefault(part, nxt)
    return result


# The decisions log --------------------------------------------------------------------------------


def decisions(root: Path) -> list[dict[str, Any]]:
    path = root / DECISIONS_FILE
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("decisions") or [])


def record(
    root: Path, what: str, before: Any = None, after: Any = None, why: str | None = None, by: str | None = None
) -> dict[str, Any]:
    """Add a decision to the log: a setting changed, or (with no before and after) a note."""
    from murakumo.environments import developer

    entry: dict[str, Any] = {"at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "by": by or developer()}
    entry["what"] = what
    if before is not None or after is not None:
        entry["from"], entry["to"] = before, after
    if why:
        entry["why"] = why.strip()
    log = decisions(root) + [entry]
    path = root / DECISIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Why the platform is built the way it is: each choice, when it was made, by whom and why.\n"
        "# Murakumo adds an entry when the stack changes; add your own with `murakumo decisions add`.\n"
    )
    path.write_text(header + yaml.safe_dump({"decisions": log}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return entry


__all__ = [
    "GENERATED_ORCHESTRATORS",
    "PART",
    "PARTS",
    "StackError",
    "check",
    "current",
    "decisions",
    "parse_assignments",
    "record",
    "set_connection",
    "set_parts",
]
