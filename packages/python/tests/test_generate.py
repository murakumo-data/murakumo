from pathlib import Path

import pytest
import sqlglot
import yaml
from typer.testing import CliRunner

from murakumo.cli import app
from murakumo.generate import GENERATED_DIR, INDEX_FILE, compare, generate, write
from murakumo.generate.plan import build_plan, snake
from murakumo.generate.targets import target_for
from murakumo.ir.model import Mapping
from murakumo.ir.types import parse_type
from murakumo.manifest import Warehouse
from murakumo.project import load_project
from murakumo.scaffold import init_project

runner = CliRunner()
COMMERCE = Path(__file__).resolve().parents[3] / "examples" / "commerce"


@pytest.fixture
def taxi(tmp_path):
    return init_project(tmp_path / "taxi")


def plan_for(project, warehouse="duckdb"):
    return build_plan(project, target_for(warehouse))


# naming and types -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("PULocationID", "pu_location_id"),
        ("VendorID", "vendor_id"),
        ("tpep_pickup_datetime", "tpep_pickup_datetime"),
        ("Airport_fee", "airport_fee"),
        ("HTTPServer2Name", "http_server2_name"),
    ],
)
def test_snake(name, expected):
    assert snake(name) == expected


@pytest.mark.parametrize(
    ("warehouse", "logical", "physical"),
    [
        ("duckdb", "decimal(10,2)", "DECIMAL(10,2)"),
        ("duckdb", "array<bigint>", "BIGINT[]"),
        ("snowflake", "integer", "NUMBER(10,0)"),
        ("snowflake", "money", "NUMBER(38,4)"),
        ("snowflake", "timestamp", "TIMESTAMP_NTZ"),
        ("snowflake", "json", "VARIANT"),
        ("databricks", "decimal", "DECIMAL(38,9)"),
        ("databricks", "timestamp_tz", "TIMESTAMP"),
        ("bigquery", "decimal(50,10)", "BIGNUMERIC(50,10)"),
        ("bigquery", "string(20)", "STRING(20)"),
        ("postgres", "json", "JSONB"),
        ("redshift", "string", "VARCHAR(65535)"),
        ("fabric", "boolean", "BIT"),
    ],
)
def test_physical_types(warehouse, logical, physical):
    assert target_for(warehouse).physical(parse_type(logical)) == physical


# planning ---------------------------------------------------------------------------------------


def test_staging_renames_casts_and_audits(taxi):
    plan = plan_for(taxi)
    staging = plan.model("stg_nyc_tlc__yellow_tripdata")
    names = [c.name for c in staging.columns]
    assert names[:4] == ["vendor_id", "tpep_pickup_datetime", "tpep_dropoff_datetime", "pu_location_id"]
    assert names[-4:] == ["_ingested_at", "_batch_id", "_source_system", "_record_hash"]
    assert staging.columns[0].sql == 'cast(source."VendorID" as INTEGER)'
    assert staging.relation == "{{ source('nyc_tlc', 'yellow_tripdata') }}"


def test_mapping_expressions_are_rewritten_per_dialect(taxi):
    for warehouse, fragment in [
        ("duckdb", "CONCAT_WS('|', CAST(yellow_tripdata.vendor_id AS TEXT)"),
        # SQLGlot keeps DuckDB's null handling with COALESCE; the cast makes that valid for numbers.
        ("snowflake", "COALESCE(CAST(yellow_tripdata.vendor_id AS VARCHAR), '')"),
        ("bigquery", "TO_HEX(MD5("),
    ]:
        trip_id = plan_for(taxi, warehouse).model("fct_trip").columns[0]
        assert fragment in trip_id.sql
        assert "VendorID" not in trip_id.sql  # raw names became staged names


def test_updated_at_is_cast_to_the_contract_type(taxi):
    # Snowflake's current_timestamp is TIMESTAMP_LTZ; the contract says TIMESTAMP_TZ.
    updated = plan_for(taxi, "snowflake").model("fct_trip").columns[-1]
    assert updated.sql == "cast(current_timestamp as TIMESTAMP_TZ)" and updated.data_type == "TIMESTAMP_TZ"


def test_incremental_fact(taxi):
    fact = plan_for(taxi).model("fct_trip")
    assert fact.materialized == "incremental"
    assert fact.unique_key == ["trip_id"]
    assert "cast('1900-01-01' as TIMESTAMP)" in fact.incremental_filter


