import subprocess
from pathlib import Path

import pytest

from murakumo import edit, gitsync
from murakumo.generate import generate, write
from murakumo.migrate import plan_migration, write_migration
from murakumo.project import load_project, save_model
from murakumo.scaffold import init_project


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def identity(repo, name):
    git(repo, "config", "user.name", name)
    git(repo, "config", "user.email", f"{name}@example.com")


@pytest.fixture
def team(tmp_path):
    """A shared remote with the taxi project on main, and two people's clones of it."""
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    ana = tmp_path / "ana"
    git(tmp_path, "clone", "-q", str(remote), str(ana))
    identity(ana, "ana")
    git(ana, "checkout", "-q", "-b", "main")
    project = init_project(ana / "platform")
    write(generate(project), project.root)
    write_migration(project, plan_migration(project))
    git(ana, "add", "-A")
    git(ana, "commit", "-q", "-m", "Start")
    git(ana, "push", "-q", "origin", "main")
    ben = tmp_path / "ben"
    git(tmp_path, "clone", "-q", str(remote), str(ben))
    identity(ben, "ben")
    return ana, ben


def change(repo, branch, action, title):
    """Make a model change on a branch, with its migration and code, and commit it."""
    project = load_project(repo / "platform").project
    action(project)
    save_model(project)
    write(generate(project), project.root)
    write_migration(project, plan_migration(project), title)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", title)


def test_sync_brings_in_main_and_replans_migrations(team):
    ana, ben = team
    git(ben, "checkout", "-q", "-b", "ben/zones")
    change(ben, "ben/zones", lambda p: edit.add_column(p, "taxi_zone_lookup", "zone_code", "string"), "zone code")
    change(ana, "main", lambda p: edit.add_column(p, "yellow_tripdata", "tip_amount", "money"), "tips")
    git(ana, "push", "-q", "origin", "main")

    project = load_project(ben / "platform").project
    before = gitsync.status(project, refresh=True)
    assert before["behind"] == 1 and before["ahead"] == 1
    assert [c["summary"] for c in before["incoming_changes"]] == ["yellow_tripdata: new column tip_amount (money)"]

    result = gitsync.sync(project)
    assert result.state == "synced" and result.commit
    # Both wrote migration 0002; Ben's is planned again after Ana's, so the numbers never clash.
    assert result.migrations == ["0002_zone_code.sql replaced by 0003_zone_code.sql, planned after main's migrations"]
    migrations = sorted(p.name for p in (ben / "platform/migrations/duckdb").glob("0*.sql"))
    assert migrations == ["0001_initial.sql", "0002_tips.sql", "0003_zone_code.sql"]
    third = (ben / "platform/migrations/duckdb/0003_zone_code.sql").read_text()
    assert "zone_code" in third and "tip_amount" not in third

    merged = load_project(ben / "platform").project
    assert merged.model.entity("yellow_tripdata").attribute("tip_amount")
    assert merged.model.entity("taxi_zone_lookup").attribute("zone_code")
    assert plan_migration(merged).changes == []  # migrations match the merged model
    assert not gitsync.uncommitted(gitsync.where(merged))
    assert gitsync.status(merged)["behind"] == 0
    assert gitsync.sync(merged).state == "up_to_date"


def test_conflicts_in_the_model_stop_the_sync(team):
    ana, ben = team
    git(ben, "checkout", "-q", "-b", "ben/describe")
    describe = lambda text: lambda p: edit.update_entity(p, "fct_trip", {"description": text})  # noqa: E731
    change(ben, "ben/describe", describe("Ben's trips"), "ben")
    change(ana, "main", describe("Ana's trips"), "ana")
    git(ana, "push", "-q", "origin", "main")

    project = load_project(ben / "platform").project
    result = gitsync.sync(project)
    assert result.state == "conflicts" and result.conflicts == ["platform/model/entities/marts/fct_trip.yaml"]
    with pytest.raises(gitsync.GitError, match="still unresolved"):
        gitsync.sync(project, resume=True)
    git(ben, "checkout", "--theirs", "--", "platform/model/entities/marts/fct_trip.yaml")
    git(ben, "add", "platform/model/entities/marts/fct_trip.yaml")
    finished = gitsync.sync(load_project(ben / "platform").project, resume=True)
    assert finished.state == "synced"
    assert load_project(ben / "platform").project.model.entity("fct_trip").description == "Ana's trips"


def test_refuses_with_uncommitted_changes(team):
    _, ben = team
    git(ben, "checkout", "-q", "-b", "ben/wip")
    (ben / "platform/murakumo.yaml").write_text((ben / "platform/murakumo.yaml").read_text() + "\n")
    with pytest.raises(gitsync.GitError, match="uncommitted"):
        gitsync.sync(load_project(ben / "platform").project)


def test_commit(team):
    _, ben = team
    project = load_project(ben / "platform").project
    edit.add_column(project, "fct_trip", "note", "string")
    save_model(project)
    [local] = gitsync.status(load_project(ben / "platform").project)["local_changes"]
    assert local["summary"] == "fct_trip: new column note (string)" and local["class"] == "additive"
    sha = gitsync.commit(project, "Add a note")
    assert sha and not gitsync.uncommitted(gitsync.where(project))
    with pytest.raises(gitsync.GitError, match="nothing to commit"):
        gitsync.commit(project, "again")


def test_not_a_repository(tmp_path):
    with pytest.raises(gitsync.GitError, match="not in a git repository"):
        gitsync.status(init_project(Path(tmp_path) / "solo"))


def test_cli_and_api(team):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from murakumo.cli import app
    from murakumo.server import create_app

    ana, ben = team
    git(ben, "checkout", "-q", "-b", "ben/x")
    change(ana, "main", lambda p: edit.add_column(p, "yellow_tripdata", "tip_amount", "money"), "tips")
    git(ana, "push", "-q", "origin", "main")
    runner = CliRunner()
    checked = runner.invoke(app, ["sync", "--check", "-p", str(ben / "platform")])
    assert checked.exit_code == 0 and "new column tip_amount" in checked.output
    diffed = runner.invoke(app, ["diff", "origin/main", "-p", str(ben / "platform")])
    assert "column tip_amount removed" in diffed.output  # this branch, compared with main, lacks it

    client = TestClient(create_app(ben / "platform"), base_url="http://localhost")
    assert client.get("/api/git").json()["behind"] == 1
    synced = client.post("/api/git/sync").json()
    assert synced["sync"]["state"] == "synced"
    assert any(e["name"] == "yellow_tripdata" for e in synced["state"]["model"]["entities"])
    assert client.post("/api/git/commit", json={"message": "x"}).status_code == 409  # nothing to commit
