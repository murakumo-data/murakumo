"""The semantic layer: dbt semantic models and metrics from model/metrics.yaml."""

from pathlib import Path

import pytest
import yaml

from murakumo.generate import generate
from murakumo.ir.model import Metric
from murakumo.project import load_project
from murakumo.validate import validate_project

COMMERCE = Path(__file__).resolve().parents[3] / "examples" / "commerce"


@pytest.fixture
def commerce():
    return load_project(COMMERCE).project


def semantic(project):
    files = generate(project).files
    return files, yaml.safe_load(files["dbt/models/semantic/_semantic.yml"])


def test_each_metric_becomes_a_measure_and_a_metric(commerce):
    files, data = semantic(commerce)
    fact = next(s for s in data["semantic_models"] if s["name"] == "fct_order_line")
    assert fact["defaults"] == {"agg_time_dimension": "ordered_on"}
    assert {"name": "revenue", "agg": "sum", "expr": "net_amount"} in fact["measures"]
    assert {"name": "orders", "agg": "count_distinct", "expr": "order_id"} in fact["measures"]
    metrics = {m["name"]: m for m in data["metrics"]}
    assert metrics["revenue"]["type"] == "simple"
    assert metrics["revenue"]["time_granularity"] == "day"
    # foreign entities follow the relationships to dimensions
    assert {"name": "dim_customer", "type": "foreign", "expr": "customer_id"} in fact["entities"]
    assert "dbt/models/semantic/metricflow_time_spine.sql" in files


def test_combined_aggregates_become_a_derived_metric(commerce):
    _, data = semantic(commerce)
    metrics = {m["name"]: m for m in data["metrics"]}
    derived = metrics["average_order_value"]
    assert derived["type"] == "derived"
    assert derived["type_params"]["expr"] == "average_order_value_part_1 / average_order_value_part_2"
    assert metrics["average_order_value_part_2"]["type_params"]["measure"] == "average_order_value_part_2"


def test_filters_and_count_star(commerce):
    _, data = semantic(commerce)
    fact = next(s for s in data["semantic_models"] if s["name"] == "fct_order_line")
    measure = next(m for m in fact["measures"] if m["name"] == "open_order_lines")
    assert measure == {"name": "open_order_lines", "agg": "sum", "expr": "case when (order_status = 'O') then 1 end"}


def test_scd2_dimensions_join_by_validity(commerce):
    _, data = semantic(commerce)
    customer = next(s for s in data["semantic_models"] if s["name"] == "dim_customer")
    assert customer["entities"] == [{"name": "dim_customer", "type": "natural", "expr": "customer_id"}]
    starts = [d for d in customer["dimensions"] if d.get("type_params", {}).get("validity_params", {}).get("is_start")]
    assert [d["name"] for d in starts] == ["valid_from"]
    assert {"name": "market_segment", "type": "categorical"} in customer["dimensions"]


def test_time_spine_per_warehouse(commerce):
    from murakumo.manifest import Warehouse

    files = generate(commerce, Warehouse.SNOWFLAKE).files
    assert "generator(rowcount" in files["dbt/models/semantic/metricflow_time_spine.sql"]


def test_metrics_are_checked(commerce):
    commerce.model.metrics.append(Metric(name="bad", entity="dim_part", expression="part_name"))
    codes = {d.code for d in validate_project(commerce)}
    assert {"MK210", "MK211"} <= codes


def test_no_metrics_no_semantic_layer(commerce):
    commerce.model.metrics = []
    assert "dbt/models/semantic/_semantic.yml" not in generate(commerce).files
