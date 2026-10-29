import json

import pytest
import yaml
from typer.testing import CliRunner

from murakumo import cli, guide
from murakumo.cli import app
from murakumo.guide import Status, install_command, profile_variables, run_checks
from murakumo.manifest import Warehouse
from murakumo.project import load_project
from murakumo.scaffold import init_project

runner = CliRunner()
SNOWFLAKE_VARIABLES = ["SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PRIVATE_KEY_PATH", "SNOWFLAKE_PASSWORD"]


def by_title(checks, text):
    return next(c for c in checks if text in c.title)


# init -------------------------------------------------------------------------------------------


def test_init_with_flags(tmp_path):
    result = runner.invoke(
        app,
        [
            "init",
            str(tmp_path / "shop"),
            "--target",
            "snowflake",
            "--orchestrator",
            "dagster",
            "--pattern",
            "datavault",
            "-y",
        ],
    )
    assert result.exit_code == 0, result.output
    manifest = yaml.safe_load((tmp_path / "shop" / "murakumo.yaml").read_text())
    assert manifest["targets"]["warehouse"] == {"type": "snowflake", "database": "SHOP"}
    assert manifest["generation"]["orchestrator"] == "dagster"
    assert manifest["modelling"]["pattern"] == "datavault"
    assert "What this project needs on this machine" in result.output
    assert load_project(tmp_path / "shop").diagnostics == []


def test_init_without_a_terminal_uses_defaults(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path / "demo")])
    assert result.exit_code == 0, result.output
    manifest = yaml.safe_load((tmp_path / "demo" / "murakumo.yaml").read_text())
    assert manifest["targets"]["warehouse"]["type"] == "duckdb"
    assert manifest["targets"]["warehouse"]["database"] == "demo.duckdb"


def test_init_asks_in_a_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_can_ask", lambda: True)
    answers = "\n".join(["sales_platform", "postgres", "prefect", "kimball", "n"]) + "\n"
    result = runner.invoke(app, ["init", str(tmp_path / "x")], input=answers)
    assert result.exit_code == 0, result.output
    project = load_project(tmp_path / "x").project
    assert project.manifest.project.name == "sales_platform"
    assert project.manifest.targets.warehouse.type is Warehouse.POSTGRES
    assert project.manifest.generation.orchestrator.value == "prefect"
    assert project.model.entities == []  # declined the example


def test_init_refuses_an_existing_project(tmp_path):
    init_project(tmp_path / "p")
    result = runner.invoke(app, ["init", str(tmp_path / "p"), "-y"])
    assert result.exit_code == 1 and "already exists" in result.output


# setup ------------------------------------------------------------------------------------------


def test_duckdb_project_is_ready(tmp_path):
    project = init_project(tmp_path / "p")
    checks = run_checks(project)
    assert [c.title for c in checks if c.status is Status.TODO] == []
    assert any("no account or credentials" in c.title for c in checks)
    result = runner.invoke(app, ["setup", str(project.root), "--format", "json", "--check"])
    assert result.exit_code == 0
    assert json.loads(result.output)["ready"] is True


def test_snowflake_needs_variables(tmp_path, monkeypatch):
    for name in SNOWFLAKE_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    project = init_project(tmp_path / "p", warehouse=Warehouse.SNOWFLAKE)
    check = by_title(run_checks(project), "connection variable")
    assert check.status is Status.TODO
    assert 'export SNOWFLAKE_ACCOUNT="..."' in check.steps
    assert any("snowflake.md" in step for step in check.steps)
    assert runner.invoke(app, ["setup", str(project.root), "--check"]).exit_code == 1


def test_snowflake_key_file(tmp_path, monkeypatch):
    key = tmp_path / "key.p8"
    key.write_text("not a real key")
    key.chmod(0o644)
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "ORG-ACCOUNT")
    monkeypatch.setenv("SNOWFLAKE_USER", "ME")
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PATH", str(key))
    project = init_project(tmp_path / "p", warehouse=Warehouse.SNOWFLAKE)
    checks = run_checks(project)
    assert by_title(checks, "Connection variables are set").status is Status.OK
    assert by_title(checks, "can be read by other users").steps == [f"chmod 600 {key}"]
    key.unlink()
    assert by_title(run_checks(project), "does not exist").status is Status.TODO


@pytest.mark.parametrize(
    ("warehouse", "expected"),
    [
        (Warehouse.DATABRICKS, ["DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN"]),
        (Warehouse.BIGQUERY, ["BIGQUERY_PROJECT"]),
        (Warehouse.POSTGRES, ["DB_HOST", "DB_USER", "DB_PASSWORD"]),
    ],
)
def test_variables_come_from_the_generated_profile(tmp_path, warehouse, expected):
    project = init_project(tmp_path / "p", warehouse=warehouse)
    required, _ = profile_variables(project, warehouse)
    assert required == expected


