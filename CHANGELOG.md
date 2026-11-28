# Changelog

## Unreleased

**Added**

- **Dynamics choices come with their labels.** A choice column holds a number (`100006`); its
  label (*Approved*) is now offered as a column of its own beside it, `statuscode_label`, in the
  table's column list. Yes-or-no columns and lookups have one too (a lookup's is the name of
  what it points to). Murakumo asks Dynamics for them in the `Prefer` header. Connections made
  before this ask once a table is added or the source is connected again.

## 1.3.2 (2026-11-27)

**Changed**

- **Tables and columns are chosen, not all added.** A connected source lists its tables, each
  marked on the canvas, fetched, or neither. Choosing one shows its columns from a sample of rows:
  type, how often it has a value, and a few values. Only the ticked columns go on the canvas, and
  for Dynamics only those are fetched (`$select`); its id and `modifiedon` are always kept. The
  same list is in **Add table** (T) for a source Murakumo fetches, and `murakumo source tables` /
  `murakumo source add-table`. 1.3.1 added every fetched table with all its columns; a fetched
  table not on the canvas is now said so, and not loaded.

**Added**

- **Fetch and load shows what it does** as it goes, under its button, and stays on the connection.
- **Show what it holds**, on a column's card: how many rows have a value, and its most common
  values, on a sample of the loaded table.
- **Select columns…** in the inspector: tick several and delete them as one change. Deleting a
  Dynamics table's columns stops fetching them.

**Fixed**

- With both side panels open, the canvas could be squeezed to a sliver, and the load hint's text
  and button with it. The left panel now gives way so the canvas keeps room, the hint stacks its
  button under its text when narrow, and a source's buttons stay in view in a narrow panel.
- A short table list hid the tables that were not the organisation's own.
- Deleting the column that says when a row changed left the table pointing at it (MK203).

## 1.3.1 (2026-11-27)

**Fixed**

- **Fetched data that never became a table.** A source whose requests had no raw table in the
  model (set up before **Add tables**, or by hand) was fetched, but the load skipped it and said
  it had loaded. Its data showed in Query → Landed and nowhere on the canvas. **Fetch and load**,
  `murakumo load` and `murakumo build --ingest` now make each missing raw table from the records
  that landed (a Dynamics table with its id as key, merged by it), then load it.
- The Dynamics table list showed such tables as *fetched* and would not let them be ticked. They
  now say **not a table yet**, and can be added.

## 1.3.0 (2026-11-27)

Connecting a source without the fiddly parts: secrets typed once and kept out of git, Microsoft
Dynamics 365 from four fields, any other API from a request that already works, and one button to
fetch and load.

**Added**

- **Secrets in the project's `.env`.** A secret field on the canvas takes the value and keeps it in
  `.env`, which Murakumo adds to `.gitignore` and makes readable only by you. The canvas, every
  command and the generated extract code read it themselves: nothing has to be exported. A
  variable set in the environment (CI, a scheduler) still wins. `murakumo secret set`, `list` and
  `remove`; `murakumo setup` says which connection secrets are missing.
