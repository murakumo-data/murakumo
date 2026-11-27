# 7. Quality and observability

How you know the data is right and the runs worked.

## 7.1 Run the tests

**Why:** Every test is a query that returns the rows that fail it: keys unique and not null, values accepted, relationships between tables kept, ranges and rules from the model.

```bash
dbt test --project-dir generated/dbt --profiles-dir generated/dbt
duckdb commerce.duckdb < generated/guide/sql/tests/customer_order_activity.sql
duckdb commerce.duckdb < generated/guide/sql/tests/dim_customer.sql
duckdb commerce.duckdb < generated/guide/sql/tests/dim_part.sql
duckdb commerce.duckdb < generated/guide/sql/tests/fct_order_fulfilment.sql
duckdb commerce.duckdb < generated/guide/sql/tests/fct_order_line.sql
duckdb commerce.duckdb < generated/guide/sql/tests/hub_customer.sql
duckdb commerce.duckdb < generated/guide/sql/tests/hub_order.sql
duckdb commerce.duckdb < generated/guide/sql/tests/int_dim_customer__versions.sql
duckdb commerce.duckdb < generated/guide/sql/tests/link_order_customer.sql
duckdb commerce.duckdb < generated/guide/sql/tests/sat_customer.sql
duckdb commerce.duckdb < generated/guide/sql/tests/sat_customer_current.sql
duckdb commerce.duckdb < generated/guide/sql/tests/stg_nyc_tlc__taxi_zone_lookup.sql
duckdb commerce.duckdb < generated/guide/sql/tests/stg_nyc_tlc__yellow_tripdata.sql
duckdb commerce.duckdb < generated/guide/sql/tests/stg_open_meteo__weather_daily.sql
duckdb commerce.duckdb < generated/guide/sql/tests/stg_open_meteo__weather_hourly.sql
duckdb commerce.duckdb < generated/guide/sql/tests/stg_tpch_erp__customer.sql
duckdb commerce.duckdb < generated/guide/sql/tests/stg_tpch_erp__lineitem.sql
duckdb commerce.duckdb < generated/guide/sql/tests/stg_tpch_erp__nation.sql
duckdb commerce.duckdb < generated/guide/sql/tests/stg_tpch_erp__orders.sql
duckdb commerce.duckdb < generated/guide/sql/tests/stg_tpch_erp__part.sql
duckdb commerce.duckdb < generated/guide/sql/tests/stg_tpch_erp__partsupp.sql
duckdb commerce.duckdb < generated/guide/sql/tests/stg_tpch_erp__region.sql
duckdb commerce.duckdb < generated/guide/sql/tests/stg_tpch_erp__supplier.sql
```

**You should see:** `PASS` for each test; each plain query returns no rows.

- [ ] Run the tests

## 7.2 Check the sources are fresh

**Why:** Each source with freshness set warns, then fails, when its newest row is older than allowed.

```bash
dbt source freshness --project-dir generated/dbt --profiles-dir generated/dbt
```

- [ ] Check the sources are fresh

## 7.3 Read the run log

**Why:** Every dbt run records itself in the audit schema: the run, each model's rows and time, each test's result, and each load's rows in and rejected. dbt creates these tables on its first run.

| Table | Holds |
| --- | --- |
| audit.run_log | one row per run: when, which schedule, how many models and tests passed |
| audit.model_runs | each model in each run: rows affected, rows in the table, seconds |
| audit.dq_results | each test in each run: pass or fail, and how many rows failed |
| audit.row_counts | each load: rows in, rows rejected, and why |
| audit.loaded_files | each file loaded, so none is loaded twice |

```sql
-- The latest runs
select run_id, schedule, status, started_at, finished_at, models_ok, models_failed,
       tests_passed, tests_failed
from audit.run_log order by started_at desc limit 20;

-- Tests that failed in the last week
select test, model, column_name, failures, finished_at
from audit.dq_results where status <> 'pass' and finished_at > current_date - 7
order by finished_at desc;

-- Rows loaded and rejected, by table and day
select table_name, cast(loaded_at as date) as day,
       sum(rows_ingested) as rows_in, sum(rows_rejected) as rejected
from audit.row_counts group by 1, 2 order by 2 desc, 1;
```

- [ ] Read the run log

## 7.4 Know what each rule does when it fails

**Why:** Each rule says what a failure does: `fail` stops the run (an error test), `warn` records it and carries on (a warning test), `quarantine` sets the failing rows aside before staging, so the rest carry on. The tests above are these rules.

| Table | Column | Rule | When it fails |
| --- | --- | --- | --- |
| dim_part | retail_price | range | fail |
| fct_order_line | net_amount | range | fail |
| lineitem | l_discount | range | fail |
| lineitem | l_extendedprice | range | fail |

- [ ] Know what each rule does when it fails

## 7.5 Agree the data contracts

**Why:** Each table has a data contract in the Open Data Contract Standard (ODCS v3.0.2): its columns and types, which may be empty, its key, its rules as SQL that must return 0, and how fresh it is. Send a raw table's contract to whoever sends that data; a modelled table's to whoever uses it. Run each contract's quality queries after a load to check it kept to it.

| Contract |
| --- |
| generated/contracts/customer.odcs.yaml |
| generated/contracts/customer_order_activity.odcs.yaml |
| generated/contracts/dim_customer.odcs.yaml |
| generated/contracts/dim_part.odcs.yaml |
| generated/contracts/fct_order_fulfilment.odcs.yaml |
| generated/contracts/fct_order_line.odcs.yaml |
| generated/contracts/hub_customer.odcs.yaml |
| generated/contracts/hub_order.odcs.yaml |
| generated/contracts/lineitem.odcs.yaml |
| generated/contracts/link_order_customer.odcs.yaml |
| generated/contracts/nation.odcs.yaml |
| generated/contracts/orders.odcs.yaml |
| generated/contracts/part.odcs.yaml |
| generated/contracts/partsupp.odcs.yaml |
| generated/contracts/region.odcs.yaml |
| generated/contracts/sat_customer.odcs.yaml |
| generated/contracts/supplier.odcs.yaml |
| generated/contracts/taxi_zone_lookup.odcs.yaml |
| generated/contracts/weather_daily.odcs.yaml |
| generated/contracts/weather_hourly.odcs.yaml |
| generated/contracts/yellow_tripdata.odcs.yaml |

```sql
-- the columns a raw table has, to compare with its contract's properties
select column_name, data_type from information_schema.columns
where table_schema = '<raw schema>' and table_name = '<raw table>';
```

- [ ] Agree the data contracts

## 7.6 Look at the data at each stage

**Why:** Read a few rows of a table at each stage, in the warehouse's own query editor: what was loaded (raw), what passed the rules (staging), what was set aside (quarantine), and what people read (marts). Files and storage read the same way with DuckDB (`select * from read_parquet('s3://bucket/path/*.parquet')`).

```sql
select * from raw_nyc_tlc.taxi_zone_lookup limit 100;  -- raw
select * from raw_open_meteo.weather_daily limit 100;  -- raw
select * from raw_tpch_erp.customer limit 100;  -- raw
select * from staging.stg_tpch_erp__customer limit 100;  -- staging
select * from staging.stg_tpch_erp__lineitem limit 100;  -- staging
select * from staging.stg_tpch_erp__nation limit 100;  -- staging
```

- [ ] Look at the data at each stage

---

Generated by Murakumo 1.3.2 for commerce. [Back to the guide](README.md)
