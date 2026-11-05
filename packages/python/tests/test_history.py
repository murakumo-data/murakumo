"""SCD types 3, 4 and 6, built twice on DuckDB from the plain SQL (which `murakumo guide --check`
proves builds the same as dbt), with the tracked columns changed in between."""

from pathlib import Path

import duckdb
import pytest
import yaml

from murakumo.generate import generate
from murakumo.generate.rebuild import _statements
from murakumo.ir.model import History
from murakumo.project import load_project
from murakumo.validate import validate_project

COMMERCE = Path(__file__).resolve().parents[3] / "examples" / "commerce"


@pytest.fixture
def commerce():
    return load_project(COMMERCE).project


def setting(project, name, history, tracked=()):
    entity = project.model.entity(name)
    entity.history = History(history)
    entity.tracked = list(tracked)


def build(project, runs):
    """Build the project once per run, after replacing the raw rows with that run's."""
    files = generate(project).files
    scripts = sorted(p for p in files if p.startswith("guide/sql/build/") and p.endswith(".sql"))
    db = duckdb.connect()
    for run, (parts, customers) in enumerate(runs, start=1):
        loaded = f"2026-01-0{run} 00:00:00+00"
        for index, script in enumerate(scripts):
            for statement in _statements(files[script]):
                db.execute(statement)
            if index == 0:  # the raw tables now exist: land this run's rows
                db.execute("delete from raw_tpch_erp.part; delete from raw_tpch_erp.customer")
                db.execute("delete from raw_tpch_erp.nation")
                db.execute(
                    "insert into raw_tpch_erp.nation (n_nationkey, n_name, _ingested_at) "
                    f"values (1, 'KENYA', '{loaded}')"
                )
                for key, brand, price in parts:
                    db.execute(
                        "insert into raw_tpch_erp.part (p_partkey, p_name, p_brand, p_retailprice, _ingested_at) "
                        f"values ({key}, 'part {key}', '{brand}', {price}, '{loaded}')"
                    )
                for key, segment in customers:
                    db.execute(
                        "insert into raw_tpch_erp.customer (c_custkey, c_name, c_nationkey, c_mktsegment, c_acctbal, "
                        f"_ingested_at) values ({key}, 'customer {key}', 1, '{segment}', 0, '{loaded}')"
                    )
    return files, db


RUNS = [
    ([(1, "Brand#1", 10), (2, "Brand#2", 20)], [(1, "BUILDING"), (2, "MACHINERY")]),
    ([(1, "Brand#9", 10), (2, "Brand#2", 25)], [(1, "AUTOMOBILE"), (2, "MACHINERY")]),
]


def test_scd3_keeps_the_previous_value_of_tracked_columns(commerce):
    setting(commerce, "dim_part", "scd3", ["brand", "retail_price"])
    files, db = build(commerce, RUNS)
    assert "previous_brand" in files["dbt/models/marts/product/dim_part.sql"]
    rows = db.execute(
        "select part_id, brand, previous_brand, retail_price, previous_retail_price "
        "from marts.dim_part where part_id > 0 order by 1"
    ).fetchall()
    assert [(r[0], r[1], r[2], float(r[3]), r[4] and float(r[4])) for r in rows] == [
        (1, "Brand#9", "Brand#1", 10.0, None),
        (2, "Brand#2", None, 25.0, 20.0),
    ]


def test_scd4_keeps_the_current_version_and_a_history_table(commerce):
    setting(commerce, "dim_customer", "scd4")
    files, db = build(commerce, RUNS)
    assert "dbt/models/marts/sales/dim_customer_history.sql" in files
    current = db.execute("select customer_id, market_segment from marts.dim_customer where customer_id > 0 order by 1")
    assert current.fetchall() == [(1, "AUTOMOBILE"), (2, "MACHINERY")]
    history = db.execute(
        "select customer_id, market_segment, valid_to is null from marts.dim_customer_history order by 1, valid_from"
    ).fetchall()
    assert history == [(1, "BUILDING", False), (1, "AUTOMOBILE", True), (2, "MACHINERY", True)]
    columns = {r[0] for r in db.execute("describe marts.dim_customer").fetchall()}
    assert "valid_to" not in columns and "valid_from" in columns


def test_scd6_puts_the_current_value_on_every_version(commerce):
    setting(commerce, "dim_customer", "scd6", ["market_segment"])
    _, db = build(commerce, RUNS)
    rows = db.execute(
        "select customer_id, market_segment, current_market_segment, is_current "
        "from marts.dim_customer where customer_id > 0 order by 1, valid_from"
    ).fetchall()
    assert rows == [
        (1, "BUILDING", "AUTOMOBILE", False),
        (1, "AUTOMOBILE", "AUTOMOBILE", True),
        (2, "MACHINERY", "MACHINERY", True),
    ]


def test_tracked_columns_are_checked(commerce):
    setting(commerce, "dim_part", "scd3", ["colour"])
    setting(commerce, "dim_customer", "scd2", ["market_segment"])
    found = {d.code for d in validate_project(commerce)}
    assert {"MK318", "MK319"} <= found


def test_scd4_history_is_its_own_model_in_dbt(commerce):
    setting(commerce, "dim_customer", "scd4")
    models = yaml.safe_load(generate(commerce).files["dbt/models/marts/sales/_sales__models.yml"])["models"]
    names = {m["name"] for m in models}
    assert {"dim_customer", "dim_customer_history"} <= names
