"""REST APIs: read an OpenAPI spec, or infer a table from a sample JSON response.

    murakumo import openapi https://petstore3.swagger.io/api/v3/openapi.json --source petstore
    murakumo import json "https://archive-api.open-meteo.com/v1/archive?...&hourly=temperature_2m" \\
        --source open_meteo --name weather_hourly --path hourly
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from murakumo import __version__
from murakumo.diagnostics import warning
from murakumo.importers.base import SourceImport, safe_name
from murakumo.importers.database import SourceUnavailable
from murakumo.importers.typemap import json_schema_to_logical
from murakumo.ir.model import Attribute, Entity, SourceSystem, SourceType

TIMEOUT_SECONDS = 30


def fetch(location: str) -> str:
    """Text from a local file or an http(s) URL."""
    if re.match(r"^https?://", location):
        request = urllib.request.Request(location, headers={"User-Agent": f"murakumo/{__version__}"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310 - user-given URL
                return response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise SourceUnavailable(f"cannot fetch {location}: {reason}") from None
    path = Path(location)
    if not path.is_file():
        raise SourceUnavailable(f"no such file: {location}")
    return path.read_text(encoding="utf-8")


# OpenAPI ----------------------------------------------------------------------------------------


def import_openapi(location: str, source: str, only: list[str] | None = None) -> SourceImport:
    """One entity per object schema in ``components.schemas`` (or Swagger 2 ``definitions``)."""
    spec = yaml.safe_load(fetch(location))
    if not isinstance(spec, dict):
        raise ValueError(f"{location} is not an OpenAPI document")
    schemas = (spec.get("components") or {}).get("schemas") or spec.get("definitions") or {}
    info = spec.get("info") or {}
    result = SourceImport(
        source=SourceSystem(
            name=source,
            type=SourceType.API,
            description=info.get("title"),
            connection=_server(spec),
        )
    )
    wanted = {o.lower() for o in only} if only else None

    for schema_name, schema in schemas.items():
        if wanted is not None and schema_name.lower() not in wanted:
            continue
        properties, required = _flatten(schema, schemas)
        if not properties:
            continue  # enums and scalar aliases are not tables
        attributes = []
        for prop_name, prop in properties.items():
            prop = _resolve(prop, schemas)
            fields: dict[str, Any] = {
                "name": safe_name(prop_name),
                "type": json_schema_to_logical(prop),
                "nullable": prop_name not in required or bool(prop.get("nullable")),
            }
            if prop.get("description"):
                fields["description"] = prop["description"].strip()
            if prop.get("enum"):
                fields["accepted_values"] = [str(v) for v in prop["enum"] if v is not None]
            if prop.get("example") is not None:
                fields["example"] = str(prop["example"])
            if prop.get("format"):
                fields["source_type"] = f"{prop.get('type', 'object')}:{prop['format']}"
            elif prop.get("type"):
                fields["source_type"] = str(prop["type"])
            attributes.append(Attribute(**fields))
        entity: dict[str, Any] = {"name": safe_name(schema_name, "table"), "source": source, "attributes": attributes}
        if schema.get("description"):
            entity["description"] = schema["description"].strip()
        result.entities.append(Entity(**entity))

    if not result.entities:
        result.diagnostics.append(warning("MK404", f"no object schemas found in {location}"))
    return result


def _server(spec: dict) -> str | None:
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict):
        return servers[0].get("url")
    if spec.get("host"):
        return f"https://{spec['host']}{spec.get('basePath', '')}"
    return None


def _resolve(schema: dict, schemas: dict, depth: int = 0) -> dict:
    """Follow a local ``$ref``. A reference to another object becomes a JSON column."""
    ref = schema.get("$ref")
    if not ref or depth > 10:
        return schema
    target = schemas.get(ref.rsplit("/", 1)[-1], {})
    resolved = _resolve(target, schemas, depth + 1)
    if resolved.get("type") == "object" or "properties" in resolved:
        return {"type": "object", "description": schema.get("description") or resolved.get("description")}
    return resolved


def _flatten(schema: dict, schemas: dict) -> tuple[dict[str, dict], set[str]]:
    """Properties and required names, merging ``allOf`` parts."""
    schema = schema if "$ref" not in schema else schemas.get(schema["$ref"].rsplit("/", 1)[-1], {})
    properties = dict(schema.get("properties") or {})
    required = set(schema.get("required") or [])
    for part in schema.get("allOf") or []:
        more, more_required = _flatten(part, schemas)
        properties.update(more)
        required |= more_required
    return properties, required


# JSON samples -----------------------------------------------------------------------------------


def records_at(data: Any, path: str | None) -> list[dict]:
    """The rows of a JSON response: a list of objects, one object, or columnar arrays.

    ``path`` is a dotted path to the rows, such as ``data.items`` or ``hourly``.
    """
    for part in [p for p in (path or "").split(".") if p]:
        if not isinstance(data, dict) or part not in data:
            raise ValueError(f"no {part!r} in the response at path {path!r}")
        data = data[part]
    if isinstance(data, list):
        return [r if isinstance(r, dict) else {"value": r} for r in data]
    if isinstance(data, dict):
        values = list(data.values())
        if values and all(isinstance(v, list) for v in values) and len({len(v) for v in values}) == 1:
            # Columnar: {"time": [...], "temperature_2m": [...]} -> one row per index.
            return [dict(zip(data, row, strict=True)) for row in zip(*values, strict=True)]
        return [data]
    raise ValueError("the response holds no objects; use --path to point at the rows")


def _infer(values: list[Any]) -> str:
    present = [v for v in values if v is not None]
    if not present:
        return "string"
    kinds = {_kind(v) for v in present}
    if kinds <= {"bigint"}:
        return "bigint"
    if kinds <= {"bigint", "double"}:
        return "double"
    if len(kinds) == 1:
        kind = kinds.pop()
        if kind == "array":
            elements = [e for v in present for e in v]
            element = _infer(elements) if elements else "string"
            return f"array<{element if not element.startswith('array') else 'json'}>"
        return kind
    if kinds <= {"timestamp", "date"}:
        return "timestamp"
    return "string" if kinds <= {"string", "date", "timestamp", "uuid"} else "json"


_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "bigint"
    if isinstance(value, float):
        return "double"
    if isinstance(value, dict):
        return "json"
    if isinstance(value, list):
        return "array"
    text = str(value)
    if _UUID.match(text):
        return "uuid"
    try:
        date.fromisoformat(text)
        return "date"
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return "timestamp_tz" if parsed.tzinfo else "timestamp"
    except ValueError:
        return "string"


def import_json(location: str, source: str, name: str, path: str | None = None) -> SourceImport:
    """Infer one entity from a sample JSON response or file."""
    try:
        data = json.loads(fetch(location))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{location} is not JSON: {exc.msg} at line {exc.lineno}") from None
    rows = records_at(data, path)
    columns: dict[str, list[Any]] = {}
    for row in rows:
        for key in row:
            columns.setdefault(key, [])
    for row in rows:
        for key in columns:
            columns[key].append(row.get(key))

    attributes = [
        Attribute(
            name=safe_name(key),
            type=_infer(values),
            nullable=any(v is None for v in values),
            example=None if values[0] is None else str(values[0])[:100],
        )
        for key, values in columns.items()
    ]
    result = SourceImport(source=SourceSystem(name=source, type=SourceType.API), partial=True)
    properties: dict[str, Any] = {"sample": location.split("?", 1)[0]}
    if path:
        properties["records_path"] = path
    result.entities.append(
        Entity(name=safe_name(name, "table"), source=source, attributes=attributes, properties=properties)
    )
    if len(rows) < 20:
        result.diagnostics.append(
            warning("MK404", f"types inferred from {len(rows)} row(s) only; check them, or use a larger sample")
        )
    return result
