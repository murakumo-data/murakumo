"""End to end: generate, load DuckDB, run dbt build twice. Each test takes a few seconds."""

from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from murakumo.cli import app
from murakumo.generate.sample import sample_rows
from murakumo.generate.verify import verify
from murakumo.project import load_project
from murakumo.scaffold import init_project

runner = CliRunner()
COMMERCE = Path(__file__).resolve().parents[3] / "examples" / "commerce"


def test_sample_rows_follow_the_model():
    model = load_project(COMMERCE).project.model
    raw = [e for e in model.entities if e.layer.value == "raw"]
    data = sample_rows(model, raw, rows=50)
    partsupp = {(r["ps_partkey"], r["ps_suppkey"]) for r in data["partsupp"]}
    assert len(partsupp) == len(data["partsupp"])  # composite key unique
    parts = {r["p_partkey"] for r in data["part"]}
    assert all(r["ps_partkey"] in parts for r in data["partsupp"])
    for line in data["lineitem"]:  # the composite foreign key points at a real partsupp row
        assert (line["l_partkey"], line["l_suppkey"]) in partsupp
        assert line["l_discount"] is None or 0 <= line["l_discount"] <= 1  # follows the range test
    assert {r["o_orderstatus"] for r in data["orders"]} <= {"F", "O", "P", None}


def test_verify_the_taxi_example(tmp_path):
    result = verify(init_project(tmp_path / "taxi"), rows=100)
    assert result.ok, [(n.name, n.status, n.message) for n in result.nodes if n.status not in ("success", "pass")]
    assert result.rerun is True
    assert result.count("model") == 4 and result.count("test", "pass") == result.count("test")


def test_verify_the_commerce_example():
    result = verify(load_project(COMMERCE).project, rows=100)
    assert result.ok, [(n.name, n.status, n.message) for n in result.nodes if n.status not in ("success", "pass")]
    assert result.count("snapshot") == 1
    assert result.count("test") > 80


def test_verify_with_a_real_file(tmp_path):
    project = init_project(tmp_path / "taxi")
    zones = tmp_path / "zones.csv"
    zones.write_text("LocationID,Borough,Zone,service_zone\n1,EWR,Newark Airport,EWR\n2,Queens,Jamaica Bay,Boro Zone\n")
    result = verify(project, rows=50, data={"taxi_zone_lookup": str(zones)}, rerun=False)
    assert result.loaded["taxi_zone_lookup"] == 2
    # Synthetic trips point at zones that the real file does not have, so the test must catch it.
    failing = {n.name for n in result.nodes if n.status == "fail"}
    assert any(name.startswith("relationships_stg_nyc_tlc__yellow_tripdata") for name in failing)


def test_cli_verify_keeps_the_build(tmp_path):
    project = init_project(tmp_path / "taxi")
    keep = tmp_path / "build"
    result = runner.invoke(app, ["verify", "-p", str(project.root), "--rows", "50", "--keep", str(keep), "--once"])
    assert result.exit_code == 0, result.output
    assert "verified" in result.output
    connection = duckdb.connect(str(keep / "verify.duckdb"), read_only=True)
    assert connection.execute("select count(*) from marts.fct_trip").fetchone()[0] == 50
    connection.close()


@pytest.mark.parametrize("bad", ["nothing", "a="])
def test_cli_verify_rejects_bad_data_arguments(tmp_path, bad):
    project = init_project(tmp_path / "taxi")
    result = runner.invoke(app, ["verify", "-p", str(project.root), "--data", bad])
    assert result.exit_code == 1


def test_a_file_missing_a_model_column_is_reported_clearly(tmp_path):
    project = init_project(tmp_path / "taxi")
    zones = tmp_path / "zones.csv"
    zones.write_text("LocationID,Borough,Zone\n1,EWR,Newark Airport\n")  # no service_zone
    result = verify(project, rows=20, data={"taxi_zone_lookup": str(zones)}, rerun=False)
    assert not result.ok
    assert "zones.csv has no column service_zone, which the model expects" in result.error
    assert "The file has: LocationID, Borough, Zone" in result.error
