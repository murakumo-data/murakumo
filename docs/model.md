# Model files (IR v1)

A Murakumo project is a folder with a manifest and a model. The model is the canonical metadata
model (the IR) that every generator compiles from. It is plain YAML, so you can edit it by hand,
review it in pull requests, and the canvas reads and writes the same files.

```text
my_platform/
  murakumo.yaml                 the manifest: targets, modelling choices, conventions
  model/
    sources/nyc_tlc.yaml        one source system per file
    entities/                   one entity per file, in any folder structure you like
      nyc_tlc/yellow_tripdata.yaml
      marts/fct_trip.yaml
    relationships.yaml          every relationship, in one list
    metrics.yaml                every metric, in one list
    layout.yaml                 where the canvas draws each entity (optional)
    decisions.yaml              why the platform is built as it is: see docs/stack.md (optional)
  custom/                       hand-written code, never touched by Murakumo
  generated/                    written by `murakumo generate`; see docs/generating.md
```

New entities go in `entities/<source>/` for raw tables and `entities/<layer>/` otherwise. If you
move a file, Murakumo keeps it where you put it.

## Ids

Every source, entity, attribute, relationship and metric has an `id` (a UUID). Names are what
people read and what references use; ids are how Murakumo knows that a renamed column is the
same column, so a rename becomes `ALTER ... RENAME` rather than a drop and an add.

`murakumo init`, `murakumo add` and the importers ([docs/importing.md](importing.md)) write ids for you. If you write YAML by hand, leave `id` out
and run `murakumo validate --fix` to fill them in. Never copy an id to a new object.

## Entity

```yaml
id: 0e3b910a-721e-4b22-9dd0-50bf65e8b966
name: fct_trip
description: One row per yellow taxi trip.
layer: marts                    # raw | staging | intermediate | marts | semantic
kind: fact                      # table | fact | dimension | bridge | hub | link | satellite | event | cumulative | accumulating | reference
domain: mobility                # must be listed under project.domains
source: nyc_tlc                 # for raw tables: the source system
grain: [trip_id]                # the columns that make a row unique
history: none                   # none | scd1 | scd2 | scd3 | scd4 | scd6 | snapshot
tracked: []                     # scd3 and scd6: the columns whose changes it keeps (default: all but keys)
load: incremental               # full | append | incremental | cdc | streaming
watermark: pickup_at            # the column that orders a key's versions (and, by choice, says what is new)
cdc:                            # load: cdc only. A change feed: Debezium, a database's log, DMS
  operation: op                 #   the column that says what happened to the row; without it no row counts as deleted
  deletes: [d, delete, deleted] #   its values that mean the row was deleted (the default)
  order: changed_at             #   which change is the latest; default the watermark, then load time
incremental_by: load_time       # load_time (the default: what was loaded since the last run) | watermark
lookback_hours: 0               # an incremental table also re-reads this many hours, for stragglers
partition_by: [day(pickup_at)]  # a column, or a transform: day(), month(), year(), hour(), bucket(n, col)
cluster_by: []
freshness: { warn_after_hours: 24, error_after_hours: 48 }
access: public                  # private | protected | public: who may build on it (dbt model access)
row_access:                     # which rows each role sees (Snowflake); see modelling-patterns.md
  column: region
  allow: { SALES_EMEA: [EMEA, UK] }
tags: []
attributes:
  - name: trip_id
    type: string
    nullable: false
    key: surrogate              # primary | natural | surrogate
    tests: [unique, not_null]
  - name: total_amount
    type: money
    classification: confidential  # public | internal | confidential | restricted
    pii: false
    unit: USD
mappings:
  - target: total_amount
    sources: [yellow_tripdata.total_amount]
  - target: trip_id
    sources: [yellow_tripdata.VendorID, yellow_tripdata.tpep_pickup_datetime]
    expression: md5(concat_ws('|', VendorID, tpep_pickup_datetime))   # DuckDB SQL, translated per warehouse
tests:                          # table-level rules
  - type: unique_combination
    columns: [pickup_at, pickup_zone_id]
    severity: warn
properties: {}                  # your own metadata, available to custom templates
```

Columns are generated in the order they are listed. `murakumo move column fct_trip total_amount
--after trip_id` reorders them (or drag them on the canvas); tables that already exist keep their
order, since no warehouse can reorder columns in place.

