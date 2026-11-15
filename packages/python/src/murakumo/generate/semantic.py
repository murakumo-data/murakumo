"""The semantic layer: dbt semantic models and metrics (MetricFlow) from ``model/metrics.yaml``.

Each table a metric is counted on gets a semantic model: its key as the primary entity, its
relationships to dimensions as foreign entities, its time column, the columns metrics break it
down by, and a measure per aggregate. Each dimension those metrics use gets one too, so
MetricFlow can join to it (an SCD2 dimension by its validity, so each fact row finds the version
current at the time). A metric with one aggregate is a simple metric; one that combines several
(``sum(a) / count(*)``) is a derived metric over a hidden simple metric per aggregate.

MetricFlow also needs a time spine, a table with one row per day: ``metricflow_time_spine``.
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp

from murakumo.diagnostics import Diagnostic, error, warning
from murakumo.generate.plan import VERSIONED, DbtModel, Plan
from murakumo.ir.model import Entity, KeyRole, Metric

FOLDER = "semantic"
TIME_SPINE = "metricflow_time_spine"
AGGREGATES = {exp.Sum: "sum", exp.Avg: "average", exp.Min: "min", exp.Max: "max", exp.Count: "count"}

# One row per day, 2000 to 2040, in each warehouse's own way of making rows.
_SPINE = {
    "duckdb": (
        "select cast(range as date) as date_day\nfrom range(date '2000-01-01', date '2041-01-01', interval 1 day)"
    ),
    "snowflake": (
        "select dateadd(day, seq4(), '2000-01-01'::date) as date_day\nfrom table(generator(rowcount => 14976))"
    ),
    "databricks": "select explode(sequence(date'2000-01-01', date'2040-12-31', interval 1 day)) as date_day",
    "bigquery": "select date_day\nfrom unnest(generate_date_array('2000-01-01', '2040-12-31')) as date_day",
    "postgres": (
        "select cast(day as date) as date_day\n"
        "from generate_series('2000-01-01'::date, '2040-12-31'::date, interval '1 day') as day"
    ),
}


def _time_column(metric: Metric, entity: Entity) -> str | None:
    if metric.time:
        return metric.time
    for attribute in entity.attributes:
        if attribute.logical_type.family in ("date", "timestamp"):
            return attribute.name
    return None


def problems(project) -> list[Diagnostic]:
    out = []
    model = project.model
    for metric in model.metrics:
        entity = model.entity(metric.entity)
        if entity is None:
            continue  # MK208
        time = _time_column(metric, entity)
        if time is None or entity.attribute(time) is None:
            out.append(
                error("MK210", f"metric {metric.name}: {entity.name} has no date or timestamp column to count it by")
            )
        try:
            tree = sqlglot.parse_one(metric.expression, read="duckdb")
        except sqlglot.errors.ParseError:
            out.append(error("MK211", f"metric {metric.name}: cannot read its expression {metric.expression!r}"))
            continue
        if not list(tree.find_all(*AGGREGATES)):
            out.append(error("MK211", f"metric {metric.name}: its expression needs an aggregate, such as sum(...)"))
        for dimension in metric.dimensions:
            table, _, column = dimension.partition(".")
            other = model.entity(table) if column else entity
            column = column or table
            if other is None and table.lower() in project.unloaded:
                continue  # its file has an error of its own
            if other is None or other.attribute(column) is None:
                out.append(warning("MK212", f"metric {metric.name}: dimension {dimension!r} does not exist"))
    return out


class Semantic:
    def __init__(self, plan: Plan):
        self.plan = plan
        self.model = plan.project.model
        self.dialect = plan.target.dialect

    def built(self, name: str) -> DbtModel | None:
        model = self.plan.model(name)
        return model if model is not None and model.entity.name == name else None

    def measures(self, metric: Metric, filters: list[str]) -> tuple[list[dict], str | None]:
        """A measure per aggregate in the expression, and the expression over them if it has more than one."""
        tree = sqlglot.parse_one(metric.expression, read="duckdb")
        found = list(tree.find_all(*AGGREGATES))
        condition = " and ".join(f"({f})" for f in filters)
        measures = []
        for index, node in enumerate(found):
            name = metric.name if len(found) == 1 and node is tree else f"{metric.name}_part_{index + 1}"
            agg = AGGREGATES[type(node)]
            inner = node.this
            if isinstance(inner, exp.Distinct):
                agg = "count_distinct"
                inner = inner.expressions[0]
            if isinstance(inner, exp.Star) or inner is None:
                expr = "1"
                agg = "sum" if agg == "count" else agg
            else:
                expr = inner.sql(dialect=self.dialect)
            if condition:
                expr = f"case when {condition} then {expr} end"
            measures.append({"name": name, "agg": agg, "expr": expr, "node": node})
        derived = None
        if len(measures) > 1 or (measures and measures[0]["node"] is not tree):
            for measure in measures:
                measure["node"].replace(exp.column(measure["name"]))
            derived = tree.sql(dialect=self.dialect)
        for measure in measures:
            measure.pop("node")
        return measures, derived

    def render(self) -> tuple[dict[str, str], list[Diagnostic]]:
        metrics = [m for m in self.model.metrics if self.built(m.entity) is not None]
        if not metrics:
            return {}, []
        semantic_models: dict[str, dict[str, Any]] = {}
        out_metrics: list[dict[str, Any]] = []
        dimension_columns: dict[str, set[str]] = {}

        for metric in metrics:
            entity = self.model.entity(metric.entity)
            time = _time_column(metric, entity)
            if time is None:
                continue  # MK210
            sm = semantic_models.setdefault(entity.name, self.semantic_model(entity, time))
            measures, derived = self.measures(metric, metric.filters)
            sm["measures"] += [{k: v for k, v in m.items()} for m in measures]
            for dimension in metric.dimensions:
                table, _, column = dimension.partition(".")
                if not column:
                    table, column = entity.name, table
                dimension_columns.setdefault(table, set()).add(column)
            label = metric.label or metric.name.replace("_", " ").capitalize()
            if derived is None:
                out_metrics.append(
                    {
                        "name": metric.name,
                        "label": label,
                        "description": metric.description or label,
                        "type": "simple",
                        "type_params": {"measure": measures[0]["name"]},
                    }
                )
            else:
                for measure in measures:
                    out_metrics.append(
                        {
                            "name": measure["name"],
                            "label": f"{label} ({measure['name']})",
                            "type": "simple",
                            "type_params": {"measure": measure["name"]},
                            "config": {"meta": {"part_of": metric.name}},
                        }
                    )
                out_metrics.append(
                    {
                        "name": metric.name,
                        "label": label,
                        "description": metric.description or label,
                        "type": "derived",
                        "type_params": {"expr": derived, "metrics": [{"name": m["name"]} for m in measures]},
                    }
                )
            if metric.grain:
                out_metrics[-1]["time_granularity"] = metric.grain

        for table, columns in sorted(dimension_columns.items()):
            entity = self.model.entity(table)
            if entity is None or self.built(entity.name) is None:
                continue
            sm = semantic_models.get(entity.name)
            if sm is None:
                sm = semantic_models.setdefault(entity.name, self.dimension_model(entity))
            names = {d["name"] for d in sm["dimensions"]}
            for column in sorted(columns):
                attribute = entity.attribute(column)
                if attribute is None or attribute.name in names:
                    continue
                if attribute.logical_type.family in ("date", "timestamp"):
                    sm["dimensions"].append(
                        {"name": attribute.name, "type": "time", "type_params": {"time_granularity": "day"}}
                    )
                else:
                    sm["dimensions"].append({"name": attribute.name, "type": "categorical"})

        models = [semantic_models[n] for n in sorted(semantic_models)]
        for sm in models:
            if not sm["measures"]:
                sm.pop("measures")
                sm.pop("defaults", None)
        files = {
            f"models/{FOLDER}/{TIME_SPINE}.sql": self.time_spine(),
            f"models/{FOLDER}/_semantic.yml": _yaml(
                {
                    "version": 2,
                    "models": [
                        {
                            "name": TIME_SPINE,
                            "description": "One row per day, 2000 to 2040: MetricFlow's time spine.",
                            "time_spine": {"standard_granularity_column": "date_day"},
                            "columns": [{"name": "date_day", "granularity": "day"}],
                        }
                    ],
                    "semantic_models": models,
                    "metrics": out_metrics,
                }
            ),
        }
        return files, []

    def keys(self, entity: Entity) -> list[str]:
        return [a.name for a in entity.primary_key] or list(entity.grain)

    def foreign(self, entity: Entity) -> list[dict]:
        out = []
        for rel in self.model.relationships:
            if rel.from_.lower() != entity.name.lower() or len(rel.keys) != 1:
                continue
            parent = self.model.entity(rel.to)
            if parent is None or self.built(parent.name) is None or parent.kind.value != "dimension":
                continue
            if any(e["name"] == parent.name for e in out):
                continue  # a second role (pickup and dropoff zone): MetricFlow joins by name, so the first
            out.append({"name": parent.name, "type": "foreign", "expr": rel.keys[0].from_})
        return out

    def semantic_model(self, entity: Entity, time: str) -> dict[str, Any]:
        keys = self.keys(entity)
        entities: list[dict] = []
        sm: dict[str, Any] = {
            "name": entity.name,
            "description": entity.description or entity.name,
            "model": f"ref('{entity.name}')",
            "defaults": {"agg_time_dimension": time},
        }
        if len(keys) == 1:
            entities.append({"name": entity.name, "type": "primary", "expr": keys[0]})
        else:
            sm["primary_entity"] = entity.name
        sm["entities"] = entities + self.foreign(entity)
        sm["dimensions"] = [{"name": time, "type": "time", "type_params": {"time_granularity": "day"}}]
        sm["measures"] = []
        return sm

    def dimension_model(self, entity: Entity) -> dict[str, Any]:
        keys = [a.name for a in entity.attributes if a.key in (KeyRole.PRIMARY, KeyRole.NATURAL)] or list(entity.grain)
        sm: dict[str, Any] = {
            "name": entity.name,
            "description": entity.description or entity.name,
            "model": f"ref('{entity.name}')",
            "dimensions": [],
            "measures": [],
        }
        if entity.history in VERSIONED and entity.history.value != "scd4":
            # every version: a fact row joins the version that was current at its time
            sm["entities"] = [{"name": entity.name, "type": "natural", "expr": keys[0]}]
            sm["primary_entity"] = f"{entity.name}_version"
            sm["dimensions"] = [
                {
                    "name": "valid_from",
                    "type": "time",
                    "type_params": {"time_granularity": "day", "validity_params": {"is_start": True}},
                },
                {
                    "name": "valid_to",
                    "type": "time",
                    "type_params": {"time_granularity": "day", "validity_params": {"is_end": True}},
                },
            ]
        else:
            sm["entities"] = [{"name": entity.name, "type": "primary", "expr": keys[0]}]
        return sm

    def time_spine(self) -> str:
        body = _SPINE.get(self.dialect, _SPINE["postgres"])
        return (
            "-- Generated by Murakumo: MetricFlow's time spine, one row per day.\n"
            "{{ config(materialized='table') }}\n\n" + body + "\n"
        )


def _yaml(data) -> str:
    import yaml

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def render(plan: Plan) -> dict[str, str]:
    files, _ = Semantic(plan).render()
    return files


__all__ = ["FOLDER", "TIME_SPINE", "problems", "render"]
