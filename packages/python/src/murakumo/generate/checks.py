"""The model's quality rules for other tools: Great Expectations suites and Soda checks.

dbt tests and the PySpark/Polars jobs always carry the rules. With ``quality.framework`` naming
``great_expectations`` or ``soda`` too, ``generate`` also writes:

    quality/great_expectations/expectations/<table>.json   an expectation suite per table
    quality/great_expectations/run.py                       validates every suite, exits 1 on a failure
    quality/soda/checks/<table>.yml                         SodaCL checks per table
    quality/soda/configuration.yml                          the warehouse, from environment variables
    quality/soda/run.py                                     runs a scan per table, exits 1 on a failure

Each table is checked where the rules apply: a raw table's rules on its staging model (the rows
that reached it), the other tables' on their own model. Rules that set rows aside (quarantine) are
left out: their rows never reach the table, so the check could not fail.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import yaml

from murakumo.generate.plain import LAYER_SCHEMAS
from murakumo.generate.plan import DbtModel, Plan, is_history, snake
from murakumo.ir.model import Attribute, Entity, KeyRole, Layer, QualityRule, RuleAction, RuleType
from murakumo.manifest import Warehouse

TOOLS = ("great_expectations", "soda")


@dataclass
class Check:
    rule: QualityRule
    column: str | None  # the column's name in the built table, or None for a table rule
    implied: bool = False  # from the model (a key, nullable: false, accepted values), not a written rule
    attribute: Attribute | None = None

    @property
    def values(self) -> list:
        """The accepted values, typed as the column is: 1, not "1", for a number."""
        from murakumo.generate.sample import _accepted

        if self.attribute is None:
            return list(self.rule.values or [])
        return [_accepted(self.attribute, str(v)) for v in self.rule.values or []]


@dataclass
class Table:
    model: DbtModel
    schema: str
    entity: Entity
    checks: list[Check] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.model.name


def _column_name(entity: Entity, attribute: Attribute) -> str:
    return snake(attribute.name) if entity.layer is Layer.RAW else attribute.name


def tables(plan: Plan) -> list[Table]:
    """Every built table with the checks that apply to it."""
    found = []
    for model in plan.models:
        if is_history(model) or model.layer == "intermediate":
            continue
        entity = model.entity
        if entity.layer is Layer.RAW and model.layer != "staging":
            continue
        if entity.layer is not Layer.RAW and model.name != entity.name:
            continue
        table = Table(model, LAYER_SCHEMAS.get(model.layer, model.layer), entity)
        names = {a.name.lower(): _column_name(entity, a) for a in entity.attributes}
        primary = entity.primary_key
        if len(primary) == 1:
            column = names[primary[0].name.lower()]
            table.checks += [Check(QualityRule(type="unique"), column, True, primary[0])]
        elif len(primary) > 1:
            columns = [names[a.name.lower()] for a in primary]
            table.checks.append(Check(QualityRule(type="unique_combination", columns=columns), None, True))
        for attribute in entity.attributes:
            column = names[attribute.name.lower()]
            if not attribute.nullable or attribute.key is KeyRole.PRIMARY:
                table.checks.append(Check(QualityRule(type="not_null"), column, True, attribute))
            if attribute.accepted_values:
                rule = QualityRule(type="accepted_values", values=sorted(attribute.accepted_values))
                table.checks.append(Check(rule, column, True, attribute))
            table.checks += [Check(rule, column, False, attribute) for rule in attribute.tests if _checked(rule)]
        for rule in entity.tests:
            if _checked(rule):
                if rule.columns:
                    rule = rule.model_copy(update={"columns": [names.get(c.lower(), c) for c in rule.columns]})
                table.checks.append(Check(rule, None))
        if table.checks:
            found.append(table)
    return found


def _checked(rule: QualityRule) -> bool:
    return not (rule.effective_action is RuleAction.QUARANTINE and rule.row_level) and rule.type is not (
        RuleType.FRESHNESS
    )


# Great Expectations -----------------------------------------------------------------------------


def expectation(check: Check, table: str) -> dict | None:
    """One rule as a Great Expectations 1.x expectation."""
    rule, column = check.rule, check.column
    between = {k: v for k, v in (("min_value", rule.min), ("max_value", rule.max)) if v is not None}
    meta = {"murakumo_rule": rule.type.value, "severity": rule.severity.value}
    if rule.description:
        meta["description"] = rule.description
    match rule.type:
        case RuleType.NOT_NULL:
            kind, kwargs = "expect_column_values_to_not_be_null", {"column": column}
        case RuleType.UNIQUE:
            kind, kwargs = "expect_column_values_to_be_unique", {"column": column}
        case RuleType.ACCEPTED_VALUES:
            kind, kwargs = "expect_column_values_to_be_in_set", {"column": column, "value_set": _plain(check.values)}
        case RuleType.RANGE:
            kind, kwargs = "expect_column_values_to_be_between", {"column": column, **between}
        case RuleType.REGEX:
            kind, kwargs = "expect_column_values_to_match_regex", {"column": column, "regex": rule.pattern}
        case RuleType.LENGTH:
            kind, kwargs = "expect_column_value_lengths_to_be_between", {"column": column, **between}
        case RuleType.UNIQUE_COMBINATION:
            kind, kwargs = "expect_compound_columns_to_be_unique", {"column_list": rule.columns}
        case RuleType.ROW_COUNT:
            kind, kwargs = "expect_table_row_count_to_be_between", between
        case RuleType.NULL_SHARE:
            kind, kwargs = "expect_column_values_to_not_be_null", {"column": column, "mostly": 1 - (rule.max or 0)}
        case RuleType.DISTINCT_COUNT:
            kind, kwargs = "expect_column_unique_value_count_to_be_between", {"column": column, **between}
        case RuleType.RECENT:
            # The lower bound is "now minus max_age_hours", so run.py sets it when it runs.
            kind, kwargs = "expect_column_max_to_be_between", {"column": column}
            meta["max_age_hours"] = rule.max_age_hours
        case RuleType.CUSTOM_SQL:
            kind = "unexpected_rows_expectation"
            kwargs = {"unexpected_rows_query": f"select * from {{batch}} where not ({rule.sql})"}
        case _:
            return None
    return {"type": kind, "kwargs": kwargs, "meta": meta}


def great_expectations(project, plan: Plan) -> dict[str, str]:
    files = {}
    names = []
    for table in tables(plan):
        expectations = [e for check in table.checks if (e := expectation(check, table.name))]
        suite = {
            "name": table.name,
            "meta": {"schema": table.schema, "table": table.name, "generated_by": "murakumo"},
            "expectations": expectations,
        }
        files[f"quality/great_expectations/expectations/{table.name}.json"] = json.dumps(suite, indent=2) + "\n"
        names.append(table.name)
    if not names:
        return {}
    warehouse = plan.target.warehouse
    files["quality/great_expectations/run.py"] = _GX_RUN.replace("__WAREHOUSE__", warehouse.value).replace(
        "__DUCKDB__", _duckdb_default(project)
    )
    files["quality/great_expectations/requirements.txt"] = "great_expectations>=1.3\nsqlalchemy>=2,<2.1\n" + (
        "duckdb>=1.4\npandas\n" if warehouse is Warehouse.DUCKDB else _driver(warehouse)
    )
    files["quality/great_expectations/README.md"] = _readme(project, "Great Expectations", names, warehouse)
    return files


def _driver(warehouse: Warehouse) -> str:
    return {
        Warehouse.SNOWFLAKE: "snowflake-sqlalchemy\n",
        Warehouse.POSTGRES: "psycopg[binary]\n",
        Warehouse.REDSHIFT: "sqlalchemy-redshift\nredshift-connector\n",
        Warehouse.BIGQUERY: "sqlalchemy-bigquery\n",
        Warehouse.DATABRICKS: "databricks-sqlalchemy\n",
        Warehouse.FABRIC: "pyodbc\n",
        Warehouse.SYNAPSE: "pyodbc\n",
    }.get(warehouse, "")


def _duckdb_default(project) -> str:
    from murakumo.environments import placement_for

    try:
        placement = placement_for(project, None, project.manifest.targets.warehouse.type, None)
        return str(placement.database or f"{project.manifest.project.name}.duckdb")
    except Exception:  # noqa: BLE001 - only a default; MURAKUMO_DUCKDB_PATH overrides it
        return f"{project.manifest.project.name}.duckdb"


# Soda -------------------------------------------------------------------------------------------


def soda_check(check: Check) -> object | None:
    """One rule as a SodaCL check: a string, or {check: settings}."""
    rule, column = check.rule, check.column
    warn = rule.severity.value == "warn"

    def threshold(metric: str, failing: str) -> object:
        # A warning rule warns instead of failing: `warn: when <failing>`.
        return {metric: {"warn": f"when {failing}"}} if warn else f"{metric} = 0"

    def bounded(metric: str) -> object:
        parts = []
        if rule.min is not None:
            parts.append(f"< {_number(rule.min)}")
        if rule.max is not None:
            parts.append(f"> {_number(rule.max)}")
        settings = {("warn" if warn else "fail"): " or ".join(f"when {p}" for p in parts)}
        return {metric: settings}

    match rule.type:
        case RuleType.NOT_NULL:
            return threshold(f"missing_count({column})", "> 0")
        case RuleType.UNIQUE:
            return threshold(f"duplicate_count({column})", "> 0")
        case RuleType.UNIQUE_COMBINATION:
            return threshold(f"duplicate_count({', '.join(rule.columns)})", "> 0")
        case RuleType.ACCEPTED_VALUES | RuleType.RANGE | RuleType.REGEX | RuleType.LENGTH:
            valid: dict = {}
            if rule.type is RuleType.ACCEPTED_VALUES:
                valid["valid values"] = _plain(check.values)
            elif rule.type is RuleType.RANGE:
                valid.update(
                    {f"valid {k}": _number(v) for k, v in (("min", rule.min), ("max", rule.max)) if v is not None}
                )
            elif rule.type is RuleType.REGEX:
                valid["valid regex"] = rule.pattern
            else:
                valid.update(
                    {f"valid {k} length": int(v) for k, v in (("min", rule.min), ("max", rule.max)) if v is not None}
                )
            metric = f"invalid_count({column})"
            return {metric: {**valid, ("warn" if warn else "fail"): "when > 0"}}
        case RuleType.ROW_COUNT:
            return bounded("row_count")
        case RuleType.DISTINCT_COUNT:
            return bounded(f"distinct({column})")
        case RuleType.NULL_SHARE:
            return {f"missing_percent({column})": {("warn" if warn else "fail"): f"when > {_number(rule.max * 100)}"}}
        case RuleType.RECENT:
            return {f"freshness({column})": {("warn" if warn else "fail"): f"when > {_number(rule.max_age_hours)}h"}}
        case RuleType.CUSTOM_SQL:
            name = rule.description or f"custom rule: {rule.sql}"
            return {"failed rows": {"name": name, "fail condition": f"not ({rule.sql})"}}
    return None


def _plain(values: list) -> list:
    """Values JSON and YAML can hold: numbers and text (a Decimal becomes a float)."""
    from decimal import Decimal

    return [float(v) if isinstance(v, Decimal) else v for v in values]


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def soda(project, plan: Plan) -> dict[str, str]:
    files = {}
    names = []
    for table in tables(plan):
        checks = [c for check in table.checks if (c := soda_check(check)) is not None]
        if not checks:
            continue
        document = {f"checks for {table.schema}.{table.name}": checks}
        header = "# Generated by Murakumo. Do not edit: change the model, then run `murakumo generate`.\n"
        files[f"quality/soda/checks/{table.name}.yml"] = header + yaml.safe_dump(document, sort_keys=False)
        names.append(table.name)
    if not names:
        return {}
    warehouse = plan.target.warehouse
    files["quality/soda/configuration.yml"] = _soda_configuration(project, warehouse)
    files["quality/soda/run.py"] = _SODA_RUN
    package = {
        Warehouse.DUCKDB: "soda-core-duckdb",
        Warehouse.SNOWFLAKE: "soda-core-snowflake",
        Warehouse.POSTGRES: "soda-core-postgres",
        Warehouse.REDSHIFT: "soda-core-redshift",
        Warehouse.BIGQUERY: "soda-core-bigquery",
        Warehouse.DATABRICKS: "soda-core-spark[databricks]",
        Warehouse.FABRIC: "soda-core-fabric",
        Warehouse.SYNAPSE: "soda-core-sqlserver",
    }[warehouse]
    files["quality/soda/requirements.txt"] = f"{package}>=3.3,<4\nsetuptools  # Soda Core 3 imports distutils\n" + (
        "pytz  # DuckDB returns timestamps with time zones through it\nduckdb>=1.4\n"
        if warehouse is Warehouse.DUCKDB
        else ""
    )
    files["quality/soda/README.md"] = _readme(project, "Soda", names, warehouse)
    return files


def _soda_configuration(project, warehouse: Warehouse) -> str:
    """The data source ``warehouse``, its settings read from the variables the dbt profile reads."""
    header = (
        "# Generated by Murakumo. The warehouse Soda checks, signed in with the same environment\n"
        "# variables as the dbt profile.\n"
    )
    settings: dict = {
        Warehouse.DUCKDB: {"type": "duckdb", "path": "${MURAKUMO_DUCKDB_PATH}", "read_only": True},
        Warehouse.SNOWFLAKE: {
            "type": "snowflake",
            "account": "${SNOWFLAKE_ACCOUNT}",
            "username": "${SNOWFLAKE_USER}",
            "private_key_path": "${SNOWFLAKE_PRIVATE_KEY_PATH}",
            "database": "${SNOWFLAKE_DATABASE}",
            "warehouse": "${SNOWFLAKE_WAREHOUSE}",
            "role": "${SNOWFLAKE_ROLE}",
        },
        Warehouse.POSTGRES: {
            "type": "postgres",
            "host": "${DB_HOST}",
            "port": "${DB_PORT}",
            "username": "${DB_USER}",
            "password": "${DB_PASSWORD}",
            "database": "${DB_NAME}",
        },
        Warehouse.REDSHIFT: {
            "type": "redshift",
            "host": "${DB_HOST}",
            "port": "${DB_PORT}",
            "username": "${DB_USER}",
            "password": "${DB_PASSWORD}",
            "database": "${DB_NAME}",
        },
        Warehouse.BIGQUERY: {
            "type": "bigquery",
            "project_id": "${BIGQUERY_PROJECT}",
            "dataset": "staging",
            "use_context_auth": True,
        },
        Warehouse.DATABRICKS: {
            "type": "spark",
            "method": "databricks",
            "host": "${DATABRICKS_HOST}",
            "http_path": "${DATABRICKS_HTTP_PATH}",
            "token": "${DATABRICKS_TOKEN}",
            "catalog": "${DATABRICKS_CATALOG}",
        },
        Warehouse.FABRIC: {
            "type": "fabric",
            "host": "${FABRIC_SERVER}",
            "database": project.manifest.project.name,
            "authentication": "ActiveDirectoryServicePrincipal",
            "client_id": "${AZURE_CLIENT_ID}",
            "client_secret": "${AZURE_CLIENT_SECRET}",
        },
        Warehouse.SYNAPSE: {
            "type": "sqlserver",
            "host": "${SYNAPSE_SERVER}",
            "database": project.manifest.project.name,
            "authentication": "ActiveDirectoryServicePrincipal",
            "client_id": "${AZURE_CLIENT_ID}",
            "client_secret": "${AZURE_CLIENT_SECRET}",
        },
    }[warehouse]
    return header + yaml.safe_dump({"data_source warehouse": settings}, sort_keys=False)


def _readme(project, tool: str, names: list[str], warehouse: Warehouse) -> str:
    folder = "great_expectations" if tool == "Great Expectations" else "soda"
    lines = [
        f"# {project.manifest.project.name}'s quality rules, for {tool}",
        "",
        "Generated by Murakumo from the model: the same rules as the dbt tests and the Python jobs.",
        f"{len(names)} table(s): " + ", ".join(f"`{n}`" for n in names) + ".",
        "",
        "```bash",
        f"pip install -r generated/quality/{folder}/requirements.txt",
        f"python generated/quality/{folder}/run.py            # every table",
        f"python generated/quality/{folder}/run.py {names[0]}   # one",
        "```",
        "",
        "It reads the tables a build made, in the warehouse the dbt profile points at, signed in with the",
        "same environment variables. MURAKUMO_SCHEMA_PREFIX and MURAKUMO_SINGLE_SCHEMA name the schemas",
        "as they do for dbt.",
    ]
    if warehouse is Warehouse.DUCKDB:
        lines += ["", "On DuckDB, MURAKUMO_DUCKDB_PATH names the database file."]
    lines += [
        "",
        "Rules that set rows aside (quarantine) are not here: those rows never reach the table. Their",
        "rows are in `<staging model>__quarantine`, and `murakumo monitor` reports how many.",
        "",
        "Exit code 0 when every check passes or only warns, 1 when one fails.",
        "",
    ]
    return "\n".join(lines)


_SCHEMA_HELPER = '''
def schema_for(layer):
    """The schema a layer is in, as dbt names it for this run (see the generated schema_names macro)."""
    single = os.environ.get("MURAKUMO_SINGLE_SCHEMA")
    return single or os.environ.get("MURAKUMO_SCHEMA_PREFIX", "") + layer
'''

_GX_RUN = (
    '''"""Generated by Murakumo. Validate every expectation suite against the built tables.

    python run.py              # every table
    python run.py <table> ...  # some of them
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("GX_ANALYTICS_ENABLED", "false")

