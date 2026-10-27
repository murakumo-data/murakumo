import pytest
from typer.testing import CliRunner

from murakumo import edit
from murakumo.cli import app
from murakumo.ir.model import KeyRole, Layer
from murakumo.project import ENTITIES_DIR, load_project
from murakumo.scaffold import init_project

runner = CliRunner()


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path / "proj")  # the NYC taxi example


def run(project, *args):
    return runner.invoke(app, [*args, "-p", str(project.root)])


def test_add_entity_and_columns(project):
    edit.add_source(project, "crm")
    entity = edit.add_entity(project, "contacts", source="crm")
    edit.add_column(project, "contacts", "contact_id", "bigint", primary_key=True)
    edit.add_column(project, "contacts", "email", "string", pii=True)
    assert entity.attribute("contact_id").key is KeyRole.PRIMARY
    assert entity.attribute("contact_id").nullable is False
    assert entity.attribute("email").nullable is True


def test_edit_errors(project):
    with pytest.raises(edit.EditError, match="already exists"):
        edit.add_entity(project, "fct_trip")
    with pytest.raises(edit.EditError, match="does not exist"):
        edit.add_entity(project, "contacts", source="crm")
    with pytest.raises(edit.EditError, match="unknown logical type"):
        edit.add_column(project, "fct_trip", "shape", "geometry")
    with pytest.raises(edit.EditError, match="no column"):
        edit.add_relationship(project, "fct_trip", "dim_taxi_zone", [("nope", "zone_id")])
    with pytest.raises(edit.EditError, match="from_column=to_column"):
        edit.parse_join("zone_id")


def test_remove_entity_takes_its_relationships(project):
    removed = edit.remove_entity(project, "dim_taxi_zone")
    assert len(removed) == 2
    assert all("dim_taxi_zone" not in (r.from_, r.to) for r in project.model.relationships)


def test_cli_add_and_remove(project):
    assert run(project, "add", "entity", "dim_vendor", "--layer", "marts", "--kind", "dimension").exit_code == 0
    assert run(project, "add", "column", "dim_vendor", "vendor_id", "integer", "--pk").exit_code == 0
    result = run(project, "add", "relationship", "fct_trip", "dim_vendor", "--on", "pickup_zone_id=vendor_id")
    assert result.exit_code == 0, result.output

    loaded = load_project(project.root).project
    vendor = loaded.model.entity("dim_vendor")
    assert vendor.layer is Layer.MARTS and vendor.primary_key[0].name == "vendor_id"
    assert any(r.to == "dim_vendor" for r in loaded.model.relationships)

    result = run(project, "remove", "entity", "dim_vendor")
    assert result.exit_code == 0 and "1 relationship" in result.output
    assert not (project.root / ENTITIES_DIR / "marts" / "dim_vendor.yaml").exists()


def test_cli_bad_type_is_a_clear_error(project):
    result = run(project, "add", "column", "fct_trip", "x", "varchar")
    assert result.exit_code == 1
    assert "unknown logical type" in result.output


def test_cli_remove_column_shows_what_breaks(project):
    result = run(project, "remove", "column", "dim_taxi_zone", "zone_id")
    assert result.exit_code == 0
    assert "MK205" in result.output  # the relationships that used it are now reported
