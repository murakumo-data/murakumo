"""Logical types: what a column means, independent of any warehouse.

Generators map each logical type to a physical type per target, so the model never says
``NUMBER(38,4)`` or ``DECIMAL(38,4)``; it says ``money``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class TypeName(StrEnum):
    STRING = "string"
    TEXT = "text"
    BOOLEAN = "boolean"
    SMALLINT = "smallint"
    INTEGER = "integer"
    BIGINT = "bigint"
    FLOAT = "float"
    DOUBLE = "double"
    DECIMAL = "decimal"
    MONEY = "money"
    DATE = "date"
    TIME = "time"
    TIMESTAMP = "timestamp"
    TIMESTAMP_TZ = "timestamp_tz"
    UUID = "uuid"
    JSON = "json"
    BINARY = "binary"
    ARRAY = "array"
    STRUCT = "struct"


# Types that take parameters, and how many: decimal(precision, scale), string(max_length), array<element>.
_PARAMS: dict[TypeName, tuple[int, int]] = {
    TypeName.DECIMAL: (0, 2),
    TypeName.STRING: (0, 1),
    TypeName.TIMESTAMP: (0, 1),
    TypeName.TIMESTAMP_TZ: (0, 1),
}

# Families decide which types can be compared or joined without a cast.
FAMILIES: dict[TypeName, str] = {
    TypeName.STRING: "text",
    TypeName.TEXT: "text",
    TypeName.UUID: "text",
    TypeName.SMALLINT: "integer",
    TypeName.INTEGER: "integer",
    TypeName.BIGINT: "integer",
    TypeName.FLOAT: "number",
    TypeName.DOUBLE: "number",
    TypeName.DECIMAL: "number",
    TypeName.MONEY: "number",
    TypeName.DATE: "date",
    TypeName.TIMESTAMP: "timestamp",
    TypeName.TIMESTAMP_TZ: "timestamp",
}

_PATTERN = re.compile(r"^\s*([a-z_]+)\s*(?:\((.*)\)|<(.*)>)?\s*$")


@dataclass(frozen=True)
class LogicalType:
    name: TypeName
    params: tuple[int, ...] = ()
    element: LogicalType | None = None

    @property
    def family(self) -> str:
        return FAMILIES.get(self.name, self.name.value)

    def __str__(self) -> str:
        if self.element is not None:
            return f"{self.name}<{self.element}>"
        if self.params:
            return f"{self.name}({','.join(str(p) for p in self.params)})"
        return self.name.value


def parse_type(text: str) -> LogicalType:
    """Parse ``decimal(10,2)``, ``string``, ``array<bigint>`` and friends. Raises ValueError."""
    match = _PATTERN.match(text.lower())
    if not match:
        raise ValueError(f"cannot parse logical type {text!r}")
    raw_name, raw_params, raw_element = match.groups()
    try:
        name = TypeName(raw_name)
    except ValueError:
        allowed = ", ".join(t.value for t in TypeName)
        raise ValueError(f"unknown logical type {raw_name!r}; use one of: {allowed}") from None

    if name is TypeName.ARRAY:
        if raw_element is None:
            raise ValueError("array needs an element type, for example array<string>")
        return LogicalType(name, element=parse_type(raw_element))
    if raw_element is not None:
        raise ValueError(f"{name} does not take an element type")

    params: tuple[int, ...] = ()
    if raw_params is not None:
        try:
            params = tuple(int(p) for p in raw_params.split(","))
        except ValueError:
            raise ValueError(f"type parameters must be integers in {text!r}") from None
    low, high = _PARAMS.get(name, (0, 0))
    if not low <= len(params) <= high:
        raise ValueError(f"{name} takes {high} parameter(s) at most, got {len(params)} in {text!r}")
    if name is TypeName.DECIMAL and len(params) == 2 and params[1] > params[0]:
        raise ValueError(f"decimal scale cannot exceed precision in {text!r}")
    return LogicalType(name, params)
