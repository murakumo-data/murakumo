# Snowflake

Murakumo reads Snowflake's catalogue (`murakumo import database`), generates for it
(`murakumo generate --warehouse snowflake`), and builds and tests the generated project on it
(`murakumo verify --warehouse snowflake`). Everything signs in with a key pair, so no password is
stored anywhere.

## One-time setup

You need a Snowflake account where you can use `ACCOUNTADMIN`, for example a
[30-day trial](https://signup.snowflake.com).

### 1. Create a key pair on your machine

```bash
mkdir -p ~/.snowflake && chmod 700 ~/.snowflake
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out ~/.snowflake/murakumo_rsa_key.p8 -nocrypt
chmod 600 ~/.snowflake/murakumo_rsa_key.p8
openssl rsa -in ~/.snowflake/murakumo_rsa_key.p8 -pubout -out ~/.snowflake/murakumo_rsa_key.pub
grep -v "PUBLIC KEY" ~/.snowflake/murakumo_rsa_key.pub | tr -d '\n'; echo   # the public key, for step 2
```

The `.p8` file is the private key: it never leaves your machine and is never committed. For
production, encrypt it (drop `-nocrypt`) or keep it in a secrets manager.

### 2. Create a role, warehouse, database and service user

Run as `ACCOUNTADMIN` in a Snowsight worksheet, with your public key pasted in:

```sql
use role accountadmin;

-- A spending cap: suspend at 20 credits.
create resource monitor if not exists murakumo_trial
  with credit_quota = 20
  triggers on 75 percent do notify
           on 100 percent do suspend;

create warehouse if not exists transforming
  warehouse_size = xsmall auto_suspend = 60 auto_resume = true initially_suspended = true
  resource_monitor = murakumo_trial;

create role if not exists transformer;
create database if not exists commerce;
grant usage on warehouse transforming to role transformer;
grant usage, create schema on database commerce to role transformer;
grant role transformer to role sysadmin;

-- Optional: Snowflake's TPC-H sample data, to test with real volumes.
grant imported privileges on database snowflake_sample_data to role transformer;

create user if not exists murakumo_dbt
  type = service
  default_role = transformer
  default_warehouse = transforming
  rsa_public_key = 'PASTE_PUBLIC_KEY_HERE';
grant role transformer to user murakumo_dbt;
```

If `snowflake_sample_data` does not exist, run
`create database snowflake_sample_data from share sfc_samples.sample_data;` first.

### 3. Set the connection variables

Your account identifier is in Snowsight under your name, then your account, then
**View account details** (it looks like `ORGNAME-ACCOUNT`).

```bash
export SNOWFLAKE_ACCOUNT="ORGNAME-ACCOUNT"
export SNOWFLAKE_USER="MURAKUMO_DBT"
export SNOWFLAKE_PRIVATE_KEY_PATH="$HOME/.snowflake/murakumo_rsa_key.p8"
export SNOWFLAKE_DATABASE="COMMERCE"        # optional; see below
```

Put them in `~/.zshrc` (or your shell's profile) to keep them. None of them is secret.

Optional: `SNOWFLAKE_WAREHOUSE` (default `TRANSFORMING`), `SNOWFLAKE_ROLE` (default
`TRANSFORMER`), and `SNOWFLAKE_PASSWORD` if you must use a password instead of a key.

Install the Snowflake extras: `pip install "murakumo[snowflake,verify]" dbt-snowflake`.

## Import

```bash
murakumo import database snowflake:///SALES_DB --source sales --schema public
murakumo import database snowflake://ORGNAME-ACCOUNT/SALES_DB --source sales
```

`snowflake:///DATABASE` (three slashes) takes the account from `SNOWFLAKE_ACCOUNT`. Columns,
types (`NUMBER(38,0)` becomes `bigint`), `NOT NULL`, comments, and primary and foreign keys from
`SHOW PRIMARY KEYS` and `SHOW IMPORTED KEYS` all carry across, composite keys included.

Snowflake stores unquoted names in upper case. Names that are entirely upper case come back
lower case (`C_CUSTKEY` becomes `c_custkey`), and Snowflake folds them back to the same column.
Names created quoted with lower-case letters keep their spelling and stay quoted in generated
SQL.

## Generate

```bash
murakumo generate --warehouse snowflake     # or set targets.warehouse.type: snowflake
```

The generated `profiles.yml` reads the same variables: key-pair login, role
`SNOWFLAKE_ROLE_<ENV>`, warehouse `SNOWFLAKE_WAREHOUSE`, and database `SNOWFLAKE_DATABASE`,
which defaults to `targets.warehouse.database` (or the project name in upper case, when the
manifest names a DuckDB file).

## Verify

```bash
murakumo verify --warehouse snowflake
murakumo verify --warehouse snowflake \
  --data lineitem=snowflake:SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.LINEITEM \
  --data yellow_tripdata=local-data/tlc/yellow_tripdata_2024-01.parquet
```

Each run creates its schemas with a unique prefix (`VERIFY_20261027_131005_RAW_TPCH_ERP`,
`..._STAGING`, `..._MARTS`) in `SNOWFLAKE_DATABASE`, so it never touches other tables there,
and drops them at the end. Add `--keep-tables` to look at the results in Snowsight first.

Raw tables are loaded through their table stages: local or `https://` Parquet and CSV files are
uploaded as they are, synthetic rows go up as CSV, and `snowflake:DB.SCHEMA.TABLE` copies a table
that is already in Snowflake. Then `dbt build` runs twice, as on DuckDB.

On an XS warehouse, the commerce example with TPC-H SF1 (6 million order lines) and a month of
NYC taxi trips (3 million) takes about 90 seconds, a few cents of credits.
