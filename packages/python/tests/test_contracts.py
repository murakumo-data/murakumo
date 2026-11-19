"""Data contracts (ODCS), the rules for Great Expectations and Soda, the query console,
write-audit-publish and quarantine in monitoring."""

import json
from pathlib import Path

import pytest
import yaml

from murakumo import contracts, query
from murakumo.generate import checks as quality_tools
from murakumo.generate import generate
from murakumo.generate.plan import build_plan
from murakumo.generate.targets import target_for
from murakumo.ir.model import ContractTerms, QualityRule
from murakumo.scaffold import init_project

SCHEMA = Path(__file__).parent / "fixtures" / "odcs-json-schema-v3.0.2.json"


def zones(tmp_path, name="shop"):
    project = init_project(tmp_path / name)
    entity = project.model.entity("taxi_zone_lookup")
    entity.attribute("Borough").tests = [
        QualityRule(type="accepted_values", values=["Queens", "Bronx"], action="quarantine")
    ]
    entity.attribute("Zone").tests = [QualityRule(type="regex", pattern="^[A-Z]", action="warn")]
    entity.attribute("service_zone").tests = [QualityRule(type="null_share", max=0.5)]
    entity.tests = [
        QualityRule(type="row_count", min=1),
        QualityRule(type="custom_sql", sql="location_id > 0", action="quarantine", description="positive"),
    ]
    return project


# Contracts ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("warehouse", ["duckdb", "snowflake", "postgres", "bigquery"])
def test_every_contract_is_valid_odcs(tmp_path, warehouse):
    jsonschema = pytest.importorskip("jsonschema")
    project = zones(tmp_path)
    files = generate(project, warehouse).files
    written = {p: yaml.safe_load(t) for p, t in files.items() if p.startswith("contracts/")}
    assert set(written) == {
        "contracts/taxi_zone_lookup.odcs.yaml",
        "contracts/yellow_tripdata.odcs.yaml",
        "contracts/dim_taxi_zone.odcs.yaml",
        "contracts/fct_trip.odcs.yaml",
    }
    validator = jsonschema.Draft201909Validator(json.loads(SCHEMA.read_text()))
    for path, document in written.items():
        errors = [f"{list(e.path)}: {e.message}" for e in validator.iter_errors(document)]
        assert not errors, (path, errors[:3])


def test_a_raw_tables_contract_reads_the_raw_table(tmp_path):
    project = zones(tmp_path)
    document = yaml.safe_load(generate(project).files["contracts/taxi_zone_lookup.odcs.yaml"])
    obj = document["schema"][0]
    assert obj["physicalName"] == "raw_nyc_tlc.taxi_zone_lookup"
    location = next(p for p in obj["properties"] if p["name"] == "LocationID")
    assert location["primaryKey"] and location["required"] and location["logicalType"] == "integer"
    zone = next(p for p in obj["properties"] if p["name"] == "Zone")
    assert zone["logicalTypeOptions"] == {"pattern": "^[A-Z]"}
    # A rule written with staging's names (location_id) reads the raw column (LocationID).
    positive = next(q for q in obj["quality"] if q.get("description") == "positive")
    assert '"LocationID" > 0' in positive["query"] and positive["mustBe"] == 0
    # The rule itself is kept as it is, so a contract read back gives it exactly.
    assert positive["customProperties"][0]["value"]["sql"] == "location_id > 0"


def test_a_contract_read_back_gives_the_same_rules(tmp_path):
    source = zones(tmp_path, "sender")
    text = generate(source).files["contracts/taxi_zone_lookup.odcs.yaml"]
    path = tmp_path / "zones.odcs.yaml"
    path.write_text(text)
    receiver = init_project(tmp_path / "receiver", example=False)
    results = contracts.apply(receiver, contracts.read(path), source="partner")
    assert [r.entity for r in results] == ["taxi_zone_lookup"] and results[0].new
    entity = receiver.model.entity("taxi_zone_lookup")
    assert entity.source == "partner" and entity.contract == ContractTerms()
    assert [a.name for a in entity.attributes] == ["LocationID", "Borough", "Zone", "service_zone"]
    assert entity.attribute("LocationID").key is not None and not entity.attribute("LocationID").nullable
    sent = {(a.name, r.type, r.action) for a in source.model.entity("taxi_zone_lookup").attributes for r in a.tests}
    got = {(a.name, r.type, r.action) for a in entity.attributes for r in a.tests}
    assert sent <= got
    assert {r.type.value for r in entity.tests} == {"row_count", "custom_sql"}
    # Reading it again adds nothing twice.
    again = contracts.apply(receiver, contracts.read(path), source="partner")
    assert again[0].rules == 0 and not again[0].added


