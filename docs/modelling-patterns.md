# Modelling patterns

Patterns from dimensional modelling and from dbt's own guidance that Murakumo writes for you:

- unknown members;
- accumulating snapshots;
- conformed dimensions and the bus matrix;
- access levels;
- exposures;
- masking policies;
- slowly changing dimensions of types 3, 4 and 6;
- the Data Vault pack: hubs, links and satellites;
- the semantic layer: metrics defined once.

Each one is set in the model, and is in the generated dbt project, the plain SQL and the
[replication guide](guide.md).

## Unknown members

A fact row whose dimension key is missing would join to nothing, and drop out of every report
that joins them. Kimball's answer is an **unknown member**: a row in each dimension, with key
`-1` and "Unknown" for its text, that such facts point at instead.

```yaml
# murakumo.yaml
modelling:
  unknown_members: true        # on for new projects
```

```yaml
# a dimension that should not have one
name: dim_calendar
kind: dimension
unknown_member: false          # true or false; left out, it follows modelling.unknown_members
```

- Each dimension with a key gets its unknown row, added to the rows it builds:
  - key columns are `-1` (or `'-1'` for text keys);
  - text columns are `'Unknown'`;
  - columns with accepted values are null, so the accepted-values test still passes;
  - other columns are null, or a neutral value (`0`, `false`, `1900-01-01`) when they cannot be
    null.
- An SCD2 dimension's unknown row is always current, valid from 1900-01-01.
- Every fact with a relationship to such a dimension writes `coalesce(<key>, -1)`, so a missing key
  points at the unknown row. The relationship test keeps reporting keys that are present but not
  yet in the dimension.
- In the canvas, a dimension's inspector has **Unknown row**: as the project, yes or no.

## Accumulating snapshots

Some business processes pass through milestones: an order is placed, shipped, then delivered. An
**accumulating snapshot** has one row per process, with a date column per milestone. Each run
updates the row as the process moves on, and computes the time between milestones.

```yaml
name: fct_order_fulfilment
layer: marts
kind: accumulating
grain: [order_id, line_number]   # the key of each row
accumulating:
  milestones: [ordered_on, shipped_on, received_on]   # its date or timestamp columns, in order
  unit: day                                          # day | hour | minute
attributes: [...]
mappings: [...]                                      # as any other table
```

- The table is incremental, and merges by its grain. A milestone once reached keeps its date,
  even when a later run does not see it again.
- It adds a column for the time between each pair of consecutive milestones
  (`ordered_on_to_shipped_on_days`), null until both are reached.
- It adds `current_milestone`: the latest milestone reached.
- **MK314:** it needs `accumulating:` and a grain.
- **MK315:** each milestone must be a date or timestamp column.
- In the canvas, a table of kind *accumulating* gets **Per**, **Milestones** and **Time in** in
  the inspector.

## Conformed dimensions and the bus matrix

A **conformed** dimension is one table that every domain's facts use, so the numbers they
report agree. The **bus matrix** shows which business processes (facts) use which dimensions.

```yaml
name: dim_customer
kind: dimension
domain: sales
conformed: true
```

- **MK316:** a fact uses another domain's dimension that is not marked conformed. Mark it, if
  every domain shares it.
- **MK317:** a dimension has the same key (from the same source column) as a conformed one. It is
  a copy that will drift: use the conformed one.
- The bus matrix is drawn from the relationships between facts (and accumulating snapshots and
  events) and dimensions. It is in the replication guide's reference chapter, and in the canvas's
  **Bus matrix** panel, where each name selects its table.

## Access levels

dbt's model access says who may build on a model:

