# commerce: the platform

What the platform is built from, how each source's data reaches the warehouse, and how to set it
up by hand. Generated from murakumo.yaml and model/decisions.yaml.

## The stack

| Part | Choice | What it does |
| --- | --- | --- |
| Warehouse | `duckdb` | DuckDB, on this computer: no account needed |
| Extract | `python` | Python scripts, one per source: run anywhere, by the orchestrator or `murakumo ingest` |
| Landing | `landing` | Where fetched files land before they are loaded: a folder, or s3://bucket/prefix. |
| Load | `build` | Each build loads the new files (`murakumo build --data`, or the orchestrator's load step) |
| Transform | `dbt` | dbt: SQL models, tests and snapshots |
| Python | `none` | No |
| Orchestrator | `airflow` | Apache Airflow |
| CI | `github` | GitHub Actions |
| Streaming | `consumer` | The extract script, on each schedule: reads what is new since the last run |
| Infrastructure as code | `none` | No: the SQL and scripts only |

- **warning** (extract): no source has a connection yet, so there is nothing to fetch: add one to a source

## Sources

No source has a connection yet, so Murakumo fetches nothing: data arrives some other way.
## Decisions

None recorded yet. `murakumo stack set ... --why` and `murakumo decisions add` record them.
