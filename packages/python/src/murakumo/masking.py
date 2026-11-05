"""Masking policies: what a column marked PII, or classified, shows to roles that may not see it.

``governance.masking`` maps ``pii`` or a classification to a rule; ``governance.unmasked_roles``
are the roles that see the real values. On Snowflake, each rule is a masking policy in the
environment database's ``governance`` schema, set on the marts' columns after each build (a dbt
post-hook, because re-creating a table drops its policies). Other warehouses have no masking
policies here, so nothing is generated for them.
"""

from __future__ import annotations

from dataclasses import dataclass

from murakumo.ir.model import Entity, History, Layer
from murakumo.project import Project

RULES = {
    "redact": "'***'",
    "hash": "sha2(val)",
    "null": "null",
    "partial": "case when length(val) <= 4 then '***' else repeat('*', length(val) - 4) || right(val, 4) end",
}
SCHEMA = "governance"


@dataclass
class Masked:
    entity: Entity
    column: str
    rule: str
    reason: str  # pii, or the classification


def rule_for(project: Project, attribute) -> tuple[str, str] | None:
    masking = project.manifest.governance.masking
    if attribute.pii and "pii" in masking:
        return masking["pii"], "pii"
    if attribute.classification and attribute.classification.value in masking:
        return masking[attribute.classification.value], attribute.classification.value
    return None


def masked_columns(project: Project) -> list[Masked]:
    """The marts' text columns that get a masking policy, by entity."""
    out = []
    for entity in sorted(project.model.entities, key=lambda e: e.name):
        if entity.layer not in (Layer.MARTS, Layer.SEMANTIC):
            continue
        for attribute in entity.attributes:
            found = rule_for(project, attribute)
            if found and found[0] in RULES and attribute.logical_type.family == "text":
                out.append(Masked(entity, attribute.name, found[0], found[1]))
    return out


def problems(project: Project) -> list[tuple[str, str, str]]:
    """(code, where, message): unknown rules (MK704), and columns that cannot be masked (MK705)."""
    out = []
    for key, rule in project.manifest.governance.masking.items():
        if rule not in RULES:
            out.append(("MK704", key, f"masking rule {rule!r} for {key}: use one of {', '.join(RULES)}"))
    for entity in project.model.entities:
        if entity.layer not in (Layer.MARTS, Layer.SEMANTIC):
            continue
        for attribute in entity.attributes:
            found = rule_for(project, attribute)
            if found and attribute.logical_type.family != "text":
                out.append(
                    (
                        "MK705",
                        f"{entity.name}.{attribute.name}",
                        f"{entity.name}.{attribute.name} is {attribute.type}: masking policies apply to text columns",
                    )
                )
    return out


def body(rule: str, roles: list[str]) -> str:
    if not roles:
        return RULES[rule]
    allowed = " or ".join(f"is_role_in_session('{r.upper()}')" for r in roles)
    return f"case when {allowed} then val else {RULES[rule]} end"


def policy_sql(project: Project, database: str | None = None) -> str:
    """The masking policies, and the columns they are set on, as Snowflake SQL. Safe to run again."""
    columns = masked_columns(project)
    if not columns:
        return ""
    roles = project.manifest.governance.unmasked_roles
    schema = f"{database}.{SCHEMA}" if database else SCHEMA
    lines = [
        "-- Masking policies: what roles outside "
        + (", ".join(roles) if roles else "(none: governance.unmasked_roles is empty)")
        + " see in these columns.",
        f"create schema if not exists {schema};",
    ]
    for rule in sorted({c.rule for c in columns}):
        policy = f"{schema}.murakumo_mask_{rule}"
        lines += [
            f"create masking policy if not exists {policy} as (val string) returns string -> {body(rule, roles)};",
            f"alter masking policy {policy} set body -> {body(rule, roles)};",
        ]
    lines.append("-- Set on the marts' columns (the dbt build does this after each table is built):")
    for c in columns:
        lines.append(f"-- {c.entity.name}.{c.column}: {c.reason}")
        tables = [c.entity.name] + ([f"{c.entity.name}_history"] if c.entity.history is History.SCD4 else [])
        lines += [
            f"alter table if exists marts.{table} modify column {c.column} "
            f"set masking policy {schema}.murakumo_mask_{c.rule} force;"
            for table in tables
        ]
    return "\n".join(lines) + "\n"


__all__ = ["RULES", "Masked", "body", "masked_columns", "policy_sql", "problems", "rule_for"]
