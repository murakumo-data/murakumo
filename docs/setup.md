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

In a terminal, `init` asks a few questions and writes the answers into `murakumo.yaml`. Each
list is numbered, with a short description of every choice: type the number (or the name), or
press Enter for the suggestion.

| Question | Choices | Suggested |
| --- | --- | --- |
| Project name | letters, digits and `_` | the folder name |
| Warehouse | 1 duckdb, 2 snowflake, 3 databricks, 4 bigquery, 5 redshift, 6 fabric, 7 synapse, 8 postgres | duckdb, which runs on your computer with no account |
| Orchestrator | 1 airflow, 2 dagster, 3 prefect, 4 databricks_workflows, 5 snowflake_tasks, ... 10 azure_data_factory | airflow |
| Modelling pattern | 1 kimball, 2 datavault, 3 obt, 4 3nf, 5 mixed | kimball |
| Add the small NYC taxi example model? | yes / no | yes: something to explore; a real project answers no |

Or give the answers as options, as in scripts and CI, where `init` never asks:

```bash
murakumo init my_platform --target snowflake --orchestrator airflow --pattern kimball --yes
murakumo init my_platform --empty --yes      # no example model
```

It then prints what the project needs on this machine (see below), leaving out what is already done,
and what to run next.

`murakumo init` can be run inside a git repository you already have (`cd your-repo && murakumo
init analytics`): it never runs `git init` itself, and `setup` recognises the repository.

## Changing your answers

```bash
murakumo config                         # asks the questions again, with your answers suggested
murakumo config --target snowflake      # or change one directly
murakumo config --orchestrator dagster --pattern datavault
```

`config` edits `murakumo.yaml` in place and keeps its comments. Changing the warehouse also renames
the database when it still has the name `init` gave it. Then run `murakumo setup` to see what the
new choices need, and `murakumo generate`.

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
| Tools | Python 3.11+; the canvas is installed; on macOS, the command line developer tools (they bring git); git is installed and knows your name and email |
| Warehouse | dbt and the warehouse's adapter (`dbt-snowflake`, `dbt-databricks`...) and driver are installed; every environment variable the generated `profiles.yml` reads is set, with the optional ones and their defaults; on Snowflake, the private key file exists and only you can read it. With `--connect`: signs in, and checks the database exists and the role can see it |
| Sources | For each source in the manifest with a `from:` line: the dictionary or file exists; for databases, the `env:` variable holding the connection string is set and the driver is installed. With `--connect`: connects and counts the tables it can see |
| Orchestrator | Whether it is installed and, if not, numbered steps to install it and run the pipelines Murakumo generates (Airflow through the Astro CLI, Dagster, Prefect), with links to their documentation; for managed services (Databricks Workflows, Snowflake tasks, AWS, Google Cloud, Azure), how to deploy them |
| Project | A git repository (its own, or one you already had); `generated/` up to date with the model; migrations started; lakehouse settings not used yet |

Each item is ✓ done, ✗ needed before the project can build, ! worth fixing, or • for your
information, and anything not done comes with the command or instruction that does it, and links
to the documentation where there is more to read. When a warehouse needs SQL run on it (on
Snowflake, creating a database that does not exist yet), `setup --connect` prints it as a block to
paste into a worksheet, with where to run it. Install
commands match how Murakumo itself was installed: `pip install ...`, `uv pip install ...`, or
`uv tool install --force ... --with ...`.

`setup` only looks: it never installs anything, writes a file, or changes a setting.

`murakumo generate` writes a pipeline per schedule for your orchestrator to
`generated/orchestration/<orchestrator>/`; the orchestrator check's steps get it running with
them. See [orchestration.md](orchestration.md) for schedules, pipelines and CI. Apply new
migrations before the pipelines run; to build by hand, use `murakumo build`
([generating.md](generating.md#build)).

## Warehouse guides

- DuckDB: nothing to set up.
- Snowflake: [snowflake.md](snowflake.md), from a trial account to key-pair login.
- Others: `murakumo setup` lists the variables the generated `profiles.yml` reads; full guides
  come with each warehouse's verify support.
