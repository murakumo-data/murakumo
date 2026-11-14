from murakumo import edit, row_access
from murakumo.generate import generate
from murakumo.ir.model import RowAccess
from murakumo.manifest import Warehouse
from murakumo.scaffold import init_project
from murakumo.validate import validate_project


def project_with_policy(tmp_path, warehouse=Warehouse.SNOWFLAKE):
    project = init_project(tmp_path / "taxi")
    project.manifest.targets.warehouse.type = warehouse
    project.manifest.governance.unmasked_roles = ["analyst_admin"]
    zone = project.model.entity("dim_taxi_zone")
    zone.row_access = RowAccess(column="borough", allow={"ops_queens": ["Queens"], "ops_city": ["Manhattan", "O'Hare"]})
    return project, zone


def codes(project):
    return {d.code for d in validate_project(project)}


def test_the_policy_says_who_sees_which_rows(tmp_path):
    project, zone = project_with_policy(tmp_path)
    assert row_access.body(zone, ["analyst_admin"]) == (
        "is_role_in_session('ANALYST_ADMIN') or "
        "(is_role_in_session('OPS_CITY') and val in ('Manhattan', 'O''Hare')) or "
        "(is_role_in_session('OPS_QUEENS') and val in ('Queens'))"
    )
    sql = row_access.policy_sql(project, "TAXI_PROD")
    assert "create row access policy if not exists TAXI_PROD.governance.murakumo_rows_dim_taxi_zone" in sql
    assert "alter table if exists marts.dim_taxi_zone drop all row access policies;" in sql
    assert "add row access policy TAXI_PROD.governance.murakumo_rows_dim_taxi_zone on (borough);" in sql
    zone.row_access = RowAccess(column="borough")
    project.manifest.governance.unmasked_roles = []
    assert row_access.body(zone, []) == "false"  # nobody named: nobody sees a row


def test_generated_for_snowflake_with_a_hook_a_file_and_a_guide_step(tmp_path):
    project, _ = project_with_policy(tmp_path)
    files = generate(project).files
    model = files["dbt/models/marts/mobility/dim_taxi_zone.sql"]
    assert "murakumo_row_access('murakumo_rows_dim_taxi_zone', 'borough'" in model
    assert "murakumo_row_access" in files["dbt/macros/murakumo/row_access.sql"]
    assert "row access policy" in files["ddl/snowflake/row_access.sql"]
    guide = "".join(c for p, c in files.items() if p.startswith("guide/") and p.endswith(".md"))
    assert "Say which rows each role sees" in guide and "guide/sql/row_access.sql" in files
    assert "murakumo_row_access" not in files["dbt/models/marts/mobility/fct_trip.sql"]


def test_the_hook_is_valid_jinja_with_quotes_in_values(tmp_path):
    from jinja2 import Environment

    project, _ = project_with_policy(tmp_path)
    model = generate(project).files["dbt/models/marts/mobility/dim_taxi_zone.sql"]
    seen = []
    env = Environment()
    env.globals.update(
        config=lambda **kwargs: seen.append(kwargs) or "", ref=lambda n: n, source=lambda a, b: b, this="t"
    )
    env.globals.update(murakumo_row_access=lambda *a: a, murakumo_masking=lambda *a: a, env_var=lambda *a: "")
    head = model[model.index("{{ config(") : model.index("]) }}") + 5]
    env.from_string(head).render()
    hook = next(h for h in seen[0]["post_hook"] if "murakumo_row_access" in h)
    rendered = env.from_string(hook).render()
    assert "O''Hare" in rendered  # the SQL's doubled quote survives the hook's own quoting


def test_rules(tmp_path):
    project, zone = project_with_policy(tmp_path)
    assert not codes(project) & {"MK707", "MK708", "MK709"}
    zone.row_access = RowAccess(column="nope")
    assert "MK707" in codes(project)
    zone.row_access = RowAccess(column="zone_id", allow={"a": ["1"]})  # not text
    assert "MK707" in codes(project)
    zone.row_access = RowAccess(column="borough")
    project.manifest.governance.unmasked_roles = []
    assert "MK708" in codes(project)
    duck, _ = project_with_policy(tmp_path / "b", Warehouse.DUCKDB)
    assert "MK709" in codes(duck)
    assert "ddl/duckdb/row_access.sql" not in generate(duck).files


def test_set_from_the_edits_the_canvas_uses(tmp_path):
    project = init_project(tmp_path / "taxi")
    edit.update_entity(project, "dim_taxi_zone", {"row_access": {"column": "borough", "allow": {"ops": ["Queens"]}}})
    assert project.model.entity("dim_taxi_zone").row_access.allow == {"ops": ["Queens"]}
    edit.update_entity(project, "dim_taxi_zone", {"row_access": None})
    assert project.model.entity("dim_taxi_zone").row_access is None
