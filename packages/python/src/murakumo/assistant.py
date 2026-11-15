"""The assistant: suggestions for the model from a language model, when the project turns it on.

Off unless murakumo.yaml says ``assistant: {enabled: true}``. It uses the project's own account
with the model provider (the key is read from an environment variable, never written down), and
sends the model only: table and column names, types and descriptions. It never sends rows of data.

It suggests; it does not decide. Every suggestion is a change the CLI or the canvas could make by
hand, shown before it is applied, applied through the same edits (``edit.py``), and refused when
the edit would be refused: a mapping whose expression does not parse, a column that does not exist.
What it can suggest:

- ``describe``: descriptions for the tables and columns that have none;
- ``classify``: which columns hold personal data, and how sensitive each is;
- ``map``: where each unmapped column of a modelled table comes from;
- ``draft``: new tables, from a sentence or two about what is needed.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from murakumo import edit
from murakumo.ir.model import Classification, Entity, EntityKind, Layer
from murakumo.project import Project

DEFAULT_MODEL = "claude-opus-5-5"
SYSTEM = (
    "You help a data engineer with a data platform's logical model: source (raw) tables, staging, "
    "and modelled tables such as facts and dimensions. You are given the model as text: tables, "
    "columns, types, keys and descriptions. You never see rows of data.\n\n"
    "Work only from what the model shows. When a name is ambiguous and nothing else settles it, "
    "leave that item out rather than guess: a missing suggestion costs the engineer nothing, a "
    "wrong one costs a review. Descriptions say what the thing is in the business's terms, in one "
    "plain sentence, without repeating the name or the type. Give a short reason for each "
    "suggestion, naming what in the model led you to it."
)


class AssistantError(Exception):
    pass


class Provider(Protocol):
    def complete(self, system: str, prompt: str, schema: dict) -> dict: ...


@dataclass
class Suggestion:
    kind: str  # describe_table | describe_column | classify | mapping | table
    entity: str
    column: str | None = None
    value: Any = None
    reason: str = ""
    refused: str | None = None  # why it was not applied

    def as_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        where = f"{self.entity}.{self.column}" if self.column else self.entity
        if self.kind == "mapping":
            what = self.value.get("expression") or ", ".join(self.value.get("sources", []))
            return f"{where} ← {what}"
        if self.kind == "classify":
            marks = ["pii"] if self.value.get("pii") else []
            marks += [self.value["classification"]] if self.value.get("classification") else []
            return f"{where}: {', '.join(marks) or 'not personal'}"
        if self.kind == "table":
            columns = ", ".join(c["name"] for c in self.value.get("columns", []))
            return f"{where} ({self.value.get('layer')}, {self.value.get('kind')}): {columns}"
        return f"{where}: {self.value}"


# Settings -------------------------------------------------------------------------------------


def require(project: Project):
    """The assistant's settings, or an error that says how to turn it on."""
    settings = project.manifest.assistant
    if not settings.enabled:
        raise AssistantError(
            "the assistant is off for this project. Turn it on with `murakumo ai enable` (or "
            "assistant: {enabled: true} in murakumo.yaml); it uses your own key with the model provider"
        )
    return settings


def provider_for(project: Project) -> Provider:
    settings = require(project)
    if settings.provider != "anthropic":
        raise AssistantError(f"no provider {settings.provider!r}: this version of Murakumo has `anthropic`")
    return AnthropicProvider(settings.model, settings.key_env, settings.effort)


class AnthropicProvider:
    """Claude, through Anthropic's SDK (``pip install 'murakumo[ai]'``)."""

    def __init__(self, model: str = DEFAULT_MODEL, key_env: str = "ANTHROPIC_API_KEY", effort: str | None = None):
        self.model = model
        self.key_env = key_env
        self.effort = effort

    def complete(self, system: str, prompt: str, schema: dict) -> dict:
        try:
            import anthropic
        except ImportError:
            raise AssistantError("the assistant needs Anthropic's SDK: pip install 'murakumo[ai]'") from None
        key = os.environ.get(self.key_env) if self.key_env else None
        # With no key in the variable, the SDK looks for its own credentials (`ant auth login`).
        client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        output: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output["effort"] = self.effort
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": output,
        }
        try:
            try:
                # If the model declines a request, let the API pass it to a fallback model.
                response = client.beta.messages.create(
                    **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
                )
            except anthropic.BadRequestError:
                response = client.messages.create(**request)  # a model or platform without fallbacks
        except anthropic.AuthenticationError:
            raise AssistantError(
                f"the model provider refused the key: set {self.key_env} to an Anthropic API key"
            ) from None
        except anthropic.RateLimitError:
            raise AssistantError("the model provider is rate limiting this key: try again shortly") from None
        except anthropic.APIStatusError as exc:
            raise AssistantError(f"the model provider answered {exc.status_code}: {exc.message}") from None
        except anthropic.APIConnectionError:
            raise AssistantError("cannot reach the model provider: check the network") from None
        if response.stop_reason == "refusal":
            raise AssistantError("the model declined this request")
        if response.stop_reason == "max_tokens":
            raise AssistantError("the answer was cut off: ask for fewer tables at once (--entity)")
        text = next((block.text for block in response.content if block.type == "text"), None)
        if text is None:
            raise AssistantError("the model returned no answer")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise AssistantError("the model's answer was not the JSON asked for") from None


