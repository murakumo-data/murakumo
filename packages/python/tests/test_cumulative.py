from collections import defaultdict

import pytest
import yaml
from typer.testing import CliRunner

from murakumo.cli import app
from murakumo.generate import export_sql, generate
from murakumo.project import load_project
from murakumo.scaffold import init_project
from murakumo.validate import validate_project

runner = CliRunner()
ZONE_ACTIVITY = {
    "name": "zone_activity",
    "layer": "marts",
    "kind": "cumulative",
    "domain": "mobility",
    "grain": ["zone_id"],
    "cumulative": {
        "date": "fct_trip.pickup_at",
        "measures": {"trips": "count(*)", "fares": "sum(fct_trip.total_amount)"},
    },
    "attributes": [{"name": "zone_id", "type": "integer", "nullable": False}],
    "mappings": [{"target": "zone_id", "sources": ["fct_trip.pickup_zone_id"]}],
}


@pytest.fixture
def project(tmp_path):
    project = init_project(tmp_path / "taxi")
    (project.root / "model/entities/marts/zone_activity.yaml").write_text(yaml.safe_dump(ZONE_ACTIVITY))
    return load_project(project.root).project


def test_checks(tmp_path):
    project = init_project(tmp_path / "p")
    bad = {**ZONE_ACTIVITY, "grain": [], "cumulative": None}
    (project.root / "model/entities/marts/zone_activity.yaml").write_text(yaml.safe_dump(bad))
    codes = {d.code for d in validate_project(load_project(project.root).project)}
    assert {"MK310", "MK312"} <= codes


def test_generated_for_every_warehouse(project):
    for warehouse, prepend in [
        ("duckdb", "list_prepend"),
        ("snowflake", "array_prepend"),
        ("bigquery", "array_concat"),
    ]:
        sql = generate(project, warehouse).files["dbt/models/marts/mobility/zone_activity.sql"]
        assert prepend in sql and "unique_key=['zone_id', 'snapshot_date']" in sql
    output = generate(project)
    plain = export_sql(output.plan, output.files)
    script = next(v for k, v in plain.items() if k.endswith("__zone_activity.sql"))
    assert "{{" not in script and "list_prepend" in script


def test_history_accumulates_day_by_day(project):
    duckdb = pytest.importorskip("duckdb")
    pytest.importorskip("dbt.adapters.duckdb")
    from murakumo.generate.verify import build

    assert build(project, sample=300).ok
    con = duckdb.connect(str(project.root / "taxi.duckdb"), read_only=True)
    last = con.execute("select max(cast(pickup_at as date)) from marts.fct_trip").fetchone()[0]
    con.close()
    first = (last.replace(day=1)).isoformat()
    result = runner.invoke(
        app, ["backfill", "zone_activity", "--from", first, "--to", last.isoformat(), "-p", str(project.root)]
    )
    assert result.exit_code == 0, result.output
    con = duckdb.connect(str(project.root / "taxi.duckdb"), read_only=True)
    expected = defaultdict(list)
    for zone, day, trips in con.execute(
        "select pickup_zone_id, cast(pickup_at as date), count(*) from marts.fct_trip "
        f"where cast(pickup_at as date) between '{first}' and '{last}' and pickup_zone_id is not null group by 1, 2"
    ).fetchall():
        expected[zone].append((day, trips))
    rows = con.execute(
        f"select zone_id, dates_active, trips, days_active from marts.zone_activity where snapshot_date = '{last}'"
    ).fetchall()
    assert len(rows) == len(expected) > 0
    for zone, dates, trips, days in rows:
        want = sorted(expected[zone], reverse=True)
        assert dates == [d for d, _ in want] and trips == [n for _, n in want] and days == len(want)
