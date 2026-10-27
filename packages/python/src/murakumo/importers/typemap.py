"""Map the types sources report (SQL, Parquet, JSON Schema) to logical types."""

from __future__ import annotations

import re

from murakumo.ir.types import parse_type

# Base type names as databases spell them, upper case, without parameters.
_SQL: dict[str, str] = {
    **dict.fromkeys(
        ["VARCHAR", "CHARACTER VARYING", "NVARCHAR", "STRING", "CHAR", "CHARACTER", "NCHAR", "BPCHAR"], "string"
    ),
    **dict.fromkeys(["TEXT", "CLOB", "NTEXT", "LONGTEXT", "MEDIUMTEXT", "CITEXT"], "text"),
    **dict.fromkeys(["BOOLEAN", "BOOL", "BIT"], "boolean"),
    **dict.fromkeys(["TINYINT", "SMALLINT", "INT2", "INT1", "UTINYINT", "SMALLSERIAL"], "smallint"),
    **dict.fromkeys(["INTEGER", "INT", "INT4", "MEDIUMINT", "USMALLINT", "SERIAL"], "integer"),
    **dict.fromkeys(["BIGINT", "INT8", "UINTEGER", "UBIGINT", "HUGEINT", "BIGSERIAL", "LONG", "INT64"], "bigint"),
    **dict.fromkeys(["REAL", "FLOAT4", "FLOAT32"], "float"),
    **dict.fromkeys(["DOUBLE", "DOUBLE PRECISION", "FLOAT8", "FLOAT", "FLOAT64"], "double"),
    **dict.fromkeys(["DECIMAL", "NUMERIC", "NUMBER", "BIGNUMERIC"], "decimal"),
    **dict.fromkeys(["MONEY", "SMALLMONEY"], "money"),
    "DATE": "date",
    **dict.fromkeys(["TIME", "TIME WITHOUT TIME ZONE", "TIME WITH TIME ZONE", "TIMETZ"], "time"),
    **dict.fromkeys(
        ["TIMESTAMP", "DATETIME", "DATETIME2", "TIMESTAMP_NTZ", "TIMESTAMP WITHOUT TIME ZONE", "SMALLDATETIME"],
        "timestamp",
    ),
    **dict.fromkeys(
        ["TIMESTAMPTZ", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP_TZ", "TIMESTAMP_LTZ", "DATETIMEOFFSET"], "timestamp_tz"
    ),
    **dict.fromkeys(["UUID", "UNIQUEIDENTIFIER"], "uuid"),
    **dict.fromkeys(["JSON", "JSONB", "VARIANT", "OBJECT", "SUPER", "MAP"], "json"),
    **dict.fromkeys(["BYTEA", "BLOB", "BINARY", "VARBINARY", "BYTES", "IMAGE"], "binary"),
    "STRUCT": "struct",
    "ROW": "struct",
}

_PARAMS = re.compile(r"^([A-Z_ ][A-Z0-9_ ]*?)\s*\(([^()]*)\)\s*(.*)$")


def sql_to_logical(source_type: str) -> tuple[str, bool]:
    """Map a SQL type such as ``NUMBER(38,0)`` or ``VARCHAR[]`` to a logical type.

    Returns the logical type and whether the mapping was exact; unknown types become ``string``.
    """
    text = " ".join(source_type.strip().upper().split())

    if text == "ARRAY":  # Snowflake's untyped array
        return "array<json>", True
    if text.endswith("[]"):
        element, exact = sql_to_logical(text[:-2])
        return f"array<{element}>", exact
    for prefix in ("ARRAY<", "LIST<"):
        if text.startswith(prefix) and text.endswith(">"):
            element, exact = sql_to_logical(text[len(prefix) : -1])
            return f"array<{element}>", exact
    if text.startswith(("STRUCT", "ROW(", "MAP(", "MAP<", "UNION(")):
        return ("json" if text.startswith(("MAP", "UNION")) else "struct"), True

    params: list[int] = []
    match = _PARAMS.match(text)
    if match:
        base, raw, rest = match.groups()
        text = f"{base} {rest}".strip()
        try:
            params = [int(p) for p in raw.split(",") if p.strip()]
        except ValueError:
            params = []

    logical = _SQL.get(text)
    if logical is None:
        return "string", False
    if logical == "decimal":
        if text == "NUMBER" and len(params) in (1, 2) and (len(params) == 1 or params[1] == 0):
            # Snowflake and Oracle spell whole numbers NUMBER(p,0).
            return ("integer" if params[0] <= 9 else "bigint"), True
        if len(params) == 2:
            return f"decimal({params[0]},{params[1]})", True
        if len(params) == 1:
            return f"decimal({params[0]},0)", True
        return "decimal(38,9)" if text in ("NUMERIC", "DECIMAL") else "decimal(38,0)", True
    if logical == "string" and params and text in ("VARCHAR", "CHARACTER VARYING", "NVARCHAR", "CHAR", "CHARACTER"):
        return f"string({params[0]})", True
    if logical in ("timestamp", "timestamp_tz") and params:
        return f"{logical}({params[0]})", True
    return logical, True


def json_schema_to_logical(schema: dict) -> str:
    """Map a JSON Schema / OpenAPI property to a logical type."""
    kind = schema.get("type")
    if isinstance(kind, list):  # ["string", "null"]
        kind = next((k for k in kind if k != "null"), "string")
    fmt = schema.get("format")
    if kind == "string":
        return {"date-time": "timestamp_tz", "date": "date", "time": "time", "uuid": "uuid", "byte": "binary"}.get(
            fmt, "string"
        )
    if kind == "integer":
        return "integer" if fmt == "int32" else "bigint"
    if kind == "number":
        return "float" if fmt == "float" else "double"
    if kind == "boolean":
        return "boolean"
    if kind == "array":
        items = schema.get("items") or {}
        element = json_schema_to_logical(items) if items.get("type") not in ("object", "array") else "json"
        return f"array<{element}>"
    return "json"


def is_logical_type(text: str) -> bool:
    try:
        parse_type(text)
    except ValueError:
        return False
    return True
