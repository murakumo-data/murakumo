# Murakumo

**Model-first data engineering.** Design the logical model (entities, relationships, mappings) on a canvas, and Murakumo generates and maintains everything downstream: DDL, dbt projects, Spark and Python jobs, streaming configs, tests, audit tables, data contracts and orchestration, on an open lakehouse.

Alpha. Working today: the model, importers, validation, the canvas, dbt and DDL generation,
migrations, and builds verified on DuckDB and Snowflake. Source:
[github.com/murakumo-data/murakumo](https://github.com/murakumo-data/murakumo).

## Install

```bash
pip install "murakumo[canvas,verify,duckdb]"
murakumo init my_platform        # asks about your warehouse and orchestrator, then shows what to set up
cd my_platform
murakumo setup                   # checks drivers, variables, keys and connections; says what to do
murakumo canvas                  # the visual editor
```

## Usage

```bash
murakumo init my_platform --target snowflake --yes   # or answer with options, without questions
cd my_platform
murakumo setup --connect         # also test the warehouse and source connections
murakumo validate                # check the manifest and model; exits 1 on errors
murakumo validate --fix          # write stable ids into hand-written YAML
murakumo validate --strict       # fail on warnings too (useful in CI)
murakumo validate --format json  # machine-readable diagnostics
murakumo schema entity           # JSON Schema for editors: manifest, model, entity, source, relationship

murakumo import csv dictionary.csv                     # or: excel, database, file, openapi, json
murakumo import database env:ERP_URL --source erp      # needs murakumo[postgres] or murakumo[duckdb]
murakumo import                                        # every source with a from: line in murakumo.yaml
murakumo add column dim_customer email string --pii    # add or remove sources, entities, columns, relationships
murakumo rename entity lineitem order_line             # relationships, mappings and the file follow
murakumo canvas                                        # the visual editor, on http://127.0.0.1:8765

murakumo generate                                      # generated/: dbt project and DDL for your warehouse
murakumo generate --check                              # fail CI if generated/ is out of date
murakumo migrate                                       # ALTER statements for tables that keep data
murakumo verify                                        # build and test it all on DuckDB, twice
murakumo verify --warehouse snowflake                  # or on Snowflake, in throwaway schemas
```

Extras: `canvas` (the editor), `verify` (builds on DuckDB), `duckdb` (files and DuckDB), `postgres`,
`snowflake`. `murakumo setup` tells you which a project needs.

Docs: [starting a project and setting up](https://github.com/murakumo-data/murakumo/blob/main/docs/setup.md),
[model files and validation rules](https://github.com/murakumo-data/murakumo/blob/main/docs/model.md),
[importing metadata](https://github.com/murakumo-data/murakumo/blob/main/docs/importing.md),
[generating and verifying](https://github.com/murakumo-data/murakumo/blob/main/docs/generating.md),
[Snowflake](https://github.com/murakumo-data/murakumo/blob/main/docs/snowflake.md),
[the canvas](https://github.com/murakumo-data/murakumo/blob/main/docs/canvas.md).

## Development

```bash
uv sync
uv run murakumo --version
uv run pytest
uv run ruff check
uv run ruff format --check
```
