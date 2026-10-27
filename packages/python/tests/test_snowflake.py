"""Snowflake, for real. Runs when SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER and a login are set:

    SNOWFLAKE_ACCOUNT=ORG-ACCT SNOWFLAKE_USER=MURAKUMO_DBT \
    SNOWFLAKE_PRIVATE_KEY_PATH=~/.snowflake/murakumo_rsa_key.p8 SNOWFLAKE_DATABASE=COMMERCE \
    uv run pytest tests/test_snowflake.py

Everything is created in schemas with a unique prefix and dropped afterwards.
"""

import os
import uuid

import pytest

from murakumo.generate.verify import verify
from murakumo.importers.database import introspect
from murakumo.scaffold import init_project

LOGIN = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH") or os.environ.get("SNOWFLAKE_PASSWORD")
pytestmark = pytest.mark.skipif(
    not (os.environ.get("SNOWFLAKE_ACCOUNT") and os.environ.get("SNOWFLAKE_USER") and LOGIN),
    reason="set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER and SNOWFLAKE_PRIVATE_KEY_PATH to run Snowflake tests",
)
DATABASE = os.environ.get("SNOWFLAKE_DATABASE", "COMMERCE")


@pytest.fixture(scope="module")
def probe():
    from murakumo.importers import snowflake

    schema = f"MURAKUMO_TEST_{uuid.uuid4().hex[:8].upper()}"
    connection = snowflake.connect(f"snowflake:///{DATABASE}")
    cursor = connection.cursor()
    cursor.execute(f"create schema {schema}")
    cursor.execute(
        f"create table {schema}.products (sku varchar(20), region varchar(2), price number(12,2),"
        " primary key (sku, region)) comment = 'Things we sell'"
    )
    cursor.execute(
        f"""create table {schema}.orders (id number(38,0) primary key, "CustomerName" varchar comment 'Who ordered',"""
        f" sku varchar(20), region varchar(2), placed_at timestamp_tz, extra variant, tags array,"
        f" foreign key (sku, region) references {schema}.products (sku, region))"
    )
    yield schema
    cursor.execute(f"drop schema if exists {schema} cascade")
    connection.close()


def test_snowflake_introspection(probe):
    result = introspect(f"snowflake:///{DATABASE}", "shop", schemas=[probe.lower()])
    tables = {e.name: e for e in result.entities}
    assert set(tables) == {"products", "orders"}
    products, orders = tables["products"], tables["orders"]
    assert products.description == "Things we sell"
    assert {a.name for a in products.primary_key} == {"sku", "region"}
    assert products.attribute("price").type == "decimal(12,2)"
    assert products.attribute("region").type == "string(2)"
    assert orders.attribute("id").type == "bigint" and not orders.attribute("id").nullable
    customer = orders.attribute("CustomerName")  # created quoted, so it keeps its case
    assert customer is not None and customer.description == "Who ordered"
    assert orders.attribute("placed_at").type == "timestamp_tz"
    assert orders.attribute("extra").type == "json" and orders.attribute("tags").type == "array<json>"
    [composite] = result.relationships
    assert {(k.from_, k.to) for k in composite.keys} == {("sku", "sku"), ("region", "region")}


def test_verify_on_snowflake(tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_DATABASE", DATABASE)
    result = verify(init_project(tmp_path / "taxi"), rows=30, warehouse="snowflake")
    assert result.ok, [(n.name, n.status, n.message) for n in result.nodes if n.status not in ("success", "pass")]
    assert result.rerun is True and result.count("model") == 4
