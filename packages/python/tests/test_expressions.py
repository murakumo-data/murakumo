import pytest

from murakumo.expressions import check_expression, preview_expression
from murakumo.scaffold import init_project


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path / "taxi", warehouse="snowflake")


def test_check_finds_columns_and_type(project):
    check = check_expression(project, "yellow_tripdata.total_amount - yellow_tripdata.trip_distance")
    assert check.ok
    assert check.references == ["yellow_tripdata.total_amount", "yellow_tripdata.trip_distance"]
    assert check.type and check.type.startswith("decimal")


def test_bare_names_resolve_through_sources(project):
    check = check_expression(project, "upper(Borough)", ["taxi_zone_lookup.Borough"])
    assert check.ok and check.references == ["taxi_zone_lookup.Borough"] and check.type == "string"
    assert not check_expression(project, "upper(Borough)").ok


def test_check_reports_problems(project):
    assert check_expression(project, "1 +").error == "the expression is incomplete"
    assert check_expression(project, "select 1").error.startswith("write one expression")
    assert check_expression(project, "nowhere.x").problems == ["nowhere.x: there is no entity nowhere"]


def test_warehouse_sql(project):
    check = check_expression(project, "concat_ws('|', yellow_tripdata.VendorID, yellow_tripdata.PULocationID)")
    assert "CAST" in check.warehouse_sql.upper()  # Snowflake needs the casts DuckDB makes implicitly


def test_preview(project):
    pytest.importorskip("duckdb")
    result = preview_expression(project, "yellow_tripdata.total_amount + yellow_tripdata.trip_distance")
    assert result["columns"] == ["yellow_tripdata.total_amount", "yellow_tripdata.trip_distance", "result"]
    assert len(result["rows"]) == 12
    fare, tip, total = (float(v) for v in result["rows"][0])
    assert total == pytest.approx(fare + tip)
