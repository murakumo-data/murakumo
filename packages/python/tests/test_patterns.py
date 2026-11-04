"""Modelling patterns: unknown members, accumulating snapshots, conformed dimensions and the bus
matrix, access levels, exposures and masking policies."""

from pathlib import Path

import pytest
import yaml

from murakumo import bus, masking
from murakumo.generate import generate
from murakumo.ir.model import Exposure
from murakumo.project import load_project
from murakumo.validate import validate_project

COMMERCE = Path(__file__).resolve().parents[3] / "examples" / "commerce"


@pytest.fixture
def commerce():
    return load_project(COMMERCE).project


def codes(project):
    return {d.code for d in validate_project(project)}


# unknown members --------------------------------------------------------------------------------


def test_dimensions_get_an_unknown_member_and_facts_point_at_it(commerce):
    files = generate(commerce).files
    dimension = files["dbt/models/marts/product/dim_part.sql"]
    assert "union all" in dimension and "cast(-1 as BIGINT)" in dimension and "'Unknown'" in dimension
    fact = files["dbt/models/marts/sales/fct_order_line.sql"]
    assert "coalesce(cast(lineitem.l_partkey as BIGINT), cast(-1 as BIGINT)) as part_id" in fact
    # the SCD2 dimension's unknown member is always current, from the beginning of time
    scd2 = files["dbt/models/marts/sales/dim_customer.sql"]
    assert "cast('1900-01-01' as TIMESTAMP),\n    cast(null as TIMESTAMP),\n    true," in scd2


def test_unknown_members_follow_the_setting_and_each_dimension_can_say_otherwise(commerce):
    commerce.manifest.modelling.unknown_members = False
    files = generate(commerce).files
    assert "union all" not in files["dbt/models/marts/product/dim_part.sql"]
    assert "coalesce(cast(lineitem.l_partkey" not in files["dbt/models/marts/sales/fct_order_line.sql"]
    commerce.model.entity("dim_part").unknown_member = True
    files = generate(commerce).files
    assert "union all" in files["dbt/models/marts/product/dim_part.sql"]
    assert "coalesce(cast(orders.o_custkey" not in files["dbt/models/marts/sales/fct_order_line.sql"]


# accumulating snapshots -------------------------------------------------------------------------


def test_accumulating_snapshots_keep_milestones_and_time_between_them(commerce):
    files = generate(commerce).files
    sql = files["dbt/models/marts/sales/fct_order_fulfilment.sql"]
    assert "coalesce(incoming.shipped_on, reached.shipped_on) as shipped_on" in sql
    assert "dbt.datediff('merged.ordered_on', 'merged.shipped_on', 'day')" in sql
    assert "as current_milestone" in sql
    models = yaml.safe_load(files["dbt/models/marts/sales/_sales__models.yml"])["models"]
    model = next(m for m in models if m["name"] == "fct_order_fulfilment")
    names = [c["name"] for c in model["columns"]]
    assert names[-3:] == ["ordered_on_to_shipped_on_days", "shipped_on_to_received_on_days", "current_milestone"]
    # without dbt: the same model as plain SQL
    from murakumo.generate import export_sql

    output = generate(commerce)
    plain = export_sql(output.plan, output.files)
    script = next(v for k, v in plain.items() if "fct_order_fulfilment" in k and not k.startswith("tests/"))
    assert "date_diff('DAY', merged.ordered_on, merged.shipped_on)" in script.replace("DATE_DIFF", "date_diff")


def test_accumulating_snapshots_need_milestones_that_are_dates(commerce):
    entity = commerce.model.entity("fct_order_fulfilment")
    entity.accumulating.milestones = ["ordered_on", "customer_id"]
    assert "MK315" in codes(commerce)
    entity.accumulating = None
    assert "MK314" in codes(commerce)


# conformed dimensions and the bus matrix --------------------------------------------------------


