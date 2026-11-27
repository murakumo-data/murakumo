# ruff: noqa: E501 - the fake API's data and requests read best on one line each
"""Connecting a source without exporting anything: secrets in the project's .env, Microsoft
Dynamics 365 from four things, a pasted request, and fetching every page then only what changed.

The Dynamics tests run against a small fake of the Web API and Microsoft's token endpoint, which
pages by @odata.nextLink and filters by modifiedon as Dynamics does."""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from typer.testing import CliRunner

from murakumo import dynamics, extract, jobs, pasted_request, secret_file
from murakumo.cli import app
from murakumo.project import load_project

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "commerce"

CONTACTS = [
    {
        "@odata.etag": f'W/"{100 + i}"',
        "contactid": f"00000000-0000-0000-0000-00000000000{i}",
        "fullname": f"Person {i}",
        "emailaddress1": f"p{i}@example.com",
        "modifiedon": f"2026-11-{10 + i:02d}T09:00:00Z",
    }
    for i in range(7)
]
LOANS = [
    {
        "vrp_mortgageloanid": f"l{i}",
        "vrp_name": f"Loan {i}",
        "vrp_amount": 1000.5 * i,
        "modifiedon": "2026-11-01T00:00:00Z",
    }
    for i in range(2)
]
ENTITIES = [
    {
        "EntitySetName": "contacts",
        "LogicalName": "contact",
        "DisplayName": {"UserLocalizedLabel": {"Label": "Contact"}},
    },
    {
        "EntitySetName": "vrp_mortgageloans",
        "LogicalName": "vrp_mortgageloan",
        "IsCustomEntity": True,
        "DisplayName": {"UserLocalizedLabel": {"Label": "Mortgage Loan"}},
    },
    {"EntitySetName": None, "LogicalName": "hidden"},
]


class FakeDynamics(BaseHTTPRequestHandler):
    """Microsoft's token endpoint and the Dataverse Web API, as far as Murakumo uses them."""

    rows = {"contacts": CONTACTS, "vrp_mortgageloans": LOANS}
    page = 3
    requests: list[str] = []

    def log_message(self, *args):  # quiet
        pass

    def reply(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode())
        basic = self.headers.get("Authorization", "").removeprefix("Basic ")
        who = base64.b64decode(basic).decode() if basic else ""
        if not self.path.endswith("/oauth2/v2.0/token"):
            return self.reply(404, {})
        if who != "app-id:s3cr$t!":
            return self.reply(
                401, {"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret"}
            )
        if form.get("scope", [""])[0] != f"{self.server.base}/.default":  # type: ignore[attr-defined]
            return self.reply(400, {"error": "invalid_scope", "error_description": "AADSTS70011: wrong scope"})
        return self.reply(200, {"access_token": "token-1", "expires_in": 3599, "token_type": "Bearer"})

    def do_GET(self):  # noqa: N802
        FakeDynamics.requests.append(self.path)
        if self.headers.get("Authorization") != "Bearer token-1":
            return self.reply(401, {"error": {"code": "0x80072560", "message": "not a member of the organization"}})
        parts = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        name = parts.path.removeprefix(dynamics.API + "/")
        if name == "EntityDefinitions":
            return self.reply(200, {"value": ENTITIES})
        if name not in self.rows:
            return self.reply(404, {"error": {"message": f"Resource not found for the segment '{name}'"}})
        rows = self.rows[name]
        if "$filter" in query:
            since = query["$filter"].split(" ge ", 1)[1]
            rows = [r for r in rows if r["modifiedon"] >= since]
        if "$top" in query:
            rows = rows[: int(query["$top"])]
        start = int(query.get("$skiptoken", 0))
        body: dict = {"@odata.context": "$metadata#" + name, "value": rows[start : start + self.page]}
        if start + self.page < len(rows):
            rest = {k: v for k, v in query.items() if k != "$skiptoken"}
            following = "&".join(f"{k}={v}" for k, v in {**rest, "$skiptoken": start + self.page}.items())
            body[dynamics.NEXT] = f"{self.server.base}{parts.path}?{following}"  # type: ignore[attr-defined]
        return self.reply(200, body)


