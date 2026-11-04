# Changelog

## 0.9.0 (unreleased)

The replication guide: everything it takes to rebuild the platform by hand, without Murakumo. And
the modelling patterns the practices review left for 0.9.

- **The replication guide** (`generated/guide/`, written by `murakumo generate` and `murakumo
  guide`):
  - Twelve chapters, in the order you build:
    - overview, with the tools and versions;
    - accounts and access, with the key pair, provisioning, the service user and the variables;
    - repository and environments;
    - sources, with how to reach each by hand and fetch it;
    - the raw layer, with a data dictionary and the load SQL per table;
    - transformations: every model as dbt and as plain SQL, where each column comes from, and its
      tests;
    - running it, quality and observability, CI/CD;
    - reference: the bus matrix, lineage, the marts' dictionary, exposures and masking;
    - the decisions log, and troubleshooting.
  - Every step has **why**, the commands or SQL in full, **you should see**, and a box to tick.
  - As Markdown, and as one HTML page that keeps your ticks. Beside them, the scripts it runs:
    `sql/build`, `sql/load`, `sql/tests`, `sql/masking.sql`, and `rebuild.sh` to run them all.
  - **Loading without Murakumo:** a SQL script per raw table, for DuckDB and Snowflake, loads the
    landed files the table's own way (full, append or incremental), each file once.
  - **`murakumo guide --check`** proves the guide is complete. In a scratch folder it rebuilds the
    project on DuckDB from the guide's scripts alone, then with Murakumo, after each of two runs
    of new files, and compares every table in rows and values. It also checks that every test
    passes, and that running it all again changes nothing.
  - **In the canvas:** a **Guide** panel with the chapters, the ticks, and **Check it rebuilds the
    project**.
- **Unknown members**:
  - Dimensions get a row with key -1 and "Unknown" text, and facts point a missing key at it, so
    no fact row is lost.
  - `modelling.unknown_members`, on for new projects, and `unknown_member:` per dimension.
- **Accumulating snapshots** (`kind: accumulating`):
  - One row per key, with a date column per milestone. A milestone once reached keeps its date,
    and the time between milestones is computed, with `current_milestone`.
  - **MK314** and **MK315** check the settings.
- **Conformed dimensions and the bus matrix**:
  - `conformed: true` on a dimension. **MK316** reports a fact using another domain's dimension
    that is not conformed; **MK317** reports a copy of a conformed dimension.
  - The bus matrix is in the guide, and in the canvas's new **Bus matrix** panel.
- **Access levels**: `access: private | protected | public` per table, as dbt model access.
  **MK706** reports a private table without a domain.
- **Exposures** (`model/exposures.yaml`):
  - The dashboards, notebooks and apps that read the marts, written into dbt and the guide.
  - **MK209** and **MK109** check them.
  - **In the canvas:** **Bus matrix** → **Exposures**.
- **Masking policies** (Snowflake):
  - `governance.masking` maps `pii` or a classification to a rule: redact, partial, hash or null.
    `governance.unmasked_roles` see the real values.
  - dbt sets the policies on the marts after each build. `ddl/snowflake/masking.sql` has the same
    SQL, and `stack deploy` has a **masking** step.
  - **MK704** and **MK705** check the settings.
  - **In the canvas:** **Settings** → **Personal data**.
- **In the canvas**, the inspector sets each table's unknown row, conformed, access and
  milestones.
- **Fixes**: `verify` and `build` no longer count dbt's exposures as failed nodes.
- **Verified**:
  - `guide --check` on the commerce example and on a new project.
  - The commerce example on DuckDB, with an accumulating snapshot, unknown members, conformed
    dimensions, an exposure and masking settings.
  - Live on Snowflake in scratch schemas, removed afterwards: the whole example built, 96 of 96
    tests passing. The policies masked the PII columns for a role outside the list, and showed
    them to one in it. The guide's `masking.sql` ran as written.
- **Not verified**: the guide's cloud chapters (Lambda, Glue, Auto Loader, an S3-fed Snowpipe)
  followed for real.

## 0.8.0 (2026-11-04)

