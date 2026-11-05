"""Open tables: the layers in ``targets.lakehouse.layers`` written as Iceberg (or Delta, or Hudi)
tables in a catalog, so any engine (Spark, Trino, Flink, Snowflake, DuckDB) can read them.

- **Snowflake:** Snowflake-managed Iceberg tables (``table_format='iceberg'``) on an external
  volume. With ``catalog: polaris`` the database syncs them to Snowflake Open Catalog.
- **Databricks:** Unity Catalog's managed tables, as Iceberg (``table_format='iceberg'``),
  Delta (the default) or Hudi.
- **DuckDB:** the dbt project attaches the catalog (an Iceberg REST catalog: Polaris or Nessie,
  on S3 or MinIO), and each table is copied into it after it is built. ``generated/lakehouse/``
  starts MinIO and Polaris on this computer, so it can be tried without a cloud account.
"""

from __future__ import annotations

from typing import Any

from murakumo.manifest import Catalog, TableFormat, Warehouse

FOLDER = "lakehouse"
ALIAS = "iceberg_catalog"  # the attached catalog, on DuckDB (not a name a DuckDB file would have)


def settings(project):
    """The lakehouse settings when tables are written open (not native), else None."""
    lakehouse = project.manifest.targets.lakehouse
    if lakehouse is None or lakehouse.table_format is TableFormat.NATIVE:
        return None
    return lakehouse


def in_lake(project, layer: str) -> bool:
    lakehouse = settings(project)
    return lakehouse is not None and layer in lakehouse.layers


def external_volume(project) -> str:
    lakehouse = settings(project)
    return (lakehouse.external_volume if lakehouse else None) or f"{project.manifest.project.name.upper()}_LAKE"


def model_config(project, warehouse: Warehouse, layer: str, name: str) -> dict[str, Any]:
    """What a model in a lakehouse layer adds to its dbt config, on this warehouse."""
    if not in_lake(project, layer):
        return {}
    lakehouse = settings(project)
    table_format = lakehouse.table_format
    if warehouse is Warehouse.SNOWFLAKE and table_format is TableFormat.ICEBERG:
        return {
            "table_format": "iceberg",
            "external_volume": external_volume(project),
            "base_location_root": layer,
            "transient": False,
        }
    if warehouse is Warehouse.DATABRICKS:
        if table_format is TableFormat.ICEBERG:
            return {"table_format": "iceberg"}
        return {"file_format": table_format.value}
    if warehouse is Warehouse.DUCKDB and table_format is TableFormat.ICEBERG:
        # after the model's own transaction: DuckDB writes to one database per transaction
        return {"post_hook": [{"sql": f"{{{{ murakumo_publish_iceberg('{ALIAS}') }}}}", "transaction": False}]}
    return {}


# DuckDB: the catalog in profiles.yml, and a local one to try it on -----------------------------


def duckdb_profile(project) -> dict[str, Any]:
    """profiles.yml's attach and secrets for the Iceberg catalog, read from the environment."""
    lakehouse = settings(project)
    if lakehouse is None or lakehouse.table_format is not TableFormat.ICEBERG:
        return {}
    uri = lakehouse.catalog_uri or "http://localhost:8181/api/catalog"
    warehouse = lakehouse.warehouse or project.manifest.project.name
    return {
        "extensions": ["httpfs", "iceberg"],
        "secrets": [
            {
                "type": "s3",
                "key_id": "{{ env_var('LAKE_S3_KEY_ID', 'test') }}",
                "secret": "{{ env_var('LAKE_S3_SECRET', 'test') }}",
                "endpoint": "{{ env_var('LAKE_S3_ENDPOINT', 'localhost:4566') }}",
                "url_style": "path",
                "use_ssl": "{{ env_var('LAKE_S3_SSL', 'false') }}",
                "region": "{{ env_var('LAKE_S3_REGION', 'us-east-1') }}",
            },
            {
                "type": "iceberg",
                "client_id": "{{ env_var('LAKE_CLIENT_ID', 'root') }}",
                "client_secret": "{{ env_var('LAKE_CLIENT_SECRET', 'secret') }}",
                "oauth2_server_uri": f"{{{{ env_var('LAKE_OAUTH_URI', '{uri}/v1/oauth/tokens') }}}}",
                "oauth2_scope": "PRINCIPAL_ROLE:ALL",
            },
        ],
        "attach": [
            {
                "path": f"{{{{ env_var('LAKE_WAREHOUSE', '{warehouse}') }}}}",
                "alias": ALIAS,
                "type": "iceberg",
                "options": {
                    "endpoint": f"{{{{ env_var('LAKE_CATALOG_URI', '{uri}') }}}}",
                    # the files are read and written with the S3 secret above, not credentials the catalog vends
                    "access_delegation_mode": "none",
                },
            }
        ],
    }


