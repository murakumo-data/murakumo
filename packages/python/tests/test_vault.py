"""The Data Vault pack, built twice on DuckDB from the plain SQL (which `murakumo guide --check`
proves builds the same as dbt): hubs and links keep each key once, satellites add a row only
when something changed."""

from pathlib import Path

import duckdb
import pytest

from murakumo.generate import generate
from murakumo.generate.rebuild import _statements
from murakumo.ir.model import EntityKind
from murakumo.project import load_project
from murakumo.validate import validate_project

COMMERCE = Path(__file__).resolve().parents[3] / "examples" / "commerce"


@pytest.fixture
def commerce():
    return load_project(COMMERCE).project


def build(project, runs):
    """Build once per run; each run lands its customers and orders as new raw rows."""
    files = generate(project).files
    scripts = sorted(p for p in files if p.startswith("guide/sql/build/") and p.endswith(".sql"))
    db = duckdb.connect()
    for run, (customers, orders) in enumerate(runs, start=1):
        loaded = f"2026-01-0{run} 00:00:00+00"
        for index, script in enumerate(scripts):
            for statement in _statements(files[script]):
                db.execute(statement)
            if index == 0:
                for key, segment in customers:
                    db.execute(
                        "insert into raw_tpch_erp.customer (c_custkey, c_name, c_mktsegment, c_acctbal, _ingested_at) "
                        f"values ({key}, ' customer {key}', '{segment}', 0, '{loaded}')"
                    )
                for key, customer in orders:
                    db.execute(
                        "insert into raw_tpch_erp.orders (o_orderkey, o_custkey, _ingested_at) "
                        f"values ({key}, {customer}, '{loaded}')"
                    )
    return files, db


RUNS = [
    ([(1, "BUILDING"), (2, "MACHINERY")], [(10, 1), (11, 2)]),
    ([(1, "AUTOMOBILE"), (2, "MACHINERY"), (3, "HOUSEHOLD")], [(10, 1), (12, 3)]),
]


def test_hubs_and_links_keep_each_key_once(commerce):
    _, db = build(commerce, RUNS)
    assert db.execute("select count(*) from intermediate.hub_customer").fetchone()[0] == 3
    assert db.execute("select count(*) from intermediate.hub_order").fetchone()[0] == 3
    first = db.execute("select load_date from intermediate.hub_customer where customer_number = 1").fetchone()[0]
    assert first.day == 1  # first seen in run 1, not replaced by run 2
    assert db.execute("select count(*) from intermediate.link_order_customer").fetchone()[0] == 3
    # the link's hub keys are the hubs' own
    orphans = db.execute(
        "select count(*) from intermediate.link_order_customer l "
        "left join intermediate.hub_customer h using (hub_customer_hk) where h.hub_customer_hk is null"
    ).fetchone()[0]
    assert orphans == 0


def test_satellites_add_a_row_only_when_something_changed(commerce):
    _, db = build(commerce, RUNS)
    rows = db.execute(
        "select customer_number, market_segment from intermediate.sat_customer order by 1, load_date"
    ).fetchall()
    assert rows == [(1, "BUILDING"), (1, "AUTOMOBILE"), (2, "MACHINERY"), (3, "HOUSEHOLD")]
    current = db.execute(
        "select customer_number, market_segment from intermediate.sat_customer_current order by 1"
    ).fetchall()
    assert current == [(1, "AUTOMOBILE"), (2, "MACHINERY"), (3, "HOUSEHOLD")]


def test_hash_keys_ignore_case_and_spaces(commerce):
    files = generate(commerce).files
    sql = files["dbt/models/intermediate/sales/hub_customer.sql"]
    assert "MD5(COALESCE(UPPER(TRIM(CAST(" in sql
    link = files["dbt/models/intermediate/sales/link_order_customer.sql"]
    assert "|| '||' ||" in link and "as hub_customer_hk" in link and "as hub_order_hk" in link


def test_a_link_needs_two_hubs(commerce):
    commerce.model.relationships = [r for r in commerce.model.relationships if r.to != "hub_order"]
    output = generate(commerce)
    assert any(d.code == "MK509" and "two or more hubs" in d.message for d in output.diagnostics)
    assert "dbt/models/intermediate/sales/link_order_customer.sql" not in output.files


def test_vault_tables_need_a_business_key(commerce):
    hub = commerce.model.entity("hub_order")
    for attribute in hub.attributes:
        attribute.key = None
    assert "MK320" in {d.code for d in validate_project(commerce)}
    assert hub.kind is EntityKind.HUB