- **Microsoft Dynamics 365 / Dataverse:** the environment's URL, the tenant, the client id and the
  secret. Murakumo signs in, lists the tables (the organisation's own first) and makes each raw
  table you tick from its first rows. Every row is fetched, following `@odata.nextLink`, then only
  what changed (`modifiedon`), and each table is merged by its id, so nothing is lost. Sign-in
  problems are said plainly: a wrong secret, an unknown client id or tenant, an app that is not an
  application user.
- **Another API from a request that works:** paste a URL or a curl command and the base URL,
  request, parameters and way of signing in are filled in. The secret goes into `.env`.
- **Fetch and load**, on the connection and in the hint above the canvas: fetches a source and
  loads what landed (`murakumo ingest --load`), ready to query.
- REST connections: `next_url` paging (the next page's URL in the response), `since_format` for
  an incremental parameter written as an expression (`modifiedon ge {since}`), and a fresh OAuth
  token before the old one runs out. **Send** finds where the records are in a response by itself,
  including columns side by side.
- The panels on the left (Sources, Explorer and the rest) can be dragged wider, like the
  inspector, and keep their width.

**Fixed**

- The **Headers** and **Parameters** boxes dropped every key you typed until the line had an `=`,
  so they could only be pasted into. They are now name and value rows.
- Fields whose names are not plain column names (Dynamics' `@odata.etag`, `Trip Distance (mi)`)
  stopped the load with *has no column odata_etag*. They now land under their column's name.
- **Load raw data** with a connected source loaded nothing. It now fetches the sources and loads
  them; the Build dialog fetches them first by default, and says when there is nothing to load.
- Signing in with OAuth reported a bare *HTTP Error 401* or *the response is not JSON*. It now
  says the token endpoint's reason.

## 1.2.0 (2026-11-24)

Exploring the data before modelling it: load the raw tables alone, look at a table before it is
built, ask it the right questions, and find which tables a column joins, cheaply. And a Simple view
for people who are not data engineers.

**Added**

- **Load the raw data only.** `murakumo build --raw-only` creates and loads the raw tables and stops:
  no dbt (which need not be installed), the cheapest build. On the canvas, a hint above the canvas
  offers it while no raw table holds data, and says what loaded when it is done; the Build dialog has
  **Load raw data only**.
- **Preview a table before it is built** (`murakumo preview TABLE`, the inspector's new **Data**
  tab): its model runs as one query, with the models it reads folded in down to the raw tables,
  each read as a sample of 10,000 rows, so the cost does not grow with the data. `--full` (**On all
  the data**) says what it would read first.
- **Questions to ask a table** (`murakumo suggest TABLE`, the Data tab, under the query editor):
  queries written from the model (its key, dates, categories and joins) from templates, with no AI.
- **Related columns** (`murakumo relations TABLE.COLUMN`, the column's card): where a column
  appears again, found in tiers to keep compute down. Names and types first, with no query; then
  column summaries from samples, cached until a table's row count changes; then the overlap of
  values on samples, only for candidates whose ranges can meet. **Add relationship** adds what it
  found. `murakumo find VALUE` (**Look for a value**) searches only the id columns a value can be in.
- **Simple or Full**, in the top bar: Simple uses plain words, opens tables on their data, keeps
  how tables load and who sees what under **Advanced**, and leaves the platform's panels off the
  rail. The model and the generated code are the same either way.
- `/api/explore/*` endpoints for all of the above.

## 1.1.1 (2026-11-22)

Fixes for the canvas found while running the end-to-end test, and a test that now covers every
feature.

**Fixed**

- **Esc** lets go of the selected tables, closes the inspector, and closes any open menu and the
  tour.
- **⌘/Ctrl+click** only adds a table to the selection, so several can be moved without the
  inspector opening.
- **Type boxes** open the whole list of types on a click, whatever they hold; typing narrows it.
- **The page never scrolls.** The tool rail fits the window on short screens, the inspector's
  Model and Code tabs stay at its top, and the heads of the panels line up.
- **The rail's menus** close when another opens or anything else is clicked.
- **The query console** fits its drawer; its pickers wrap instead of being cut off.
- **The tour** lets you try what each card points at while it is open.

**Added**

- **A column's details.** Click a column on the canvas: the inspector shows its type, key,
  whether it may be empty, where it comes from and its expression, its joins, checks and what
  uses it, its description, and its lineage.
- **A mapping's sources in full.** Its box shows the first and how many more; a click lists them
  all, then every other column to tick.
- **Show the names** on the rail, to see every button's name beside its icon.
- **Query a file before building.** With nothing built yet, the query console offers to read the
  data files directly, so the raw data can be explored while modelling.
- **The minimap** is behind a map button under the zoom buttons, off by default, and smaller.
- **The end-to-end test** walks the tour card by card and adds steps for the above, redo, the
  shortcuts list, removing a relationship, arranging from the rail, and the commands it never
  ran: `murakumo move column`, `schema`, `docs` and `decisions add`.

## 1.1.0 (2026-11-19)

Data quality you configure on the canvas and that runs everywhere, data contracts, and a query
console for every table at every stage. Each has a canvas path, a step in the replication guide,
and a part in the end-to-end test.

**Added**

- **Quality rules with an action.** New rules: `regex`, `length`, `null_share`,
  `distinct_count`, `recent`, `row_count`, beside not empty, unique, one of a list, between,
  an expression and unique together. Each rule says what a failure does: `fail` (stop the run),
  `warn`, or `quarantine`.
- **Quarantine.** On a raw table, rows that fail a quarantine rule are set aside in
  `<staging model>__quarantine`, with the first rule each failed; the rest carry on. Made-up data
  for `verify` keeps to the rules. MK326 to MK330.
- **The same rules everywhere.** dbt tests, checks in the PySpark and Polars jobs, and, with
  `quality.framework`, a Great Expectations suite and Soda checks per table, each with a
  `run.py` that exits 1 on a failure.
- **Reconciliation** (`quality.reconciliation`): a test that every raw row reached staging or was
  set aside.
- **Write-audit-publish** (`quality.write_audit_publish`): `murakumo build` builds and tests in
  `wap_` schemas, and publishes only when every test passes.
- **Data contracts (ODCS v3.0.2).** A contract per table in `generated/contracts/`, validated
  against the standard's JSON Schema in the tests; `murakumo import odcs` brings one in;
  `murakumo contract` checks each load against its contract, and `murakumo build` stops on a
  breach of one that blocks (`contracts.enforce_on: [sources]`). Each table's `contract:` sets its
  version, status and enforcement.
- **Monitoring** reports a load that had many rows set aside (**quarantined**).
- **The query console.** The canvas's **Query** panel runs read-only SQL on the source, what
  landed, raw, staging, quarantine, marts, snapshots, the audit tables and the Iceberg catalog,
  and on files in a folder, S3, GCS or Azure (Parquet, CSV, JSON, Excel, Iceberg, Delta). Only
  reads run; results stop at a row limit and a timeout.
- **In the canvas:** the inspector's **Quality rules** and **Data contract**, and its **Query**
  button; **Health → Data contracts**; **Settings → Data quality**. Rules and accepted values can
  now be edited through the canvas API.

**Fixed**

- `murakumo run --sample` ignored `--data` files for the tables it gave them to.
- `murakumo run --data` read relative paths from a temporary folder, so they were not found.

**Verified:** quarantine, reconciliation and write-audit-publish with real dbt builds on DuckDB;
quarantine in the Polars job; the generated Great Expectations (1.25) and Soda Core (3.5)
checks run against a DuckDB build; every generated contract against the ODCS v3.0.2 JSON Schema;
a contract round trip; the console against DuckDB and files; the canvas panels in a browser.

**Not verified:** Great Expectations and Soda against the cloud warehouses; the console against a
source database, cloud storage and an Iceberg catalog; quarantine on warehouses other than
DuckDB (the SQL is generated for each, and checked in the tests).

## 1.0.0 (2026-11-14)

The rest of the original plan's Evolution and Breadth phases: more ways to keep history, the Data
Vault, a semantic layer, the platform as Terraform, open tables in a catalog, and streaming. Each
has a canvas path, a step in the replication guide, and a part in the end-to-end test.

- **Slowly changing dimensions of types 3, 4 and 6**, built as themselves (not as 1 and 2):
  - `scd3`: `previous_<column>` beside each tracked column, compared on each incremental run;
  - `scd4`: the current version in the dimension, every version in `<name>_history`;
  - `scd6`: type 2 with `current_<column>` on every version.
  - `tracked:` picks the columns (MK318, MK319). In the canvas: the inspector's **History** and
    **Tracked**.
- **The Data Vault pack**: hubs, links and satellites, insert-only, with hash keys of the trimmed,
  upper-cased business keys, `load_date` and `record_source`. A satellite adds a row when its
  `hashdiff` changes; `<satellite>_current` has each key's latest row (MK320, MK509). The commerce
  example has a small vault.
- **The semantic layer**: dbt semantic models and metrics (MetricFlow) from `model/metrics.yaml`.
  Simple, derived (`sum(a) / count(*)`) and filtered metrics; SCD2 dimensions joined by validity;
  the time spine. Metrics gain `label` and `time` (MK210 to MK212). In the canvas: **Bus matrix**
  → **Metrics**.
- **Terraform** (`stack.iac: terraform`): a module each for Snowflake, AWS and Databricks.
  - Snowflake's access roles (`<DB>_READ`, `<DB>_WRITE`) are now kept apart from the functional
    roles made of them, in Terraform and in the provisioning SQL alike.
  - The AWS module (bucket, Lambda or Glue, Snowpipe's role) takes an `endpoint`, to try it on
    LocalStack.
  - `murakumo stack deploy` lists a step per module.
- **Open tables** (`targets.lakehouse`): the chosen layers as Iceberg tables in a catalog.
  Snowflake-managed Iceberg on an external volume (with sync to Open Catalog); Iceberg, Delta or
  Hudi on Databricks; and from DuckDB into an Iceberg REST catalog, with `generated/lakehouse/`
  starting S3 (LocalStack) and Polaris locally. In the canvas: **Settings** → **Open tables**.
- **Kafka sources and streaming**: a `kafka` connection kind, read by the extract script on each
  schedule (committing only after the files are written), or continuously by a Flink SQL job, a
  Spark Structured Streaming job, or the Snowflake Kafka connector through Snowpipe Streaming
  (`generation.streaming.engine`). `generated/streaming/` starts Redpanda and Flink locally.
  Streaming engines' part files load like any other landed file. The `kafka` extra installs
  kafka-python.
- **Fixed:** the guide's rebuild check now leaves out `load_date`, which differs between builds.
- Docs: modelling patterns, the stack, model and generating codes, practices; end-to-end Part 19.
  Version 1.0.0.

After a review of the project against the tools it competes with, the rest of 1.0 closes what the
review found and adds what they have that Murakumo did not.

**Fixed**

- **The canvas server refused nothing sent from another site when the request had no body.** A web
  page open in the same browser could start a generate, a CI run, an undo or a git sync. Every
  change is now refused when the browser says it comes from another site (`Origin`,
  `Sec-Fetch-Site`). The canvas, the Vite dev server and scripts are unaffected.
- **Incremental models used `delete+insert` on every warehouse.** BigQuery has no such strategy,
  and Databricks has had `merge` for longer: both now use `merge`.
- **`load: cdc` was loaded as append, and nothing said so.** It is now a change feed (below).
- **Accumulating snapshots failed their contract on PostgreSQL**: the time between milestones came
  back as an integer where the contract says bigint. It is now cast. Found by running `verify` on
  PostgreSQL for the first time.
- **The Fabric and Synapse dbt profiles had PostgreSQL's settings.** They now sign in with a
  service principal, as those adapters expect.
- **A new project named like `taxi_platform` did not build on another warehouse.** When the
  database or orchestrator name was as wide as the column `murakumo.yaml`'s comments line up on,
  the comment became part of the name. Found by the end-to-end test's Part 21.
- **dlt loaded an API answer made of columns of values as one row.** Open-Meteo's
  `{"time": [...], "temperature_2m": [...]}` is now one row per position, as Murakumo's own
  extract has always read it.
- **dlt could not take over a raw table Murakumo had already loaded**: it adds its two columns
  NOT NULL, which a table with rows refuses. The pipeline now adds them itself.
- **`murakumo ingest` only said to run the dlt pipeline.** It now runs it; `murakumo[dlt]` installs
  dlt beside Murakumo.
- **An alert written as the documentation shows it did not load**: YAML reads the key `on` as
  true.
- The README still said 0.9.0. The package is now classified beta.

**Added**

- **Change feeds** (`load: cdc`): the raw table keeps every change, and staging is each key's
  latest change without the keys a change deleted. `cdc.operation`, `cdc.deletes`, `cdc.order`;
  MK321 to MK325. In the canvas: the inspector's **Operation** and **Latest by**.
- **`verify` and `build` on every warehouse**: PostgreSQL, Redshift, Databricks, BigQuery, Fabric
  and Synapse, beside DuckDB and Snowflake, each with an install extra. Environments, `runs` and
  `monitor` reach them too.
- **The four cloud orchestrators are generated**, where there was a script and a note: AWS Step
  Functions (a state machine on Fargate, EventBridge schedules), AWS Glue (a Python shell job and
  triggers), Google Cloud Workflows (a Cloud Run job, Cloud Scheduler) and Azure Data Factory (a
  pipeline that starts a Container Apps job, schedule triggers).
- **Fetching with dlt, Airbyte or Fivetran** (`stack.extract`): a dlt pipeline per source that
  loads straight into the raw tables; for Airbyte and Fivetran, what to set up and a view per raw
  table over what they land.
- **Importing an existing dbt project** (`murakumo import dbt`): sources, models, keys,
  relationships, and where each column comes from, read from the compiled SQL.
- **The assistant** (`murakumo ai`): descriptions, personal-data marks, mappings and new tables
  suggested by a language model. Off until a project turns it on, with its own key; it is sent the
  model, never rows; every suggestion is shown first and applied through the usual edits.
- **An MCP server** (`murakumo mcp`): the model as tools for an agent of your own.
- **Monitoring** (`murakumo monitor`): loads far from their usual size, late tables, rejected rows
  and failed runs, from the run log, with alerts to Slack, Teams or a webhook.
- **Cost** (`murakumo cost`): roughly what the schedules cost a month, with every assumption shown
  and your own prices under `costs:`.
- **Lineage for a catalog** (`murakumo catalog export | send`): OpenLineage events with column
  lineage.
- **Slim CI** (`murakumo verify --changed-since`): builds what a change reaches. The generated CI
  uses it on pull requests.
- **Row-access policies** (`row_access:` on a mart, Snowflake): which values of a column each role
  sees; MK707 to MK709; a deploy step and a guide step.
- **In the canvas:** a **Health** panel (monitoring, cost, catalog lineage, importing a dbt
  project) and an **Assistant** panel; the inspector's change-feed and row-access settings.
- **The canvas has tests** (Vitest), run in CI. `CONTRIBUTING.md` and `SECURITY.md`.

**Verified**

- Lint, and the Python and canvas test suites.
- `verify` on PostgreSQL, live, in a container: the commerce example, 113 of 113 tests, with the
  incremental rebuild, and its schemas dropped afterwards. CI runs the same against its PostgreSQL.
- Change feeds: the staging SQL run on DuckDB, and parsed for all eight warehouses.
- dlt: the generated pipeline run for real into DuckDB, from files and from a database; its REST
  settings accepted by dlt without sending a request.
- Importing dbt: on dbt projects Murakumo wrote and dbt compiled.
- The cloud orchestrators' Terraform parses (`terraform fmt`).
- The catalog's `send`, against a local HTTP endpoint. The two canvas panels, in a browser.

**Not verified** (no account to run them against)

- `verify` and `build` on Redshift, Databricks, BigQuery, Fabric and Synapse.
- The four cloud orchestrators deployed, and the dlt pipeline into a warehouse other than DuckDB.
- Airbyte's and Fivetran's views against what those tools really land. Fivetran's column naming is
  assumed to be lower snake_case.
- Row-access policies on a live Snowflake account.
- The assistant against the provider's API: it is tested with a stand-in, to spend nothing.
- The cost command's starting prices: list prices as they were known when written. Use your own.

## 0.9.0 (2026-11-04)

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
