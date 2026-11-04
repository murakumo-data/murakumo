"""Fetch new data from a source into the landing area, as files the load picks up.

This file stands on its own: it imports nothing from Murakumo, so it runs wherever the platform
runs (a laptop, an Airflow worker, AWS Lambda, a Glue Python shell job). Murakumo copies it into
``generated/ingestion/python/extract.py`` with one settings file per source beside it
(``sources/<source>.json``, written from murakumo.yaml), and runs it for ``murakumo ingest``.

    python extract.py sources/weather_api.json                 # every table of the source
    python extract.py sources/weather_api.json --table hourly  # one table
    python extract.py sources/weather_api.json --landing s3://my-bucket/landing

Each run writes new files under ``<landing>/<source>/<table>/``, named
``<table>_<when it ran>_...``, and remembers where it got to in
``<landing>/<source>/_state.json``: the latest watermark per table (for ``since_param`` and
``incremental_column``) and the files already copied (SFTP, folders). A run that finds nothing new
writes nothing. Secrets come from environment variables named in the settings, never the file.

It runs on Python 3.9 and later (AWS Glue's Python shell jobs are 3.9). Only the standard library
is needed for REST APIs and local folders. SFTP needs ``paramiko``,
databases ``sqlalchemy`` (and the database's driver), S3 ``boto3``, protobuf ``protobuf`` and
``grpcio-tools``.
"""

# ruff: noqa: UP017, B905 - runs on Python 3.9 too (no datetime.UTC, no zip(strict=))
from __future__ import annotations

import argparse
import base64
import fnmatch
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

TIMEOUT_SECONDS = 60
RETRIES = 4
ROWS_PER_FILE = 50_000
USER_AGENT = "murakumo-extract/1"


class ExtractError(Exception):
    pass


def secret(name: str | None, what: str) -> str:
    if not name:
        raise ExtractError(f"{what}: name the environment variable that holds it")
    value = os.environ.get(name)
    if not value:
        raise ExtractError(f"{what}: set the environment variable {name}")
    return value


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return str(value)


# The landing area: a local folder, or S3 --------------------------------------------------------


