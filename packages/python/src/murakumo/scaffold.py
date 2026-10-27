"""``murakumo init``: create a new project folder."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from murakumo.ir.model import (
    Attribute,
    Cardinality,
    Classification,
    Entity,
    EntityKind,
    History,
    JoinKey,
    KeyRole,
    Layer,
    LoadPattern,
    Mapping,
    Metric,
    Model,
    QualityRule,
    Relationship,
    RuleType,
    SourceSystem,
    SourceType,
)
from murakumo.manifest import MANIFEST_FILE, Manifest
from murakumo.project import Project, save_model

MANIFEST_TEMPLATE = """\
# Murakumo project manifest. Every section is optional except project.name.
# Reference: https://github.com/murakumo-data/murakumo
version: 1

project:
  name: {name}
  domains: [mobility]

targets:
  warehouse:
    type: duckdb               # snowflake | databricks | bigquery | redshift | fabric | synapse | postgres | duckdb
    database: {name}.duckdb
  environments: [dev, prod]
  # lakehouse:
  #   table_format: iceberg    # iceberg | delta | hudi | native
  #   catalog: polaris         # unity | polaris | glue | nessie
  #   storage: s3://my-lake/

modelling:
  pattern: kimball             # kimball | datavault | obt | 3nf | mixed
  default_scd: 2
  surrogate_keys: hash

generation:
  transform: dbt
  orchestrator: airflow
"""

GITIGNORE = """\
# Local warehouse files and secrets
*.duckdb
*.duckdb.wal
.env
"""

CUSTOM_README = """\
Hand-written code lives here. Murakumo never overwrites or deletes anything in this folder.
"""

GENERATED_README = """\
Everything in this folder is written by `murakumo generate`. Do not edit it by hand:
change the model under model/ instead, or put custom code in custom/.
"""


class ProjectExistsError(Exception):
    pass


def project_name(path: Path) -> str:
    """A valid project name from a folder name: my-platform -> my_platform."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", path.resolve().name).strip("_").lower() or "my_project"
    return f"p_{name}" if name[0].isdigit() else name


