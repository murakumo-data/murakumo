# Murakumo

**Model-first data engineering.** Design the logical model (entities, relationships, mappings) on a canvas, and Murakumo generates and maintains everything downstream: DDL, dbt projects, Spark and Python jobs, streaming configs, tests, audit tables, data contracts and orchestration, on an open lakehouse.

> Status: early. The packages below are placeholders that reserve the names; the engine is in design.

## Packages

| Package | Install | Source |
| --- | --- | --- |
| Python CLI and engine | `pip install murakumo` | [packages/python](packages/python) |
| JavaScript SDK (future) | `npm install murakumo` | [packages/npm](packages/npm) |

## Licence

[Apache-2.0](LICENSE)
