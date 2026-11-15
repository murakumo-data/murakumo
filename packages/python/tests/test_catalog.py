import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest
from typer.testing import CliRunner

from murakumo import catalog
from murakumo.cli import app
from murakumo.scaffold import init_project

runner = CliRunner()


@pytest.fixture
def taxi(tmp_path):
    return init_project(tmp_path / "taxi")


def test_an_event_per_modelled_table_with_column_lineage(taxi):
    found = catalog.events(taxi, at=datetime(2026, 11, 14, tzinfo=UTC), where="duckdb://taxi")
    by_job = {e["job"]["name"]: e for e in found}
    assert {"marts.fct_trip", "marts.dim_taxi_zone"} <= set(by_job) and not any(j.startswith("raw.") for j in by_job)
    zone = by_job["marts.dim_taxi_zone"]
    assert zone["eventType"] == "COMPLETE" and zone["eventTime"] == "2026-11-14T00:00:00+00:00"
    assert zone["inputs"] == [{"namespace": "duckdb://taxi", "name": "raw_nyc_tlc.taxi_zone_lookup"}]
    output = zone["outputs"][0]
    assert output["name"] == "marts.dim_taxi_zone"
    assert {"name": "zone_id", "type": "integer"} in output["facets"]["schema"]["fields"]
    lineage = output["facets"]["columnLineage"]["fields"]["zone_id"]["inputFields"][0]
    assert (lineage["name"], lineage["field"]) == ("raw_nyc_tlc.taxi_zone_lookup", "LocationID")
    assert lineage["transformations"][0]["subtype"] == "IDENTITY"
    # The same table has the same run id every time: sending again updates the catalog.
    again = catalog.events(taxi, where="duckdb://taxi")
    assert {e["job"]["name"]: e["run"]["runId"] for e in again} == {n: e["run"]["runId"] for n, e in by_job.items()}


def test_a_computed_column_says_how(taxi):
    fact = next(e for e in catalog.events(taxi) if e["job"]["name"] == "marts.fct_trip")
    fields = fact["outputs"][0]["facets"]["columnLineage"]["fields"]
    computed = [f for f in fields.values() if f["inputFields"][0]["transformations"][0]["subtype"] == "TRANSFORMATION"]
    assert computed and computed[0]["inputFields"][0]["transformations"][0]["description"]


def test_export_writes_events_or_a_graph(taxi, tmp_path):
    out = tmp_path / "lineage.jsonl"
    done = runner.invoke(app, ["catalog", "export", "-o", str(out), "-p", str(taxi.root)])
    assert done.exit_code == 0, done.output
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert lines and all(e["schemaURL"].startswith("https://openlineage.io/spec/") for e in lines)
    graph = tmp_path / "graph.json"
    assert (
        runner.invoke(app, ["catalog", "export", "--format", "json", "-o", str(graph), "-p", str(taxi.root)]).exit_code
        == 0
    )
    tables = {t["name"]: t for t in json.loads(graph.read_text())["tables"]}
    zone_id = next(c for c in tables["dim_taxi_zone"]["columns"] if c["name"] == "zone_id")
    assert (
        zone_id["from"] == ["taxi_zone_lookup.LocationID"]
        and tables["dim_taxi_zone"]["dataset"] == "marts.dim_taxi_zone"
    )


def test_send_posts_each_event_with_the_key(taxi, monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers.get("Authorization"), body["job"]["name"]))
            self.send_response(201)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("CATALOG_URL", f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setenv("CATALOG_KEY", "k")
        sent = catalog.send(catalog.events(taxi), "CATALOG_URL", "CATALOG_KEY")
    finally:
        server.shutdown()
    assert sent == len(received) > 1
    assert received[0][0] == "/api/v1/lineage" and received[0][1] == "Bearer k"
    monkeypatch.setenv("CATALOG_URL", "http://catalog.example.test")
    with pytest.raises(catalog.CatalogError, match="https://"):
        catalog.send([], "CATALOG_URL")
    monkeypatch.delenv("CATALOG_URL")
    done = runner.invoke(app, ["catalog", "send", "--url-env", "CATALOG_URL", "-p", str(taxi.root)])
    assert done.exit_code == 1 and "set CATALOG_URL" in done.output
