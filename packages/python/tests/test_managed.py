"""Ingestion by dlt, Airbyte or Fivetran. The dlt pipeline is run for real, into DuckDB."""

import ast
import json
import os
import sqlite3
import subprocess
import sys

import pytest
import sqlglot

from murakumo import stack
from murakumo.generate import generate, write
from murakumo.generate.targets import target_for
from murakumo.manifest import (
    Auth,
    DatabaseConnection,
    Endpoint,
    FilesConnection,
    FileSet,
    Paging,
    Query,
    RestConnection,
    SourceDeclaration,
    Warehouse,
)
from murakumo.scaffold import init_project

duckdb = pytest.importorskip("duckdb")


def taxi(tmp_path, extract, connection):
    project = init_project(tmp_path / "taxi")
    project.manifest.stack.extract = extract
    declaration = next((d for d in project.manifest.sources if d.name == "nyc_tlc"), None)
    if declaration is None:
        declaration = SourceDeclaration(name="nyc_tlc")
        project.manifest.sources.append(declaration)
    declaration.connection = connection
    return project


def zone_files():
    return FilesConnection(location="incoming", files=[FileSet(table="taxi_zone_lookup", pattern="zones_*.csv")])


def run_pipeline(project, tmp_path, env=None):
    write(generate(project), project.root)
    script = project.root / "generated" / "ingestion" / "dlt" / "pipeline.py"
    db = tmp_path / "taxi.duckdb"
    done = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "MURAKUMO_DUCKDB_PATH": str(db),
            "DLT_DATA_DIR": str(tmp_path / "dlt"),
            "RUNTIME__DLTHUB_TELEMETRY": "false",
            **(env or {}),
        },
    )
    assert done.returncode == 0, done.stderr[-3000:]
    return duckdb.connect(str(db), read_only=True)


def test_dlt_loads_files_into_the_raw_table_as_the_model_names_it(tmp_path):
    pytest.importorskip("dlt")
    pytest.importorskip("pandas")
    project = taxi(tmp_path, "dlt", zone_files())
    (project.root / "incoming").mkdir()
    (project.root / "incoming" / "zones_1.csv").write_text(
        "LocationID,Borough,Zone,service_zone\n1,EWR,Newark Airport,EWR\n2,Queens,Jamaica Bay,Boro Zone\n"
    )
    db = run_pipeline(project, tmp_path)
    columns = [r[0] for r in db.execute("describe raw_nyc_tlc.taxi_zone_lookup").fetchall()]
    assert {"LocationID", "Borough", "Zone", "service_zone", "_ingested_at", "_batch_id"} <= set(columns)
    rows = db.execute(
        'select "LocationID", "Borough", _batch_id from raw_nyc_tlc.taxi_zone_lookup order by 1'
    ).fetchall()
    assert [r[:2] for r in rows] == [(1, "EWR"), (2, "Queens")] and rows[0][2].startswith("dlt_")
    db.close()
    # A second file arrives. The table is loaded in full, so it is every file's rows, once each.
    (project.root / "incoming" / "zones_2.csv").write_text(
        "LocationID,Borough,Zone,service_zone\n3,Bronx,Allerton,Boro Zone\n"
    )
    db = run_pipeline(project, tmp_path)
    assert db.execute("select count(*) from raw_nyc_tlc.taxi_zone_lookup").fetchone()[0] == 3
    # Staging, as generated, reads what dlt loaded.
    files = generate(project).files
    staging = files["dbt/models/staging/nyc_tlc/stg_nyc_tlc__taxi_zone_lookup.sql"]
    assert 'source."LocationID"' in staging and "source._ingested_at" in staging
    db.close()


def test_dlt_loads_a_database_table(tmp_path, monkeypatch):
    pytest.importorskip("dlt")
    pytest.importorskip("sqlalchemy")
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute(
            'create table taxi_zone_lookup ("LocationID" integer, "Borough" text, "Zone" text, service_zone text)'
        )
        connection.execute(
            "insert into taxi_zone_lookup values "
            "(1, 'EWR', 'Newark Airport', 'EWR'), (4, 'Manhattan', 'Alphabet City', 'Yellow Zone')"
        )
    project = taxi(
        tmp_path,
        "dlt",
        DatabaseConnection(url_env="ZONES_URL", tables=[Query(table="taxi_zone_lookup", source="taxi_zone_lookup")]),
    )
    db = run_pipeline(project, tmp_path, {"ZONES_URL": f"sqlite:///{source}"})
    rows = db.execute('select "LocationID", "Zone" from raw_nyc_tlc.taxi_zone_lookup order by 1').fetchall()
    assert rows == [(1, "Newark Airport"), (4, "Alphabet City")]
    db.close()


