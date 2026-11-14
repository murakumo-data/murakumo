"""verify and build on the warehouses reached through a plain connection.

The PostgreSQL run is live: it needs MURAKUMO_TEST_POSTGRES_URL, as in CI (see test_postgres.py).
The other warehouses need an account, so only what does not is tested here.
"""

import os
from urllib.parse import urlsplit

import pytest
import yaml

from murakumo.generate import generate
from murakumo.generate.sqlbackend import BACKENDS, statements
from murakumo.generate.verify import SUPPORTED, verify
from murakumo.importers.database import SourceUnavailable
from murakumo.manifest import Warehouse
from murakumo.scaffold import init_project

URL = os.environ.get("MURAKUMO_TEST_POSTGRES_URL")


def test_every_warehouse_can_be_verified():
    assert set(SUPPORTED) == set(Warehouse)
    assert set(BACKENDS) == set(Warehouse) - {Warehouse.DUCKDB, Warehouse.SNOWFLAKE}


def test_statements_are_split_at_line_ends():
    script = "-- a comment\ncreate schema a;\ncreate table a.b (c text); -- trailing\ninsert into a.b values ('x;y');\n"
    assert statements(script) == [
        "create schema a",
        "create table a.b (c text); -- trailing\ninsert into a.b values ('x;y')",
    ]


def test_a_missing_connection_says_what_to_set(tmp_path, monkeypatch):
    pytest.importorskip("dbt.adapters.postgres")
    for name in ("DB_HOST", "DB_USER", "DB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SourceUnavailable, match="DB_HOST, DB_USER, DB_PASSWORD"):
        verify(init_project(tmp_path / "taxi"), warehouse="postgres")


@pytest.mark.parametrize("warehouse", ["postgres", "redshift", "fabric", "synapse", "databricks"])
def test_profiles_read_the_variables_the_backends_read(tmp_path, warehouse):
    project = init_project(tmp_path / "taxi")
    profile = yaml.safe_load(generate(project, warehouse).files["dbt/profiles.yml"])["taxi"]["outputs"]["dev"]
    text = str(profile)
    if warehouse in ("postgres", "redshift"):
        assert "env_var('DB_PORT'" in text and "env_var('DB_NAME'" in text
    elif warehouse == "databricks":
        assert "env_var('DATABRICKS_CATALOG'" in text
    else:
        assert profile["authentication"] == "ServicePrincipal" and "_SERVER" in profile["server"]


@pytest.mark.skipif(not URL, reason="set MURAKUMO_TEST_POSTGRES_URL to run PostgreSQL tests")
def test_verify_on_postgres(tmp_path, monkeypatch):
    pytest.importorskip("dbt.adapters.postgres")
    psycopg = pytest.importorskip("psycopg")
    url = urlsplit(URL)
    for name, value in (
        ("DB_HOST", url.hostname),
        ("DB_PORT", str(url.port or 5432)),
        ("DB_USER", url.username),
        ("DB_PASSWORD", url.password),
        ("DB_NAME", url.path.lstrip("/")),
    ):
        monkeypatch.setenv(name, value)
    result = verify(init_project(tmp_path / "taxi"), rows=40, warehouse="postgres")
    assert result.ok, result.error or [n for n in result.nodes if n.status not in ("success", "pass")]
    assert result.rerun is True and result.count("model") > 3 and result.count("test", "pass") > 10
    with psycopg.connect(URL) as connection:  # nothing left behind
        left = connection.execute(
            "select count(*) from information_schema.schemata where schema_name like 'verify\\_%'"
        ).fetchone()[0]
    assert left == 0
