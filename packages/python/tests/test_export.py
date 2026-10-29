import shutil
from pathlib import Path

import pytest
import sqlglot
from typer.testing import CliRunner

from murakumo.cli import app
from murakumo.generate import export_sql, generate
from murakumo.manifest import Warehouse
from murakumo.project import load_project
from murakumo.scaffold import init_project

runner = CliRunner()
COMMERCE = Path(__file__).resolve().parents[3] / "examples" / "commerce"


@pytest.fixture
def commerce(tmp_path):
    shutil.copytree(COMMERCE / "model", tmp_path / "commerce" / "model")
    shutil.copy(COMMERCE / "murakumo.yaml", tmp_path / "commerce")
    return load_project(tmp_path / "commerce").project


def exported(project, warehouse=None):
    output = generate(project, warehouse)
    return export_sql(output.plan, output.files)


def test_scripts_run_in_dependency_order(commerce):
    names = [p for p in exported(commerce) if p.endswith(".sql") and "/" not in p]
    assert names[0] == "00_landing.sql"
    position = {n.split("__", 1)[1].removesuffix(".sql"): i for i, n in enumerate(names) if "__" in n}
    assert position["stg_tpch_erp__customer"] < position["int_dim_customer__versions"]
    assert position["int_dim_customer__versions"] < position["snp_dim_customer"] < position["dim_customer"]
    assert position["dim_customer"] < position["fct_order_line"]


def test_no_dbt_left(commerce):
    for path, content in exported(commerce).items():
        assert "{{" not in content and "{%" not in content, path


@pytest.mark.parametrize(
    "warehouse", [Warehouse.SNOWFLAKE, Warehouse.POSTGRES, Warehouse.BIGQUERY, Warehouse.DATABRICKS]
)
def test_every_statement_parses_in_the_warehouse_dialect(commerce, warehouse):
    dialect = {"postgres": "postgres"}.get(warehouse.value, warehouse.value)
    for path, content in exported(commerce, warehouse).items():
        if path.endswith(".sql"):
            sqlglot.parse(content, read=dialect)  # raises on SQL the warehouse would not read


def test_incremental_and_snapshot_statements(commerce):
    files = exported(commerce)
    snapshot = next(v for k, v in files.items() if k.endswith("__snp_dim_customer.sql"))
    assert "UPDATE" in snapshot and "dbt_valid_to IS NULL" in snapshot and "IS DISTINCT FROM" in snapshot
    tests = files["tests/fct_order_line.sql"]
    assert "-- not_null: fct_order_line.order_id" in tests


def test_dbt_and_plain_sql_build_the_same_tables(tmp_path):
    duckdb = pytest.importorskip("duckdb")
    pytest.importorskip("dbt.adapters.duckdb")
    from murakumo.generate.verify import build

    project = init_project(tmp_path / "taxi")  # an incremental fact table, and a zone dimension
    result = build(project, sample=60)
    assert result.ok, result.error
    files = exported(project)
    con = duckdb.connect(str(tmp_path / "plain.duckdb"))
    con.execute(f"attach '{project.root / 'taxi.duckdb'}' as built (read_only)")
    con.execute(files["00_landing.sql"])
    raw = con.execute(
        "select table_schema, table_name from information_schema.tables "
        "where table_catalog = 'built' and table_schema like 'raw_%'"
    ).fetchall()
    for schema, table in raw:
        con.execute(f'insert into "{schema}"."{table}" select * from built."{schema}"."{table}"')
    scripts = sorted(p for p in files if p.endswith(".sql") and "/" not in p)[1:]
    nodes = [p.removesuffix(".sql").split("_", 1)[1].split("__", 1) for p in scripts]
    for _ in range(2):  # the second run takes the incremental path
        for script in scripts:
            con.execute(files[script])
        for schema, name in nodes:
            plain = con.execute(f'select count(*) from "{schema}"."{name}"').fetchone()[0]
            built = con.execute(f'select count(*) from built."{schema}"."{name}"').fetchone()[0]
            assert plain == built, name
    for path, content in files.items():
        if path.startswith("tests/"):
            for query in [q for q in content.split(";\n") if "SELECT" in q]:
                body = "\n".join(line for line in query.splitlines() if not line.startswith("--"))
                assert con.execute(body).fetchall() == [], query.splitlines()[0]


def test_cli_and_manifest_setting(commerce):
    result = runner.invoke(app, ["export", "sql", "-p", str(commerce.root), "--warehouse", "snowflake"])
    assert result.exit_code == 0, result.output
    assert (commerce.root / "export/sql/snowflake/00_landing.sql").is_file()
    commerce.manifest.generation.plain_sql = True
    assert "sql/duckdb/00_landing.sql" in generate(commerce).files
