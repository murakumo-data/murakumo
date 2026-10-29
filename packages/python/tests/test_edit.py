import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from murakumo import edit
from murakumo.cli import app
from murakumo.diagnostics import Level
from murakumo.ir.model import KeyRole, Layer
from murakumo.project import ENTITIES_DIR, load_project, save_model
from murakumo.scaffold import init_project
from murakumo.validate import validate_project

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


# changing and renaming --------------------------------------------------------------------------

COMMERCE = Path(__file__).resolve().parents[3] / "examples" / "commerce"


@pytest.fixture
def commerce(tmp_path):
    shutil.copytree(COMMERCE / "model", tmp_path / "commerce" / "model")
    shutil.copy(COMMERCE / "murakumo.yaml", tmp_path / "commerce")
    return load_project(tmp_path / "commerce").project


def errors(project):
    return [d for d in validate_project(project) if d.level is Level.ERROR]


def test_update_entity_and_column(project):
    edit.update_entity(project, "fct_trip", {"description": "One row per trip", "domain": "mobility"})
    column = edit.update_column(
        project, "fct_trip", "trip_distance_miles", {"type": "decimal(12,3)", "nullable": False}
    )
    assert project.model.entity("fct_trip").domain == "mobility"
    assert column.type == "decimal(12,3)" and not column.nullable
    # A bad value changes nothing.
    with pytest.raises(edit.EditError, match="unknown logical type"):
        edit.update_column(project, "fct_trip", "trip_distance_miles", {"type": "geometry", "nullable": True})
    assert project.model.entity("fct_trip").attribute("trip_distance_miles").nullable is False
    with pytest.raises(edit.EditError, match="cannot change id here"):
        edit.update_entity(project, "fct_trip", {"id": "nope"})
    with pytest.raises(edit.EditError, match="no column 'nope'"):
        edit.update_entity(project, "fct_trip", {"watermark": "nope"})


def test_primary_key_implies_not_null(project):
    entity = edit.add_entity(project, "contacts")
    edit.add_column(project, "contacts", "contact_id", "bigint")
    edit.update_column(project, "contacts", "contact_id", {"key": "primary"})
    assert entity.attribute("contact_id").key is KeyRole.PRIMARY
    assert entity.attribute("contact_id").nullable is False


def test_rename_entity_follows_references(commerce):
    original = commerce.origins[commerce.model.entity("lineitem").id]
    entity = edit.rename_entity(commerce, "lineitem", "line_item")
    model = commerce.model
    assert {r.from_ for r in model.relationships if r.name and r.name.startswith("lineitem__")} == {"line_item"}
    fact = model.entity("fct_order_line")
    assert all(not s.startswith("lineitem.") for m in fact.mappings for s in m.sources)
    assert any(s == "line_item.l_discount" for m in fact.mappings for s in m.sources)
    assert commerce.origins[entity.id] == original.with_name("line_item.yaml")
    assert errors(commerce) == []

    save_model(commerce, prune=True)
    assert not original.exists() and original.with_name("line_item.yaml").exists()
    assert load_project(commerce.root).project.model.entity("line_item").id == entity.id


def test_rename_column_follows_references(commerce):
    edit.rename_column(commerce, "lineitem", "l_discount", "discount")
    edit.rename_column(commerce, "orders", "o_orderkey", "order_key")
    model = commerce.model
    net = next(m for m in model.entity("fct_order_line").mappings if m.expression)
    assert "lineitem.discount" in net.sources
    assert net.expression == "l_extendedprice * (1 - discount)"
    keys = [(r.from_, k.from_, r.to, k.to) for r in model.relationships for k in r.keys]
    assert ("lineitem", "l_orderkey", "orders", "order_key") in keys
    assert errors(commerce) == []
    # The renamed project still generates, with no errors.
    from murakumo.generate import generate

    output = generate(commerce, "duckdb")
    assert [d for d in output.diagnostics if d.level is Level.ERROR] == []
    sql = next(text for path, text in output.files.items() if path.endswith("fct_order_line.sql"))
    assert "discount" in sql and "l_discount" not in sql


def test_rename_errors(project):
    with pytest.raises(edit.EditError, match="already exists"):
        edit.rename_entity(project, "fct_trip", "dim_taxi_zone")
    with pytest.raises(edit.EditError, match="not a valid name"):
        edit.rename_entity(project, "fct_trip", "fct trip")
    with pytest.raises(edit.EditError, match="already has a column"):
        edit.rename_column(project, "fct_trip", "trip_distance_miles", "total_amount")


def test_update_and_remove_relationship(project):
    rel = project.model.relationships[0]
    updated = edit.update_relationship(project, rel.id, {"optional": True, "role": "pickup_zone"})
    assert updated.id == rel.id and updated.optional and updated.role == "pickup_zone"
    with pytest.raises(edit.EditError, match="has no column"):
        edit.update_relationship(project, rel.id, {"keys": [{"from": "nope", "to": "zone_id"}]})
    edit.remove_relationship(project, rel.id)
    assert all(r.id != rel.id for r in project.model.relationships)
    with pytest.raises(edit.EditError, match="does not exist"):
        edit.remove_relationship(project, rel.id)


