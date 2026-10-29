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
murakumo import database env:ERP_URL --source erp --table sales.orders   # only some tables
murakumo import                                        # every source with a from: line in murakumo.yaml
murakumo add column dim_customer email string --pii    # add or remove sources, domains, entities, columns, relationships
murakumo rename entity lineitem order_line             # relationships, mappings and the file follow
murakumo move column dim_customer email --after name   # reorder columns
murakumo add domain finance --owner "Finance team"     # domains become dbt groups
murakumo lineage fct_trip.trip_id                      # where a column comes from; --down for what uses it
murakumo canvas                                        # the visual editor, on http://127.0.0.1:8765

murakumo generate                                      # generated/: dbt project and DDL for your warehouse
murakumo generate --check                              # fail CI if generated/ is out of date
murakumo migrate                                       # ALTER statements for tables that keep data
murakumo add schedule nightly --cron "0 6 * * *"       # schedules become pipelines for your orchestrator
murakumo schedules                                     # when each runs, and what it refreshes
murakumo export sql                                    # the project as plain SQL, without dbt
murakumo docs                                          # pipeline docs: tables, columns, lineage, schedules
murakumo ci                                            # a GitHub Actions (or GitLab) workflow for every push
murakumo verify                                        # build and test it all on DuckDB, twice
murakumo verify --warehouse snowflake                  # or on Snowflake, in throwaway schemas
murakumo build --sample 200                            # build into the project's warehouse and keep the tables
murakumo config --target snowflake                     # change the answers you gave init
murakumo --help                                        # every command, with what it does
```

Extras: `canvas` (the editor), `verify` (builds on DuckDB), `duckdb` (files and DuckDB), `postgres`,
`snowflake`. `murakumo setup` tells you which a project needs.

Docs: [starting a project and setting up](https://github.com/murakumo-data/murakumo/blob/main/docs/setup.md),
[model files and validation rules](https://github.com/murakumo-data/murakumo/blob/main/docs/model.md),
[importing metadata](https://github.com/murakumo-data/murakumo/blob/main/docs/importing.md),
[generating and verifying](https://github.com/murakumo-data/murakumo/blob/main/docs/generating.md),
[Snowflake](https://github.com/murakumo-data/murakumo/blob/main/docs/snowflake.md),
[the canvas](https://github.com/murakumo-data/murakumo/blob/main/docs/canvas.md),
[schedules, pipelines and CI](https://github.com/murakumo-data/murakumo/blob/main/docs/orchestration.md).

## Development

```bash
uv sync
uv run murakumo --version
uv run pytest
uv run ruff check
uv run ruff format --check
```
