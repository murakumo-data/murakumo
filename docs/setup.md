# Starting a project and setting up a machine

## Install

```bash
pip install "murakumo[canvas,verify,duckdb]"   # the editor, local builds on DuckDB, and file imports
murakumo --version
```

Add `snowflake` or `postgres` to the brackets for those databases; `murakumo setup` says what a
project needs, with the exact command, so you do not have to guess. With
[uv](https://docs.astral.sh/uv/): `uv tool install "murakumo[canvas,verify,duckdb]"`.

## `murakumo init`

```bash
murakumo init my_platform
```

In a terminal, `init` asks four questions and writes the answers into `murakumo.yaml`:

| Question | Choices | Default |
| --- | --- | --- |
| Project name | letters, digits and `_` | the folder name |
| Warehouse | snowflake, databricks, bigquery, redshift, fabric, synapse, postgres, duckdb | duckdb, which runs on your computer with no account |
| Orchestrator | airflow, dagster, prefect, databricks_workflows, snowflake_tasks, ... | airflow |
| Modelling pattern | kimball, datavault, obt, 3nf, mixed | kimball |
| Start with the NYC taxi example? | yes / no | yes |

Or give the answers as options, as in scripts and CI, where `init` never asks:

```bash
murakumo init my_platform --target snowflake --orchestrator airflow --pattern kimball --yes
murakumo init my_platform --empty --yes      # no example model
```

It then prints what the project needs on this machine (see below), leaving out what is already done.

## `murakumo setup`

```bash
murakumo setup             # check, and say what to do about anything missing
murakumo setup --connect   # also open the warehouse and source connections
murakumo setup --check     # exit 1 if anything required is missing (for scripts)
murakumo setup --format json
```

`setup` reads the manifest and checks, for this project on this machine:

| Area | What it checks |
| --- | --- |
| Tools | Python 3.11+; the canvas is installed; git is installed |
| Warehouse | dbt and the warehouse's adapter (`dbt-snowflake`, `dbt-databricks`...) and driver are installed; every environment variable the generated `profiles.yml` reads is set, with the optional ones and their defaults; on Snowflake, the private key file exists and only you can read it. With `--connect`: signs in, and checks the database exists and the role can see it |
| Sources | For each source in the manifest with a `from:` line: the dictionary or file exists; for databases, the `env:` variable holding the connection string is set and the driver is installed. With `--connect`: connects and counts the tables it can see |
| Orchestrator | Whether it is installed, and how to install it (Airflow through the Astro CLI or pip with constraints, Dagster, Prefect); managed services need nothing locally |
| Project | A git repository; `generated/` up to date with the model; migrations started; lakehouse settings not used yet |

Each item is ✓ done, ✗ needed before the project can build, ! worth fixing, or • for your
information, and anything not done comes with the command or instruction that does it. Install
commands match how Murakumo itself was installed: `pip install ...`, `uv pip install ...`, or
`uv tool install --force ... --with ...`.

`setup` only looks: it never installs anything, writes a file, or changes a setting.

Orchestration is guidance only in this release: Murakumo does not generate Airflow, Dagster or
Prefect pipelines yet, so until it does, run `dbt build` in `generated/dbt` from your scheduler
(for Airflow, [Cosmos](https://github.com/astronomer/astronomer-cosmos) runs a dbt project as tasks),
after applying new migrations.

## Warehouse guides

- DuckDB: nothing to set up.
- Snowflake: [snowflake.md](snowflake.md), from a trial account to key-pair login.
- Others: `murakumo setup` lists the variables the generated `profiles.yml` reads; full guides
  come with each warehouse's verify support.
