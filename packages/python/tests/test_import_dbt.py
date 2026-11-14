"""Importing an existing dbt project: tested on dbt projects Murakumo itself wrote, compiled by dbt."""

import os
import shutil

import pytest
import yaml
from typer.testing import CliRunner

from murakumo.cli import app
from murakumo.generate import generate, write
from murakumo.importers import dbt_project
from murakumo.project import load_project
from murakumo.scaffold import init_project
from murakumo.validate import validate_project

runner = CliRunner()


def dbt_folder(project, tmp_path, compile_it=True):
    """The project's generated dbt project, on its own in a folder, compiled when asked."""
    write(generate(project), project.root)
    folder = tmp_path / "their_dbt"
    shutil.copytree(project.root / "generated" / "dbt", folder)
    # Murakumo's run log hook writes to a table that only a build creates; a compile does not need it.
    settings = yaml.safe_load((folder / "dbt_project.yml").read_text())
    settings.pop("on-run-end", None)
    settings.pop("on-run-start", None)
    (folder / "dbt_project.yml").write_text(yaml.safe_dump(settings))
    if compile_it:
        pytest.importorskip("dbt.adapters.duckdb")
        from dbt.cli.main import dbtRunner

        variables = {"MURAKUMO_DUCKDB_PATH": str(tmp_path / "x.duckdb"), "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
        saved = {k: os.environ.get(k) for k in variables}
        os.environ.update(variables)
        try:
            done = dbtRunner().invoke(
                ["compile", "--project-dir", str(folder), "--profiles-dir", str(folder), "--quiet"]
            )
        finally:
            for key, value in saved.items():
                os.environ.pop(key, None) if value is None else os.environ.__setitem__(key, value)
        assert done.success, done.exception
    return folder


def empty(tmp_path):
    project = init_project(tmp_path / "new")
    project.model.entities.clear()
    project.model.relationships.clear()
    project.model.sources.clear()
    project.model.metrics.clear()
    return project


def test_a_compiled_project_comes_in_with_its_mappings(tmp_path):
    folder = dbt_folder(init_project(tmp_path / "taxi"), tmp_path)
    imported = dbt_project.read(folder)
    assert imported.compiled and imported.dialect == "duckdb"
    assert [s.source.name for s in imported.sources] == ["nyc_tlc"]
    raw = {e.name: e for e in imported.sources[0].entities}
    assert set(raw) == {"yellow_tripdata", "taxi_zone_lookup"}
    assert raw["yellow_tripdata"].attribute("VendorID") is not None  # the source's own names
    assert any(n.startswith("yellow_tripdata: the dbt project does not say its columns' types") for n in imported.notes)
    assert "stg_nyc_tlc__yellow_tripdata" in imported.skipped  # Murakumo writes staging itself

    project = empty(tmp_path)
    report = dbt_project.apply(project, imported)
    fact, zone = project.model.entity("fct_trip"), project.model.entity("dim_taxi_zone")
    assert fact.layer.value == "marts" and fact.kind.value == "fact" and zone.kind.value == "dimension"
    assert zone.primary_key[0].name == "zone_id"
    mapped = {m.target: m.sources for m in zone.mappings}
    # Followed through the staging model's rename, back to the raw column.
    assert mapped["zone_id"] == ["taxi_zone_lookup.LocationID"] and mapped["borough"] == ["taxi_zone_lookup.Borough"]
    assert {m.target: m.sources for m in fact.mappings}["pickup_at"] == ["yellow_tripdata.tpep_pickup_datetime"]
    # coalesce(column, -1) is not the column unchanged: left for a person, with what it reads.
    assert "fct_trip.pickup_zone_id: computed in the dbt model (it reads yellow_tripdata.PULocationID)" in " ".join(
        report.notes
    )
    assert "trip_id" not in {m.target for m in fact.mappings}  # a hash of several columns: computed
    assert any(note.startswith("fct_trip.trip_id: computed") for note in report.notes)
    assert report.mapped > 5 and report.relationships >= 1
    assert any(r.from_ == "fct_trip" and r.to == "dim_taxi_zone" for r in project.model.relationships)
    errors = [d for d in validate_project(project) if d.level.value == "error"]
    assert not errors, errors


def test_without_a_compile_it_brings_tables_and_says_what_is_missing(tmp_path):
    folder = dbt_folder(init_project(tmp_path / "taxi"), tmp_path, compile_it=False)
    imported = dbt_project.read(folder)
    assert not imported.compiled and "dbt compile" in imported.notes[0]
    project = empty(tmp_path)
    report = dbt_project.apply(project, imported)
    assert {"yellow_tripdata", "fct_trip", "dim_taxi_zone"} <= set(report.added)
    assert project.model.entity("fct_trip").attributes and report.mapped == 0


def test_the_command_writes_the_model_and_leaves_existing_tables_alone(tmp_path):
    folder = dbt_folder(init_project(tmp_path / "taxi"), tmp_path, compile_it=False)
    target = init_project(tmp_path / "mine")  # already has fct_trip and the rest
    result = runner.invoke(app, ["import", "dbt", str(folder), "-p", str(target.root)])
    assert result.exit_code == 0, result.output
    assert "fct_trip is already in the model" in result.output
    assert len(load_project(target.root).project.model.entity("fct_trip").mappings) > 3  # untouched

    fresh = tmp_path / "fresh"
    assert runner.invoke(app, ["init", str(fresh), "--yes"]).exit_code == 0
    for entity in list((fresh / "model" / "entities").rglob("*.yaml")):
        entity.unlink()
    (fresh / "model" / "relationships.yaml").unlink(missing_ok=True)
    (fresh / "model" / "metrics.yaml").unlink(missing_ok=True)
    dry = runner.invoke(app, ["import", "dbt", str(folder), "-p", str(fresh), "--dry-run"])
    assert dry.exit_code == 0 and "nothing was written" in dry.output
    result = runner.invoke(app, ["import", "dbt", str(folder), "-p", str(fresh)])
    assert result.exit_code == 0, result.output
    assert load_project(fresh).project.model.entity("dim_taxi_zone") is not None


def test_not_a_dbt_project(tmp_path):
    with pytest.raises(dbt_project.DbtImportError, match="dbt_project.yml"):
        dbt_project.read(tmp_path)
