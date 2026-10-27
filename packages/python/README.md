# Murakumo

**Model-first data engineering.** Design the logical model (entities, relationships, mappings) on a canvas, and Murakumo generates and maintains everything downstream: DDL, dbt projects, Spark and Python jobs, streaming configs, tests, audit tables, data contracts and orchestration, on an open lakehouse.

Early development: there is no usable functionality yet. Source: [github.com/murakumo-data/murakumo](https://github.com/murakumo-data/murakumo).

## Development

```bash
uv sync
uv run murakumo --version
uv run pytest
uv run ruff check
```
