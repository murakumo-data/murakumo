"""``murakumo mcp``: the project as tools for an agent, over the Model Context Protocol.

An agent of your own (Claude Code, Claude Desktop, anything that speaks MCP) can read the model,
check it, trace lineage and change it, through the same edits as the CLI and the canvas: every
change is validated and saved to the YAML files, and a change the CLI would refuse is refused here
with the same message. Nothing here calls a language model: the agent brings its own.

The protocol is JSON-RPC 2.0, one message per line, on stdin and stdout (the ``stdio`` transport).
Add it to an agent with a command such as ``murakumo mcp -p /path/to/project``.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO

from murakumo import __version__, edit
from murakumo.diagnostics import Level
from murakumo.ir.model import EntityKind, Layer
from murakumo.project import Project, load_project, save_model
from murakumo.validate import validate_project

PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


class ToolError(Exception):
    pass


def _open(root: Path) -> Project:
    result = load_project(root)
    blocking = [d for d in result.diagnostics if d.level is Level.ERROR]
    if result.project is None or blocking:
        raise ToolError("the project does not load: " + "; ".join(f"{d.code} {d.message}" for d in blocking[:5]))
    return result.project


def _problems(project: Project) -> list[dict]:
    out = []
    for d in validate_project(project):
        file = str(d.file.relative_to(project.root)) if d.file and d.file.is_relative_to(project.root) else None
        out.append({"level": d.level.value, "code": d.code, "message": d.message, "file": file})
    return out


def _saved(project: Project, done: str) -> dict:
    """Save a change and say what the model now needs."""
    save_model(project)
    problems = _problems(project)
    errors = sum(p["level"] == "error" for p in problems)
    return {"done": done, "errors": errors, "warnings": len(problems) - errors, "problems": problems[:30]}


def _entity(project: Project, name: str) -> dict:
    entity = project.model.entity(name)
    if entity is None:
        raise ToolError(f"entity {name!r} does not exist")
    data = entity.model_dump(mode="json", exclude_none=True, exclude_defaults=True, by_alias=True)
    data.pop("id", None)
    for column in data.get("attributes", []):
        column.pop("id", None)
    return data


# Tools ----------------------------------------------------------------------------------------


def model_summary(root: Path, arguments: dict) -> Any:
    project = _open(root)
    manifest = project.manifest
    return {
        "project": manifest.project.name,
        "warehouse": manifest.targets.warehouse.type.value,
        "orchestrator": manifest.generation.orchestrator.value,
        "domains": [d.name for d in manifest.project.domains],
        "sources": [s.name for s in project.model.sources],
        "entities": [
            {
                "name": e.name,
                "layer": e.layer.value,
                "kind": e.kind.value,
                "source": e.source,
                "domain": e.domain,
                "columns": len(e.attributes),
                "description": e.description,
            }
            for e in project.model.entities
        ],
        "relationships": [
            {
                "from": r.from_,
                "to": r.to,
                "on": [f"{k.from_}={k.to}" for k in r.keys],
                "cardinality": r.cardinality.value,
            }
            for r in project.model.relationships
        ],
    }


def get_entity(root: Path, arguments: dict) -> Any:
    return _entity(_open(root), arguments["name"])


def validate(root: Path, arguments: dict) -> Any:
    problems = _problems(_open(root))
    errors = sum(p["level"] == "error" for p in problems)
    return {"errors": errors, "warnings": len(problems) - errors, "problems": problems}


def lineage(root: Path, arguments: dict) -> Any:
    from murakumo.lineage import trace

    project = _open(root)
    try:
        return trace(project, arguments["entity"], arguments.get("column"))
    except (KeyError, ValueError) as exc:
        raise ToolError(str(exc)) from None


def add_entity(root: Path, arguments: dict) -> Any:
    project = _open(root)
    entity = edit.add_entity(
        project,
        arguments["name"],
        Layer(arguments.get("layer", "raw")),
        EntityKind(arguments.get("kind", "table")),
        arguments.get("source"),
        arguments.get("domain"),
        arguments.get("description"),
    )
    for column in arguments.get("columns", []):
        edit.add_column(
            project,
            entity.name,
            column["name"],
            column["type"],
            bool(column.get("primary_key")),
            None,
            column.get("description"),
            bool(column.get("pii")),
        )
    return _saved(project, f"added {entity.name} with {len(entity.attributes)} column(s)")


def add_column(root: Path, arguments: dict) -> Any:
    project = _open(root)
    column = edit.add_column(
        project,
        arguments["entity"],
        arguments["name"],
        arguments["type"],
        bool(arguments.get("primary_key")),
        None,
        arguments.get("description"),
        bool(arguments.get("pii")),
    )
    return _saved(project, f"added {arguments['entity']}.{column.name}")


def update_entity(root: Path, arguments: dict) -> Any:
    project = _open(root)
    entity = edit.update_entity(project, arguments["name"], arguments["changes"])
    return _saved(project, f"changed {entity.name}")


def update_column(root: Path, arguments: dict) -> Any:
    project = _open(root)
    column = edit.update_column(project, arguments["entity"], arguments["column"], arguments["changes"])
    return _saved(project, f"changed {arguments['entity']}.{column.name}")


def set_mapping(root: Path, arguments: dict) -> Any:
    project = _open(root)
    expression = arguments.get("expression")
    if expression:
        from murakumo.expressions import check_expression

        check = check_expression(
            project, expression, sources=arguments.get("sources") or [], target=arguments["entity"]
        )
        if check.error or check.problems:
            raise ToolError(check.error or "; ".join(check.problems))
    mapping = edit.set_mapping(
        project, arguments["entity"], arguments["column"], arguments.get("sources") or [], expression
    )
    done = "removed the mapping of" if mapping is None else "mapped"
    return _saved(project, f"{done} {arguments['entity']}.{arguments['column']}")


def add_relationship(root: Path, arguments: dict) -> Any:
    project = _open(root)
    keys = [edit.parse_join(pair) for pair in arguments["on"]]
    relationship = edit.add_relationship(project, arguments["from"], arguments["to"], keys)
    return _saved(project, f"added the relationship {relationship.from_} → {relationship.to}")


def generate(root: Path, arguments: dict) -> Any:
    from murakumo.generate import compare, write
    from murakumo.generate import generate as build

    project = _open(root)
    errors = [p for p in _problems(project) if p["level"] == "error"]
    if errors:
        raise ToolError("the model has errors; fix them first: " + "; ".join(p["message"] for p in errors[:5]))
    output = build(project)
    changes = compare(output, root) if arguments.get("dry_run") else write(output, root)
    return {
        "written": not arguments.get("dry_run"),
        "files": len(output.files),
        "added": sorted(changes.added)[:50],
        "changed": sorted(changes.changed)[:50],
        "removed": sorted(changes.removed)[:50],
    }


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


S = {"type": "string"}
COLUMN = _schema(
    {"name": S, "type": S, "primary_key": {"type": "boolean"}, "description": S, "pii": {"type": "boolean"}},
    ["name", "type"],
)

TOOLS: dict[str, tuple[str, dict, Callable[[Path, dict], Any]]] = {
    "model_summary": (
        "The project's tables (name, layer, kind, source, domain), relationships, warehouse and orchestrator. "
        "Start here.",
        _schema({}, []),
        model_summary,
    ),
    "get_entity": (
        "One table in full: its columns with types, keys, descriptions and personal-data marks, and how each "
        "column is derived (mappings).",
        _schema({"name": S}, ["name"]),
        get_entity,
    ),
    "validate": (
        "Check the model. Returns each problem with its code (MK...), message and file. Run it after changes.",
        _schema({}, []),
        validate,
    ),
    "lineage": (
        "Where a table or one of its columns comes from, and what is built from it.",
        _schema({"entity": S, "column": S}, ["entity"]),
        lineage,
    ),
    "add_entity": (
        "Add a table, optionally with its columns. layer: raw, staging, intermediate, marts, semantic. "
        "kind: table, fact, dimension, bridge and others. A raw table needs a source. Types are logical: "
        "string, integer, bigint, decimal(p,s), money, boolean, date, timestamp.",
        _schema(
            {
                "name": S,
                "layer": S,
                "kind": S,
                "source": S,
                "domain": S,
                "description": S,
                "columns": {"type": "array", "items": COLUMN},
            },
            ["name"],
        ),
        add_entity,
    ),
    "add_column": (
        "Add a column to a table.",
        _schema({"entity": S, **COLUMN["properties"]}, ["entity", "name", "type"]),
        add_column,
    ),
    "update_entity": (
        "Change a table's settings. changes may set: " + ", ".join(sorted(edit.ENTITY_FIELDS)) + ".",
        _schema({"name": S, "changes": {"type": "object"}}, ["name", "changes"]),
        update_entity,
    ),
    "update_column": (
        "Change a column's settings. changes may set: " + ", ".join(sorted(edit.COLUMN_FIELDS)) + ".",
        _schema({"entity": S, "column": S, "changes": {"type": "object"}}, ["entity", "column", "changes"]),
        update_column,
    ),
    "set_mapping": (
        "Say how a column of a modelled table is derived: from `sources` (each written table.column), an "
        "`expression` (SQL in DuckDB's dialect, columns written table.column), or both. Neither removes it.",
        _schema(
            {"entity": S, "column": S, "sources": {"type": "array", "items": S}, "expression": S},
            ["entity", "column"],
        ),
        set_mapping,
    ),
    "add_relationship": (
        "Add a relationship from the table holding the foreign key to the table it points at. "
        "`on` lists the joins, each written from_column=to_column.",
        _schema({"from": S, "to": S, "on": {"type": "array", "items": S}}, ["from", "to", "on"]),
        add_relationship,
    ),
    "generate": (
        "Write the generated code (dbt, DDL, ingestion, orchestration, the replication guide) under generated/. "
        "With dry_run, only say what would change.",
        _schema({"dry_run": {"type": "boolean"}}, []),
        generate,
    ),
}


# The protocol ---------------------------------------------------------------------------------


def handle(root: Path, message: dict) -> dict | None:
    """Answer one JSON-RPC message; None for a notification."""
    method, identifier = message.get("method"), message.get("id")
    if identifier is None:
        return None  # a notification (notifications/initialized, cancellations): nothing to answer

    def result(value: Any) -> dict:
        return {"jsonrpc": "2.0", "id": identifier, "result": value}

    def failure(code: int, text: str) -> dict:
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": text}}

    if method == "initialize":
        asked = (message.get("params") or {}).get("protocolVersion")
        return result(
            {
                "protocolVersion": asked if asked in SUPPORTED_PROTOCOLS else PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "murakumo", "version": __version__},
                "instructions": "A Murakumo data platform project. Read the model with model_summary and "
                "get_entity, change it with the add_, update_ and set_ tools, and run validate after changes.",
            }
        )
    if method == "ping":
        return result({})
    if method == "tools/list":
        return result(
            {
                "tools": [
                    {"name": name, "description": description, "inputSchema": schema}
                    for name, (description, schema, _) in TOOLS.items()
                ]
            }
        )
    if method == "tools/call":
        params = message.get("params") or {}
        tool = TOOLS.get(params.get("name"))
        if tool is None:
            return failure(-32602, f"no tool {params.get('name')!r}")
        try:
            value = tool[2](root, params.get("arguments") or {})
            text, failed = json.dumps(value, indent=2, default=str), False
        except (ToolError, edit.EditError) as exc:
            text, failed = str(exc), True
        except (KeyError, ValueError, TypeError) as exc:  # a missing or mistyped argument
            text, failed = f"cannot run {params.get('name')}: {exc}", True
        return result({"content": [{"type": "text", "text": text}], "isError": failed})
    return failure(-32601, f"no method {method!r}")


def serve(root: Path, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            answer: dict | None = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "not JSON"}}
        else:
            answer = handle(root, message) if isinstance(message, dict) else None
        if answer is not None:
            stdout.write(json.dumps(answer) + "\n")
            stdout.flush()


__all__ = ["TOOLS", "handle", "serve"]
