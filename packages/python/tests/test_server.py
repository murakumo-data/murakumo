import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from murakumo.project import ENTITIES_DIR, LAYOUT_FILE, load_layout, load_project  # noqa: E402
from murakumo.scaffold import init_project  # noqa: E402
from murakumo.server import REVISION_HEADER, create_app  # noqa: E402


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path / "proj")  # the NYC taxi example


@pytest.fixture
def client(project):
    return TestClient(create_app(project.root), base_url="http://localhost")


def entity_id(state, name):
    return next(e["id"] for e in state["model"]["entities"] if e["name"] == name)


def column_id(state, entity, name):
    columns = next(e["attributes"] for e in state["model"]["entities"] if e["name"] == entity)
    return next(c["id"] for c in columns if c["name"] == name)


def test_state(client):
    state = client.get("/api/state").json()
    assert state["editable"] is True
    assert state["project"] == "proj"
    assert {e["name"] for e in state["model"]["entities"]} >= {"fct_trip", "dim_taxi_zone"}
    assert state["model"]["relationships"][0]["from"] == "yellow_tripdata"
    assert "nyc_tlc" in state["choices"]["sources"] and "money" in state["choices"]["types"]
    assert client.get("/api/revision").json()["revision"] == state["revision"]


def test_add_entity_column_and_relationship(client, project):
    state = client.post(
        "/api/entities",
        json={"name": "dim_vendor", "layer": "marts", "kind": "dimension", "position": {"x": 10.4, "y": -20}},
    ).json()
    vendor = entity_id(state, "dim_vendor")
    assert state["layout"][vendor] == {"x": 10, "y": -20}

    state = client.post(
        f"/api/entities/{vendor}/columns", json={"name": "vendor_id", "type": "integer", "primary_key": True}
    ).json()
    trip = entity_id(state, "fct_trip")
    state = client.post(f"/api/entities/{trip}/columns", json={"name": "vendor_id", "type": "integer"}).json()
    state = client.post(
        "/api/relationships",
        json={"from": "fct_trip", "to": "dim_vendor", "keys": [{"from": "vendor_id", "to": "vendor_id"}]},
    ).json()
    assert any(r["to"] == "dim_vendor" for r in state["model"]["relationships"])

    # Everything is on disk, in the same files the CLI writes.
    loaded = load_project(project.root).project
    assert loaded.model.entity("dim_vendor").primary_key[0].name == "vendor_id"
    assert (project.root / ENTITIES_DIR / "marts" / "dim_vendor.yaml").is_file()


def test_rename_and_update(client, project):
    state = client.get("/api/state").json()
    zone = entity_id(state, "dim_taxi_zone")
    state = client.patch(f"/api/entities/{zone}", json={"name": "dim_zone", "description": "Taxi zones"}).json()
    assert entity_id(state, "dim_zone") == zone
    assert {r["to"] for r in state["model"]["relationships"] if r["from"] == "fct_trip"} == {"dim_zone"}
    assert not (project.root / ENTITIES_DIR / "marts" / "dim_taxi_zone.yaml").exists()

    column = column_id(state, "dim_zone", "zone_name")
    state = client.patch(f"/api/entities/{zone}/columns/{column}", json={"name": "zone", "type": "text"}).json()
    renamed = next(e for e in state["model"]["entities"] if e["name"] == "dim_zone")
    assert [c for c in renamed["attributes"] if c["id"] == column][0] | {"name": "zone", "type": "text"} == [
        c for c in renamed["attributes"] if c["id"] == column
    ][0]

    rel = state["model"]["relationships"][0]
    state = client.patch(f"/api/relationships/{rel['id']}", json={"optional": True}).json()
    assert state["model"]["relationships"][0]["optional"] is True


def test_remove(client, project):
    state = client.get("/api/state").json()
    zone = entity_id(state, "dim_taxi_zone")
    client.put("/api/layout", json={"positions": {zone: {"x": 1, "y": 2}}})
    state = client.delete(f"/api/entities/{zone}").json()
    assert all(r["to"] != "dim_taxi_zone" for r in state["model"]["relationships"])
    assert zone not in state["layout"]
    trip = entity_id(state, "fct_trip")
    state = client.delete(f"/api/entities/{trip}/columns/{column_id(state, 'fct_trip', 'total_amount')}").json()
    rel = state["model"]["relationships"][0]["id"]
    state = client.delete(f"/api/relationships/{rel}").json()
    assert all(r["id"] != rel for r in state["model"]["relationships"])