class Landing:
    def __init__(self, location: str, source: str):
        self.location = location.rstrip("/")
        self.source = source
        self.stamp = _stamp()  # one per run: a run's files share it, so the latest run's are known
        self.s3 = self.location.startswith("s3://")
        if self.s3:
            try:
                import boto3  # type: ignore[import-not-found]
            except ImportError:
                raise ExtractError("landing in S3 needs boto3: pip install boto3") from None
            self.client = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
            self.bucket, _, self.prefix = self.location.removeprefix("s3://").partition("/")

    def path(self, *parts: str) -> str:
        return "/".join([self.location, self.source, *parts])

    def _key(self, *parts: str) -> str:
        return "/".join(p for p in [self.prefix, self.source, *parts] if p)

    def write(self, table: str, name: str, data: bytes) -> str:
        if self.s3:
            self.client.put_object(Bucket=self.bucket, Key=self._key(table, name), Body=data)
        else:
            folder = Path(self.path(table))
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / f".{name}.part"
            tmp.write_bytes(data)
            tmp.replace(folder / name)  # the load never sees half a file
        return self.path(table, name)

    def copy_in(self, table: str, name: str, local: Path) -> str:
        if self.s3:
            self.client.upload_file(str(local), self.bucket, self._key(table, name))
        else:
            folder = Path(self.path(table))
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, folder / f".{name}.part")
            (folder / f".{name}.part").replace(folder / name)
        return self.path(table, name)

    def read_state(self) -> dict[str, Any]:
        try:
            if self.s3:
                body = self.client.get_object(Bucket=self.bucket, Key=self._key("_state.json"))["Body"].read()
                return json.loads(body)
            return json.loads(Path(self.path("_state.json")).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - no state yet: start from the beginning
            return {}

    def write_state(self, state: dict[str, Any]) -> None:
        data = json.dumps(state, indent=2, sort_keys=True, default=_json_default).encode()
        if self.s3:
            self.client.put_object(Bucket=self.bucket, Key=self._key("_state.json"), Body=data)
        else:
            Path(self.path()).mkdir(parents=True, exist_ok=True)
            Path(self.path("_state.json")).write_bytes(data)


class Writer:
    """Records written as JSON lines, a file per ROWS_PER_FILE records."""

    def __init__(self, landing: Landing, table: str):
        self.landing, self.table = landing, table
        self.stamp = landing.stamp
        self.rows: list[bytes] = []
        self.files: list[str] = []
        self.count = 0

    def add(self, record: dict[str, Any]) -> None:
        self.rows.append(json.dumps(record, default=_json_default, ensure_ascii=False).encode())
        self.count += 1
        if len(self.rows) >= ROWS_PER_FILE:
            self.flush()

    def flush(self) -> None:
        if self.rows:
            name = f"{self.table}_{self.stamp}_{len(self.files) + 1:04d}.jsonl"
            self.files.append(self.landing.write(self.table, name, b"\n".join(self.rows) + b"\n"))
            self.rows = []


def _latest(current: Any, value: Any) -> Any:
    if value is None:
        return current
    return value if current is None or str(value) > str(current) else current


# REST APIs ----------------------------------------------------------------------------------------


def records_at(data: Any, path: str | None) -> list[dict[str, Any]]:
    """The records in a response: at a dotted path (data.items), a list, one object, or columnar arrays."""
    for part in [p for p in (path or "").split(".") if p]:
        if not isinstance(data, dict) or part not in data:
            raise ExtractError(f"no {part!r} in the response at {path!r}")
        data = data[part]
    if isinstance(data, list):
        return [r if isinstance(r, dict) else {"value": r} for r in data]
    if isinstance(data, dict):
        values = list(data.values())
        if values and all(isinstance(v, list) for v in values) and len({len(v) for v in values}) == 1:
            return [dict(zip(data, row)) for row in zip(*values)]
        return [data]
    return []


def _at(data: Any, path: str | None) -> Any:
    for part in [p for p in (path or "").split(".") if p]:
        data = data.get(part) if isinstance(data, dict) else None
    return data


class Rest:
    def __init__(self, connection: dict[str, Any]):
        self.c = connection
        self.auth = connection.get("auth") or {}
        self.paging = connection.get("paging") or {}
        self.token: str | None = None
        self.last_call = 0.0

    def _oauth_token(self) -> str:
        if self.token is None:
            form = {"grant_type": "client_credentials"}
            if self.auth.get("scope"):
                form["scope"] = self.auth["scope"]
            client = secret(self.auth.get("client_id_env"), "the OAuth client id")
            password = secret(self.auth.get("client_secret_env"), "the OAuth client secret")
            request = urllib.request.Request(
                self.auth.get("token_url") or "",
                data=urllib.parse.urlencode(form).encode(),
                headers={
                    "Authorization": "Basic " + base64.b64encode(f"{client}:{password}".encode()).decode(),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": USER_AGENT,
                },
            )
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                self.token = json.loads(response.read())["access_token"]
        return self.token

    def _authorise(self, headers: dict[str, str], params: dict[str, str]) -> None:
        kind = self.auth.get("type", "none")
        if kind == "api_key":
            key = secret(self.auth.get("env"), "the API key")
            (params if self.auth.get("location") == "query" else headers)[self.auth.get("name", "Authorization")] = key
        elif kind == "bearer":
            headers["Authorization"] = "Bearer " + secret(self.auth.get("env"), "the token")
        elif kind == "basic":
            user = secret(self.auth.get("user_env"), "the user name")
            password = secret(self.auth.get("password_env"), "the password")
            headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        elif kind == "oauth2":
            headers["Authorization"] = "Bearer " + self._oauth_token()

    def call(self, url: str, method: str = "GET", params: dict[str, Any] | None = None, body: Any = None):
        """One request, retried on 429 and 5xx with a growing wait. Returns (status, headers, JSON)."""
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **(self.c.get("headers") or {})}
        query = {k: str(v) for k, v in (params or {}).items() if v is not None}
        self._authorise(headers, query)
        if query:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        rate = self.c.get("requests_per_second")
        for attempt in range(RETRIES + 1):
            if rate:
                time.sleep(max(0.0, self.last_call + 1 / rate - time.monotonic()))
            self.last_call = time.monotonic()
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                    text = response.read().decode("utf-8", "replace")
                    return response.status, dict(response.headers), json.loads(text) if text.strip() else None
            except urllib.error.HTTPError as exc:
                if (exc.code == 429 or exc.code >= 500) and attempt < RETRIES:
                    wait = exc.headers.get("Retry-After")
                    time.sleep(float(wait) if wait and wait.isdigit() else 2**attempt)
                    continue
                detail = exc.read().decode("utf-8", "replace")[:500]
                raise ExtractError(f"{method} {url.split('?')[0]}: {exc.code} {exc.reason} {detail}".strip()) from None
            except urllib.error.URLError as exc:
                if attempt < RETRIES:
                    time.sleep(2**attempt)
                    continue
                raise ExtractError(f"{method} {url.split('?')[0]}: {exc.reason}") from None
            except json.JSONDecodeError:
                raise ExtractError(f"{method} {url.split('?')[0]}: the response is not JSON") from None
        raise ExtractError("unreachable")  # pragma: no cover

    def pages(self, endpoint: dict[str, Any], since: Any = None, max_pages: int | None = None):
        """Each page's (status, headers, records, response), following the paging."""
        p = self.paging
        kind = p.get("type", "none")
        url = self.c["base_url"].rstrip("/") + "/" + endpoint.get("path", "").lstrip("/")
        params = dict(endpoint.get("params") or {})
        if since is not None and endpoint.get("since_param"):
            params[endpoint["since_param"]] = since
        if kind != "none" and p.get("size_param"):
            params[p["size_param"]] = p.get("size", 100)
        number = p.get("start", 1) if kind == "page" else 0
        limit = min(x for x in (max_pages, p.get("max_pages"), 10**9) if x)
        for _ in range(limit):
            if kind == "page":
                params[p.get("param") or "page"] = number
            elif kind == "offset":
                params[p.get("param") or "offset"] = number
            status, headers, data = self.call(url, endpoint.get("method", "GET"), params, endpoint.get("body"))
            records = records_at(data, endpoint.get("records")) if data is not None else []
            yield status, headers, records, data
            if kind == "none" or not records:
                return
            if kind == "page":
                number += 1
            elif kind == "offset":
                number += len(records)
            elif kind == "cursor":
                cursor = _at(data, p.get("cursor_path"))
                if not cursor:
                    return
                params[p.get("param") or "cursor"] = cursor
            elif kind == "link":
                following = _next_link(headers.get("Link") or headers.get("link") or "")
                if not following:
                    return
                url, params = following, {}
            if kind in {"page", "offset"} and len(records) < int(p.get("size", 100)):
                return


