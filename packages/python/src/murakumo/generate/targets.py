"""What differs between warehouses: physical types, SQL dialect and dbt adapter.

Adding a warehouse means adding one ``Target`` here; the generators use nothing else.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from murakumo.ir.types import LogicalType, TypeName, parse_type
from murakumo.manifest import Warehouse

T = TypeName


@dataclass(frozen=True)
class Target:
    warehouse: Warehouse
    dialect: str  # SQLGlot dialect name
    adapter: str  # dbt adapter package
    types: dict[TypeName, str]
    decimal: Callable[[int, int], str]
    string_n: Callable[[int], str] | None = None
    array: Callable[[str], str] = lambda element: f"ARRAY<{element}>"
    max_precision: int = 38

    def physical_name(self, logical: str) -> str:
        """The physical type for a logical type written as text: ``physical_name("timestamp")``."""
        return self.physical(parse_type(logical))

    def physical(self, logical: LogicalType) -> str:
        if logical.name is T.ARRAY and logical.element is not None:
            return self.array(self.physical(logical.element))
        if logical.name is T.DECIMAL:
            params = logical.params
            if not params:
                precision, scale = 38, 9
            elif len(params) == 1:
                precision, scale = params[0], 0
            else:
                precision, scale = params[0], params[1]
            return self.decimal(min(precision, self.max_precision), scale)
        if logical.name is T.STRING and logical.params and self.string_n is not None:
            return self.string_n(logical.params[0])
        return self.types[logical.name]


_COMMON = {
    T.BOOLEAN: "BOOLEAN",
    T.SMALLINT: "SMALLINT",
    T.INTEGER: "INTEGER",
    T.BIGINT: "BIGINT",
    T.DATE: "DATE",
}

TARGETS: dict[Warehouse, Target] = {
    Warehouse.DUCKDB: Target(
        Warehouse.DUCKDB,
        dialect="duckdb",
        adapter="dbt-duckdb",
        types={
            **_COMMON,
            T.STRING: "VARCHAR",
            T.TEXT: "VARCHAR",
            T.FLOAT: "FLOAT",
            T.DOUBLE: "DOUBLE",
            T.MONEY: "DECIMAL(18,4)",
            T.TIME: "TIME",
            T.TIMESTAMP: "TIMESTAMP",
            T.TIMESTAMP_TZ: "TIMESTAMPTZ",
            T.UUID: "UUID",
            T.JSON: "JSON",
            T.BINARY: "BLOB",
            T.STRUCT: "JSON",
        },
        decimal=lambda p, s: f"DECIMAL({p},{s})",
        string_n=lambda n: f"VARCHAR({n})",
        array=lambda element: f"{element}[]",
    ),
    Warehouse.SNOWFLAKE: Target(
        Warehouse.SNOWFLAKE,
        dialect="snowflake",
        adapter="dbt-snowflake",
        types={
            **_COMMON,
            T.SMALLINT: "NUMBER(5,0)",
            T.INTEGER: "NUMBER(10,0)",
            T.BIGINT: "NUMBER(19,0)",
            T.STRING: "VARCHAR",
            T.TEXT: "VARCHAR",
            T.FLOAT: "FLOAT",
            T.DOUBLE: "FLOAT",
            T.MONEY: "NUMBER(38,4)",
            T.TIME: "TIME",
            T.TIMESTAMP: "TIMESTAMP_NTZ",
            T.TIMESTAMP_TZ: "TIMESTAMP_TZ",
            T.UUID: "VARCHAR(36)",
            T.JSON: "VARIANT",
            T.BINARY: "BINARY",
            T.STRUCT: "OBJECT",
        },
        decimal=lambda p, s: f"NUMBER({p},{s})",
        string_n=lambda n: f"VARCHAR({n})",
        array=lambda element: "ARRAY",
    ),
    Warehouse.DATABRICKS: Target(
        Warehouse.DATABRICKS,
        dialect="databricks",
        adapter="dbt-databricks",
        types={
            **_COMMON,
            T.STRING: "STRING",
            T.TEXT: "STRING",
            T.FLOAT: "FLOAT",
            T.DOUBLE: "DOUBLE",
            T.MONEY: "DECIMAL(38,4)",
            T.TIME: "STRING",
            T.TIMESTAMP: "TIMESTAMP_NTZ",
            T.TIMESTAMP_TZ: "TIMESTAMP",
            T.UUID: "STRING",
            T.JSON: "STRING",
            T.BINARY: "BINARY",
            T.STRUCT: "STRING",
        },
        decimal=lambda p, s: f"DECIMAL({p},{s})",
    ),
    Warehouse.BIGQUERY: Target(
        Warehouse.BIGQUERY,
        dialect="bigquery",
        adapter="dbt-bigquery",
        types={
            T.BOOLEAN: "BOOL",
            T.SMALLINT: "INT64",
            T.INTEGER: "INT64",
            T.BIGINT: "INT64",
            T.DATE: "DATE",
            T.STRING: "STRING",
            T.TEXT: "STRING",
            T.FLOAT: "FLOAT64",
            T.DOUBLE: "FLOAT64",
            T.MONEY: "NUMERIC(38,4)",
            T.TIME: "TIME",
            T.TIMESTAMP: "DATETIME",
            T.TIMESTAMP_TZ: "TIMESTAMP",
            T.UUID: "STRING",
            T.JSON: "JSON",
            T.BINARY: "BYTES",
            T.STRUCT: "JSON",
        },
        decimal=lambda p, s: f"BIGNUMERIC({p},{s})" if p > 38 or s > 9 else f"NUMERIC({p},{s})",
        string_n=lambda n: f"STRING({n})",
        max_precision=76,
    ),
    Warehouse.POSTGRES: Target(
        Warehouse.POSTGRES,
        dialect="postgres",
        adapter="dbt-postgres",
        types={
            **_COMMON,
            T.STRING: "TEXT",
            T.TEXT: "TEXT",
            T.FLOAT: "REAL",
            T.DOUBLE: "DOUBLE PRECISION",
            T.MONEY: "NUMERIC(18,4)",
            T.TIME: "TIME",
            T.TIMESTAMP: "TIMESTAMP",
            T.TIMESTAMP_TZ: "TIMESTAMPTZ",
            T.UUID: "UUID",
            T.JSON: "JSONB",
            T.BINARY: "BYTEA",
            T.STRUCT: "JSONB",
        },
        decimal=lambda p, s: f"NUMERIC({p},{s})",
        string_n=lambda n: f"VARCHAR({n})",
        array=lambda element: f"{element}[]",
    ),
    Warehouse.REDSHIFT: Target(
        Warehouse.REDSHIFT,
        dialect="redshift",
        adapter="dbt-redshift",
        types={
            **_COMMON,
            T.STRING: "VARCHAR(65535)",
            T.TEXT: "VARCHAR(65535)",
            T.FLOAT: "REAL",
            T.DOUBLE: "DOUBLE PRECISION",
            T.MONEY: "NUMERIC(18,4)",
            T.TIME: "TIME",
            T.TIMESTAMP: "TIMESTAMP",
            T.TIMESTAMP_TZ: "TIMESTAMPTZ",
            T.UUID: "VARCHAR(36)",
            T.JSON: "SUPER",
            T.BINARY: "VARBYTE",
            T.STRUCT: "SUPER",
        },
        decimal=lambda p, s: f"NUMERIC({p},{s})",
        string_n=lambda n: f"VARCHAR({n})",
        array=lambda element: "SUPER",
    ),
}
# Fabric and Synapse speak T-SQL; their adapters arrive with the Microsoft target work.
_TSQL = Target(
    Warehouse.FABRIC,
    dialect="tsql",
    adapter="dbt-fabric",
    types={
        T.BOOLEAN: "BIT",
        T.SMALLINT: "SMALLINT",
        T.INTEGER: "INT",
        T.BIGINT: "BIGINT",
        T.DATE: "DATE",
        T.STRING: "VARCHAR(8000)",
        T.TEXT: "VARCHAR(8000)",
        T.FLOAT: "REAL",
        T.DOUBLE: "FLOAT",
        T.MONEY: "DECIMAL(18,4)",
        T.TIME: "TIME(6)",
        T.TIMESTAMP: "DATETIME2(6)",
        T.TIMESTAMP_TZ: "DATETIME2(6)",
        T.UUID: "UNIQUEIDENTIFIER",
        T.JSON: "VARCHAR(8000)",
        T.BINARY: "VARBINARY(8000)",
        T.STRUCT: "VARCHAR(8000)",
    },
    decimal=lambda p, s: f"DECIMAL({p},{s})",
    string_n=lambda n: f"VARCHAR({n})",
    array=lambda element: "VARCHAR(8000)",
)
TARGETS[Warehouse.FABRIC] = _TSQL
TARGETS[Warehouse.SYNAPSE] = Target(**{**_TSQL.__dict__, "warehouse": Warehouse.SYNAPSE, "adapter": "dbt-synapse"})


def target_for(warehouse: Warehouse | str) -> Target:
    return TARGETS[Warehouse(warehouse)]
