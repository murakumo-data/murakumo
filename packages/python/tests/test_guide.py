"""The replication guide: its chapters, the plain load SQL, and rebuilding from it alone."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from murakumo.cli import app
from murakumo.generate import generate, loads
from murakumo.generate.ddl import DDL
from murakumo.project import load_project

COMMERCE = Path(__file__).resolve().parents[3] / "examples" / "commerce"
runner = CliRunner()


@pytest.fixture
def commerce():
    return load_project(COMMERCE).project


def test_the_guide_has_every_chapter_and_the_scripts_it_runs(commerce):
    files = generate(commerce).files
    chapters = sorted(p for p in files if p.startswith("guide/") and p.count("/") == 1 and p[6:8].isdigit())
    assert [p.split("-", 1)[1] for p in chapters] == [
        "overview.md",
        "accounts.md",
        "repository.md",
        "sources.md",
        "raw.md",
        "transformations.md",
        "running.md",
        "quality.md",
        "ci.md",
        "reference.md",
        "decisions.md",
        "troubleshooting.md",
    ]
    transformations = files["guide/05-transformations.md"]
    # every model, its dbt SQL, the same as plain SQL, and a box to tick
    for model in ("stg_tpch_erp__orders", "dim_customer", "fct_order_fulfilment", "customer_order_activity"):
        assert f"Build {model}" in transformations
        assert f"- [ ] Build {model}" in transformations
    assert "**You should see:**" in transformations and "**Why:**" in transformations
    assert "guide/sql/build/00_landing.sql" in files and "guide/sql/load/00_audit.sql" in files
    assert "guide/sql/tests/dim_customer.sql" in files
    rebuild = files["guide/rebuild.sh"]
    assert rebuild.index("sql/build/00_landing.sql") < rebuild.index("sql/load/") < rebuild.index("sql/build/01_")
    reference = files["guide/09-reference.md"]
    assert "dim_customer (conformed)" in reference and "sales_dashboard" in reference
    page = files["guide/index.html"]
    assert page.count('type="checkbox"') == sum(f.count("\n- [ ] ") for p, f in files.items() if p in chapters)
    assert "localStorage" in page


def test_the_guide_follows_the_warehouse(commerce):
    files = generate(commerce, "snowflake").files
    accounts = files["guide/01-accounts.md"]
    assert "openssl genrsa" in accounts and "create user if not exists murakumo_dbt" in accounts
    assert "guide/sql/masking.sql" in files
    load = next(v for k, v in files.items() if k.startswith("guide/sql/load/") and k.endswith("__orders.sql"))
    assert "put 'file://landing/tpch_erp/orders/orders_*.jsonl' @raw_tpch_erp.%orders" in load
    assert "snow sql -f" in files["guide/rebuild.sh"]


def test_the_decisions_log_is_in_the_guide(commerce, tmp_path):
    import shutil

    from murakumo import stack

    copy = tmp_path / "commerce"
    shutil.copytree(COMMERCE, copy, ignore=shutil.ignore_patterns("generated", "*.duckdb"))
    stack.record(copy, "Zones are fetched daily", why="they change a few times a year")
    files = generate(load_project(copy).project).files
    assert "Zones are fetched daily" in files["guide/10-decisions.md"]


def test_plain_loads_take_each_file_once_the_tables_own_way(commerce, tmp_path, monkeypatch):
    import duckdb

    plan = generate(commerce).plan
    scripts = loads.render(plan)
    monkeypatch.chdir(tmp_path)
    con = duckdb.connect()
    con.execute(DDL(plan).raw())
    con.execute(scripts["00_audit.sql"])

    def land(table, run, rows):
        folder = Path(f"landing/tpch_erp/{table}")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{table}_2026110{run}T000000000000Z_0001.jsonl").write_text("\n".join(json.dumps(r) for r in rows))

    def load(table):
        con.execute(next(v for k, v in scripts.items() if k.endswith(f"__{table}.sql")))
        return con.execute(f"select count(*) from raw_tpch_erp.{table}").fetchone()[0]

    entity = commerce.model.entity("region")
    assert entity.load.value == "full"
    land("region", 1, [{"r_regionkey": i, "r_name": "a"} for i in range(3)])
    assert load("region") == 3
    assert load("region") == 3  # nothing new
    land("region", 2, [{"r_regionkey": i, "r_name": "b"} for i in range(5)])
    assert load("region") == 5  # the latest run replaces the table
    assert con.execute("select distinct r_name from raw_tpch_erp.region").fetchall() == [("b",)]

    land("customer", 1, [{"c_custkey": 1}, {"c_custkey": 2}])  # cdc: appended
    assert load("customer") == 2
    assert load("customer") == 2
    land("customer", 2, [{"c_custkey": 3}])
    assert load("customer") == 3  # the new file's rows are added

    def weather(run, rows):
        folder = Path("landing/open_meteo/weather_daily")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"weather_daily_2026110{run}T000000000000Z_0001.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows)
        )
        con.execute(next(v for k, v in scripts.items() if k.endswith("__weather_daily.sql")))
        return con.execute(
            "select location_key, observed_date::varchar, temperature_2m_max "
            "from raw_open_meteo.weather_daily order by 1, 2"
        ).fetchall()

    day = {"location_key": "oslo", "observed_date": "2026-11-01"}
    assert weather(1, [{**day, "temperature_2m_max": 3.0}]) == [("oslo", "2026-11-01", 3.0)]
    later = {"location_key": "oslo", "observed_date": "2026-11-02", "temperature_2m_max": 5.0}
    # incremental: the new row for the same key replaces the old one; a new key is added
    assert weather(2, [{**day, "temperature_2m_max": 4.0}, later]) == [
        ("oslo", "2026-11-01", 4.0),
        ("oslo", "2026-11-02", 5.0),
    ]


def test_guide_check_rebuilds_the_project_from_the_guide_alone(commerce):
    pytest.importorskip("dbt.adapters.duckdb")
    result = runner.invoke(app, ["guide", "--check", "-p", str(COMMERCE), "--rows", "20"])
    assert result.exit_code == 0, result.output
    assert "The guide rebuilds the project" in result.output
    assert "after each of 2 runs of new files" in " ".join(result.output.split())
