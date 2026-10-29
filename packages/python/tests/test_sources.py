import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from murakumo import sources
from murakumo.cli import app
from murakumo.importers.database import introspect
from murakumo.project import load_project
from murakumo.scaffold import init_project
from murakumo.server import create_app

duckdb = pytest.importorskip("duckdb")
runner = CliRunner()


@pytest.fixture
def shop_db(tmp_path):
    path = tmp_path / "shop.duckdb"
    connection = duckdb.connect(str(path))
    connection.execute(
        """
        create table customers (id bigint primary key, email varchar not null);
        create table orders (id bigint primary key, customer_id bigint references customers(id), total decimal(10,2));
        create table refunds (id bigint primary key, order_id bigint references orders(id));
        create schema archive;
        create table archive.orders (id bigint);
        """
    )
    connection.close()
    return path


@pytest.fixture
def project(tmp_path, shop_db, monkeypatch):
    monkeypatch.setenv("SHOP_DB_URL", f"duckdb:///{shop_db}")
    project = init_project(tmp_path / "p", example=False)
    sources.add_source(project, "shop", "database", "SHOP_DB_URL")
    return load_project(project.root).project


def test_add_source_writes_the_manifest(project):
    text = (project.root / "murakumo.yaml").read_text()
    assert "sources:\n  - name: shop\n    from: introspect:env:SHOP_DB_URL" in text
    assert "# kimball | datavault" in text  # comments elsewhere are kept
    with pytest.raises(sources.SourceError, match="environment variable"):
        sources.from_line("database", "postgresql://bob:secret@db/shop")
    assert sources.from_line("snowflake", "COMMERCE") == "introspect:snowflake:///COMMERCE"
    assert sources.from_line("file", "data/trips.parquet") == "file:data/trips.parquet"


def test_list_sources(project):
    [shop] = sources.list_sources(project)
    assert shop == {
        "name": "shop",
        "type": None,
        "kind": "database",
        "from": "introspect:env:SHOP_DB_URL",
        "declared": True,
        "tables": 0,
        "description": None,
        "updated": None,
    }


def test_browse_reads_the_catalogue(project):
    schemas = sources.browse_source(project, "shop")
    assert [s["name"] for s in schemas] == ["archive", "main"]
    main = schemas[1]
    assert [t["name"] for t in main["tables"]] == ["customers", "orders", "refunds"]
    orders = main["tables"][1]
    assert orders["columns"][0] == {"name": "id", "type": "BIGINT", "nullable": False, "primary_key": True}
    assert orders["in_model"] is False


def test_import_picked_tables(project):
    reports = sources.import_source(project, "shop", ["main.customers", "main.orders"])
    assert reports[0]["added"] == ["customers", "main__orders"]
    assert reports[0]["added_relationships"] == ["main__orders -> customers (customer_id = id)"]
    # A second, partial import leaves the first one's tables alone.
    reports = sources.import_source(project, "shop", ["refunds"])
    assert reports[0]["added"] == ["refunds"] and reports[0]["missing"] == []
    assert {e.name for e in project.model.entities} == {"customers", "main__orders", "refunds"}
    with pytest.raises(sources.SourceError, match="at least one"):
        sources.import_source(project, "shop", [])


def test_errors_hide_passwords(project, monkeypatch):
    monkeypatch.setenv("SHOP_DB_URL", "postgresql://bob:hunter2@127.0.0.1:1/shop")
    pytest.importorskip("psycopg")
    with pytest.raises(sources.SourceError) as error:
        sources.browse_source(project, "shop")
    assert "hunter2" not in str(error.value)


def test_introspect_table_names_do_not_depend_on_the_pick(shop_db):
    result = introspect(f"duckdb:///{shop_db}", "shop", tables=["main.orders"])
    assert [e.name for e in result.entities] == ["main__orders"] and result.partial


def test_cli_table_option(tmp_path, shop_db):
    project = init_project(tmp_path / "c", example=False)
    result = runner.invoke(
        app,
        [
            "import",
            "database",
            f"duckdb:///{shop_db}",
            "--source",
            "shop",
            "--table",
            "customers",
            "-p",
            str(project.root),
        ],
    )
    assert result.exit_code == 0, result.output
    assert [e.name for e in load_project(project.root).project.model.entities] == ["customers"]


def test_api(project):
    client = TestClient(create_app(project.root), base_url="http://localhost")
    state = client.get("/api/state").json()
    assert state["sources"][0]["name"] == "shop"
    browse = client.get("/api/sources/shop/browse").json()
    assert browse["schemas"][1]["tables"][0]["name"] == "customers"
    preview = client.post("/api/sources/shop/import/preview", json={"tables": ["customers"]}).json()
    assert preview["reports"][0]["added"] == ["customers"]
    assert client.get("/api/state").json()["model"]["entities"] == []  # a preview writes nothing
    result = client.post("/api/sources/shop/import", json={"tables": ["customers"]}).json()
    assert [e["name"] for e in result["model"]["entities"]] == ["customers"]
    assert result["reports"][0]["added"] == ["customers"]
    added = client.post("/api/sources", json={"name": "crm", "kind": "database", "location": "CRM_DB_URL"}).json()
    assert [s["name"] for s in added["sources"]] == ["crm", "shop"]
    bad = client.post("/api/sources", json={"name": "x", "kind": "database", "location": "postgresql://a:b@c/d"})
    assert bad.status_code == 400 and "b@c" not in bad.json()["message"]
    assert client.get("/api/sources/crm/browse").status_code == 400  # CRM_DB_URL is not set
