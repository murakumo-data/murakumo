# Choosing the stack

A data platform is more than the warehouse and dbt. Data has to be fetched from the sources (APIs,
SFTP servers, databases, files), landed somewhere, loaded into raw tables, transformed, scheduled
and deployed. Murakumo calls these choices the **stack**: you pick each part, and it writes the code
for every choice. The pipeline docs then say, step by step, how to set the platform up by hand,
so you can rebuild it without Murakumo.

```bash
murakumo stack                                   # every part, and anything that does not go together
murakumo stack set load=snowpipe landing=s3://acme-landing/taxi --why "loads within a minute"
murakumo stack check                             # exits 1 when two choices do not go together
```

In the canvas, the **Stack** panel (the layers icon on the rail) does the same.

## The parts

| Part | Choices | Where it lives in murakumo.yaml |
| --- | --- | --- |
| Warehouse | duckdb, snowflake, databricks, bigquery, redshift, postgres, fabric, synapse | `targets.warehouse.type` |
| Extract: what fetches the data | `python`, `lambda`, `glue`, `none` | `stack.extract` |
| Landing: where fetched files land | a folder in the project, or `s3://bucket/prefix` | `stack.landing` |
| Load: what loads landed files into raw tables | `build`, `snowpipe`, `autoloader` | `stack.load` |
| Transform | `dbt` | `generation.transform` |
| Python | `none`, `polars`, `pyspark` (the pipeline written as Python too) | `generation.python_engine` |
| Orchestrator | airflow, dagster, prefect, databricks_workflows, snowflake_tasks, and others as a starting script | `generation.orchestrator` |
| CI | `github`, `gitlab`, `none` | `stack.ci` |

```yaml
stack:
  extract: python      # python | lambda | glue | none
  load: build          # build | snowpipe | autoloader
  landing: landing     # a folder, or s3://bucket/prefix
  ci: github           # github | gitlab | none
```

Some choices only work together, and `murakumo stack check` (and `murakumo validate`, as MK701)
says so:

- Snowpipe loads into Snowflake, from S3.
- Auto Loader runs on Databricks, from cloud storage.
- Lambda and Glue land files in S3.
- Snowflake tasks run in Snowflake.

## The decisions log

Every change to the stack is recorded in `model/decisions.yaml`: what changed, from what to what,
when, by whom, and why (`--why`, or the **Why?** box in the canvas). Add decisions of your own too:

```bash
murakumo decisions add "Zones are fetched daily, not hourly" --why "they change a few times a year"
murakumo decisions                              # the log, oldest first
```

The pipeline docs print the log (`generated/docs/stack.md`), so whoever rebuilds the platform
knows why it is built the way it is.

## Connecting a source

A source in murakumo.yaml can say how its data is fetched: its `connection`. Each kind of
connection fills one or more raw tables. **Secrets are never written in murakumo.yaml**: each
field ending in `env` names the environment variable that holds the secret, on the machine that
runs the extract.

### REST APIs

```yaml
sources:
  - name: weather
    connection:
      kind: rest
      base_url: https://api.weather.example/v1
      auth:
        type: bearer            # none | api_key | bearer | basic | oauth2
        env: WEATHER_TOKEN
      paging:
        type: page              # none | page | offset | cursor | link
        size_param: per_page
        size: 500
      requests_per_second: 5
      endpoints:
        - table: readings       # the raw table its records fill
          path: /readings
          params: {station: all}
          records: data.items   # where the records are in the response
          since_param: since    # gets the table's watermark, so each run asks only for what is new
```

| Auth | Fields |
| --- | --- |
| `api_key` | `env`, `name` (the header or parameter), `location` (`header` or `query`) |
| `bearer` | `env` |
| `basic` | `user_env`, `password_env` |
| `oauth2` (client credentials) | `token_url`, `client_id_env`, `client_secret_env`, `scope` |

| Paging | Fields |
| --- | --- |
| `page` | `param` (page), `size_param`, `size`, `start` (1) |
| `offset` | `param` (offset), `size_param`, `size` |
| `cursor` | `param` (cursor), `cursor_path` (where the next cursor is: `meta.next_cursor`) |
| `link` | none: follows the `Link: <...>; rel="next"` header |

