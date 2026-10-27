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