def test_composite_keys_are_tested_as_a_combination():
    project = load_project(COMMERCE).project
    plan = plan_for(project)
    lineitem = plan.model("stg_tpch_erp__lineitem")
    assert not any(t.name == "unique" for c in lineitem.columns for t in c.tests)
    assert any(t.name == "murakumo_unique_combination" for t in lineitem.tests)
    fact = plan.model("fct_order_line")
    assert fact.unique_key == ["order_id", "line_number"]


def test_scd2_dimension_uses_a_snapshot():
    plan = plan_for(load_project(COMMERCE).project)
    assert [s.name for s in plan.snapshots] == ["snp_dim_customer"]
    dimension = plan.model("dim_customer")
    assert dimension.relation == "{{ ref('snp_dim_customer') }}"
    assert {"valid_from", "valid_to", "is_current"} <= {c.name for c in dimension.columns}
    assert plan.model("int_dim_customer__versions").layer == "intermediate"


def test_joins_follow_relationships():
    fact = plan_for(load_project(COMMERCE).project).model("fct_order_line")
    [join] = fact.joins
    assert join.alias == "orders" and join.on == "lineitem.l_orderkey = orders.o_orderkey"


def test_relationship_tests_between_generated_models(taxi):
    fact = plan_for(taxi).model("fct_trip")
    pickup = next(c for c in fact.columns if c.name == "pickup_zone_id")
    assert any(t.name == "relationships" and t.args["field"] == "zone_id" for t in pickup.tests)


def test_planning_problems_skip_only_that_entity(taxi):
    entity = taxi.model.entity("dim_taxi_zone")
    # Reading both taxi tables needs one relationship between them, but there are two.
    entity.mappings = [
        *entity.mappings,
        Mapping(target="borough", sources=["yellow_tripdata.VendorID", "taxi_zone_lookup.Borough"], expression="1"),
    ]
    entity.mappings = [entity.mappings[0], entity.mappings[-1]]
    plan = plan_for(taxi)
    assert [d.code for d in plan.diagnostics if d.level.value == "error"] == ["MK501"]
    assert plan.model("dim_taxi_zone") is None and plan.model("fct_trip") is not None


def test_combining_columns_needs_an_expression(taxi):
    taxi.model.entity("dim_taxi_zone").mappings[1] = Mapping(
        target="borough", sources=["taxi_zone_lookup.Borough", "taxi_zone_lookup.Zone"]
    )
    assert "MK502" in {d.code for d in plan_for(taxi).diagnostics}


def test_entity_without_mappings_is_reported(taxi):
    taxi.model.entity("dim_taxi_zone").mappings = []
    assert "MK504" in {d.code for d in plan_for(taxi).diagnostics}


# rendering --------------------------------------------------------------------------------------


@pytest.mark.parametrize("warehouse", [w.value for w in Warehouse])
def test_every_warehouse_renders_and_its_ddl_parses(warehouse):
    output = generate(load_project(COMMERCE).project, warehouse)
    dialect = target_for(warehouse).dialect
    for name in ("raw.sql", "marts.sql"):
        sql = output.files[f"ddl/{warehouse}/{name}"]
        sqlglot.parse(sql, read=dialect)  # raises on invalid SQL
    profiles = yaml.safe_load(output.files["dbt/profiles.yml"])
    assert profiles["commerce"]["outputs"]


def test_dbt_yaml_is_valid_and_complete(taxi):
    files = generate(taxi).files
    project = yaml.safe_load(files["dbt/dbt_project.yml"])
    assert project["name"] == "taxi" and project["models"]["taxi"]["marts"]["+schema"] == "marts"
    marts = yaml.safe_load(files["dbt/models/marts/mobility/_mobility__models.yml"])
    fact = next(m for m in marts["models"] if m["name"] == "fct_trip")
    assert fact["config"]["contract"]["enforced"] is True
    assert all("data_type" in c for c in fact["columns"])
    sources = yaml.safe_load(files["dbt/models/staging/nyc_tlc/_nyc_tlc__sources.yml"])
    # verify on a shared warehouse prefixes every schema of a run
    assert sources["sources"][0]["schema"] == "{{ env_var('MURAKUMO_SCHEMA_PREFIX', '') }}raw_nyc_tlc"
    assert "MURAKUMO_SCHEMA_PREFIX" in files["dbt/macros/murakumo/schema_names.sql"]


