"""The project manifest, ``murakumo.yaml``: one file that says where to build and how.

Every section has defaults, so a minimal manifest is just ``version`` and ``project.name``.
Unknown keys are errors, so a typo such as ``orchestartor`` is caught by ``murakumo validate``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

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
    # Snowflake platform settings. Tables in these layers can be rebuilt from raw, so they are transient:
    # no fail-safe, and so no storage paid for it.
    transient_layers: list[str] = Field(default_factory=lambda: ["staging", "intermediate"])
    # Days of time travel per layer (raw: 7, marts: 30); a layer not listed keeps Snowflake's default of 1.
    time_travel_days: dict[str, int] = Field(default_factory=dict)
    # Credits the compute warehouse may use a month: notified at 75% and 90%, suspended at 100%.
    monthly_credits: int | None = Field(default=None, ge=1)

    @field_validator("time_travel_days")
    @classmethod
    def _days(cls, value: dict[str, int]) -> dict[str, int]:
        for layer, days in value.items():
            if not 0 <= days <= 90:
                raise ValueError(f"time travel for {layer} is between 0 and 90 days, not {days}")
        return value


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
    layers: list[str] = Field(default_factory=lambda: ["marts"])  # the layers written as open tables
    catalog_uri: str | None = None  # an Iceberg REST catalog's address (Polaris, Nessie)
    warehouse: str | None = None  # the catalog's warehouse (its name in Polaris)
    external_volume: str | None = None  # Snowflake: where its Iceberg tables' files go
    system_of_record: str = Field(default="lakehouse", pattern="^(lakehouse|warehouse)$")
    zones: list[str] = Field(default_factory=lambda: ["landing", "bronze", "silver", "gold"])
    path_template: str = "{zone}/{domain}/{entity}/"
    partitioning: Partitioning = Field(default_factory=Partitioning)
    maintenance: Maintenance = Field(default_factory=Maintenance)
    native_warehouse_tables: list[str] = Field(default_factory=list)


class Environment(Section):
    """A place the project is built: dev, stage, prod. Each can have a database of its own (a
    Snowflake database, a DuckDB file) and the git branch that deploys to it."""

    name: Name
    database: str | None = None  # default: the warehouse's database (one database for every environment)
    branch: str | None = None  # merging into this branch deploys to this environment

    @field_validator("name")
    @classmethod
    def _not_personal(cls, value: str) -> str:
        if value.lower() == "personal":
            raise ValueError("'personal' is everyone's own environment; name a shared one dev, stage or prod")
        return value


class Targets(Section):
    warehouse: WarehouseTarget = Field(default_factory=WarehouseTarget)
    lakehouse: LakehouseTarget | None = None
    environments: list[Environment] = Field(default_factory=lambda: [Environment(name="dev"), Environment(name="prod")])

    @field_validator("environments", mode="before")
    @classmethod
    def _names_alone(cls, value: Any) -> Any:
        """Before 0.7 environments were names only (environments: [dev, prod]); those still load."""
        if isinstance(value, list):
            return [{"name": v} if isinstance(v, str) else v for v in value]
        return value

    @property
    def names(self) -> list[str]:
        return [e.name for e in self.environments] or ["dev"]

    def environment(self, name: str) -> Environment | None:
        return next((e for e in self.environments if e.name.lower() == name.lower()), None)


class Ingestion(Section):
    tool: str | None = None
    load: str | None = None
    watermark: str | None = None
    schedule: str | None = None


# Connections: how a source's data is fetched -----------------------------------------------------
# Secrets are never written here: each is the name of an environment variable that holds it.


class Auth(Section):
    """How a REST API knows who is asking."""

    type: str = Field(default="none", pattern="^(none|api_key|bearer|basic|oauth2)$")
    env: str | None = None  # api_key, bearer: the variable holding the key or token
    name: str = "Authorization"  # api_key: the header or query parameter it goes in
    location: str = Field(default="header", pattern="^(header|query)$")  # api_key: where it goes
    user_env: str | None = None  # basic
    password_env: str | None = None  # basic
    token_url: str | None = None  # oauth2 client credentials
    client_id_env: str | None = None
    client_secret_env: str | None = None
    scope: str | None = None


class Paging(Section):
    """How a REST API hands over a long list a page at a time."""

    type: str = Field(default="none", pattern="^(none|page|offset|cursor|link)$")
    param: str | None = None  # the page, offset or cursor parameter (page, offset, cursor)
    size_param: str | None = None  # the page-size parameter (per_page, limit)
    size: int = Field(default=100, ge=1, le=10000)
    start: int = 1  # page: the first page's number; offset starts at 0
    cursor_path: str | None = None  # cursor: where the next cursor is in the response (meta.next_cursor)
    max_pages: int | None = Field(default=None, ge=1)  # a safety stop


class Endpoint(Section):
    """One request, whose records become one raw table."""

    table: Name
    path: str = ""
    method: str = Field(default="GET", pattern="^(GET|POST)$")
    params: dict[str, str | int | float | bool] = Field(default_factory=dict)
    body: dict[str, Any] | None = None
    records: str | None = None  # where the list of records is in the response (data.items); default: all of it
    since_param: str | None = None  # incremental: this parameter gets the latest watermark already fetched


class RestConnection(Section):
    kind: Literal["rest"] = "rest"
    base_url: str
    headers: dict[str, str] = Field(default_factory=dict)
    auth: Auth = Field(default_factory=Auth)
    paging: Paging = Field(default_factory=Paging)
    requests_per_second: float | None = Field(default=None, gt=0)
    endpoints: list[Endpoint] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def _http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("a REST API's base_url starts with https:// (or http://)")
        return value.rstrip("/")


class FileSet(Section):
    """Files whose rows become one raw table."""

    table: Name
    pattern: str  # a glob under the connection's path: trips_*.csv
    format: str | None = Field(default=None, pattern="^(csv|tsv|json|jsonl|parquet|xlsx)$")  # default: by extension


class SftpConnection(Section):
    kind: Literal["sftp"] = "sftp"
    host: str
    port: int = 22
    user_env: str
    key_file_env: str | None = None  # the variable holding the private key file's path
    password_env: str | None = None
    path: str = "/"
    files: list[FileSet] = Field(default_factory=list)


class Query(Section):
    """A table (or a query) in a source database, copied into one raw table."""

    table: Name
    source: str  # schema.table in the source database, or a SELECT
    incremental_column: str | None = None  # fetch only rows where this is past the latest fetched


class DatabaseConnection(Section):
    kind: Literal["database"] = "database"
    url_env: str  # the variable holding the SQLAlchemy URL: postgresql+psycopg://user:pass@host/db
    tables: list[Query] = Field(default_factory=list)


class FilesConnection(Section):
    kind: Literal["files"] = "files"
    location: str  # a folder, or s3://bucket/prefix
    files: list[FileSet] = Field(default_factory=list)


class Message(Section):
    """A protobuf message type whose messages become one raw table."""

    table: Name
    message: str  # the message's name in the .proto file
    pattern: str  # where files of length-delimited messages arrive: events/orders_*.bin


class ProtobufConnection(Section):
    kind: Literal["protobuf"] = "protobuf"
    proto: str  # the .proto file, relative to the project
    location: str  # a folder, or s3://bucket/prefix, where the message files arrive
    messages: list[Message] = Field(default_factory=list)


class Topic(Section):
    """A Kafka topic whose messages (JSON objects) become one raw table."""

    table: Name
    topic: str


class KafkaConnection(Section):
    """Kafka, or anything that speaks its protocol (Redpanda, Amazon MSK, Confluent Cloud)."""

    kind: Literal["kafka"] = "kafka"
    bootstrap_servers: str  # host:port, comma-separated
    security_protocol: str = Field(default="PLAINTEXT", pattern="^(PLAINTEXT|SSL|SASL_PLAINTEXT|SASL_SSL)$")
    sasl_mechanism: str | None = Field(default=None, pattern="^(PLAIN|SCRAM-SHA-256|SCRAM-SHA-512)$")
    username_env: str | None = None
    password_env: str | None = None
    group: str | None = None  # the consumer group; default murakumo-<source>
    topics: list[Topic] = Field(default_factory=list)


Connection = Annotated[
    RestConnection | SftpConnection | DatabaseConnection | FilesConnection | ProtobufConnection | KafkaConnection,
    Field(discriminator="kind"),
]


class SourceDeclaration(Section):
    """A source: where to import its metadata from (``csv:path``, ``openapi:url``...), and how its
    data is fetched (``connection``)."""

    name: Name
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str | None = Field(default=None, alias="from")
    ingestion: Ingestion = Field(default_factory=Ingestion)
    connection: Connection | None = None

    def tables(self) -> list[str]:
        """The raw tables the connection fills."""
        c = self.connection
        if c is None:
            return []
        items = {
            "rest": lambda: c.endpoints,
            "sftp": lambda: c.files,
            "database": lambda: c.tables,
            "files": lambda: c.files,
            "protobuf": lambda: c.messages,
            "kafka": lambda: c.topics,
        }[c.kind]()
        return [i.table for i in items]


class Modelling(Section):
    pattern: ModellingPattern = ModellingPattern.KIMBALL
    layers: list[str] = Field(default_factory=lambda: ["raw", "staging", "intermediate", "marts"])
    default_scd: int = Field(default=2, ge=0, le=6)
    surrogate_keys: str = Field(default="hash", pattern="^(hash|sequence)$")
    cumulative_tables: bool = False
    # text: raw tables keep every column as text, exactly as it arrived; staging converts the types,
    # and a value that does not convert becomes null and is counted (a warning) instead of failing the load.
    raw_columns: str = Field(default="typed", pattern="^(typed|text)$")
    # Dimensions get an "unknown" row (key -1) that facts point at when their key is missing.
    unknown_members: bool = False


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
    # What reads the Kafka sources: the extract script on each schedule (consumer), or continuously
    # (Flink, Spark Structured Streaming, or Snowflake's Kafka connector through Snowpipe Streaming).
    engine: str = Field(default="consumer", pattern="^(consumer|flink|spark|snowpipe_streaming)$")
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


class Collaboration(Section):
    base_branch: str = "main"  # the branch people branch from and bring in with `murakumo sync`
    remote: str = "origin"
    personal_schemas: bool = True  # on a shared warehouse, everyone's own builds go to a schema of their own
    personal_database: str | None = None  # where those schemas live (MURAKUMO); default: the warehouse's database
    personal_environment: str = "personal"  # what `murakumo build` builds when no --env is given


class Generation(Section):
    transform: str = Field(default="dbt", pattern="^(dbt|sqlmesh|spark)$")
    python_engine: str = Field(default="none", pattern="^(pyspark|polars|none)$")
    orchestrator: Orchestrator = Orchestrator.AIRFLOW
    plain_sql: bool = False  # also write the project as plain SQL scripts, under generated/sql/
    guide: bool = True  # the replication guide, under generated/guide/: how to rebuild it all by hand
    streaming: Streaming = Field(default_factory=Streaming)
    infrastructure: str | None = None
    semantic_layer: str | None = None


class Stack(Section):
    """The tools the data platform is built from, beyond the warehouse, transform and orchestrator
    (in targets and generation): how data is fetched from the sources, how it is loaded, and CI."""

    extract: str = Field(default="python", pattern="^(python|glue|lambda|none)$")
    load: str = Field(default="build", pattern="^(build|snowpipe|autoloader)$")
    landing: str = "landing"  # where fetched files land: a folder in the project, or s3://bucket/prefix
    region: str | None = None  # the cloud region, for Glue, Lambda and S3 (default: AWS_REGION)
    ci: str = Field(default="github", pattern="^(github|gitlab|none)$")
    iac: str = Field(default="none", pattern="^(none|terraform)$")  # the platform as code, beside the SQL


class Quality(Section):
    framework: list[str] = Field(default_factory=lambda: ["dbt_tests"])
    default_tests: list[str] = Field(default_factory=lambda: ["unique_pk", "not_null_pk", "relationships"])
    reconciliation: bool = False
    write_audit_publish: bool = False
    run_log: bool = True  # every run records itself, its models, tests and loads in the audit schema


class Contracts(Section):
    standard: str = "odcs"
    enforce_on: list[str] = Field(default_factory=lambda: ["marts"])
    breaking_change: str = Field(default="block", pattern="^(block|warn|version)$")


class Governance(Section):
    classification_tags: list[str] = Field(default_factory=lambda: ["pii", "confidential"])
    # Masking policies (Snowflake): what a column marked pii, or with a classification, shows to
    # roles not in unmasked_roles: {pii: redact, restricted: hash, confidential: partial}.
    masking: dict[str, str] = Field(default_factory=dict)
    unmasked_roles: list[str] = Field(default_factory=list)
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
    stack: Stack = Field(default_factory=Stack)
    orchestration: Orchestration = Field(default_factory=Orchestration)
    collaboration: Collaboration = Field(default_factory=Collaboration)
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