def test_bus_matrix_marks_which_facts_use_which_dimensions(commerce):
    matrix = bus.bus_matrix(commerce.model)
    assert matrix.uses["fct_order_line"] == ["dim_part", "dim_customer"] or set(matrix.uses["fct_order_line"]) == {
        "dim_customer",
        "dim_part",
    }
    assert matrix.uses["fct_order_fulfilment"] == ["dim_customer"]
    table = bus.markdown(matrix)
    assert "dim_customer (conformed)" in table and "| fct_order_line | sales |" in table


def test_another_domains_dimension_should_be_conformed(commerce):
    assert not {"MK316", "MK317"} & codes(commerce)
    commerce.model.entity("dim_part").conformed = False  # dim_part is product's; fct_order_line is sales'
    assert "MK316" in codes(commerce)


def test_a_copy_of_a_conformed_dimension_is_reported(commerce):
    original = commerce.model.entity("dim_part")
    copy = original.model_copy(deep=True, update={"name": "dim_part_copy", "conformed": False})
    copy.id = __import__("uuid").uuid4()
    commerce.model.entities.append(copy)
    problems = bus.conformed_problems(commerce.model)
    assert any(code == "MK317" and name == "dim_part_copy" for code, name, _ in problems)


# access and exposures ---------------------------------------------------------------------------


def test_access_levels_reach_dbt(commerce):
    commerce.model.entity("dim_part").access = "public"
    models = yaml.safe_load(generate(commerce).files["dbt/models/marts/product/_product__models.yml"])["models"]
    assert next(m for m in models if m["name"] == "dim_part")["config"]["access"] == "public"
    commerce.model.entity("dim_part").domain = None
    commerce.model.entity("dim_part").access = "private"
    assert "MK706" in codes(commerce)


def test_exposures_are_written_for_dbt_and_checked(commerce):
    exposures = yaml.safe_load(generate(commerce).files["dbt/models/_exposures.yml"])["exposures"]
    assert exposures[0]["name"] == "sales_dashboard"
    assert exposures[0]["owner"] == {"name": "Sales analytics", "email": "sales-analytics@example.com"}
    assert "ref('fct_order_fulfilment')" in exposures[0]["depends_on"]
    commerce.model.exposures.append(Exposure(name="broken", depends_on=["no_such_table"]))
    assert "MK209" in codes(commerce)


# masking policies -------------------------------------------------------------------------------


def test_masking_policies_on_snowflake(commerce):
    output = generate(commerce, "snowflake")
    sql = output.files["ddl/snowflake/masking.sql"]
    assert "create masking policy if not exists governance.murakumo_mask_redact" in sql
    assert "is_role_in_session('ANALYST_PII')" in sql
    assert "alter table if exists marts.dim_customer modify column phone set masking policy" in sql
    # set after each build, by a post-hook in the model's own config (dbt renders YAML hooks too early)
    sql = output.files["dbt/models/marts/sales/dim_customer.sql"]
    assert (
        "post_hook=[\"{{ murakumo_masking({'customer_name': 'redact', 'phone': 'redact'}, ['ANALYST_PII']) }}\"]" in sql
    )
    assert "post_hook" not in output.files["dbt/models/intermediate/sales/int_dim_customer__versions.sql"]  # a view
    assert "dbt/macros/murakumo/masking.sql" in output.files


def test_masking_is_snowflake_only_and_checked(commerce):
    output = generate(commerce, "duckdb")
    assert "ddl/duckdb/masking.sql" not in output.files
    assert "post_hook" not in output.files["dbt/models/marts/sales/dim_customer.sql"]
    commerce.manifest.governance.masking = {"pii": "scramble"}
    assert "MK704" in codes(commerce)
    commerce.manifest.governance.masking = {"restricted": "redact"}
    commerce.model.entity("dim_customer").attribute("account_balance").classification = "restricted"
    assert "MK705" in codes(commerce)  # a number: masking policies here mask text
    assert {m.column for m in masking.masked_columns(commerce)} == {"customer_name", "phone"}
