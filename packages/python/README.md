# Murakumo

**Model-first data engineering.** Design the logical model (entities, relationships, mappings) on a canvas, and Murakumo generates and maintains everything downstream: DDL, dbt projects, Spark and Python jobs, streaming configs, tests, audit tables, data contracts and orchestration, on an open lakehouse.

Early development: the model, importers and validation work; code generation is not built yet. Source: [github.com/murakumo-data/murakumo](https://github.com/murakumo-data/murakumo).

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
```

Database and file support are extras: `pip install "murakumo[duckdb,postgres]"`.

Docs: [model files and validation rules](https://github.com/murakumo-data/murakumo/blob/main/docs/model.md),
[importing metadata](https://github.com/murakumo-data/murakumo/blob/main/docs/importing.md).

## Development

```bash
uv sync
uv run murakumo --version
uv run pytest
uv run ruff check
uv run ruff format --check
```