def test_warehouse_database_ignores_duckdb_file_names(taxi):
    from murakumo.generate.dbt import warehouse_database

    assert taxi.manifest.targets.warehouse.database == "taxi.duckdb"
    assert warehouse_database(taxi.manifest) == "TAXI"
    profiles = yaml.safe_load(generate(taxi, "snowflake").files["dbt/profiles.yml"])
    assert profiles["taxi"]["outputs"]["dev"]["database"] == "{{ env_var('SNOWFLAKE_DATABASE', 'TAXI') }}"
    duckdb_profile = yaml.safe_load(generate(taxi).files["dbt/profiles.yml"])
    assert "taxi.duckdb" in duckdb_profile["taxi"]["outputs"]["dev"]["path"]


def test_raw_ddl_takes_a_schema_prefix(taxi):
    from murakumo.generate.ddl import DDL

    sql = DDL(plan_for(taxi, "snowflake"), schema_prefix="verify_1_").raw()
    assert "CREATE SCHEMA IF NOT EXISTS verify_1_raw_nyc_tlc;" in sql
    assert 'verify_1_raw_nyc_tlc.yellow_tripdata (\n    "VendorID" NUMBER(10,0)' in sql


def test_generation_is_deterministic(taxi):
    assert generate(taxi).files == generate(load_project(taxi.root).project).files


# writing ----------------------------------------------------------------------------------------


def test_write_check_and_cleanup(taxi):
    output = generate(taxi)
    changes = write(output, taxi.root)
    assert len(changes.added) == len(output.files)
    assert not compare(generate(taxi), taxi.root).any
    assert (taxi.root / GENERATED_DIR / INDEX_FILE).is_file()

    (taxi.root / "custom" / "my_model.sql").write_text("select 1")
    taxi.model.entities = [e for e in taxi.model.entities if e.name != "fct_trip"]
    taxi.model.relationships = [r for r in taxi.model.relationships if r.from_ != "fct_trip"]
    taxi.model.metrics = []
    changes = write(generate(taxi), taxi.root)
    assert "dbt/models/marts/mobility/fct_trip.sql" in changes.removed
    assert not (taxi.root / GENERATED_DIR / "dbt/models/marts/mobility/fct_trip.sql").exists()
    assert (taxi.root / "custom" / "my_model.sql").is_file()  # never touched


def test_hand_edits_are_detected(taxi):
    write(generate(taxi), taxi.root)
    target = taxi.root / GENERATED_DIR / "dbt/models/marts/mobility/fct_trip.sql"
    target.write_text(target.read_text() + "\n-- my change\n")
    changes = compare(generate(taxi), taxi.root)
    assert changes.edited_by_hand == ["dbt/models/marts/mobility/fct_trip.sql"]


def test_files_murakumo_did_not_write_are_left_alone(taxi):
    write(generate(taxi), taxi.root)
    mine = taxi.root / GENERATED_DIR / "dbt" / "models" / "notes.md"
    mine.write_text("hand-written")
    write(generate(taxi), taxi.root)
    assert mine.is_file()


def test_cli_generate_and_check(taxi):
    path = str(taxi.root)
    assert runner.invoke(app, ["generate", "-p", path, "--check"]).exit_code == 1
    result = runner.invoke(app, ["generate", "-p", path])
    assert result.exit_code == 0, result.output
    assert runner.invoke(app, ["generate", "-p", path, "--check"]).exit_code == 0
    result = runner.invoke(app, ["generate", "-p", path, "--warehouse", "snowflake"])
    assert (taxi.root / GENERATED_DIR / "ddl" / "snowflake" / "raw.sql").is_file()
    assert not (taxi.root / GENERATED_DIR / "ddl" / "duckdb").exists()


def test_cli_generate_refuses_an_invalid_model(taxi):
    taxi.model.entity("fct_trip").mappings[0] = Mapping(target="trip_id", sources=["ghost.id"])
    from murakumo.project import save_model

    save_model(taxi)
    result = runner.invoke(app, ["generate", "-p", str(taxi.root)])
    assert result.exit_code == 1 and "MK207" in result.output


def test_committed_example_is_up_to_date():
    """examples/commerce/generated is the golden output: regenerate it when templates change."""
    result = runner.invoke(app, ["generate", "-p", str(COMMERCE), "--check"])
    assert result.exit_code == 0, result.output