def test_source_checks(tmp_path, monkeypatch):
    monkeypatch.delenv("CRM_DB_URL", raising=False)
    project = init_project(tmp_path / "p", example=False)
    manifest = project.root / "murakumo.yaml"
    manifest.write_text(
        manifest.read_text()
        + "\nsources:\n  - name: erp\n    from: csv:dictionaries/erp.csv\n"
        + "  - name: crm\n    from: introspect:env:CRM_DB_URL\n  - name: manual\n"
    )
    project = load_project(project.root).project
    checks = run_checks(project)
    assert by_title(checks, "csv file not found").status is Status.TODO
    crm = by_title(checks, "Set CRM_DB_URL")
    assert crm.status is Status.TODO and crm.steps[0].startswith('export CRM_DB_URL="postgresql://')
    assert by_title(checks, "No from: line").status is Status.INFO

    (project.root / "dictionaries").mkdir()
    (project.root / "dictionaries" / "erp.csv").write_text("table,column,type\n")
    monkeypatch.setenv("CRM_DB_URL", "postgresql://reader@localhost/crm")
    checks = run_checks(project)
    assert by_title(checks, "csv file found").status is Status.OK
    assert by_title(checks, "postgres connection configured").status is Status.OK


def test_orchestrator_is_guidance_only(tmp_path, monkeypatch):
    monkeypatch.setattr(guide.shutil, "which", lambda command: None)
    project = init_project(tmp_path / "p")
    check = next(c for c in run_checks(project) if c.area == "Orchestrator: airflow")
    assert check.status is Status.INFO  # not needed to build yet
    assert any("astro dev start" in step for step in check.steps)
    assert "does not generate its pipelines yet" in check.detail
    assert check.numbered and any("astronomer.io" in link for link in check.docs)


def test_install_command(monkeypatch):
    monkeypatch.setattr(guide.sys, "prefix", "/Users/me/.local/share/uv/tools/murakumo")
    assert install_command(["snowflake"], ["dbt-snowflake"]) == (
        'uv tool install --force "murakumo[snowflake]" --with dbt-snowflake'
    )
    monkeypatch.setattr(guide.sys, "prefix", "/Users/me/project/.venv")
    monkeypatch.setattr(guide, "installed", lambda module: module == "pip")
    assert install_command(["canvas"], []) == 'pip install "murakumo[canvas]"'
    monkeypatch.setattr(guide, "installed", lambda module: False)
    assert install_command(["canvas"], []) == 'uv pip install "murakumo[canvas]"'


def test_install_command_through_the_npm_launcher(monkeypatch):
    monkeypatch.setenv("MURAKUMO_LAUNCHER", "npm")
    monkeypatch.setenv("MURAKUMO_EXTRAS", "postgres")
    monkeypatch.delenv("MURAKUMO_WITH", raising=False)
    command = install_command(["snowflake", "verify"], ["dbt-snowflake"])
    assert command.startswith('export MURAKUMO_EXTRAS="postgres,snowflake,verify" MURAKUMO_WITH="dbt-snowflake"')


def test_init_takes_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_can_ask", lambda: True)
    answers = "\n".join(["shop", "2", "", "9", "4", "y"]) + "\n"
    result = runner.invoke(app, ["init", str(tmp_path / "x")], input=answers)
    assert result.exit_code == 0, result.output
    manifest = load_project(tmp_path / "x").project.manifest
    assert manifest.targets.warehouse.type is Warehouse.SNOWFLAKE
    assert manifest.generation.orchestrator.value == "airflow"  # Enter keeps the suggestion
    assert manifest.modelling.pattern.value == "3nf"  # 9 is out of range, so it asked again
    assert "Type a number from 1 to 5" in result.output
    assert "murakumo --help" in result.output and "murakumo config" in result.output


def test_config_changes_answers_and_keeps_comments(tmp_path):
    project = init_project(tmp_path / "p")
    result = runner.invoke(app, ["config", str(project.root), "--target", "snowflake", "--pattern", "datavault"])
    assert result.exit_code == 0, result.output
    text = (project.root / "murakumo.yaml").read_text()
    assert "type: snowflake" in text and "database: P\n" in text
    assert "# snowflake | databricks" in text and "# kimball | datavault" in text
    manifest = load_project(project.root).project.manifest
    assert manifest.modelling.pattern.value == "datavault"
    assert "Nothing changed" in runner.invoke(app, ["config", str(project.root), "-t", "snowflake"]).output


def test_missing_database_gives_sql(tmp_path):
    check = guide.Check("Warehouse", "Database X does not exist", Status.TODO, sql="create database X;")
    assert check.as_dict()["sql"] == "create database X;"
