"""Snowflake: connections and catalogue reads.

Connection string: ``snowflake://[USER@]ACCOUNT[/DATABASE][?warehouse=W&role=R]``. Anything left
out comes from the environment, the same variables the generated ``profiles.yml`` reads:

    SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_DATABASE, SNOWFLAKE_WAREHOUSE, SNOWFLAKE_ROLE
    SNOWFLAKE_PRIVATE_KEY_PATH   key-pair login (recommended), or
    SNOWFLAKE_PASSWORD           password login

``snowflake://`` on its own therefore uses the environment entirely, and ``snowflake:///DATABASE``
(three slashes) takes the account from the environment and names only the database. Secrets never appear in the
connection string, so nothing secret can reach the model.

Snowflake stores unquoted names in upper case. Names that are entirely upper case come back
lower case (``C_CUSTKEY`` becomes ``c_custkey``), which Snowflake folds back to the same column.
Names with any lower-case letter were created quoted and keep their exact spelling.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

SYSTEM_SCHEMAS = {"INFORMATION_SCHEMA"}


class SnowflakeUnavailable(Exception):
    pass


def settings(url: str = "snowflake://") -> dict[str, Any]:
    """Connection settings from the URL, filled in from the environment."""
    parts = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    path = [p for p in parts.path.split("/") if p]
    values = {
        # The account as written (urlsplit's hostname would lower-case it).
        "account": parts.netloc.rsplit("@", 1)[-1] or os.environ.get("SNOWFLAKE_ACCOUNT"),
        "user": unquote(parts.username) if parts.username else os.environ.get("SNOWFLAKE_USER"),
        "database": path[0] if path else os.environ.get("SNOWFLAKE_DATABASE"),
        "warehouse": query.get("warehouse") or os.environ.get("SNOWFLAKE_WAREHOUSE", "TRANSFORMING"),
        "role": query.get("role") or os.environ.get("SNOWFLAKE_ROLE", "TRANSFORMER"),
    }
    missing = [k for k in ("account", "user") if not values[k]]
    if missing:
        raise SnowflakeUnavailable(
            f"Snowflake {' and '.join(missing)} not set: put it in the URL or set "
            + " and ".join(f"SNOWFLAKE_{m.upper()}" for m in missing)
        )
    key_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH")
    if key_path:
        values["authenticator"] = "SNOWFLAKE_JWT"
        values["private_key_file"] = os.path.expanduser(key_path)
    elif os.environ.get("SNOWFLAKE_PASSWORD"):
        values["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    else:
        raise SnowflakeUnavailable("set SNOWFLAKE_PRIVATE_KEY_PATH (key-pair login) or SNOWFLAKE_PASSWORD")
    return {k: v for k, v in values.items() if v}


def connect(url: str = "snowflake://", **overrides: Any) -> Any:
    try:
        import snowflake.connector
    except ImportError:
        raise SnowflakeUnavailable("Snowflake support is not installed: pip install 'murakumo[snowflake]'") from None
    params = {**settings(url), **overrides}
    try:
        return snowflake.connector.connect(login_timeout=30, network_timeout=120, **params)
    except snowflake.connector.errors.Error as exc:
        message = str(exc).strip().splitlines()[0]
        raise SnowflakeUnavailable(f"cannot connect to Snowflake account {params.get('account')}: {message}") from None


def fold(name: str) -> str:
    """``C_CUSTKEY`` -> ``c_custkey``; ``MixedCase`` stays as it is."""
    return name.lower() if name == name.upper() else name


def read_catalogue(connection: Any, database: str) -> tuple[list[tuple], dict, list, dict, dict]:
    """Columns, primary keys, foreign keys, and table and column comments of one database.

    Shapes match ``database.introspect``: columns are (schema, table, column, position, type,
    nullable); keys and comments are keyed by (schema, table[, column]); names are folded.
    """
    cursor = connection.cursor()
    db = '"' + database.replace('"', '""') + '"' if database != database.upper() else database
    cursor.execute(
        "select table_schema, table_name, column_name, ordinal_position, data_type, is_nullable,"
        " character_maximum_length, numeric_precision, numeric_scale, comment"
        f" from {db}.information_schema.columns order by table_schema, table_name, ordinal_position"
    )
    columns = []
    column_comments: dict[tuple[str, str, str], str] = {}
    for schema, table, name, position, data_type, nullable, length, precision, scale, comment in cursor.fetchall():
        if schema in SYSTEM_SCHEMAS:
            continue
        text = str(data_type).upper()
        if text == "NUMBER" and precision is not None:
            text = f"NUMBER({precision},{scale or 0})"
        elif text == "TEXT":
            text = f"VARCHAR({length})" if length and length < 16_777_216 else "VARCHAR"
        key = (fold(schema), fold(table), fold(name))
        columns.append((*key, int(position), text, str(nullable).upper() == "YES"))
        if comment:
            column_comments[key] = comment

    table_comments: dict[tuple[str, str], str] = {}
    cursor.execute(f"select table_schema, table_name, comment from {db}.information_schema.tables")
    for schema, table, comment in cursor.fetchall():
        if comment and schema not in SYSTEM_SCHEMAS:
            table_comments[(fold(schema), fold(table))] = comment

    primary: dict[tuple[str, str], list[str]] = defaultdict(list)
    cursor.execute(f"show primary keys in database {db}")
    names = [d[0] for d in cursor.description]
    for row in sorted(
        cursor.fetchall(), key=lambda r: (r[names.index("constraint_name")], r[names.index("key_sequence")])
    ):
        record = dict(zip(names, row, strict=True))
        primary[(fold(record["schema_name"]), fold(record["table_name"]))].append(fold(record["column_name"]))

    grouped: dict[str, dict[str, Any]] = {}
    cursor.execute(f"show imported keys in database {db}")
    names = [d[0] for d in cursor.description]
    for row in cursor.fetchall():
        record = dict(zip(names, row, strict=True))
        entry = grouped.setdefault(
            record["fk_name"],
            {
                "from": (fold(record["fk_schema_name"]), fold(record["fk_table_name"])),
                "to": (fold(record["pk_schema_name"]), fold(record["pk_table_name"])),
                "pairs": [],
            },
        )
        entry["pairs"].append(
            (int(record["key_sequence"]), fold(record["fk_column_name"]), fold(record["pk_column_name"]))
        )
    foreign = []
    for entry in grouped.values():
        pairs = sorted(entry["pairs"])
        foreign.append((*entry["from"], [p[1] for p in pairs], *entry["to"], [p[2] for p in pairs]))
    cursor.close()
    return columns, dict(primary), foreign, table_comments, column_comments
