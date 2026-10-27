# Changelog

## 0.1.0 (unreleased)

The first release: design a data model, generate a tested dbt project and DDL from it, and keep
both in step as the model changes.

- **Projects**: `murakumo init` asks about the warehouse, orchestrator and modelling pattern (or
  takes `--target`, `--orchestrator`, `--pattern`), and starts from an NYC taxi example or empty.
  `murakumo setup` checks what the project needs on this machine (drivers, variables, keys,
  connections, orchestrator) and says how to set up each.
- **Model**: a YAML model with stable ids (sources, entities, columns, relationships, mappings,
  tests, metrics), validated with rule codes MK0xx-MK3xx; `murakumo validate`, `add`, `rename`
  (references follow) and `remove`.
- **Import**: data dictionaries (CSV, Excel), databases (PostgreSQL, DuckDB, Snowflake), files
  (Parquet, CSV, JSON), OpenAPI and JSON samples. Re-imports keep hand edits and ids.
- **Generate**: a dbt project (staging, intermediate, marts; contracts; SCD2 snapshots;
  incremental models; tests from the model) and DDL for DuckDB, Snowflake, Databricks, BigQuery,
  PostgreSQL, Redshift, Fabric and Synapse. Deterministic output, with `--check` for CI.
- **Migrations**: `murakumo migrate` turns model changes into numbered SQL migrations for tables
  that keep data (landing tables, incremental models, snapshots), matched by id so renames keep
  their data; data-losing statements are left for a person to uncomment.
- **Verify**: `murakumo verify` builds and tests the generated project on DuckDB or Snowflake,
  with synthetic rows that respect the model's keys and rules, or real files and tables.
- **Install**: `uv tool install "murakumo[canvas,verify,duckdb]"`, `pip install ...`, or
  `npm install -g murakumo` (the npm package runs the same PyPI release through uv).
- **Canvas**: `murakumo canvas` opens a visual editor that reads and writes the same YAML: entities
  and columns, drag-to-relate, mappings, the generated code for each entity, diffs of what
  generating would change, migrations, and validation, all live as files change on disk.
