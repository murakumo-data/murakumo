"""Sources as the canvas shows them: what each one is, where it is read from, and its tables.

A source is declared in murakumo.yaml (``sources:``, with a ``from:`` line saying where its
metadata comes from) and described in model/sources/ once it has been imported. This module
lists both, adds declarations, browses a database source's catalogue and imports the tables
picked from it, with a preview of what an import would change before anything is written.

Connection details never pass through here as values: a database source names an environment
variable (``introspect:env:CRM_DB_URL``) or uses the SNOWFLAKE_* variables, and anything shown
or returned has passwords removed.
"""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from murakumo.importers import import_declared
from murakumo.importers.base import MergeReport, merge
from murakumo.importers.database import SourceUnavailable, browse, introspect, redact
from murakumo.manifest import SourceDeclaration
from murakumo.manifest_edit import ManifestEditError, write_section
from murakumo.project import Project

KINDS = {
    "database": "introspect",  # a live database, read through an environment variable
    "snowflake": "introspect",  # Snowflake, through the SNOWFLAKE_* variables
    "csv": "csv",  # a data dictionary, one row per column
    "excel": "excel",
    "file": "file",  # a Parquet, CSV or JSON data file
    "openapi": "openapi",
}
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class SourceError(Exception):
    pass


def _safe(text: str | None) -> str | None:
    """A ``from:`` line or message with any password or query string in a URL removed."""
    if not text:
        return text
    return re.sub(r"[a-z][a-z0-9+]*://[^\s'\"]+", lambda m: redact(m.group(0)), text)


def _declaration(project: Project, name: str) -> SourceDeclaration | None:
    return next((s for s in project.manifest.sources if s.name.lower() == name.lower()), None)


def kind_of(declaration: SourceDeclaration | None) -> str | None:
    """csv, excel, file, openapi, database or snowflake, from the ``from:`` line."""
    if declaration is None or not declaration.from_:
        return None
    scheme, _, target = declaration.from_.partition(":")
    if scheme == "introspect":
        return "snowflake" if target.startswith("snowflake:") else "database"
    return scheme


def list_sources(project: Project) -> list[dict[str, Any]]:
    names: dict[str, None] = {}
    for source in project.model.sources:
        names.setdefault(source.name)
    for declared in project.manifest.sources:
        if not any(n.lower() == declared.name.lower() for n in names):
            names.setdefault(declared.name)
    result = []
    for name in sorted(names, key=str.lower):
        system = project.model.source(name)
        declaration = _declaration(project, name)
        origin = project.origins.get(system.id) if system else None
        updated = (
            datetime.fromtimestamp(origin.stat().st_mtime, UTC).isoformat(timespec="seconds")
            if origin and origin.is_file()
            else None
        )
        result.append(
            {
                "name": system.name if system else name,
                "type": system.type.value if system else None,
                "kind": kind_of(declaration),
                "from": _safe(declaration.from_) if declaration else None,
                "declared": declaration is not None,
                "tables": sum(1 for e in project.model.entities if (e.source or "").lower() == name.lower()),
                "description": system.description if system else None,
                "updated": updated,
            }
        )
    return result


def from_line(kind: str, location: str) -> str:
    """The ``from:`` line for a new source. A database is named by an environment variable."""
    location = location.strip()
    if kind not in KINDS:
        raise SourceError(f"unknown kind {kind!r}; use one of {', '.join(KINDS)}")
    if kind == "database":
        name = location.removeprefix("env:")
        if not _ENV_NAME.match(name):
            raise SourceError(
                "give the name of an environment variable that holds the connection string, such as CRM_DB_URL; "
                "the connection string itself stays out of murakumo.yaml"
            )
        return f"introspect:env:{name}"
    if kind == "snowflake":
        database = location.strip().strip("/")
        if database and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", database):
            raise SourceError("give the Snowflake database name, such as COMMERCE")
        return f"introspect:snowflake:///{database}" if database else "introspect:snowflake://"
    if not location:
        raise SourceError("give the path or URL to read from")
    if "://" in location and kind in ("csv", "excel"):
        raise SourceError("data dictionaries are read from a file in the project, not a URL")
    return f"{KINDS[kind]}:{location}"


