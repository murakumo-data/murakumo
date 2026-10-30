import pytest
from typer.testing import CliRunner

from murakumo import edit, environments
from murakumo.cli import app
from murakumo.generate import generate, write
from murakumo.manifest import Warehouse
from murakumo.migrate import plan_migration, prefixed, write_migration
from murakumo.project import load_project, save_model
from murakumo.scaffold import init_project

runner = CliRunner()


def test_personal_prefix(tmp_path, monkeypatch):
    project = init_project(tmp_path / "p")
    monkeypatch.setenv("MURAKUMO_DEVELOPER", "Ana Lima")
    assert environments.developer() == "ana"
    assert environments.prefix_for(project, "dev", Warehouse.SNOWFLAKE) == "dev_ana_"
    assert environments.prefix_for(project, "prod", Warehouse.SNOWFLAKE) == ""
    assert environments.prefix_for(project, "dev", Warehouse.DUCKDB) == ""  # a DuckDB file is already personal
    assert environments.prefix_for(project, "dev", Warehouse.SNOWFLAKE, "pr_7_") == "pr_7_"
    with pytest.raises(environments.EnvError):
        environments.prefix_for(project, "dev", Warehouse.SNOWFLAKE, "Bad prefix")


def test_migrations_move_under_a_prefix():
    sql = (
        "CREATE SCHEMA IF NOT EXISTS raw_shop;\n"
        "ALTER TABLE raw_shop.orders RENAME COLUMN a TO b;\n"
        "-- DROP TABLE raw_shop.old;\n"
        "ALTER TABLE IF EXISTS marts.fct ADD COLUMN c INT;"
    )
    statements = prefixed(sql, "pr_1_", "duckdb", {"raw_shop", "marts"})
    assert statements[0] == "CREATE SCHEMA IF NOT EXISTS pr_1_raw_shop"
    assert "pr_1_raw_shop.orders" in statements[1] and "pr_1_marts.fct" in statements[2]
    assert len(statements) == 3  # the commented-out drop stays a comment


def test_try_migrations_on_a_clone(tmp_path):
    pytest.importorskip("dbt.adapters.duckdb")
    from murakumo.generate.verify import build

    project = init_project(tmp_path / "taxi")
    write(generate(project), project.root)
    write_migration(project, plan_migration(project))
    assert build(project, sample=40).ok  # the shared schemas, as production
    root = str(project.root)
    assert runner.invoke(app, ["migrate", "--apply", "--mark-applied", "-p", root]).exit_code == 0

    # A branch renames a raw column and adds one, with its migration.
    edit.rename_column(project, "taxi_zone_lookup", "Zone", "ZoneName")
    edit.add_column(project, "taxi_zone_lookup", "zone_code", "string")
    save_model(project)
    write(generate(project), project.root)
    write_migration(project, plan_migration(project), "zone changes")

    cloned = runner.invoke(app, ["env", "clone", "--prefix", "pr_1_", "-p", root])
    assert cloned.exit_code == 0 and "raw_nyc_tlc -> pr_1_raw_nyc_tlc" in cloned.output
    applied = runner.invoke(app, ["migrate", "--apply", "--prefix", "pr_1_", "-p", root])
    assert applied.exit_code == 0 and "0002_zone_changes.sql" in applied.output, applied.output

    connection = environments.connect(load_project(project.root).project)
    columns = lambda schema: {  # noqa: E731
        r[0]
        for r in connection.execute(
            f"select column_name from information_schema.columns where table_schema = '{schema}' "
            "and table_name = 'taxi_zone_lookup'",
            fetch=True,
        )
    }
    assert {"ZoneName", "zone_code"} <= columns("pr_1_raw_nyc_tlc")
    assert "Zone" in columns("raw_nyc_tlc") and "zone_code" not in columns("raw_nyc_tlc")  # production untouched
    connection.close()

    built = runner.invoke(app, ["build", "--prefix", "pr_1_", "-p", root])
    assert built.exit_code == 0 and "built:" in built.output, built.output
    assert "pr_1_staging" in runner.invoke(app, ["env", "--prefix", "pr_1_", "-p", root]).output
    dropped = runner.invoke(app, ["env", "drop", "--prefix", "pr_1_", "--yes", "-p", root])
    assert dropped.exit_code == 0 and "pr_1_raw_nyc_tlc" in dropped.output
    assert runner.invoke(app, ["migrate", "--apply", "--prefix", "pr_1_", "-p", root]).exit_code == 0  # re-creatable


def test_shared_schemas_are_never_dropped(tmp_path):
    project = init_project(tmp_path / "p")
    result = runner.invoke(app, ["env", "drop", "--yes", "-p", str(project.root)])
    assert result.exit_code == 1 and "never dropped" in result.output


class FakeSnowflake:
    """Records statements; answers SHOW SCHEMAS with the shared schemas."""

    def __init__(self, schemas):
        self.statements, self.schemas = [], schemas

    def cursor(self):
        return self

    def execute(self, sql):
        self.statements.append(sql)
        self.result = [(None, s.upper()) for s in self.schemas] if sql.startswith("show schemas") else []

    def fetchall(self):
        return self.result

    def close(self):
        pass


def test_snowflake_clones_are_zero_copy(tmp_path):
    project = init_project(tmp_path / "p", warehouse=Warehouse.SNOWFLAKE)
    fake = FakeSnowflake(["raw_nyc_tlc", "staging", "marts"])
    connection = environments.Connection(Warehouse.SNOWFLAKE, fake, "P")
    copied = environments.clone(project, connection, "pr_3_")
    assert copied == ["raw_nyc_tlc -> pr_3_raw_nyc_tlc", "staging -> pr_3_staging", "marts -> pr_3_marts"]
    assert "create or replace schema P.pr_3_raw_nyc_tlc clone P.raw_nyc_tlc" in fake.statements
