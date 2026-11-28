"""A connected source's tables, offered to the model one at a time and column by column.

Nothing is added to the model until it is chosen: a source lists what it can give (every table a
Dynamics app can read; the tables a connection fetches), each table's columns come with what their
values look like (from a small sample, so a choice can be made from the data), and only the chosen
columns become the raw table. For Dynamics, only those columns are fetched too (``$select``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from murakumo import dynamics, extract
from murakumo.project import Project

SAMPLE_ROWS = 100
EXAMPLES = 3


class SourceTableError(ValueError):
    pass


def _declaration(project: Project, source: str):
    declaration = next((d for d in project.manifest.sources if d.name.lower() == source.lower()), None)
    if declaration is None:
        raise SourceTableError(f"no source {source} in murakumo.yaml")
    if declaration.connection is None:
        raise SourceTableError(f"{source} has no connection: connect it first (Sources → {source} → Connect…)")
    return declaration


def _connection(declaration) -> dict[str, Any]:
    return declaration.connection.model_dump(exclude_none=True)


def _landed(project: Project, source: str, table: str) -> Path:
    from murakumo.ingest import landing_folder

    return Path(landing_folder(project, source, table))


def available(project: Project, source: str) -> list[dict[str, Any]]:
    """The tables a source can give: for each, whether it is in the model, fetched, and has landed."""
    declaration = _declaration(project, source)
    connection = _connection(declaration)
    fetched = {e.get("table"): e for e in connection.get("endpoints", [])}

    def state(table: str) -> dict[str, Any]:
        entity = project.model.entity(table)
        folder = _landed(project, declaration.name, table)
        return {
            "in_model": entity is not None,
            "columns_in_model": len(entity.attributes) if entity is not None else 0,
            "fetched": table in fetched or table in declaration.tables(),
            "landed": folder.is_dir() and any(folder.glob("*.jsonl")),
        }

    if dynamics.is_dynamics(connection):
        try:
            found = dynamics.tables(connection)
        except extract.ExtractError as exc:
            raise SourceTableError(dynamics.explain(str(exc))) from None
        return [{**t, **state(t["table"])} for t in found]
    return [
        {"table": table, "entity_set": None, "logical_name": None, "label": table, "custom": True, **state(table)}
        for table in declaration.tables()
    ]


def _records(project: Project, declaration, table: str, entity_set: str | None) -> list[dict[str, Any]]:
    """A sample of a table's records: from Dynamics itself, else what landed, else one request."""
    connection = _connection(declaration)
    if dynamics.is_dynamics(connection):
        try:
            return dynamics.sample(connection, entity_set or table, SAMPLE_ROWS)
        except extract.ExtractError as exc:
            raise SourceTableError(dynamics.explain(str(exc))) from None
    folder = _landed(project, declaration.name, table)
    records: list[dict[str, Any]] = []
    if folder.is_dir():
        for file in sorted(folder.glob("*.jsonl"), reverse=True):
            with file.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        records.append(json.loads(line))
                    if len(records) >= SAMPLE_ROWS:
                        return records
    if records:
        return records
    endpoint = next((e for e in connection.get("endpoints", []) if e.get("table") == table), None)
    if endpoint is None:
        raise SourceTableError(f"nothing has landed for {table} yet: fetch {declaration.name} first")
    try:
        return extract.preview({**connection, "endpoints": []}, endpoint)["sample"]
    except extract.ExtractError as exc:
        raise SourceTableError(str(exc)) from None


def columns(project: Project, source: str, table: str, entity_set: str | None = None) -> dict[str, Any]:
    """Each column of a table, from a sample: its type, how many sampled rows have a value, and
    a few of its values. Which column is the id, and which says when a row changed."""
    from murakumo.importers.api import _infer
    from murakumo.importers.base import safe_name

    declaration = _declaration(project, source)
    records = _records(project, declaration, table, entity_set)
    keys: dict[str, list[Any]] = {}
    for record in records:
        for key in record:
            keys.setdefault(key, [])
    for record in records:
        for key, values in keys.items():
            values.append(record.get(key))
    entity = project.model.entity(table)
    have = {a.name for a in entity.attributes} if entity is not None else set()
    is_dynamics = dynamics.is_dynamics(_connection(declaration))
    found = []
    for key, values in keys.items():
        label_for = dynamics.labelled(key) if is_dynamics and dynamics.is_label(key) else None
        if label_for is not None and not dynamics.worth_a_label(label_for, keys.get(label_for, []), values):
            continue  # a date's or a number's label is only the value, formatted
        present = [v for v in values if v not in (None, "", [], {})]
        examples: list[str] = []
        for value in present:
            text = value if isinstance(value, str) else json.dumps(value, default=str)
            if text not in examples:
                examples.append(text[:80])
            if len(examples) >= EXAMPLES:
                break
        name = extract.column_name(key) if label_for else safe_name(key)
        found.append(
            {
                "key": key,  # as the source names it
                "name": name,  # as the raw table's column is named
                "type": _infer(values),
                "filled": len(present),
                "examples": examples,
                "in_model": name in have,
                "label_for": label_for,  # the column whose values this names: Approved for 100006
                "annotation": label_for is None and "@" in key,  # Dynamics' own bookkeeping
            }
        )
    # Each label straight after the column it names.
    order = {c["key"]: i for i, c in enumerate(found)}
    found.sort(key=lambda c: (order.get(c["label_for"], order[c["key"]]), c["label_for"] is not None))
    names = [c["key"] for c in found if not c["label_for"]]
    key = dynamics.primary_id(names, entity_set or table, None) if is_dynamics else None
    watermark = dynamics.WATERMARK if is_dynamics and dynamics.WATERMARK in names else None
    return {"table": table, "rows": len(records), "columns": found, "key": key, "watermark": watermark}