def test_errors_change_nothing(client, project):
    state = client.get("/api/state").json()
    response = client.post("/api/entities", json={"name": "fct_trip"})
    assert response.status_code == 400 and "already exists" in response.json()["message"]
    trip = entity_id(state, "fct_trip")
    response = client.post(f"/api/entities/{trip}/columns", json={"name": "shape", "type": "geometry"})
    assert response.status_code == 400 and "unknown logical type" in response.json()["message"]
    assert client.get("/api/state").json()["revision"] == state["revision"]


def test_stale_revision_is_refused(client, project):
    state = client.get("/api/state").json()
    # Someone edits a file by hand meanwhile.
    path = project.root / ENTITIES_DIR / "marts" / "fct_trip.yaml"
    path.write_text(path.read_text().replace("One row per yellow taxi trip", "Edited by hand", 1))
    response = client.post("/api/entities", json={"name": "dim_vendor"}, headers={REVISION_HEADER: state["revision"]})
    assert response.status_code == 409
    fresh = response.json()["state"]
    assert next(e for e in fresh["model"]["entities"] if e["name"] == "fct_trip")["description"].startswith(
        "Edited by hand"
    )
    response = client.post("/api/entities", json={"name": "dim_vendor"}, headers={REVISION_HEADER: fresh["revision"]})
    assert response.status_code == 200


def test_broken_files_block_changes(client, project):
    (project.root / ENTITIES_DIR / "marts" / "fct_trip.yaml").write_text("name: fct_trip\nattributes: [oops\n")
    state = client.get("/api/state").json()
    assert state["editable"] is False
    assert any(d["code"] == "MK001" for d in state["diagnostics"])
    assert client.post("/api/entities", json={"name": "dim_vendor"}).status_code == 409


def test_diagnostics_point_at_entities(client, project):
    state = client.get("/api/state").json()
    trip = entity_id(state, "fct_trip")
    state = client.post(f"/api/entities/{trip}/columns", json={"name": "email", "type": "string", "pii": True}).json()
    unclassified = [d for d in state["diagnostics"] if d["code"] == "MK306"]
    assert unclassified and unclassified[0]["entity_id"] == trip


def test_layout(client, project):
    state = client.get("/api/state").json()
    trip = entity_id(state, "fct_trip")
    revision = client.put("/api/layout", json={"positions": {trip: {"x": 120.6, "y": 40}}}).json()["revision"]
    assert revision != state["revision"]
    assert client.get("/api/state").json()["layout"][trip] == {"x": 121, "y": 40}
    assert (project.root / LAYOUT_FILE).is_file()
    assert load_project(project.root).diagnostics == []  # layout.yaml is a known file
    assert list(load_layout(project.root).values()) == [(121, 40)]


def test_only_answers_localhost(project):
    remote = TestClient(create_app(project.root), base_url="http://evil.example")
    assert remote.get("/api/state").status_code == 400


def test_changes_need_json(client):
    # A cross-site form post cannot send JSON without a CORS preflight, which the server never allows.
    response = client.post("/api/entities", content="name=dim_vendor", headers={"Content-Type": "text/plain"})
    assert response.status_code == 422


# generated code ---------------------------------------------------------------------------------


def test_generated_before_and_after_generate(client, project):
    summary = client.get("/api/generated").json()
    assert summary["warehouse"] == "duckdb" and summary["blocked"] is None
    assert {f["status"] for f in summary["files"]} == {"added"}  # nothing generated yet
    trip = next(f for f in summary["files"] if f["path"].endswith("/fct_trip.sql"))
    assert trip["entities"] == [entity_id(client.get("/api/state").json(), "fct_trip")]
    assert summary["migration"]["baseline"] is True

    revision = client.get("/api/revision").json()["generated"]
    summary = client.post("/api/generate").json()
    assert {f["status"] for f in summary["files"]} == {"unchanged"}
    assert (project.root / "generated" / "dbt" / "dbt_project.yml").is_file()
    assert client.get("/api/revision").json()["generated"] != revision


