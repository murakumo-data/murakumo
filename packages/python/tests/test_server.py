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
