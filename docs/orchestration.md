# Schedules, pipelines and CI

Once the model builds, three things keep it running: **schedules** say when each part is refreshed,
**pipelines** for your orchestrator run them, and **CI** checks every change before it lands.
Murakumo generates all three from the project, and can also export it as **plain SQL** for teams
that do not use dbt, and **pipeline docs** for everyone else.

## Schedules

Schedules live in `murakumo.yaml`:

```yaml
orchestration:
  timezone: UTC                    # any IANA name: Europe/London, America/New_York, ...
  schedules:
    - name: hourly_sales
      cron: "0 * * * *"            # minute hour day month weekday
      select: [+domain:sales]
      description: Order lines and customers, every hour
    - name: nightly
      cron: "0 6 * * 1-5"          # no select: everything the other schedules leave out
```

A schedule's `select` picks tables:

| Write | Picks |
| --- | --- |
| `domain:sales` | every table in the sales domain |
| `layer:marts` | every table in that layer (`raw`, `staging`, `intermediate`, `marts`, `semantic`) |
| `source:tpch_erp` | every raw table of that source |
| `fct_orders` | one table |
| `+fct_orders` | that table and everything it is built from |
| `tpch_erp+` | ... and everything built from it |

One schedule may leave `select` out: it refreshes whatever no other schedule picks, so nothing is
forgotten. With no schedules at all, everything is refreshed once a day at 06:00.

```bash
murakumo schedules                                       # each schedule, in words, with its next run
murakumo add schedule hourly_sales --cron "0 * * * *" --select +domain:sales
murakumo rename schedule hourly_sales sales_hourly
murakumo remove schedule sales_hourly
murakumo schedules --timezone Europe/London
murakumo build --schedule hourly_sales                   # run one schedule by hand
```

Cron times take `*`, numbers, ranges (`1-5`), steps (`*/15`), lists (`6,18`), month and day names
(`jan`, `mon`) and `@hourly`, `@daily`, `@weekly`, `@monthly`. `murakumo validate` checks them,
and checks that every selection names something that exists (MK601); a name used twice (MK602),
two schedules that both refresh "the rest" (MK603), and tables no schedule refreshes (MK604, a
warning) are reported too. The canvas's **Schedules** panel edits the same list, says each time in
words with its next run, and highlights the tables a schedule refreshes.

Each schedule becomes a dbt selector in `generated/dbt/selectors.yml`, naming its models, so
`dbt build --selector hourly_sales` runs exactly that schedule. A schedule runs the tests on the
tables it builds; the schedule that refreshes the rest also runs every relationship test, so a
test between tables in different schedules still runs once a day.

## Pipelines

`murakumo generate` writes pipelines for the orchestrator in `murakumo.yaml` (`generation.orchestrator`,
or `murakumo config --orchestrator`) to `generated/orchestration/<orchestrator>/`, one per schedule,
with a README on deploying them:

| Orchestrator | Generated | Each run |
| --- | --- | --- |
| Airflow, Cloud Composer | `dags/<project>__<schedule>.py`, `requirements.txt` | source freshness, then the selector through [Cosmos](https://astronomer.github.io/astronomer-cosmos/), one task per model |
| Dagster | `definitions.py`, `requirements.txt` | the schedule's models as dbt assets, with their tests as asset checks |
| Prefect | `flows.py`, `prefect.yaml` | a flow per schedule; deployments on its cron time |
| Databricks Workflows | `databricks.yml` (an asset bundle) | a dbt task on a SQL warehouse; schedules deploy paused |
| Snowflake Tasks | `tasks.sql` | a task per schedule running the dbt project deployed in Snowflake |
| AWS Glue, Step Functions, GCP Workflows, Azure Data Factory | `run.sh` and steps | `run.sh <schedule>`, started by the service on the cron time |

Pipelines find the dbt project at `generated/dbt`, beside them; set `MURAKUMO_DBT_DIR` if you copy
them elsewhere, and `MURAKUMO_TARGET` to build another environment than `prod`. Warehouse
credentials come from the same environment variables as `profiles.yml`. Freshness is checked for
sources that say how fresh they should be (`freshness` on the source or table).

The Airflow, Dagster and Prefect pipelines have been run for real on DuckDB with the commerce
example. To try an Airflow DAG without its scheduler, run `airflow dags test <dag_id>` with
`AIRFLOW__COSMOS__ENABLE_DATASET_ALIAS=False`: on Airflow 2.10, `dags test` cannot save Cosmos's
dataset aliases (scheduled runs can).

## Plain SQL

```bash
murakumo export sql                          # export/sql/<warehouse>/
murakumo export sql --warehouse snowflake --out sql/
```

The project as scripts that need nothing but the warehouse: `00_landing.sql` (schemas and landing
tables), then one numbered script per model in the order they must run, and `tests/<model>.sql`,
where each query returns the rows that fail a test. The scripts are the same SQL as the dbt
project with dbt's parts written out: views and tables are re-created; an incremental model is
built in full once, then adds the rows past its watermark, replacing those with the same key; an
SCD2 snapshot closes the current version of each changed row and adds a new one, with
`dbt_valid_from` and `dbt_valid_to`. Run them in order in one session.

On the same data, the plain SQL builds the same tables as dbt, row for row, on a first and a second
run: Murakumo's tests check this on DuckDB. Set `plain_sql: true` under `generation` in
`murakumo.yaml` to keep the scripts in `generated/sql/<warehouse>/`, written and checked with
everything else.

## Pipeline docs

`murakumo generate` writes `generated/docs/index.html`, one page that needs nothing else, and
`generated/docs/<table>.md` for reading in the repository. They cover the domains and their
owners, the schedules and what each refreshes, the sources, and for every table: its description,
grain, load, schedule, the tables it is built from and feeds, and each column with its type, keys,
PII and tests, where it comes from (with its expression) and what uses it. `murakumo docs` opens the
page as the model is now; so does **Docs** in the canvas. The docs contain no dates, so they
change only when the project does.

## CI

```bash
murakumo ci                         # .github/workflows/murakumo.yml at the repository root
murakumo ci --provider gitlab       # .gitlab-ci.yml
murakumo ci --deploy                # also build production on pushes to main, after approval
murakumo ci --dry-run               # print it instead
murakumo ci --run                   # run the same checks here
```

The workflow runs on every pull request and push to main, in the project's folder:

1. `murakumo validate --strict`: the model has no errors or warnings;
2. `murakumo generate --check`: `generated/` matches the model;
3. `murakumo migrate --check`: every table change has its migration;
4. `murakumo verify`: the dbt project builds and its tests pass, on DuckDB with made-up rows.

With `--deploy`, a second job builds the production environment after the checks pass, on pushes
to main only: on GitHub it uses the `production` environment (add required reviewers to it under
Settings → Environments, so each deploy waits for approval); on GitLab it is a manual job. The
warehouse credentials come from the CI's secrets (`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER` and
`SNOWFLAKE_PRIVATE_KEY`, the key's contents, for Snowflake). A project that builds into a DuckDB
file has nothing to deploy to.

The file is yours once written. If it differs from what Murakumo would write, `murakumo ci` shows
the difference and changes nothing; `--force` replaces it. The canvas's **CI** panel does the
same, and its **Run checks now** runs the four checks on your computer, showing each as it
passes or fails.
