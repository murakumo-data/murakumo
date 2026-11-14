"""Lineage for a data catalog: ``murakumo catalog export`` and ``murakumo catalog send``.

The model knows where every column comes from. This writes that down in OpenLineage's format, the
open standard that Marquez, DataHub, Atlan, Collibra, Microsoft Purview and others read: one event
per modelled table, with the tables it reads, its columns and types, and for each column the
columns it is made from.

``export`` writes the events to a file (one JSON object per line). ``send`` posts them to an
OpenLineage endpoint; its address and key are read from environment variables. What is sent is the
model's metadata: table and column names, types, descriptions. Never rows of data.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from typing import Any

from murakumo import __version__
from murakumo.generate.plan import Plan, build_plan
from murakumo.generate.targets import target_for
from murakumo.ir.model import Layer
from murakumo.lineage import build_lineage
from murakumo.project import Project

PRODUCER = f"https://github.com/murakumo-data/murakumo/tree/v{__version__}"
SCHEMA = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
FACETS = "https://openlineage.io/spec/facets"
LAYER_SCHEMAS = {"staging": "staging", "intermediate": "intermediate", "marts": "marts", "semantic": "semantic"}


class CatalogError(Exception):
    pass


def namespace(project: Project) -> str:
    """Where the tables are, as OpenLineage names a warehouse: ``snowflake://<account>``."""
    warehouse = project.manifest.targets.warehouse.type.value
    where = {
        "snowflake": os.environ.get("SNOWFLAKE_ACCOUNT"),
        "databricks": (os.environ.get("DATABRICKS_HOST") or "").removeprefix("https://") or None,
        "bigquery": None,
        "postgres": os.environ.get("DB_HOST"),
        "redshift": os.environ.get("DB_HOST"),
        "fabric": os.environ.get("FABRIC_SERVER"),
        "synapse": os.environ.get("SYNAPSE_SERVER"),
    }.get(warehouse)
    if warehouse == "bigquery":
        return "bigquery"
    return f"{warehouse}://{where or project.manifest.project.name}"


def _datasets(project: Project, plan: Plan) -> dict[str, str]:
    """Entity -> the table it is in the warehouse: schema.table."""
    names: dict[str, str] = {}
    for group in plan.sources:
        for table in group.tables:
            names[table.entity.name] = f"{group.schema}.{table.entity.name}"
    for model in plan.models:
        if model.layer == "staging" or model.entity is None:
            continue
        names.setdefault(model.entity.name, f"{LAYER_SCHEMAS.get(model.layer, model.layer)}.{model.name}")
    return names


def graph(project: Project) -> dict[str, Any]:
    """The lineage as plain JSON: tables, and each column with the columns it is made from."""
    plan = build_plan(project, target_for(project.manifest.targets.warehouse.type))
    lineage = build_lineage(project)
    datasets = _datasets(project, plan)
    tables = []
    for entity in project.model.entities:
        tables.append(
            {
                "name": entity.name,
                "dataset": datasets.get(entity.name),
                "layer": entity.layer.value,
                "kind": entity.kind.value,
                "source": entity.source,
                "domain": entity.domain,
                "description": entity.description,
                "columns": [
                    {
                        "name": a.name,
                        "type": a.type,
                        "description": a.description,
                        "pii": a.pii,
                        "classification": a.classification.value if a.classification else None,
                        "from": [f"{e}.{c}" for e, c in lineage.sources_of(entity.name, a.name)],
                    }
                    for a in entity.attributes
                ],
            }
        )
    return {"project": project.manifest.project.name, "namespace": namespace(project), "tables": tables}


