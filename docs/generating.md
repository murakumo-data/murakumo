# Generating and verifying

```bash
murakumo generate                        # write generated/: a dbt project and DDL
murakumo generate --warehouse snowflake  # for another warehouse than the manifest's
murakumo generate --check                # CI: fail if generated/ is out of date
murakumo migrate                         # write the next migration for tables that keep data
murakumo migrate --check                 # CI: fail if the model changed without a migration
murakumo verify                          # build it on DuckDB and run every test, twice
murakumo build                           # build it in the project's warehouse, and keep the tables
murakumo build --schedule nightly        # only what one schedule refreshes
murakumo export sql                      # the project as plain SQL scripts, without dbt
murakumo docs                            # open the pipeline docs
murakumo runs                            # the latest runs, loads and failed tests, from the audit tables
murakumo backfill TABLE --from D --to D  # build a cumulative table day by day
murakumo run --engine polars             # the same pipeline as Polars (or PySpark) jobs
```

`generate` refuses to run while `murakumo validate` reports errors. Everything it writes goes under
`generated/`; it never touches `model/`, `custom/` or anything else. It keeps a list of the files
it wrote in `generated/.murakumo-files.json`, so when an entity is removed, its files are removed
too, while files you added yourself are left alone. If you edited a generated file by hand, the
next run overwrites it and tells you; put hand-written code in `custom/` instead.

Output is deterministic: the same model always gives byte-identical files, so `--check` in CI and
code review of `generated/` both work.

## What is generated

```text
generated/
  dbt/
    dbt_project.yml, profiles.yml       profiles read credentials from environment variables
    models/staging/<source>/            one view per raw table, plus sources.yml and tests
    models/intermediate/<domain>/       the versions behind SCD2 dimensions
    models/marts/<domain>/              dimensions and facts, with contracts and tests
    snapshots/_snapshots.yml            SCD2 history
    macros/murakumo/                    the record hash, schema names, extra tests and the run log
    selectors.yml                       one selector per schedule
  ddl/<warehouse>/
    raw.sql                             landing schemas and tables for ingestion to write into
    marts.sql                           reference DDL for the modelled tables, with keys
  orchestration/<orchestrator>/         a pipeline per schedule, and how to deploy them
  docs/
    index.html, <table>.md              pipeline docs: tables, columns, lineage, schedules, sources
  sql/<warehouse>/                      the project as plain SQL, with `plain_sql: true`
  python/<engine>/                      the pipeline as PySpark or Polars jobs, with `python_engine`
```

Schedules, pipelines, plain SQL and CI are described in [orchestration.md](orchestration.md).

### Staging

Every raw entity becomes `stg_<source>__<entity>` (from `conventions.naming.staging`), a view
that:

- renames every column to snake_case: `PULocationID` becomes `pu_location_id`;
- casts every column to the physical type of its logical type on the warehouse;
- passes through the landing columns `_ingested_at` and `_batch_id`, which ingestion writes;
- adds `_source_system` and `_record_hash` (a hash of every source column, to detect changes).

Tests come from the model: `unique` and `not_null` for a single-column primary key, one
`murakumo_unique_combination` for a composite key, `not_null` for non-nullable columns,
`accepted_values`, `relationships` for foreign keys, and every rule in `tests:`.

### Modelled layers

An entity in `intermediate`, `marts` or `semantic` is built from its mappings:

- the first entity a mapping reads drives the query; other entities are left-joined through the
  model's relationships, which must connect them directly and unambiguously (MK501 otherwise);
- `entity.column` sources resolve to the staged column names, and expressions are rewritten to
  match. **Expressions are written in DuckDB SQL** and translated to each warehouse's dialect, so
  `md5(concat_ws('|', a, b))` becomes the null-safe Snowflake form, `TO_HEX(MD5(...))` on
  BigQuery, and so on;
- every column is cast to its physical type, and `_updated_at` is added;
- `load: incremental` with a `watermark` becomes an incremental model that only reads newer rows,
  keyed on the grain (or primary key);
- `history: scd2` becomes a view of the current rows, a dbt snapshot that keeps every version, and
  the dimension on top with `valid_from`, `valid_to` and `is_current`;
- tables in `marts` get an enforced dbt contract (`contracts.enforce_on` in the manifest), so a
  wrong type fails the build instead of reaching consumers;
- `partition_by` and `cluster_by` become warehouse config where the warehouse supports them.

Columns with no mapping are generated as typed nulls, with warning MK503.

Every column of a modelled entity carries its lineage in the dbt YAML, as `meta.lineage`: the
`entity.column`s it is made from, so tools that read dbt's manifest (catalogues, docs sites) can
show it too.

