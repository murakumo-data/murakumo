# Operating the platform: data quality, contracts, the query console, monitoring, cost, lineage for a catalog, Slim CI

## Data quality: rules, quarantine, reconciliation, write-audit-publish

Each column and table carries quality rules ([model.md](model.md#quality-rules)): not empty,
unique, one of a list, between, matches a pattern, length, an expression, share empty, distinct
values, recent, row count, unique together. Each says what a failure does:

- **fail**: the run stops (`murakumo build` exits 1; the orchestrator's run fails);
- **warn**: recorded in the run log (`audit.dq_results`), and the run carries on;
- **quarantine** (raw tables, rules that judge each row): the rows that fail are set aside in
  `<staging model>__quarantine`, each with the first rule it failed (`_failed_rule`), and the rest
  carry on into staging and everything built from it.

The rules are written as dbt tests, as checks in the PySpark and Polars jobs (`generation.python_engine`),
and, when `quality.framework` names them, as Great Expectations suites and Soda checks:

```yaml
quality:
  framework: [dbt_tests, great_expectations, soda]
  reconciliation: true         # every raw row is in staging or set aside: none lost, none made up
  write_audit_publish: true    # build and test in wap_ schemas first; publish only when all pass
```

```bash
pip install -r generated/quality/great_expectations/requirements.txt
python generated/quality/great_expectations/run.py      # every table, or name some
pip install -r generated/quality/soda/requirements.txt
python generated/quality/soda/run.py
```

They read the tables a build made, with the same environment variables as dbt. On DuckDB, Great
Expectations reads each table into a dataframe (its DuckDB connector does not read schemas);
elsewhere it reads the warehouse through the SQLAlchemy URL in `MURAKUMO_GX_URL`. Each exits 1
when a check fails, so an orchestrator step can stop on it. Rules that set rows aside are not
among them: those rows never reach the table.

**Write-audit-publish.** With `write_audit_publish`, `murakumo build` first builds and tests
everything in `wap_` schemas (`wap_staging`, `wap_marts`...). Only when every test passes does it
build where people read it, without the tests (they just passed on the same data). When a test
fails, nothing is published and readers keep the last tables that passed. It costs a second
build; on Snowflake and BigQuery that is compute, on DuckDB seconds.

**Monitoring** reports a load that had more than `quality.monitoring.rejected_tolerance` of its
rows set aside (check **quarantined**).

**In the canvas:** the inspector's **Quality rules** (each rule, what a failure does, Add rule);
**Settings → Data quality** (the tools, reconciliation, write-audit-publish, where contracts are
enforced).

## Data contracts

Every table has a contract in the Open Data Contract Standard (ODCS v3.0.2), written from the
model into `generated/contracts/<table>.odcs.yaml`: its columns, types and which may be empty, its
key, its rules (as SQL that must return 0, and kept exactly as a custom property), and its
freshness as a latency SLA. Send a raw table's contract to whoever sends that data, and a
modelled table's to whoever uses it. Each table's version, status and enforcement are in its
model file (`contract:`), or the inspector's **Data contract**.

A contract someone sends you comes in with:

```bash
murakumo import odcs orders.odcs.yaml --source shop_db --dry-run
murakumo import odcs orders.odcs.yaml --source shop_db
```

It adds a raw table per table the contract describes, with its columns, keys and rules (from
Murakumo's own custom property, or the ODCS fields other tools write: `required`, `unique`,
`logicalTypeOptions`, library rules `duplicateCount`, `validValues`, `rowCount`). A table already
in the model gains what it lacks; where the contract and the model differ, it says so and changes
nothing.

```bash
murakumo contract              # your own environment
murakumo contract --env prod
```

checks each raw table's latest load against its contract: every column it promises, of a type
that can hold it; columns it does not promise; rows set aside by quarantine rules; the latest
load's size against a `row_count` rule; and how long since it loaded. It exits 1 on a breach of a
contract that blocks (`enforce: block`). With `contracts.enforce_on` including `sources`,
`murakumo build` runs the same check after loading and before dbt: a load that breaks a contract
that blocks stops the build before anything is built from it.

```yaml
contracts:
  standard: odcs
  enforce_on: [sources, marts]   # sources: check each load; marts: dbt enforces their columns and types
```

**In the canvas:** **Health → Data contracts** (the check, each contract to download, and
importing one); **Settings → Data quality**.

## The query console

The canvas's **Query** panel runs read-only SQL on any table at any stage, and on files wherever
they are. Its list has a ready query for each:

| Stage | What | Where it runs |
| --- | --- | --- |
| source | a source database's tables; a file source's files | the source database (its URL variable); DuckDB here |
| landed | what Murakumo fetched from REST, SFTP, Kafka and protobuf sources | DuckDB here, over the landing folder or bucket |
| raw, staging, quarantine, intermediate, marts, snapshot, audit | the built tables | the warehouse, in the environment you choose |
| lake | the lakehouse's Iceberg tables (`targets.lakehouse`) | DuckDB here, attached to the Iceberg catalog |

Files and storage: Parquet, CSV, JSON, Excel, `iceberg_scan(...)` and `delta_scan(...)`, local or in
S3, Google Cloud Storage or Azure, read with this computer's own credentials (the AWS credential
chain, `gcloud`, `AZURE_STORAGE_CONNECTION_STRING` or `AZURE_STORAGE_ACCOUNT`).

Only one statement that reads runs: `SELECT` (with `WITH`, `UNION`...), `DESCRIBE`, `SHOW`,
`EXPLAIN`. Anything that writes, `COPY`, `ATTACH`, `SET`, `INSTALL` and more than one statement are
refused before reaching the warehouse; on DuckDB files the connection is read-only too, and
elsewhere the session is never committed. Results stop at the row limit (100 to 10,000), and a
query at 30 seconds. Masking and row-access policies apply as for anyone with the same role. The
inspector's **Query** button opens it on the selected table. Results download as CSV.


## Monitoring: what is unusual

Tests check the data against rules someone wrote. `murakumo monitor` checks what nobody writes a
rule for, from what every run already records in the audit schema:

```bash
murakumo monitor                  # your own environment
murakumo monitor --env prod
murakumo monitor --env prod --notify
```

| Check | Reported when |
| --- | --- |
| **volume** | A table's latest load is far from the size of its recent loads, or brought no rows |
| **late** | A table has not loaded for longer than its `freshness` allows, or, with none set, for several times its usual gap between loads |
| **rejected** | A load refused more than a small share of its rows |
| **failed** | The latest run failed, or tests failed in it |

"Usual" is each table's own history: the middle of its earlier loads and how far they spread. A
size a table has had before is not reported, so a table whose loads swing is left alone, and a
table with fewer than four earlier loads says nothing yet. It exits with 1 when something is an
error, so an orchestrator step can fail on it.

```yaml
quality:
  monitoring:
    volume_tolerance: 0.5      # a load this far from the usual size is reported
    rejected_tolerance: 0.05   # a load refusing more than this share of its rows
    history: 30                # how many earlier loads "usual" is taken from
    late_after: 3              # late: this many times the usual gap between loads
  alerts:
    - kind: slack              # slack | teams | webhook
      url_env: SLACK_WEBHOOK   # the variable holding the webhook's address
      on: [error]              # error, warn
```

`--notify` sends the findings to each webhook: table names and counts, never rows. Slack and Teams
get a message; `webhook` gets the findings as JSON. The address must be `https://`.

**In the canvas:** the **Health** panel, **What is unusual**.

## Cost: roughly what the schedules cost a month

```bash
murakumo cost
murakumo cost --env prod       # measure run times from prod's run log
```

It multiplies how often each schedule runs in a month (from its cron time), how long a run takes
(measured from the run log where runs are recorded, else assumed), and what the warehouse charges.
Every assumption is printed with the figure.

The starting prices are public list prices for the smallest size, as they were when this was
written. They are a place to start, not a quote: put your own contract's figures in murakumo.yaml.

```yaml
costs:
  currency: USD
  unit_price: 2.6            # what one credit, DBU or RPU-hour costs you
  units_per_hour: 2          # a Small warehouse, say
  minutes_per_run: 8         # until runs are recorded to measure
  gb_scanned_per_run: 40     # BigQuery and Synapse serverless: billed by data read
```

Storage, ingestion tools, the orchestrator and people's own queries are not counted.

**In the canvas:** the **Health** panel.

## Lineage for a data catalog

The model knows where every column comes from. `murakumo catalog` writes it in
[OpenLineage](https://openlineage.io)'s format, which Marquez, DataHub, Atlan, Collibra, Microsoft
Purview and others read: an event per modelled table, with the tables it reads, its columns and
types, and for each column the columns it is made from (and the expression, when it is computed).

```bash
murakumo catalog export -o lineage.jsonl          # one event per line
murakumo catalog export --format json -o lineage.json   # a plain graph
murakumo catalog send --url-env OPENLINEAGE_URL --key-env OPENLINEAGE_API_KEY
```

`send` posts each event to `<address>/api/v1/lineage`. A table has the same run id every time, so
sending again updates the catalog. What is sent is metadata: names, types, descriptions.

**In the canvas:** the **Health** panel, **Lineage for a catalog**.

## Slim CI: check only what a change reaches

```bash
murakumo verify --changed-since origin/main
```

builds and tests the tables that changed since that commit, everything built from them, and what
they are built from; tables on other branches of the model are left out. What changed is read from
the model, not from file names: a renamed column or a new mapping names the tables it reaches,
through lineage. A change of words only (a description) builds nothing, and a change to
murakumo.yaml builds everything, because settings can reach any table.

`murakumo ci` writes this into the workflow: a pull request builds what it changed, and a push to
the main branch builds everything.
