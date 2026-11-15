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