### Domains

Each domain in `project.domains` that has models becomes a dbt group in `models/_groups.yml`,
owned by the domain's owner, and each model of an entity in a domain gets `group:` and
`meta.domain` in its config. Models keep the default `protected` access, so models in other
domains can still use them; groups say who owns what. Files stay in their layer folders, so
setting a domain moves nothing.

### Warehouses

DuckDB, Snowflake, Databricks, BigQuery, PostgreSQL, Redshift, Microsoft Fabric and Synapse each
have their own physical types, dialect and `profiles.yml`. The logical type decides the physical
one: `money` is `NUMBER(38,4)` on Snowflake and `DECIMAL(38,4)` on Databricks; `timestamp` is
`TIMESTAMP_NTZ` on Snowflake and `DATETIME` on BigQuery. Override a single column with
`physical_types` in the model.

## Verify

`murakumo verify` proves the generated project works, without touching `generated/`. It runs
on DuckDB, locally and with no warehouse, or on Snowflake with `--warehouse snowflake` (see
[snowflake.md](snowflake.md)):

1. it generates the project for that warehouse in a temporary folder;
2. creates the raw tables from the generated DDL;
3. loads them with synthetic rows that follow the model: unique keys (composite too), foreign
   keys that point at real parent rows, accepted values, NOT NULL, and range rules;
4. runs `dbt build`: every model, snapshot and test;
5. runs it again, so incremental models and snapshots take their update paths too.

```bash
murakumo verify --rows 1000
murakumo verify --data yellow_tripdata=local-data/tlc/yellow_tripdata_2024-01.parquet \
                --data taxi_zone_lookup=local-data/tlc/taxi_zone_lookup.csv
murakumo verify --keep build/    # keep the dbt project and verify.duckdb to look around
```

With `--data`, a raw table is loaded from a real Parquet, CSV or JSON file (local or `https://`)
instead, matching columns by name, or on Snowflake from a table (`snowflake:DB.SCHEMA.TABLE`);
`--limit` caps the rows. Real data is where tests start to find real problems.

Verify needs dbt and DuckDB: `pip install "murakumo[verify]"`, plus `dbt-snowflake` for Snowflake.

## Build

`verify` leaves nothing behind. `murakumo build` builds the project where it belongs, in the
warehouse from `murakumo.yaml` (a DuckDB file in the project folder, or a Snowflake database), and
keeps every table:

1. creates any raw landing table that is missing, from the generated DDL (existing tables and their
   rows are left as they are);
2. loads raw tables only when asked: `--data TABLE=PATH` from a file or table, or `--sample N`
   made-up rows into tables that are still empty;
3. runs `dbt build` for the environment (`--env`, by default the first in `murakumo.yaml`), into
   its schemas: `staging`, `marts` and so on.

```bash
murakumo build --sample 200        # try it out with made-up rows
murakumo build --data yellow_tripdata=data/yellow_tripdata_2024-01.parquet
murakumo build --env prod          # the prod profile and schemas
murakumo build                     # rebuild the models on whatever has landed
```

It builds the model as it is now, and says so if `generated/` is out of date or a change to a
table that keeps data has no migration yet: apply migrations to the warehouse first, since `build`
never alters an existing table. It runs on DuckDB and Snowflake; for other warehouses, run
`dbt build` in `generated/dbt`. In production an orchestrator does the same on a schedule.

## Audit tables

Every run records itself in an `audit` schema beside the tables it builds (under a personal or
pull-request environment's prefix, in that environment's own `audit` schema):

| Table | One row per | Records |
| --- | --- | --- |
| `audit.run_log` | run | when, which engine, environment and schedule, who, status, models and tests passed and failed |
| `audit.model_runs` | model or snapshot | status, seconds, rows in the table afterwards (views are not counted) |
| `audit.dq_results` | test | passed, warned or failed, and how many rows failed it |
| `audit.row_counts` | raw table loaded | rows ingested, rows rejected and why |
| `audit.daily_summary` | day (a view) | runs, rows ingested and rejected, models and tests that failed |

The dbt project writes the first three at the end of every run (`on-run-end`), wherever it runs:
`murakumo build`, an orchestrator, dbt by hand. `murakumo build` writes `row_counts` as it loads:
rows of a CSV file that do not fit the model's column types are set aside and counted as rejected,
with the reason (*Could not convert string "nope" to INTEGER*), instead of failing the load.
`murakumo runs` shows the last days, runs, loads and failed tests. Set `quality.run_log: false` to
turn the run log off.

## Python pipelines

