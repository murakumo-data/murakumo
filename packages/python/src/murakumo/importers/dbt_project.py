"""Bring an existing dbt project into the model: ``murakumo import dbt <folder>``.

A team that already has a dbt project does not start from nothing. This reads the project and adds:

- each dbt **source** as a source system, with its tables as raw tables;
- each **model** as a modelled table (``fct_`` and ``dim_`` as facts and dimensions in marts, the
  rest as intermediate tables), with its columns, descriptions and keys;
- ``relationships`` tests as relationships;
- where each column **comes from**, read from the model's compiled SQL: a column that is another
  column under a new name (or a cast of one) gets a mapping. A computed column is left unmapped
  and listed with its SQL, to be written as an expression by hand or with ``murakumo ai map``.

Staging models that only rename and cast one source table are not imported: Murakumo generates
staging from the raw tables itself. Their renames are followed, so a mart column maps to the raw
column it began as.

It reads ``target/manifest.json`` (and ``target/catalog.json`` for column types) when dbt has
written them: run ``dbt compile`` (or ``dbt docs generate``, for the types) first. Without them it
reads the project's YAML files, which give tables and columns but not where columns come from.
The dbt project is only read, never changed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from murakumo.importers.base import SourceImport, fk_relationship, safe_name, unique_names
from murakumo.importers.typemap import sql_to_logical
from murakumo.ir.model import (
    Attribute,
    Entity,
    EntityKind,
    KeyRole,
    Layer,
    Mapping,
    Relationship,
    SourceSystem,
)
from murakumo.project import Project


class DbtImportError(Exception):
    pass


@dataclass
class Table:
    """A dbt source table or model, as far as the import needs it."""

    id: str  # dbt's unique_id, or model.<name> / source.<source>.<table> when read from YAML
    name: str
    kind: str  # source | model
    source: str | None = None  # for a source table: its source's name
    path: str = ""
    description: str | None = None
    columns: dict[str, dict] = field(default_factory=dict)  # name -> {type, description, tests}
    depends: list[str] = field(default_factory=list)
    sql: str | None = None  # compiled
    relation: str | None = None  # database.schema.table, as the compiled SQL writes it
    relationships: list[tuple[str, str, str]] = field(default_factory=list)  # column, to model, to column
    unique_combination: list[str] = field(default_factory=list)


@dataclass
class DbtImport:
    name: str
    dialect: str | None
    sources: list[SourceImport] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # staging models Murakumo generates itself
    notes: list[str] = field(default_factory=list)  # what is left to finish by hand
    compiled: bool = False


@dataclass
class DbtReport:
    added_sources: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    mapped: int = 0
    unmapped: int = 0
    relationships: int = 0
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# Reading the dbt project ----------------------------------------------------------------------


def _norm(relation: str | None) -> str | None:
    return re.sub(r'[`"\[\]]', "", relation).lower() if relation else None


def _tests(column: dict) -> list[Any]:
    return list(column.get("data_tests") or column.get("tests") or [])


def _column(raw: dict) -> dict:
    names = {t if isinstance(t, str) else next(iter(t)) for t in _tests(raw)}
    constraints = {c.get("type") for c in raw.get("constraints") or [] if isinstance(c, dict)}
    return {
        "type": raw.get("data_type"),
        "description": raw.get("description") or None,
        "unique": "unique" in names or "primary_key" in constraints,
        "not_null": "not_null" in names or bool({"not_null", "primary_key"} & constraints),
        "primary": "primary_key" in constraints,
    }


def _from_manifest(folder: Path) -> tuple[dict[str, Table], str, str | None]:
    manifest = json.loads((folder / "target" / "manifest.json").read_text(encoding="utf-8"))
    catalog_path = folder / "target" / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.is_file() else {}
    types = {
        unique_id: {name.lower(): column.get("type") for name, column in (node.get("columns") or {}).items()}
        for section in ("nodes", "sources")
        for unique_id, node in (catalog.get(section) or {}).items()
    }
    tables: dict[str, Table] = {}

    def columns(unique_id: str, node: dict) -> dict[str, dict]:
        found = {name: _column(raw) for name, raw in (node.get("columns") or {}).items()}
        for name, kind in types.get(unique_id, {}).items():  # the catalog knows every column and its type
            current = next((c for n, c in found.items() if n.lower() == name), None)
            if current is None:
                found[name] = _column({"data_type": kind})
            elif not current["type"]:
                current["type"] = kind
        return found

    for unique_id, node in (manifest.get("sources") or {}).items():
        tables[unique_id] = Table(
            unique_id,
            node.get("identifier") or node["name"],
            "source",
            source=node.get("source_name"),
            description=node.get("description") or None,
            columns=columns(unique_id, node),
            relation=_norm(node.get("relation_name")),
        )
    for unique_id, node in (manifest.get("nodes") or {}).items():
        if node.get("resource_type") != "model":
            continue
        tables[unique_id] = Table(
            unique_id,
            node.get("alias") or node["name"],
            "model",
            path=node.get("original_file_path") or node.get("path") or "",
            description=node.get("description") or None,
            columns=columns(unique_id, node),
            depends=[d for d in (node.get("depends_on") or {}).get("nodes", [])],
            sql=node.get("compiled_code") or node.get("compiled_sql"),
            relation=_norm(node.get("relation_name")),
        )
    # Tests are their own nodes in the manifest: unique, not_null, relationships, and combinations.
    for node in (manifest.get("nodes") or {}).values():
        meta = node.get("test_metadata") or {}
        if node.get("resource_type") != "test" or not meta:
            continue
        attached = node.get("attached_node") or next(iter((node.get("depends_on") or {}).get("nodes", [])), None)
        table = tables.get(attached or "")
        kwargs = meta.get("kwargs") or {}
        column = node.get("column_name") or kwargs.get("column_name")
        if table is None:
            continue
        if meta.get("name") in ("unique", "not_null") and column:
            found = next((c for n, c in table.columns.items() if n.lower() == column.lower()), None)
            if found is None:
                found = table.columns.setdefault(column, _column({}))
            found[meta["name"]] = True
        elif meta.get("name") == "relationships" and column and kwargs.get("to") and kwargs.get("field"):
            target = re.search(r"ref\(\s*['\"]([^'\"]+)['\"]", str(kwargs["to"]))
            if target:
                table.relationships.append((column, target.group(1), str(kwargs["field"])))
        elif meta.get("name") in ("unique_combination_of_columns", "murakumo_unique_combination"):
            combination = kwargs.get("combination_of_columns") or kwargs.get("columns") or []
            table.unique_combination = [str(c) for c in combination]
    metadata = manifest.get("metadata") or {}
    return tables, metadata.get("project_name") or folder.name, metadata.get("adapter_type")


def _from_yaml(folder: Path) -> tuple[dict[str, Table], str, str | None]:
    project_file = folder / "dbt_project.yml"
    settings = yaml.safe_load(project_file.read_text(encoding="utf-8")) or {}
    roots = [folder / p for p in settings.get("model-paths") or settings.get("source-paths") or ["models"]]
    tables: dict[str, Table] = {}
    sql_files = {p.stem.lower(): p for root in roots for p in root.rglob("*.sql")}
    for root in roots:
        for path in sorted([*root.rglob("*.yml"), *root.rglob("*.yaml")]):
            try:
                document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise DbtImportError(f"cannot read {path}: {str(exc).splitlines()[0]}") from None
            if not isinstance(document, dict):
                continue
            for source in document.get("sources") or []:
                for raw in source.get("tables") or []:
                    unique_id = f"source.{source['name']}.{raw['name']}"
                    tables[unique_id] = Table(
                        unique_id,
                        raw.get("identifier") or raw["name"],
                        "source",
                        source=source["name"],
                        description=raw.get("description") or None,
                        columns={c["name"]: _column(c) for c in raw.get("columns") or []},
                    )
            for raw in document.get("models") or []:
                unique_id = f"model.{raw['name']}"
                table = Table(
                    unique_id,
                    raw["name"],
                    "model",
                    description=raw.get("description") or None,
                    columns={c["name"]: _column(c) for c in raw.get("columns") or []},
                )
                for column in raw.get("columns") or []:
                    for test in _tests(column):
                        if isinstance(test, dict) and "relationships" in test:
                            arguments = test["relationships"].get("arguments") or test["relationships"]
                            target = re.search(r"ref\(\s*['\"]([^'\"]+)['\"]", str(arguments.get("to", "")))
                            if target and arguments.get("field"):
                                table.relationships.append((column["name"], target.group(1), str(arguments["field"])))
                tables[unique_id] = table
    for stem, path in sql_files.items():  # models with no YAML entry still exist
        table = tables.setdefault(f"model.{stem}", Table(f"model.{stem}", path.stem, "model"))
        table.path = str(path.relative_to(folder))
        text = path.read_text(encoding="utf-8")
        table.depends = [f"model.{name.lower()}" for name in re.findall(r"ref\(\s*['\"]([^'\"]+)['\"]\s*\)", text)]
        table.depends += [
            f"source.{a}.{b}"
            for a, b in re.findall(r"source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)", text)
        ]
    profile = settings.get("profile")
    return tables, settings.get("name") or folder.name, None if not profile else None


# Building the model ---------------------------------------------------------------------------

DIALECTS = {"synapse": "tsql", "fabric": "tsql", "sqlserver": "tsql"}


def _type(raw: str | None) -> str:
    if not raw:
        return "string"
    return sql_to_logical(raw)[0]


def _is_staging(table: Table, tables: dict[str, Table]) -> bool:
    """A model that only prepares one source table: Murakumo writes that itself."""
    upstream = [tables[d] for d in table.depends if d in tables]
    named = table.name.lower().startswith(("stg_", "staging_", "base_")) or "/staging/" in f"/{table.path}"
    return named and len(upstream) == 1 and upstream[0].kind == "source"


def _placement(table: Table) -> tuple[Layer, EntityKind]:
    name, path = table.name.lower(), f"/{table.path.lower()}"
    if name.startswith(("fct_", "fact_")):
        return Layer.MARTS, EntityKind.FACT
    if name.startswith(("dim_", "dimension_")):
        return Layer.MARTS, EntityKind.DIMENSION
    if "/marts/" in path or "/mart/" in path or "/gold/" in path:
        return Layer.MARTS, EntityKind.TABLE
    return Layer.INTERMEDIATE, EntityKind.TABLE


def _attributes(table: Table, raw: bool) -> list[Attribute]:
    keys = [n for n, c in table.columns.items() if c["primary"]] or [
        n for n, c in table.columns.items() if c["unique"] and c["not_null"]
    ]
    if len(keys) > 1 and not any(table.columns[k]["primary"] for k in keys):
        keys = keys[:1]  # several unique columns: the first is the key, the rest stay unique by their tests
    if not keys and table.unique_combination:
        keys = [n for n in table.columns if n.lower() in {c.lower() for c in table.unique_combination}]
    out = []
    for name, column in table.columns.items():
        primary = name in keys
        out.append(
            Attribute(
                name=name if raw else safe_name(name).lower(),
                type=_type(column["type"]),
                nullable=not (primary or column["not_null"]),
                key=KeyRole.PRIMARY if primary else None,
                description=column["description"],
                source_type=column["type"] if raw else None,
            )
        )
    return out


class _Lineage:
    """Where a model's columns come from, read from compiled SQL."""

    def __init__(self, tables: dict[str, Table], dialect: str | None, entity_of: dict[str, str]):
        self.tables = tables
        self.dialect = dialect
        self.entity_of = entity_of  # dbt id -> the entity it became (models and source tables)
        self.by_relation = {t.relation: t for t in tables.values() if t.relation}
        self.by_name = {t.name.lower(): t for t in tables.values()}
        self.cache: dict[tuple[str, str], tuple[str, str, bool] | None] = {}

    def _schema(self, table: Table) -> dict | None:
        """What SQLGlot needs to follow `select *`: the columns of the relations this model reads.
        The documented ones, and every column name its SQL mentions, since documentation is rarely
        complete (and never lists the columns ingestion adds)."""
        import sqlglot
        from sqlglot import exp

        try:
            mentioned = {c.name for c in sqlglot.parse_one(table.sql, read=self.dialect).find_all(exp.Column)}
        except Exception:  # noqa: BLE001
            return None
        schema: dict = {}
        for unique_id in table.depends:
            upstream = self.tables.get(unique_id)
            parts = (upstream.relation or "").split(".") if upstream else []
            if len(parts) != 3:
                continue
            names = set(upstream.columns) | {n.lower() for n in upstream.columns} | mentioned
            schema.setdefault(parts[0], {}).setdefault(parts[1], {})[parts[2]] = {n: "text" for n in names if n}
        return schema or None

    def _upstream(self, node) -> Table | None:
        from sqlglot import exp

        source = node.source
        if not isinstance(source, exp.Table):
            return None
        full = ".".join(part for part in (source.catalog, source.db, source.name) if part).lower()
        return self.by_relation.get(full) or self.by_name.get(source.name.lower())

    def origin(self, table: Table, column: str, depth: int = 0) -> tuple[str, str, bool] | None:
        """(entity, column, direct) for a model's column: the one column it reads, and whether it
        is that column unchanged (renamed or cast). None when it reads several, or none."""
        key = (table.id, column.lower())
        if key in self.cache:
            return self.cache[key]
        self.cache[key] = None
        if not table.sql or depth > 12:
            return None
        from sqlglot import exp
        from sqlglot.lineage import lineage

        try:
            root = lineage(column, table.sql, schema=self._schema(table), dialect=self.dialect)
        except Exception:  # noqa: BLE001 - SQL that SQLGlot cannot follow is simply not mapped
            return None
        nodes = list(root.walk())
        # Where it ends: the columns of tables it reads. A branch of a UNION that is a constant
        # (an "unknown" row) reads nothing and does not count.
        leaves = [n for n in nodes if not n.downstream and isinstance(n.source, exp.Table)]
        ends = {(getattr(self._upstream(n), "id", None), n.name.rsplit(".", 1)[-1].lower()) for n in leaves}
        if len(ends) != 1:
            return None
        leaf = leaves[0]
        upstream = self._upstream(leaf)
        if upstream is None:
            return None
        direct = True
        for node in nodes:
            expression = node.expression.unalias() if isinstance(node.expression, exp.Alias) else node.expression
            while isinstance(expression, exp.Cast | exp.TryCast | exp.Paren):
                expression = expression.this
            if isinstance(expression, exp.Column | exp.Table | exp.Star):
                continue
            if isinstance(expression, exp.Query | exp.Subquery) or not list(expression.find_all(exp.Column)):
                continue  # a whole SELECT the column passes through, or a constant
            direct = False
        source_column = leaf.name.rsplit(".", 1)[-1].strip('"`[]')
        if upstream.id in self.entity_of:  # a table in the model: stop here
            found = next((n for n in upstream.columns if n.lower() == source_column.lower()), source_column)
            name = found if upstream.kind == "source" else safe_name(found).lower()
            result: tuple[str, str, bool] | None = (self.entity_of[upstream.id], name, direct)
        else:  # a staging model that was not imported: follow it to the source
            further = self.origin(upstream, source_column, depth + 1)
            result = None if further is None else (further[0], further[1], direct and further[2])
        self.cache[key] = result
        return result