Choosing the stack: how data is fetched, landed and loaded, the code for each tool, the platform's
Snowflake settings, deploying it, and why it is built that way.

- **The stack**:
  - Every part of the platform in one place: the warehouse, extract (`python`, `lambda`, `glue`,
    `none`), landing (a folder or `s3://`), load (`build`, `snowpipe`, `autoloader`), transform,
    Python, orchestrator and CI. The new ones are in murakumo.yaml's `stack:` section.
  - `murakumo stack` shows it, `stack set PART=VALUE --why ...` changes it, and `stack check` says
    when two choices do not go together. `validate` reports that too, as **MK701**.
  - **In the canvas:** a **Stack** panel (the layers icon), with each choice explained, the
    problems, and a **Why?** for every change.
- **The decisions log**:
  - Every stack and connection change is recorded in `model/decisions.yaml`: what, from what, when,
    who and why.
  - `murakumo decisions` lists it, and `decisions add` records your own.
  - The canvas has a **Decisions** tab, and the pipeline docs print the log.
- **Source connections**:
  - A source can say how its data is fetched. **REST APIs:** API key, bearer, basic or OAuth2
    client-credentials auth; page, offset, cursor or Link-header paging; a records path, a rate
    limit, and `since_param` for increments. Also SFTP, databases (by an incremental column),
    folders or S3 prefixes, and protobuf files.
  - Secrets are always named environment variables, never values.
  - `murakumo source connect / disconnect / test / request`.
  - `murakumo import protobuf` makes a raw table per message type.
  - **In the canvas:** **Connect…** on a source, or **+ Add source** → *Fetched by Murakumo*. It
    opens a request builder:
    - **Send** shows the status, time and records;
    - **Make … from these records** creates the raw table;
    - **Test**, and **Fetch now**.
  - **MK702** warns about a connection to a table the model does not have.
- **Fetching and loading**:
  - `murakumo ingest` fetches what is new into `<landing>/<source>/<table>/`, remembering
    watermarks and files copied. `landing/` is in new projects' `.gitignore`, and is added to
    existing ones on first fetch.
  - `murakumo load` loads what landed without running dbt; full tables take only the latest
    fetch.
  - `murakumo build --ingest` does both, then builds. The Build dialog has **Fetch what is new from
    the sources first**.
- **The code for each tool** (`generated/ingestion/`):
  - **Every extract:** a standalone `extract.py` with a settings file per source. It runs on
    Python 3.9 and later, needs only the standard library for REST and folders, and imports
    nothing from Murakumo.
  - **Lambda:** an AWS SAM template. **Glue:** jobs with scheduled triggers and a deploy script.
  - **Snowpipe:** a storage integration, then per environment the stage, file format, raw tables
    and a pipe per raw table, with `_ingested_at` and `_batch_id` from the file's metadata.
  - **Auto Loader:** Databricks notebooks and a file-arrival job.
  - The Airflow, Dagster and Prefect pipelines fetch and load before each schedule's build.
  - `generated/docs/stack.md` is the platform's page: its tools, sources, every setup step with
    the SQL and commands in full, and the decisions.
- **Snowflake platform settings** (the 0.8 gaps from the practices review):
  - **Transient layers:** staging and intermediate by default. dbt alone makes every table
    transient; Murakumo now keeps marts and snapshots permanent.
  - **Time travel per layer:** `time_travel_days`. **MK703** warns when a transient layer asks
    for more than Snowflake allows.
  - **A spending cap:** with `monthly_credits` set, provisioning creates a resource monitor.
- **Building it for you**: `murakumo stack deploy --env <env>` lists each step, with its SQL or
  commands and who runs it.
  - With `--apply`, Murakumo runs the warehouse steps, each as the role it needs: provisioning,
    the integration, the pipes. It also writes the CI workflow.
  - Cloud steps stay commands for you to run, with your own credentials.
  - **In the canvas:** **Stack** → **Deploy**.
- **Fixes**:
  - The canvas's tool rail fits short windows.
  - `murakumo config` keeps a space before a comment when a value grows longer.