def test_a_contract_from_another_tool_is_read_from_its_odcs_fields(tmp_path):
    path = tmp_path / "orders.odcs.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "apiVersion": "v3.0.2",
                "kind": "DataContract",
                "id": "orders-1",
                "version": "2.1.0",
                "status": "active",
                "schema": [
                    {
                        "name": "orders",
                        "properties": [
                            {"name": "order_id", "logicalType": "string", "primaryKey": True, "required": True},
                            {
                                "name": "status",
                                "logicalType": "string",
                                "quality": [{"type": "library", "rule": "validValues", "validValues": ["new", "paid"]}],
                            },
                            {"name": "amount", "physicalType": "decimal(12,2)", "logicalTypeOptions": {"minimum": 0}},
                            {"name": "code", "logicalType": "string", "logicalTypeOptions": {"pattern": "^[A-Z]{3}$"}},
                        ],
                        "quality": [{"type": "library", "rule": "rowCount", "mustBeGreaterThan": 0}],
                    }
                ],
            }
        )
    )
    project = init_project(tmp_path / "shop", example=False)
    contracts.apply(project, contracts.read(path), source="shop_db")
    orders = project.model.entity("orders")
    assert orders.contract.version == "2.1.0"
    assert orders.attribute("amount").type == "decimal(12,2)"
    assert [(r.type.value, r.values) for r in orders.attribute("status").tests] == [
        ("accepted_values", ["new", "paid"])
    ]
    assert [(r.type.value, r.min) for r in orders.attribute("amount").tests] == [("range", 0)]
    assert [r.pattern for r in orders.attribute("code").tests] == ["^[A-Z]{3}$"]
    assert [(r.type.value, r.min) for r in orders.tests] == [("row_count", 0)]


def test_what_is_not_a_contract_is_refused(tmp_path):
    path = tmp_path / "x.yaml"
    path.write_text("kind: Something\n")
    with pytest.raises(ValueError, match="not an ODCS v3 data contract"):
        contracts.read(path)


def test_the_latest_load_is_checked_against_its_contract(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    project = zones(tmp_path)
    plan = build_plan(project, target_for("duckdb"))
    del plan
    path = project.root / "shop.duckdb"
    with duckdb.connect(str(path)) as db:
        db.execute("create schema raw_nyc_tlc")
        db.execute(
            'create table raw_nyc_tlc.taxi_zone_lookup ("LocationID" integer, "Borough" varchar, surprise varchar, '
            "_ingested_at timestamp, _batch_id varchar)"
        )
        db.execute("insert into raw_nyc_tlc.taxi_zone_lookup values (1, 'Queens', 'x', now(), 'b1')")
    found = contracts.check(project)
    assert found["checked"] and found["tables"] == ["taxi_zone_lookup"]
    messages = {(b["level"], b["what"], b["message"].split(" ")[0]) for b in found["breaches"]}
    assert ("error", "schema", "Zone") in messages  # promised, missing: staging cannot build without it
    assert any(b["what"] == "schema" and "surprise" in b["message"] and b["level"] == "warn" for b in found["breaches"])
    # A contract that only warns reports the same as warnings.
    project.model.entity("taxi_zone_lookup").contract = ContractTerms(enforce="warn")
    assert all(b["level"] == "warn" for b in contracts.check(project)["breaches"])


# Great Expectations and Soda -----------------------------------------------------------------------


def test_the_rules_are_written_for_great_expectations_and_soda(tmp_path):
    project = zones(tmp_path)
    project.manifest.quality.framework = ["dbt_tests", "great_expectations", "soda"]
    files = generate(project).files
    suite = json.loads(files["quality/great_expectations/expectations/stg_nyc_tlc__taxi_zone_lookup.json"])
    kinds = {e["type"] for e in suite["expectations"]}
    assert {
        "expect_column_values_to_be_unique",
        "expect_column_values_to_not_be_null",
        "expect_column_values_to_match_regex",
        "expect_table_row_count_to_be_between",
    } <= kinds
    # Quarantine rules are left out: their rows never reach the table.
    assert "expect_column_values_to_be_in_set" not in kinds
    assert not any("location_id > 0" in json.dumps(e) for e in suite["expectations"])
    regex = next(e for e in suite["expectations"] if e["type"] == "expect_column_values_to_match_regex")
    assert regex["meta"]["severity"] == "warn"
    checks = yaml.safe_load(files["quality/soda/checks/stg_nyc_tlc__taxi_zone_lookup.yml"])
    listed = checks["checks for staging.stg_nyc_tlc__taxi_zone_lookup"]
    assert "duplicate_count(location_id) = 0" in listed
    assert {"missing_percent(service_zone)": {"fail": "when > 50"}} in listed
    assert {"invalid_count(zone)": {"valid regex": "^[A-Z]", "warn": "when > 0"}} in listed
    assert "data_source warehouse" in yaml.safe_load(files["quality/soda/configuration.yml"])
    for tool in ("great_expectations", "soda"):
        compile(files[f"quality/{tool}/run.py"], f"{tool}/run.py", "exec")
    # Accepted values are typed as their column is.
    trips = json.loads(files["quality/great_expectations/expectations/stg_nyc_tlc__yellow_tripdata.json"])
    in_set = [
        e["kwargs"]["value_set"] for e in trips["expectations"] if e["type"] == "expect_column_values_to_be_in_set"
    ]
    assert in_set and all(isinstance(v, int) for v in in_set[0])


def test_nothing_is_written_for_tools_not_chosen(tmp_path):
    files = generate(zones(tmp_path)).files
    assert not [p for p in files if p.startswith("quality/")]
    assert quality_tools.tables(build_plan(zones(tmp_path / "x"), target_for("duckdb")))


def test_a_framework_murakumo_does_not_write_is_a_warning(tmp_path):
    from murakumo.validate import validate_project

    project = init_project(tmp_path / "shop")
    project.manifest.quality.framework = ["dbt_tests", "elementary"]
    found = [d for d in validate_project(project) if d.code == "MK331"]
    assert found and "elementary" in found[0].message and found[0].level.value == "warning"


# Query console --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "drop table marts.x",
        "insert into x values (1)",
        "update x set a = 1",
        "delete from x",
        "with y as (select 1) delete from x",
        "create table x as select 1",
        "copy x to 'x.csv'",
        "attach 'other.db'",
        "set threads = 1",
        "select 1; select 2",
        "select * into newt from x",
        "install httpfs",
        "pragma enable_profiling",
        "",
    ],
)
def test_the_console_only_reads(sql):
    with pytest.raises(query.QueryError):
        query.read_only(sql, "duckdb")


