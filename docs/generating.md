# Generating and verifying

```bash
murakumo generate                        # write generated/: a dbt project and DDL
murakumo generate --warehouse snowflake  # for another warehouse than the manifest's
murakumo generate --check                # CI: fail if generated/ is out of date
murakumo verify                          # build it on DuckDB and run every test, twice
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
    macros/murakumo/                    the record hash, schema names and extra generic tests
  ddl/<warehouse>/
    raw.sql                             landing schemas and tables for ingestion to write into
    marts.sql                           reference DDL for the modelled tables, with keys
```

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

### Warehouses

DuckDB, Snowflake, Databricks, BigQuery, PostgreSQL, Redshift, Microsoft Fabric and Synapse each
have their own physical types, dialect and `profiles.yml`. The logical type decides the physical
one: `money` is `NUMBER(38,4)` on Snowflake and `DECIMAL(38,4)` on Databricks; `timestamp` is
`TIMESTAMP_NTZ` on Snowflake and `DATETIME` on BigQuery. Override a single column with
`physical_types` in the model.

## Verify

`murakumo verify` proves the generated project works, without a warehouse and without touching
`generated/`:

1. it generates the project for DuckDB in a temporary folder;
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
instead, matching columns by name; `--limit` caps the rows. Real data is where tests start to
find real problems.

Verify needs dbt and DuckDB: `pip install "murakumo[verify]"`.

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

An entity with an error is skipped; the rest of the project is still generated.
