"""The canonical metadata model (IR v1).

Every generator compiles from these objects. The canvas edits them, importers create them,
and the project stores them as YAML under ``model/``.

Rules carried over from the brief:
- every object has a stable UUID, so a rename is a rename, not a drop and an add;
- logical types are separate from physical types;
- references between objects use names, which are what people read and write in YAML;
- every object accepts custom ``properties`` for team-specific metadata.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from murakumo.ir.types import LogicalType, parse_type

IR_VERSION = 1

Name = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]


class IRModel(BaseModel):
    """Base for every IR object: unknown keys are errors, so typos in YAML never pass silently."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=False)


class Node(IRModel):
    """An IR object with a stable identity."""

    id: UUID = Field(default_factory=uuid4)
    description: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


# Enumerations -----------------------------------------------------------------------------------


class Layer(StrEnum):
    RAW = "raw"
    STAGING = "staging"
    INTERMEDIATE = "intermediate"
    MARTS = "marts"
    SEMANTIC = "semantic"


class EntityKind(StrEnum):
    TABLE = "table"
    FACT = "fact"
    DIMENSION = "dimension"
    BRIDGE = "bridge"
    HUB = "hub"
    LINK = "link"
    SATELLITE = "satellite"
    EVENT = "event"
    CUMULATIVE = "cumulative"
    REFERENCE = "reference"


class History(StrEnum):
    NONE = "none"
    SCD1 = "scd1"
    SCD2 = "scd2"
    SCD3 = "scd3"
    SCD4 = "scd4"
    SCD6 = "scd6"
    SNAPSHOT = "snapshot"


class LoadPattern(StrEnum):
    FULL = "full"
    APPEND = "append"
    INCREMENTAL = "incremental"
    CDC = "cdc"
    STREAMING = "streaming"


class KeyRole(StrEnum):
    PRIMARY = "primary"
    NATURAL = "natural"
    SURROGATE = "surrogate"


