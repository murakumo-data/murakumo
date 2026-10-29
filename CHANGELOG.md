# Changelog

## 0.1.1 (unreleased)

Fixes and polish from the first end-to-end test.

- **Build**: `murakumo build` builds the project into its own warehouse (the DuckDB file, or the
  Snowflake database) and keeps the tables, unlike `verify`, which drops everything afterwards.
  `--sample N` fills empty raw tables with made-up rows, `--data` loads files, `--env` picks the
  environment.
- **Snowflake**: when the database does not exist, `setup --connect` prints the SQL that creates it
  as a block to paste into a Snowsight worksheet, with where to run it.
- **`init`**: numbered choices with a description of each; a clearer question about the example
  model; **Ready.** in green and **Next:** with the `cd` to run; a pointer to `murakumo --help`.
- **`murakumo config`** changes the warehouse, orchestrator or modelling pattern after `init`,
  keeping the manifest's comments.
- **Setup checklist**: a key to the symbols; numbered steps for installing the chosen orchestrator
  and pointing it at the dbt project, with links to its documentation (and what to set up in the
  cloud for managed ones); checks for the macOS developer tools and git's name and email; projects
  inside an existing repository are recognised; plainer explanations in the Project section.
- **Canvas**:
  - relationships in crow's-foot notation, with a **Notation & tips** legend; labels show on
    hover; hovering or selecting a line highlights the joined columns;
  - lines no longer disappear behind tables dragged one above the other;
  - Shift+drag selects several tables to move them together;
  - the inspector can be resized, has a column search, a **+ Relationship** form, and
    **+ From another table** to add columns by ticking them; mapping sources can be ticked in a
    list;
  - one button writes the code, or the migration and the code together, with plainer messages
    (*Wrote 12 files to generated/*).
- The manifest template lists storage locations for each cloud and a local folder.

## 0.1.0 (2026-10-27)

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