- **Verified**:
  - The pipe, COPY with file metadata, raw-table retention and the transient-table limit, live on
    Snowflake in a scratch schema.
  - The REST extract against a local API (every paging and auth kind) and against Open-Meteo.
  - The generated script on Python 3.9.
- **Not verified**: Lambda, Glue and Auto Loader, and a Snowpipe fed by a real S3 bucket.

## 0.7.0 (2026-11-04)

Environments, deployment, and loading only what is new.

- **Environments as databases**:
  - Each environment (dev, stage, prod) has a database and the branch that deploys it, set in
    `murakumo.yaml` (`targets.environments`). On DuckDB, each is a file.
  - Projects from before 0.7, with `environments: [dev, prod]`, keep working.
  - `murakumo config` keeps the databases in step when the warehouse changes.
  - **Provisioning:** `murakumo env provision` prints the setup SQL: databases, a deployer role per
    environment, a developer role, a self-suspending warehouse, and the grants. `murakumo setup
    --connect` checks every database and offers the same SQL.
  - **In the canvas:** Settings → Project → **Environments**, with **Setup SQL…**.
- **Your own schema**:
  - `murakumo build` with no `--env` builds your own copy, in one schema named after you, in
    `MURAKUMO` (`collaboration.personal_database`), as dbt does it.
  - `env clone` copies an environment into it; `env drop` drops it.
  - **MK108** warns about table names that would collide there.
  - Pull-request copies still use a prefix, now beside the environment they target
    (`--env prod --prefix pr_1_`).
  - Each environment's Snowflake role (`SNOWFLAKE_ROLE_<ENV>`) is used by the loader and the env
    commands, as dbt uses it.
- **The git flow**:
  - **Branch commands:** `murakumo branch start` (creates dev and stage from main), `new` (feature,
    fix, chore or hotfix), `check` (the pull request follows dev → stage → main), `env` and
    `promote`.
  - **CI:** deploys each environment from its branch (migrations, then build); checks every pull
    request follows the flow; and previews a pull request on a clone of the environment it goes
    into. Branch names reach CI scripts as variables, not written into them (actionlint's
    script-injection check).
  - **Team panel:** **Start new work** and **Promote**, which shows what would move and opens the
    pull request.
  - **Setup:** says when the flow's branches are missing.
  - New projects start new work from `dev`.
- **Loading only new data**:
  - `--data` takes a folder or a glob.
  - Each file loaded is remembered by its content in `audit.loaded_files`, so it goes in once, even
    renamed.
  - A raw table's `load` now applies: `append` adds new files, `incremental` merges by primary key
    (the latest kept), `full` replaces the table when anything changed.
  - **In the canvas:** **New rows** in the inspector; the Build dialog loads every new file in
    `data/` for a table.
- **Incremental tables by load time**:
  - Modelled tables carry `_loaded_at` (the raw row's `_ingested_at`), and incremental ones read
    what was loaded since their last run, so late-arriving rows are no longer missed.
  - `lookback_hours` re-reads a margin.
  - `incremental_by: watermark` keeps the old behaviour (MK313 when it has no watermark).
  - MK308 now applies to raw tables only.
  - Existing incremental tables gain the column through a migration (the commerce example's
    `0003_loaded_at`).
- **Raw columns as text** (`modelling.raw_columns: text`, a switch in Settings):
  - Landing tables keep every column as text.
  - Staging converts with each warehouse's safe cast.
  - Values that do not convert are counted by a warning test, `murakumo_converts_to`, instead of
    failing the load.
- **`murakumo scheduler`**:
  - Runs the schedules on this computer at their times, optionally loading new files first.
  - Each run is recorded in the run log, with its schedule.
  - **In the canvas:** **Run them here** / **Stop** in the Schedules panel, running beside other jobs.
- **A best-practices review** ([docs/practices.md](docs/practices.md)): Murakumo against Kimball,
  dbt, Snowflake and *Designing Data-Intensive Applications* practice, with the gaps sorted into
  milestones.
- **Fixes**:
  - **MK107:** a table named like a generated staging model is refused.
  - **Import cycle:** a circular import between lineage and the pipeline docs is fixed.
- Docs: [working as a team](docs/teamwork.md) rewritten for environments, your own schema and the
  flow; loading, increments and text columns in [generating](docs/generating.md); the scheduler and
  per-environment deploys in [orchestration](docs/orchestration.md); environments in
  [Snowflake](docs/snowflake.md); end-to-end steps 14.9, 14.10, 15.5 and Part 16 updated.

## 0.6.0 (2026-11-04)

Everything the CLI does, in the canvas: run builds and checks as jobs with live output, set the
project up, load data, see what ran, and test a change before it is merged.

- **Jobs and Activity**: builds, verify, Python runs, backfills, migrations, clones and exports run
  in the background as the same `murakumo` commands, with their output as it comes, the command to
  copy, and **Stop**. The canvas names a kind of job and its options, and the server checks each
  before it builds the command line. `POST /api/jobs`, `GET /api/jobs/{id}`.
- **Settings**: the project's choices (warehouse, orchestrator, pattern, Python pipeline, plain
  SQL, run log, parent branch, personal schemas), saved to `murakumo.yaml` keeping its comments,
  and undoable; and **This machine**, the `murakumo setup` checklist with how to fix each item.