class Classification(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class Cardinality(StrEnum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_ONE = "many_to_one"
    MANY_TO_MANY = "many_to_many"


class RuleType(StrEnum):
    UNIQUE = "unique"
    NOT_NULL = "not_null"
    ACCEPTED_VALUES = "accepted_values"
    RANGE = "range"
    UNIQUE_COMBINATION = "unique_combination"
    FRESHNESS = "freshness"
    CUSTOM_SQL = "custom_sql"


class Severity(StrEnum):
    ERROR = "error"
    WARN = "warn"


class SourceType(StrEnum):
    DATABASE = "database"
    FILE = "file"
    API = "api"
    STREAM = "stream"
    MANUAL = "manual"


# Objects ----------------------------------------------------------------------------------------


class QualityRule(IRModel):
    """A data test. Column-level rules sit on an attribute; table-level rules sit on an entity."""

    type: RuleType
    columns: list[Name] = Field(default_factory=list)
    severity: Severity = Severity.ERROR
    values: list[str] | None = None
    min: float | None = None
    max: float | None = None
    sql: str | None = None
    max_age_hours: float | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _check_arguments(self) -> QualityRule:
        if self.type is RuleType.RANGE and self.min is None and self.max is None:
            raise ValueError("a range rule needs min, max or both")
        if self.type is RuleType.CUSTOM_SQL and not self.sql:
            raise ValueError("a custom_sql rule needs sql")
        if self.type is RuleType.FRESHNESS and self.max_age_hours is None:
            raise ValueError("a freshness rule needs max_age_hours")
        if self.type is RuleType.UNIQUE_COMBINATION and len(self.columns) < 2:
            raise ValueError("a unique_combination rule needs two or more columns")
        return self


def _shorthand_rule(value: Any) -> Any:
    """Allow ``tests: [unique, not_null]`` as well as full rule objects."""
    return {"type": value} if isinstance(value, str) else value


class Attribute(Node):
    """A column of an entity."""

    name: Name
    type: str
    nullable: bool = True
    key: KeyRole | None = None
    default: str | None = None
    physical_types: dict[str, str] = Field(default_factory=dict)
    source_type: str | None = None
    classification: Classification | None = None
    pii: bool = False
    accepted_values: dict[str, str | None] | None = None
    unit: str | None = None
    example: str | None = None
    business_term: str | None = None
    tests: list[QualityRule] = Field(default_factory=list)

    @field_validator("type")
    @classmethod
    def _check_type(cls, value: str) -> str:
        return str(parse_type(value))

    @field_validator("accepted_values", mode="before")
    @classmethod
    def _normalise_accepted_values(cls, value: Any) -> Any:
        """Accept a list of codes or a mapping of code to label; keys become strings."""
        if value is None:
            return None
        if isinstance(value, list):
            return {str(v): None for v in value}
        if isinstance(value, dict):
            return {str(k): (None if v is None else str(v)) for k, v in value.items()}
        return value

    @field_validator("tests", mode="before")
    @classmethod
    def _expand_tests(cls, value: Any) -> Any:
        return [_shorthand_rule(v) for v in value] if isinstance(value, list) else value

    @property
    def logical_type(self) -> LogicalType:
        return parse_type(self.type)


class Mapping(IRModel):
    """How one target attribute is derived: from source columns, an expression, or both.

    Source columns are written ``entity.column``. The expression is SQL in the generic dialect.
    """

    target: Name
    sources: list[str] = Field(default_factory=list)
    expression: str | None = None

    @field_validator("sources")
    @classmethod
    def _check_sources(cls, value: list[str]) -> list[str]:
        for ref in value:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", ref):
                raise ValueError(f"source {ref!r} must be written entity.column")
        return value

    @model_validator(mode="after")
    def _needs_input(self) -> Mapping:
        if not self.sources and not self.expression:
            raise ValueError(f"mapping for {self.target!r} needs sources, an expression or both")
        return self


class Freshness(IRModel):
    warn_after_hours: float | None = None
    error_after_hours: float | None = None


class Cumulative(IRModel):
    """How a cumulative table accumulates: one row per key per day, carrying its whole history.

    ``date`` is the source column whose day a row counts for (``fct_trip.pickup_at``); each
    measure is an aggregate over that day's rows (``count(*)``, ``sum(fct_trip.total_amount)``),
    kept as an array with one value per active day, most recent first.
    """

    date: str
    measures: dict[Name, str] = Field(default_factory=dict)

    @field_validator("date")
    @classmethod
    def _qualified(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError(f"cumulative date {value!r} must be written entity.column")
        return value


class Entity(Node):
    """A table in the logical model: a source table, a staging model, a dimension, a fact..."""

    name: Name
    layer: Layer = Layer.RAW
    kind: EntityKind = EntityKind.TABLE
    source: Name | None = None
    domain: Name | None = None
    owner: str | None = None
    grain: list[Name] = Field(default_factory=list)
    history: History = History.NONE
    load: LoadPattern = LoadPattern.FULL
    watermark: Name | None = None
    partition_by: list[str] = Field(default_factory=list)
    cluster_by: list[Name] = Field(default_factory=list)
    freshness: Freshness | None = None
    cumulative: Cumulative | None = None  # for kind: cumulative
    tags: list[str] = Field(default_factory=list)
    attributes: list[Attribute] = Field(default_factory=list)
    mappings: list[Mapping] = Field(default_factory=list)
    tests: list[QualityRule] = Field(default_factory=list)

    @field_validator("tests", mode="before")
    @classmethod
    def _expand_tests(cls, value: Any) -> Any:
        return [_shorthand_rule(v) for v in value] if isinstance(value, list) else value

    def attribute(self, name: str) -> Attribute | None:
        lowered = name.lower()
        return next((a for a in self.attributes if a.name.lower() == lowered), None)

    @property
    def primary_key(self) -> list[Attribute]:
        return [a for a in self.attributes if a.key is KeyRole.PRIMARY]


class JoinKey(IRModel):
    from_: Name = Field(alias="from")
    to: Name

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Relationship(Node):
    """A link between two entities. ``from`` is usually the many side (the foreign key holder)."""

    name: Name | None = None
    from_: Name = Field(alias="from")
    to: Name
    cardinality: Cardinality = Cardinality.MANY_TO_ONE
    optional: bool = False
    role: str | None = None
    keys: list[JoinKey] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid", validate_assignment=True, populate_by_name=True)


class SourceSystem(Node):
    """Where raw data comes from, and how it is loaded."""

    name: Name
    type: SourceType = SourceType.DATABASE
    connection: str | None = None
    owner: str | None = None
    load_tool: str | None = None
    schedule: str | None = None
    freshness: Freshness | None = None
    tags: list[str] = Field(default_factory=list)


class Metric(Node):
    name: Name
    entity: Name
    expression: str
    grain: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)


class Model(IRModel):
    """The whole logical model of a project."""

    sources: list[SourceSystem] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)

    def entity(self, name: str) -> Entity | None:
        lowered = name.lower()
        return next((e for e in self.entities if e.name.lower() == lowered), None)

    def source(self, name: str) -> SourceSystem | None:
        lowered = name.lower()
        return next((s for s in self.sources if s.name.lower() == lowered), None)
