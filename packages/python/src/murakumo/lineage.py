"""Column-level lineage: which columns each column is made from, and which columns it feeds.

The model already says it: every mapping names the columns a column comes from, as sources and
in its expression. Lineage follows those links up (to the raw columns a column is made from) and
down (to every column that uses it). Relationships are not data flow, so they are kept apart, as
joins between the columns they connect.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import sqlglot

from murakumo.expressions import Check, resolve_columns
from murakumo.generate.plan import EXPRESSION_DIALECT
from murakumo.project import Project

ColumnRef = tuple[str, str]  # (entity, column), as the model names them


@dataclass
class Flow:
    """One column made, wholly or partly, from another."""

    source: ColumnRef
    target: ColumnRef
    expression: str | None = None


@dataclass
class Join:
    relationship: str
    left: ColumnRef
    right: ColumnRef


@dataclass
class Lineage:
    flows: list[Flow] = field(default_factory=list)
    joins: list[Join] = field(default_factory=list)
    layers: dict[str, str] = field(default_factory=dict)  # entity -> layer
    upstream: dict[ColumnRef, list[Flow]] = field(default_factory=lambda: defaultdict(list))
    downstream: dict[ColumnRef, list[Flow]] = field(default_factory=lambda: defaultdict(list))
    columns: dict[str, list[str]] = field(default_factory=dict)  # entity -> its columns, in order

    def sources_of(self, entity: str, column: str) -> list[ColumnRef]:
        """The columns this column is made from directly."""
        return [f.source for f in self.upstream.get(_key(entity, column), [])]


def _key(entity: str, column: str) -> ColumnRef:
    return (entity, column)


def mapping_reads(project: Project, mapping) -> list[ColumnRef]:
    """The columns a mapping reads: its sources, and the columns its expression names."""
    refs: dict[ColumnRef, None] = {}
    if mapping.expression:
        try:
            tree = sqlglot.parse_one(mapping.expression, read=EXPRESSION_DIALECT)
        except sqlglot.errors.ParseError:
            tree = None
        if tree is not None:
            for source_entity, attribute in resolve_columns(project, tree, mapping.sources, Check()).values():
                refs.setdefault((source_entity.name, attribute.name))
    for ref in mapping.sources:
        name, _, column = ref.partition(".")
        source = project.model.entity(name)
        attribute = source.attribute(column) if source else None
        if source and attribute:
            refs.setdefault((source.name, attribute.name))
    return list(refs)


def build_lineage(project: Project) -> Lineage:
    lineage = Lineage()
    for entity in project.model.entities:
        lineage.layers[entity.name] = entity.layer.value
        lineage.columns[entity.name] = [a.name for a in entity.attributes]
        for mapping in entity.mappings:
            attribute = entity.attribute(mapping.target)
            if attribute is None:
                continue
            target = (entity.name, attribute.name)
            for source in mapping_reads(project, mapping):
                flow = Flow(source, target, mapping.expression)
                lineage.flows.append(flow)
                lineage.upstream[target].append(flow)
                lineage.downstream[source].append(flow)
    for rel in project.model.relationships:
        left, right = project.model.entity(rel.from_), project.model.entity(rel.to)
        if left is None or right is None:
            continue
        for key in rel.keys:
            a, b = left.attribute(key.from_), right.attribute(key.to)
            if a and b:
                lineage.joins.append(
                    Join(rel.name or f"{left.name}->{right.name}", (left.name, a.name), (right.name, b.name))
                )
    return lineage


def _walk(lineage: Lineage, start: list[ColumnRef], up: bool) -> tuple[dict[ColumnRef, int], list[Flow]]:
    """Every column reachable from ``start``, with its distance, and the flows on the way."""
    links = lineage.upstream if up else lineage.downstream
    depth: dict[ColumnRef, int] = {ref: 0 for ref in start}
    flows: list[Flow] = []
    queue = list(start)
    while queue:
        ref = queue.pop(0)
        for flow in links.get(ref, []):
            nxt = flow.source if up else flow.target
            flows.append(flow)
            if nxt not in depth:  # models can be cyclic by mistake; each column is visited once
                depth[nxt] = depth[ref] + 1
                queue.append(nxt)
    return depth, flows


def resolve_ref(project: Project, text: str) -> tuple[str, str | None]:
    """``entity`` or ``entity.column`` as the model names them. Raises ValueError if unknown."""
    name, _, column = text.partition(".")
    entity = project.model.entity(name)
    if entity is None:
        raise ValueError(f"there is no entity {name!r}")
    if not column:
        return entity.name, None
    attribute = entity.attribute(column)
    if attribute is None:
        raise ValueError(f"{entity.name} has no column {column!r}")
    return entity.name, attribute.name


def trace(project: Project, entity: str, column: str | None = None, lineage: Lineage | None = None) -> dict[str, Any]:
    """Upstream and downstream of a column, or of every column of a table, as nodes and links."""
    lineage = lineage or build_lineage(project)
    start = [(entity, column)] if column else [(entity, c) for c in lineage.columns.get(entity, [])]
    up, up_flows = _walk(lineage, start, up=True)
    down, down_flows = _walk(lineage, start, up=False)
    nodes: dict[ColumnRef, dict[str, Any]] = {}
    for ref, d in up.items():
        nodes[ref] = {"entity": ref[0], "column": ref[1], "layer": lineage.layers.get(ref[0]), "depth": -d}
    for ref, d in down.items():
        if ref not in nodes or d:
            nodes[ref] = {"entity": ref[0], "column": ref[1], "layer": lineage.layers.get(ref[0]), "depth": d}
    seen: set[tuple] = set()
    links = []
    for flow in [*up_flows, *down_flows]:
        key = (flow.source, flow.target)
        if key in seen:
            continue
        seen.add(key)
        links.append(
            {
                "source": {"entity": flow.source[0], "column": flow.source[1]},
                "target": {"entity": flow.target[0], "column": flow.target[1]},
                "expression": flow.expression,
            }
        )
    joins = [
        {
            "relationship": j.relationship,
            "left": {"entity": j.left[0], "column": j.left[1]},
            "right": {"entity": j.right[0], "column": j.right[1]},
        }
        for j in lineage.joins
        if j.left in nodes or j.right in nodes
    ]
    return {
        "entity": entity,
        "column": column,
        "nodes": sorted(nodes.values(), key=lambda n: (n["depth"], n["entity"].lower(), n["column"].lower())),
        "links": links,
        "joins": joins,
    }


__all__ = ["Flow", "Join", "Lineage", "build_lineage", "mapping_reads", "resolve_ref", "trace"]