def test_dlt_rest_settings_are_ones_dlt_accepts(tmp_path, monkeypatch):
    pytest.importorskip("dlt")
    monkeypatch.setenv("ZONES_TOKEN", "t")
    connection = RestConnection(
        base_url="https://api.example.test/v1",
        auth=Auth(type="bearer", env="ZONES_TOKEN"),
        paging=Paging(type="page", param="page", size_param="per_page", size=50),
        endpoints=[Endpoint(table="taxi_zone_lookup", path="/zones", records="data")],
    )
    code = generate(taxi(tmp_path, "dlt", connection)).files["ingestion/dlt/pipeline.py"]
    ast.parse(code)
    assert 'os.environ["ZONES_TOKEN"]' in code and '"ZONES_TOKEN"' in code and "@@" not in code
    scope: dict = {"__name__": "pipeline", "__file__": str(tmp_path / "a" / "b" / "c" / "pipeline.py")}
    exec(compile(code, "pipeline.py", "exec"), scope)  # noqa: S102 - the code under test
    dataset, resources = scope["nyc_tlc"]()  # builds the source: dlt checks the settings, no request is sent
    assert dataset == "raw_nyc_tlc" and [r.name for r in resources] == ["taxi_zone_lookup"]


@pytest.mark.parametrize("tool", ["airbyte", "fivetran"])
def test_tools_get_what_to_set_up_and_views_over_what_they_land(tmp_path, tool):
    project = taxi(tmp_path, tool, zone_files())
    for warehouse in Warehouse:
        files = generate(project, warehouse).files
        sql = files[f"ingestion/{tool}/views.sql"]
        for statement in sqlglot.parse(sql, read=target_for(warehouse).dialect):
            assert statement is not None
        assert "_ingested_at" in sql and "_batch_id" in sql
        assert not [p for p in files if p.startswith("ingestion/python/")]  # the tool fetches, not our scripts
    files = generate(project, "snowflake").files
    sql = files[f"ingestion/{tool}/views.sql"]
    assert f"FROM raw_nyc_tlc_{tool}.taxi_zone_lookup" in sql
    assert "CREATE OR REPLACE VIEW raw_nyc_tlc.taxi_zone_lookup" in sql
    if tool == "fivetran":
        assert 'location_id AS "LocationID"' in sql and "_fivetran_synced AS _ingested_at" in sql
        assert "COALESCE(_fivetran_deleted, FALSE) = FALSE" in sql
    else:
        assert '"LocationID" AS "LocationID"' in sql and "_airbyte_extracted_at AS _ingested_at" in sql
    plan = json.loads(files[f"ingestion/{tool}/connections.json"])
    assert plan[0]["destination_schema"] == f"raw_nyc_tlc_{tool}"
    table = plan[0]["tables"][0]
    assert table["table"] == "taxi_zone_lookup" and table["primary_key"] == ["LocationID"]
    guide = "".join(content for path, content in files.items() if path.startswith("guide/") and path.endswith(".md"))
    assert f"ingestion/{tool}/README.md" in files and f"generated/ingestion/{tool}/views.sql" in guide


def test_stack_knows_the_tools(tmp_path):
    project = taxi(tmp_path, "dlt", zone_files())
    assert not [p for p in stack.check(project) if p[0] == "error"]
    project.manifest.stack.load = "snowpipe"
    project.manifest.targets.warehouse.type = Warehouse.SNOWFLAKE
    assert any(level == "error" and part == "load" for level, part, _ in stack.check(project))


def test_orchestrators_run_the_dlt_pipeline_and_skip_the_load(tmp_path):
    from murakumo import schedules
    from murakumo.manifest import Orchestrator

    project = init_project(tmp_path / "taxi")
    schedules.add_schedule(project, "zones", "0 * * * *", ["+dim_taxi_zone"])  # saves, and reads the manifest again
    project.manifest.stack.extract = "dlt"
    project.manifest.sources.append(SourceDeclaration(name="nyc_tlc", connection=zone_files()))
    dag = generate(project).files["orchestration/airflow/dags/taxi__zones.py"]
    assert "{DLT_DIR}/pipeline.py nyc_tlc" in dag and "murakumo load" not in dag
    ast.parse(dag)
    project.manifest.generation.orchestrator = Orchestrator.PREFECT
    flows = generate(project).files["orchestration/prefect/flows.py"]
    assert "DLT_SOURCES = ['nyc_tlc']" in flows and "\n    load_landed()\n" not in flows
    ast.parse(flows)