def test_change_shows_as_a_diff(client, project):
    client.post("/api/generate")
    state = client.get("/api/state").json()
    trip = entity_id(state, "fct_trip")
    column = column_id(state, "fct_trip", "trip_distance_miles")
    client.patch(f"/api/entities/{trip}/columns/{column}", json={"name": "distance_miles"})

    changed = [f["path"] for f in client.get("/api/generated").json()["files"] if f["status"] == "changed"]
    sql = next(p for p in changed if p.endswith("/fct_trip.sql"))
    diff = client.get("/api/generated/file", params={"path": sql}).json()["diff"]
    assert "-" in diff and "+" in diff and "distance_miles" in diff
    assert client.get("/api/generated/file", params={"path": "../murakumo.yaml"}).status_code == 400


def test_entity_code(client):
    state = client.get("/api/state").json()
    code = client.get(f"/api/entities/{entity_id(state, 'yellow_tripdata')}/code").json()
    assert [f["path"] for f in code["files"]] == ["dbt/models/staging/nyc_tlc/stg_nyc_tlc__yellow_tripdata.sql"]
    assert code["ddl"].startswith("CREATE TABLE IF NOT EXISTS raw_nyc_tlc.yellow_tripdata")


def test_migrations(client, project):
    summary = client.post("/api/migrations", json={}).json()
    assert summary["migration"] is None
    assert (project.root / "migrations" / "duckdb" / "0001_initial.sql").is_file()
    assert client.post("/api/migrations", json={}).status_code == 409  # nothing new

    state = client.get("/api/state").json()
    trip = entity_id(state, "yellow_tripdata")
    client.patch(
        f"/api/entities/{trip}/columns/{column_id(state, 'yellow_tripdata', 'trip_distance')}", json={"name": "miles"}
    )
    migration = client.get("/api/generated").json()["migration"]
    assert migration["file"] == "migrations/duckdb/0002_rename_yellow_tripdata_trip_distance.sql"
    assert "RENAME COLUMN trip_distance TO miles" in migration["sql"]
    client.post("/api/migrations", json={"name": "shorter names"})
    assert (project.root / "migrations" / "duckdb" / "0002_shorter_names.sql").is_file()


def test_mappings(client, project):
    state = client.get("/api/state").json()
    trip = entity_id(state, "fct_trip")
    column = column_id(state, "fct_trip", "total_amount")
    url = f"/api/entities/{trip}/mappings/{column}"
    state = client.put(
        url, json={"sources": ["yellow_tripdata.total_amount"], "expression": "total_amount * 100"}
    ).json()
    mapping = next(
        m
        for e in state["model"]["entities"]
        if e["name"] == "fct_trip"
        for m in e["mappings"]
        if m["target"] == "total_amount"
    )
    assert mapping["expression"] == "total_amount * 100"
    response = client.put(url, json={"sources": ["yellow_tripdata.nope"]})
    assert response.status_code == 400 and "no column 'nope'" in response.json()["message"]


def test_generate_is_blocked_by_model_errors(client, project):
    state = client.get("/api/state").json()
    trip = entity_id(state, "fct_trip")
    client.patch(f"/api/entities/{trip}", json={"domain": "nowhere"})  # not a declared domain: MK202
    summary = client.get("/api/generated").json()
    assert summary["blocked"] and summary["files"] == []
    assert client.post("/api/generate").status_code == 409


def test_column_order(client):
    state = client.get("/api/state").json()
    zone = entity_id(state, "dim_taxi_zone")
    names = ["service_zone", "zone_id", "borough", "zone_name"]
    ids = [column_id(state, "dim_taxi_zone", n) for n in names]
    state = client.put(f"/api/entities/{zone}/columns/order", json={"columns": ids}).json()
    columns = next(e["attributes"] for e in state["model"]["entities"] if e["name"] == "dim_taxi_zone")
    assert [c["name"] for c in columns] == names
    response = client.put(f"/api/entities/{zone}/columns/order", json={"columns": ids[:2]})
    assert response.status_code == 400 and "every column" in response.json()["message"]


