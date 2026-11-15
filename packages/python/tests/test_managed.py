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


def test_dlt_turns_columns_of_values_into_a_row_each(tmp_path):
    # Some APIs answer {"time": [...], "temperature": [...]}: one row per position, not one row.
    code = generate(taxi(tmp_path, "dlt", zone_files())).files["ingestion/dlt/pipeline.py"]
    assert "add_yield_map(rows)" in code
    tree = ast.parse(code)
    wanted = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ("stamp", "rows")]
    scope = {"NOW": "now", "BATCH": "b1"}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), "pipeline", "exec"), scope)  # noqa: S102
    made = list(scope["rows"]({"time": ["t1", "t2"], "temperature": [1.5, 2.5]}))
    assert [(r["time"], r["temperature"], r["_batch_id"]) for r in made] == [("t1", 1.5, "b1"), ("t2", 2.5, "b1")]
    # An ordinary record, lists of different lengths included, stays one row.
    assert len(list(scope["rows"]({"id": 1, "tags": ["a", "b"]}))) == 1
    assert len(list(scope["rows"]({"a": [1], "b": [1, 2]}))) == 1


def test_ingest_runs_the_dlt_pipeline(tmp_path, monkeypatch):
    pytest.importorskip("dlt")
    pytest.importorskip("pandas")
    from typer.testing import CliRunner

    from murakumo.cli import app
    from murakumo.project import load_project

    root = init_project(tmp_path / "taxi").root
    with (root / "murakumo.yaml").open("a") as file:
        file.write(
            "\nstack:\n  extract: dlt\nsources:\n  - name: nyc_tlc\n    connection:\n      kind: files\n"
            "      location: incoming\n      files:\n        - {table: taxi_zone_lookup, pattern: 'zones_*.csv'}\n"
        )
    project = load_project(root).project
    write(generate(project), project.root)
    (project.root / "incoming").mkdir()
    (project.root / "incoming" / "zones_1.csv").write_text(
        "LocationID,Borough,Zone,service_zone\n1,EWR,Newark Airport,EWR\n"
    )
    db = tmp_path / "taxi.duckdb"
    monkeypatch.setenv("MURAKUMO_DUCKDB_PATH", str(db))
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt"))
    done = CliRunner().invoke(app, ["ingest", "-p", str(project.root)])
    assert done.exit_code == 0, done.output
    assert "Loaded into the raw tables by dlt" in done.output
    with duckdb.connect(str(db), read_only=True) as connection:
        assert connection.execute("select count(*) from raw_nyc_tlc.taxi_zone_lookup").fetchone()[0] == 1


def test_dlt_takes_over_a_raw_table_that_already_has_rows(tmp_path):
    # Loaded by Murakumo first, then the stack changes to dlt: dlt's own columns cannot be added
    # NOT NULL to a table with rows, so the pipeline adds them itself.
    pytest.importorskip("dlt")
    pytest.importorskip("pandas")
    project = taxi(tmp_path, "dlt", zone_files())
    with duckdb.connect(str(tmp_path / "taxi.duckdb")) as connection:
        connection.execute("create schema raw_nyc_tlc")
        connection.execute(
            'create table raw_nyc_tlc.taxi_zone_lookup as select 9 as "LocationID", \'Old\' as "Borough", '
            "'Old' as \"Zone\", 'Old' as service_zone, now() as _ingested_at, 'b0' as _batch_id"
        )
    (project.root / "incoming").mkdir()
    (project.root / "incoming" / "zones_1.csv").write_text(
        "LocationID,Borough,Zone,service_zone\n1,EWR,Newark Airport,EWR\n"
    )
    db = run_pipeline(project, tmp_path)
    assert db.execute('select "LocationID" from raw_nyc_tlc.taxi_zone_lookup').fetchall() == [(1,)]
    db.close()