- **Build and load data**: a **Build** dialog (files, made-up rows, environment, schedule, run date,
  prefix) that shows its command; files are added to `data/`, which git ignores; dictionaries are
  uploaded for import from **Sources**. Rows a file could not fit are reported as rejected, in the
  terminal too.
- **Runs**: the run log by day, runs, loads and failed tests. Each schedule shows when it last ran
  and how it ended (the evidence a schedule ran), with **Run now**. Backfill cumulative tables
  from the inspector, and run the pipeline as Polars or PySpark from **Activity**.
- **Test my change**: copy production into a pull-request prefix, apply this branch's migrations,
  build and test there, drop it, one step at a time. **Compare with** any branch shows the impact
  report, ready to copy into a pull request. **Export as plain SQL** from Activity.
- **Fixes**:
  - Scheduled builds skipped relationship tests between two of their own tables (dbt applies
    cautious selection per criterion). Selectors now select a tag per schedule, and models
    carry `schedule_<name>` tags.
  - A table named like the staging model generated for a raw table (`stg_<source>__<table>`) is
    now caught by validation (MK107) instead of failing in dbt.
  - Job output has no colour codes or spinner frames; DuckDB is read without taking a build's lock.
- Docs: the canvas page's **Running the project**, the new endpoints, and an **In the canvas** note
  beside each terminal step of the end-to-end test.

## 0.5.0 (2026-11-04)

A canvas made for large models, modelled on dedicated data-modelling tools: undo, an Explorer,
view modes, colour labels, notes, diagrams, auto-layout and export, and the fixes from the 0.4
end-to-end run.

- **Undo and redo**: every canvas change, ⌘Z and ⇧⌘Z, with buttons that say what they would take
  back. A removed table returns with its columns, mappings, relationships and place. Undo refuses
  when the files changed some other way since, so it never overwrites work made in an editor or
  by `murakumo sync`. `POST /api/undo`, `/api/redo`.
- **A new layout**: a tool rail (add a table T, a relationship R, a note N; arrange; export; the
  panels; docs; theme; help), an Explorer of every table by domain, layer, colour label and
  source, with search, ⌘K to find any table or column, docked panels, light and dark themes, and
  sharper tables: header bands in the layer's colour, key and foreign-key icons, striped rows.
- **Table display**: all columns, keys only, names only or descriptions (⌘1 to ⌘4); collapse a
  table to its name (lines then meet its header), or every table over a number of columns.
- **Organising large models**: colour labels with a meaning, given to one table or several; sticky
  notes; diagrams, saved views of some tables as tabs; auto-layout (left to right or top to
  bottom, with a way back); PNG and SVG export; and a bar for several selected tables (domain,
  layer, colour, arrange, remove), each one change and one undo. All kept in `model/layout.yaml`,
  so the team sees them; the model files do not change. `PUT /api/canvas`, `PATCH /api/entities`,
  `POST /api/entities/remove`.
