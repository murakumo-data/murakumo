import csv
import json
from pathlib import Path

import duckdb
import pytest
import yaml
from typer.testing import CliRunner

from murakumo.cli import app
from murakumo.importers import import_declared, merge
from murakumo.importers.api import import_json, import_openapi, records_at
from murakumo.importers.database import SourceUnavailable, introspect, redact
from murakumo.importers.dictionary import import_dictionary
from murakumo.importers.files import import_file, table_name
from murakumo.importers.typemap import json_schema_to_logical, sql_to_logical
from murakumo.ir.model import KeyRole, LoadPattern
from murakumo.manifest import SourceDeclaration
from murakumo.project import ENTITIES_DIR, load_project
from murakumo.scaffold import init_project
from murakumo.validate import validate_project

runner = CliRunner()
SAMPLES = Path(__file__).resolve().parents[3] / "examples" / "sample-dictionaries"
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path / "proj", example=False)


def reload(project):
    result = load_project(project.root)
    assert result.project is not None, result.diagnostics
    return result.project


def rows_of(name):
    with (SAMPLES / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# type mapping -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("source", "logical"),
    [
        ("VARCHAR", "string"),
        ("character varying(40)", "string(40)"),
        ("NUMBER(38,0)", "bigint"),
        ("NUMBER(5,0)", "integer"),
        ("NUMERIC(12,2)", "decimal(12,2)"),
        ("DECIMAL", "decimal(38,9)"),
        ("TIMESTAMP WITH TIME ZONE", "timestamp_tz"),
        ("TIMESTAMP_NTZ(9)", "timestamp(9)"),
        ("int4[]", "array<integer>"),
        ("ARRAY<STRING>", "array<string>"),
        ("STRUCT(x INTEGER)", "struct"),
        ("JSONB", "json"),
        ("bytea", "binary"),
    ],
)
def test_sql_types_map_to_logical_types(source, logical):
    assert sql_to_logical(source) == (logical, True)


def test_unknown_sql_type_falls_back_to_string():
    assert sql_to_logical("GEOGRAPHY") == ("string", False)


def test_json_schema_types():
    assert json_schema_to_logical({"type": "string", "format": "date-time"}) == "timestamp_tz"
    assert json_schema_to_logical({"type": ["integer", "null"], "format": "int32"}) == "integer"
    assert json_schema_to_logical({"type": "array", "items": {"type": "number"}}) == "array<double>"
    assert json_schema_to_logical({"type": "object"}) == "json"


# dictionaries -----------------------------------------------------------------------------------


def test_csv_dictionary_reads_every_field():
    [tlc] = import_dictionary(SAMPLES / "nyc_tlc.csv")
    trips = next(e for e in tlc.entities if e.name == "yellow_tripdata")
    assert tlc.source.name == "nyc_tlc"
    assert len(trips.attributes) == 19
    assert trips.load is LoadPattern.APPEND
    assert trips.properties == {"update_pattern": "monthly_file_append"}
    vendor = trips.attribute("VendorID")
    assert vendor.accepted_values["1"] == "Creative Mobile Technologies"
    fare = trips.attribute("fare_amount")
    assert (fare.unit, fare.classification.value, fare.type) == ("USD", "confidential", "decimal(10,2)")
    zones = next(e for e in tlc.entities if e.name == "taxi_zone_lookup")
    assert [a.name for a in zones.primary_key] == ["LocationID"]
    assert not zones.attribute("LocationID").nullable


def test_two_foreign_keys_to_one_table_stay_separate():
    [tlc] = import_dictionary(SAMPLES / "nyc_tlc.csv")
    assert sorted(r.keys[0].from_ for r in tlc.relationships) == ["DOLocationID", "PULocationID"]


def test_composite_foreign_key_to_a_composite_primary_key():
    [tpch] = import_dictionary(SAMPLES / "tpch_erp.csv")
    to_partsupp = [r for r in tpch.relationships if r.from_ == "lineitem" and r.to == "partsupp"]
    assert len(to_partsupp) == 1
    assert {(k.from_, k.to) for k in to_partsupp[0].keys} == {("l_partkey", "ps_partkey"), ("l_suppkey", "ps_suppkey")}
    assert len(tpch.relationships) == 10
    assert len({r.name for r in tpch.relationships}) == 10  # names are unique