# What the model is shown ----------------------------------------------------------------------


def _table(entity: Entity, mappings: bool = False) -> str:
    head = f"{entity.name} [{entity.layer.value}, {entity.kind.value}"
    head += f", source {entity.source}" if entity.source else ""
    head += f", domain {entity.domain}" if entity.domain else ""
    head += "]" + (f": {entity.description}" if entity.description else "")
    mapped = {m.target.lower(): m for m in entity.mappings}
    lines = [head]
    for a in entity.attributes:
        marks = [a.type]
        if a.key:
            marks.append(f"{a.key.value} key")
        if a.pii:
            marks.append("pii")
        if a.classification:
            marks.append(a.classification.value)
        line = f"  - {a.name} ({', '.join(marks)})" + (f": {a.description}" if a.description else "")
        mapping = mapped.get(a.name.lower())
        if mappings and mapping is not None:
            line += f"  <- {mapping.expression or ', '.join(mapping.sources)}"
        lines.append(line)
    return "\n".join(lines)


def describe_model(project: Project, entities: list[Entity] | None = None, mappings: bool = False) -> str:
    """The model as text: what is sent to the provider. Names, types and descriptions only."""
    chosen = entities if entities is not None else project.model.entities
    parts = [_table(e, mappings) for e in chosen]
    related = {e.name.lower() for e in chosen}
    joins = [
        f"{r.from_}({', '.join(k.from_ for k in r.keys)}) -> {r.to}({', '.join(k.to for k in r.keys)})"
        for r in project.model.relationships
        if r.from_.lower() in related or r.to.lower() in related
    ]
    if joins:
        parts.append("Relationships:\n" + "\n".join(f"  - {j}" for j in joins))
    return "\n\n".join(parts)


def _chosen(project: Project, only: list[str] | None) -> list[Entity]:
    if not only:
        return list(project.model.entities)
    found = []
    for name in only:
        entity = project.model.entity(name)
        if entity is None:
            raise AssistantError(f"entity {name!r} does not exist")
        found.append(entity)
    return found


def _object(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else list(properties),
        "additionalProperties": False,
    }


def _items(name: str, properties: dict) -> dict:
    return _object({name: {"type": "array", "items": _object(properties)}})


STRING = {"type": "string"}


# Suggestions ----------------------------------------------------------------------------------


def describe(project: Project, provider: Provider, only: list[str] | None = None) -> list[Suggestion]:
    """Descriptions for the tables and columns that have none."""
    entities = _chosen(project, only)
    wanted = [e for e in entities if not e.description or any(not a.description for a in e.attributes)]
    if not wanted:
        return []
    missing = []
    for e in wanted:
        if not e.description:
            missing.append(f"- {e.name} (the table)")
        missing += [f"- {e.name}.{a.name}" for a in e.attributes if not a.description]
    answer = provider.complete(
        SYSTEM,
        "Write a description for each item listed under MISSING. Use the rest of the model for context.\n\n"
        f"MODEL\n{describe_model(project, wanted)}\n\nMISSING\n" + "\n".join(missing),
        _items(
            "descriptions",
            {
                "entity": STRING,
                "column": {"type": ["string", "null"], "description": "null for the table itself"},
                "description": STRING,
                "reason": STRING,
            },
        ),
    )
    out = []
    for item in answer.get("descriptions", []):
        entity = project.model.entity(item["entity"])
        column = item.get("column")
        if entity is None or not item.get("description"):
            continue
        if column:
            attribute = entity.attribute(column)
            if attribute is None or attribute.description:
                continue
            out.append(Suggestion("describe_column", entity.name, attribute.name, item["description"], item["reason"]))
        elif not entity.description:
            out.append(Suggestion("describe_table", entity.name, None, item["description"], item["reason"]))
    return out