def _next_link(header: str) -> str | None:
    for part in header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip().strip("<>")
    return None


def extract_rest(settings: dict[str, Any], landing: Landing, state: dict[str, Any], only: str | None):
    rest = Rest(settings["connection"])
    done = {}
    for endpoint in settings["connection"].get("endpoints") or []:
        table = endpoint["table"]
        if only and table != only:
            continue
        watermark = (settings.get("watermarks") or {}).get(table)
        table_state = state.setdefault(table, {})
        writer, latest = Writer(landing, table), table_state.get("watermark")
        for _status, _headers, records, _data in rest.pages(endpoint, since=latest):
            for record in records:
                writer.add(record)
                if watermark:
                    latest = _latest(latest, record.get(watermark))
        writer.flush()
        if latest is not None:
            table_state["watermark"] = latest
        done[table] = (writer.count, writer.files)
    return done


# Files: a folder, S3, or SFTP --------------------------------------------------------------------


def _list_local(location: str, pattern: str) -> list[tuple[str, int, float]]:
    root = Path(location)
    found = []
    for path in sorted(root.glob(pattern)):
        if path.is_file():
            stat = path.stat()
            found.append((str(path.relative_to(root)), stat.st_size, stat.st_mtime))
    return found


def _list_s3(location: str, pattern: str):
    import boto3  # type: ignore[import-not-found]

    bucket, _, prefix = location.removeprefix("s3://").partition("/")
    client = boto3.client("s3", region_name=os.environ.get("AWS_REGION"))
    found = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix.rstrip("/") + "/"):
        for item in page.get("Contents", []):
            name = item["Key"][len(prefix.rstrip("/")) + 1 :]
            if fnmatch.fnmatch(name, pattern):
                found.append((name, item["Size"], item["LastModified"].timestamp()))
    return client, bucket, prefix, found


def _copy_new(table_state: dict[str, Any], found, fetch, landing: Landing, table: str) -> tuple[int, list[str]]:
    """Copy the files not copied before (by name, size and time), oldest first."""
    seen = table_state.setdefault("files", {})
    files = []
    for name, size, mtime in sorted(found, key=lambda f: f[2]):
        mark = f"{size}:{int(mtime)}"
        if seen.get(name) == mark:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / Path(name).name
            fetch(name, local)
            files.append(landing.copy_in(table, f"{table}_{landing.stamp}_{Path(name).name}", local))
        seen[name] = mark
    return len(files), files


