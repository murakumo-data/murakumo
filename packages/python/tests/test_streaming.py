"""Kafka sources, the streaming engines' code, and open tables (Iceberg) per warehouse."""

import py_compile

import pytest
import yaml

from murakumo import stack
from murakumo.generate import generate
from murakumo.importers.files import file_format
from murakumo.loading import expand
from murakumo.manifest import LakehouseTarget, Warehouse
from murakumo.project import load_project
from murakumo.scaffold import init_project

KAFKA = {
    "kind": "kafka",
    "bootstrap_servers": "localhost:19092",
    "topics": [{"table": "taxi_zone_lookup", "topic": "zones"}],
}


def reload(project):
    return load_project(project.root).project


@pytest.fixture
def project(tmp_path):
    project = init_project(tmp_path / "taxi")
    stack.set_connection(project, "nyc_tlc", KAFKA, why="zones arrive on a topic")
    return reload(project)


def test_kafka_is_a_connection_kind(project):
    declaration = project.manifest.sources[0]
    assert declaration.connection.kind == "kafka" and declaration.tables() == ["taxi_zone_lookup"]
    files = generate(project).files
    assert "kafka-python>=2.0" in files["ingestion/python/requirements.txt"]
    assert not any(p.startswith("streaming/") for p in files)  # the consumer is the extract script


def test_flink_jobs_land_files_where_the_loads_look(project):
    stack.set_parts(project, {"stream": "flink"})
    files = generate(reload(project)).files
    sql = files["streaming/flink/nyc_tlc.sql"]
    assert "'connector' = 'kafka'" in sql and "'topic' = 'zones'" in sql
    assert (
        "'path' = 'file:///project/landing/nyc_tlc/taxi_zone_lookup'" in sql
        and "INSERT INTO taxi_zone_lookup_landing" in sql
    )
    assert "`LocationID` BIGINT" in sql
    assert "redpanda" in files["streaming/docker-compose.yml"]
    load = next(v for k, v in files.items() if k.startswith("guide/sql/load/") and "taxi_zone_lookup" in k)
    assert "[!._]*" in load


def test_spark_job_parses(project, tmp_path):
    stack.set_parts(project, {"stream": "spark"})
    files = generate(reload(project)).files
    target = tmp_path / "job.py"
    target.write_text(files["streaming/spark/nyc_tlc.py"])
    py_compile.compile(str(target), doraise=True)
    assert 'option("subscribe", topic)' in files["streaming/spark/nyc_tlc.py"]


def test_snowpipe_streaming_needs_snowflake(project):
    stack.set_parts(project, {"stream": "snowpipe_streaming"})
    assert ("error", "stream", "Snowpipe Streaming writes into Snowflake; the warehouse is duckdb") in stack.check(
        reload(project)
    )
    stack.set_parts(project, {"warehouse": "snowflake"})
    files = generate(reload(project)).files
    config = yaml.safe_load(files["streaming/kafka-connect/nyc_tlc.json"])["config"]
    assert config["snowflake.ingestion.method"] == "SNOWPIPE_STREAMING"
    assert config["snowflake.topic2table.map"] == "zones:TAXI_ZONE_LOOKUP"


def test_streaming_part_files_are_read_as_json(tmp_path):
    (tmp_path / "part-1a2b-0-0").write_text('{"a": 1}\n')
    (tmp_path / ".part-1a2b-0-1.inprogress.x").write_text("")
    (tmp_path / "zones_20260101T000000000000Z_1.jsonl").write_text('{"a": 2}\n')
    assert [p.rsplit("/", 1)[-1] for p in expand(str(tmp_path))] == [
        "part-1a2b-0-0",
        "zones_20260101T000000000000Z_1.jsonl",
    ]
    assert file_format(str(tmp_path / "part-1a2b-0-0")) == "json"


# open tables ------------------------------------------------------------------------------------


def lake(project, **settings):
    project.manifest.targets.lakehouse = LakehouseTarget(**settings)
    return project


def test_iceberg_on_snowflake(project):
    files = generate(lake(project, table_format="iceberg", catalog="polaris"), Warehouse.SNOWFLAKE).files
    fact = files["dbt/models/marts/mobility/fct_trip.sql"]
    assert "table_format='iceberg'" in fact and "external_volume='TAXI_LAKE'" in fact
    assert "create external volume if not exists TAXI_LAKE" in files["lakehouse/snowflake.sql"]
    assert "catalog_source = polaris" in files["lakehouse/snowflake.sql"]
    staging = next(v for k, v in files.items() if k.startswith("dbt/models/staging/") and k.endswith(".sql"))
    assert "iceberg" not in staging  # only the lakehouse's layers


def test_iceberg_and_delta_on_databricks(project):
    files = generate(lake(project, table_format="iceberg"), Warehouse.DATABRICKS).files
    assert "table_format='iceberg'" in files["dbt/models/marts/mobility/fct_trip.sql"]
    files = generate(lake(project, table_format="delta"), Warehouse.DATABRICKS).files
    assert "file_format='delta'" in files["dbt/models/marts/mobility/fct_trip.sql"]


def test_iceberg_from_duckdb_into_a_local_catalog(project):
    files = generate(lake(project, table_format="iceberg", catalog="polaris", warehouse="taxi")).files
    assert "murakumo_publish_iceberg('iceberg_catalog')" in files["dbt/models/marts/mobility/fct_trip.sql"]
    profile = yaml.safe_load(files["dbt/profiles.yml"])["taxi"]["outputs"]["prod"]
    assert profile["attach"][0]["type"] == "iceberg" and profile["attach"][0]["alias"] == "iceberg_catalog"
    assert {s["type"] for s in profile["secrets"]} == {"s3", "iceberg"}
    assert (
        "apache/polaris" in files["lakehouse/docker-compose.yml"]
        and "localstack" in files["lakehouse/docker-compose.yml"]
    )
    assert "create table {{ target }} as select * from {{ this }}" in files["dbt/macros/murakumo/publish_iceberg.sql"]


def test_open_tables_that_do_not_go_together(project):
    project = lake(project, table_format="delta")
    assert any(part == "warehouse" and "Iceberg catalog" in message for _, part, message in stack.check(project))