- **Problems on their column**: each problem says which column it is about; the column is marked
  red or amber on the canvas, with the message on hover, and clicking a problem (or a search
  result) goes to the column and points it out.
- **Columns from the source**: **+ From a source table** lists first the tables a table most
  likely reads from (named in its mappings or relationships, the raw table a staging table is
  named after, the layer before); a new table with no columns offers it straight away; the
  columns arrive with their types, descriptions and mappings in one change.
  `POST /api/entities/{id}/columns/copy`.
- **Expressions**: calculated columns carry ƒ on the canvas, with the expression on hover; a
  lineage link's expression is drawn over the tables and wraps, instead of hiding behind them.
- **Help**: a first-run tour of the canvas, a keyboard shortcuts sheet (?), and a help menu.
- Docs: [the canvas](docs/canvas.md) and the end-to-end test's canvas parts rewritten for the new
  layout, with steps for each new feature.

## 0.4.0 (2026-10-30)

Working as a team, and seeing what each run did: sync with the parent branch, typed diffs and
impact reports, personal and pull-request environments, run logs, cumulative tables, and the
pipeline as Polars or PySpark jobs.

- **Sync**: `murakumo sync` brings in the parent branch (`collaboration.base_branch`, main or dev):
  it says what is coming in, in model terms; merges; takes the parent's side of conflicts in
  generated code; stops on conflicts in model files (`--continue`, `--abort`); and, when both
  branches wrote migrations, re-plans yours after the parent's so no number is used twice. Then
  it regenerates and commits. `--check` only reports. The canvas has the same in a **Team** panel,
  with **Get latest** and **Commit**.
- **Typed diffs and impact reports**: `murakumo diff [REF]` matches changes by id, so a rename is a
  rename, and classes each as additive, compatible or breaking. Under each change it lists what it
  reaches through the column lineage (columns, tables, tests, metrics, schedules), the generated
  files that change, and the migration it needs. `--format markdown|json`, `--fail-on breaking`.
  The CI workflow writes the report on each pull request's summary. In the canvas, a breaking
  edit is previewed and asks before it is saved.