def init_project(path: Path, name: str | None = None, example: bool = True) -> Project:
    """Create a project at ``path``. The folder may exist but must not already hold a manifest."""
    path = path.resolve()
    if (path / MANIFEST_FILE).exists():
        raise ProjectExistsError(f"{path / MANIFEST_FILE} already exists")
    name = name or project_name(path)
    text = MANIFEST_TEMPLATE.format(name=name)
    manifest = Manifest.model_validate(yaml.safe_load(text))

    path.mkdir(parents=True, exist_ok=True)
    (path / MANIFEST_FILE).write_text(text, encoding="utf-8")
    (path / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    for folder, readme in (("custom", CUSTOM_README), ("generated", GENERATED_README)):
        (path / folder).mkdir(exist_ok=True)
        (path / folder / "README.md").write_text(readme, encoding="utf-8")
    (path / "model").mkdir(exist_ok=True)

    project = Project(root=path, manifest=manifest, model=example_model() if example else Model())
    save_model(project)
    return project


def example_model() -> Model:
    """A small NYC taxi model: two raw tables, a dimension, a fact and a metric."""
    nyc_tlc = SourceSystem(
        name="nyc_tlc",
        type=SourceType.FILE,
        description="NYC Taxi & Limousine Commission trip records, published monthly as Parquet.",
        connection="https://d37ci6vzurychx.cloudfront.net/trip-data/",
        schedule="monthly",
    )
    money = {"unit": "USD", "classification": Classification.CONFIDENTIAL}

    trips = Entity(
        name="yellow_tripdata",
        source="nyc_tlc",
        domain="mobility",
        description="One row per completed yellow taxi trip.",
        load=LoadPattern.APPEND,
        attributes=[
            Attribute(
                name="VendorID",
                type="integer",
                description="TPEP provider that supplied the record.",
                accepted_values={"1": "Creative Mobile Technologies", "2": "Curb Mobility"},
            ),
            Attribute(name="tpep_pickup_datetime", type="timestamp", nullable=False, description="Meter engaged."),
            Attribute(name="tpep_dropoff_datetime", type="timestamp", nullable=False, description="Meter disengaged."),
            Attribute(name="PULocationID", type="integer", description="Pickup taxi zone."),
            Attribute(name="DOLocationID", type="integer", description="Drop-off taxi zone."),
            Attribute(name="trip_distance", type="decimal(10,2)", unit="miles", description="Trip distance."),
            Attribute(
                name="total_amount", type="decimal(10,2)", description="Total charged to the passenger.", **money
            ),
        ],
    )
    zones = Entity(
        name="taxi_zone_lookup",
        source="nyc_tlc",
        domain="mobility",
        description="The 265 TLC taxi zones.",
        attributes=[
            Attribute(name="LocationID", type="integer", nullable=False, key=KeyRole.PRIMARY),
            Attribute(name="Borough", type="string"),
            Attribute(name="Zone", type="string"),
            Attribute(name="service_zone", type="string"),
        ],
    )
    dim_zone = Entity(
        name="dim_taxi_zone",
        layer=Layer.MARTS,
        kind=EntityKind.DIMENSION,
        domain="mobility",
        history=History.SCD1,
        description="Taxi zones with their borough and service area.",
        attributes=[
            Attribute(
                name="zone_id", type="integer", nullable=False, key=KeyRole.PRIMARY, tests=["unique", "not_null"]
            ),
            Attribute(name="borough", type="string"),
            Attribute(name="zone_name", type="string"),
            Attribute(name="service_zone", type="string"),
        ],
        mappings=[
            Mapping(target="zone_id", sources=["taxi_zone_lookup.LocationID"]),
            Mapping(target="borough", sources=["taxi_zone_lookup.Borough"]),
            Mapping(target="zone_name", sources=["taxi_zone_lookup.Zone"]),
            Mapping(target="service_zone", sources=["taxi_zone_lookup.service_zone"]),
        ],
    )
    trip_columns = ["VendorID", "tpep_pickup_datetime", "tpep_dropoff_datetime", "PULocationID", "DOLocationID"]
    fct_trip = Entity(
        name="fct_trip",
        layer=Layer.MARTS,
        kind=EntityKind.FACT,
        domain="mobility",
        description="One row per yellow taxi trip, linked to its pickup and drop-off zones.",
        grain=["trip_id"],
        load=LoadPattern.INCREMENTAL,
        watermark="pickup_at",
        partition_by=["day(pickup_at)"],
        attributes=[
            Attribute(
                name="trip_id", type="string", nullable=False, key=KeyRole.SURROGATE, tests=["unique", "not_null"]
            ),
            Attribute(name="pickup_at", type="timestamp", nullable=False),
            Attribute(name="dropoff_at", type="timestamp", nullable=False),
            Attribute(name="pickup_zone_id", type="integer"),
            Attribute(name="dropoff_zone_id", type="integer"),
            Attribute(
                name="trip_distance_miles",
                type="decimal(10,2)",
                unit="miles",
                tests=[QualityRule(type=RuleType.RANGE, min=0, severity="warn")],
            ),
            Attribute(name="total_amount", type="money", **money),
        ],
        mappings=[
            Mapping(
                target="trip_id",
                sources=[f"yellow_tripdata.{c}" for c in trip_columns],
                expression="md5(concat_ws('|', " + ", ".join(trip_columns) + "))",
            ),
            Mapping(target="pickup_at", sources=["yellow_tripdata.tpep_pickup_datetime"]),
            Mapping(target="dropoff_at", sources=["yellow_tripdata.tpep_dropoff_datetime"]),
            Mapping(target="pickup_zone_id", sources=["yellow_tripdata.PULocationID"]),
            Mapping(target="dropoff_zone_id", sources=["yellow_tripdata.DOLocationID"]),
            Mapping(target="trip_distance_miles", sources=["yellow_tripdata.trip_distance"]),
            Mapping(target="total_amount", sources=["yellow_tripdata.total_amount"]),
        ],
    )

    def zone_link(name: str, source: str, column: str, target: str, key: str, role: str) -> Relationship:
        return Relationship(
            name=name,
            from_=source,
            to=target,
            cardinality=Cardinality.MANY_TO_ONE,
            optional=True,
            role=role,
            keys=[JoinKey(from_=column, to=key)],
        )

    return Model(
        sources=[nyc_tlc],
        entities=[trips, zones, dim_zone, fct_trip],
        relationships=[
            zone_link(
                "trip_pickup_zone", "yellow_tripdata", "PULocationID", "taxi_zone_lookup", "LocationID", "pickup"
            ),
            zone_link(
                "trip_dropoff_zone", "yellow_tripdata", "DOLocationID", "taxi_zone_lookup", "LocationID", "dropoff"
            ),
            zone_link("fct_trip_pickup_zone", "fct_trip", "pickup_zone_id", "dim_taxi_zone", "zone_id", "pickup"),
            zone_link("fct_trip_dropoff_zone", "fct_trip", "dropoff_zone_id", "dim_taxi_zone", "zone_id", "dropoff"),
        ],
        metrics=[
            Metric(
                name="total_revenue",
                entity="fct_trip",
                expression="sum(total_amount)",
                grain="day",
                dimensions=["dim_taxi_zone.borough"],
                description="Total charged to passengers, excluding cash tips.",
            )
        ],
    )
