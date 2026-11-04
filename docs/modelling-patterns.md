# Modelling patterns

Patterns from dimensional modelling and from dbt's own guidance that Murakumo writes for you:

- unknown members;
- accumulating snapshots;
- conformed dimensions and the bus matrix;
- access levels;
- exposures;
- masking policies.

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