@pytest.mark.parametrize(
    "sql",
    [
        "select 1",
        "with a as (select 1 as x) select * from a",
        "select 1 union all select 2",
        "describe x",
        "show tables",
    ],
)
def test_the_console_runs_reads(sql):
    query.read_only(sql, "duckdb")


def test_results_are_cut_at_the_limit():
    text, tree = query.read_only("select 1 as x union all select 2", "duckdb")
    assert query.limited(tree, text, "duckdb", 10).endswith("LIMIT 11")
    text, tree = query.read_only("select 1 as x", "tsql")
    assert "TOP 11" in query.limited(tree, text, "tsql", 10)


def test_every_stage_can_be_previewed(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    project = zones(tmp_path)
    with duckdb.connect(str(project.root / "shop.duckdb")) as db:
        db.execute("create schema raw_nyc_tlc; create schema staging")
        db.execute('create table raw_nyc_tlc.taxi_zone_lookup as select 1 as "LocationID", \'Queens\' as "Borough"')
        db.execute("create table staging.stg_nyc_tlc__taxi_zone_lookup__quarantine as select 2 as location_id")
    targets = query.targets(project)
    stages = {t.stage for t in targets}
    assert {"raw", "staging", "quarantine", "marts", "audit"} <= stages
    raw = next(t for t in targets if t.stage == "raw" and t.entity == "taxi_zone_lookup")
    result = query.run(project, raw.sql)
    assert result.columns == ["LocationID", "Borough"] and result.rows == [[1, "Queens"]] and not result.truncated
    aside = next(t for t in targets if t.stage == "quarantine")
    assert query.run(project, aside.sql).rows == [[2]]
    many = query.run(project, "select * from range(50)", limit=10)
    assert len(many.rows) == 10 and many.truncated


def test_files_are_read_where_they_are(tmp_path):
    pytest.importorskip("duckdb")
    project = init_project(tmp_path / "shop")
    (project.root / "zones.csv").write_text("id,name\n1,a\n2,b\n")
    result = query.run(project, "select count(*) as n from read_csv('zones.csv')", engine="files")
    assert result.rows == [[2]]
    with pytest.raises(query.QueryError, match="only reads"):
        query.run(project, "copy (select 1) to 'out.csv'", engine="files")
    assert not (project.root / "out.csv").exists()
