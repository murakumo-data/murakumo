from typer.testing import CliRunner

from murakumo import __version__
from murakumo.cli import app

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_info_command_runs():
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert "murakumo" in result.output


def test_a_long_name_does_not_swallow_the_comment_after_it(tmp_path):
    # "taxi_platform.duckdb" is as wide as the column the comments line up on.
    import yaml

    from murakumo.manifest import Orchestrator
    from murakumo.scaffold import init_project

    project = init_project(tmp_path / "taxi_platform", orchestrator=Orchestrator.DATABRICKS_WORKFLOWS)
    written = yaml.safe_load((project.root / "murakumo.yaml").read_text())
    assert written["targets"]["warehouse"]["database"] == "taxi_platform.duckdb"
    assert written["generation"]["orchestrator"] == "databricks_workflows"


def test_explore_commands_read_the_model_alone():
    from pathlib import Path

    commerce = str(Path(__file__).resolve().parents[3] / "examples" / "commerce")
    names = runner.invoke(app, ["relations", "orders.o_custkey", "-p", commerce])
    assert names.exit_code == 0, names.output
    assert "customer.c_custkey" in names.output and "--check" in names.output
    sql = runner.invoke(app, ["preview", "fct_order_line", "--sql", "-p", commerce])
    assert sql.exit_code == 0 and "__stg_" in sql.output
    assert runner.invoke(app, ["relations", "orders", "-p", commerce]).exit_code == 1  # TABLE.COLUMN
