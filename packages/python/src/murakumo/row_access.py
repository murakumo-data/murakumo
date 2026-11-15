"""Row-access policies: which rows of a mart each role may see.

A table says which column decides and which values each role may see::

    row_access:
      column: region
      allow:
        SALES_EMEA: [EMEA, UK]
        SALES_US: [US]

Roles in ``governance.unmasked_roles`` see every row; a role not named sees none. On Snowflake each
such table gets a row-access policy in the environment database's ``governance`` schema, set after
each build (a dbt post-hook, because re-creating a table drops its policies). Other warehouses have
no row-access policies here yet, and ``validate`` says so.
"""

from __future__ import annotations

from murakumo.ir.model import Entity, History, Layer
from murakumo.project import Project

SCHEMA = "governance"


def protected(project: Project) -> list[Entity]:
    """The marts with a row-access setting that can be applied."""
    return [
        e
        for e in sorted(project.model.entities, key=lambda e: e.name)
        if e.row_access is not None
        and e.layer in (Layer.MARTS, Layer.SEMANTIC)
        and (column := e.attribute(e.row_access.column)) is not None
        and column.logical_type.family == "text"
    ]


def _literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def body(entity: Entity, roles: list[str]) -> str:
    """The policy's condition: true for the rows the session's roles may see."""
    parts = [f"is_role_in_session('{r.upper()}')" for r in roles]
    for role, values in sorted(entity.row_access.allow.items()):
        listed = ", ".join(_literal(v) for v in values)
        parts.append(f"(is_role_in_session('{role.upper()}') and val in ({listed}))")
    return " or ".join(parts) or "false"


def policy(entity: Entity) -> str:
    return f"murakumo_rows_{entity.name.lower()}"


def problems(project: Project) -> list[tuple[str, str, str, str]]:
    """(level, code, entity, message)."""
    out = []
    snowflake = project.manifest.targets.warehouse.type.value == "snowflake"
    for entity in project.model.entities:
        setting = entity.row_access
        if setting is None:
            continue
        column = entity.attribute(setting.column)
        if entity.layer not in (Layer.MARTS, Layer.SEMANTIC):
            out.append(("warning", "MK707", entity.name, "row_access is set on marts: this table's is not used"))
        elif column is None:
            out.append(
                ("error", "MK707", entity.name, f"row_access.column: {entity.name} has no column {setting.column!r}")
            )
        elif column.logical_type.family != "text":
            out.append(
                ("error", "MK707", entity.name, f"row_access.column {column.name} is {column.type}: use a text column")
            )
        elif not setting.allow and not project.manifest.governance.unmasked_roles:
            out.append(("warning", "MK708", entity.name, "row_access allows no role, so nobody would see a row"))
        elif not snowflake:
            out.append(
                ("warning", "MK709", entity.name, "row-access policies are generated for Snowflake only, for now")
            )
    return out


def policy_sql(project: Project, database: str | None = None) -> str:
    """The row-access policies, and the tables they are set on, as Snowflake SQL. Safe to run again."""
    entities = protected(project)
    if not entities:
        return ""
    roles = project.manifest.governance.unmasked_roles
    schema = f"{database}.{SCHEMA}" if database else SCHEMA
    lines = [
        "-- Row-access policies: which rows each role sees. Roles that see every row: "
        + (", ".join(roles) if roles else "(none: governance.unmasked_roles is empty)")
        + ".",
        f"create schema if not exists {schema};",
    ]
    for entity in entities:
        name = f"{schema}.{policy(entity)}"
        condition = body(entity, roles)
        tables = [entity.name] + ([f"{entity.name}_history"] if entity.history is History.SCD4 else [])
        lines += [
            f"-- {entity.name}, by {entity.row_access.column}",
            f"create row access policy if not exists {name} as (val string) returns boolean -> {condition};",
            f"alter row access policy {name} set body -> {condition};",
        ]
        for table in tables:
            lines += [
                f"alter table if exists marts.{table} drop all row access policies;",
                f"alter table if exists marts.{table} add row access policy {name} on ({entity.row_access.column});",
            ]
    return "\n".join(lines) + "\n"


__all__ = ["body", "policy", "policy_sql", "problems", "protected"]