def test_domains(client):
    state = client.post("/api/domains", json={"name": "finance", "owner": "Finance"}).json()
    assert {"name": "finance", "owner": "Finance", "description": None, "entities": 0} in state["domains"]
    state = client.patch("/api/domains/mobility", json={"name": "transport"}).json()
    assert {e["domain"] for e in state["model"]["entities"]} == {"transport"}
    assert client.delete("/api/domains/transport").status_code == 400
    state = client.delete("/api/domains/finance").json()
    assert [d["name"] for d in state["domains"]] == ["transport"]


def test_expressions(client):
    check = client.post("/api/expressions/check", json={"expression": "upper(taxi_zone_lookup.Zone)"}).json()
    assert check["ok"] and check["type"] == "string"
    preview = client.post("/api/expressions/preview", json={"expression": "upper(taxi_zone_lookup.Zone)"}).json()
    assert preview["columns"] == ["taxi_zone_lookup.Zone", "result"] and preview["rows"]
    state = client.get("/api/state").json()
    zone = entity_id(state, "dim_taxi_zone")
    state = client.post(
        f"/api/entities/{zone}/calculated", json={"name": "label", "expression": "upper(taxi_zone_lookup.Zone)"}
    ).json()
    entity = next(e for e in state["model"]["entities"] if e["name"] == "dim_taxi_zone")
    assert entity["attributes"][-1]["name"] == "label"
    assert {
        "target": "label",
        "sources": ["taxi_zone_lookup.Zone"],
        "expression": "upper(taxi_zone_lookup.Zone)",
    } in entity["mappings"]


def test_lineage(client):
    result = client.get("/api/lineage", params={"entity": "dim_taxi_zone", "column": "borough"}).json()
    assert {"entity": "taxi_zone_lookup", "column": "Borough", "layer": "raw", "depth": -1} in result["nodes"]
    assert client.get("/api/lineage", params={"entity": "nope"}).status_code == 404


def test_settings_change_murakumo_yaml_keeping_comments_and_can_be_undone(client, project):
    path = project.root / "murakumo.yaml"
    path.write_text("# my notes about this project\n" + path.read_text())
    settings = client.get("/api/settings").json()
    assert settings["warehouse"] == "duckdb" and "polars" in settings["choices"]["python_engines"]

    changed = client.put(
        "/api/settings", json={"python_engine": "polars", "run_log": False, "orchestrator": "dagster"}
    ).json()
    assert changed["python_engine"] == "polars" and changed["run_log"] is False and changed["orchestrator"] == "dagster"
    assert len(changed["changed"]) == 3
    assert path.read_text().startswith("# my notes about this project")
    assert changed["state"]["history"]["undo"].startswith("settings: ")

    client.post("/api/undo")
    assert client.get("/api/settings").json()["python_engine"] == "none"
    assert client.put("/api/settings", json={"python_engine": "bash"}).status_code == 422
    assert client.put("/api/settings", json={"base_branch": "dev; rm"}).status_code == 422


def test_setup_lists_checks(client):
    setup = client.get("/api/setup").json()
    assert setup["checks"] and {"area", "title", "status", "steps"} <= set(setup["checks"][0])


