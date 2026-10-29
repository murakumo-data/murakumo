import json

import pytest
from typer.testing import CliRunner

from murakumo.cli import app
from murakumo.generate import generate
from murakumo.lineage import build_lineage, resolve_ref, trace
from murakumo.scaffold import init_project

runner = CliRunner()


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path / "taxi")


def test_sources_come_from_mappings_and_expressions(project):
    lineage = build_lineage(project)
    assert lineage.sources_of("dim_taxi_zone", "borough") == [("taxi_zone_lookup", "Borough")]
    trip_id = lineage.sources_of("fct_trip", "trip_id")
    assert ("yellow_tripdata", "VendorID") in trip_id and len(trip_id) == 7


def test_trace_up_and_down(project):
    result = trace(project, "taxi_zone_lookup", "LocationID")
    depths = {(n["entity"], n["column"]): n["depth"] for n in result["nodes"]}
    assert depths[("taxi_zone_lookup", "LocationID")] == 0
    assert depths[("dim_taxi_zone", "zone_id")] == 1
    joined = {j["relationship"] for j in result["joins"]}
    assert "trip_pickup_zone" in joined  # relationships show as joins, not as data flow
    whole = trace(project, "dim_taxi_zone")
    assert {n["entity"] for n in whole["nodes"]} == {"dim_taxi_zone", "taxi_zone_lookup"}


def test_resolve_ref(project):
    assert resolve_ref(project, "FCT_TRIP.Trip_Id") == ("fct_trip", "trip_id")
    with pytest.raises(ValueError, match="no column"):
        resolve_ref(project, "fct_trip.nope")


def test_cli(project):
    result = runner.invoke(app, ["lineage", "fct_trip.pickup_zone_id", "-p", str(project.root)])
    assert result.exit_code == 0 and "yellow_tripdata.PULocationID" in result.output
    result = runner.invoke(app, ["lineage", "yellow_tripdata.VendorID", "--down", "-p", str(project.root)])
    assert "fct_trip.trip_id" in result.output and "md5(" in result.output
    result = runner.invoke(app, ["lineage", "dim_taxi_zone.zone_id", "--format", "json", "-p", str(project.root)])
    assert json.loads(result.output)["column"] == "zone_id"


def test_dbt_columns_carry_their_lineage(project):
    files = generate(project).files
    marts = next(v for k, v in files.items() if k.startswith("dbt/models/marts") and k.endswith("__models.yml"))
    assert "lineage:\n" in marts and "- taxi_zone_lookup.Borough" in marts