Requests are retried on 429 and 5xx, waiting as `Retry-After` says.

### SFTP, databases, folders and protobuf

```yaml
  - name: partner
    connection:
      kind: sftp
      host: sftp.partner.com
      user_env: PARTNER_SFTP_USER
      key_file_env: PARTNER_SFTP_KEY_FILE     # or password_env
      path: /outbound
      files:
        - {table: shipments, pattern: "shipments_*.csv"}

  - name: crm
    connection:
      kind: database
      url_env: CRM_DB_URL                     # postgresql+psycopg://user:password@host/db
      tables:
        - {table: accounts, source: public.accounts, incremental_column: updated_at}
        - {table: open_deals, source: "select * from deals where status = 'open'"}

  - name: drops
    connection:
      kind: files
      location: s3://partner-drops/trips      # or a folder in the project
      files:
        - {table: trips, pattern: "trips_*.parquet"}

  - name: events
    connection:
      kind: protobuf
      proto: protos/orders.proto
      location: events/
      messages:
        - {table: orders, message: Order, pattern: "orders_*.bin"}
```

- Databases fetch only rows past the latest value of `incremental_column`, which defaults to the
  raw table's watermark.
- SFTP and folders copy each file once, by name, size and time.
- Protobuf files hold length-delimited messages. Each message becomes a JSON record, with the
  field names as the .proto file writes them.
- `murakumo import protobuf protos/orders.proto --source events` makes a raw table per message
  type, with each field's type.

From the command line, `murakumo source connect` writes a connection from `key=value` pairs, or
from a file:

```bash
murakumo source connect weather rest base_url=https://api.weather.example/v1 \
  auth.type=bearer auth.env=WEATHER_TOKEN paging.type=page paging.size=500 \
  endpoints.0.table=readings endpoints.0.path=/readings endpoints.0.records=data.items \
  --why "the weather API replaces the nightly CSV"
murakumo source connect partner sftp --file partner.yaml
murakumo source test weather                   # reach it, and see what it has, without landing anything
murakumo source request weather                 # send one request and print the first records
murakumo source disconnect weather
```

### In the canvas: a request builder

**Sources** → **Connect…** (or **Connection**) on a source:

1. Choose the kind, set the auth and paging once, and add a request for each raw table.
2. **Send** runs the request (its first page) from this machine, with its environment variables,
   and shows the status, the time it took and the records, as a table or the raw response.
3. **Make … from these records** creates the raw table from them, with each column's type
   inferred. **Update … from these records** does the same for a table that already exists.
4. **Save connection**, with the reason. Then **Test**, and **Fetch now**.

## Fetching and loading

```bash
murakumo ingest                  # fetch what is new from every connected source
murakumo ingest --source weather --table readings
murakumo load --env dev          # load what has landed into the raw tables, without running dbt
murakumo build --ingest          # fetch, load everything landed, then build
```

### Where files land

Each run lands its files under `<landing>/<source>/<table>/`:

- REST APIs, databases and protobuf: JSON lines, a file per 50,000 records.
- SFTP and folders: the files as they are.
- File names start with the table and the run's time, so a run's files sort together.

### Where each run got to

Each run records how far it got in `<landing>/<source>/_state.json`: the latest watermark per
table, and the files already copied. A run that finds nothing new writes nothing.

### How the files are loaded

Loading follows each raw table's `load`:

- `append` and `incremental` tables take every landed file, each once (remembered by its content
  in `audit.loaded_files`). Incremental tables then merge by key.
- `full` tables take the latest run's files only, because each run fetched the whole of it.

The landing folder is in the project, so add it to `.gitignore` (or land in S3).

## The code it writes

Everything is under `generated/ingestion/`, for every source with a connection. The README there
says how to set it up.