import great_expectations as gx  # noqa: E402
from great_expectations.core.expectation_suite import ExpectationSuite  # noqa: E402

HERE = Path(__file__).resolve().parent
WAREHOUSE = "__WAREHOUSE__"
'''
    + _SCHEMA_HELPER
    + '''

def suites(names):
    for path in sorted((HERE / "expectations").glob("*.json")):
        suite = json.loads(path.read_text())
        if not names or suite["name"] in names:
            yield suite


def ready(suite):
    """Fill in what depends on when it runs: the oldest a 'recent' column's newest value may be."""
    for expectation in suite["expectations"]:
        hours = expectation.get("meta", {}).get("max_age_hours")
        if hours is not None:
            oldest = datetime.now(timezone.utc) - timedelta(hours=float(hours))
            expectation["kwargs"]["min_value"] = oldest.replace(tzinfo=None).isoformat(sep=" ")
            expectation["kwargs"]["parse_strings_as_datetimes"] = True
    return suite


def batches(context, suite):
    """The table as Great Expectations reads it: on DuckDB a dataframe, elsewhere the warehouse."""
    schema, table = schema_for(suite["meta"]["schema"]), suite["meta"]["table"]
    if WAREHOUSE == "duckdb":
        import duckdb

        path = os.environ.get("MURAKUMO_DUCKDB_PATH", str(HERE.parents[2] / "__DUCKDB__"))
        with duckdb.connect(path, read_only=True) as connection:
            frame = connection.execute(f'select * from "{schema}"."{table}"').df()
        source = context.data_sources.add_pandas(name=table)
        definition = source.add_dataframe_asset(name=table).add_batch_definition_whole_dataframe("all")
        return definition, {"dataframe": frame}
    url = os.environ.get("MURAKUMO_GX_URL")
    if not url:
        sys.exit("Set MURAKUMO_GX_URL to the warehouse's SQLAlchemy URL (see the README).")
    source = context.data_sources.add_sql(name=table, connection_string=url)
    asset = source.add_table_asset(name=table, table_name=table, schema_name=schema)
    return asset.add_batch_definition_whole_table("all"), {}