def read(folder: Path) -> DbtImport:
    """Read a dbt project into what would be added to the model. Changes nothing."""
    folder = folder.resolve()
    if not (folder / "dbt_project.yml").is_file():
        raise DbtImportError(f"{folder} is not a dbt project: it has no dbt_project.yml")
    compiled = (folder / "target" / "manifest.json").is_file()
    try:
        tables, name, adapter = _from_manifest(folder) if compiled else _from_yaml(folder)
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise DbtImportError(f"cannot read the dbt project: {exc}") from None
    dialect = DIALECTS.get(adapter or "", adapter)
    result = DbtImport(name=name, dialect=dialect, compiled=compiled)

    entity_of: dict[str, str] = {}
    by_source: dict[str, list[Table]] = {}
    for table in tables.values():
        if table.kind == "source":
            by_source.setdefault(table.source or "raw", []).append(table)
    for source_name, found in sorted(by_source.items()):
        source = safe_name(source_name, "source")
        imported = SourceImport(SourceSystem(name=source, description=f"Imported from the dbt project {name}."))
        for table in sorted(found, key=lambda t: t.name):
            entity_name = safe_name(table.name, "table")
            entity_of[table.id] = entity_name
            if not table.columns:
                result.notes.append(
                    f"{entity_name}: the dbt project lists no columns for this source table; "
                    "import them from the database (`murakumo import database`)"
                )
            elif not all(c["type"] for c in table.columns.values()):
                result.notes.append(
                    f"{entity_name}: the dbt project does not say its columns' types, so they are string; run "
                    "`dbt docs generate` in it and import again, or `murakumo import database`"
                )
            imported.entities.append(
                Entity(
                    name=entity_name,
                    layer=Layer.RAW,
                    source=source,
                    description=table.description,
                    attributes=_attributes(table, raw=True),
                )
            )
        result.sources.append(imported)

    models = [t for t in tables.values() if t.kind == "model"]
    kept = []
    for table in sorted(models, key=lambda t: t.name):
        if _is_staging(table, tables):
            result.skipped.append(table.name)
        else:
            kept.append(table)
            entity_of[table.id] = safe_name(table.name, "table").lower()
    lineage = _Lineage(tables, dialect, entity_of)
    model_names = {t.name.lower(): t for t in models}
    relationships: list[Relationship] = []
    for table in kept:
        layer, kind = _placement(table)
        entity = Entity(
            name=entity_of[table.id],
            layer=layer,
            kind=kind,
            description=table.description,
            attributes=_attributes(table, raw=False),
        )
        if kind is EntityKind.FACT:
            entity.grain = [a.name for a in entity.attributes if a.key is KeyRole.PRIMARY]
        if not table.columns:
            result.notes.append(
                f"{entity.name}: no columns are documented; run `dbt docs generate` in the dbt project and import again"
            )
        for original in table.columns:
            column = safe_name(original).lower()
            origin = lineage.origin(table, original)
            if origin is not None and origin[2]:
                entity.mappings.append(Mapping(target=column, sources=[f"{origin[0]}.{origin[1]}"]))
            elif table.sql:
                where = f" (it reads {origin[0]}.{origin[1]})" if origin else ""
                result.notes.append(f"{entity.name}.{column}: computed in the dbt model{where}; write its expression")
        for column, target_name, target_column in table.relationships:
            target = model_names.get(target_name.lower())
            if target is None:
                continue
            if target.id in entity_of:
                to_entity, to_column = entity_of[target.id], safe_name(target_column).lower()
            else:  # the test points at a staging model: point at what it prepares
                found = lineage.origin(target, target_column)
                if found is None:
                    continue
                to_entity, to_column = found[0], found[1]
            nullable = not table.columns.get(column, {}).get("not_null", False)
            relationships.append(
                fk_relationship(entity.name, to_entity, [(safe_name(column).lower(), to_column)], nullable)
            )
        result.entities.append(entity)
    result.relationships = unique_names(relationships)
    if not compiled:
        result.notes.insert(
            0,
            "the dbt project is not compiled (no target/manifest.json), so where columns come from was not read: "
            "run `dbt compile` in it and import again",
        )
    return result