def extract_files(settings: dict[str, Any], landing: Landing, state: dict[str, Any], only: str | None):
    c = settings["connection"]
    done = {}
    for fileset in c.get("files") or []:
        table = fileset["table"]
        if only and table != only:
            continue
        location = c["location"].rstrip("/")
        if location.startswith("s3://"):
            client, bucket, prefix, found = _list_s3(location, fileset["pattern"])

            def fetch(name: str, local: Path, client=client, bucket=bucket, prefix=prefix) -> None:
                client.download_file(bucket, f"{prefix.rstrip('/')}/{name}", str(local))
        else:
            found = _list_local(location, fileset["pattern"])

            def fetch(name: str, local: Path, location=location) -> None:
                shutil.copyfile(Path(location) / name, local)

        done[table] = _copy_new(state.setdefault(table, {}), found, fetch, landing, table)
    return done


def extract_sftp(settings: dict[str, Any], landing: Landing, state: dict[str, Any], only: str | None):
    try:
        import paramiko  # type: ignore[import-not-found]
    except ImportError:
        raise ExtractError("SFTP needs paramiko: pip install paramiko") from None
    c = settings["connection"]
    transport = paramiko.Transport((c["host"], int(c.get("port", 22))))
    user = secret(c.get("user_env"), "the SFTP user")
    if c.get("key_file_env"):
        key = paramiko.PKey.from_path(secret(c["key_file_env"], "the SFTP key file"))
        transport.connect(username=user, pkey=key)
    else:
        transport.connect(username=user, password=secret(c.get("password_env"), "the SFTP password"))
    sftp = paramiko.SFTPClient.from_transport(transport)
    done = {}
    try:
        root = c.get("path", "/").rstrip("/") or "/"
        listing = sftp.listdir_attr(root)
        for fileset in c.get("files") or []:
            table = fileset["table"]
            if only and table != only:
                continue
            found = [
                (f.filename, f.st_size or 0, f.st_mtime or 0)
                for f in listing
                if fnmatch.fnmatch(f.filename, fileset["pattern"])
            ]

            def fetch(name: str, local: Path) -> None:
                sftp.get(f"{root.rstrip('/')}/{name}", str(local))

            done[table] = _copy_new(state.setdefault(table, {}), found, fetch, landing, table)
    finally:
        sftp.close()
        transport.close()
    return done


# Databases ----------------------------------------------------------------------------------------


def extract_database(settings: dict[str, Any], landing: Landing, state: dict[str, Any], only: str | None):
    try:
        import sqlalchemy  # type: ignore[import-not-found]
    except ImportError:
        raise ExtractError("databases need sqlalchemy and the database's driver: pip install sqlalchemy") from None
    c = settings["connection"]
    engine = sqlalchemy.create_engine(secret(c.get("url_env"), "the database URL"))
    done = {}
    with engine.connect() as connection:
        for query in c.get("tables") or []:
            table = query["table"]
            if only and table != only:
                continue
            table_state = state.setdefault(table, {})
            source = query["source"]
            sql = source if source.lstrip().lower().startswith("select") else f"select * from {source}"
            column, latest, bind = query.get("incremental_column"), table_state.get("watermark"), {}
            if column and latest is not None:
                sql = f"select * from ({sql}) as source where {column} > :since order by {column}"
                bind["since"] = latest
            writer = Writer(landing, table)
            result = connection.execution_options(stream_results=True).execute(sqlalchemy.text(sql), bind)
            for row in result.mappings():
                record = dict(row)
                writer.add(record)
                if column:
                    latest = _latest(latest, record.get(column))
            writer.flush()
            if latest is not None:
                table_state["watermark"] = latest
            done[table] = (writer.count, writer.files)
    return done


# Protobuf -----------------------------------------------------------------------------------------


def _messages_module(proto: str):
    """The .proto file compiled to Python with grpcio-tools, and imported."""
    try:
        from grpc_tools import protoc  # type: ignore[import-not-found]
    except ImportError:
        raise ExtractError("protobuf needs protobuf and grpcio-tools: pip install protobuf grpcio-tools") from None
    import importlib.util

    proto_path = Path(proto).resolve()
    out = Path(tempfile.mkdtemp())
    code = protoc.main(["protoc", f"-I{proto_path.parent}", f"--python_out={out}", str(proto_path)])
    if code != 0:
        raise ExtractError(f"protoc could not compile {proto}")
    module_file = out / f"{proto_path.stem}_pb2.py"
    spec = importlib.util.spec_from_file_location(f"{proto_path.stem}_pb2", module_file)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _delimited(data: bytes):
    """Length-delimited messages: each a varint length, then that many bytes."""
    position = 0
    while position < len(data):
        length = shift = 0
        while True:
            byte = data[position]
            position += 1
            length |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
            shift += 7
        yield data[position : position + length]
        position += length


