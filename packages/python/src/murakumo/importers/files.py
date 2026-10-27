"""Read the schema of data files: Parquet, CSV and JSON, local or over HTTPS, single files or globs.

DuckDB reads only what it needs: Parquet footers hold the schema, and CSV and JSON are sampled.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from murakumo.diagnostics import warning
from murakumo.importers.base import SourceImport, safe_name
from murakumo.importers.database import SourceUnavailable
from murakumo.importers.typemap import sql_to_logical
from murakumo.ir.model import Attribute, Entity, SourceSystem, SourceType

READERS = {
    "parquet": "read_parquet",
    "csv": "read_csv",
    "json": "read_json",
}

_EXTENSIONS = {
    ".parquet": "parquet",
    ".pq": "parquet",
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "csv",
    ".json": "json",
    ".jsonl": "json",
    ".ndjson": "json",
}

# Partition-style suffixes in file names: _2024-01, -20240101, _2024_01_31.
_DATE_SUFFIX = re.compile(r"[_-]\d{4}(?:[-_]?\d{2}){0,2}$")


def file_format(location: str) -> str:
    suffix = PurePosixPath(location.split("?", 1)[0]).suffix.lower()
    if suffix == ".gz":
        suffix = PurePosixPath(location.split("?", 1)[0][: -len(".gz")]).suffix.lower()
    if suffix not in _EXTENSIONS:
        raise ValueError(f"cannot tell the format of {location!r}; pass --format parquet, csv or json")
    return _EXTENSIONS[suffix]


def table_name(location: str) -> str:
    """``.../yellow_tripdata_2024-01.parquet`` -> ``yellow_tripdata``."""
    path = PurePosixPath(location.split("?", 1)[0])
    stem = re.sub(r"(\.[A-Za-z0-9]+)+$", "", path.name).replace("*", "").strip("_-")
    if not stem:  # a glob such as trips/*.parquet is named after its folder
        stem = path.parent.name.replace("*", "")
    return safe_name(_DATE_SUFFIX.sub("", stem) or stem, "table")


def describe(location: str, fmt: str) -> list[tuple[str, str, bool]]:
    """Column name, source type and nullability, as DuckDB reads them."""
    try:
        import duckdb
    except ImportError:
        raise SourceUnavailable("reading files needs DuckDB: pip install 'murakumo[duckdb]'") from None
    connection = duckdb.connect()
    try:
        rows: list[Any] = connection.execute(f"describe select * from {READERS[fmt]}(?)", [location]).fetchall()
    except duckdb.Error as exc:
        raise SourceUnavailable(f"cannot read {location}: {str(exc).splitlines()[0]}") from None
    finally:
        connection.close()
    # DESCRIBE returns name, type, null ("YES"/"NO"), key, default, extra.
    return [(row[0], row[1], row[2] != "NO") for row in rows]


def import_file(location: str, source: str, name: str | None = None, fmt: str | None = None) -> SourceImport:
    fmt = fmt or file_format(location)
    if fmt not in READERS:
        raise ValueError(f"unknown format {fmt!r}; use parquet, csv or json")
    result = SourceImport(source=SourceSystem(name=source, type=SourceType.FILE), partial=True)
    attributes = []
    for column, source_type, nullable in describe(location, fmt):
        logical, exact = sql_to_logical(source_type)
        if not exact:
            result.diagnostics.append(warning("MK404", f"{column}: unknown type {source_type!r}; using string"))
        attributes.append(Attribute(name=safe_name(column), type=logical, source_type=source_type, nullable=nullable))
    result.entities.append(
        Entity(
            name=safe_name(name) if name else table_name(location),
            source=source,
            attributes=attributes,
            properties={"location": location, "format": fmt},
        )
    )
    return result