def add(
    project: Project,
    source: str,
    table: str,
    chosen: list[str],
    entity_set: str | None = None,
    logical_name: str | None = None,
) -> dict[str, Any]:
    """Make (or add to) a raw table with only the chosen columns, and fetch only those. Returns
    what was added and the connection to save (None when it does not change)."""
    from murakumo.importers.api import import_records
    from murakumo.importers.base import merge, safe_name

    if not chosen:
        raise SourceTableError("choose at least one column")
    declaration = _declaration(project, source)
    connection = _connection(declaration)
    is_dynamics = dynamics.is_dynamics(connection)
    records = _records(project, declaration, table, entity_set)
    if not records:
        raise SourceTableError(f"{table} has no rows yet to read its columns from")
    present = {k for r in records for k in r}
    wanted = [c for c in dict.fromkeys(chosen) if c in present]
    missing = [c for c in chosen if c not in present]
    if missing:
        raise SourceTableError(f"not in {table}: {', '.join(missing)}")
    if is_dynamics:  # its id and when it changed are always kept: they are how it loads
        key = dynamics.primary_id(sorted(present), entity_set or table, logical_name)
        wanted = [c for c in (key, dynamics.WATERMARK) if c and c in present and c not in wanted] + wanted
    # Named as they land: a label is its column's name and _label.
    rows = [{extract.column_name(k) if dynamics.is_label(k) else k: r.get(k) for k in wanted} for r in records]
    report = merge(project, import_records(rows, safe_name(declaration.name, "source"), table))
    entity = project.model.entity(table)
    if entity is None:
        raise SourceTableError(f"{table} could not be made")
    if is_dynamics:
        dynamics.shape(entity, entity_set or table, logical_name)
    new_connection = None
    if is_dynamics:
        # What to ask for, by the raw table's column: a label comes with its column, so asking for a
        # label is asking for the column it names.
        by_name: dict[str, str] = {}
        for k in present:
            if dynamics.is_label(k):
                by_name.setdefault(extract.column_name(k), dynamics.labelled(k))
            elif "@" not in k:
                by_name.setdefault(safe_name(k), k)
        selected = [by_name[a.name] for a in entity.attributes if a.name in by_name]
        spec = dynamics.endpoint(entity_set or table, dynamics.WATERMARK in present)
        spec["table"] = table
        spec["params"] = {"$select": ",".join(sorted(dict.fromkeys(selected)))}
        others = [e for e in connection.get("endpoints", []) if e.get("table") != table]
        new_connection = {**dynamics.with_labels(connection), "endpoints": [*others, spec]}
    elif table not in declaration.tables():
        raise SourceTableError(f"{source} does not fetch {table}")
    return {
        "table": table,
        "columns": len(entity.attributes),
        "added": report.added,
        "changed": report.changed,
        "key": next((a.name for a in entity.attributes if a.key and a.key.value == "primary"), None),
        "connection": new_connection,
    }


def fetched_columns(project: Project, table: str) -> tuple[str, dict[str, Any]] | None:
    """After columns are removed from a Dynamics table: its source and connection, fetching only the
    columns it still has. None when nothing changes."""
    entity = project.model.entity(table)
    if entity is None or not entity.source:
        return None
    declaration = next(
        (d for d in project.manifest.sources if d.name.lower() == entity.source.lower() and d.connection), None
    )
    if declaration is None:
        return None
    connection = _connection(declaration)
    if not dynamics.is_dynamics(connection):
        return None
    names = {a.name for a in entity.attributes}
    endpoints, changed = [], False
    for endpoint in connection.get("endpoints", []):
        chosen = (endpoint.get("params") or {}).get("$select")
        if endpoint.get("table") == table and chosen:
            # A column stays fetched while it, or its label, is still in the table.
            kept = [c for c in chosen.split(",") if {extract.column_name(c), extract.column_name(c) + "_label"} & names]
            if kept != chosen.split(","):
                endpoint = {**endpoint, "params": {**endpoint["params"], "$select": ",".join(kept)}}
                changed = True
        endpoints.append(endpoint)
    return (declaration.name, {**connection, "endpoints": endpoints}) if changed else None


__all__ = ["SourceTableError", "add", "available", "columns", "fetched_columns"]