def main(names):
    context = gx.get_context(mode="ephemeral")
    failed = warned = 0
    for suite in suites(names):
        suite = ready(suite)
        definition, parameters = batches(context, suite)
        validation = gx.ValidationDefinition(
            name=suite["name"],
            data=definition,
            suite=context.suites.add(ExpectationSuite(name=suite["name"], expectations=suite["expectations"])),
        )
        result = context.validation_definitions.add(validation).run(batch_parameters=parameters)
        for outcome in result.results:
            if outcome.success:
                continue
            severity = outcome.expectation_config.meta.get("severity", "error")
            found = outcome.result.get("unexpected_count", outcome.result.get("observed_value"))
            print(f"{severity}: {suite['name']}: {outcome.expectation_config.type} ({found})")
            if severity == "warn":
                warned += 1
            else:
                failed += 1
    print(f"{failed} failed, {warned} warned.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''
)

_SODA_RUN = (
    '''"""Generated by Murakumo. Run the Soda checks against the built tables.

    python run.py              # every table
    python run.py <table> ...  # some of them
"""

import os
import sys
from pathlib import Path

from soda.scan import Scan

HERE = Path(__file__).resolve().parent
'''
    + _SCHEMA_HELPER
    + '''

def checks(path):
    """The checks, with the schemas this run uses: `checks for staging.x` reads <prefix>staging.x."""
    text = path.read_text()
    for layer in ("staging", "intermediate", "marts", "snapshots"):
        text = text.replace(f"checks for {layer}.", f"checks for {schema_for(layer)}.")
    return text


def main(names):
    failed = 0
    for path in sorted((HERE / "checks").glob("*.yml")):
        if names and path.stem not in names:
            continue
        scan = Scan()
        scan.set_data_source_name("warehouse")
        scan.add_configuration_yaml_file(str(HERE / "configuration.yml"))
        scan.add_sodacl_yaml_str(checks(path))
        scan.execute()
        print(scan.get_logs_text())
        if scan.has_check_fails() or scan.has_error_logs():
            failed += 1
    print(f"{failed} table(s) with failing checks.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''
)


def render(project, plan: Plan) -> dict[str, str]:
    """The files for each tool quality.framework names."""
    chosen = set(project.manifest.quality.framework)
    files: dict[str, str] = {}
    if "great_expectations" in chosen:
        files.update(great_expectations(project, plan))
    if "soda" in chosen:
        files.update(soda(project, plan))
    return files


__all__ = ["TOOLS", "Check", "Table", "expectation", "render", "soda_check", "tables"]