def events(project: Project, at: datetime | None = None, where: str | None = None) -> list[dict[str, Any]]:
    """An OpenLineage COMPLETE event per modelled table."""
    plan = build_plan(project, target_for(project.manifest.targets.warehouse.type))
    lineage = build_lineage(project)
    datasets = _datasets(project, plan)
    space = where or namespace(project)
    when = (at or datetime.now(UTC)).isoformat()
    name = project.manifest.project.name
    out = []
    for entity in project.model.entities:
        if entity.layer is Layer.RAW or entity.name not in datasets:
            continue
        fields: dict[str, Any] = {}
        inputs: dict[str, None] = {}
        for attribute in entity.attributes:
            sources = lineage.sources_of(entity.name, attribute.name)
            known = [(e, c) for e, c in sources if e in datasets]
            for source_entity, _ in known:
                inputs.setdefault(source_entity)
            if known:
                mapping = next((m for m in entity.mappings if m.target.lower() == attribute.name.lower()), None)
                computed = bool(mapping and mapping.expression)
                fields[attribute.name] = {
                    "inputFields": [
                        {
                            "namespace": space,
                            "name": datasets[e],
                            "field": c,
                            "transformations": [
                                {
                                    "type": "DIRECT",
                                    "subtype": "TRANSFORMATION" if computed else "IDENTITY",
                                    "description": mapping.expression if computed else "",
                                    "masking": False,
                                }
                            ],
                        }
                        for e, c in known
                    ]
                }
        facet = {"_producer": PRODUCER}
        schema = {
            **facet,
            "_schemaURL": f"{FACETS}/1-1-1/SchemaDatasetFacet.json#/$defs/SchemaDatasetFacet",
            "fields": [
                {"name": a.name, "type": a.type, **({"description": a.description} if a.description else {})}
                for a in entity.attributes
            ],
        }
        output: dict[str, Any] = {"namespace": space, "name": datasets[entity.name], "facets": {"schema": schema}}
        if fields:
            output["facets"]["columnLineage"] = {
                **facet,
                "_schemaURL": f"{FACETS}/1-2-0/ColumnLineageDatasetFacet.json#/$defs/ColumnLineageDatasetFacet",
                "fields": fields,
            }
        if entity.description:
            output["facets"]["documentation"] = {
                **facet,
                "_schemaURL": f"{FACETS}/1-0-0/DocumentationDatasetFacet.json#/$defs/DocumentationDatasetFacet",
                "description": entity.description,
            }
        out.append(
            {
                "eventType": "COMPLETE",
                "eventTime": when,
                "producer": PRODUCER,
                "schemaURL": SCHEMA,
                # The same table gets the same run id each time, so sending again updates, not adds.
                "run": {"runId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"murakumo/{name}/{entity.name}"))},
                "job": {"namespace": name, "name": f"{entity.layer.value}.{entity.name}"},
                "inputs": [{"namespace": space, "name": datasets[e]} for e in inputs],
                "outputs": [output],
            }
        )
    return out


def send(found: list[dict[str, Any]], url_env: str, key_env: str | None = None) -> int:
    """Post each event to an OpenLineage endpoint (``<address>/api/v1/lineage``). Returns how many."""
    url = os.environ.get(url_env)
    if not url:
        raise CatalogError(f"set {url_env} to the catalog's OpenLineage address")
    if not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise CatalogError(f"{url_env} must be an https:// address")
    endpoint = url.rstrip("/")
    if not endpoint.endswith("/lineage"):
        endpoint += "/api/v1/lineage"
    headers = {"Content-Type": "application/json"}
    if key_env:
        key = os.environ.get(key_env)
        if not key:
            raise CatalogError(f"set {key_env} to the catalog's key")
        headers["Authorization"] = f"Bearer {key}"
    for event in found:
        request = urllib.request.Request(  # noqa: S310 - the scheme is checked above
            endpoint, data=json.dumps(event).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=30):  # noqa: S310
                pass
        except urllib.error.HTTPError as exc:
            raise CatalogError(f"the catalog answered {exc.code} for {event['job']['name']}: {exc.reason}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CatalogError(f"cannot reach the catalog: {getattr(exc, 'reason', exc)}") from None
    return len(found)


__all__ = ["CatalogError", "events", "graph", "namespace", "send"]
