"""Quality rules: the rule library, each rule's action, quarantine and reconciliation."""

import duckdb
import pytest

from murakumo import edit
from murakumo.generate import generate
from murakumo.generate.plain import Plain
from murakumo.generate.plan import build_plan
from murakumo.generate.targets import target_for
from murakumo.ir.model import QualityRule, RuleAction, RuleType, Severity
from murakumo.scaffold import init_project
from murakumo.validate import validate_project


def zones(tmp_path, reconciliation=False):
    project = init_project(tmp_path / "shop")
    project.manifest.quality.reconciliation = reconciliation
    entity = project.model.entity("taxi_zone_lookup")
    borough = entity.attribute("Borough")
    borough.tests = [QualityRule(type="accepted_values", values=["Queens", "Bronx"], action="quarantine")]
    entity.attribute("service_zone").tests = [QualityRule(type="length", min=2, max=20, action="quarantine")]
    entity.tests = [QualityRule(type="custom_sql", sql="location_id > 0", action="quarantine", description="positive")]
    return project, entity


def codes(project):
    return {d.code for d in validate_project(project)}


def plain(project, warehouse="duckdb"):
    plan = build_plan(project, target_for(warehouse))
    files = {p.removeprefix("dbt/"): c for p, c in generate(project, warehouse).files.items() if p.startswith("dbt/")}
    return plan, Plain(plan, files)


def test_the_action_sets_the_severity():
    assert QualityRule(type="not_null", action="warn").severity is Severity.WARN
    assert QualityRule(type="not_null", action="fail").severity is Severity.ERROR
    assert QualityRule(type="not_null", severity="warn").effective_action is RuleAction.WARN
    assert QualityRule(type="not_null").effective_action is RuleAction.FAIL
    assert QualityRule(type="regex", pattern="^a").row_level and not QualityRule(type="row_count", min=1).row_level


@pytest.mark.parametrize(
    ("rule", "problem"),
    [
        ({"type": "regex"}, "pattern"),
        ({"type": "length"}, "min, max"),
        ({"type": "row_count"}, "min, max"),
        ({"type": "null_share", "max": 2}, "share from 0 to 1"),
        ({"type": "recent"}, "max_age_hours"),
    ],
)
def test_a_rule_without_its_settings_is_refused(rule, problem):
    with pytest.raises(ValueError, match=problem):
        QualityRule(**rule)


def test_failing_rows_are_set_aside_and_the_rest_carry_on(tmp_path):
    project, _ = zones(tmp_path, reconciliation=True)
    plan, flat = plain(project)
    assert plan.model("stg_nyc_tlc__taxi_zone_lookup__quarantine").materialized == "table"
    staging = flat.select(plan.model("stg_nyc_tlc__taxi_zone_lookup"))
    aside = flat.select(plan.model("stg_nyc_tlc__taxi_zone_lookup__quarantine"))
    db = duckdb.connect()
    db.execute("create schema raw_nyc_tlc; create schema staging")
    db.execute(
        'create table raw_nyc_tlc.taxi_zone_lookup ("LocationID" integer, "Borough" varchar, "Zone" varchar, '
        "service_zone varchar, _ingested_at timestamp, _batch_id varchar)"
    )
    db.execute(
        "insert into raw_nyc_tlc.taxi_zone_lookup values "
        "(1, 'Queens', 'a', 'Boro Zone', now(), 'b1'), (2, 'Atlantis', 'b', 'Boro Zone', now(), 'b1'), "
        "(3, 'Bronx', 'c', 'X', now(), 'b1'), (-4, 'Bronx', 'd', 'Boro Zone', now(), 'b1'), "
        "(5, null, 'e', 'Boro Zone', now(), 'b1')"
    )
    kept = db.execute(f"select location_id from ({staging}) order by 1").fetchall()
    assert kept == [(1,), (5,)]  # a null is not a value outside the list: not_null says whether it may be null
    failed = db.execute(f"select location_id, _failed_rule from ({aside}) order by 1").fetchall()
    assert failed == [(-4, "positive"), (2, "borough: accepted_values"), (3, "service_zone: length")]
    # The quarantined rules are not also tests on staging: their rows never reach it.
    tests = [t.name for c in plan.model("stg_nyc_tlc__taxi_zone_lookup").columns for t in c.tests]
    assert "accepted_values" not in tests
    # Reconciliation runs on the quarantine table, which is built after staging.
    reconcile = plan.model("stg_nyc_tlc__taxi_zone_lookup__quarantine").tests
    assert [t.name for t in reconcile] == ["murakumo_reconciles"] and "other" in reconcile[0].args
    db.execute(f"create view staging.stg_nyc_tlc__taxi_zone_lookup as {staging}")
    db.execute(f"create table staging.stg_nyc_tlc__taxi_zone_lookup__quarantine as {aside}")
    query = flat.test("stg_nyc_tlc__taxi_zone_lookup__quarantine", reconcile[0]).split("\n", 1)[1]
    assert db.execute(query).fetchall() == []
    db.execute("insert into raw_nyc_tlc.taxi_zone_lookup values (9, 'Queens', 'z', 'Boro Zone', now(), 'b2')")
    db.execute("delete from staging.stg_nyc_tlc__taxi_zone_lookup__quarantine where location_id = 2")
    assert db.execute(query).fetchall()  # one row lost: the counts no longer add up


def test_a_side_table_is_never_what_a_reference_to_its_entity_means(tmp_path):
    project, _ = zones(tmp_path)
    plan, _ = plain(project)
    to = {t.args.get("to") for m in plan.models for c in m.columns for t in c.tests if t.name == "relationships"}
    assert "ref('stg_nyc_tlc__taxi_zone_lookup')" in to
    assert not any("quarantine" in str(t) for t in to)


