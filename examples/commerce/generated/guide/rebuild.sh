#!/usr/bin/env bash
# Rebuild commerce without Murakumo: every script in this guide, in order.
# Run it from the project folder: bash generated/guide/rebuild.sh
set -euo pipefail
here="generated/guide"
db="${MURAKUMO_DUCKDB_PATH:-commerce.duckdb}"
run() { echo "== $1"; duckdb "$db" < "$here/$1"; }

# The schemas and raw tables
run sql/build/00_landing.sql

# Load what has landed
run sql/load/00_audit.sql
run sql/load/01_raw_nyc_tlc__taxi_zone_lookup.sql
run sql/load/02_raw_nyc_tlc__yellow_tripdata.sql
run sql/load/03_raw_open_meteo__weather_daily.sql
run sql/load/04_raw_open_meteo__weather_hourly.sql
run sql/load/05_raw_tpch_erp__customer.sql
run sql/load/06_raw_tpch_erp__lineitem.sql
run sql/load/07_raw_tpch_erp__nation.sql
run sql/load/08_raw_tpch_erp__orders.sql
run sql/load/09_raw_tpch_erp__part.sql
run sql/load/10_raw_tpch_erp__partsupp.sql
run sql/load/11_raw_tpch_erp__region.sql
run sql/load/12_raw_tpch_erp__supplier.sql

# The models, in dependency order
run sql/build/01_staging__stg_nyc_tlc__taxi_zone_lookup.sql
run sql/build/02_staging__stg_nyc_tlc__yellow_tripdata.sql
run sql/build/03_staging__stg_open_meteo__weather_daily.sql
run sql/build/04_staging__stg_open_meteo__weather_hourly.sql
run sql/build/05_staging__stg_tpch_erp__customer.sql
run sql/build/06_staging__stg_tpch_erp__lineitem.sql
run sql/build/07_staging__stg_tpch_erp__nation.sql
run sql/build/08_staging__stg_tpch_erp__orders.sql
run sql/build/09_staging__stg_tpch_erp__part.sql
run sql/build/10_staging__stg_tpch_erp__partsupp.sql
run sql/build/11_staging__stg_tpch_erp__region.sql
run sql/build/12_staging__stg_tpch_erp__supplier.sql
run sql/build/13_intermediate__hub_customer.sql
run sql/build/14_intermediate__hub_order.sql
run sql/build/15_intermediate__int_dim_customer__versions.sql
run sql/build/16_intermediate__link_order_customer.sql
run sql/build/17_intermediate__sat_customer.sql
run sql/build/18_intermediate__sat_customer_current.sql
run sql/build/19_snapshots__snp_dim_customer.sql
run sql/build/20_marts__dim_customer.sql
run sql/build/21_marts__dim_part.sql
run sql/build/22_marts__fct_order_fulfilment.sql
run sql/build/23_marts__fct_order_line.sql
run sql/build/24_marts__customer_order_activity.sql

echo "Built. The test queries are in $here/sql/tests: each returns the rows that fail."