def test_cli_rename(project):
    assert run(project, "rename", "entity", "dim_taxi_zone", "dim_zone").exit_code == 0
    result = run(project, "rename", "column", "dim_zone", "zone_id", "taxi_zone_id")
    assert result.exit_code == 0, result.output
    loaded = load_project(project.root).project
    assert loaded.model.entity("dim_zone").attribute("taxi_zone_id")
    assert all(k.to == "taxi_zone_id" for r in loaded.model.relationships if r.to == "dim_zone" for k in r.keys)
    assert not (project.root / ENTITIES_DIR / "marts" / "dim_taxi_zone.yaml").exists()


# mappings ---------------------------------------------------------------------------------------


def test_set_mapping(commerce):
    fact = commerce.model.entity("fct_order_line")
    order = [m.target for m in fact.mappings]
    edit.set_mapping(commerce, "fct_order_line", "quantity", ["lineitem.l_quantity"], "l_quantity * 2")
    assert [m.target for m in fact.mappings] == order  # replaced where it was
    assert fact.mappings[order.index("quantity")].expression == "l_quantity * 2"

    edit.set_mapping(commerce, "fct_order_line", "quantity", [], None)
    assert "quantity" not in [m.target for m in fact.mappings]
    edit.set_mapping(commerce, "fct_order_line", "quantity", ["lineitem.l_quantity"])
    assert [m.target for m in fact.mappings] == order  # a new one goes in column order
    assert errors(commerce) == []


@pytest.mark.parametrize(
    ("target", "sources", "expression", "message"),
    [
        ("nope", ["lineitem.l_quantity"], None, "has no column 'nope'"),
        ("quantity", ["l_quantity"], None, "must be entity.column"),
        ("quantity", ["lineitem.nope"], None, "lineitem has no column 'nope'"),
        ("quantity", ["lineitem.l_quantity"], "l_quantity *", "cannot read the expression"),
    ],
)
def test_set_mapping_errors(commerce, target, sources, expression, message):
    with pytest.raises(edit.EditError, match=message):
        edit.set_mapping(commerce, "fct_order_line", target, sources, expression)


def test_move_and_order_columns(project):
    entity = edit.move_column(project, "dim_taxi_zone", "zone_name", after="zone_id")
    assert [a.name for a in entity.attributes] == ["zone_id", "zone_name", "borough", "service_zone"]
    assert [m.target for m in entity.mappings] == ["zone_id", "zone_name", "borough", "service_zone"]
    entity = edit.move_column(project, "dim_taxi_zone", "service_zone")
    assert entity.attributes[0].name == "service_zone"
    entity = edit.move_column(project, "dim_taxi_zone", "service_zone", before="borough")
    assert [a.name for a in entity.attributes] == ["zone_id", "zone_name", "service_zone", "borough"]
    with pytest.raises(edit.EditError, match="missing borough"):
        edit.order_columns(project, "dim_taxi_zone", ["zone_id", "zone_name", "service_zone"])
    with pytest.raises(edit.EditError, match="next to itself"):
        edit.move_column(project, "dim_taxi_zone", "borough", after="borough")


def test_cli_move_column(project):
    result = run(project, "move", "column", "taxi_zone_lookup", "Zone", "--after", "LocationID")
    assert result.exit_code == 0, result.output
    entity = load_project(project.root).project.model.entity("taxi_zone_lookup")
    assert [a.name for a in entity.attributes][:2] == ["LocationID", "Zone"]


def test_domains(project):
    manifest = project.root / "murakumo.yaml"
    edit.add_domain(project, "finance", owner="Finance team")
    text = manifest.read_text()
    assert "- name: finance\n      owner: Finance team" in text and "# kimball | datavault" in text
    edit.update_domain(project, "mobility", {"name": "transport", "owner": "Ops"})
    assert {e.domain for e in project.model.entities} == {"transport"}
    save_model(project)
    reloaded = load_project(project.root).project
    assert [d.name for d in reloaded.manifest.project.domains] == ["transport", "finance"]
    assert validate_project(reloaded) == [] or all(d.level is not Level.ERROR for d in validate_project(reloaded))
    with pytest.raises(edit.EditError, match="used by"):
        edit.remove_domain(reloaded, "transport")
    edit.remove_domain(reloaded, "finance")
    assert "finance" not in manifest.read_text()


def test_cli_domains(project):
    assert run(project, "add", "domain", "finance").exit_code == 0
    result = run(project, "rename", "domain", "mobility", "transport")
    assert result.exit_code == 0 and "4 entities" in result.output
    assert "domains: [transport, finance]" in (project.root / "murakumo.yaml").read_text()


def test_calculated_column(project):
    column = edit.add_calculated_column(
        project,
        "fct_trip",
        "cost_per_mile",
        "yellow_tripdata.total_amount / nullif(yellow_tripdata.trip_distance, 0)",
    )
    assert column.type.startswith("decimal") or column.type in ("double", "money")
    mapping = next(m for m in project.model.entity("fct_trip").mappings if m.target == "cost_per_mile")
    assert mapping.sources == ["yellow_tripdata.total_amount", "yellow_tripdata.trip_distance"]
    with pytest.raises(edit.EditError, match="not its own columns"):
        edit.add_calculated_column(project, "fct_trip", "z", "fct_trip.total_amount * 2")
    with pytest.raises(edit.EditError, match="raw table"):
        edit.add_calculated_column(project, "yellow_tripdata", "x", "1 + 1")
    with pytest.raises(edit.EditError, match="has no column"):
        edit.add_calculated_column(project, "fct_trip", "y", "fct_trip.nope + 1")