def run_start(project, warehouse: Warehouse) -> str | None:
    """The hook that creates the catalog's namespaces for the lakehouse layers, before any model."""
    lakehouse = settings(project)
    if warehouse is not Warehouse.DUCKDB or lakehouse is None or lakehouse.table_format is not TableFormat.ICEBERG:
        return None
    return f"{{{{ murakumo_iceberg_namespaces('{ALIAS}', {list(lakehouse.layers)!r}) }}}}"


PUBLISH_MACRO = """{#- Generated by Murakumo: copy a table into the attached Iceberg catalog after dbt builds it,
    so other engines read the same table. Each build replaces it there in one commit. -#}
{% macro murakumo_publish_iceberg(catalog) %}
  {%- set target = catalog ~ '.' ~ this.schema ~ '.' ~ this.identifier -%}
  drop table if exists {{ target }};
  create table {{ target }} as select * from {{ this }};
{% endmacro %}

{#- The namespaces in the catalog, one per lakehouse layer, named as the warehouse's schemas are. -#}
{% macro murakumo_iceberg_namespaces(catalog, layers) %}
  {%- for layer in layers %}
  create schema if not exists {{ catalog }}.{{ generate_schema_name(layer, none) }};
  {%- endfor %}
{% endmacro %}
"""


def _compose(project) -> str:
    warehouse = (settings(project).warehouse if settings(project) else None) or project.manifest.project.name
    return f"""# Generated by Murakumo: an Iceberg lakehouse on this computer. LocalStack is the object store
# (S3's API), Apache Polaris the Iceberg REST catalog. `docker compose up -d`, then ./setup.sh.
services:
  s3:
    image: localstack/localstack:4.0
    environment:
      SERVICES: s3,sts
    ports: ["4566:4566"]
    networks:
      default:
        # LocalStack's names, so the bucket is found by path or by host name ({warehouse}.s3...)
        aliases: ["s3.localhost.localstack.cloud", "{warehouse}.s3.localhost.localstack.cloud"]

  polaris:
    image: apache/polaris:1.1.0-incubating
    depends_on: [s3]
    environment:
      POLARIS_BOOTSTRAP_CREDENTIALS: POLARIS,root,secret
      polaris.realm-context.realms: POLARIS
      polaris.features."ALLOW_INSECURE_STORAGE_TYPES": "true"
      polaris.features."SUPPORTED_CATALOG_STORAGE_TYPES": '["FILE","S3"]'
      polaris.features."SKIP_CREDENTIAL_SUBSCOPING_INDIRECTION": "true"
      polaris.readiness.ignore-severe-issues: "true"
      AWS_REGION: us-east-1
      AWS_ACCESS_KEY_ID: test
      AWS_SECRET_ACCESS_KEY: test
      AWS_ENDPOINT_URL: http://s3.localhost.localstack.cloud:4566
      AWS_ENDPOINT_URL_S3: http://s3.localhost.localstack.cloud:4566
    ports: ["8181:8181", "8182:8182"]
"""


def _setup(project) -> str:
    warehouse = (settings(project).warehouse if settings(project) else None) or project.manifest.project.name
    return f"""#!/usr/bin/env bash
# Generated by Murakumo: the bucket, and the Iceberg catalog "{warehouse}" in Polaris on it.
set -euo pipefail
until curl -sf http://localhost:4566/_localstack/health >/dev/null; do sleep 2; done
curl -sf -X PUT http://localhost:4566/{warehouse} >/dev/null || true
until curl -sf http://localhost:8182/q/health >/dev/null; do sleep 2; done
TOKEN=$(curl -s http://localhost:8181/api/catalog/v1/oauth/tokens \\
  -d grant_type=client_credentials -d client_id=root -d client_secret=secret -d scope=PRINCIPAL_ROLE:ALL \\
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')
curl -s -X POST http://localhost:8181/api/management/v1/catalogs \\
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
  -d '{{"catalog": {{"name": "{warehouse}", "type": "INTERNAL",
       "properties": {{"default-base-location": "s3://{warehouse}/",
         "s3.endpoint": "http://s3.localhost.localstack.cloud:4566", "s3.path-style-access": "true",
         "s3.region": "us-east-1"}},
       "storageConfigInfo": {{"storageType": "S3", "allowedLocations": ["s3://{warehouse}/"],
         "endpoint": "http://localhost:4566", "endpointInternal": "http://s3.localhost.localstack.cloud:4566",
         "pathStyleAccess": true, "stsUnavailable": true}}}}}}' >/dev/null || true
# The root principal manages everything in the catalog.
curl -s -X PUT http://localhost:8181/api/management/v1/catalogs/{warehouse}/catalog-roles/catalog_admin/grants \\
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
  -d '{{"grant": {{"type": "catalog", "privilege": "CATALOG_MANAGE_CONTENT"}}}}' >/dev/null
echo "The catalog {warehouse} is ready at http://localhost:8181/api/catalog, its files in s3://{warehouse}/."
"""