def apply(project: Project, imported: DbtImport) -> DbtReport:
    """Add what was read to the model (not yet saved). Tables already in the model are left alone."""
    from murakumo.importers.base import _key, merge

    report = DbtReport(skipped=list(imported.skipped), notes=list(imported.notes))
    for source in imported.sources:
        merged = merge(project, source)
        if merged.added_source:
            report.added_sources.append(source.source.name)
        report.added += merged.added
        report.notes += [d.message for d in merged.diagnostics]
    for entity in imported.entities:
        if project.model.entity(entity.name) is not None:
            report.existing.append(entity.name)
            continue
        # A mapping is kept only when what it reads is in the model.
        kept = []
        for mapping in entity.mappings:
            name, _, column = mapping.sources[0].partition(".")
            source = project.model.entity(name) or next((e for e in imported.entities if e.name == name), None)
            if source is not None and source.attribute(column) is not None:
                kept.append(mapping)
        entity.mappings = kept
        project.model.entities.append(entity)
        report.added.append(entity.name)
        report.mapped += len(kept)
        report.unmapped += len(entity.attributes) - len(kept)
    existing = {_key(r) for r in project.model.relationships}
    for relationship in imported.relationships:
        ends = (project.model.entity(relationship.from_), project.model.entity(relationship.to))
        if None in ends or _key(relationship) in existing:
            continue
        if any(ends[0].attribute(k.from_) is None or ends[1].attribute(k.to) is None for k in relationship.keys):
            continue
        project.model.relationships.append(relationship)
        existing.add(_key(relationship))
        report.relationships += 1
    return report


__all__ = ["DbtImport", "DbtImportError", "DbtReport", "apply", "read"]
