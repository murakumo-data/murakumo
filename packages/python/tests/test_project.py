import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from murakumo.cli import app
from murakumo.diagnostics import Level
from murakumo.manifest import Manifest
from murakumo.project import ENTITIES_DIR, RELATIONSHIPS_FILE, dump_yaml, load_project, save_model, to_data
from murakumo.scaffold import ProjectExistsError, init_project
from murakumo.validate import validate_project

runner = CliRunner()


def codes(diagnostics):
    """Diagnostic codes, leaving out MK005 (missing ids), which every hand-written fixture triggers."""
    return sorted(d.code for d in diagnostics if d.code != "MK005")


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else yaml.safe_dump(data, sort_keys=False))


def load_and_validate(root: Path):
    result = load_project(root)
    assert result.project is not None, result.diagnostics
    return result.diagnostics + validate_project(result.project)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    init_project(tmp_path / "demo")
    return tmp_path / "demo"


# init and round trip ----------------------------------------------------------------------------


def test_init_creates_a_valid_project(project_dir):
    assert (project_dir / "murakumo.yaml").is_file()
    assert (project_dir / "custom" / "README.md").is_file()
    assert (project_dir / ENTITIES_DIR / "nyc_tlc" / "yellow_tripdata.yaml").is_file()
    assert (project_dir / ENTITIES_DIR / "marts" / "fct_trip.yaml").is_file()
    assert load_and_validate(project_dir) == []  # init writes ids, so not even MK005


def test_init_refuses_an_existing_project(project_dir):
    with pytest.raises(ProjectExistsError):
        init_project(project_dir)


def test_init_empty_project(tmp_path):
    project = init_project(tmp_path / "blank", example=False)
    assert project.manifest.project.name == "blank"
    assert load_and_validate(tmp_path / "blank") == []


def test_save_and_reload_is_stable(project_dir):
    first = load_project(project_dir).project
    before = {p: p.read_text() for p in project_dir.rglob("*.yaml")}
    save_model(first)
    after = {p: p.read_text() for p in project_dir.rglob("*.yaml")}
    assert before == after

    second = load_project(project_dir).project
    assert [to_data(e) for e in first.model.entities] == [to_data(e) for e in second.model.entities]


def test_ids_survive_a_round_trip(project_dir):
    first = load_project(project_dir).project
    second = load_project(project_dir).project
    assert [e.id for e in first.model.entities] == [e.id for e in second.model.entities]
    assert first.model.entities[0].attributes[0].id == second.model.entities[0].attributes[0].id


def test_saving_keeps_the_users_file_location(project_dir):
    original = project_dir / ENTITIES_DIR / "marts" / "fct_trip.yaml"
    moved = project_dir / ENTITIES_DIR / "finance" / "trips" / "fct_trip.yaml"
    moved.parent.mkdir(parents=True)
    original.rename(moved)
    project = load_project(project_dir).project
    save_model(project)
    assert moved.is_file() and not original.exists()


def test_prune_removes_deleted_entities(project_dir):
    project = load_project(project_dir).project
    project.model.entities = [e for e in project.model.entities if e.name != "dim_taxi_zone"]
    project.model.relationships = [r for r in project.model.relationships if r.to != "dim_taxi_zone"]
    save_model(project, prune=True)
    assert not (project_dir / ENTITIES_DIR / "marts" / "dim_taxi_zone.yaml").exists()


# loading errors ---------------------------------------------------------------------------------


def test_missing_manifest(tmp_path):
    result = load_project(tmp_path)
    assert result.project is None
    assert codes(result.diagnostics) == ["MK000"]


def test_yaml_syntax_error_reports_line(project_dir):
    bad = project_dir / ENTITIES_DIR / "broken.yaml"
    write(bad, "name: broken\nattributes: [\n")
    result = load_project(project_dir)
    [diag] = [d for d in result.diagnostics if d.code == "MK001"]
    assert diag.file == bad and "line" in diag.where


def test_unknown_key_and_bad_type_are_reported_with_names(project_dir):
    write(
        project_dir / ENTITIES_DIR / "orders.yaml",
        {"name": "orders", "colour": "red", "attributes": [{"name": "amount", "type": "varchar"}]},
    )
    result = load_project(project_dir)
    problems = {d.where: d.message for d in result.diagnostics if d.code == "MK002"}
    assert "colour" in problems
    assert "unknown logical type" in problems["attributes[amount].type"]


def test_one_bad_file_does_not_hide_the_others(project_dir):
    write(project_dir / ENTITIES_DIR / "bad.yaml", {"name": "bad", "layer": "gold"})
    result = load_project(project_dir)
    assert len(result.project.model.entities) == 4
    assert codes(result.diagnostics) == ["MK002"]


def test_manifest_from_the_brief_is_valid():
    text = (Path(__file__).parent / "fixtures" / "brief_manifest.yaml").read_text()
    manifest = Manifest.model_validate(yaml.safe_load(text))
    assert manifest.targets.warehouse.type == "snowflake"
    assert manifest.targets.lakehouse.table_format == "iceberg"
    assert [d.name for d in manifest.project.domains] == ["sales", "finance", "product"]
    assert manifest.sources[1].from_ == "introspect:env:CRM_DB_URL"


def test_manifest_typo_is_an_error(project_dir):
    manifest = project_dir / "murakumo.yaml"
    manifest.write_text(manifest.read_text().replace("orchestrator: airflow", "orchestartor: airflow"))
    result = load_project(project_dir)
    assert result.project is None
    assert any(d.where == "generation.orchestartor" for d in result.diagnostics)


# model rules ------------------------------------------------------------------------------------