- `private`: only models in its group (its domain);
- `protected`: models in this project (dbt's default);
- `public`: other projects too.

```yaml
name: dim_customer
access: public
```

The model's YAML gets `access:`. **MK706:** a private table needs a domain, or no group may use
it.

## Exposures

The dashboards, notebooks and apps that read the marts. dbt lists them with what they read, so
lineage and the docs show what a change would break.

```yaml
# model/exposures.yaml
exposures:
  - name: sales_dashboard
    type: dashboard              # dashboard | notebook | analysis | ml | application
    description: Weekly revenue and late shipments, by customer segment.
    owner: Sales analytics
    email: sales-analytics@example.com
    url: https://bi.example.com/dashboards/sales
    maturity: high               # high | medium | low
    depends_on: [fct_order_line, fct_order_fulfilment, dim_customer]
```

- They are written to `generated/dbt/models/_exposures.yml`, drawn in the guide's lineage and
  architecture, and listed in its reference chapter.
- **MK209:** an exposure reads a table that does not exist. **MK109:** two exposures have the same
  name.
- In the canvas: **Bus matrix** → **Exposures** adds and edits them.

## Masking policies

Columns marked `pii: true`, or with a classification, can be masked on Snowflake for every role
that should not see them.

```yaml
# murakumo.yaml
governance:
  masking:                       # pii, or a classification, to a rule
    pii: redact
    restricted: hash
  unmasked_roles: [analyst_pii]  # these roles see the real values
```

| Rule | Shows |
| --- | --- |
| `redact` | `***` |
| `partial` | all but the last 4 characters as `*` |
| `hash` | a SHA-2 hash: the same value gives the same hash, so it can still be joined and counted |
| `null` | null |

- Each rule is a masking policy, `<database>.governance.murakumo_mask_<rule>`.
- The marts' text columns that match are set to use it.
- Re-creating a table drops its policies, so dbt sets them again after each build: a post-hook
  calls the `murakumo_masking` macro. The macro creates or updates the policies, then sets them on
  the table's columns.
- `generated/ddl/snowflake/masking.sql` (and the guide's `sql/masking.sql`) is the same as SQL to
  run by hand.
- `murakumo stack deploy` has a **masking** step that creates the policies in an environment.
- The role that builds needs CREATE MASKING POLICY on the governance schema (its owner has it).
  To set a policy, it needs to own both the table and the policy, or APPLY MASKING POLICY.
- **MK704:** a rule that does not exist. **MK705:** a column to mask that is not text (masking
  policies here mask text).
- Other warehouses have no masking policies here, so nothing is generated for them.
- In the canvas: **Settings** → **Personal data**.

Row-access policies, which hide whole rows from some roles, are not generated yet.

## Slowly changing dimensions: types 3, 4 and 6

Type 1 overwrites and type 2 keeps every version. Three more are built:

```yaml
name: dim_part
kind: dimension
history: scd3                   # scd1 | scd2 | scd3 | scd4 | scd6
tracked: [brand, retail_price]  # scd3 and scd6; left out: every column that is not a key
```

| History | What is built |
| --- | --- |
| `scd3` | One row per key, with `previous_<column>` beside each tracked column: its value before its latest change. The table is incremental, so each run compares what arrives with the row it replaces |
| `scd4` | The dimension holds only the current version of each key (`valid_from` says since when), and `<name>_history` every version, with `valid_from`, `valid_to` and `is_current` |
| `scd6` | Type 2, with `current_<column>` for each tracked column on every version: the value as it is now, so old facts can be reported by today's values |

- **MK318:** a tracked column does not exist. **MK319:** `tracked` is set but the history is not
  `scd3` or `scd6`.
- In the canvas, a dimension's inspector has **History**, and **Tracked** for types 3 and 6.

## The Data Vault pack

Hubs, links and satellites, insert-only, so nothing loaded is ever lost.

```yaml
name: hub_customer
layer: intermediate
kind: hub                        # hub | link | satellite
attributes:
  - {name: customer_number, type: bigint, nullable: false, key: natural}   # the business key
mappings:
  - {target: customer_number, sources: [customer.c_custkey]}
```

| Kind | What it holds |
| --- | --- |
| `hub` | Each business key once: `<hub>_hk` (its hash), the key's columns, `load_date` (when it was first loaded), `record_source` |
| `link` | Each combination of business keys once: `<link>_hk`, a `<hub>_hk` for each hub it connects, the keys' columns, `load_date`, `record_source` |
| `satellite` | The descriptive columns of a hub or link, a row each time they change: `<parent>_hk`, `hashdiff`, `load_date`, `record_source` |

- A link connects its hubs through relationships to them; a satellite has one relationship, to
  its hub or link. The relationships map the business key's columns.
- Hash keys are md5 of the trimmed, upper-cased key values joined by `||`, so `abc ` and `ABC` are
  one key, and a hub's key hashes the same wherever it is computed.
- Each run adds what was loaded since the last one (by `load_date`). Within a run, a hub or link
  keeps a key's first row, a satellite its latest.
- `<satellite>_current` is a view of each key's latest row: other tables that read a satellite
  read it.
- **MK320:** a hub, link or satellite has no business key. **MK509:** a link with fewer than two
  hubs, a satellite without its parent, or a vault table that does not read a raw table.
- The commerce example has `hub_customer`, `hub_order`, `link_order_customer` and `sat_customer`.

## The semantic layer

Metrics are defined once, in `model/metrics.yaml`, and written as dbt semantic models and
metrics (MetricFlow), so every tool asks for "revenue" and gets the same number.

```yaml
metrics:
  - name: revenue
    label: Revenue
    entity: fct_order_line                 # the table it is counted on
    expression: sum(net_amount)            # one or more aggregates
    time: ordered_on                       # left out: the table's first date column
    grain: day
    dimensions: [order_status, dim_customer.market_segment]
  - name: average_order_value
    entity: fct_order_line
    expression: sum(net_amount) / count(distinct order_id)
  - name: open_order_lines
    entity: fct_order_line
    expression: count(*)
    filters: ["order_status = 'O'"]        # only rows that meet them count
```

- `generated/dbt/models/semantic/_semantic.yml` has a semantic model per table a metric is
  counted on: its key as the primary entity, its relationships to dimensions as foreign entities,
  its time column, the columns metrics break it down by, and a measure per aggregate.
- Each dimension those metrics use gets a semantic model too. An SCD2 dimension joins by its
  validity (`valid_from`, `valid_to`), so each fact row finds the version current at its time.
- One aggregate is a simple metric; several (`sum(a) / count(*)`) are a derived metric over a
  hidden simple metric per aggregate (`<metric>_part_1`, ...).
- `metricflow_time_spine` is the table of days MetricFlow needs.
- Query it with MetricFlow: `pip install "dbt-metricflow[dbt-duckdb]"`, then
  `mf query --metrics revenue --group-by metric_time__month`.
- **MK210:** a metric's table has no date column. **MK211:** its expression has no aggregate.
  **MK212:** a dimension it names does not exist.
- In the canvas: **Bus matrix** → **Metrics** adds and edits them.
