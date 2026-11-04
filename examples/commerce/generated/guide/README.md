# Rebuilding commerce by hand

This guide is everything it takes to build this data platform yourself, without Murakumo: every
account, table, load, model, test, schedule and pipeline, with the SQL and commands in full, and
why each is there. Work through the chapters in order and tick each step as you go. The scripts
the steps run are in `sql/`, and `rebuild.sh` runs them all, in order.

Murakumo writes this guide from the model each time it generates, so it always matches what is
built. `murakumo guide --check` rebuilds the project from these scripts alone and compares the
result with a Murakumo build.

## Chapters

0. [Overview](00-overview.md): What commerce is built from, and what you need before you start.
1. [Accounts and access](01-accounts.md): The duckdb accounts, roles and credentials the platform runs as.
2. [Repository and environments](02-repository.md): The repository's layout, the environments it builds and the git flow between them.
3. [Sources](03-sources.md): Where the data comes from, how to reach each source by hand, and the code that fetches it into the landing area (`landing`).
4. [The raw layer](04-raw.md): The raw tables, what each column holds, and the SQL that loads the landed files into them, each table its own way (full, append or incremental). Run the loads from the project folder, after the files have landed.
5. [Transformations](05-transformations.md): Each model, in the order they must run: what it is for, where each column comes from, its dbt SQL, the same model as plain SQL, and its tests. Staging models clean and type the raw tables; intermediate models and snapshots prepare history; the marts are the facts and dimensions people use.
6. [Running it](06-running.md): Building the models, on demand and on schedule, and rebuilding when needed.
7. [Quality and observability](07-quality.md): How you know the data is right and the runs worked.
8. [CI/CD](08-ci.md): Checking each change, and deploying each environment when its branch changes.
9. [Reference](09-reference.md): The data dictionary of every mart, the bus matrix, lineage, the dashboards and apps that read the marts, and what is masked.
10. [Decisions](10-decisions.md): The timestamped log of why the platform is built the way it is.
11. [Troubleshooting](11-troubleshooting.md): What usually goes wrong, and what to do about it.

## Checklist

**0. Overview**

- [ ] 0.1 Read the architecture
- [ ] 0.2 Install the tools

**1. Accounts and access**

- [ ] 1.1 Nothing to create: the warehouse is a file

**2. Repository and environments**

- [ ] 2.1 Know where everything is
- [ ] 2.2 Read how dbt connects

**3. Sources**

- [ ] 3.1 Land the tables nothing here fetches

**4. The raw layer**

- [ ] 4.1 Create the schemas and the raw tables
- [ ] 4.2 Create the table that remembers loaded files
- [ ] 4.3 Load raw_nyc_tlc.taxi_zone_lookup
- [ ] 4.4 Load raw_nyc_tlc.yellow_tripdata
- [ ] 4.5 Load raw_open_meteo.weather_daily
- [ ] 4.6 Load raw_open_meteo.weather_hourly
- [ ] 4.7 Load raw_tpch_erp.customer
- [ ] 4.8 Load raw_tpch_erp.lineitem
- [ ] 4.9 Load raw_tpch_erp.nation
- [ ] 4.10 Load raw_tpch_erp.orders
- [ ] 4.11 Load raw_tpch_erp.part
- [ ] 4.12 Load raw_tpch_erp.partsupp
- [ ] 4.13 Load raw_tpch_erp.region
- [ ] 4.14 Load raw_tpch_erp.supplier

**5. Transformations**

- [ ] 5.1 Build stg_nyc_tlc__taxi_zone_lookup
- [ ] 5.2 Build stg_nyc_tlc__yellow_tripdata
- [ ] 5.3 Build stg_open_meteo__weather_daily
- [ ] 5.4 Build stg_open_meteo__weather_hourly
- [ ] 5.5 Build stg_tpch_erp__customer
- [ ] 5.6 Build stg_tpch_erp__lineitem
- [ ] 5.7 Build stg_tpch_erp__nation
- [ ] 5.8 Build stg_tpch_erp__orders
- [ ] 5.9 Build stg_tpch_erp__part
- [ ] 5.10 Build stg_tpch_erp__partsupp
- [ ] 5.11 Build stg_tpch_erp__region
- [ ] 5.12 Build stg_tpch_erp__supplier
- [ ] 5.13 Build int_dim_customer__versions
- [ ] 5.14 Build snp_dim_customer
- [ ] 5.15 Build dim_customer
- [ ] 5.16 Build dim_part
- [ ] 5.17 Build fct_order_fulfilment
- [ ] 5.18 Build fct_order_line
- [ ] 5.19 Build customer_order_activity

**6. Running it**

- [ ] 6.1 Build everything with dbt
- [ ] 6.2 Or build it with plain SQL, in order
- [ ] 6.3 Schedule the builds
- [ ] 6.4 Backfill and rebuild

**7. Quality and observability**

- [ ] 7.1 Run the tests
- [ ] 7.2 Check the sources are fresh
- [ ] 7.3 Read the run log

**8. CI/CD**

- [ ] 8.1 Add the CI workflow

**9. Reference**

- [ ] 9.1 The bus matrix
- [ ] 9.2 Lineage
- [ ] 9.3 customer_order_activity
- [ ] 9.4 dim_customer
- [ ] 9.5 dim_part
- [ ] 9.6 fct_order_fulfilment
- [ ] 9.7 fct_order_line

**10. Decisions**

- [ ] 10.1 Read why it is built this way

**11. Troubleshooting**


