import pytest
from typer.testing import CliRunner

from murakumo.cli import app
from murakumo.generate import generate
from murakumo.scaffold import init_project

runner = CliRunner()


def test_hooks_only_when_the_run_log_is_on(tmp_path):
    project = init_project(tmp_path / "p")
    files = generate(project).files
    assert "murakumo_audit(results)" in files["dbt/dbt_project.yml"]
    assert "dbt/macros/murakumo/audit.sql" in files
    project.manifest.quality.run_log = False
    assert "murakumo_audit" not in generate(project).files["dbt/dbt_project.yml"]


def test_every_run_records_itself(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    pytest.importorskip("dbt.adapters.duckdb")
    from murakumo.generate.verify import build

    project = init_project(tmp_path / "taxi")
    zones = tmp_path / "zones.csv"
    zones.write_text(
        "LocationID,Borough,Zone,service_zone\n1,EWR,Newark Airport,EWR\nnope,Queens,Bad Row,Boro Zone\n"
        "2,Queens,Jamaica Bay,Boro Zone\n"
    )
    result = build(project, sample=20, data={"taxi_zone_lookup": str(zones)}, schedule="daily")
    assert result.loaded["taxi_zone_lookup"] == 2
    con = duckdb.connect(str(project.root / "taxi.duckdb"), read_only=True)
    rows = con.execute("select table_name, rows_ingested, rows_rejected from audit.row_counts").fetchall()
    loads = {name: (ingested, rejected) for name, ingested, rejected in rows}
    assert loads == {"taxi_zone_lookup": (2, 1), "yellow_tripdata": (20, 0)}
    reason = con.execute("select reason from audit.row_counts where table_name = 'taxi_zone_lookup'").fetchone()[0]
    assert '"nope"' in reason
    run = con.execute(
        "select engine, schedule, models_ok + models_failed, tests_passed + tests_failed from audit.run_log"
    ).fetchone()
    assert run == ("dbt", "daily", 4, len(con.execute("select * from audit.dq_results").fetchall()))
    counts = dict(con.execute("select model, rows_in_table from audit.model_runs").fetchall())
    assert counts["dim_taxi_zone"] == 2 and counts["stg_nyc_tlc__taxi_zone_lookup"] is None  # a view is not counted
    day = con.execute("select rows_ingested, rows_rejected from audit.daily_summary").fetchone()
    assert day == (22, 1)
    con.close()
    shown = runner.invoke(app, ["runs", "-p", str(project.root)], env={"COLUMNS": "160"})
    assert shown.exit_code == 0 and "taxi_zone_lookup" in shown.output and "By day" in shown.output
