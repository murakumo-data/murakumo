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
    assert check["steps"][3]["run"].startswith("murakumo branch check")  # the pull request follows the flow
    assert "--format markdown" in check["steps"][4]["run"]  # the pull request's impact summary
    assert [s["run"] for s in check["steps"][5:]] == [
        "murakumo validate --strict",
        "murakumo generate --check",
        "murakumo migrate --check",
        "murakumo verify",
    ]
    assert "deploy" not in workflow["jobs"]


def test_deploy_needs_a_real_warehouse(project):
    assert ci.status(project, "github", deploy=True)["deploy_possible"] is False  # DuckDB: nothing to deploy to
    project.manifest.targets.warehouse.type = Warehouse.SNOWFLAKE
    workflow = yaml.safe_load(ci.render(project, "github", deploy=True)[1])
    assert workflow[True]["push"]["branches"] == ["dev", "stage", "main"]  # each environment's branch
    deploy = workflow["jobs"]["deploy-prod"]
    assert deploy["environment"] == "prod" and deploy["needs"] == "check"
    assert deploy["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    assert deploy["env"]["SNOWFLAKE_PRIVATE_KEY"] == "${{ secrets.SNOWFLAKE_PRIVATE_KEY }}"
    assert deploy["steps"][-1]["run"].splitlines() == [
        "murakumo migrate --apply --env prod",
        "murakumo build --env prod",
    ]
    assert workflow["jobs"]["deploy-dev"]["steps"][-1]["run"].splitlines()[-1] == "murakumo build --env dev"
    assert "refs/heads/stage" in workflow["jobs"]["deploy-stage"]["if"]
    gitlab = yaml.safe_load(ci.render(project, "gitlab", deploy=True)[1])
    assert gitlab["murakumo-deploy-prod"]["rules"][0] == {"if": '$CI_COMMIT_BRANCH == "main"', "when": "manual"}
    assert gitlab["murakumo-deploy-dev"]["rules"][0] == {"if": '$CI_COMMIT_BRANCH == "dev"'}


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


def test_pull_request_previews(project):
    project.manifest.targets.warehouse.type = Warehouse.SNOWFLAKE
    workflow = yaml.safe_load(ci.render(project, "github", deploy=False, preview=True)[1])
    assert workflow[True]["pull_request"]["types"] == ["opened", "synchronize", "reopened", "closed"]
    preview = workflow["jobs"]["preview"]
    assert preview["steps"][-1]["run"].splitlines() == [
        'ENV="$(murakumo branch env "$BASE_BRANCH")"',  # a copy of the environment it goes into
        'murakumo env clone --env "$ENV" --prefix pr_${{ github.event.number }}_',
        'murakumo migrate --apply --env "$ENV" --prefix pr_${{ github.event.number }}_',
        'murakumo build --env "$ENV" --prefix pr_${{ github.event.number }}_',
    ]
    cleanup = workflow["jobs"]["preview-cleanup"]
    assert "closed" in cleanup["if"] and "env drop" in cleanup["steps"][-1]["run"]
    gitlab = yaml.safe_load(ci.render(project, "gitlab", deploy=False, preview=True)[1])
    assert gitlab["murakumo-preview"]["environment"]["on_stop"] == "murakumo-preview-stop"
    project.manifest.targets.warehouse.type = Warehouse.DUCKDB
    assert "preview" not in yaml.safe_load(ci.render(project, "github", deploy=False, preview=True)[1])["jobs"]
