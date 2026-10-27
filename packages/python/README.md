# Murakumo

**Model-first data engineering.** Design the logical model (entities, relationships, mappings) on a canvas, and Murakumo generates and maintains everything downstream: DDL, dbt projects, Spark and Python jobs, streaming configs, tests, audit tables, data contracts and orchestration, on an open lakehouse.

Early development: the model, importers, validation, and dbt and DDL generation work. Source: [github.com/murakumo-data/murakumo](https://github.com/murakumo-data/murakumo).

## Usage

```bash
murakumo init my_platform        # manifest, model folder and a small NYC taxi example
cd my_platform
murakumo validate                # check the manifest and model; exits 1 on errors
murakumo validate --fix          # write stable ids into hand-written YAML
murakumo validate --strict       # fail on warnings too (useful in CI)
murakumo validate --format json  # machine-readable diagnostics
murakumo schema entity           # JSON Schema for editors: manifest, model, entity, source, relationship

murakumo import csv dictionary.csv                     # or: excel, database, file, openapi, json
murakumo import database env:ERP_URL --source erp      # needs murakumo[postgres] or murakumo[duckdb]
murakumo import                                        # every source with a from: line in murakumo.yaml
murakumo add column dim_customer email string --pii    # add or remove sources, entities, columns, relationships

murakumo generate                                      # generated/: dbt project and DDL for your warehouse
murakumo generate --check                              # fail CI if generated/ is out of date
murakumo verify                                        # build and test it all on DuckDB, twice
murakumo verify --warehouse snowflake                  # or on Snowflake, in throwaway schemas
```

Extras: `pip install "murakumo[duckdb,postgres,snowflake,verify]"` for databases, files and `murakumo verify`.

Docs: [model files and validation rules](https://github.com/murakumo-data/murakumo/blob/main/docs/model.md),
[importing metadata](https://github.com/murakumo-data/murakumo/blob/main/docs/importing.md),
[generating and verifying](https://github.com/murakumo-data/murakumo/blob/main/docs/generating.md),
[Snowflake](https://github.com/murakumo-data/murakumo/blob/main/docs/snowflake.md).

## Development

```bash
uv sync
uv run murakumo --version
uv run pytest
uv run ruff check
uv run ruff format --check
```
