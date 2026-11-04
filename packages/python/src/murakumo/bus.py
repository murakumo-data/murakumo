"""The bus matrix and conformed dimensions: which facts share which dimensions.

Kimball's bus matrix has a row per business process (a fact) and a column per dimension, with a
mark where the fact uses the dimension. A dimension every domain shares is **conformed**: one
table, used by every fact that needs it, instead of a copy per domain that drifts apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from murakumo.ir.model import Entity, EntityKind, Layer, Model

FACT_KINDS = (EntityKind.FACT, EntityKind.ACCUMULATING, EntityKind.EVENT)


@dataclass
class BusMatrix:
    dimensions: list[Entity] = field(default_factory=list)
    facts: list[Entity] = field(default_factory=list)
    uses: dict[str, list[str]] = field(default_factory=dict)  # fact -> the dimensions it uses

    def as_dict(self) -> dict:
        return {
            "dimensions": [
                {"name": d.name, "domain": d.domain, "conformed": d.conformed, "description": d.description}
                for d in self.dimensions
            ],
            "facts": [
                {"name": f.name, "domain": f.domain, "kind": f.kind.value, "dimensions": self.uses[f.name]}
                for f in self.facts
            ],
        }


def _modelled(entity: Entity) -> bool:
    return entity.layer is not Layer.RAW and entity.layer is not Layer.STAGING


def bus_matrix(model: Model) -> BusMatrix:
    entities = sorted((e for e in model.entities if _modelled(e)), key=lambda e: (e.domain or "", e.name))
    dimensions = [e for e in entities if e.kind is EntityKind.DIMENSION]
    facts = [e for e in entities if e.kind in FACT_KINDS]
    names = {d.name.lower() for d in dimensions}
    matrix = BusMatrix(dimensions, facts)
    for fact in facts:
        used = set()
        for rel in model.relationships:
            ends = {rel.from_.lower(), rel.to.lower()}
            if fact.name.lower() in ends:
                used |= (ends - {fact.name.lower()}) & names
        matrix.uses[fact.name] = [d.name for d in dimensions if d.name.lower() in used]
    return matrix


def _key_sources(entity: Entity) -> set[str]:
    """The source columns a dimension's key comes from (customer.c_custkey)."""
    keys = {a.name.lower() for a in entity.primary_key}
    return {s.lower() for m in entity.mappings if m.target.lower() in keys for s in m.sources}


def conformed_problems(model: Model) -> list[tuple[str, str, str]]:
    """(code, entity, message) for facts that use another domain's dimension that is not conformed
    (MK316), and dimensions that copy a conformed one (MK317)."""
    matrix = bus_matrix(model)
    by_name = {d.name: d for d in matrix.dimensions}
    out = []
    for fact in matrix.facts:
        for name in matrix.uses[fact.name]:
            dimension = by_name[name]
            if dimension.domain and fact.domain and dimension.domain != fact.domain and not dimension.conformed:
                out.append(
                    (
                        "MK316",
                        fact.name,
                        f"{fact.name} ({fact.domain}) uses {name} from {dimension.domain}: mark {name} "
                        "conformed: true if every domain shares it",
                    )
                )
    for conformed in (d for d in matrix.dimensions if d.conformed):
        sources = _key_sources(conformed)
        for other in matrix.dimensions:
            if other is not conformed and not other.conformed and sources and sources & _key_sources(other):
                out.append(
                    (
                        "MK317",
                        other.name,
                        f"{other.name} has the same key as the conformed {conformed.name} "
                        f"({', '.join(sorted(sources & _key_sources(other)))}): use {conformed.name} instead of a copy",
                    )
                )
    return out


def markdown(matrix: BusMatrix) -> str:
    """The bus matrix as a Markdown table: a row per fact, a column per dimension."""
    if not matrix.facts or not matrix.dimensions:
        return "No facts and dimensions to put in a bus matrix yet.\n"
    head = [
        "Fact (process)",
        "Domain",
        *(f"{d.name}{' (conformed)' if d.conformed else ''}" for d in matrix.dimensions),
    ]
    lines = ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |"]
    for fact in matrix.facts:
        marks = ["✓" if d.name in matrix.uses[fact.name] else "" for d in matrix.dimensions]
        lines.append("| " + " | ".join([fact.name, fact.domain or "", *marks]) + " |")
    return "\n".join(lines) + "\n"


__all__ = ["BusMatrix", "bus_matrix", "conformed_problems", "markdown"]
