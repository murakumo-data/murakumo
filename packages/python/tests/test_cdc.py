"""Change feeds (load: cdc): raw keeps every change, staging is the table as it is now."""

import duckdb

from murakumo import edit
from murakumo.generate import generate
from murakumo.generate.plain import Plain
from murakumo.generate.plan import build_plan
from murakumo.generate.targets import target_for
from murakumo.ir.model import Cdc, LoadPattern
from murakumo.manifest import Warehouse
from murakumo.scaffold import init_project
from murakumo.validate import validate_project


def feed(tmp_path):
    project = init_project(tmp_path / "shop")
    edit.add_entity(project, "customers", layer="raw", source="nyc_tlc")
    for name, kind, key in (("id", "integer", True), ("name", "string", False), ("op", "string", False)):
        edit.add_column(project, "customers", name, kind, primary_key=key)
    edit.add_column(project, "customers", "changed_at", "timestamp")
    entity = project.model.entity("customers")
    entity.load = LoadPattern.CDC
    entity.cdc = Cdc(operation="op", order="changed_at")
    return project, entity


def staging_sql(project, entity, warehouse="duckdb"):
    plan = build_plan(project, target_for(warehouse))
    files = {p.removeprefix("dbt/"): c for p, c in generate(project, warehouse).files.items() if p.startswith("dbt/")}
    return plan, Plain(plan, files).select(plan.model("stg_nyc_tlc__customers"))


def codes(project):
    return {d.code for d in validate_project(project)}


def test_staging_is_the_latest_change_of_each_key_without_the_deleted(tmp_path):
    project, entity = feed(tmp_path)
    _, select = staging_sql(project, entity)
    db = duckdb.connect()
    db.execute("create schema raw_nyc_tlc")
    db.execute(
        "create table raw_nyc_tlc.customers (id integer, name varchar, op varchar, changed_at timestamp, "
        "_ingested_at timestamptz, _batch_id varchar)"
    )
    db.execute(
        """insert into raw_nyc_tlc.customers values
        (1, 'Ama', 'c', '2026-01-01', now(), 'a'),
        (1, 'Ama B', 'u', '2026-01-03', now(), 'b'),
        (2, 'Kofi', 'c', '2026-01-01', now(), 'a'),
        (2, 'Kofi', 'D', '2026-01-02', now(), 'b'),
        (3, 'Esi', null, '2026-01-02', now(), 'b')"""
    )
    rows = db.execute(f"select id, name from ({select}) order by id").fetchall()
    assert rows == [(1, "Ama B"), (3, "Esi")]  # the update won, the delete went, a snapshot row (no op) stays


def test_every_warehouse_renders_a_change_feed(tmp_path):
    import sqlglot

    project, entity = feed(tmp_path)
    for warehouse in Warehouse:
        plan, sql = staging_sql(project, entity, warehouse)
        assert "_change_rank = 1" in sql and "not in ('d', 'delete', 'deleted')" in sql
        sqlglot.parse_one(sql, read=plan.target.dialect)


def test_change_feed_rules(tmp_path):
    project, entity = feed(tmp_path)
    assert not codes(project) & {"MK321", "MK322", "MK323"}
    entity.cdc = None  # a feed with no deletes: still the latest change of each key
    assert not codes(project) & {"MK321", "MK322", "MK323"}
    assert "_change_rank = 1" in staging_sql(project, entity)[1] and " not in (" not in staging_sql(project, entity)[1]
    entity.cdc = Cdc(operation="nope")
    assert "MK322" in codes(project)
    entity.cdc = Cdc(operation="op")
    for column in entity.attributes:
        column.key = None
    assert "MK321" in codes(project)
    entity.load = LoadPattern.APPEND
    assert "MK323" in codes(project)
    entity.load = LoadPattern.STREAMING
    assert "MK324" in codes(project)  # nothing streams into it
