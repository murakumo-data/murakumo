import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from murakumo.project import load_project  # noqa: E402
from murakumo.scaffold import init_project  # noqa: E402
from murakumo.server import create_app  # noqa: E402


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path / "proj")


@pytest.fixture
def client(project):
    return TestClient(create_app(project.root), base_url="http://localhost")


def entity_id(state, name):
    return next(e["id"] for e in state["model"]["entities"] if e["name"] == name)


def names(state):
    return {e["name"] for e in state["model"]["entities"]}


def test_undo_and_redo_a_removed_table(client, project):
    state = client.get("/api/state").json()
    assert state["history"] == {"undo": None, "redo": None}
    zone = entity_id(state, "dim_taxi_zone")
    position = {"x": 40, "y": 80}
    state = {**state, **client.put("/api/layout", json={"positions": {zone: position}}).json()}
    relationships = len(state["model"]["relationships"])
    files = sorted(p.name for p in (project.root / "model").rglob("*.yaml"))

    state = client.delete(f"/api/entities/{zone}").json()
    assert "dim_taxi_zone" not in names(state)
    assert "dim_taxi_zone" in state["history"]["undo"]

    state = client.post("/api/undo").json()
    assert state["message"].startswith("Undid: table dim_taxi_zone removed")
    assert "dim_taxi_zone" in names(state)
    assert len(state["model"]["relationships"]) == relationships
    assert state["layout"][zone] == position  # it comes back where it was
    assert sorted(p.name for p in (project.root / "model").rglob("*.yaml")) == files
    assert state["history"]["undo"] is None and "dim_taxi_zone" in state["history"]["redo"]

    state = client.post("/api/redo").json()
    assert "dim_taxi_zone" not in names(state)
    assert client.post("/api/redo").status_code == 409  # nothing left to redo


def test_undo_keeps_moves_and_labels_manifest_changes(client, project):
    state = client.post("/api/domains", json={"name": "finance"}).json()
    assert state["history"]["undo"] == "new domain finance"
    trip = entity_id(state, "fct_trip")
    client.put("/api/layout", json={"positions": {trip: {"x": 999, "y": 111}}})
    state = client.post("/api/undo").json()
    assert "finance" not in {d["name"] for d in state["domains"]}
    assert state["layout"][trip] == {"x": 999, "y": 111}  # moving tables is not undone


def test_undo_refuses_after_an_edit_outside_the_canvas(client, project):
    state = client.get("/api/state").json()
    trip = entity_id(state, "fct_trip")
    client.post(f"/api/entities/{trip}/columns", json={"name": "note", "type": "string"})
    path = project.root / "murakumo.yaml"
    path.write_text(path.read_text() + "\n# edited by hand\n")
    response = client.post("/api/undo")
    assert response.status_code == 409
    assert "changed outside the canvas" in response.json()["message"]
    assert "# edited by hand" in path.read_text()
    assert response.json()["state"]["history"]["undo"] is None
    assert "note" in {a.name for a in load_project(project.root).project.model.entity("fct_trip").attributes}


def test_a_new_change_clears_redo(client):
    state = client.get("/api/state").json()
    trip = entity_id(state, "fct_trip")
    client.post(f"/api/entities/{trip}/columns", json={"name": "a", "type": "string"})
    client.post("/api/undo")
    state = client.post(f"/api/entities/{trip}/columns", json={"name": "b", "type": "string"}).json()
    assert state["history"]["redo"] is None


def test_canvas_settings_are_saved_pruned_and_undoable(client, project):
    state = client.get("/api/state").json()
    trip = entity_id(state, "fct_trip")
    zone = entity_id(state, "dim_taxi_zone")
    state = client.put(
        "/api/canvas",
        json={
            "label": "colour fct_trip",
            "labels": [{"name": "Finance", "colour": "#2457d6"}],
            "colours": {trip: "Finance", zone: "Finance"},
            "collapsed": {trip: True},
            "notes": [{"id": "n1", "text": "Trips land hourly", "x": 10, "y": 20}],
            "diagrams": [{"id": "d1", "name": "Trips", "entities": [trip, zone]}],
        },
    ).json()
    assert state["canvas"]["colours"] == {trip: "Finance", zone: "Finance"}
    assert state["history"]["undo"] == "colour fct_trip"
    assert state["canvas"]["notes"][0]["width"] == 220

    # Removing a table drops what pointed at it; removing a label uncolours its tables.
    state = client.delete(f"/api/entities/{zone}").json()
    assert state["canvas"]["colours"] == {trip: "Finance"}
    assert state["canvas"]["diagrams"][0]["entities"] == [trip]
    state = client.put("/api/canvas", json={"labels": []}).json()
    assert "colours" not in state["canvas"] and "labels" not in state["canvas"]
    assert state["canvas"]["collapsed"] == {trip: True}

    state = client.post("/api/undo").json()
    assert state["canvas"]["labels"][0]["name"] == "Finance"
    assert client.put("/api/canvas", json={"labels": [{"name": "x", "colour": "blue"}]}).status_code == 422


def test_bulk_changes_are_one_undo(client):
    state = client.get("/api/state").json()
    client.post("/api/domains", json={"name": "finance"})
    ids = [entity_id(state, "fct_trip"), entity_id(state, "dim_taxi_zone")]
    state = client.patch("/api/entities", json={"ids": ids, "changes": {"domain": "finance"}}).json()
    assert {e["domain"] for e in state["model"]["entities"] if e["id"] in ids} == {"finance"}
    assert state["history"]["undo"].endswith("(+1 more)")
    state = client.post("/api/undo").json()
    assert {e["domain"] for e in state["model"]["entities"] if e["id"] in ids} != {"finance"}

    state = client.post("/api/entities/remove", json={"ids": ids}).json()
    assert {"fct_trip", "dim_taxi_zone"}.isdisjoint(names(state))
    state = client.post("/api/undo").json()
    assert {"fct_trip", "dim_taxi_zone"} <= names(state)
    assert client.patch("/api/entities", json={"ids": ids, "changes": {"name": "x"}}).status_code == 400
