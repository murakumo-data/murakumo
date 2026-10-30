from fastapi.testclient import TestClient
from typer.testing import CliRunner

from murakumo import edit
from murakumo.cli import app
from murakumo.impact import markdown, report
from murakumo.modeldiff import diff, widens
from murakumo.project import load_project
from murakumo.scaffold import init_project
from murakumo.server import create_app

runner = CliRunner()


def test_types_widen_or_narrow():
    assert widens("integer", "bigint") and not widens("bigint", "integer")
    assert widens("decimal(10,2)", "decimal(12,2)") and not widens("decimal(10,2)", "decimal(10,1)")
    assert widens("string(20)", "string") and not widens("string", "string(20)")
    assert widens("date", "timestamp") and widens("integer", "string")
    assert widens("integer", "integer") is None


def test_diff_classifies_changes(tmp_path):
    project = init_project(tmp_path / "p")
    old = load_project(project.root).project.model
    edit.rename_column(project, "yellow_tripdata", "PULocationID", "pickup_location")
    edit.update_column(project, "dim_taxi_zone", "zone_id", {"type": "bigint"})
    edit.update_column(project, "dim_taxi_zone", "borough", {"nullable": False})
    edit.remove_column(project, "dim_taxi_zone", "service_zone")
    edit.add_column(project, "fct_trip", "note", "string")
    changes = {c.summary: c.cls for c in diff(old, project.model)}
    assert changes == {
        "dim_taxi_zone.borough is now not null": "breaking",
        "dim_taxi_zone: column service_zone removed": "breaking",
        "dim_taxi_zone.zone_id: integer -> bigint (wider)": "additive",
        "fct_trip: new column note (string)": "additive",
        "yellow_tripdata: column PULocationID renamed to pickup_location": "compatible",
    }


def test_impact_follows_lineage(tmp_path):
    project = init_project(tmp_path / "p")
    old = load_project(project.root).project.model
    edit.remove_column(project, "taxi_zone_lookup", "LocationID")
    result = report(project, old, project.model)
    [removed] = [i for i in result.impacts if i.change.kind == "column_removed"]
    assert "dim_taxi_zone.zone_id" in removed.columns  # built from it, in the model as it was
    assert "dim_taxi_zone" in removed.tables
    assert any("relationship yellow_tripdata.PULocationID -> taxi_zone_lookup.LocationID" == t for t in removed.tests)
    assert removed.schedules == ["daily"]
    assert result.worst == "breaking"
    page = markdown(result, "main")
    assert "🔴 breaking" in page and "dim_taxi_zone" in page


def test_canvas_previews_an_edit_without_saving(tmp_path):
    project = init_project(tmp_path / "p")
    client = TestClient(create_app(project.root), base_url="http://localhost")
    state = client.get("/api/state").json()
    zone = next(e for e in state["model"]["entities"] if e["name"] == "taxi_zone_lookup")
    column = next(c for c in zone["attributes"] if c["name"] == "LocationID")
    url = f"/api/entities/{zone['id']}/columns/{column['id']}"
    preview = client.delete(url, headers={"X-Murakumo-Preview": "1"}).json()["preview"]
    assert preview["worst"] == "breaking" and preview["impacts"][0]["columns"]
    assert load_project(project.root).project.model.entity("taxi_zone_lookup").attribute("LocationID")  # not saved
    assert "preview" not in client.delete(url).json()  # without the header, it is done


def test_cli_diff_fails_on_breaking(tmp_path):
    import subprocess

    project = init_project(tmp_path / "p")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=project.root, check=True)
    edit.remove_column(project, "fct_trip", "dropoff_at")
    from murakumo.project import save_model

    save_model(project)
    result = runner.invoke(app, ["diff", "HEAD", "--fail-on", "breaking", "-p", str(project.root)])
    assert result.exit_code == 1 and "column dropoff_at removed" in result.output and "reaches" in result.output
