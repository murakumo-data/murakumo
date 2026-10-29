"""Check and preview calculated columns: the SQL expressions in mappings.

Expressions are written in DuckDB's SQL and read columns as ``entity.column``, or by their bare
name when a mapping source already names the entity. Checking one says what it reads, whether
every column exists, what type it returns and how it reads in the warehouse's own SQL. A preview
runs it on a few made-up rows in an in-memory DuckDB database, so nothing touches a warehouse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.annotate_types import annotate_types

from murakumo.generate.plan import EXPRESSION_DIALECT, _text_concat_arguments
from murakumo.generate.targets import TARGETS
from murakumo.importers.typemap import sql_to_logical
from murakumo.ir.model import Attribute, Entity
from murakumo.manifest import Warehouse
from murakumo.project import Project

PREVIEW_ROWS = 12


@dataclass
class Check:
    error: str | None = None
    line: int | None = None
    column: int | None = None
    references: list[str] = field(default_factory=list)  # entity.column, as the model names them
    problems: list[str] = field(default_factory=list)  # columns that do not resolve, and why
    type: str | None = None  # the logical type the expression returns, when it can be told
    warehouse_sql: str | None = None  # the expression in the project warehouse's dialect

    @property
    def ok(self) -> bool:
        return self.error is None and not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "line": self.line,
            "column": self.column,
            "references": self.references,
            "problems": self.problems,
            "type": self.type,
            "warehouse_sql": self.warehouse_sql,
        }


def _parse(expression: str, check: Check) -> exp.Expression | None:
    try:
        tree = sqlglot.parse_one(expression, read=EXPRESSION_DIALECT)
    except sqlglot.errors.ParseError as exc:
        first = exc.errors[0] if exc.errors else {}
        description = str(first.get("description") or str(exc).splitlines()[0])
        # SQLGlot names its own classes when something is missing; say it in words instead.
        check.error = "the expression is incomplete" if description.startswith("Required keyword") else description
        check.line, check.column = first.get("line"), first.get("col")
        return None
    if tree is None or isinstance(tree, (exp.Select, exp.Union, exp.Insert, exp.Update, exp.Delete, exp.Create)):
        check.error = "write one expression, such as quantity * unit_price, not a whole statement"
        return None
    return tree


def _resolve(
    project: Project, tree: exp.Expression, sources: list[str], check: Check
) -> dict[int, tuple[Entity, Attribute]]:
    """Each column node's entity and attribute, keyed by node id. Unresolved ones go to problems."""
    model = project.model
    bare: dict[str, list[tuple[Entity, Attribute]]] = {}
    for ref in sources:
        entity_name, _, column_name = ref.partition(".")
        entity = model.entity(entity_name)
        attribute = entity.attribute(column_name) if entity else None
        if entity and attribute:
            bare.setdefault(attribute.name.lower(), []).append((entity, attribute))
    # A bare name may also be any column of an entity the expression already names in full.
    named = {c.table.lower() for c in tree.find_all(exp.Column) if c.table}

    found: dict[int, tuple[Entity, Attribute]] = {}
    for node in tree.find_all(exp.Column):
        if node.table:
            entity = model.entity(node.table)
            if entity is None:
                check.problems.append(f"{node.table}.{node.name}: there is no entity {node.table}")
                continue
            attribute = entity.attribute(node.name)
            if attribute is None:
                check.problems.append(f"{node.table}.{node.name}: {entity.name} has no column {node.name}")
                continue
            found[id(node)] = (entity, attribute)
            continue
        candidates = bare.get(node.name.lower(), [])
        if not candidates:
            for entity_name in named:
                entity = model.entity(entity_name)
                attribute = entity.attribute(node.name) if entity else None
                if attribute is not None:
                    candidates.append((entity, attribute))
        if len(candidates) == 1:
            found[id(node)] = candidates[0]
        elif candidates:
            options = " or ".join(f"{e.name}.{a.name}" for e, a in candidates)
            check.problems.append(f"{node.name} is ambiguous: write {options}")
        else:
            check.problems.append(f"{node.name}: write it as entity.column, so it is clear which table it comes from")
    return found


