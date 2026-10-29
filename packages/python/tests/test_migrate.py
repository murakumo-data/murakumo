import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from murakumo import edit
from murakumo.cli import app
from murakumo.migrate import SCHEMA_FILE, folder, load_state, plan_migration, write_migration
from murakumo.project import load_project, save_model
from murakumo.scaffold import init_project

duckdb = pytest.importorskip("duckdb")

runner = CliRunner()
COMMERCE = Path(__file__).resolve().parents[3] / "examples" / "commerce"


@pytest.fixture
def taxi(tmp_path):
    return init_project(tmp_path / "taxi")


@pytest.fixture
def commerce(tmp_path):
    shutil.copytree(COMMERCE / "model", tmp_path / "commerce" / "model")
    shutil.copy(COMMERCE / "murakumo.yaml", tmp_path / "commerce")
    return load_project(tmp_path / "commerce").project


def migrate(project, name=None):
    return write_migration(project, plan_migration(project), name)


def statements(pending):
    return [line for change in pending.changes for line in change.sql]


def apply(connection, path):
    connection.execute(path.read_text())


def test_baseline_creates_schemas_and_landing_tables(taxi):
    pending = plan_migration(taxi)
    assert pending.baseline and pending.number == 1 and pending.name == "initial"
    path = migrate(taxi)
    assert path.name == "0001_initial.sql"
    connection = duckdb.connect()
    apply(connection, path)
    apply(connection, path)  # safe to run again
    columns = connection.execute(
        "select column_name from information_schema.columns where table_name = 'yellow_tripdata'"
    ).fetchall()
    assert ("_ingested_at",) in columns
    state = json.loads((folder(taxi.root, "duckdb") / SCHEMA_FILE).read_text())
    assert state["migration"] == 1
    assert {t["name"]: t["kind"] for t in state["tables"]}["fct_trip"] == "incremental"
    assert plan_migration(taxi).changes == []  # nothing new


def test_renames_keep_data(taxi):
    connection = duckdb.connect()
    apply(connection, migrate(taxi))
    connection.execute("insert into raw_nyc_tlc.yellow_tripdata (trip_distance, total_amount) values (2.5, 12.0)")

    edit.rename_column(taxi, "yellow_tripdata", "trip_distance", "distance_miles")
    edit.rename_entity(taxi, "taxi_zone_lookup", "zones")
    edit.add_column(taxi, "yellow_tripdata", "tip_amount", "decimal(10,2)")
    edit.remove_column(taxi, "yellow_tripdata", "VendorID")
    edit.update_column(taxi, "yellow_tripdata", "total_amount", {"type": "decimal(12,2)"})
    save_model(taxi, prune=True)

    pending = plan_migration(taxi)
    summaries = [c.summary for c in pending.changes]
    assert "yellow_tripdata: rename trip_distance to distance_miles" in summaries
    assert "rename raw_nyc_tlc.taxi_zone_lookup to zones" in summaries
    assert any(c.destructive and "VendorID" in c.summary for c in pending.changes)
    path = migrate(taxi)
    assert path.name.startswith("0002_")
    apply(connection, path)

    row = connection.execute("select distance_miles, total_amount from raw_nyc_tlc.yellow_tripdata").fetchone()
    assert [float(v) for v in row] == [2.5, 12.0]
    columns = {
        name: data_type
        for name, data_type in connection.execute(
            "select column_name, data_type from information_schema.columns where table_name = 'yellow_tripdata'"
        ).fetchall()
    }
    assert "tip_amount" in columns and columns["total_amount"] == "DECIMAL(12,2)"
    assert "VendorID" in columns  # dropping data is left to a person
    assert connection.execute("select count(*) from raw_nyc_tlc.zones").fetchone() == (0,)


def test_removed_entity_is_a_commented_drop(taxi):
    migrate(taxi)
    edit.remove_entity(taxi, "taxi_zone_lookup")
    lines = statements(plan_migration(taxi))
    assert "-- DROP TABLE raw_nyc_tlc.taxi_zone_lookup;" in lines
    assert not any(line.startswith("DROP") for line in lines)


def test_new_source_gets_its_schema(taxi):
    migrate(taxi)
    edit.add_source(taxi, "crm")
    edit.add_entity(taxi, "contacts", source="crm")
    edit.add_column(taxi, "contacts", "contact_id", "bigint")
    lines = statements(plan_migration(taxi))
    assert lines[0] == "CREATE SCHEMA IF NOT EXISTS raw_crm;"
    assert lines[1].startswith("CREATE TABLE IF NOT EXISTS raw_crm.contacts")


