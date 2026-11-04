"""The extract code: REST paging, auth and increments, folders, protobuf framing, and the landing area."""

import base64
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from murakumo import extract

ROWS = [{"id": i, "city": "Oslo" if i % 2 else "Lima", "updated_at": f"2026-11-01T{i:02d}:00:00"} for i in range(25)]


class Api(BaseHTTPRequestHandler):
    """A small API: bearer, API key or basic auth; page, offset, cursor or link paging; since=."""

    calls: list[str] = []
    fail_first = 0

    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        Api.calls.append(self.path)
        if Api.fail_first:
            Api.fail_first -= 1
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.end_headers()
            return
        ok = {
            "/bearer": self.headers.get("Authorization") == "Bearer t0ken",
            "/key": query.get("api_key") == "k3y",
            "/basic": self.headers.get("Authorization") == "Basic " + base64.b64encode(b"ann:pw").decode(),
        }.get(url.path.rsplit("/", 1)[0] if url.path.count("/") > 1 else url.path, True)
        if not ok:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error": "who?"}')
            return
        rows = [r for r in ROWS if r["updated_at"] > query.get("since", "")]
        size = int(query.get("size", 10))
        kind = url.path.rsplit("/", 1)[-1]
        headers = {}
        if kind == "page":
            start = (int(query.get("page", 1)) - 1) * size
        elif kind == "offset":
            start = int(query.get("offset", 0))
        else:  # cursor and link: the cursor is the next row's index
            start = int(query.get("cursor", 0))
        chunk = rows[start : start + size]
        body: dict = {"data": {"items": chunk}}
        following = start + size if start + size < len(rows) else None
        if kind == "cursor":
            body["meta"] = {"next": following}
        if kind == "link" and following is not None:
            host = f"http://127.0.0.1:{self.server.server_address[1]}"
            headers["Link"] = f'<{host}/link?cursor={following}&size={size}>; rel="next"'
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def api():
    server = HTTPServer(("127.0.0.1", 0), Api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def rest(base, path, paging, auth=None, since=None):
    return {
        "source": "weather",
        "landing": "landing",
        "connection": {
            "kind": "rest",
            "base_url": base,
            "auth": auth or {"type": "none"},
            "paging": paging,
            "endpoints": [
                {
                    "table": "readings",
                    "path": path,
                    "records": "data.items",
                    **({"since_param": since} if since else {}),
                }
            ],
        },
        "watermarks": {"readings": "updated_at"},
    }


def landed(tmp_path):
    return [
        json.loads(line)
        for f in sorted((tmp_path / "landing" / "weather" / "readings").glob("*.jsonl"))
        for line in f.read_text().splitlines()
    ]


@pytest.mark.parametrize(
    "path,paging",
    [
        ("/page", {"type": "page", "size_param": "size", "size": 10}),
        ("/offset", {"type": "offset", "size_param": "size", "size": 10}),
        ("/cursor", {"type": "cursor", "cursor_path": "meta.next", "size_param": "size", "size": 10}),
        ("/link", {"type": "link", "size_param": "size", "size": 10}),
    ],
)
def test_every_paging_kind_fetches_every_record(api, tmp_path, path, paging):
    done = extract.run(rest(api, path, paging), here=tmp_path)
    assert done["readings"][0] == 25
    assert [r["id"] for r in landed(tmp_path)] == list(range(25))


def test_auth_kinds_and_secrets_from_the_environment(api, tmp_path, monkeypatch):
    paging = {"type": "none"}
    with pytest.raises(extract.ExtractError, match="set the environment variable API_TOKEN"):
        extract.run(rest(api, "/bearer/x", paging, {"type": "bearer", "env": "API_TOKEN"}), here=tmp_path)
    monkeypatch.setenv("API_TOKEN", "t0ken")
    assert (
        extract.run(rest(api, "/bearer/x", paging, {"type": "bearer", "env": "API_TOKEN"}), here=tmp_path)["readings"][
            0
        ]
        == 10
    )
    monkeypatch.setenv("API_KEY", "k3y")
    key = {"type": "api_key", "env": "API_KEY", "name": "api_key", "location": "query"}
    assert extract.run(rest(api, "/key/x", paging, key), here=tmp_path)["readings"][0] == 10
    monkeypatch.setenv("API_USER", "ann")
    monkeypatch.setenv("API_PASSWORD", "wrong")
    basic = {"type": "basic", "user_env": "API_USER", "password_env": "API_PASSWORD"}
    with pytest.raises(extract.ExtractError, match="401"):
        extract.run(rest(api, "/basic/x", paging, basic), here=tmp_path)


def test_increments_ask_only_for_what_is_newer(api, tmp_path):
    settings = rest(api, "/page", {"type": "page", "size_param": "size", "size": 10}, since="since")
    assert extract.run(settings, here=tmp_path)["readings"][0] == 25
    state = json.loads((tmp_path / "landing" / "weather" / "_state.json").read_text())
    assert state["readings"]["watermark"] == max(r["updated_at"] for r in ROWS)
    again = extract.run(settings, here=tmp_path)
    assert again["readings"] == (0, [])  # nothing newer: no file written


def test_rate_limits_are_waited_out(api, tmp_path, monkeypatch):
    monkeypatch.setattr(extract.time, "sleep", lambda s: None)
    Api.fail_first = 2
    assert extract.run(rest(api, "/page", {"type": "none"}), here=tmp_path)["readings"][0] == 10


def test_preview_is_the_first_page(api):
    settings = rest(api, "/page", {"type": "page", "size_param": "size", "size": 5})
    result = extract.preview(settings["connection"], settings["connection"]["endpoints"][0])
    assert result["status"] == 200 and result["records"] == 5 and result["sample"][0]["id"] == 0


def test_folders_copy_each_new_file_once(tmp_path):
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    (incoming / "trips_1.csv").write_text("a\n1\n")
    (incoming / "other.csv").write_text("a\n9\n")
    settings = {
        "source": "partner",
        "root": "..",
        "landing": "landing",
        "connection": {
            "kind": "files",
            "location": "incoming",
            "files": [{"table": "trips", "pattern": "trips_*.csv"}],
        },
    }
    here = tmp_path / "settings"  # relative paths start from root, from the settings file's folder
    first = extract.run(settings, here=here)
    assert first["trips"][0] == 1
    assert extract.run(settings, here=here)["trips"][0] == 0
    (incoming / "trips_2.csv").write_text("a\n2\n")
    assert extract.run(settings, here=here)["trips"][0] == 1
    names = sorted(p.name for p in (tmp_path / "landing" / "partner" / "trips").iterdir())
    assert len(names) == 2 and all(n.startswith("trips_") and n.endswith(".csv") for n in names)


def test_length_delimited_messages_are_split():
    def frame(payload: bytes) -> bytes:
        size, out = len(payload), b""
        while True:
            byte = size & 0x7F
            size >>= 7
            out += bytes([byte | (0x80 if size else 0)])
            if not size:
                return out + payload

    big = b"x" * 300  # a two-byte length
    data = frame(b"one") + frame(big) + frame(b"")
    assert list(extract._delimited(data)) == [b"one", big, b""]


def test_runs_standalone_from_its_settings_file(api, tmp_path):
    import subprocess
    import sys

    folder = tmp_path / "generated" / "ingestion" / "python"
    (folder / "sources").mkdir(parents=True)
    script = folder / "extract.py"
    script.write_text(Path(extract.__file__).read_text())
    settings = {**rest(api, "/page", {"type": "page", "size_param": "size", "size": 10}), "root": "../../../.."}
    (folder / "sources" / "weather.json").write_text(json.dumps(settings))
    done = subprocess.run(
        [sys.executable, str(script), str(folder / "sources" / "weather.json")], capture_output=True, text=True, cwd="/"
    )
    assert done.returncode == 0, done.stderr
    assert "weather.readings: 25 new row(s)" in done.stdout
    assert len(landed(tmp_path)) == 25


class Zones(BaseHTTPRequestHandler):
    rows = [{"LocationID": i, "Borough": "Queens", "Zone": f"Zone {i}", "service_zone": "Boro Zone"} for i in (1, 2, 3)]

    def do_GET(self):  # noqa: N802
        payload = json.dumps({"data": Zones.rows}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def test_ingest_then_load_then_build(tmp_path):
    pytest.importorskip("dbt.adapters.duckdb")
    import duckdb
    from typer.testing import CliRunner

    from murakumo import stack
    from murakumo.cli import app
    from murakumo.scaffold import init_project

    server = HTTPServer(("127.0.0.1", 0), Zones)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        project = init_project(tmp_path / "taxi")
        connection = {
            "kind": "rest",
            "base_url": f"http://127.0.0.1:{server.server_address[1]}",
            "endpoints": [{"table": "taxi_zone_lookup", "path": "/zones", "records": "data"}],
        }
        stack.set_connection(project, "nyc_tlc", connection)
        runner = CliRunner()
        fetched = runner.invoke(app, ["ingest", "-p", str(project.root)])
        assert fetched.exit_code == 0, fetched.output
        assert "nyc_tlc.taxi_zone_lookup: 3 new" in fetched.output
        loaded = runner.invoke(app, ["load", "-p", str(project.root)])
        assert loaded.exit_code == 0, loaded.output
        assert "loaded taxi_zone_lookup: 3 rows" in loaded.output

        Zones.rows = Zones.rows + [{"LocationID": 4, "Borough": "Bronx", "Zone": "Zone 4", "service_zone": "Boro Zone"}]
        built = runner.invoke(app, ["build", "--ingest", "-p", str(project.root)])
        assert built.exit_code == 0, built.output
        assert "fetched nyc_tlc.taxi_zone_lookup: 4 new" in built.output
        con = duckdb.connect(str(project.root / "taxi.duckdb"), read_only=True)
        try:  # the zones table loads in full: the latest fetch replaces it
            assert con.execute("select count(*) from raw_nyc_tlc.taxi_zone_lookup").fetchone() == (4,)
        finally:
            con.close()
    finally:
        server.shutdown()