@pytest.fixture
def fake(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeDynamics)
    server.base = f"http://127.0.0.1:{server.server_port}"  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(dynamics, "LOGIN", server.base)  # type: ignore[attr-defined]
    FakeDynamics.requests = []
    yield server.base  # type: ignore[attr-defined]
    server.shutdown()


PREFIXES = ("CRM_", "DYNAMICS_", "WEATHER_", "A_", "TOKEN_", "OPEN_METEO_")


@pytest.fixture
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(PREFIXES):
            monkeypatch.delenv(name, raising=False)
    yield monkeypatch
    for name in list(os.environ):  # what saving a secret set, so it does not reach other tests
        if name.startswith(PREFIXES):
            del os.environ[name]


# The project's .env ---------------------------------------------------------------------------------


def test_env_file_is_read_kept_private_and_out_of_git(tmp_path, clean_env):
    (tmp_path / ".gitignore").write_text("target/\n")
    secret_file.save(tmp_path, "CRM_CLIENT_SECRET", "s3cr$t! with 'quotes'")
    secret_file.save(tmp_path, "CRM_CLIENT_ID", "app-id")
    secret_file.save(tmp_path, "CRM_CLIENT_ID", "app-id-2")  # replaced, not added twice
    text = (tmp_path / ".env").read_text()
    assert text.count("CRM_CLIENT_ID=") == 1
    assert ".env" in (tmp_path / ".gitignore").read_text().splitlines()
    assert "target/" in (tmp_path / ".gitignore").read_text()
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    assert secret_file.read(tmp_path) == {"CRM_CLIENT_SECRET": "s3cr$t! with 'quotes'", "CRM_CLIENT_ID": "app-id-2"}
    # The extract code reads it the same way, with no Murakumo installed.
    clean_env.delenv("CRM_CLIENT_SECRET", raising=False)
    extract.read_env_file(tmp_path)
    assert os.environ["CRM_CLIENT_SECRET"] == "s3cr$t! with 'quotes'"


def test_env_file_formats_people_write(tmp_path, clean_env):
    (tmp_path / ".env").write_text(
        '# comment\nA_ONE = spaced\nexport A_TWO=\'x$y\'\nA_THREE="q\\"w"\nA_FOUR=plain # note\n\nbad line\n'
    )
    assert secret_file.read(tmp_path) == {"A_ONE": "spaced", "A_TWO": "x$y", "A_THREE": 'q"w', "A_FOUR": "plain"}


def test_the_environment_wins_over_the_file(tmp_path, clean_env):
    (tmp_path / ".env").write_text("A_KEY=from-file\nA_OTHER=from-file\n")
    clean_env.setenv("A_KEY", "from-ci")
    assert secret_file.load(tmp_path) == ["A_OTHER"]
    assert os.environ["A_KEY"] == "from-ci"
    status = {s["name"]: s for s in secret_file.status(tmp_path, ["A_KEY", "A_OTHER", "A_NONE"])}
    assert status["A_OTHER"]["from"] == "file" and status["A_NONE"]["set"] is False
    assert secret_file.remove(tmp_path, "A_OTHER") and "A_OTHER" not in secret_file.read(tmp_path)


def test_secret_names_are_checked(tmp_path, clean_env):
    with pytest.raises(secret_file.SecretError):
        secret_file.save(tmp_path, "not a name", "x")
    assert secret_file.variable("veripark-sit", "client_secret") == "VERIPARK_SIT_CLIENT_SECRET"


# A pasted request ------------------------------------------------------------------------------------


def test_a_pasted_curl_command_becomes_a_connection():
    parsed = pasted_request.parse(
        "curl -X GET 'https://api.weather.example/v1/readings?station=all&page=2' "
        "-H 'Authorization: Bearer abc.def' -H 'Accept: application/json' -H 'X-Tenant: north'"
    )
    assert parsed["connection"]["base_url"] == "https://api.weather.example/v1"
    assert parsed["connection"]["auth"] == {"type": "bearer"}
    assert parsed["connection"]["headers"] == {"X-Tenant": "north"}
    assert parsed["endpoint"] == {
        "table": "readings",
        "path": "/readings",
        "method": "GET",
        "params": {"station": "all", "page": "2"},
    }
    assert parsed["secret"] == "abc.def"


