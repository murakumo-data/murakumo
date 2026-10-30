import ast
import json
import subprocess
import sys

import pytest
import yaml

from murakumo.generate import generate
from murakumo.generate.python import render
from murakumo.project import load_project
from murakumo.scaffold import init_project

ZONE_ACTIVITY = {
    "name": "zone_activity",
    "layer": "marts",
    "kind": "cumulative",
    "grain": ["zone_id"],
    "cumulative": {"date": "fct_trip.pickup_at", "measures": {"trips": "count(*)"}},
    "attributes": [{"name": "zone_id", "type": "integer", "nullable": False}],
    "mappings": [{"target": "zone_id", "sources": ["fct_trip.pickup_zone_id"]}],
}


@pytest.fixture
def project(tmp_path):
    project = init_project(tmp_path / "taxi")
    (project.root / "model/entities/marts/zone_activity.yaml").write_text(yaml.safe_dump(ZONE_ACTIVITY))
    return load_project(project.root).project


@pytest.mark.parametrize("engine", ["polars", "pyspark"])
def test_generated_code_parses(project, engine):
    files = render(project, engine)
    for path, content in files.items():
        if path.endswith(".py"):
            ast.parse(content, filename=path)
    assert [p for p in files if p.startswith("models/0")] == [
        "models/01_stg_nyc_tlc__taxi_zone_lookup.py",
        "models/02_stg_nyc_tlc__yellow_tripdata.py",
        "models/03_dim_taxi_zone.py",
        "models/04_fct_trip.py",
        "models/05_zone_activity.py",
    ]
    config = json.loads(files["project.json"])
    assert config["raw"][0]["view"] == "raw_nyc_tlc__taxi_zone_lookup"
    assert config["schedules"]["daily"]  # the default schedule builds everything
    assert "{{" not in files["models/04_fct_trip.py"]


def test_only_with_the_setting(project):
    assert not any(p.startswith("python/") for p in generate(project).files)
    project.manifest.generation.python_engine = "polars"
    assert "python/polars/run.py" in generate(project).files


def test_polars_builds_what_dbt_builds(project, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    pytest.importorskip("polars")
    pytest.importorskip("dbt.adapters.duckdb")
    from murakumo.generate.verify import build

    assert build(project, sample=150).ok
    folder = tmp_path / "pipeline"
    for path, content in render(project, "polars").items():
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_text(content)
    lake = tmp_path / "lake"
    database = str(project.root / "taxi.duckdb")
    for _ in range(2):  # the second run takes the incremental and cumulative paths
        done = subprocess.run(
            [sys.executable, "run.py", "--duckdb", database, "--output", str(lake)],
            cwd=folder,
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0 and "success" in done.stdout, done.stdout + done.stderr
    con = duckdb.connect(database, read_only=True)
    skip = {"_updated_at"}
    for table in ("dim_taxi_zone", "fct_trip", "zone_activity"):
        columns = [r[0] for r in con.execute(f"describe select * from marts.{table}").fetchall() if r[0] not in skip]
        listed = ", ".join(columns)
        theirs = f"read_parquet('{lake}/marts/{table}.parquet')"
        for left, right in ((f"marts.{table}", theirs), (theirs, f"marts.{table}")):
            extra = con.execute(
                f"select count(*) from (select {listed} from {left} except all select {listed} from {right})"
            )
            assert extra.fetchone()[0] == 0, table
    runs = con.execute(
        f"select engine, status, tests_failed from read_parquet('{lake}/audit/run_log.parquet')"
    ).fetchall()
    assert runs == [("polars", "success", 0), ("polars", "success", 0)]
