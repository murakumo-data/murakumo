from datetime import UTC, datetime

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from murakumo import schedules
from murakumo.cli import app
from murakumo.cron import CronError, describe, next_runs, parse
from murakumo.generate import generate
from murakumo.project import load_project
from murakumo.scaffold import init_project
from murakumo.server import create_app
from murakumo.validate import validate_project

runner = CliRunner()
AFTER = datetime(2026, 10, 29, 21, 30, tzinfo=UTC)  # a Thursday


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path / "taxi")


@pytest.mark.parametrize(
    ("cron", "words"),
    [
        ("0 6 * * *", "every day at 06:00 UTC"),
        ("0 * * * *", "every hour, on the hour"),
        ("*/15 * * * *", "every 15 minutes"),
        ("0 6 * * 1-5", "every weekday at 06:00 UTC"),
        ("30 2 1 * *", "on the 1st of the month at 02:30 UTC"),
        ("0 6,18 * * *", "every day at 06:00 and 18:00 UTC"),
        ("@weekly", "every Sunday at 00:00 UTC"),
    ],
)
def test_cron_in_words(cron, words):
    assert describe(cron) == words


def test_next_runs():
    runs = next_runs("0 6 * * 1-5", "UTC", 3, AFTER)
    assert [r.strftime("%a %d %H:%M") for r in runs] == ["Fri 30 06:00", "Mon 02 06:00", "Tue 03 06:00"]
    assert next_runs("*/15 * * * *", "UTC", 2, AFTER)[0].strftime("%H:%M") == "21:45"


@pytest.mark.parametrize("bad", ["0 6 * *", "61 * * * *", "0 25 * * *", "a * * * *", "5-1 * * * *"])
def test_bad_cron(bad):
    with pytest.raises(CronError):
        parse(bad)


def test_no_schedules_means_daily(project):
    [entry] = schedules.resolve(project)
    assert entry.implicit and entry.schedule.name == "daily" and len(entry.entities) == 4


def test_selection_and_the_rest(project):
    schedules.add_schedule(project, "zones", "0 * * * *", ["+dim_taxi_zone"])
    schedules.add_schedule(project, "nightly", "0 6 * * *")
    resolved = {r.schedule.name: [e.name for e in r.entities] for r in schedules.resolve(project)}
    assert resolved == {"zones": ["dim_taxi_zone", "taxi_zone_lookup"], "nightly": ["fct_trip", "yellow_tripdata"]}
    assert schedules.select(project, "yellow_tripdata+") == {"yellow_tripdata", "fct_trip"}
    assert schedules.select(project, "layer:raw") == {"yellow_tripdata", "taxi_zone_lookup"}
    with pytest.raises(schedules.ScheduleError, match="refreshes the rest"):
        schedules.add_schedule(project, "another", "0 7 * * *")
    with pytest.raises(schedules.ScheduleError, match="no domain"):
        schedules.add_schedule(project, "x", "0 7 * * *", ["domain:nope"])
    with pytest.raises(schedules.ScheduleError, match="five fields"):
        schedules.add_schedule(project, "y", "0 7 * *", ["layer:marts"])


def test_manifest_keeps_its_comments(project):
    schedules.add_schedule(project, "nightly", "0 6 * * *", description="Everything")
    schedules.set_timezone(project, "Europe/London")
    text = (project.root / "murakumo.yaml").read_text()
    assert "# kimball | datavault" in text and "timezone: Europe/London" in text
    reloaded = load_project(project.root).project
    assert reloaded.manifest.orchestration.schedules[0].description == "Everything"
    schedules.update_schedule(reloaded, "nightly", {"name": "every_night", "cron": "0 5 * * *"})
    schedules.remove_schedule(load_project(project.root).project, "every_night")
    assert load_project(project.root).project.manifest.orchestration.schedules == []


def test_validation(project):
    schedules.add_schedule(project, "zones", "0 * * * *", ["dim_taxi_zone"])
    codes = {d.code for d in validate_project(load_project(project.root).project)}
    assert "MK604" in codes  # nothing refreshes the other tables
    manifest = project.root / "murakumo.yaml"
    manifest.write_text(manifest.read_text().replace("- dim_taxi_zone", "- nope"))
    assert "MK601" in {d.code for d in validate_project(load_project(project.root).project)}


def test_dbt_selectors(project):
    schedules.add_schedule(project, "zones", "0 * * * *", ["+dim_taxi_zone"])
    schedules.add_schedule(project, "nightly", "0 6 * * *")
    data = yaml.safe_load(generate(project).files["dbt/selectors.yml"])
    zones, nightly = data["selectors"]
    assert [i["value"] for i in zones["definition"]["union"]] == ["dim_taxi_zone", "stg_nyc_tlc__taxi_zone_lookup"]
    assert all(i["indirect_selection"] == "cautious" for i in zones["definition"]["union"])
    assert {"method": "test_name", "value": "relationships"} in nightly["definition"]["union"]
    assert nightly["description"] == "every day at 06:00 UTC"


def test_cli(project):
    result = runner.invoke(
        app, ["add", "schedule", "zones", "--cron", "0 * * * *", "--select", "domain:mobility", "-p", str(project.root)]
    )
    assert result.exit_code == 0 and "every hour, on the hour" in result.output
    result = runner.invoke(app, ["schedules", "-p", str(project.root)])
    assert "zones" in result.output and "4: " in result.output
    assert runner.invoke(app, ["rename", "schedule", "zones", "hourly", "-p", str(project.root)]).exit_code == 0
    assert runner.invoke(app, ["remove", "schedule", "hourly", "-p", str(project.root)]).exit_code == 0


def test_api(project):
    client = TestClient(create_app(project.root), base_url="http://localhost")
    assert client.get("/api/state").json()["orchestration"]["implicit"] is True
    state = client.post("/api/schedules", json={"name": "zones", "cron": "0 * * * *", "select": ["layer:raw"]}).json()
    [zones] = state["orchestration"]["schedules"]
    assert zones["when"] == "every hour, on the hour" and len(zones["next_runs"]) == 3
    assert zones["entities"] == ["taxi_zone_lookup", "yellow_tripdata"]
    state = client.patch("/api/schedules/zones", json={"cron": "0 6 * * *"}).json()
    assert state["orchestration"]["schedules"][0]["when"] == "every day at 06:00 UTC"
    assert client.post("/api/schedules", json={"name": "bad", "cron": "nope"}).status_code == 400
    state = client.put("/api/orchestration/timezone", json={"timezone": "Asia/Tokyo"}).json()
    assert state["orchestration"]["timezone"] == "Asia/Tokyo"
    assert client.delete("/api/schedules/zones").json()["orchestration"]["implicit"] is True
