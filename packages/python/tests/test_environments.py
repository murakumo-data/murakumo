import pytest
import yaml
from typer.testing import CliRunner

from murakumo import edit, environments
from murakumo.cli import app
from murakumo.generate import generate, write
from murakumo.manifest import Warehouse
from murakumo.migrate import plan_migration, prefixed, write_migration
from murakumo.project import load_project, save_model
from murakumo.scaffold import init_project

runner = CliRunner()


def test_placements(tmp_path, monkeypatch):
    project = init_project(tmp_path / "p")
    monkeypatch.setenv("MURAKUMO_DEVELOPER", "Ana Lima")
    assert environments.developer() == "ana"
    mine = environments.placement_for(project, None, Warehouse.SNOWFLAKE)
    assert (mine.environment, mine.single, mine.schema("staging"), mine.schema("raw_nyc_tlc")) == (
        "personal",
        "ana",
        "ana",
        "ana",
    )  # everyone's own schema holds every layer
    assert environments.placement_for(project, None, Warehouse.DUCKDB).shared  # a DuckDB file is already personal
    prod = environments.placement_for(project, "prod", Warehouse.SNOWFLAKE)
    assert prod.shared and prod.schema("marts") == "marts"
    pr = environments.placement_for(project, "prod", Warehouse.SNOWFLAKE, "pr_7_")
    assert pr.prefix == "pr_7_" and pr.schema("marts") == "pr_7_marts"
    with pytest.raises(environments.EnvError):
        environments.placement_for(project, "prod", Warehouse.SNOWFLAKE, "Bad prefix")
    with pytest.raises(environments.EnvError, match="no environment 'qa'"):
        environments.placement_for(project, "qa", Warehouse.SNOWFLAKE)


def test_environment_databases(tmp_path):
    from murakumo.manifest import Manifest

    manifest = Manifest.model_validate(
        {
            "project": {"name": "taxi"},
            "targets": {
                "warehouse": {"type": "snowflake"},
                "environments": ["dev", {"name": "prod", "database": "TAXI_PROD", "branch": "main"}],
            },
            "collaboration": {"personal_database": "MURAKUMO"},
        }
    )
    assert manifest.targets.names == ["dev", "prod"] and manifest.targets.environment("PROD").branch == "main"
    project = init_project(tmp_path / "p", warehouse=Warehouse.SNOWFLAKE)
    project.manifest = manifest
    assert environments.placement_for(project, None, Warehouse.SNOWFLAKE).database == "MURAKUMO"
    assert environments.placement_for(project, "prod", Warehouse.SNOWFLAKE).database == "TAXI_PROD"
    assert environments.placement_for(project, "dev", Warehouse.SNOWFLAKE).database is None  # the warehouse's
    profiles = yaml.safe_load(generate(project).files["dbt/profiles.yml"])["taxi"]
    assert profiles["target"] == "personal" and set(profiles["outputs"]) == {"personal", "dev", "prod"}
    assert "MURAKUMO" in profiles["outputs"]["personal"]["database"]
    assert "TAXI_PROD" in profiles["outputs"]["prod"]["database"]
    with pytest.raises(ValueError, match="everyone's own"):
        Manifest.model_validate({"project": {"name": "t"}, "targets": {"environments": ["personal"]}})


def test_a_build_in_ones_own_schema(tmp_path, monkeypatch):
    """Someone's own schema holds every layer: raw tables, models, snapshots and the audit tables.
    Built on DuckDB, by placing it there as a shared warehouse would."""
    pytest.importorskip("dbt.adapters.duckdb")
    from murakumo.generate.verify import build

    project = init_project(tmp_path / "taxi")
    real = environments.placement_for
    monkeypatch.setattr(
        environments,
        "placement_for",
        lambda p, e, w, x=None: (
            environments.Placement("personal", single="ana") if e is None and x is None else real(p, e, w, x)
        ),
    )
    result = build(project, sample=30)
    assert result.ok, result.error
    connection = environments.connect(load_project(project.root).project)
    tables = {
        r[0]
        for r in connection.execute(
            "select table_name from information_schema.tables where table_schema = 'ana'", fetch=True
        )
    }
    connection.close()
    assert {
        "yellow_tripdata",
        "stg_nyc_tlc__yellow_tripdata",
        "fct_trip",
        "dim_taxi_zone",
        "run_log",
        "row_counts",
    } <= tables


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
    assert build(project, environment="prod", sample=40).ok  # production: taxi_prod.duckdb
    root = str(project.root)
    assert runner.invoke(app, ["migrate", "--apply", "--env", "prod", "--mark-applied", "-p", root]).exit_code == 0

    # A branch renames a raw column and adds one, with its migration.
    edit.rename_column(project, "taxi_zone_lookup", "Zone", "ZoneName")
    edit.add_column(project, "taxi_zone_lookup", "zone_code", "string")
    save_model(project)
    write(generate(project), project.root)
    write_migration(project, plan_migration(project), "zone changes")

    cloned = runner.invoke(app, ["env", "clone", "--env", "prod", "--prefix", "pr_1_", "-p", root])
    assert cloned.exit_code == 0 and "raw_nyc_tlc -> pr_1_raw_nyc_tlc" in cloned.output
    applied = runner.invoke(app, ["migrate", "--apply", "--env", "prod", "--prefix", "pr_1_", "-p", root])
    assert applied.exit_code == 0 and "0002_zone_changes.sql" in applied.output, applied.output

    connection = environments.connect(load_project(project.root).project, database="taxi_prod.duckdb")
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

    built = runner.invoke(app, ["build", "--env", "prod", "--prefix", "pr_1_", "-p", root])
    assert built.exit_code == 0 and "built:" in built.output, built.output
    assert "pr_1_staging" in runner.invoke(app, ["env", "--env", "prod", "--prefix", "pr_1_", "-p", root]).output
    dropped = runner.invoke(app, ["env", "drop", "--env", "prod", "--prefix", "pr_1_", "--yes", "-p", root])
    assert dropped.exit_code == 0 and "pr_1_raw_nyc_tlc" in dropped.output
    assert runner.invoke(app, ["migrate", "--apply", "--env", "prod", "--prefix", "pr_1_", "-p", root]).exit_code == 0


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


def test_provisioning(tmp_path):
    project = init_project(tmp_path / "taxi", warehouse=Warehouse.SNOWFLAKE)
    sql = environments.provision(project)
    for line in (
        "create database if not exists MURAKUMO;",
        "create database if not exists TAXI_PROD;",
        "grant all on database TAXI_PROD to role TAXI_PROD_WRITE;",
        "grant role TAXI_PROD_WRITE to role TAXI_DEPLOYER_PROD;",
        "grant select on all tables in database TAXI_PROD to role TAXI_PROD_READ;",
        "grant role TAXI_PROD_READ to role TAXI_DEVELOPER;",
    ):
        assert line in sql
    assert "Nothing to run on DuckDB" in environments.provision(init_project(tmp_path / "local"))
    result = runner.invoke(app, ["env", "provision", "-p", str(project.root)])
    assert result.exit_code == 0 and "TAXI_STAGE" in result.output