def test_a_pasted_url_with_a_key_in_it():
    parsed = pasted_request.parse("https://api.example.com/data/2.5/weather?q=London&appid=KEY123")
    assert parsed["connection"]["auth"] == {"type": "api_key", "name": "appid", "location": "query"}
    assert parsed["secret"] == "KEY123" and parsed["endpoint"]["params"] == {"q": "London"}
    assert pasted_request.parse("https://org.crm11.dynamics.com/api/data/v9.2/accounts")["dynamics"] is True
    with pytest.raises(pasted_request.PastedRequestError):
        pasted_request.parse("not a url")


# Dynamics ----------------------------------------------------------------------------------------------


def test_the_environment_url_however_it_is_pasted():
    for pasted in (
        "allafrica-sit.crm11.dynamics.com",
        "https://allafrica-sit.crm11.dynamics.com/",
        "https://allafrica-sit.crm11.dynamics.com/api/data/v9.2",
        "https://allafrica-sit.crm11.dynamics.com/main.aspx?appid=1",
    ):
        assert dynamics.host(pasted) == "https://allafrica-sit.crm11.dynamics.com"
    conn = dynamics.connection("veripark", "allafrica-sit.crm11.dynamics.com", "tenant-guid")
    assert conn["base_url"] == "https://allafrica-sit.crm11.dynamics.com/api/data/v9.2"
    assert conn["auth"]["scope"] == "https://allafrica-sit.crm11.dynamics.com/.default"
    assert conn["auth"]["token_url"].endswith("/tenant-guid/oauth2/v2.0/token")
    assert conn["auth"]["client_secret_env"] == "VERIPARK_CLIENT_SECRET"
    assert dynamics.is_dynamics(conn) and dynamics.settings_of(conn)["tenant"] == "tenant-guid"
    with pytest.raises(dynamics.DynamicsError):
        dynamics.tenant("tenant id with spaces")


def test_every_page_then_only_what_changed(fake, tmp_path, clean_env):
    clean_env.setenv("CRM_CLIENT_ID", "app-id")
    clean_env.setenv("CRM_CLIENT_SECRET", "s3cr$t!")
    conn = dynamics.connection("crm", fake, "t1", [dynamics.endpoint("contacts")])
    assert [t["entity_set"] for t in dynamics.tables(conn)] == ["vrp_mortgageloans", "contacts"]  # their own first
    settings = {
        "source": "crm",
        "landing": str(tmp_path / "landing"),
        "connection": conn,
        "watermarks": {"contacts": "modifiedon"},
    }
    done = extract.run(settings)
    assert done["contacts"][0] == 7  # three pages of three, followed by @odata.nextLink
    assert sum("$skiptoken" in r for r in FakeDynamics.requests) == 2
    state = json.loads((tmp_path / "landing" / "crm" / "_state.json").read_text())
    assert state["contacts"]["watermark"] == "2026-11-16T09:00:00Z"
    FakeDynamics.requests = []
    again = extract.run(settings)
    assert again["contacts"][0] == 1  # modifiedon ge the latest: only the last row again
    assert any(
        "modifiedon%20ge%202026-11-16T09:00:00Z" in r or "modifiedon+ge+2026-11-16T09" in r
        for r in FakeDynamics.requests
    )


def test_sign_in_problems_are_said_plainly(fake, clean_env):
    clean_env.setenv("CRM_CLIENT_ID", "app-id")
    clean_env.setenv("CRM_CLIENT_SECRET", "wrong")
    conn = dynamics.connection("crm", fake, "t1")
    with pytest.raises(extract.ExtractError) as caught:
        dynamics.tables(conn)
    assert "AADSTS7000215" in str(caught.value)
    assert "client secret" in dynamics.explain(str(caught.value))
    clean_env.delenv("CRM_CLIENT_SECRET")
    with pytest.raises(extract.ExtractError) as missing:
        dynamics.tables(conn)
    assert "CRM_CLIENT_SECRET is not set" in str(missing.value)


