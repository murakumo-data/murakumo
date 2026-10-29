# Airflow DAGs for commerce

One DAG per schedule. Each checks the freshness of the sources it reads, then builds its dbt
selector with [Cosmos](https://astronomer.github.io/astronomer-cosmos/), one Airflow task per model.

| Schedule | When | Cron |
| --- | --- | --- |
| `hourly_sales` | every hour, on the hour | `0 * * * *` |
| `nightly` | every day at 06:00 UTC | `0 6 * * *` |

## Run them

1. Add `requirements.txt` to your Airflow environment (Astro: append it to the project's).
2. Put the `dags/` files in Airflow's dags folder, and the dbt project where they can read it:
   either copy the whole `generated/` folder so `dags/` sits beside `dbt/`, or set
   `MURAKUMO_DBT_DIR` to the dbt project's path.
3. The DAGs start paused: turn them on in the Airflow UI.

To try one without the scheduler, run `airflow dags test <dag_id>` with
`AIRFLOW__COSMOS__ENABLE_DATASET_ALIAS=False` set: on Airflow 2.10, `dags test` cannot save Cosmos's
dataset aliases (scheduled runs can).

Set these where the pipelines run:

- the warehouse credentials `profiles.yml` reads (see `generated/dbt/profiles.yml`);
- `MURAKUMO_TARGET` to build another environment than `prod`;
- `MURAKUMO_DBT_DIR` if the dbt project is not at `generated/dbt`, beside `generated/orchestration/`.