def classify(project: Project, provider: Provider, only: list[str] | None = None) -> list[Suggestion]:
    """Which columns hold personal data, and how sensitive each is. Columns already marked are left alone."""
    entities = [e for e in _chosen(project, only) if e.layer is Layer.RAW or only]
    open_columns = [(e, a) for e in entities for a in e.attributes if not a.pii and a.classification is None]
    if not open_columns:
        return []
    levels = [c.value for c in Classification]
    answer = provider.complete(
        SYSTEM,
        "For the columns that hold personal data (anything about an identifiable person: names, contact "
        "details, identifiers, addresses, dates of birth, account or device numbers), say so and give a "
        f"classification from {', '.join(levels)}. Personal data is confidential or restricted; restricted "
        "is for what would harm a person if leaked (government identifiers, health, payment details). "
        "List only the columns that hold personal data or need a classification above internal.\n\n"
        f"MODEL\n{describe_model(project, entities)}",
        _items(
            "columns",
            {
                "entity": STRING,
                "column": STRING,
                "pii": {"type": "boolean"},
                "classification": {"type": "string", "enum": levels},
                "reason": STRING,
            },
        ),
    )
    out = []
    for item in answer.get("columns", []):
        entity = project.model.entity(item["entity"])
        attribute = entity.attribute(item["column"]) if entity else None
        if attribute is None or attribute.pii or attribute.classification is not None:
            continue
        value = {"pii": bool(item["pii"]), "classification": item["classification"]}
        out.append(Suggestion("classify", entity.name, attribute.name, value, item["reason"]))
    return out


def map_columns(project: Project, provider: Provider, entity_name: str) -> list[Suggestion]:
    """Where each unmapped column of a modelled table comes from."""
    entity = project.model.entity(entity_name)
    if entity is None:
        raise AssistantError(f"entity {entity_name!r} does not exist")
    if entity.layer is Layer.RAW:
        raise AssistantError(f"{entity.name} is a raw table: its columns come from the source")
    mapped = {m.target.lower() for m in entity.mappings}
    unmapped = [a for a in entity.attributes if a.name.lower() not in mapped]
    if not unmapped:
        return []
    upstream = [e for e in project.model.entities if e is not entity and e.layer is not Layer.SEMANTIC]
    answer = provider.complete(
        SYSTEM,
        f"Say where each UNMAPPED column of {entity.name} comes from. `sources` lists the columns it reads, "
        "each written table.column, from the tables in the model. Give `expression` (SQL, DuckDB dialect, "
        "columns written table.column) only when the value is computed or converted; leave it null for a "
        "straight copy. Prefer the tables its mapped columns already read. Leave out a column you cannot "
        "place.\n\n"
        f"TARGET\n{_table(entity, mappings=True)}\n\nUNMAPPED\n"
        + "\n".join(f"- {a.name} ({a.type})" for a in unmapped)
        + f"\n\nMODEL\n{describe_model(project, upstream)}",
        _items(
            "mappings",
            {
                "column": STRING,
                "sources": {"type": "array", "items": STRING},
                "expression": {"type": ["string", "null"]},
                "reason": STRING,
            },
        ),
    )
    names = {a.name.lower(): a.name for a in unmapped}
    out = []
    for item in answer.get("mappings", []):
        column = names.get(item["column"].lower())
        if column is None or not (item.get("sources") or item.get("expression")):
            continue
        value = {"sources": item.get("sources") or [], "expression": item.get("expression") or None}
        out.append(Suggestion("mapping", entity.name, column, value, item["reason"]))
    return out


def draft(project: Project, provider: Provider, request: str) -> list[Suggestion]:
    """New tables, from a sentence or two about what is needed."""
    if not request.strip():
        raise AssistantError("say what tables are needed")
    domains = [d.name for d in project.manifest.project.domains]
    layers = [layer.value for layer in Layer if layer not in (Layer.RAW, Layer.STAGING)]
    kinds = [k.value for k in (EntityKind.TABLE, EntityKind.FACT, EntityKind.DIMENSION, EntityKind.BRIDGE)]
    answer = provider.complete(
        SYSTEM,
        "Design the modelled tables the request asks for, built from the tables already in the model. "
        "Follow dimensional modelling: a fact has a grain (the columns that make a row unique) and keys to "
        "its dimensions; a dimension has one primary key. Names are snake_case; facts start fct_, dimensions "
        "dim_. Types are logical: string, integer, bigint, decimal(p,s), money, boolean, date, timestamp. "
        "For each column give `sources` (table.column in the existing model) when it comes from one. "
        "Do not repeat a table that already exists."
        + (f" Domains to choose from: {', '.join(domains)}." if domains else "")
        + f"\n\nREQUEST\n{request.strip()}\n\nMODEL\n{describe_model(project)}",
        _items(
            "tables",
            {
                "name": STRING,
                "layer": {"type": "string", "enum": layers},
                "kind": {"type": "string", "enum": kinds},
                "domain": {"type": ["string", "null"]},
                "description": STRING,
                "grain": {"type": "array", "items": STRING},
                "columns": {
                    "type": "array",
                    "items": _object(
                        {
                            "name": STRING,
                            "type": STRING,
                            "primary_key": {"type": "boolean"},
                            "description": STRING,
                            "sources": {"type": "array", "items": STRING},
                            "expression": {"type": ["string", "null"]},
                        }
                    ),
                },
                "reason": STRING,
            },
        ),
    )
    out = []
    for item in answer.get("tables", []):
        if project.model.entity(item["name"]) or not item.get("columns"):
            continue
        value = {k: v for k, v in item.items() if k not in ("name", "reason")}
        out.append(Suggestion("table", item["name"], None, value, item["reason"]))
    return out


