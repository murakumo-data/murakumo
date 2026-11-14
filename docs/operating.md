# Operating the platform: monitoring, cost, lineage for a catalog, Slim CI

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
