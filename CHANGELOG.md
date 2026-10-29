# Changelog

## 0.3.0 (2026-10-29)

Running the project: schedules, pipelines for the orchestrator, plain SQL, pipeline docs and CI.

- **Schedules**: `orchestration.schedules` in `murakumo.yaml` says when each part of the project is
  refreshed, as cron times in a chosen time zone, selecting tables by `domain:`, `layer:`,
  `source:` or name (with `+` for upstream or downstream); one schedule can refresh the rest.
  Each becomes a dbt selector. `murakumo add/rename/remove schedule`, `murakumo schedules`,
  `murakumo build --schedule`, checks MK601-MK604, and a Schedules panel in the canvas that says
  each time in words with its next run and highlights its tables.
- **Pipelines**: `generate` writes a pipeline per schedule for the project's orchestrator to
  `generated/orchestration/<orchestrator>/`: Airflow and Cloud Composer DAGs (Cosmos), Dagster
  definitions (dagster-dbt), Prefect flows and deployments, a Databricks asset bundle, Snowflake
  tasks, and a run script with steps for AWS Glue, Step Functions, GCP Workflows and Azure Data
  Factory; each with a README. The Airflow, Dagster and Prefect ones were run on DuckDB.
  `murakumo setup` points at them.
- **Plain SQL**: `murakumo export sql` writes the project as ordered scripts that need only the
  warehouse, with incremental models and SCD2 snapshots written out as statements and every test
  as a query; they build the same tables as dbt. `generation.plain_sql: true` keeps them in
  `generated/sql/`.
- **Pipeline docs**: `generate` writes `generated/docs/index.html`, a self-contained page of
  domains, schedules, sources and every table's columns, lineage and tests, and a Markdown page per
  table. `murakumo docs` and **Docs** in the canvas open it.
- **CI**: `murakumo ci` writes a GitHub Actions or GitLab workflow at the repository root that runs
  `validate --strict`, `generate --check`, `migrate --check` and `verify` on every change, with an
  optional production build after approval; it never overwrites a file changed by hand without
  `--force`. A CI panel in the canvas writes it and runs the same checks locally.
- `python -m murakumo` runs the CLI.

## 0.2.0 (2026-10-29)

Working on the model in the canvas: sources, calculated columns, column order, domains and lineage.

- **Sources panel**: lists each source with where it is read from and its tables; **+ Add source**
  declares one in `murakumo.yaml` (keeping its comments): a database by the name of an environment
  variable holding its connection string, Snowflake by its database name, or a dictionary, data
  file or OpenAPI spec by path or URL. **Browse tables** lists a database's schemas, tables and
  columns from its catalogue (never its rows) to tick the ones to import, after a preview of what
  would change; other sources get **Re-import** with the same preview. Errors never show
  passwords. `murakumo import database --table` imports only some tables; `murakumo remove
  source` removes a source with no tables.
- **Calculated columns**: an expression editor with syntax highlighting, suggestions for tables,
  columns and functions as you type, live checks (the columns it reads, whether they exist, the
  type it returns, its SQL in your warehouse's dialect) and a preview on made-up rows in DuckDB.
  **+ Calculated** adds a column and its expression in one step; saving an expression sets the
  mapping's sources to the columns it reads.
- **Column order**: drag columns in the inspector (or Alt+↑/↓), or `murakumo move column`.
  Generated tables and models follow; existing tables keep their order, which the Changes panel
  and `murakumo migrate` say.
- **Domains**: a Domains panel to add, rename (entities follow), give owners to and remove
  domains; **Show domains** colours tables and shades each domain's area; a domain filter; the
  inspector picks an entity's domain from a list. `murakumo add/rename/remove domain`. Each domain
  with models becomes a dbt group owned by its owner, and models get `group` and `meta.domain`.
- **Lineage**: `murakumo lineage entity.column` shows where a column comes from (or, with
  `--down`, what uses it), as a tree or JSON. On the canvas, **⇄** beside a column (or
  **Lineage** for a table) highlights its lineage across tables with animated links that show
  their expressions, fades the rest, and lists the columns it comes from and feeds, to follow
  further. dbt columns carry `meta.lineage`.
- The API gains endpoints for column order, expressions, lineage, domains and sources; see
  [the canvas](docs/canvas.md#the-api).

## 0.1.1 (2026-10-29)

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
