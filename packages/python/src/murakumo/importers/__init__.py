"""Importers turn a source's metadata into IR objects, then merge them into the project.

| Importer | Reads |
| --- | --- |
| ``dictionary`` | CSV or Excel data dictionaries, one row per column |
| ``database`` | Live databases through ``information_schema`` (DuckDB, PostgreSQL) |
| ``files`` | Parquet, CSV and JSON files, local or over HTTPS |
| ``api`` | OpenAPI specs, and sample JSON responses |

Sources declared in ``murakumo.yaml`` with a ``from:`` line are imported by ``murakumo import``:

    sources:
      - name: erp
        from: csv:dictionaries/erp.csv       # csv: | excel: | introspect: | file: | openapi:
"""

from __future__ import annotations

from pathlib import Path

from murakumo.importers.base import MergeReport, SourceImport, merge
from murakumo.manifest import SourceDeclaration

SCHEMES = ("csv", "excel", "introspect", "file", "openapi")


def import_declared(declaration: SourceDeclaration, root: Path) -> list[SourceImport]:
    """Run the importer a manifest source names in its ``from:`` line."""
    from murakumo.importers.api import import_openapi
    from murakumo.importers.database import introspect
    from murakumo.importers.dictionary import import_dictionary
    from murakumo.importers.files import import_file

    if not declaration.from_:
        raise ValueError(f"source {declaration.name} has no from: line")
    scheme, sep, target = declaration.from_.partition(":")
    if not sep or scheme not in SCHEMES:
        raise ValueError(
            f"source {declaration.name}: cannot read from {declaration.from_!r}; "
            f"start it with one of {', '.join(s + ':' for s in SCHEMES)}"
        )

    def local(path: str) -> Path:
        return (root / path).resolve() if not Path(path).is_absolute() else Path(path)

    if scheme in ("csv", "excel"):
        imports = import_dictionary(local(target), source=declaration.name)
    elif scheme == "introspect":
        imports = [introspect(target, declaration.name)]
    elif scheme == "file":
        location = target if "://" in target else str(local(target))
        imports = [import_file(location, declaration.name)]
    else:
        location = target if "://" in target else str(local(target))
        imports = [import_openapi(location, declaration.name)]

    ingestion = declaration.ingestion
    for imported in imports:
        if ingestion.tool:
            imported.source.load_tool = ingestion.tool
        if ingestion.schedule:
            imported.source.schedule = ingestion.schedule
    return imports


__all__ = ["MergeReport", "SourceImport", "import_declared", "merge"]
