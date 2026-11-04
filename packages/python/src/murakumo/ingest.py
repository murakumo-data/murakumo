"""Fetching the sources' data here: ``murakumo ingest``, and the landed files a build loads.

``murakumo ingest`` runs the same extract code the platform runs (``generated/ingestion/python``),
for each source with a connection, into the landing area. ``murakumo load`` and
``murakumo build --ingest`` then load what has landed: each table's folder,
``<landing>/<source>/<table>/``, whose files are loaded once each.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from murakumo import extract
from murakumo.generate.ingestion import connected, settings
from murakumo.project import Project

SETTINGS_DIR = "generated/ingestion/python/sources"


class IngestError(Exception):
    pass


@dataclass
class Fetched:
    source: str
    tables: dict[str, tuple[int, list[str]]] = field(default_factory=dict)
    error: str | None = None


def _declaration(project: Project, source: str):
    match = next((d for d in connected(project) if d.name.lower() == source.lower()), None)
    if match is None:
        names = ", ".join(d.name for d in connected(project)) or "none yet"
        raise IngestError(f"no source {source!r} with a connection; sources with one: {names}")
    return match


def run(project: Project, source: str | None = None, table: str | None = None) -> list[Fetched]:
    """Fetch what is new from each source with a connection (or one), into the landing area."""
    if project.manifest.stack.extract == "none":
        raise IngestError("the stack's extract is none: something else fetches the data")
    declarations = [_declaration(project, source)] if source else connected(project)
    if not declarations:
        raise IngestError("no source has a connection yet: add one to a source in murakumo.yaml or the canvas")
    results = []
    for declaration in declarations:
        fetched = Fetched(declaration.name)
        try:
            fetched.tables = extract.run(settings(project, declaration), only=table, here=project.root / SETTINGS_DIR)
        except extract.ExtractError as exc:
            fetched.error = str(exc)
        results.append(fetched)
    return results


def landing_folder(project: Project, source: str, table: str) -> str:
    landing = project.manifest.stack.landing.rstrip("/")
    if "://" in landing:
        return f"{landing}/{source}/{table}/*.jsonl"
    return str((project.root / landing / source / table).resolve())


def landed(project: Project) -> dict[str, str]:
    """Each connected raw table with files landed, and where: {table: location} for --data.

    A table loaded in full (``load: full``) takes only the latest run's files: each run fetched the
    whole of it. Other tables take every file, each loaded once."""
    from murakumo.ir.model import LoadPattern

    data: dict[str, str] = {}
    for declaration in connected(project):
        for table in declaration.tables():
            location = landing_folder(project, declaration.name, table)
            if "://" in location:
                data[table] = location
                continue
            folder = Path(location)
            files = (
                sorted(p.name for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
                if folder.is_dir()
                else []
            )
            if not files:
                continue
            entity = project.model.entity(table)
            stamps = sorted({_run_of(table, name) for name in files} - {None})
            if entity is not None and entity.load is LoadPattern.FULL and stamps:
                data[table] = str(folder / f"{table}_{stamps[-1]}_*")
            else:
                data[table] = location
    return data


def _run_of(table: str, name: str) -> str | None:
    """When the run that landed a file ran: <table>_<YYYYmmddTHHMMSSffffffZ>_..."""
    rest = name[len(table) + 1 :] if name.startswith(f"{table}_") else ""
    stamp = rest.split("_", 1)[0]
    return stamp if len(stamp) == 22 and stamp.endswith("Z") and "T" in stamp else None


def preview(connection: dict, endpoint: dict) -> dict:
    """Try one request of a REST endpoint, for the canvas's request builder and `source test`."""
    try:
        return extract.preview(connection, endpoint)
    except extract.ExtractError as exc:
        raise IngestError(str(exc)) from None


def test(project: Project, source: str) -> list[str]:
    """Check a source's connection without landing anything: what it can see."""
    declaration = _declaration(project, source)
    c: Any = declaration.connection
    lines: list[str] = []
    try:
        if c.kind == "rest":
            data = c.model_dump(exclude_none=True)
            for endpoint in data.get("endpoints", []):
                result = extract.preview(data, endpoint)
                lines.append(
                    f"{endpoint['table']}: {result['status']} in {result['milliseconds']} ms, "
                    f"{result['records']} record(s) on the first page"
                )
        elif c.kind in {"files", "protobuf"}:
            location = c.location
            base = location if "://" in location else str(project.root / location)
            for item in c.files if c.kind == "files" else c.messages:
                if "://" in base:
                    lines.append(f"{item.table}: {base}/{item.pattern} (listed when fetched)")
                else:
                    found = [p for p in Path(base).glob(item.pattern) if p.is_file()]
                    lines.append(f"{item.table}: {len(found)} file(s) match {item.pattern} in {location}")
            if c.kind == "protobuf":
                from murakumo.importers.protobuf import parse

                messages = parse((project.root / c.proto).read_text(encoding="utf-8"))
                lines.append(f"{c.proto}: {len(messages)} message type(s)")
        elif c.kind == "database":
            try:
                import sqlalchemy
            except ImportError:
                raise IngestError("databases need sqlalchemy and the database's driver") from None
            engine = sqlalchemy.create_engine(extract.secret(c.url_env, "the database URL"))
            with engine.connect() as connection:
                for query in c.tables:
                    sql = query.source
                    if not sql.lower().lstrip().startswith("select"):
                        sql = f"select * from {sql}"
                    connection.execute(sqlalchemy.text(f"select 1 from ({sql}) as source where 1 = 0"))
                    lines.append(f"{query.table}: {query.source} is readable")
        elif c.kind == "sftp":
            try:
                import paramiko
            except ImportError:
                raise IngestError("SFTP needs paramiko: pip install paramiko") from None
            transport = paramiko.Transport((c.host, c.port))
            user = extract.secret(c.user_env, "the SFTP user")
            if c.key_file_env:
                key = paramiko.PKey.from_path(extract.secret(c.key_file_env, "the SFTP key file"))
                transport.connect(username=user, pkey=key)
            else:
                transport.connect(username=user, password=extract.secret(c.password_env, "the SFTP password"))
            sftp = paramiko.SFTPClient.from_transport(transport)
            try:
                names = sftp.listdir(c.path)
            finally:
                transport.close()
            import fnmatch

            for item in c.files:
                count = sum(fnmatch.fnmatch(n, item.pattern) for n in names)
                lines.append(f"{item.table}: {count} file(s) match {item.pattern} in {c.path}")
    except extract.ExtractError as exc:
        raise IngestError(str(exc)) from None
    except IngestError:
        raise
    except Exception as exc:  # noqa: BLE001 - any driver's error, said plainly
        raise IngestError(" ".join(str(exc).split()) or type(exc).__name__) from None
    return lines


__all__ = ["Fetched", "IngestError", "landed", "landing_folder", "preview", "run", "test"]