| Choice | Files | What they are |
| --- | --- | --- |
| extract: `python` (and every other extract) | `python/extract.py`, `python/sources/<source>.json`, `python/requirements.txt` | One file that fetches any source from its settings. Python 3.9 or later; the standard library only for REST and folders. It imports nothing from Murakumo: `python extract.py sources/weather.json` |
| extract: `lambda` | `lambda/template.yaml` | An AWS SAM template: a function per source on its schedule (`ingestion.schedule`, else hourly), landing in S3. Secrets are `NoEcho` parameters `sam deploy --guided` asks for |
| extract: `glue` | `glue/<source>.py`, `glue/jobs/<source>.json`, `glue/deploy.sh` | A Glue job per source: Spark over JDBC for databases (writing Parquet), a Python shell job running `extract.py` for the rest, each with a scheduled trigger |
| load: `snowpipe` | `snowpipe/integration.sql`, `snowpipe/<env>.sql` | The storage integration (once, as ACCOUNTADMIN). Then per environment: the stage, the JSON file format, the raw tables, and a pipe per raw table whose COPY fills `_ingested_at` and `_batch_id` from the file's metadata |
| load: `autoloader` | `autoloader/<source>.py`, `autoloader/databricks.yml` | A Databricks notebook per source (`cloudFiles`, `availableNow`) and a bundle with a job that runs when files arrive |

### Fetching and loading in the pipelines

The orchestrator's pipelines fetch and load before each schedule's build:

- **Airflow:** an `extract_<source>` task per source the schedule reads, then `load`, then the dbt
  build.
- **Dagster:** the same, before the dbt assets build, for the schedule that started the run.
- **Prefect:** the same, as tasks in each flow.

When something else fetches the data (Lambda, Glue), the pipelines only load. When it is loaded
continuously (Snowpipe, Auto Loader), they only build.

## Snowflake platform settings

```yaml
targets:
  warehouse:
    type: snowflake
    transient_layers: [staging, intermediate]   # the default
    time_travel_days: {raw: 7, marts: 30}
    monthly_credits: 50
```

- **Transient tables.** Tables in `transient_layers` can be rebuilt from raw, so they are transient:
  no fail-safe, and no storage paid for it. dbt makes every Snowflake table transient unless told
  otherwise. Murakumo now says so for each layer, and keeps marts and snapshots permanent.
- **Time travel per layer:**
  - Raw tables are created with it.
  - dbt sets it on each layer's tables after building them (the `murakumo_retention` macro).
  - Transient tables keep at most 1 day, and **MK703** warns when a transient layer asks for more.
- **A spending cap.** With `monthly_credits` set, `murakumo env provision` creates a resource
  monitor on the compute warehouse:
  - it notifies at 75% and 90%;
  - at 100% it suspends the warehouse once running queries finish, and at 110% it suspends at once;
  - resource monitors need ACCOUNTADMIN.

## Deploying: build it for you

```bash
murakumo stack deploy --env dev                  # the steps, in order; nothing runs
murakumo stack deploy --env dev --sql            # with each step's SQL in full
murakumo stack deploy --env dev --apply          # run the steps Murakumo can run
murakumo stack deploy --env dev --apply --step pipes
```

The steps follow the stack:

| Step | Who runs it |
| --- | --- |
| `provision`: databases, warehouse, roles, and the resource monitor | Murakumo, in Snowflake, as `SNOWFLAKE_ADMIN_ROLE` (else your default role) |
| `integration`: the storage integration for Snowpipe | Murakumo, as ACCOUNTADMIN |
| `pipes`: the stage, raw tables and pipes for the environment | Murakumo, as the environment's role (`SNOWFLAKE_ROLE_<ENV>`) |
| `ci`: the CI workflow | Murakumo writes it (never over a file you changed) |
| the sources' secrets, the first fetch, Lambda, Glue, Auto Loader, the orchestrator | You, with your own credentials: Murakumo never holds cloud keys |

`--apply` stops at the first step that fails. Every SQL step can run again safely.

In the canvas: **Stack** → **Deploy**. Pick the environment, open each step to read its SQL or
commands, then **Run Murakumo's steps**. The run happens in the background, in **Activity**.

## The pipeline docs

`murakumo generate` writes `generated/docs/stack.md`, which covers:

- the stack, part by part;
- each source's connection;
- every step to set the platform up by hand, with the SQL and commands in full;
- the decisions log.

The full step-by-step rebuild is the [replication guide](guide.md), in `generated/guide/`.