With `python_engine: pyspark` or `polars` under `generation`, `generate` also writes the pipeline as
Python to `generated/python/<engine>/`: a module per model, in the order they run, a runtime, and
`run.py`. Each model's SQL is its dbt SQL rendered once more with the runtime's own `source()`,
`ref()` and `this`, in Spark SQL for PySpark and DuckDB SQL for Polars, so the jobs build the same
tables as dbt:

- **PySpark** runs everything as Spark SQL over temporary views and writes Parquet tables, or Delta
  with `--format delta`;
- **Polars** holds the data as Polars data frames and runs each model's SQL with DuckDB over them,
  writing Parquet. The SQL is not rewritten into Polars expressions by hand.

Both keep incremental, cumulative and SCD2 snapshot tables up to date between runs (merging new
rows by key, closing changed versions), run every test, and record each run in `<output>/audit/`
as the dbt project does in the warehouse.

```bash
murakumo run --engine polars --sample 200          # on made-up rows
murakumo run --engine pyspark                      # raw tables from the project's DuckDB file
murakumo run --engine pyspark --data orders=orders.parquet --schedule nightly --run-date 2024-01-31
```

`murakumo run` writes the pipeline to a temporary folder and runs it in its own environment (from
its `requirements.txt`, with uv), so Murakumo itself does not need Spark. PySpark 3.5 needs Java 17
or 21: the requirements include `jdk4py`, which brings one. On the same data, both engines build the
same tables as dbt, row for row, including SCD2 history after a change: Murakumo's tests check
Polars against dbt on every change, and PySpark was checked the same way by hand.

## Migrations

dbt rebuilds views and tables on every run, so a model change reaches them by itself. Three kinds
of table keep data between runs, so their schema has to change in place:

- **raw landing tables**, which ingestion writes into;
- **incremental models**, which dbt only adds rows to;
- the **snapshots** behind SCD2 dimensions, which hold every version.

`murakumo migrate` compares those tables in the model with the state recorded by the last
migration, and writes the difference as the next numbered SQL file:

```text
migrations/duckdb/
  schema.json                                  the tables as of the last migration, by id
  0001_initial.sql                             the first run: schemas and landing tables
  0002_rename_lineitem_l_discount_and_3_more.sql
```

```sql
-- lineitem: rename l_discount to discount
ALTER TABLE raw_tpch_erp.lineitem RENAME COLUMN l_discount TO discount;

-- lineitem: drop l_comment
-- l_comment was removed from the model. Uncomment to drop it and its data:
-- ALTER TABLE raw_tpch_erp.lineitem DROP COLUMN l_comment;
```

- Tables and columns are matched **by id**, so a rename is `RENAME`, which keeps the data, not a
  dropped column and an empty new one. This is why renames should go through `murakumo rename`
  or the canvas, which keep ids, rather than editing a name and its id by hand.
- New tables, new columns, renames and type changes are written as statements. Anything that
  loses data (a dropped column or table) is **written commented out** for a person to decide.
- Incremental models and snapshots are built by dbt, so a new environment does not have them
  until dbt's first run, which creates them in their new shape. Their statements use
  `ALTER TABLE IF EXISTS` (DuckDB, Snowflake, Postgres, BigQuery), so migrations can always run
  before dbt; on other warehouses a comment says to skip them where dbt has not built the table.
- For incremental models and snapshots, dbt adds new columns itself
  (`on_schema_change: append_new_columns`), so those additions are left as comments too.
- Statements are written for the manifest's warehouse (`--warehouse` for another): `RENAME
  COLUMN`, `SET DATA TYPE` or `TYPE`, `sp_rename` on Fabric and Synapse, and so on.
- `--name` names the file; `--dry-run` prints it instead of writing.

Apply migrations in order before running dbt, with your usual tool for the warehouse, and
commit each one with the model change that caused it. Files are never rewritten; to undo a
change, change the model back and write another migration. `murakumo migrate --check` in CI
catches a model change that needs a migration and has none. A project with no `migrations/`
folder has nothing to check, so migrations are opt-in: the first `murakumo migrate` starts them.

## Rule codes

| Code | Level | Meaning |
| --- | --- | --- |
| MK501 | error | An entity reads two entities with no single relationship between them |
| MK502 | error | A mapping combines several columns without an expression |
| MK503 | warning | A column has no mapping; it is generated as null |
| MK504 | warning | A modelled entity has no mappings, so nothing is generated |
| MK505 | error | An SCD2 entity has no primary or natural key |
| MK506 | error | No mapping names a source column, so there is no table to select from |
| MK507 | error | A mapping expression is not valid SQL |
| MK508 | error | A cumulative table's grain column has no column or mapping |

An entity with an error is skipped; the rest of the project is still generated.