def test_the_records_are_found_without_being_told():
    assert extract.guess_records({"@odata.context": "x", "value": [{"a": 1}]}) == "value"
    assert extract.guess_records({"meta": {"n": 1}, "data": {"items": [{"a": 1}], "tags": ["x"]}}) == "data.items"
    assert extract.guess_records([{"a": 1}]) is None
    columns = {
        "latitude": 40.78,
        "hourly_units": {"time": "iso8601"},
        "hourly": {"time": ["t1", "t2"], "temperature_2m": [1.0, 2.0]},
    }
    assert extract.guess_records(columns) == "hourly"
    assert extract.records_at(columns, "hourly") == [
        {"time": "t1", "temperature_2m": 1.0},
        {"time": "t2", "temperature_2m": 2.0},
    ]
    assert extract._at({dynamics.NEXT: "u"}, dynamics.NEXT) == "u"


# The canvas: connect, pick tables, fetch and load -------------------------------------------------------


@pytest.fixture
def project(tmp_path, clean_env):
    folder = tmp_path / "commerce"
    shutil.copytree(
        EXAMPLE, folder, ignore=shutil.ignore_patterns("target", "logs", ".murakumo", "*.duckdb", "landing")
    )
    return folder


def test_connect_dynamics_on_the_canvas(fake, project, clean_env):
    from fastapi.testclient import TestClient

    from murakumo.server import create_app

    client = TestClient(create_app(project), base_url="http://localhost")
    bad = client.post(
        "/api/sources/crm/dynamics", json={"url": fake, "tenant": "t1", "client_id": "app-id", "client_secret": "nope"}
    )
    assert bad.status_code == 400 and "client secret" in bad.json()["message"]
    answer = client.post("/api/sources/crm/dynamics", json={"url": fake, "tenant": "t1", "client_secret": "s3cr$t!"})
    assert answer.status_code == 200, answer.text
    assert [t["label"] for t in answer.json()["tables"]] == ["Mortgage Loan", "Contact"]
    assert ".env" in (project / ".gitignore").read_text()
    assert "s3cr$t!" not in (project / "murakumo.yaml").read_text()  # the secret is only in .env
    status = client.get("/api/sources/crm/dynamics").json()
    assert status["tenant"] == "t1" and all(s["set"] for s in status["secrets"])

    picked = client.post(
        "/api/sources/crm/dynamics/tables", json={"tables": ["contacts", "vrp_mortgageloans", "missing"]}
    )
    assert picked.status_code == 200, picked.text
    made = {m["entity_set"]: m for m in picked.json()["made"]}
    assert made["contacts"]["watermark"] and "error" in made["missing"]
    model = load_project(project).project
    contacts = model.model.entity("contacts")
    assert contacts.watermark == "modifiedon" and contacts.load.value == "incremental"
    assert [a.name for a in contacts.attributes if a.key] == ["contactid"]
    endpoints = {e.table: e for e in model.manifest.sources[-1].connection.endpoints}
    assert endpoints["contacts"].since_format == "modifiedon ge {since}"
    assert set(endpoints) == {"contacts", "vrp_mortgageloans"}

    # Fetch and load, as the canvas's button runs it, then query what landed.
    clean_env.delenv("CRM_CLIENT_SECRET", raising=False)  # read from the project's .env, not exported
    clean_env.delenv("CRM_CLIENT_ID", raising=False)
    result = CliRunner().invoke(app, ["ingest", "--source", "crm", "--load", "-p", str(project)])
    assert result.exit_code == 0, result.output
    assert "crm.contacts: 7 new" in result.output and "loaded contacts: 7 rows" in result.output
    assert "merged by key" in result.output

    # The next fetch brings only what changed, and the table keeps every row: merged by contactid.
    FakeDynamics.rows = {**FakeDynamics.rows, "contacts": [*CONTACTS[:6], {**CONTACTS[6], "fullname": "Renamed"}]}
    try:
        again = CliRunner().invoke(app, ["ingest", "--source", "crm", "--load", "-p", str(project)])
    finally:
        FakeDynamics.rows = {**FakeDynamics.rows, "contacts": CONTACTS}
    assert again.exit_code == 0, again.output
    assert "crm.contacts: 1 new" in again.output
    status = client.get("/api/explore/status").json()
    assert status["tables"]["contacts"]["rows"] == 7 and status["tables"]["vrp_mortgageloans"]["rows"] == 2
    renamed = client.post(
        "/api/query", json={"sql": "select fullname from raw_crm.contacts where cast(contactid as varchar) like '%6'"}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["rows"] == [["Renamed"]]


def test_paste_keeps_the_secret_out_of_the_model(project, clean_env):
    from fastapi.testclient import TestClient

    from murakumo.server import create_app

    client = TestClient(create_app(project), base_url="http://localhost")
    answer = client.post(
        "/api/sources/weather/paste",
        json={"text": "curl https://api.example.com/v1/hourly -H 'Authorization: Bearer abc'"},
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["connection"]["auth"] == {"type": "bearer", "env": "WEATHER_TOKEN"} and body["saved"] == [
        "WEATHER_TOKEN"
    ]
    assert "secret" not in body and secret_file.read(project)["WEATHER_TOKEN"] == "abc"
    assert client.get("/api/secrets", params={"names": "WEATHER_TOKEN"}).json()["secrets"][0]["from"] == "file"


def test_fetch_and_load_job():
    title, args = jobs.command(Path("."), "ingest", {"source": "crm", "load": True, "environment": "dev"})
    assert title == "Fetch and load crm" and args == ["ingest", "--source", "crm", "--load", "--env", "dev"]


def test_the_row_id_is_found():
    assert dynamics.primary_id(["contactid", "fullname"], "contacts", "contact") == "contactid"
    assert dynamics.primary_id(["opportunityid"], "opportunities") == "opportunityid"
    assert dynamics.primary_id(["vrp_mortgageloanid"], "vrp_mortgageloans") == "vrp_mortgageloanid"
    assert dynamics.primary_id(["name"], "things") is None


def test_landed_fields_are_named_as_their_columns():
    from murakumo.importers.base import safe_name

    for key in ("@odata.etag", "Trip Distance (mi)", "2nd", "plain", "a-b.c", "!!!"):
        assert extract.column_name(key) == safe_name(key)


def test_secret_commands(project, clean_env):
    runner = CliRunner()
    kept = runner.invoke(app, ["secret", "set", "A_TOKEN", "--value", "v$1", "-p", str(project)])
    assert kept.exit_code == 0, kept.output
    assert secret_file.read(project)["A_TOKEN"] == "v$1"
    listed = runner.invoke(app, ["secret", "list", "-p", str(project)])
    assert "A_TOKEN: in .env" in listed.output and "v$1" not in listed.output
    assert "Removed" in runner.invoke(app, ["secret", "remove", "A_TOKEN", "-p", str(project)]).output


def test_a_dynamics_source_set_up_by_hand_moves_over(fake, project, clean_env):
    from fastapi.testclient import TestClient

    from murakumo import stack
    from murakumo.server import create_app

    by_hand = {
        "kind": "rest",
        "base_url": fake + dynamics.API,
        "auth": {"type": "oauth2", "token_url": "x", "client_id_env": "A_ID", "client_secret_env": "A_SECRET"},
        "endpoints": [{"table": "contacts", "path": "/contacts", "records": "value"}],
    }
    stack.set_connection(load_project(project).project, "crm", by_hand)
    client = TestClient(create_app(project), base_url="http://localhost")
    answer = client.post(
        "/api/sources/crm/dynamics",
        json={"url": fake, "tenant": "t1", "client_id": "app-id", "client_secret": "s3cr$t!"},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["fetching"] == ["contacts"]
    connection = load_project(project).project.manifest.sources[-1].connection
    assert connection.paging.type == "next_url" and connection.auth.client_secret_env == "CRM_CLIENT_SECRET"
