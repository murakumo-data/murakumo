"""The project manifest, ``murakumo.yaml``: one file that says where to build and how.

Every section has defaults, so a minimal manifest is just ``version`` and ``project.name``.
Unknown keys are errors, so a typo such as ``orchestartor`` is caught by ``murakumo validate``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from murakumo.ir.model import Name

MANIFEST_FILE = "murakumo.yaml"
MANIFEST_VERSION = 1


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Warehouse(StrEnum):
    SNOWFLAKE = "snowflake"
    DATABRICKS = "databricks"
    BIGQUERY = "bigquery"
    REDSHIFT = "redshift"
    FABRIC = "fabric"
    SYNAPSE = "synapse"
    POSTGRES = "postgres"
    DUCKDB = "duckdb"


class TableFormat(StrEnum):
    ICEBERG = "iceberg"
    DELTA = "delta"
    HUDI = "hudi"
    NATIVE = "native"


class Catalog(StrEnum):
    POLARIS = "polaris"
    UNITY = "unity"
    GLUE = "glue"
    NESSIE = "nessie"
    NONE = "none"


class Orchestrator(StrEnum):
    AIRFLOW = "airflow"
    DAGSTER = "dagster"
    PREFECT = "prefect"
    DATABRICKS_WORKFLOWS = "databricks_workflows"
    SNOWFLAKE_TASKS = "snowflake_tasks"
    AWS_GLUE = "aws_glue"
    AWS_STEP_FUNCTIONS = "aws_step_functions"
    GCP_COMPOSER = "gcp_composer"
    GCP_WORKFLOWS = "gcp_workflows"
    AZURE_DATA_FACTORY = "azure_data_factory"


class ModellingPattern(StrEnum):
    KIMBALL = "kimball"
    DATAVAULT = "datavault"
    OBT = "obt"
    THIRD_NORMAL_FORM = "3nf"
    MIXED = "mixed"


class Domain(Section):
    name: Name
    owner: str | None = None
    description: str | None = None


class ProjectInfo(Section):
    name: Name
    owner: str | None = None
    description: str | None = None
    domains: list[Domain] = Field(default_factory=list)

    @field_validator("domains", mode="before")
    @classmethod
    def _domain_shorthand(cls, value: Any) -> Any:
        """``domains: [sales, finance]`` is shorthand for a list of domain objects."""
        if isinstance(value, list):
            return [{"name": v} if isinstance(v, str) else v for v in value]
        return value


class WarehouseTarget(Section):
    type: Warehouse = Warehouse.DUCKDB
    database: str | None = None
    connection: str | None = None


class Partitioning(Section):
    min_table_size_gb: float = 1
    default_grain: str = "day"
    target_file_mb: int = 512


class Maintenance(Section):
    compaction: str = "daily"
    expire_snapshots_days: int = 7
    orphan_cleanup: str = "weekly"


class LakehouseTarget(Section):
    table_format: TableFormat = TableFormat.ICEBERG
    catalog: Catalog = Catalog.NONE
    storage: str | None = None
    system_of_record: str = Field(default="lakehouse", pattern="^(lakehouse|warehouse)$")
    zones: list[str] = Field(default_factory=lambda: ["landing", "bronze", "silver", "gold"])
    path_template: str = "{zone}/{domain}/{entity}/"
    partitioning: Partitioning = Field(default_factory=Partitioning)
    maintenance: Maintenance = Field(default_factory=Maintenance)
    native_warehouse_tables: list[str] = Field(default_factory=list)


class Targets(Section):
    warehouse: WarehouseTarget = Field(default_factory=WarehouseTarget)
    lakehouse: LakehouseTarget | None = None
    environments: list[str] = Field(default_factory=lambda: ["dev", "prod"])


class Ingestion(Section):
    tool: str | None = None
    load: str | None = None
    watermark: str | None = None
    schedule: str | None = None


class SourceDeclaration(Section):
    """Where to import a source's metadata from: ``csv:path``, ``excel:path``, ``introspect:url``..."""

    name: Name
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str | None = Field(default=None, alias="from")
    ingestion: Ingestion = Field(default_factory=Ingestion)


class Modelling(Section):
    pattern: ModellingPattern = ModellingPattern.KIMBALL
    layers: list[str] = Field(default_factory=lambda: ["raw", "staging", "intermediate", "marts"])
    default_scd: int = Field(default=2, ge=0, le=6)
    surrogate_keys: str = Field(default="hash", pattern="^(hash|sequence)$")
    cumulative_tables: bool = False


