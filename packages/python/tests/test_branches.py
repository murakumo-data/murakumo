import subprocess

import pytest
from typer.testing import CliRunner

from murakumo import branches, edit
from murakumo.cli import app
from murakumo.gitsync import GitError
from murakumo.project import load_project, save_model
from murakumo.scaffold import init_project

runner = CliRunner()


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(remote), str(work))
    git(work, "config", "user.name", "Ana")
    git(work, "config", "user.email", "ana@example.com")
    git(work, "checkout", "-q", "-b", "main")
    init_project(work / "taxi")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "Start")
    git(work, "push", "-q", "origin", "main")
    return work


def test_the_flow(repo):
    project = load_project(repo / "taxi").project
    assert [(s.environment, s.branch) for s in branches.flow(project)] == [
        ("dev", "dev"),
        ("stage", "stage"),
        ("prod", "main"),
    ]
    with pytest.raises(GitError, match="branch start"):
        branches.new(project, "feature", "zone codes")  # no dev yet

    started = branches.start(project, push=True)
    assert "created dev from origin/main" in started and "pushed stage to origin" in started
    assert "dev" in git(repo, "ls-remote", "--heads", "origin")

    branch = branches.new(project, "feature", "Add zone codes!")
    assert branch == "feature/add-zone-codes" and git(repo, "branch", "--show-current").strip() == branch
    with pytest.raises(GitError):
        branches.new(project, "experiment", "x")

    assert branches.check(project, "feature/add-zone-codes", "dev") == []
    assert branches.check(project, "dev", "stage") == [] and branches.check(project, "stage", "main") == []
    assert branches.check(project, "hotfix/urgent", "main") == []
    assert "takes stage" in branches.check(project, "dev", "main")[0]
    assert "neither what nor why" in branches.check(project, "zone-codes", "dev")[0]
    assert "not release" in branches.check(project, "feature/x", "release")[0]
    assert branches.environment_for(project, "stage") == "stage" and branches.environment_for(project, "main") == "prod"
    assert branches.environment_for(project, "feature/x") == "prod"

    # Work merged into dev: promoting dev to stage would move it.
    edit.add_column(project, "taxi_zone_lookup", "zone_code", "string")
    save_model(project)
    git(repo, "commit", "-qam", "Zone code")
    git(repo, "checkout", "-q", "dev")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge", branch)
    git(repo, "push", "-q", "origin", "dev")
    promotion = branches.promotion(load_project(repo / "taxi").project, "dev")
    assert (promotion["from"], promotion["to"], promotion["to_environment"]) == ("dev", "stage", "stage")
    assert promotion["commits"] == 2 and "zone_code" in promotion["changes"][0]["summary"]
    assert promotion["url"] is None  # a local remote has no web page to open

    result = runner.invoke(app, ["branch", "check", "--head", "dev", "--base", "main", "-p", str(repo / "taxi")])
    assert result.exit_code == 1 and "takes stage" in result.output
    assert runner.invoke(app, ["branch", "env", "main", "-p", str(repo / "taxi")]).output.strip() == "prod"


def test_compare_urls(repo, monkeypatch):
    project = load_project(repo / "taxi").project
    git(repo, "remote", "set-url", "origin", "git@github.com:murakumo-data/taxi.git")
    assert (
        branches._compare_url(project, "dev", "stage")
        == "https://github.com/murakumo-data/taxi/compare/stage...dev?expand=1"
    )
    git(repo, "remote", "set-url", "origin", "https://gitlab.com/team/taxi.git")
    assert "merge_requests/new?merge_request[source_branch]=dev" in branches._compare_url(project, "dev", "stage")
