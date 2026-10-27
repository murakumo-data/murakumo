# Sample data

Three public sources used to develop and test Murakumo. Together they cover every input path: files, a REST API, and a relational database with foreign keys.

| Source | Kind | Tables | Where the data comes from | Licence / terms |
| --- | --- | --- | --- | --- |
| `nyc_tlc` | Monthly Parquet files (~3M rows/month) + zone lookup CSV | `yellow_tripdata`, `taxi_zone_lookup` | [NYC TLC trip record data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) | NYC Open Data terms of use |
| `open_meteo` | REST API, JSON, no key | `weather_hourly`, `weather_daily` | [Open-Meteo historical weather API](https://open-meteo.com/en/docs/historical-weather-api) | CC BY 4.0: attribute Open-Meteo |
| `tpch_erp` | Relational database (generated) | `region`, `nation`, `customer`, `supplier`, `part`, `partsupp`, `orders`, `lineitem` | Generated locally with DuckDB: `CALL dbgen(sf=1)` | [TPC-H](https://www.tpc.org/tpch/) benchmark schema |

The TPC-H names, addresses and phone numbers are synthetic, but they are flagged as PII in the dictionary so classification and masking can be tested.

## Data dictionaries

`sample-dictionaries/` holds one CSV per source and `sample_dictionaries.xlsx` with one sheet per source. They are the reference input for the CSV and Excel importers. Regenerate them with:

```bash
uv run --no-project --with openpyxl python tools/build_sample_dictionaries.py
```

### Dictionary format

One row per column. Table-level fields repeat on each row of the table.

| Field | Meaning |
| --- | --- |
| `source_system` | Source the table comes from |
| `table_name`, `table_description` | Table name and purpose |
| `update_pattern` | How the source changes: `cdc`, `monthly_file_append`, `api_daily_incremental`, `full_refresh_rarely_changes` |
| `column_name`, `ordinal` | Column name and position |
| `logical_type` | Platform-neutral type: `integer`, `bigint`, `decimal(p,s)`, `string`, `boolean`, `date`, `timestamp` |
| `source_type` | Type as the source reports it |
| `nullable`, `primary_key` | `Y` / `N`; composite keys mark several columns `Y` |
| `foreign_key` | `table.column` it references; several separated by `;` |
| `accepted_values` | Allowed values, `code=label` pairs separated by `;` |
| `unit` | Unit of measure (`USD`, `miles`, `°C`, ...) |
| `pii`, `classification` | PII flag and `internal` / `confidential` / `restricted` |
| `description`, `example`, `business_term`, `owner` | Documentation and ownership |

## Real data

Downloaded or generated data goes in `local-data/` at the repo root, which git ignores.