A **calculated column** is a column whose mapping has an expression. The expression is DuckDB SQL
over the tables the entity is built from, written `entity.column` (or just `column`, when a
mapping source names its table), and is translated for each warehouse. The canvas checks and
previews expressions as you write them; see [the canvas](canvas.md#what-you-can-do).

**Domains** group entities by business area. List them in `murakumo.yaml`, with an owner if you
like, and set `domain:` on each entity; an entity's domain must be listed (MK202):

```yaml
project:
  name: shop
  domains:
    - name: sales
      owner: Sales analytics
    - finance                   # just a name is fine too
```

`murakumo add domain`, `rename domain` (its entities follow) and `remove domain` change the list
without touching the file's comments, as does the canvas's Domains panel. Each domain with models
becomes a dbt group.

**Lineage** comes from the mappings: a column is made from the columns its mapping's sources and
expression name. `murakumo lineage fct_trip.trip_id` shows where a column comes from, as a tree,
down to the raw columns; `--down` shows what uses it instead; `--format json` gives the same as
data. Give a table instead of a column for all of its columns. Generated dbt columns carry it as
`meta.lineage`.

Only `name` is required. Attributes also accept `default`, `physical_types` (an override per
target, such as `{snowflake: VARCHAR(16)}`), `source_type`, `accepted_values` (a list of codes, or
a mapping of code to label), `example`, `business_term` and `description`.

### Logical types

`string`, `text`, `boolean`, `smallint`, `integer`, `bigint`, `float`, `double`,
`decimal(p,s)`, `money`, `date`, `time`, `timestamp`, `timestamp_tz`, `uuid`, `json`, `binary`,
`array<type>`, `struct`. `string(n)` sets a maximum length. Generators map each one to the right
physical type per warehouse.

### Quality rules

Write `tests: [unique, not_null]` for simple rules, or a full rule. Rules on a column sit under the
column; rules on the whole table sit under the table's `tests:`.

| Type | Checks | Needs | Judges each row |
| --- | --- | --- | --- |
| `not_null` | every row has a value | nothing | yes |
| `unique` | no two rows share a value | nothing | no |
| `accepted_values` | values come from a list | `values` (or the column's `accepted_values`) | yes |
| `range` | numbers or dates between bounds | `min`, `max` or both | yes |
| `regex` | text matches a pattern | `pattern` | yes |
| `length` | text length between bounds | `min`, `max` or both | yes |
| `custom_sql` | an expression every row satisfies | `sql` (staging's column names) | yes |
| `null_share` | at most a share of values is empty | `max`, from 0 to 1 | no |
| `distinct_count` | how many distinct values | `min`, `max` or both | no |
| `recent` | the newest date or time is recent | `max_age_hours` | no |
| `row_count` | how many rows (table level) | `min`, `max` or both | no |
| `unique_combination` | no two rows share these columns together (table level) | two or more `columns` | no |
| `freshness` | the table loads often enough | `max_age_hours` | no |

What a failing rule does is its `action`:

| `action` | When a row or the table fails |
| --- | --- |
| `fail` (default) | the run stops: an error test |
| `warn` | it is recorded and the run carries on: a warning test |
| `quarantine` | the rows that fail are set aside in `<staging model>__quarantine`, with the first rule each failed (`_failed_rule`); the rest carry on. Only for rules that judge each row, on raw tables |

```yaml
attributes:
  - name: Borough
    type: string
    tests:
      - type: accepted_values
        values: [Manhattan, Queens, Bronx, Brooklyn, Staten Island, EWR]
        action: quarantine
  - name: service_zone
    type: string
    example: Boro Zone            # made-up data for `verify` uses it for the regex rule
    tests:
      - { type: regex, pattern: '^[A-Za-z ]+$', action: warn }
      - { type: null_share, max: 0.05 }
tests:
  - { type: row_count, min: 1 }
  - { type: custom_sql, sql: location_id > 0, action: quarantine, description: ids are positive }
```

`severity: error | warn` still works; `action` sets it (`fail` is `error`, `warn` is `warn`). Every
rule becomes a dbt test (or, for quarantine, the CASE that splits staging), a check in the PySpark
and Polars jobs, an expectation for Great Expectations and a SodaCL check when
`quality.framework` names them, and a quality entry in the table's data contract. In the canvas:
the inspector's **Quality rules**.

#### Data contract

```yaml
contract:
  version: 1.2.0
  status: active                  # proposed | draft | active | deprecated | retired
  enforce: block                  # block: a load that breaks it stops the build; warn: it is reported
```

Every table has a contract in `generated/contracts/<table>.odcs.yaml` (ODCS v3.0.2); these
settings are its version and status. See [operating.md](operating.md#data-contracts).

### Cumulative tables

A cumulative table keeps, for each key, its whole history in one row per day: the days it was
active, and a value per active day for each measure, most recent first. It is the pattern in the
project brief: each day takes yesterday's rows and puts today's activity in front of their arrays,
so a year of history never has to be scanned again.

```yaml
name: customer_order_activity
layer: marts
kind: cumulative
grain: [customer_id]                        # the key it accumulates per
cumulative:
  date: fct_order_line.ordered_on           # the source column whose day counts as activity
  measures:                                 # optional: one value per active day
    order_lines: count(*)
    spend: sum(fct_order_line.net_amount)
attributes:
  - { name: customer_id, type: bigint, nullable: false }
mappings:
  - { target: customer_id, sources: [fct_order_line.customer_id] }
```

The generated table adds `dates_active` (an array of dates), an array per measure, `days_active`,
`last_active` and `snapshot_date`, unique on the grain and `snapshot_date`. Each run adds one day:
`run_date` (`murakumo build --run-date 2024-01-31`, or what the orchestrator passes), or the
latest day with activity. `murakumo backfill customer_order_activity --from 2024-01-01 --to
2024-01-31` builds a range of days, one after another. Rows whose key is empty are left out:
they have no history to join. On the canvas, a table of kind cumulative gets the same settings in
the inspector.

### Modelling patterns

Unknown members, accumulating snapshots, conformed dimensions, access levels, exposures
(`model/exposures.yaml`) and masking policies have a page of their own:
[modelling patterns](modelling-patterns.md).

## Relationship

```yaml
relationships:
  - name: fct_trip_pickup_zone
    from: fct_trip              # usually the many side, holding the foreign key
    to: dim_taxi_zone
    cardinality: many_to_one    # one_to_one | one_to_many | many_to_one | many_to_many
    optional: true              # a trip may have no zone
    role: pickup                # tells apart two links between the same tables
    keys:
      - { from: pickup_zone_id, to: zone_id }
```

## Source system

```yaml
name: nyc_tlc
type: file                      # database | file | api | stream | manual
connection: https://d37ci6vzurychx.cloudfront.net/trip-data/
load_tool: dlt
schedule: monthly
```

Use `env:NAME` in `connection` for anything secret; never put credentials in model files.

## Metric

```yaml
metrics:
  - name: total_revenue
    label: Total revenue
    entity: fct_trip                   # the table it is counted on
    expression: sum(total_amount)      # one or more aggregates
    time: pickup_at                    # left out: the table's first date or timestamp column
    grain: day                         # hour | day | week | month | quarter | year
    dimensions: [dim_taxi_zone.borough]
    filters: ["payment_type = 1"]      # only rows that meet them count
```

Metrics become dbt's semantic layer: see [modelling patterns](modelling-patterns.md#the-semantic-layer).

## Validation rules

`murakumo validate` reports errors (the model cannot be generated) and warnings (it can, but
something looks wrong). It exits with 1 on errors, or on warnings too with `--strict`.

| Code | Level | Meaning |
| --- | --- | --- |
| MK000 | error | No `murakumo.yaml` in this folder or any folder above it |
| MK001 | error | A file is not valid YAML, or cannot be read |
| MK002 | error | A field is wrong: unknown key, bad type, missing value, value not allowed |
| MK003 | error | A list file has the wrong shape |
| MK004 | warning | A file under `model/` is ignored (wrong extension or unknown name) |
| MK005 | warning | Objects have no id; run `murakumo validate --fix` |
| MK101 | error | Two entities have the same name |
| MK102 | error | An entity has two columns with the same name |
| MK103 | error | Two objects share an id |
| MK104–106 | error | Two sources, metrics or relationships have the same name |
| MK107 | error | A table has the name of the staging model generated for a raw table (`stg_<source>__<table>`) |
| MK109 | error | Two exposures have the same name |
| MK108 | warning | Two tables of different layers (or a table and an audit table) share a name, so they would collide in someone's own schema on a shared warehouse |
| MK201 | error | An entity's source is not declared |
| MK202 | error | An entity's domain is not in `project.domains` |
| MK203 | error | grain, watermark, partition, cluster or test columns do not exist |
| MK204 | error | A relationship points to an entity that does not exist |
| MK205 | error | A relationship's join column does not exist |
| MK206 | error | A mapping targets a column the entity does not have |
| MK207 | error | A mapping reads from an entity or column that does not exist |
| MK208 | error | A metric's entity does not exist |
| MK209 | error | An exposure reads an entity that does not exist |
| MK210 | error | A metric's table has no date or timestamp column to count it by |
| MK211 | error | A metric's expression cannot be read, or has no aggregate |
| MK212 | warning | A metric's dimension does not exist |
| MK301 | warning | A join compares different types, so it will need a cast |
| MK302 | warning | The one side of a relationship is not joined on its primary or natural key |
| MK303 | warning | A fact table has no grain |
| MK304 | warning | A dimension has no primary key |
| MK305 | warning | An entity has no columns |
| MK306 | warning | A PII column is not classified confidential or restricted |
| MK307 | warning | A name is not snake_case (raw tables are exempt: they mirror the source) |
| MK308 | warning | An incremental raw table has no watermark |
| MK309 | warning | SCD2 or SCD6 history without a primary or natural key |
| MK310 | error | A cumulative table has no `cumulative:` setting |
| MK311 | warning | A primary key column is nullable |
| MK312 | error | A cumulative table has no grain |
| MK313 | warning | A table is built incrementally by its watermark (`incremental_by: watermark`) but has no watermark column, so it is rebuilt in full |
| MK314 | error | An accumulating snapshot has no `accumulating:` setting, or no grain |
| MK315 | error | An accumulating snapshot's milestone is not a date or timestamp column |
| MK316 | warning | A fact uses another domain's dimension that is not marked `conformed: true` |
| MK317 | warning | A dimension has the same key as a conformed one: a copy of it |
| MK318 | error | A tracked column (`tracked:`) is not a column of the table |
| MK319 | warning | `tracked` is set, but only `scd3` and `scd6` history use it |
| MK320 | error | A hub, link or satellite has no business key (`key: natural`) |
| MK321 | error | A change feed (`load: cdc`) has no primary key |
| MK322 | error | `cdc.operation` or `cdc.order` is not a column of the table |
| MK323 | warning | `cdc:` settings on a table that is not `load: cdc` |
| MK324 | warning | `load: streaming` on a table no Kafka connection fills: it is loaded as append |
| MK325 | warning | A table built incrementally reads a change feed that marks deleted rows (`cdc.operation`): they stay until a full rebuild |
| MK326 | error | `action: quarantine` on a rule that judges the whole table (unique, row_count...), not each row |
| MK327 | error | `action: quarantine` on a table that is not raw: rows are set aside as they are loaded |
| MK328 | warning | A `regex` rule on Synapse, which has no regular expressions |
| MK329 | error | A `regex` rule's pattern is not a regular expression |
| MK330 | warning | A `regex` rule's column has no `example` that matches: made-up data for `verify` cannot match a pattern |
| MK331 | warning | `quality.framework` names a tool Murakumo does not write rules for (it writes dbt_tests, great_expectations, soda) |
| MK401 | error | An import would overwrite a table that belongs to another source or layer |
| MK402 | error | A dictionary row has no table or column name, or the file has no rows |
| MK403 | warning | A dictionary value could not be used (type, classification, foreign key) |
| MK404 | warning | An import found an unknown type, no tables, or too small a sample |
| MK501–508 | mixed | Generation problems; see [docs/generating.md](generating.md#rule-codes) |
| MK601–604 | mixed | Schedules: a selection names nothing, a name is used twice, two schedules refresh "the rest", a table no schedule refreshes; see [orchestration.md](orchestration.md#schedules) |

## Editor support

`murakumo schema entity` prints the JSON Schema for entity files (also `manifest`, `model`,
`source` and `relationship`). Point your editor's YAML extension at it for autocomplete and
inline errors.