# Applying -------------------------------------------------------------------------------------


@dataclass
class Applied:
    applied: list[Suggestion] = field(default_factory=list)
    refused: list[Suggestion] = field(default_factory=list)


def _apply_table(project: Project, s: Suggestion) -> None:
    value = s.value
    domain = value.get("domain")
    if domain and project.manifest.domain(domain) is None:
        domain = None
    edit.add_entity(
        project, s.entity, Layer(value["layer"]), EntityKind(value["kind"]), None, domain, value["description"]
    )
    try:
        for column in value["columns"]:
            edit.add_column(
                project,
                s.entity,
                column["name"],
                column["type"],
                bool(column.get("primary_key")),
                None,
                column.get("description") or None,
            )
        names = {c["name"].lower() for c in value["columns"]}
        grain = [g for g in value.get("grain", []) if g.lower() in names]
        if grain:
            edit.update_entity(project, s.entity, {"grain": grain})
        for column in value["columns"]:
            if column.get("sources") or column.get("expression"):
                try:
                    _check_mapping(project, s.entity, column.get("sources") or [], column.get("expression"))
                    edit.set_mapping(
                        project, s.entity, column["name"], column.get("sources") or [], column.get("expression")
                    )
                except edit.EditError:
                    pass  # the column is kept, unmapped: validate reports it, and `ai map` can try again
    except edit.EditError:
        edit.remove_entity(project, s.entity)  # all of a table or none of it
        raise


def _check_mapping(project: Project, entity: str, sources: list[str], expression: str | None) -> None:
    if not expression:
        return
    from murakumo.expressions import check_expression

    check = check_expression(project, expression, sources=sources, target=entity)
    if check.error:
        raise edit.EditError(f"cannot read the expression: {check.error}")
    if check.problems:
        raise edit.EditError("; ".join(check.problems))


def apply(project: Project, suggestions: list[Suggestion]) -> Applied:
    """Make each suggestion's change in the model (not yet saved). One that the model would not
    accept by hand is refused, with the reason, and the rest still apply."""
    result = Applied()
    for s in suggestions:
        try:
            if s.kind == "describe_table":
                edit.update_entity(project, s.entity, {"description": s.value})
            elif s.kind == "describe_column":
                edit.update_column(project, s.entity, s.column, {"description": s.value})
            elif s.kind == "classify":
                edit.update_column(project, s.entity, s.column, s.value)
            elif s.kind == "mapping":
                _check_mapping(project, s.entity, s.value["sources"], s.value["expression"])
                edit.set_mapping(project, s.entity, s.column, s.value["sources"], s.value["expression"])
            elif s.kind == "table":
                _apply_table(project, s)
            else:
                raise edit.EditError(f"no suggestion of kind {s.kind!r}")
        except (edit.EditError, ValueError, KeyError, TypeError) as exc:
            s.refused = " ".join(str(exc).split())[:300]
            result.refused.append(s)
        else:
            result.applied.append(s)
    return result


def suggest(project: Project, kind: str, provider: Provider | None = None, **options: Any) -> list[Suggestion]:
    """The one entry point the CLI, the canvas and the MCP server share."""
    provider = provider or provider_for(project)
    if kind == "describe":
        return describe(project, provider, options.get("entities"))
    if kind == "classify":
        return classify(project, provider, options.get("entities"))
    if kind == "map":
        return map_columns(project, provider, options.get("entity") or "")
    if kind == "draft":
        return draft(project, provider, options.get("request") or "")
    raise AssistantError(f"no suggestion of kind {kind!r}: describe, classify, map or draft")


def from_dict(data: dict) -> Suggestion:
    return Suggestion(
        kind=str(data.get("kind", "")),
        entity=str(data.get("entity", "")),
        column=data.get("column"),
        value=data.get("value"),
        reason=str(data.get("reason", "")),
    )


__all__ = [
    "AnthropicProvider",
    "Applied",
    "AssistantError",
    "Provider",
    "Suggestion",
    "apply",
    "describe_model",
    "from_dict",
    "provider_for",
    "require",
    "suggest",
]