def test_excel_workbook_holds_all_three_sources():
    imports = import_dictionary(SAMPLES / "sample_dictionaries.xlsx")
    assert sorted(i.source.name for i in imports) == ["nyc_tlc", "open_meteo", "tpch_erp"]
    assert sum(len(e.attributes) for i in imports for e in i.entities) == 109
    [one] = import_dictionary(SAMPLES / "sample_dictionaries.xlsx", sheet="open_meteo")
    assert one.source.name == "open_meteo"


def test_loose_headers_and_messy_rows(tmp_path):
    path = tmp_path / "dict.csv"
    path.write_text(
        "Table;Column;Data Type;PK;Description\n"
        "orders;order id;NUMBER(38,0);yes;Order number\n"
        "orders;amount;NUMERIC(12,2);;\n"
        ";missing_table;VARCHAR;;\n"
        "orders;shape;GEOGRAPHY;;\n"
    )
    [result] = import_dictionary(path, source="erp")
    [orders] = result.entities
    assert [a.name for a in orders.attributes] == ["order_id", "amount", "shape"]
    assert orders.attribute("order_id").type == "bigint" and orders.attribute("order_id").key is KeyRole.PRIMARY
    assert orders.attribute("amount").type == "decimal(12,2)"
    messages = " ".join(d.message for d in result.diagnostics)
    assert "renamed" in messages and "no table_name" in messages and "GEOGRAPHY" in messages


def test_import_merges_and_validates_clean(project):
    for imported in import_dictionary(SAMPLES / "sample_dictionaries.xlsx"):
        merge(project, imported)
    from murakumo.project import save_model

    save_model(project)
    errors = [d for d in validate_project(reload(project)) if d.level.value == "error"]
    assert errors == []
    assert (project.root / ENTITIES_DIR / "tpch_erp" / "lineitem.yaml").is_file()


# re-import --------------------------------------------------------------------------------------


def test_reimport_keeps_ids_and_hand_edits(project, tmp_path):
    from murakumo.project import save_model

    [first] = import_dictionary(SAMPLES / "nyc_tlc.csv")
    merge(project, first)
    save_model(project)

    # A person adds a test and a tag by hand.
    project = reload(project)
    trips = project.model.entity("yellow_tripdata")
    trips.tags = ["gold_source"]
    trips.attribute("trip_distance").tests = [{"type": "not_null"}]
    save_model(project)
    ids = {a.name: a.id for a in trips.attributes}

    rows = [r for r in rows_of("nyc_tlc.csv") if r["column_name"] != "store_and_fwd_flag"]
    for row in rows:
        if row["column_name"] == "trip_distance":
            row["logical_type"] = "decimal(12,3)"
    rows.append({**rows[0], "column_name": "cbd_congestion_fee", "ordinal": "20", "accepted_values": ""})
    changed = tmp_path / "changed.csv"
    write_rows(changed, rows)

    project = reload(project)
    [second] = import_dictionary(changed, source="nyc_tlc")
    report = merge(project, second)
    assert report.removed_columns == ["yellow_tripdata.store_and_fwd_flag"]
    assert any("trip_distance: type decimal(10,2) -> decimal(12,3)" in c for c in report.changed)
    assert any("new column cbd_congestion_fee" in c for c in report.changed)

    trips = project.model.entity("yellow_tripdata")
    assert trips.attribute("trip_distance").id == ids["trip_distance"]
    assert trips.tags == ["gold_source"]
    assert [t.type.value for t in trips.attribute("trip_distance").tests] == ["not_null"]


def test_reimport_without_changes_reports_nothing(project):
    from murakumo.project import save_model

    for imported in import_dictionary(SAMPLES / "tpch_erp.csv"):
        merge(project, imported)
    save_model(project)
    project = reload(project)
    [again] = import_dictionary(SAMPLES / "tpch_erp.csv")
    assert not merge(project, again).has_changes


def test_missing_tables_are_kept_unless_pruned(project, tmp_path):
    [full] = import_dictionary(SAMPLES / "nyc_tlc.csv")
    merge(project, full)
    rows = [r for r in rows_of("nyc_tlc.csv") if r["table_name"] == "taxi_zone_lookup"]
    write_rows(tmp_path / "zones.csv", rows)

    [zones_only] = import_dictionary(tmp_path / "zones.csv")
    report = merge(project, zones_only)
    assert report.missing == ["yellow_tripdata"] and project.model.entity("yellow_tripdata")

    [zones_only] = import_dictionary(tmp_path / "zones.csv")
    report = merge(project, zones_only, prune=True)
    assert report.removed == ["yellow_tripdata"] and project.model.entity("yellow_tripdata") is None
    assert project.model.relationships == []