def test_incremental_and_snapshot_tables(commerce):
    migrate(commerce)
    kinds = {t.name: t.kind for t in load_state(commerce.root, "duckdb").values()}
    assert kinds["fct_order_line"] == "incremental"
    assert kinds["snp_dim_customer"] == "snapshot"

    edit.rename_column(commerce, "fct_order_line", "net_amount", "net_revenue")
    edit.add_column(commerce, "fct_order_line", "note", "string")
    edit.rename_column(commerce, "dim_customer", "phone", "phone_number")
    lines = statements(plan_migration(commerce))
    assert "ALTER TABLE IF EXISTS marts.fct_order_line RENAME COLUMN net_amount TO net_revenue;" in lines
    assert any(line.startswith("-- dbt adds it on its next run") and "note" in line for line in lines)
    assert any("snapshots." in line and "RENAME COLUMN phone TO phone_number" in line for line in lines)


@pytest.mark.parametrize(
    ("warehouse", "expected"),
    [
        ("snowflake", "ALTER TABLE raw_nyc_tlc.taxi_zone_lookup RENAME TO raw_nyc_tlc.zones;"),
        ("postgres", "ALTER TABLE raw_nyc_tlc.taxi_zone_lookup RENAME TO zones;"),
        ("fabric", "EXEC sp_rename 'raw_nyc_tlc.taxi_zone_lookup', 'zones';"),
    ],
)
def test_dialects(taxi, warehouse, expected):
    write_migration(taxi, plan_migration(taxi, warehouse))
    edit.rename_entity(taxi, "taxi_zone_lookup", "zones")
    edit.update_column(taxi, "zones", "Borough", {"type": "string(100)"})
    lines = statements(plan_migration(taxi, warehouse))
    assert expected in lines
    retype = next(line for line in lines if "ALTER COLUMN" in line)
    assert {"snowflake": "SET DATA TYPE", "postgres": " TYPE ", "fabric": "ALTER COLUMN"}[warehouse] in retype


def test_cli(taxi):
    root = str(taxi.root)
    result = runner.invoke(app, ["migrate", "--check", "-p", root])
    assert result.exit_code == 0 and "nothing to check" in result.output
    assert runner.invoke(app, ["migrate", "-p", root]).exit_code == 0
    assert runner.invoke(app, ["migrate", "--check", "-p", root]).exit_code == 0

    assert (
        runner.invoke(app, ["rename", "column", "yellow_tripdata", "trip_distance", "miles", "-p", root]).exit_code == 0
    )
    result = runner.invoke(app, ["migrate", "--check", "-p", root])
    assert result.exit_code == 1 and "rename trip_distance to miles" in result.output
    result = runner.invoke(app, ["migrate", "--dry-run", "-p", root])
    assert "RENAME COLUMN trip_distance TO miles" in result.output
    assert not list(folder(taxi.root, "duckdb").glob("0002_*"))
    result = runner.invoke(app, ["migrate", "--name", "Rename trip distance", "-p", root])
    assert result.exit_code == 0, result.output
    assert (folder(taxi.root, "duckdb") / "0002_rename_trip_distance.sql").is_file()


def test_commerce_example_is_migrated():
    """The example's migrations match its model, as `murakumo migrate --check` would say in CI."""
    project = load_project(COMMERCE).project
    assert plan_migration(project).changes == []


def test_dbt_built_tables_are_safe_on_a_new_environment(commerce, tmp_path):
    """dbt has not built marts or snapshots in a new environment, so their migrations skip them."""
    migrate(commerce)
    edit.rename_column(commerce, "fct_order_line", "net_amount", "net_revenue")
    edit.rename_column(commerce, "lineitem", "l_discount", "discount")
    path = migrate(commerce)
    connection = duckdb.connect()
    apply(connection, folder(commerce.root, "duckdb") / "0001_initial.sql")
    apply(connection, path)  # marts.fct_order_line does not exist yet: skipped, no error
    columns = connection.execute("select column_name from information_schema.columns where table_name = 'lineitem'")
    assert ("discount",) in columns.fetchall()


def test_warehouses_without_if_exists_get_a_note(commerce):
    write_migration(commerce, plan_migration(commerce, "databricks"))
    edit.rename_column(commerce, "fct_order_line", "net_amount", "net_revenue")
    lines = statements(plan_migration(commerce, "databricks"))
    index = lines.index("ALTER TABLE marts.fct_order_line RENAME COLUMN net_amount TO net_revenue;")
    assert lines[index - 1].startswith("-- Only where dbt has already built fct_order_line")


def test_column_order_is_a_note_not_a_change(taxi):
    migrate(taxi)
    edit.move_column(taxi, "taxi_zone_lookup", "Zone")
    pending = plan_migration(taxi)
    assert pending.changes == []
    assert len(pending.notes) == 1 and "taxi_zone_lookup: new column order" in pending.notes[0]


def test_scd1_to_scd2_says_history_starts(taxi):
    migrate(taxi)
    edit.update_entity(taxi, "dim_taxi_zone", {"history": "scd2"})
    pending = plan_migration(taxi)
    assert [c.summary for c in pending.changes] == [
        "dim_taxi_zone starts keeping history (SCD2) in snapshots.snp_dim_taxi_zone"
    ]
    assert "first version" in "\n".join(pending.changes[0].sql)
