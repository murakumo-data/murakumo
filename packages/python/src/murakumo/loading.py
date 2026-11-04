"""Loading only what is new: files are remembered by their content, so a folder can be loaded again
and again and each file goes in once.

A ``--data`` location can be a file, a folder (every data file in it) or a glob
(``landing/trips_*.parquet``). Each file loaded is recorded in ``<audit>.loaded_files`` with its
size and a SHA-256 of its content; a file whose content was loaded before is skipped, even renamed.

How the new rows meet the old depends on the raw table's ``load`` in the model:

- ``full``: the table is replaced by every file given, when any of them changed;
- ``append`` (and ``cdc``, ``streaming``): the new files' rows are added;
- ``incremental``: the new rows replace the rows with the same key, the latest of each key (by the
  table's watermark column, else by load time) kept.
"""

from __future__ import annotations

import glob
import hashlib
from pathlib import Path

from murakumo.importers.files import READERS
from murakumo.ir.model import Entity, LoadPattern

LOCAL_FORMATS = {f".{name}" for name in READERS} | {".tsv", ".jsonl", ".ndjson"}


def remote(location: str) -> bool:
    """A location read where it is (a URL, a warehouse table), not a local file to remember."""
    return "://" in location or location.startswith("snowflake:")


def expand(location: str) -> list[str]:
    """The files a location means: itself, the data files in a folder, or a glob's matches, sorted."""
    if remote(location):
        return [location]
    path = Path(location)
    if path.is_dir():
        return sorted(str(p) for p in path.iterdir() if p.is_file() and p.suffix.lower() in LOCAL_FORMATS)
    if any(c in location for c in "*?["):
        return sorted(p for p in glob.glob(location) if Path(p).is_file())
    return [location]


def fingerprint(path: str) -> str:
    """A SHA-256 of a file's content: the same file under another name is the same file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def mode(entity: Entity) -> str:
    """How a raw table takes new rows: full, append or incremental."""
    if entity.load is LoadPattern.FULL:
        return "full"
    if entity.load is LoadPattern.INCREMENTAL and any(a.key and a.key.value == "primary" for a in entity.attributes):
        return "incremental"
    return "append"


def keys(entity: Entity) -> list[str]:
    return [a.name for a in entity.attributes if a.key and a.key.value == "primary"]


def loaded_files_sql(dialect: str, audit: str) -> str:
    text, stamp, number = (
        ("varchar", "timestamp", "bigint") if dialect == "duckdb" else ("varchar", "timestamp_ntz", "number")
    )
    return (
        f"create schema if not exists {audit};\n"
        f"create table if not exists {audit}.loaded_files (table_name {text}, path {text}, size {number}, "
        f"sha256 {text}, rows_ingested {number}, batch_id {text}, loaded_at {stamp});\n"
    )


def record_file_sql(audit: str, table: str, path: str, size: int, sha: str, rows: int, batch: str) -> str:
    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    return (
        f"insert into {audit}.loaded_files values ({literal(table)}, {literal(path)}, {int(size)}, "
        f"{literal(sha)}, {int(rows)}, {literal(batch)}, current_timestamp);\n"
    )


__all__ = ["expand", "fingerprint", "keys", "loaded_files_sql", "mode", "record_file_sql", "remote"]
