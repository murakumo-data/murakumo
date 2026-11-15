import json

import pytest
from typer.testing import CliRunner

from murakumo import cost, schedules
from murakumo.cli import app
from murakumo.manifest import Warehouse
from murakumo.scaffold import init_project

runner = CliRunner()


@pytest.mark.parametrize(
    ("cron", "runs"),
    [
        ("0 6 * * *", 365 / 12),
        ("0 * * * *", 365 * 24 / 12),
        ("0 6 * * 1-5", 261 / 12),
        ("*/15 * * * *", 365 * 96 / 12),
        ("0 0 1 * *", 1),
    ],
)
def test_runs_per_month(cron, runs):
    assert cost.runs_per_month(cron) == pytest.approx(runs)


@pytest.fixture
def project(tmp_path):
    project = init_project(tmp_path / "taxi")
    schedules.add_schedule(project, "nightly", "0 6 * * *")
    schedules.add_schedule(project, "zones", "0 * * * *", ["+dim_taxi_zone"])
    project.manifest.targets.warehouse.type = Warehouse.SNOWFLAKE
    return project


def test_time_billed_warehouse(project):
    result = cost.estimate(project, measured={"nightly": 12.0})
    lines = {line.schedule: line for line in result.lines}
    # Nightly: 30.4 runs of 12 minutes on 1 credit an hour at 3 dollars.
    assert lines["nightly"].measured and lines["nightly"].cost == pytest.approx(365 / 12 * 12 / 60 * 1.0 * 3.0)
    # Hourly, not measured: the assumed 5 minutes a run.
    assert not lines["zones"].measured and lines["zones"].minutes == 5.0
    assert result.total == pytest.approx(lines["nightly"].cost + lines["zones"].cost)
    assert any("No run of zones is recorded yet" in a for a in result.assumptions)
    assert any("a list price to start from" in a for a in result.assumptions)


def test_a_short_run_is_billed_the_minimum_and_your_own_prices_win(project):
    project.manifest.costs.unit_price = 2.0
    project.manifest.costs.units_per_hour = 2.0  # a Small warehouse
    result = cost.estimate(project, measured={"nightly": 0.2, "zones": 0.2})
    nightly = next(line for line in result.lines if line.schedule == "nightly")
    assert nightly.cost == pytest.approx(365 / 12 * (60 / 3600) * 2.0 * 2.0)  # 60 seconds, not 12
    assert any("yours, from murakumo.yaml" in a for a in result.assumptions)


def test_scan_billed_and_free_warehouses(project):
    project.manifest.targets.warehouse.type = Warehouse.BIGQUERY
    project.manifest.costs.gb_scanned_per_run = 512
    result = cost.estimate(project, measured={})
    nightly = next(line for line in result.lines if line.schedule == "nightly")
    assert nightly.cost == pytest.approx(365 / 12 * 0.5 * 6.25) and result.unit == "TiB read"
    project.manifest.targets.warehouse.type = Warehouse.DUCKDB
    assert cost.estimate(project, measured={}).total == 0


def test_every_warehouse_has_a_rate():
    assert set(cost.RATES) == set(Warehouse)


def test_the_command(tmp_path):
    project = init_project(tmp_path / "taxi")
    schedules.add_schedule(project, "nightly", "0 6 * * *")
    shown = runner.invoke(app, ["cost", "-p", str(project.root)])
    assert shown.exit_code == 0 and "An estimate" in shown.output and "nightly" in shown.output
    data = json.loads(runner.invoke(app, ["cost", "--json", "-p", str(project.root)]).output)
    assert data["warehouse"] == "duckdb" and data["lines"][0]["schedule"] == "nightly" and data["assumptions"]
