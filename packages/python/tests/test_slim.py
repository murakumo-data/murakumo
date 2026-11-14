"""Slim CI: only what a change reaches is built."""

import subprocess

import pytest
from typer.testing import CliRunner

from murakumo import edit, slim
from murakumo.cli import app
from murakumo.project import load_project, save_model
from murakumo.scaffold import init_project

runner = CliRunner()


def git(root, *args):
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


@pytest.fixture
def project(tmp_path):
    project = init_project(tmp_path / "taxi")
    git(project.root, "init", "-q", "-b", "main")
    git(project.root, "add", "-A")
    git(project.root, "commit", "-q", "-m", "start")
    return project


def test_nothing_changed(project):
    chosen = slim.changed_since(project, "main")
    assert chosen.nothing and chosen.select == ""
    done = runner.invoke(app, ["verify", "--changed-since", "main", "-p", str(project.root)])
    assert done.exit_code == 0 and "nothing to build" in done.output


def test_a_change_selects_what_it_reaches_and_no_more(project):
    edit.update_column(project, "dim_taxi_zone", "borough", {"description": "The borough, as the city names it."})
    assert slim.changed_since(project, "main").nothing  # words only: nothing to build
    edit.update_column(project, "dim_taxi_zone", "borough", {"type": "string(40)"})
    chosen = slim.changed_since(project, "main")
    assert chosen.tables == ["dim_taxi_zone"] and chosen.models == ["dim_taxi_zone"]
    assert chosen.select == "+dim_taxi_zone"  # with what it is built from; fct_trip is left out


def test_a_raw_change_reaches_everything_built_from_it(project):
    edit.update_column(project, "taxi_zone_lookup", "Borough", {"type": "string(40)"})
    chosen = slim.changed_since(project, "main")
    assert "taxi_zone_lookup" in chosen.tables and "dim_taxi_zone" in chosen.tables
    assert "stg_nyc_tlc__taxi_zone_lookup" in chosen.models and "dim_taxi_zone" in chosen.models
    assert "fct_trip" not in chosen.models


def test_a_settings_change_builds_everything(project):
    path = project.root / "murakumo.yaml"
    path.write_text(path.read_text() + "\n# a change\n")
    chosen = slim.changed_since(load_project(project.root).project, "main")
    assert chosen.everything and "murakumo.yaml changed" in chosen.why and chosen.select is None


def test_an_unknown_commit_is_said_plainly(project):
    with pytest.raises(slim.SlimError, match="no commit 'origin/nope'"):
        slim.changed_since(project, "origin/nope")


def test_verify_builds_only_the_selection(project):
    pytest.importorskip("dbt.adapters.duckdb")
    edit.update_column(project, "dim_taxi_zone", "borough", {"type": "string(40)"})
    save_model(project)
    done = runner.invoke(app, ["verify", "--changed-since", "main", "--once", "-p", str(project.root)])
    assert done.exit_code == 0, done.output
    assert "dim_taxi_zone" in done.output and "verified" in done.output
    # The dimension and its staging model, not the fact and the rest.
    assert "2 models and snapshots built" in done.output.replace("\n", " ")