def test_import_does_not_overwrite_another_sources_table(project, tmp_path):
    [tlc] = import_dictionary(SAMPLES / "nyc_tlc.csv")
    merge(project, tlc)
    [other] = import_dictionary(SAMPLES / "nyc_tlc.csv", source="other")
    report = merge(project, other)
    assert {d.code for d in report.diagnostics} == {"MK401"}


# databases --------------------------------------------------------------------------------------


@pytest.fixture
def shop_db(tmp_path):
    path = tmp_path / "shop.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        create table customers (id bigint primary key, email varchar not null, joined_at timestamptz);
        comment on table customers is 'People who buy things';
        comment on column customers.email is 'Login email';
        create table products (sku varchar, region varchar, price decimal(12,2), primary key (sku, region));
        create table orders (
            id bigint primary key,
            customer_id bigint references customers(id),
            sku varchar, region varchar,
            tags varchar[],
            foreign key (sku, region) references products(sku, region)
        );
        create schema archive;
        create table archive.orders (id bigint);
        """
    )
    connection.close()
    return path


def test_introspect_duckdb(shop_db):
    result = introspect(f"duckdb:///{shop_db}", "shop")
    names = {e.name for e in result.entities}
    assert names == {"customers", "products", "main__orders", "archive__orders"}
    customers = next(e for e in result.entities if e.name == "customers")
    assert customers.description == "People who buy things"
    assert customers.attribute("email").description == "Login email"
    assert not customers.attribute("email").nullable
    assert customers.attribute("joined_at").type == "timestamp_tz"
    assert [a.name for a in customers.primary_key] == ["id"]
    orders = next(e for e in result.entities if e.name == "main__orders")
    assert orders.attribute("tags").type == "array<string>"
    composite = next(r for r in result.relationships if r.to == "products")
    assert {(k.from_, k.to) for k in composite.keys} == {("sku", "sku"), ("region", "region")}
    assert any(r.to == "customers" for r in result.relationships)


def test_introspect_one_schema(shop_db):
    result = introspect(str(shop_db), "shop", schemas=["main"])
    assert {e.name for e in result.entities} == {"customers", "products", "orders"}


def test_introspect_errors(tmp_path, monkeypatch):
    with pytest.raises(SourceUnavailable, match="unsupported"):
        introspect("mysql://x", "s")
    monkeypatch.delenv("NO_SUCH_DB", raising=False)
    with pytest.raises(SourceUnavailable, match="NO_SUCH_DB"):
        introspect("env:NO_SUCH_DB", "s")


def test_passwords_never_reach_the_model():
    assert redact("postgresql://bob:hunter2@db:5432/shop?sslmode=require") == "postgresql://bob@db:5432/shop"
    assert redact("env:SHOP_DB") == "env:SHOP_DB"
    assert redact("duckdb:///data/shop.duckdb") == "duckdb:///data/shop.duckdb"


# files and APIs ---------------------------------------------------------------------------------


def test_parquet_file_schema(tmp_path):
    path = tmp_path / "yellow_tripdata_2024-01.parquet"
    duckdb.sql(
        "select 1::integer as VendorID, now()::timestamp as pickup, 2.5::double as distance, 'Y' as flag"
    ).write_parquet(str(path))
    result = import_file(str(path), "nyc_tlc")
    [entity] = result.entities
    assert entity.name == "yellow_tripdata"
    assert [(a.name, a.type) for a in entity.attributes] == [
        ("VendorID", "integer"),
        ("pickup", "timestamp"),
        ("distance", "double"),
        ("flag", "string"),
    ]
    assert result.partial


def test_file_names_lose_their_dates():
    assert table_name("s3://lake/trips_20240131.csv.gz") == "trips"
    assert table_name("https://x/y/green_tripdata_2023-12.parquet?sig=1") == "green_tripdata"
    assert table_name("lake/trips/*.parquet") == "trips"  # a glob is named after its folder


def test_openapi_schemas_become_entities():
    result = import_openapi(str(FIXTURES / "shop_openapi.yaml"), "shop")
    assert result.source.connection == "https://api.shop.example/v1"
    names = {e.name for e in result.entities}
    assert names == {"Address", "Customer", "VipCustomer"}  # the Status enum is not a table
    customer = next(e for e in result.entities if e.name == "Customer")
    types = {a.name: a.type for a in customer.attributes}
    assert types == {
        "id": "bigint",
        "email": "string",
        "created_at": "timestamp_tz",
        "status": "string",
        "address": "json",
        "tags": "array<string>",
    }
    assert not customer.attribute("id").nullable and customer.attribute("created_at").nullable
    assert customer.attribute("status").accepted_values == {"active": None, "closed": None}
    vip = next(e for e in result.entities if e.name == "VipCustomer")
    assert vip.attribute("tier").example == "gold" and vip.attribute("email") is not None


def test_columnar_json_like_open_meteo(tmp_path):
    sample = {
        "latitude": 40.78,
        "hourly": {
            "time": ["2024-01-01T00:00", "2024-01-01T01:00"],
            "temperature_2m": [4.3, None],
            "weather_code": [2, 3],
        },
    }
    path = tmp_path / "meteo.json"
    path.write_text(json.dumps(sample))
    [entity] = import_json(str(path), "open_meteo", "weather_hourly", path="hourly").entities
    assert [(a.name, a.type, a.nullable) for a in entity.attributes] == [
        ("time", "timestamp", False),
        ("temperature_2m", "double", True),
        ("weather_code", "bigint", False),
    ]
    assert entity.properties["records_path"] == "hourly"


def test_json_records_and_errors():
    assert records_at({"data": {"items": [{"a": 1}]}}, "data.items") == [{"a": 1}]
    assert records_at({"a": 1}, None) == [{"a": 1}]
    with pytest.raises(ValueError, match="no 'rows'"):
        records_at({"data": []}, "rows")


# manifest and CLI -------------------------------------------------------------------------------


def test_declared_sources(tmp_path, shop_db):
    declaration = SourceDeclaration.model_validate(
        {"name": "erp", "from": f"csv:{SAMPLES / 'tpch_erp.csv'}", "ingestion": {"tool": "airbyte"}}
    )
    [result] = import_declared(declaration, tmp_path)
    assert result.source.name == "erp" and result.source.load_tool == "airbyte"
    [db] = import_declared(SourceDeclaration(name="shop", from_=f"introspect:duckdb:///{shop_db}"), tmp_path)
    assert len(db.entities) == 4
    with pytest.raises(ValueError, match="start it with"):
        import_declared(SourceDeclaration(name="x", from_="ftp:thing"), tmp_path)


def test_cli_import_csv_then_validate(project):
    result = runner.invoke(app, ["import", "csv", str(SAMPLES / "nyc_tlc.csv"), "-p", str(project.root)])
    assert result.exit_code == 0, result.output
    assert "+ table yellow_tripdata" in result.output
    again = runner.invoke(app, ["import", "csv", str(SAMPLES / "nyc_tlc.csv"), "-p", str(project.root)])
    assert "already up to date" in again.output
    assert runner.invoke(app, ["validate", str(project.root)]).exit_code == 0


def test_cli_dry_run_writes_nothing(project):
    before = sorted(project.root.rglob("*.yaml"))
    result = runner.invoke(
        app, ["import", "excel", str(SAMPLES / "sample_dictionaries.xlsx"), "-p", str(project.root), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert sorted(project.root.rglob("*.yaml")) == before


def test_cli_import_declared_sources(project):
    manifest = project.root / "murakumo.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["sources"] = [{"name": "open_meteo", "from": f"excel:{SAMPLES / 'sample_dictionaries.xlsx'}"}]
    manifest.write_text(yaml.safe_dump(data))
    result = runner.invoke(app, ["import", "-p", str(project.root)])
    assert result.exit_code == 0, result.output
    assert "open_meteo" in result.output


def test_cli_import_refuses_a_broken_project(project):
    (project.root / ENTITIES_DIR).mkdir(parents=True)
    (project.root / ENTITIES_DIR / "broken.yaml").write_text("name: [")
    result = runner.invoke(app, ["import", "csv", str(SAMPLES / "nyc_tlc.csv"), "-p", str(project.root)])
    assert result.exit_code == 1
    assert "fix the errors" in result.output


def test_cli_import_database(project, shop_db):
    result = runner.invoke(
        app,
        ["import", "database", f"duckdb:///{shop_db}", "--source", "shop", "--schema", "main", "-p", str(project.root)],
    )
    assert result.exit_code == 0, result.output
    assert (project.root / ENTITIES_DIR / "shop" / "orders.yaml").is_file()