@pytest.mark.parametrize("warehouse", ["duckdb", "snowflake", "postgres", "bigquery", "databricks", "fabric"])
def test_row_rules_are_written_for_each_warehouse(tmp_path, warehouse):
    project, entity = zones(tmp_path)
    entity.attribute("Zone").tests = [QualityRule(type="regex", pattern="^[A-Z]", action="quarantine")]
    files = generate(project, warehouse).files
    sql = files["dbt/models/staging/nyc_tlc/stg_nyc_tlc__taxi_zone_lookup__quarantine.sql"]
    expected = {
        "duckdb": "REGEXP_MATCHES(",
        "snowflake": "REGEXP_LIKE(",
        "postgres": " ~ ",
        "bigquery": "REGEXP_CONTAINS(",
        "databricks": "REGEXP_LIKE(",
        "fabric": "REGEXP_LIKE(",
    }[warehouse]
    assert expected in sql and "'zone: regex'" in sql


def test_table_rules_become_tests_that_run(tmp_path):
    project = init_project(tmp_path / "shop")
    entity = project.model.entity("taxi_zone_lookup")
    entity.tests = [QualityRule(type="row_count", min=3)]
    entity.attribute("Zone").tests = [
        QualityRule(type="null_share", max=0.25),
        QualityRule(type="distinct_count", min=2, max=10),
        QualityRule(type="regex", pattern="^[A-Z]", action="warn"),
    ]
    plan, flat = plain(project)
    model = plan.model("stg_nyc_tlc__taxi_zone_lookup")
    tests = [*(t for c in model.columns for t in c.tests), *model.tests]
    names = {t.name for t in tests}
    assert {"murakumo_row_count", "murakumo_null_share", "murakumo_distinct_count", "murakumo_expression"} <= names
    db = duckdb.connect()
    db.execute("create schema staging")
    db.execute(
        "create table staging.stg_nyc_tlc__taxi_zone_lookup as select * from (values "
        "(1, 'Alpha'), (2, null), (3, 'beta')) as t(location_id, zone)"
    )
    failing = {
        t.name: bool(db.execute(flat.test(model.name, t).split("\n", 1)[1]).fetchall())
        for t in tests
        if t.name.startswith("murakumo_") and t.name != "murakumo_reconciles"
    }
    # 3 rows (ok), a third null (> 25%), 2 distinct (ok), "beta" does not start with a capital.
    assert failing == {
        "murakumo_row_count": False,
        "murakumo_null_share": True,
        "murakumo_distinct_count": False,
        "murakumo_expression": True,
    }
    assert next(t for t in tests if t.name == "murakumo_expression").severity == "warn"


def test_misused_rules_are_reported(tmp_path):
    project = init_project(tmp_path / "shop")
    raw = project.model.entity("taxi_zone_lookup")
    raw.tests = [QualityRule(type="row_count", min=1, action="quarantine")]
    raw.attribute("Zone").tests = [QualityRule(type="regex", pattern="([", action="warn")]
    mart = project.model.entity("dim_taxi_zone")
    mart.attributes[1].tests = [QualityRule(type="not_null", action="quarantine")]
    found = codes(project)
    assert {"MK326", "MK327", "MK329"} <= found
    raw.attribute("Zone").tests = [QualityRule(type="regex", pattern="^[A-Z]")]
    assert "MK330" in codes(project)  # no example for made-up data to use
    raw.attribute("Zone").example = "Alphabet City"
    assert "MK330" not in codes(project)


def test_made_up_data_keeps_to_the_rules(tmp_path):
    from murakumo.generate.sample import sample_rows

    project, entity = zones(tmp_path)
    entity.attribute("Zone").tests = [QualityRule(type="regex", pattern="^[A-Z]")]
    entity.attribute("Zone").example = "Midtown"
    rows = sample_rows(project.model, [entity], rows=50)[entity.name]
    assert {r["Borough"] for r in rows} <= {"Queens", "Bronx"}
    assert {r["Zone"] for r in rows} == {"Midtown"}
    assert all(r["service_zone"] is None or 2 <= len(r["service_zone"]) <= 20 for r in rows)


def test_rules_can_be_edited_like_any_other_field(tmp_path):
    project = init_project(tmp_path / "shop")
    edit.update_column(
        project,
        "taxi_zone_lookup",
        "Zone",
        {"tests": [{"type": "regex", "pattern": "^[A-Z]", "action": "quarantine"}]},
    )
    rule = project.model.entity("taxi_zone_lookup").attribute("Zone").tests[0]
    assert rule.type is RuleType.REGEX and rule.action is RuleAction.QUARANTINE


def test_write_audit_publish_keeps_the_last_good_tables(tmp_path):
    duckdb_module = pytest.importorskip("duckdb")
    from murakumo.generate.verify import build

    project = init_project(tmp_path / "taxi")
    project.manifest.quality.write_audit_publish = True
    first = build(project, sample=30)
    assert first.ok and first.published
    database = project.root / "taxi.duckdb"
    with duckdb_module.connect(str(database)) as db:
        schemas = {r[0] for r in db.execute("select schema_name from information_schema.schemata").fetchall()}
        assert {"wap_staging", "wap_marts", "staging", "marts"} <= schemas
        published = db.execute("select count(*) from marts.dim_taxi_zone").fetchone()[0]
        # A duplicate key arrives: the audit build's tests fail.
        db.execute("insert into raw_nyc_tlc.taxi_zone_lookup select * from raw_nyc_tlc.taxi_zone_lookup limit 1")
    second = build(project)
    assert not second.ok and not second.published and "nothing was published" in second.error
    with duckdb_module.connect(str(database), read_only=True) as db:
        assert db.execute("select count(*) from marts.dim_taxi_zone").fetchone()[0] == published
