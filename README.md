# Murakumo

**Model-first data engineering.** Design the logical model (entities, relationships, mappings) on a canvas, and Murakumo generates and maintains everything downstream: DDL, dbt projects, Spark and Python jobs, streaming configs, tests, audit tables, data contracts and orchestration, on an open lakehouse.

> Status: early development. The published packages are placeholders.

## Repository layout

| Path | What it is |
| --- | --- |
| [packages/python](packages/python) | Engine and `murakumo` CLI (`pip install murakumo`) |
| [packages/npm](packages/npm) | npm package placeholder (`npm install murakumo`), future JavaScript SDK |
| [apps/canvas](apps/canvas) | Modelling canvas (React + React Flow), served by `murakumo canvas` |
| [docs](docs) | Reference: [model files and validation rules](docs/model.md), [importing metadata](docs/importing.md), [generating and verifying](docs/generating.md), [Snowflake](docs/snowflake.md), [the canvas](docs/canvas.md) |
| [examples](examples) | Sample data dictionaries, and [commerce](examples/commerce): a full project with its generated dbt code |
| [tools](tools) | Developer scripts |

## Getting started

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 22+.

```bash
cd packages/python && uv sync && uv run murakumo --version
uv run murakumo init ../../local-data/demo && uv run murakumo validate ../../local-data/demo
uv run murakumo verify -p ../../examples/commerce
cd ../../apps/canvas && npm install && npm run build
cd ../../packages/python && uv run murakumo canvas ../../examples/commerce   # the visual editor
```

## Licence

[Apache-2.0](LICENSE)