def check_expression(
    project: Project, expression: str, sources: list[str] | None = None, target: str | None = None
) -> Check:
    """Check an expression; ``target`` is the entity it calculates a column of, if known."""
    check = Check()
    tree = _parse(expression, check)
    if tree is None:
        return check
    resolved = _resolve(project, tree, sources or [], check)
    if target:
        own = {f"{e.name}.{a.name}" for e, a in resolved.values() if e.name.lower() == target.lower()}
        for ref in sorted(own):
            check.problems.append(f"{ref}: read the tables {target} is built from, not its own columns")
    seen: dict[str, None] = {}
    for entity, attribute in resolved.values():
        seen.setdefault(f"{entity.name}.{attribute.name}")
    check.references = list(seen)

    target = TARGETS[project.manifest.targets.warehouse.type]
    try:
        check.warehouse_sql = _text_concat_arguments(tree.copy()).sql(dialect=target.dialect)
    except Exception:  # noqa: BLE001
        check.warehouse_sql = None

    # The type: each column becomes a typed null, and SQLGlot works out what the expression returns.
    # The transform runs in place (copy=False), because the resolved columns are known by node id.
    duckdb = TARGETS[Warehouse.DUCKDB]

    def typed(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and id(node) in resolved:
            physical = duckdb.physical(resolved[id(node)][1].logical_type)
            return exp.cast(exp.null(), exp.DataType.build(physical, dialect="duckdb"))
        return node

    try:
        annotated = annotate_types(tree.transform(typed, copy=False), dialect="duckdb")
        kind = annotated.type
        if kind is not None and not kind.is_type(exp.DataType.Type.UNKNOWN, exp.DataType.Type.NULL):
            logical, exact = sql_to_logical(kind.sql(dialect="duckdb"))
            check.type = ("string" if logical == "text" else logical) if exact else None
    except Exception:  # noqa: BLE001 - a guess at the type is a convenience, never an error
        check.type = None
    return check


def _json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)


def preview_expression(
    project: Project,
    expression: str,
    sources: list[str] | None = None,
    target: str | None = None,
    rows: int = PREVIEW_ROWS,
) -> dict[str, Any]:
    """Run the expression on made-up rows of the tables it reads. Returns columns, rows and notes."""
    check = check_expression(project, expression, sources, target)
    if not check.ok:
        return {"check": check.as_dict(), "columns": [], "rows": [], "note": None}
    try:
        import duckdb
    except ImportError:
        return {
            "check": check.as_dict(),
            "columns": [],
            "rows": [],
            "note": 'Previews run in DuckDB: install it with `pip install "murakumo[duckdb]"`.',
        }
    from murakumo.generate.sample import sample_rows

    tree = _parse(expression, Check())
    resolved = _resolve(project, tree, sources or [], Check())
    entities: dict[str, Entity] = {}
    inputs: dict[str, tuple[Entity, Attribute]] = {}
    for entity, attribute in resolved.values():
        entities.setdefault(entity.name.lower(), entity)
        inputs.setdefault(f"{entity.name}.{attribute.name}", (entity, attribute))

    data = sample_rows(project.model, list(entities.values()), rows=rows) if entities else {}
    aliases = {name: f"t{i}" for i, name in enumerate(entities)}

    def qualify(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and id(node) in resolved:
            entity, attribute = resolved[id(node)]
            return exp.column(attribute.name, table=aliases[entity.name.lower()], quoted=True)
        return node

    duckdb_target = TARGETS[Warehouse.DUCKDB]
    connection = duckdb.connect(":memory:")
    try:
        for name, entity in entities.items():
            alias = aliases[name]
            columns = ", ".join(f'"{a.name}" {duckdb_target.physical(a.logical_type)}' for a in entity.attributes)
            connection.execute(f"create table {alias} ({columns})")
            table = data.get(entity.name, [])
            if table:
                placeholders = ", ".join("?" for _ in entity.attributes)
                connection.executemany(
                    f"insert into {alias} values ({placeholders})",
                    [[row.get(a.name) for a in entity.attributes] for row in table],
                )
        names = list(entities)
        if not names:
            source = ""
        else:
            # Several tables are paired row by row, not joined: the preview shows what the
            # expression does with values, and made-up rows have no real matches to join on.
            numbered = [f"(select *, row_number() over () as _n from {aliases[n]}) as {aliases[n]}" for n in names]
            source = (
                " from "
                + numbered[0]
                + "".join(
                    f" join {numbered[i]} on {aliases[names[i]]}._n = {aliases[names[0]]}._n"
                    for i in range(1, len(names))
                )
            )
        shown = [f'{aliases[e.name.lower()]}."{a.name}"' for e, a in inputs.values()]
        sql = tree.transform(qualify, copy=False).sql(dialect="duckdb")
        query = f"select {', '.join([*shown, sql + ' as _result'])}{source} limit {rows}"
        try:
            result = connection.execute(query).fetchall()
        except duckdb.Error as exc:
            message = re.sub(r"\s+", " ", str(exc)).strip()
            return {"check": check.as_dict(), "columns": [], "rows": [], "note": None, "error": message}
    finally:
        connection.close()
    note = "Rows from different tables are paired by position, not joined." if len(entities) > 1 else None
    return {
        "check": check.as_dict(),
        "columns": [*inputs, "result"],
        "rows": [[_json(v) for v in row] for row in result],
        "note": note,
    }


__all__ = ["Check", "check_expression", "preview_expression"]
