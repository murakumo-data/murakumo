import subprocess

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from murakumo import ci
from murakumo.cli import app
from murakumo.generate import generate, write
from murakumo.manifest import Warehouse
from murakumo.scaffold import init_project
from murakumo.server import create_app

runner = CliRunner()


@pytest.fixture
def project(tmp_path):
    (tmp_path / "repo").mkdir()
    subprocess.run(["git", "init", "-q", str(tmp_path / "repo")], check=True)
    return init_project(tmp_path / "repo" / "platform")


def test_github_workflow(project):
    path, content = ci.render(project, "github", deploy=False)
    assert path == project.root.parent / ".github" / "workflows" / "murakumo.yml"  # at the repository root
    workflow = yaml.safe_load(content)
    check = workflow["jobs"]["check"]
    assert check["defaults"]["run"]["working-directory"] == "platform"
    assert [s["run"] for s in check["steps"][3:]] == [
        "murakumo validate --strict",
        "murakumo generate --check",
        "murakumo migrate --check",
        "murakumo verify",
    ]
    assert "deploy" not in workflow["jobs"]


def test_deploy_needs_a_real_warehouse(project):
    assert ci.status(project, "github", deploy=True)["deploy_possible"] is False  # DuckDB: nothing to deploy to
    project.manifest.targets.warehouse.type = Warehouse.SNOWFLAKE
    deploy = yaml.safe_load(ci.render(project, "github", deploy=True)[1])["jobs"]["deploy"]
    assert deploy["environment"] == "production" and deploy["needs"] == "check"
    assert deploy["env"]["SNOWFLAKE_PRIVATE_KEY"] == "${{ secrets.SNOWFLAKE_PRIVATE_KEY }}"
    assert deploy["steps"][-1]["run"] == "murakumo build --env prod"
    gitlab = yaml.safe_load(ci.render(project, "gitlab", deploy=True)[1])
    assert gitlab["murakumo-deploy"]["rules"][0]["when"] == "manual"


def test_never_overwrites_changes_without_asking(project):
    assert ci.status(project, "github", False)["state"] == "missing"
    info = ci.write(project, "github", False)
    assert info["state"] == "current"
    path = project.root.parent / ".github/workflows/murakumo.yml"
    path.write_text(path.read_text() + "# my own step\n")
    assert ci.status(project, "github", False)["state"] == "differs"
    with pytest.raises(ci.CIError, match="not Murakumo's"):
        ci.write(project, "github", False)
    assert "-# my own step" in ci.status(project, "github", False)["diff"]
    assert ci.write(project, "github", False, force=True)["state"] == "current"


def test_cli(project):
    result = runner.invoke(app, ["ci", "--provider", "gitlab", "-p", str(project.root)])
    assert result.exit_code == 0, result.output
    assert (project.root.parent / ".gitlab-ci.yml").is_file()
    assert "is up to date" in runner.invoke(app, ["ci", "--provider", "gitlab", "-p", str(project.root)]).output


def test_the_checks_run_here(project):
    pytest.importorskip("dbt.adapters.duckdb")
    write(generate(project), project.root)
    run = ci.run_checks(project.root, ci.Run())
    assert [s.state for s in run.steps] == ["passed"] * 4, [s.output[-300:] for s in run.steps]


def test_api(project):
    client = TestClient(create_app(project.root), base_url="http://localhost")
    info = client.get("/api/ci").json()
    assert info["state"] == "missing" and info["file"] == ".github/workflows/murakumo.yml"
    assert client.post("/api/ci", json={"provider": "github"}).json()["state"] == "current"
    assert client.get("/api/ci/run").json() == {"running": False, "steps": []}