- **Personal environments**: on Snowflake, `dev` builds go to schemas of your own (`dev_<name>_*`,
  from git's user name or `MURAKUMO_DEVELOPER`). `murakumo env` shows them, `env clone` copies
  production into a prefix (zero-copy on Snowflake), and `env drop` removes them. `build
  --prefix` builds anywhere.
- **Migrations on clones**: `murakumo migrate --apply [--prefix]` runs the migrations a warehouse
  has not run yet and records them in `audit.murakumo_migrations` (`--mark-applied` only records
  them). `murakumo ci --preview` builds each pull request on a clone of production with its new
  migrations, and drops the clone when the pull request closes (GitHub; on GitLab, a review
  environment).
- **Run logs**: every dbt run records itself in the `audit` schema: `run_log`, `model_runs` (rows
  affected, rows in the table, seconds), `dq_results` (each test and its failing rows),
  `row_counts` (rows ingested and rejected per load, with the reason), and a `daily_summary` view.
  Loading CSV files into DuckDB now rejects rows that do not fit the model's types, and counts
  them, instead of failing the load. `murakumo runs` shows it all. `quality.run_log: false`
  turns it off.
- **Cumulative tables**: `kind: cumulative` keeps each key's whole history, one row per key per
  day (its active dates and measures, newest first, and `days_active`), built one day at a time
  (`run_date`). `murakumo backfill TABLE --from --to` fills a range. The canvas edits them
  (**Per**, **Active on**, **Each day**). Checks MK310, MK312 and MK508.
- **Polars and PySpark**: `generation.python_engine: polars|pyspark` writes the pipeline as Python
  jobs in `generated/python/<engine>/`, from the same SQL as the dbt project, with incremental,
  cumulative and snapshot tables, the tests and the audit tables (as Parquet, or Delta on Spark).
  `murakumo run --engine polars|pyspark` runs them. Extras: `murakumo[polars]`,
  `murakumo[pyspark]`.
- **Fixes**: BigQuery and Databricks column descriptions with a quote in them are now escaped.
- **Migrations**: a new snapshot says it "starts keeping history (SCD2)".
- Docs: [Working as a team](docs/teamwork.md); and cumulative tables, run logs, Python pipelines,
  pull-request previews and the end-to-end test brought up to date.

## 0.3.0 (2026-10-29)

Running the project: schedules, pipelines for the orchestrator, plain SQL, pipeline docs and CI.

- **Schedules**: `orchestration.schedules` in `murakumo.yaml` says when each part of the project is
  refreshed, as cron times in a chosen time zone, selecting tables by `domain:`, `layer:`,
  `source:` or name (with `+` for upstream or downstream); one schedule can refresh the rest.
  Each becomes a dbt selector. `murakumo add/rename/remove schedule`, `murakumo schedules`,
  `murakumo build --schedule`, checks MK601-MK604, and a Schedules panel in the canvas that says
  each time in words with its next run and highlights its tables.
- **Pipelines**: `generate` writes a pipeline per schedule for the project's orchestrator to
  `generated/orchestration/<orchestrator>/`: Airflow and Cloud Composer DAGs (Cosmos), Dagster
  definitions (dagster-dbt), Prefect flows and deployments, a Databricks asset bundle, Snowflake
  tasks, and a run script with steps for AWS Glue, Step Functions, GCP Workflows and Azure Data
  Factory; each with a README. The Airflow, Dagster and Prefect ones were run on DuckDB.
  `murakumo setup` points at them.
- **Plain SQL**: `murakumo export sql` writes the project as ordered scripts that need only the
  warehouse, with incremental models and SCD2 snapshots written out as statements and every test
  as a query; they build the same tables as dbt. `generation.plain_sql: true` keeps them in
  `generated/sql/`.
- **Pipeline docs**: `generate` writes `generated/docs/index.html`, a self-contained page of
  domains, schedules, sources and every table's columns, lineage and tests, and a Markdown page per
  table. `murakumo docs` and **Docs** in the canvas open it.
- **CI**: `murakumo ci` writes a GitHub Actions or GitLab workflow at the repository root that runs
  `validate --strict`, `generate --check`, `migrate --check` and `verify` on every change, with an
  optional production build after approval; it never overwrites a file changed by hand without
  `--force`. A CI panel in the canvas writes it and runs the same checks locally.
- `python -m murakumo` runs the CLI.

## 0.2.0 (2026-10-29)

Working on the model in the canvas: sources, calculated columns, column order, domains and lineage.

- **Sources panel**: lists each source with where it is read from and its tables; **+ Add source**
  declares one in `murakumo.yaml` (keeping its comments): a database by the name of an environment
  variable holding its connection string, Snowflake by its database name, or a dictionary, data
  file or OpenAPI spec by path or URL. **Browse tables** lists a database's schemas, tables and
  columns from its catalogue (never its rows) to tick the ones to import, after a preview of what
  would change; other sources get **Re-import** with the same preview. Errors never show
  passwords. `murakumo import database --table` imports only some tables; `murakumo remove
  source` removes a source with no tables.
- **Calculated columns**: an expression editor with syntax highlighting, suggestions for tables,
  columns and functions as you type, live checks (the columns it reads, whether they exist, the
  type it returns, its SQL in your warehouse's dialect) and a preview on made-up rows in DuckDB.
  **+ Calculated** adds a column and its expression in one step; saving an expression sets the
  mapping's sources to the columns it reads.
- **Column order**: drag columns in the inspector (or Alt+↑/↓), or `murakumo move column`.
  Generated tables and models follow; existing tables keep their order, which the Changes panel
  and `murakumo migrate` say.
- **Domains**: a Domains panel to add, rename (entities follow), give owners to and remove
  domains; **Show domains** colours tables and shades each domain's area; a domain filter; the
  inspector picks an entity's domain from a list. `murakumo add/rename/remove domain`. Each domain
  with models becomes a dbt group owned by its owner, and models get `group` and `meta.domain`.
- **Lineage**: `murakumo lineage entity.column` shows where a column comes from (or, with
  `--down`, what uses it), as a tree or JSON. On the canvas, **⇄** beside a column (or
  **Lineage** for a table) highlights its lineage across tables with animated links that show
  their expressions, fades the rest, and lists the columns it comes from and feeds, to follow
  further. dbt columns carry `meta.lineage`.
- The API gains endpoints for column order, expressions, lineage, domains and sources; see
  [the canvas](docs/canvas.md#the-api).

## 0.1.1 (2026-10-29)

Fixes and polish from the first end-to-end test.

- **Build**: `murakumo build` builds the project into its own warehouse (the DuckDB file, or the
  Snowflake database) and keeps the tables, unlike `verify`, which drops everything afterwards.
  `--sample N` fills empty raw tables with made-up rows, `--data` loads files, `--env` picks the
  environment.
- **Snowflake**: when the database does not exist, `setup --connect` prints the SQL that creates it
  as a block to paste into a Snowsight worksheet, with where to run it.
- **`init`**: numbered choices with a description of each; a clearer question about the example
  model; **Ready.** in green and **Next:** with the `cd` to run; a pointer to `murakumo --help`.
- **`murakumo config`** changes the warehouse, orchestrator or modelling pattern after `init`,
  keeping the manifest's comments.
- **Setup checklist**: a key to the symbols; numbered steps for installing the chosen orchestrator
  and pointing it at the dbt project, with links to its documentation (and what to set up in the
  cloud for managed ones); checks for the macOS developer tools and git's name and email; projects
  inside an existing repository are recognised; plainer explanations in the Project section.
- **Canvas**:
  - relationships in crow's-foot notation, with a **Notation & tips** legend; labels show on
    hover; hovering or selecting a line highlights the joined columns;
  - lines no longer disappear behind tables dragged one above the other;
  - Shift+drag selects several tables to move them together;
  - the inspector can be resized, has a column search, a **+ Relationship** form, and
    **+ From another table** to add columns by ticking them; mapping sources can be ticked in a
    list;
  - one button writes the code, or the migration and the code together, with plainer messages
    (*Wrote 12 files to generated/*).
- The manifest template lists storage locations for each cloud and a local folder.

## 0.1.0 (2026-10-27)

The first release: design a data model, generate a tested dbt project and DDL from it, and keep
both in step as the model changes.

- **Projects**: `murakumo init` asks about the warehouse, orchestrator and modelling pattern (or
  takes `--target`, `--orchestrator`, `--pattern`), and starts from an NYC taxi example or empty.
  `murakumo setup` checks what the project needs on this machine (drivers, variables, keys,
  connections, orchestrator) and says how to set up each.
- **Model**: a YAML model with stable ids (sources, entities, columns, relationships, mappings,
  tests, metrics), validated with rule codes MK0xx-MK3xx; `murakumo validate`, `add`, `rename`
  (references follow) and `remove`.
- **Import**: data dictionaries (CSV, Excel), databases (PostgreSQL, DuckDB, Snowflake), files
  (Parquet, CSV, JSON), OpenAPI and JSON samples. Re-imports keep hand edits and ids.
- **Generate**: a dbt project (staging, intermediate, marts; contracts; SCD2 snapshots;
  incremental models; tests from the model) and DDL for DuckDB, Snowflake, Databricks, BigQuery,
  PostgreSQL, Redshift, Fabric and Synapse. Deterministic output, with `--check` for CI.
- **Migrations**: `murakumo migrate` turns model changes into numbered SQL migrations for tables
  that keep data (landing tables, incremental models, snapshots), matched by id so renames keep
  their data; data-losing statements are left for a person to uncomment.
- **Verify**: `murakumo verify` builds and tests the generated project on DuckDB or Snowflake,
  with synthetic rows that respect the model's keys and rules, or real files and tables.
- **Install**: `uv tool install "murakumo[canvas,verify,duckdb]"`, `pip install ...`, or
  `npm install -g murakumo` (the npm package runs the same PyPI release through uv).
- **Canvas**: `murakumo canvas` opens a visual editor that reads and writes the same YAML: entities
  and columns, drag-to-relate, mappings, the generated code for each entity, diffs of what
  generating would change, migrations, and validation, all live as files change on disk.