def snowflake_sql(project) -> str:
    """The external volume Snowflake writes Iceberg tables to, and the sync to Open Catalog."""
    lakehouse = settings(project)
    if lakehouse is None or lakehouse.table_format is not TableFormat.ICEBERG:
        return ""
    volume = external_volume(project)
    storage = (lakehouse.storage or f"s3://{project.manifest.project.name.lower()}-lake/").rstrip("/") + "/"
    lines = [
        "-- Where Snowflake writes the Iceberg tables' files (ACCOUNTADMIN, once). Then trust Snowflake in",
        "-- the AWS role: `describe external volume` gives STORAGE_AWS_IAM_USER_ARN and STORAGE_AWS_EXTERNAL_ID.",
        f"create external volume if not exists {volume}",
        "  storage_locations = ((",
        "    name = 'lake'",
        "    storage_provider = 'S3'",
        f"    storage_base_url = '{storage}'",
        "    storage_aws_role_arn = 'arn:aws:iam::<account id>:role/<role Snowflake assumes>'",
        "  ))",
        "  allow_writes = true;",
    ]
    if lakehouse.catalog is Catalog.POLARIS:
        name = project.manifest.project.name.upper()
        lines += [
            "",
            "-- Sync every Iceberg table to Snowflake Open Catalog (Polaris), so other engines find them there.",
            f"create catalog integration if not exists {name}_OPEN_CATALOG",
            "  catalog_source = polaris table_format = iceberg rest_config = (",
            f"    catalog_uri = '{lakehouse.catalog_uri or 'https://<org>-<account>.snowflakecomputing.com/polaris/api/catalog'}'",
            f"    catalog_name = '{lakehouse.warehouse or project.manifest.project.name}')",
            "  rest_authentication = (type = oauth oauth_client_id = '<client id>'"
            " oauth_client_secret = '<client secret>' oauth_allowed_scopes = ('PRINCIPAL_ROLE:ALL'))",
            "  enabled = true;",
        ]
    return "\n".join(lines) + "\n"


def render(project, plan) -> dict[str, str]:
    lakehouse = settings(project)
    if lakehouse is None:
        return {}
    files: dict[str, str] = {}
    warehouse = plan.target.warehouse
    if warehouse is Warehouse.DUCKDB and lakehouse.table_format is TableFormat.ICEBERG:
        files["dbt/macros/murakumo/publish_iceberg.sql"] = PUBLISH_MACRO
        files[f"{FOLDER}/docker-compose.yml"] = _compose(project)
        files[f"{FOLDER}/setup.sh"] = _setup(project)
    if warehouse is Warehouse.SNOWFLAKE:
        sql = snowflake_sql(project)
        if sql:
            files[f"{FOLDER}/snowflake.sql"] = sql
    if files:
        files[f"{FOLDER}/README.md"] = _readme(project, warehouse)
    return files


def _readme(project, warehouse: Warehouse) -> str:
    lakehouse = settings(project)
    layers = ", ".join(lakehouse.layers)
    lines = [
        "# The lakehouse",
        "",
        f"The {layers} tables are written as {lakehouse.table_format.value} tables"
        + (f", in the {lakehouse.catalog.value} catalog" if lakehouse.catalog is not Catalog.NONE else "")
        + ", so any engine can read them.",
        "",
    ]
    if warehouse is Warehouse.DUCKDB:
        lines += [
            "On this computer, with LocalStack's S3 as the object store and Polaris as the catalog:",
            "",
            "```bash",
            "docker compose -f generated/lakehouse/docker-compose.yml up -d",
            "bash generated/lakehouse/setup.sh",
            "murakumo build                 # each table is copied into the catalog after it is built",
            "```",
            "",
            "Point it at a catalog elsewhere with `LAKE_CATALOG_URI`, `LAKE_WAREHOUSE`, `LAKE_CLIENT_ID` and",
            "`LAKE_CLIENT_SECRET`, and at S3 with `LAKE_S3_ENDPOINT`, `LAKE_S3_KEY_ID` and `LAKE_S3_SECRET`.",
        ]
    elif warehouse is Warehouse.SNOWFLAKE:
        lines += [
            "Run `snowflake.sql` once as ACCOUNTADMIN: the external volume the tables' files go to"
            + (", and the sync to Open Catalog." if lakehouse.catalog is Catalog.POLARIS else "."),
        ]
    elif warehouse is Warehouse.DATABRICKS:
        lines += ["Unity Catalog manages them: nothing to set up beyond the catalog."]
    return "\n".join(lines) + "\n"


__all__ = ["ALIAS", "FOLDER", "duckdb_profile", "in_lake", "model_config", "render", "settings"]