def test_reference_errors(project_dir):
    write(
        project_dir / ENTITIES_DIR / "orders.yaml",
        {
            "name": "orders",
            "source": "erp",
            "domain": "sales",
            "grain": ["order_id"],
            "partition_by": ["month(ordered_at)"],
            "attributes": [{"name": "id", "type": "integer", "key": "primary", "nullable": False}],
            "mappings": [{"target": "id", "sources": ["customers.id", "yellow_tripdata.nope"]}],
        },
    )
    rels = yaml.safe_load((project_dir / RELATIONSHIPS_FILE).read_text())
    rels["relationships"].append({"from": "orders", "to": "ghost", "keys": [{"from": "id", "to": "id"}]})
    rels["relationships"].append(
        {"from": "orders", "to": "taxi_zone_lookup", "keys": [{"from": "x", "to": "LocationID"}]}
    )
    write(project_dir / RELATIONSHIPS_FILE, rels)

    found = codes(d for d in load_and_validate(project_dir) if d.level is Level.ERROR)
    assert found == ["MK201", "MK202", "MK203", "MK203", "MK204", "MK205", "MK207", "MK207"]


def test_duplicate_names_and_ids(project_dir):
    original = project_dir / ENTITIES_DIR / "marts" / "fct_trip.yaml"
    write(project_dir / ENTITIES_DIR / "copy" / "fct_trip.yaml", original.read_text())
    found = codes(load_and_validate(project_dir))
    assert "MK101" in found and "MK103" in found


def test_modelling_warnings(project_dir):
    write(
        project_dir / ENTITIES_DIR / "marts" / "fct_sales.yaml",
        {
            "name": "fct_sales",
            "layer": "marts",
            "kind": "fact",
            "load": "incremental",
            "attributes": [
                {"name": "OrderId", "type": "string", "key": "primary"},
                {"name": "email", "type": "string", "pii": True},
                {"name": "zone", "type": "integer"},
            ],
        },
    )
    write(project_dir / ENTITIES_DIR / "marts" / "dim_empty.yaml", {"name": "dim_empty", "kind": "dimension"})
    rels = yaml.safe_load((project_dir / RELATIONSHIPS_FILE).read_text())
    rels["relationships"].append(
        {"from": "fct_sales", "to": "dim_taxi_zone", "keys": [{"from": "zone", "to": "borough"}]}
    )
    write(project_dir / RELATIONSHIPS_FILE, rels)

    diagnostics = load_and_validate(project_dir)
    assert all(d.level is Level.WARNING for d in diagnostics)
    assert codes(diagnostics) == ["MK301", "MK302", "MK303", "MK304", "MK305", "MK306", "MK307", "MK308", "MK311"]


def test_raw_layer_keeps_source_names(project_dir):
    # yellow_tripdata has VendorID and PULocationID: raw tables mirror the source, so no snake_case warning.
    assert not [d for d in load_and_validate(project_dir) if d.code == "MK307"]


# CLI --------------------------------------------------------------------------------------------


def test_cli_init_and_validate(tmp_path):
    target = tmp_path / "cli_demo"
    result = runner.invoke(app, ["init", str(target)])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["validate", str(target / "model")])  # any folder inside the project works
    assert result.exit_code == 0, result.output
    assert "valid" in result.output


def test_cli_validate_fails_on_errors(project_dir):
    write(project_dir / ENTITIES_DIR / "orders.yaml", {"name": "orders", "source": "erp", "attributes": []})
    result = runner.invoke(app, ["validate", str(project_dir), "--format", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert {"MK201", "MK305"} <= {d["code"] for d in payload["diagnostics"]}
    [missing_source] = [d for d in payload["diagnostics"] if d["code"] == "MK201"]
    assert missing_source["file"] == "model/entities/orders.yaml"


def test_cli_strict_fails_on_warnings(project_dir):
    write(project_dir / ENTITIES_DIR / "lonely.yaml", {"name": "lonely"})
    assert runner.invoke(app, ["validate", str(project_dir)]).exit_code == 0
    assert runner.invoke(app, ["validate", str(project_dir), "--strict"]).exit_code == 1


def test_cli_fix_writes_missing_ids(project_dir):
    path = project_dir / ENTITIES_DIR / "orders.yaml"
    write(path, {"name": "orders", "attributes": [{"name": "order_id", "type": "bigint"}]})
    result = runner.invoke(app, ["validate", str(project_dir)])
    assert "MK005" in result.output

    result = runner.invoke(app, ["validate", str(project_dir), "--fix"])
    assert result.exit_code == 0, result.output
    saved = yaml.safe_load(path.read_text())
    assert "id" in saved and "id" in saved["attributes"][0]
    assert "MK005" not in runner.invoke(app, ["validate", str(project_dir)]).output


def test_cli_fix_refuses_when_files_are_broken(project_dir):
    write(project_dir / ENTITIES_DIR / "orders.yaml", {"name": "orders"})
    write(project_dir / ENTITIES_DIR / "broken.yaml", "name: [")
    before = (project_dir / ENTITIES_DIR / "orders.yaml").read_text()
    runner.invoke(app, ["validate", str(project_dir), "--fix"])
    assert (project_dir / ENTITIES_DIR / "orders.yaml").read_text() == before


def test_cli_schema_prints_json_schema():
    result = runner.invoke(app, ["schema", "entity"])
    assert result.exit_code == 0
    schema = json.loads(result.output)
    assert "attributes" in schema["properties"]


def test_dump_yaml_uses_literal_blocks_for_multiline_text():
    assert "|" in dump_yaml({"expression": "select 1\nfrom t"})
