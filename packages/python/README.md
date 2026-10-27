# Murakumo

**Model-first data engineering.** Design the logical model (entities, relationships, mappings) on a canvas, and Murakumo generates and maintains everything downstream: DDL, dbt projects, Spark and Python jobs, streaming configs, tests, audit tables, data contracts and orchestration, on an open lakehouse.

Early development: the model and validation work; code generation is not built yet. Source: [github.com/murakumo-data/murakumo](https://github.com/murakumo-data/murakumo).

## Usage

```bash
murakumo init my_platform        # manifest, model folder and a small NYC taxi example
cd my_platform
murakumo validate                # check the manifest and model; exits 1 on errors
murakumo validate --fix          # write stable ids into hand-written YAML
murakumo validate --strict       # fail on warnings too (useful in CI)
murakumo validate --format json  # machine-readable diagnostics
murakumo schema entity           # JSON Schema for editors: manifest, model, entity, source, relationship
```

The model files and every validation rule are described in [docs/model.md](https://github.com/murakumo-data/murakumo/blob/main/docs/model.md).

## Development

```bash
uv sync
uv run murakumo --version
uv run pytest
uv run ruff check
uv run ruff format --check
```