class Conventions(Section):
    naming: dict[str, str] = Field(
        default_factory=lambda: {
            "staging": "stg_{source}__{entity}",
            "intermediate": "int_{name}",
            "fact": "fct_{name}",
            "dimension": "dim_{name}",
        }
    )
    case: str = Field(default="snake", pattern="^(snake|preserve)$")
    audit_columns: list[str] = Field(
        default_factory=lambda: ["_ingested_at", "_batch_id", "_source_system", "_record_hash", "_updated_at"]
    )


class Streaming(Section):
    engine: str = "flink"
    registry_compatibility: str = "backward"


class Schedule(Section):
    """When a part of the project is refreshed. With no ``select``, it runs everything no other
    schedule picks."""

    name: Name
    cron: str
    select: list[str] = Field(default_factory=list)
    description: str | None = None

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, value: str) -> str:
        from murakumo.cron import CronError, check

        try:
            return check(value)
        except CronError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("select", mode="before")
    @classmethod
    def _one_or_many(cls, value: Any) -> Any:
        return [value] if isinstance(value, str) else value


class Orchestration(Section):
    timezone: str = "UTC"
    schedules: list[Schedule] = Field(default_factory=list)

    @field_validator("timezone")
    @classmethod
    def _valid_zone(cls, value: str) -> str:
        from murakumo.cron import CronError, zone

        try:
            zone(value)
        except CronError as exc:
            raise ValueError(str(exc)) from None
        return value

    def schedule(self, name: str) -> Schedule | None:
        return next((s for s in self.schedules if s.name.lower() == name.lower()), None)


class Generation(Section):
    transform: str = Field(default="dbt", pattern="^(dbt|sqlmesh|spark)$")
    python_engine: str = Field(default="none", pattern="^(pyspark|polars|none)$")
    orchestrator: Orchestrator = Orchestrator.AIRFLOW
    streaming: Streaming | None = None
    infrastructure: str | None = None
    semantic_layer: str | None = None


class Quality(Section):
    framework: list[str] = Field(default_factory=lambda: ["dbt_tests"])
    default_tests: list[str] = Field(default_factory=lambda: ["unique_pk", "not_null_pk", "relationships"])
    reconciliation: bool = False
    write_audit_publish: bool = False


class Contracts(Section):
    standard: str = "odcs"
    enforce_on: list[str] = Field(default_factory=lambda: ["marts"])
    breaking_change: str = Field(default="block", pattern="^(block|warn|version)$")


class Governance(Section):
    classification_tags: list[str] = Field(default_factory=lambda: ["pii", "confidential"])
    masking: dict[str, str] = Field(default_factory=dict)
    retention_days: dict[str, int] = Field(default_factory=dict)
    lineage: str | None = "openlineage"
    catalog_sync: str | None = None


class ChangePropagation(Section):
    staging: str = Field(default="auto", pattern="^(auto|suggest|locked)$")
    intermediate: str = Field(default="suggest", pattern="^(auto|suggest|locked)$")
    marts: str = Field(default="suggest", pattern="^(auto|suggest|locked)$")
    contracted: str = Field(default="locked", pattern="^(auto|suggest|locked)$")
    source_drift: str = Field(default="quarantine", pattern="^(quarantine|auto_add|fail)$")


class Manifest(Section):
    version: int = MANIFEST_VERSION
    project: ProjectInfo
    targets: Targets = Field(default_factory=Targets)
    sources: list[SourceDeclaration] = Field(default_factory=list)
    modelling: Modelling = Field(default_factory=Modelling)
    conventions: Conventions = Field(default_factory=Conventions)
    generation: Generation = Field(default_factory=Generation)
    orchestration: Orchestration = Field(default_factory=Orchestration)
    quality: Quality = Field(default_factory=Quality)
    contracts: Contracts = Field(default_factory=Contracts)
    governance: Governance = Field(default_factory=Governance)
    change_propagation: ChangePropagation = Field(default_factory=ChangePropagation)
    packs: list[str] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def _supported_version(cls, value: int) -> int:
        if value != MANIFEST_VERSION:
            raise ValueError(f"this version of murakumo reads manifest version {MANIFEST_VERSION}, not {value}")
        return value

    def domain(self, name: str) -> Domain | None:
        return next((d for d in self.project.domains if d.name.lower() == name.lower()), None)