def add_source(project: Project, name: str, kind: str, location: str) -> SourceDeclaration:
    """Declare a new source in murakumo.yaml, keeping the file's comments."""
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise SourceError(f"{name!r} is not a valid name: use letters, digits and underscores")
    if _declaration(project, name) or project.model.source(name):
        raise SourceError(f"source {name!r} already exists")
    declaration = SourceDeclaration.model_validate({"name": name, "from": from_line(kind, location)})
    entries = [s.model_dump(by_alias=True, exclude_defaults=True) for s in project.manifest.sources]
    entries.append({"name": declaration.name, "from": declaration.from_})
    try:
        project.manifest = write_section(project.root, ("sources",), entries)
    except ManifestEditError as exc:
        raise SourceError(str(exc)) from None
    return declaration


def _introspect_url(project: Project, name: str) -> str:
    declaration = _declaration(project, name)
    if declaration is None or not declaration.from_:
        raise SourceError(f"source {name} has no from: line in murakumo.yaml")
    scheme, _, target = declaration.from_.partition(":")
    if scheme != "introspect":
        raise SourceError(f"source {name} is read from {scheme}, not a database; re-import it instead")
    return target


def browse_source(project: Project, name: str) -> list[dict[str, Any]]:
    """The database's schemas, tables and columns, each table marked if it is already in the model."""
    url = _introspect_url(project, name)
    try:
        schemas = browse(url)
    except SourceUnavailable as exc:
        raise SourceError(_safe(str(exc))) from None
    except Exception as exc:  # noqa: BLE001 - driver errors vary; show them without secrets
        raise SourceError(_safe(f"cannot read the catalogue: {exc}")) from None
    in_model = {
        (str(e.properties.get("schema", "")).lower(), e.name.lower())
        for e in project.model.entities
        if (e.source or "").lower() == name.lower()
    }
    names_only = {table for _, table in in_model}
    for schema in schemas:
        for table in schema["tables"]:
            key = (schema["name"].lower(), table["name"].lower())
            table["in_model"] = key in in_model or table["name"].lower() in names_only
    return schemas


def _imports(project: Project, name: str, tables: list[str] | None) -> list:
    declaration = _declaration(project, name)
    if declaration is None:
        raise SourceError(f"source {name} is not declared in murakumo.yaml")
    try:
        if tables is not None:
            return [introspect(_introspect_url(project, name), declaration.name, tables=tables)]
        return import_declared(declaration, Path(project.root))
    except SourceUnavailable as exc:
        raise SourceError(_safe(str(exc))) from None
    except FileNotFoundError as exc:
        raise SourceError(f"cannot find {exc.filename}; check the from: line of {name} in murakumo.yaml") from None
    except (ValueError, OSError) as exc:
        raise SourceError(_safe(str(exc))) from None


def report_dict(report: MergeReport) -> dict[str, Any]:
    data = asdict(report)
    data["diagnostics"] = [{"level": d.level.value, "code": d.code, "message": d.message} for d in report.diagnostics]
    data["has_changes"] = report.has_changes
    return data


def import_source(
    project: Project, name: str, tables: list[str] | None = None, prune: bool = False
) -> list[dict[str, Any]]:
    """Import a source (or the picked tables of a database source) into the model in memory.

    Returns one report per import; the caller saves the project, or not, for a preview.
    """
    if tables is not None and not tables:
        raise SourceError("pick at least one table")
    return [report_dict(merge(project, imported, prune=prune)) for imported in _imports(project, name, tables)]


__all__ = ["KINDS", "SourceError", "add_source", "browse_source", "from_line", "import_source", "list_sources"]
