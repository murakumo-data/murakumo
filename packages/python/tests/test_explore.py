"""Exploring before modelling: raw-only loads, previews before a build, suggested queries, and
finding where a column appears again."""

import shutil
from pathlib import Path

import pytest

from murakumo import explore, relations
from murakumo.project import load_project

COMMERCE = Path(__file__).resolve().parents[3] / "examples" / "commerce"


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    """The commerce example with only its raw tables loaded: made-up rows, no dbt run."""
    pytest.importorskip("duckdb")
    from murakumo.generate.verify import build

    root = tmp_path_factory.mktemp("explore") / "commerce"
    shutil.copytree(COMMERCE, root, ignore=shutil.ignore_patterns("*.duckdb", ".murakumo"))
    project = load_project(root).project
    result = build(project, sample=60, load_only=True)
    assert result.error is None and result.loaded and not result.nodes  # raw tables only
    return load_project(root).project


# Tier 0 needs no warehouse ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "column", "expected"),
    [
        ("orders", "customer_id", "customer_id"),
        ("orders", "CustomerID", "customer_id"),
        ("orders", "cust_id", "customer_id"),
        ("orders", "o_custkey", "customer_id"),
        ("customer", "c_custkey", "customer_id"),
        ("customer", "id", "customer_id"),
        ("stg_erp__customers", "id", "customer_id"),
        ("dim_customer", "customer_key", "customer_id"),
        ("yellow_tripdata", "PULocationID", "location_id"),
        ("trips", "pickup_location_id", "location_id"),
        ("taxi_zone_lookup", "LocationID", "location_id"),
        ("orders", "order_date", None),
        ("orders", "status", None),
    ],
)
def test_canonical_names(table, column, expected):
    assert relations.canonical(table, column) == expected


def test_candidates_from_names_and_the_model():
    project = load_project(COMMERCE).project
    found = relations.candidates(project, "orders", "o_custkey")
    assert found["canonical"] == "customer_id"
    first = found["candidates"][0]
    assert (first["entity"], first["column"], first["strength"]) == ("customer", "c_custkey", "related")
    assert first["unique"]  # the customer's key
    assert all(c["layer"] in ("raw", "staging") for c in found["candidates"])
    with pytest.raises(explore.ExploreError):
        relations.candidates(project, "orders", "nothing_here")


def test_preview_sql_folds_models_down_to_sampled_raw_tables():
    project = load_project(COMMERCE).project
    sql, reads = explore.preview_sql(project, "fct_order_line")
    assert sorted(reads) == ["lineitem", "orders"]
    assert f"limit {explore.SAMPLE}" in sql and "__stg_" in sql
    assert "{{" not in sql and "ref(" not in sql  # every dbt function resolved
    full, _ = explore.preview_sql(project, "fct_order_line", sample=None)
    assert f"limit {explore.SAMPLE}" not in full
    # An SCD2 table reads a snapshot, which only a build has: previewed as its first run.
    scd, _ = explore.preview_sql(project, "dim_customer")
    assert "__snp_dim_customer" in scd and "dbt_valid_to" in scd


# On a warehouse with the raw tables loaded ------------------------------------------------------------


def test_status_reads_the_catalog(loaded):
    found = explore.status(loaded)
    assert found["reason"] is None
    assert found["tables"]["orders"] == {"exists": True, "rows": 60}
    assert found["built"]["fct_order_line"] is False  # only the raw tables were loaded


def test_status_before_anything_is_loaded(tmp_path):
    pytest.importorskip("duckdb")
    root = tmp_path / "commerce"
    shutil.copytree(COMMERCE, root, ignore=shutil.ignore_patterns("*.duckdb", ".murakumo"))
    found = explore.status(load_project(root).project)
    assert "does not exist yet" in found["reason"]
    assert not any(t["exists"] for t in found["tables"].values())


@pytest.mark.parametrize(
    "table", ["orders", "fct_order_line", "dim_customer", "customer_order_activity", "hub_customer"]
)
def test_preview_before_building(loaded, table):
    result = explore.preview(loaded, table)
    assert result["sampled"] and result["columns"] and result["rows"]


def test_preview_on_all_the_data_and_its_estimate(loaded):
    result = explore.preview(loaded, "fct_order_line", full=True)
    assert not result["sampled"] and result["rows"]
    assert explore.estimate(loaded, "fct_order_line")["free"]  # DuckDB, here


def test_suggested_queries_run(loaded):
    from murakumo import query

    found = explore.suggested(loaded, "orders")
    titles = [q["title"] for q in found]
    assert titles[:2] == ["First rows", "How many rows"]
    assert any("per day" in t for t in titles) and "Duplicate keys" in titles
    assert "o_custkey with no match in customer" in titles
    assert len(titles) == len(set(titles))  # each one has a name of its own
    for item in found:  # each one runs as written
        query.run(loaded, item["sql"], "warehouse")


def test_check_compares_values_on_samples(loaded):
    found = relations.check(loaded, "orders", "o_custkey")
    match = next(m for m in found["matches"] if m["entity"] == "customer")
    assert match["overlap"]["likely"] and match["overlap"]["cardinality"] == "many_to_one"
    assert not found["wide"] and found["others"] > 0  # ids with other names are left out unless asked
    assert (loaded.root / ".murakumo" / "profiles.json").exists()
    assert (loaded.root / ".murakumo" / ".gitignore").exists()
    wide = relations.check(loaded, "orders", "o_custkey", wide=True)
    chance = [m for m in wide["matches"] if m["strength"] == "maybe" and m["overlap"]["here_in_there"] >= 0.8]
    assert chance and all(not m["overlap"]["likely"] for m in chance)
    assert all(m["overlap"]["chance"] or m["overlap"]["shared"] for m in chance)


def test_profiles_are_kept_until_the_table_changes(loaded, monkeypatch):
    from murakumo import query

    relations.profiles(loaded, {"orders": ["o_custkey"]})
    calls = []
    real = query.run
    monkeypatch.setattr(query, "run", lambda *a, **k: calls.append(a[1]) or real(*a, **k))
    relations.profiles(loaded, {"orders": ["o_custkey"]})
    assert calls == []  # from the cache: the row count has not changed


def test_find_a_value(loaded):
    from murakumo import query

    key = query.run(loaded, "select o_custkey from raw_tpch_erp.orders limit 1", "warehouse").rows[0][0]
    found = relations.find(loaded, str(key))
    assert {"entity": "orders", "column": "o_custkey"} in found["found"]
    assert relations.find(loaded, "no such value at all")["found"] == []