def test_uploads_go_to_an_ignored_data_folder(client, project):
    put = client.put(
        "/api/uploads/zones.csv",
        content=b"LocationID,Zone\n1,Newark\n",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert put.status_code == 200 and put.json() == {"name": "zones.csv", "path": "data/zones.csv", "size": 25}
    assert (project.root / "data" / "zones.csv").read_text().startswith("LocationID")
    assert "data/" in (project.root / ".gitignore").read_text().splitlines()
    assert client.get("/api/uploads").json()["files"][0]["name"] == "zones.csv"

    def put_as(name, content_type="application/octet-stream"):
        return client.put(f"/api/uploads/{name}", content=b"x", headers={"Content-Type": content_type}).status_code

    assert put_as("zones.csv", "text/plain") == 415  # a form or a fetch without consent cannot send this
    assert put_as("..%2Fescape.csv") in (400, 404)
    assert put_as(".hidden.csv") == 400
    assert put_as("script.sh") == 400
    assert client.delete("/api/uploads/zones.csv").json() == {"files": []}


def test_env_and_diff(client, project):
    env = client.get("/api/env").json()
    assert env["prefix"] == "" and env["warehouse"] == "duckdb"  # a DuckDB file is already personal
    assert "build the project first" in env["reason"]
    assert client.get("/api/env?prefix=PROD").status_code == 400
    assert client.get("/api/diff?ref=main;rm").status_code == 400
    assert client.get("/api/diff?ref=main").status_code == 409  # not a git repository


def test_environments_in_settings(client, project):
    settings = client.get("/api/settings").json()
    assert [e["name"] for e in settings["environments"]] == ["dev", "stage", "prod"]
    environments = settings["environments"]
    environments[2]["database"] = "taxi_live.duckdb"
    changed = client.put("/api/settings", json={"environments": environments, "personal_database": "SANDBOX"}).json()
    assert changed["environments"][2]["database"] == "taxi_live.duckdb" and changed["personal_database"] == "SANDBOX"
    text = (project.root / "murakumo.yaml").read_text()
    assert "taxi_live.duckdb" in text and "branch: main" in text
    assert client.get("/api/env/provision").json()["sql"].startswith("-- Nothing to run on DuckDB")
    bad = [{"name": "prod; drop", "database": None, "branch": None}]
    assert client.put("/api/settings", json={"environments": bad}).status_code == 422


def test_guide_bus_matrix_and_exposures(client, project):
    guide = client.get("/api/guide").json()
    assert [c["slug"] for c in guide["chapters"]][:3] == ["overview", "accounts", "repository"]
    assert any(s["key"] == "model-fct_trip" for c in guide["chapters"] for s in c["steps"])

    bus = client.get("/api/bus").json()
    assert "fct_trip" in [f["name"] for f in bus["facts"]]
    assert "dim_taxi_zone" in next(f for f in bus["facts"] if f["name"] == "fct_trip")["dimensions"]

    state = client.get("/api/state").json()
    zone = entity_id(state, "dim_taxi_zone")
    state = client.patch(f"/api/entities/{zone}", json={"conformed": True, "access": "public"}).json()
    entity = next(e for e in state["model"]["entities"] if e["name"] == "dim_taxi_zone")
    assert entity["conformed"] is True and entity["access"] == "public"

    exposure = {"name": "trips_board", "type": "dashboard", "owner": "Ops", "depends_on": ["fct_trip"]}
    state = client.put("/api/exposures", json={"exposures": [exposure]}).json()
    assert state["model"]["exposures"][0]["name"] == "trips_board"
    assert (project.root / "model" / "exposures.yaml").is_file()
    bad = client.put("/api/exposures", json={"exposures": [{**exposure, "depends_on": ["nope"]}]})
    assert bad.status_code == 400 and "nope" in bad.json()["message"]

    settings = client.put("/api/settings", json={"unknown_members": True, "masking": {"pii": "hash"}}).json()
    assert settings["unknown_members"] is True and settings["masking"] == {"pii": "hash"}
    assert load_project(project.root).project.manifest.governance.masking == {"pii": "hash"}


def test_metrics_lakehouse_and_history_from_the_canvas(client):
    state = client.get("/api/state").json()
    assert "scd6" in state["choices"]["histories"]
    metrics = [{"name": "trips", "entity": "fct_trip", "expression": "count(*)"}]
    saved = client.put("/api/metrics", json={"metrics": metrics})
    assert saved.status_code == 200 and saved.json()["model"]["metrics"][0]["name"] == "trips"
    bad = client.put("/api/metrics", json={"metrics": [{"name": "x", "entity": "nope", "expression": "count(*)"}]})
    assert bad.status_code == 400 and "does not exist" in bad.json()["message"]
    lake = {"table_format": "iceberg", "catalog": "polaris", "layers": ["marts"]}
    assert client.put("/api/settings", json={"lakehouse": lake}).status_code == 200
    assert client.get("/api/settings").json()["lakehouse"]["catalog"] == "polaris"
