# Getting metadata in

Murakumo builds the raw layer of the model from your sources' metadata. It reads table and
column definitions, keys and descriptions. It never reads data rows from databases, and from
files it reads only what it needs to learn the schema.

| Command | Reads | Needs |
| --- | --- | --- |
| `murakumo import csv FILE` | A data dictionary CSV, one row per column | - |
| `murakumo import excel FILE` | A dictionary workbook, every sheet or `--sheet NAME` | - |
| `murakumo import database URL --source NAME` | A live database's tables, columns, keys and comments | `murakumo[duckdb]`, `[postgres]` or `[snowflake]` |
| `murakumo import file LOCATION --source NAME` | A Parquet, CSV or JSON file's schema, local, glob or `https://` | `murakumo[duckdb]` |
| `murakumo import openapi LOCATION --source NAME` | One table per object schema in an OpenAPI or Swagger 2 spec | - |
| `murakumo import json LOCATION --source NAME --name TABLE` | A sample JSON response; types are inferred | - |
| `murakumo import` | Every source in `murakumo.yaml` with a `from:` line | as above |

Every import command takes `--project/-p` (default: the current folder) and `--dry-run`, which
shows what would change without writing anything.

## Data dictionaries

The format is in [examples/README.md](../examples/README.md#dictionary-format). Only
`table_name` and `column_name` are required. Headers are matched loosely (`Table`, `table name`
and `TABLE_NAME` all work, and so do common names like `Field`, `Data Type` and `PK`), and the
delimiter can be a comma, semicolon, tab or pipe.

- `logical_type` is used when it is a Murakumo type; otherwise `source_type` (or a SQL type
  written in `logical_type`, such as `VARCHAR(40)`) is mapped to one.
- `foreign_key` holds `table.column` (several separated by `;`). Columns that point at the same
  table become one composite relationship only when together they are that table's whole
  primary key; otherwise each column is its own relationship. So a trip's pickup zone and
  drop-off zone stay separate, and `lineitem` to `partsupp` is a single two-column join.
- `update_pattern` sets the load pattern: anything containing `cdc`, `stream`, `append`,
  `increment` or `full`/`refresh`/`snapshot`. The original text is kept in `properties`.
- `accepted_values` is `code=label;code=label`, or just `a;b;c`.

Bad rows are reported with their row number and skipped; the rest still import.

## Databases

```bash
murakumo import database duckdb:///warehouse.duckdb --source erp
murakumo import database postgresql://reader@db.internal:5432/erp --source erp --schema sales
murakumo import database env:ERP_DATABASE_URL --source erp
murakumo import database snowflake:///SALES_DB --source sales   # account and key from SNOWFLAKE_*
```

Primary keys, foreign keys (composite ones too), `NOT NULL`, and table and column comments all
carry across. Table names stay as they are; the schema is added (`sales__orders`) only when two
schemas have a table with the same name.

**Credentials never go into the model.** Put the password in `PGPASSWORD` or `~/.pgpass`, or the
whole connection string in an environment variable and pass `env:NAME`. If a URL does contain a
password, it is removed before the connection is saved in `model/sources/`.

Snowflake works the same way with `snowflake:///DATABASE`; see [snowflake.md](snowflake.md) for the
key-pair setup. Databricks, BigQuery and the other warehouses follow in later milestones.

## Files

```bash
murakumo import file https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet --source nyc_tlc
murakumo import file "s3-export/orders/*.parquet" --source erp --name orders
```

The table is named after the file without dates (`yellow_tripdata_2024-01.parquet` becomes
`yellow_tripdata`), or after the folder for a glob. Use `--name` to choose.

## APIs

```bash
murakumo import openapi https://api.example.com/openapi.json --source shop
murakumo import json "https://archive-api.open-meteo.com/v1/archive?latitude=40.78&longitude=-73.97&start_date=2024-01-01&end_date=2024-01-07&hourly=temperature_2m,precipitation" \
  --source open_meteo --name weather_hourly --path hourly
```

For OpenAPI, `required` decides nullability, `enum` becomes accepted values, `allOf` is merged,
and nested objects become `json` columns. For JSON samples, `--path` points at the rows; it can be
a list of objects, a single object, or columnar arrays like Open-Meteo's
`{"time": [...], "temperature_2m": [...]}`. Inferred types are only as good as the sample, so
use a sample with plenty of rows and check the result.

## Sources in the manifest

```yaml
sources:
  - name: erp
    from: csv:dictionaries/erp.csv          # paths are relative to the project folder
    ingestion: { tool: airbyte, schedule: hourly }
  - name: crm
    from: introspect:env:CRM_DATABASE_URL
  - name: nyc_tlc
    from: file:https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv
  - name: shop
    from: openapi:https://api.example.com/openapi.json
```

`murakumo import` then refreshes all of them. `ingestion.tool` and `ingestion.schedule` are
copied to the source system.

## Importing again

Run an import again whenever the source changes. Murakumo compares and updates the model:

- tables and columns that already exist **keep their ids**, so a changed type is an update, not a
  drop and a re-create;
- only what the source describes is overwritten; tests, tags, partitioning, custom properties and
  anything else you added by hand stay;
- new columns are added; columns the source no longer has are removed, because raw tables mirror
  their source;
- tables the source no longer has are reported and kept; add `--prune` to remove them and their
  foreign keys;
- a table that belongs to a different source, or to a modelled layer, is never overwritten.

Imports from a single file or JSON sample describe only part of a source, so they never report
other tables as missing.

## Adding things by hand

For sources without metadata, or for modelled layers:

```bash
murakumo add source crm --type api
murakumo add entity dim_customer --layer marts --kind dimension
murakumo add column dim_customer customer_key bigint --pk
murakumo add column dim_customer email string --pii --classification confidential
murakumo add relationship fct_order dim_customer --on customer_key=customer_key
murakumo remove column dim_customer email
murakumo remove entity dim_customer       # also removes its relationships and file
```

Every change is saved and then validated, so you see straight away what else needs fixing. The
canvas will use the same operations.
