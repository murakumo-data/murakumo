"""PostgreSQL introspection against a real server.

Runs when MURAKUMO_TEST_POSTGRES_URL points at a disposable database, as in CI:

    docker run -d --rm -e POSTGRES_PASSWORD=test -e POSTGRES_DB=shop -p 55432:5432 postgres:17
    MURAKUMO_TEST_POSTGRES_URL=postgresql://postgres:test@localhost:55432/shop uv run pytest tests/test_postgres.py
"""

import os

import pytest

from murakumo.importers.database import introspect

URL = os.environ.get("MURAKUMO_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set MURAKUMO_TEST_POSTGRES_URL to run PostgreSQL tests")


@pytest.fixture(scope="module")
def shop():
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(URL, autocommit=True) as connection:
        connection.execute(
            """
            drop schema if exists sales cascade;
            create schema sales;
            create table sales.customers (
                id bigserial primary key,
                email varchar(320) not null,
                joined_at timestamptz default now(),
                profile jsonb
            );
            comment on table sales.customers is 'People who buy things';
            comment on column sales.customers.email is 'Login email';
            create table sales.products (
                sku text, region char(2), price numeric(12,2), tags int[],
                primary key (sku, region)
            );
            create table sales.orders (
                id bigint primary key,
                customer_id bigint references sales.customers(id),
                sku text, region char(2),
                ordered_on date not null,
                foreign key (sku, region) references sales.products(sku, region)
            );
            """
        )
    yield
    with psycopg.connect(URL, autocommit=True) as connection:
        connection.execute("drop schema if exists sales cascade")


def test_postgres_tables_types_keys_and_comments(shop):
    result = introspect(URL, "shop", schemas=["sales"])
    by_name = {e.name: e for e in result.entities}
    assert set(by_name) == {"customers", "products", "orders"}

    customers = by_name["customers"]
    assert customers.description == "People who buy things"
    email = customers.attribute("email")
    assert (email.type, email.nullable, email.description) == ("string(320)", False, "Login email")
    assert customers.attribute("joined_at").type == "timestamp_tz"
    assert customers.attribute("profile").type == "json"
    assert [a.name for a in customers.primary_key] == ["id"]

    products = by_name["products"]
    assert products.attribute("price").type == "decimal(12,2)"
    assert products.attribute("region").type == "string(2)"
    assert products.attribute("tags").type == "array<integer>"
    assert {a.name for a in products.primary_key} == {"sku", "region"}

    composite = next(r for r in result.relationships if r.to == "products")
    assert {(k.from_, k.to) for k in composite.keys} == {("sku", "sku"), ("region", "region")}
    assert any(r.to == "customers" for r in result.relationships)


def test_password_is_not_stored(shop):
    result = introspect(URL, "shop", schemas=["sales"])
    assert ":test@" not in (result.source.connection or "")
