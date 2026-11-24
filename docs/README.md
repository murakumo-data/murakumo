# Murakumo documentation

Murakumo is **model-first data engineering**. You design the logical model (the entities, how
they relate, and where each column comes from) on a canvas or in YAML files, choose your stack,
and Murakumo writes everything downstream: the DDL, a dbt project, PySpark or Polars jobs,
streaming jobs, tests, data contracts, audit tables, orchestration, CI and Terraform.

Everything it writes is ordinary code in your repository, and the
[replication guide](guide.md) says how to build all of it by hand, so you can stop using Murakumo
at any time and lose nothing.

```bash
pip install "murakumo[canvas,verify,duckdb]"
murakumo init my_platform
murakumo canvas my_platform
```

## Start here

- [Install and set up](setup.md): installing, `murakumo init`, and what each machine needs.
- [The end-to-end test](end-to-end-test.md): every feature, step by step, from an empty folder
  to a running platform. The quickest way to see it all.

## Design the model

- [Model files](model.md): the YAML that holds the model, and every rule it is checked against.
- [Getting metadata in](importing.md): from databases, data dictionaries, files, APIs, protobuf,
  an existing dbt project, or a data contract.
- [The canvas](canvas.md): the visual editor, served by `murakumo canvas`.
- [Modelling patterns](modelling-patterns.md): SCD types, Data Vault, CDC, masking and row
  access, the semantic layer.

## Build the platform

- [Generating and verifying](generating.md): what `generate`, `verify` and `build` do.
- [Choosing the stack](stack.md): warehouses, sources, streaming, open tables, Terraform, deploying.
- [Snowflake](snowflake.md): one-time setup, importing, environments and platform settings.
- [Schedules, pipelines and CI](orchestration.md): Airflow, Dagster, Prefect and seven more.

## Run it

- [Quality, contracts and the query console](operating.md): quality rules, quarantine, data
  contracts, exploring the raw data before modelling it, monitoring, cost, lineage and Slim CI.
- [Working as a team](teamwork.md): staying in sync, environments, your own schema, the git
  flow, and testing a change before it is merged.
- [The assistant and MCP](assistant.md): optional, and off until you turn it on.

## Without Murakumo

- [The replication guide](guide.md): how to build the whole platform by hand.
- [Against established practice](practices.md): what Murakumo writes, measured against Kimball,
  dbt and Snowflake practice.