def extract_protobuf(settings: dict[str, Any], landing: Landing, state: dict[str, Any], only: str | None):
    from google.protobuf import json_format  # type: ignore[import-not-found]

    c = settings["connection"]
    module = _messages_module(c["proto"])
    done = {}
    for message in c.get("messages") or []:
        table = message["table"]
        if only and table != only:
            continue
        kind = getattr(module, message["message"])
        writer = Writer(landing, table)
        location = c["location"].rstrip("/")
        found = _list_local(location, message["pattern"]) if "://" not in location else []
        seen = state.setdefault(table, {}).setdefault("files", {})
        for name, size, mtime in found:
            mark = f"{size}:{int(mtime)}"
            if seen.get(name) == mark:
                continue
            for raw in _delimited((Path(location) / name).read_bytes()):
                parsed = kind()
                parsed.ParseFromString(raw)
                writer.add(json_format.MessageToDict(parsed, preserving_proto_field_name=True))
            seen[name] = mark
        writer.flush()
        done[table] = (writer.count, writer.files)
    return done


# Running ------------------------------------------------------------------------------------------

KINDS = {
    "rest": extract_rest,
    "files": extract_files,
    "sftp": extract_sftp,
    "database": extract_database,
    "protobuf": extract_protobuf,
}


def _resolve(settings: dict[str, Any], here: Path | None) -> dict[str, Any]:
    """Relative paths in the settings are from the project's folder: ``root``, from the settings file."""
    base = (here / settings.get("root", ".")).resolve() if here else Path.cwd()
    settings = json.loads(json.dumps(settings))

    def local(value: str) -> str:
        return value if "://" in value or Path(value).is_absolute() else str(base / value)

    settings["landing"] = local(settings.get("landing", "landing"))
    c = settings["connection"]
    for key in ("location", "proto"):
        if key in c:
            c[key] = local(c[key])
    return settings


def run(
    settings: dict[str, Any], landing: str | None = None, only: str | None = None, here: Path | None = None
) -> dict[str, tuple[int, list[str]]]:
    """Fetch what is new for each of a source's tables: {table: (records or files, files written)}.
    ``here`` is the settings file's folder, which relative paths start from."""
    settings = _resolve(settings, here)
    kind = settings["connection"]["kind"]
    place = Landing(landing or os.environ.get("MURAKUMO_LANDING") or settings["landing"], settings["source"])
    state = place.read_state()
    done = KINDS[kind](settings, place, state, only)
    place.write_state(state)
    return done


def preview(connection: dict[str, Any], endpoint: dict[str, Any]) -> dict[str, Any]:
    """One request of a REST endpoint (its first page): what came back, for trying a request out."""
    rest = Rest(connection)
    started = time.monotonic()
    status, headers, records, data = next(rest.pages(endpoint, max_pages=1))
    return {
        "status": status,
        "milliseconds": round((time.monotonic() - started) * 1000),
        "headers": {k: v for k, v in headers.items() if k.lower() in {"content-type", "link", "x-total-count"}},
        "records": len(records),
        "sample": records[:20],
        "response": data,
    }


def handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """AWS Lambda: run the source named by MURAKUMO_SOURCE (its settings file is beside this one)."""
    source = (event or {}).get("source") or os.environ["MURAKUMO_SOURCE"]
    folder = Path(__file__).parent / "sources"
    settings = json.loads((folder / f"{source}.json").read_text(encoding="utf-8"))
    done = run(settings, only=(event or {}).get("table"), here=folder)
    return {table: {"rows": count, "files": files} for table, (count, files) in done.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch new data from a source into the landing area.")
    parser.add_argument("settings", help="the source's settings file, sources/<source>.json")
    parser.add_argument("--table", help="only this table")
    parser.add_argument("--landing", help="where files land (default: the settings' landing, or MURAKUMO_LANDING)")
    args = parser.parse_args(argv)
    settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    try:
        done = run(settings, args.landing, args.table, here=Path(args.settings).parent)
    except ExtractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for table, (count, files) in done.items():
        unit = "row(s)" if settings["connection"]["kind"] in {"rest", "database", "protobuf"} else "file(s)"
        print(f"{settings['source']}.{table}: {count} new {unit}, {len(files)} file(s) landed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
