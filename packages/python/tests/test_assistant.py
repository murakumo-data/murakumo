"""The assistant, with a stand-in for the model provider: no key, no network."""

import io
import json

import pytest
from typer.testing import CliRunner

from murakumo import assistant, mcp
from murakumo.cli import app
from murakumo.project import load_project, save_model
from murakumo.scaffold import init_project
from murakumo.validate import validate_project

runner = CliRunner()


class Fake:
    """Answers with what it was given, and remembers what it was asked."""

    def __init__(self, answer):
        self.answer = answer
        self.prompts = []
        self.schemas = []

    def complete(self, system, prompt, schema):
        self.prompts.append(prompt)
        self.schemas.append(schema)
        return self.answer


@pytest.fixture
def taxi(tmp_path):
    project = init_project(tmp_path / "taxi")
    project.manifest.assistant.enabled = True
    return project


def test_it_is_off_until_turned_on(tmp_path):
    project = init_project(tmp_path / "taxi")
    assert project.manifest.assistant.enabled is False
    with pytest.raises(assistant.AssistantError, match="murakumo ai enable"):
        assistant.suggest(project, "describe")
    result = runner.invoke(app, ["ai", "describe", "-p", str(project.root)])
    assert result.exit_code == 1 and "off for this project" in result.output
    assert runner.invoke(app, ["ai", "enable", "-p", str(project.root)]).exit_code == 0
    assert load_project(project.root).project.manifest.assistant.enabled is True
    assert runner.invoke(app, ["ai", "disable", "-p", str(project.root)]).exit_code == 0
    assert load_project(project.root).project.manifest.assistant.enabled is False


def test_only_the_model_is_sent_never_data(taxi):
    text = assistant.describe_model(taxi)
    assert "yellow_tripdata [raw, table, source nyc_tlc" in text and "VendorID (integer" in text
    assert "fct_trip" in text and "Relationships:" in text
    for secret in ("password", "SNOWFLAKE", "s3://"):
        assert secret not in text


def test_describe_fills_only_what_is_missing(taxi):
    trips = taxi.model.entity("yellow_tripdata")
    trips.attribute("VendorID").description = None
    kept = trips.attribute("tpep_pickup_datetime").description
    fake = Fake(
        {
            "descriptions": [
                {"entity": "yellow_tripdata", "column": "VendorID", "description": "Who sent it.", "reason": "name"},
                {"entity": "yellow_tripdata", "column": "tpep_pickup_datetime", "description": "X", "reason": "r"},
                {"entity": "nowhere", "column": None, "description": "X", "reason": "r"},
            ]
        }
    )
    suggestions = assistant.describe(taxi, fake, ["yellow_tripdata"])
    assert [(s.kind, s.column) for s in suggestions] == [("describe_column", "VendorID")]
    assert "- yellow_tripdata.VendorID" in fake.prompts[0]
    assert fake.schemas[0]["additionalProperties"] is False
    assistant.apply(taxi, suggestions)
    assert trips.attribute("VendorID").description == "Who sent it."
    assert trips.attribute("tpep_pickup_datetime").description == kept


def test_classify_marks_personal_data(taxi):
    fake = Fake(
        {
            "columns": [
                {
                    "entity": "yellow_tripdata",
                    "column": "VendorID",
                    "pii": True,
                    "classification": "confidential",
                    "reason": "identifies a vendor",
                }
            ]
        }
    )
    result = assistant.apply(taxi, assistant.classify(taxi, fake))
    column = taxi.model.entity("yellow_tripdata").attribute("VendorID")
    assert len(result.applied) == 1 and column.pii and column.classification.value == "confidential"
    assert assistant.classify(taxi, fake) == []  # already marked: left alone


def test_a_mapping_the_model_would_not_accept_is_refused(taxi):
    from murakumo import edit

    edit.add_column(taxi, "fct_trip", "tip_share", "decimal(10,4)")
    edit.add_column(taxi, "fct_trip", "made_up", "string")
    fake = Fake(
        {
            "mappings": [
                {
                    "column": "tip_share",
                    "sources": ["yellow_tripdata.trip_distance", "yellow_tripdata.total_amount"],
                    "expression": "yellow_tripdata.trip_distance / nullif(yellow_tripdata.total_amount, 0)",
                    "reason": "a share of the total",
                },
                {"column": "made_up", "sources": ["yellow_tripdata.no_such_column"], "expression": None, "reason": "?"},
                {"column": "trip_id", "sources": ["yellow_tripdata.VendorID"], "expression": None, "reason": "mapped"},
            ]
        }
    )
    suggestions = assistant.map_columns(taxi, fake, "fct_trip")
    assert [s.column for s in suggestions] == ["tip_share", "made_up"]  # trip_id is mapped already
    result = assistant.apply(taxi, suggestions)
    assert [s.column for s in result.applied] == ["tip_share"]
    assert "no_such_column" in result.refused[0].refused
    assert not [d for d in validate_project(taxi) if d.level.value == "error"]


def test_draft_adds_a_whole_table_or_none(taxi):
    table = {
        "name": "dim_vendor",
        "layer": "marts",
        "kind": "dimension",
        "domain": None,
        "description": "The companies that supply trip records.",
        "grain": [],
        "columns": [
            {
                "name": "vendor_id",
                "type": "integer",
                "primary_key": True,
                "description": "The vendor.",
                "sources": ["yellow_tripdata.VendorID"],
                "expression": None,
            },
            {
                "name": "bad",
                "type": "no_such_type",
                "primary_key": False,
                "description": "x",
                "sources": [],
                "expression": None,
            },
        ],
        "reason": "the fact has a vendor",
    }
    result = assistant.apply(taxi, assistant.draft(taxi, Fake({"tables": [table]}), "a vendor dimension"))
    assert result.refused and taxi.model.entity("dim_vendor") is None  # one bad column: no table
    table["columns"].pop()
    result = assistant.apply(taxi, assistant.draft(taxi, Fake({"tables": [table]}), "a vendor dimension"))
    vendor = taxi.model.entity("dim_vendor")
    assert result.applied and vendor.primary_key[0].name == "vendor_id" and vendor.mappings[0].sources
    assert assistant.draft(taxi, Fake({"tables": [table]}), "again") == []  # it exists now


def test_the_cli_shows_then_applies(taxi, monkeypatch):
    taxi.model.entity("yellow_tripdata").attribute("VendorID").description = None
    save_model(taxi)
    assert runner.invoke(app, ["ai", "enable", "-p", str(taxi.root)]).exit_code == 0
    answer = {
        "descriptions": [{"entity": "yellow_tripdata", "column": "VendorID", "description": "Who.", "reason": "r"}]
    }
    monkeypatch.setattr(assistant, "provider_for", lambda project: Fake(answer))
    shown = runner.invoke(app, ["ai", "describe", "-e", "yellow_tripdata", "-p", str(taxi.root)])
    assert shown.exit_code == 0 and "--apply" in shown.output
    assert load_project(taxi.root).project.model.entity("yellow_tripdata").attribute("VendorID").description is None
    applied = runner.invoke(app, ["ai", "describe", "-e", "yellow_tripdata", "--apply", "-p", str(taxi.root)])
    assert applied.exit_code == 0, applied.output
    assert load_project(taxi.root).project.model.entity("yellow_tripdata").attribute("VendorID").description == "Who."


# The MCP server --------------------------------------------------------------------------------


def call(root, tool, **arguments):
    answer = mcp.handle(
        root, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
    )
    content = answer["result"]["content"][0]["text"]
    return answer["result"]["isError"], (content if answer["result"]["isError"] else json.loads(content))


def test_mcp_speaks_the_protocol(taxi):
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "nonsense"},
    ]
    out = io.StringIO()
    mcp.serve(taxi.root, io.StringIO("\n".join(json.dumps(m) for m in lines) + "\nnot json\n"), out)
    answers = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [a["id"] for a in answers] == [1, 2, 3, None]  # the notification got no answer
    assert answers[0]["result"]["serverInfo"]["name"] == "murakumo" and "tools" in answers[0]["result"]["capabilities"]
    tools = {t["name"]: t for t in answers[1]["result"]["tools"]}
    assert {"model_summary", "get_entity", "validate", "set_mapping", "generate"} <= set(tools)
    assert all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools.values())
    assert answers[2]["error"]["code"] == -32601 and answers[3]["error"]["code"] == -32700


def test_mcp_reads_and_changes_the_model(taxi):
    root = taxi.root
    failed, summary = call(root, "model_summary")
    assert not failed and "fct_trip" in [e["name"] for e in summary["entities"]]
    failed, entity = call(root, "get_entity", name="fct_trip")
    assert not failed and entity["attributes"] and "id" not in entity
    failed, done = call(root, "add_column", entity="fct_trip", name="tip_share", type="decimal(10,4)")
    assert not failed and "added fct_trip.tip_share" in done["done"]
    failed, done = call(
        root,
        "set_mapping",
        entity="fct_trip",
        column="tip_share",
        sources=["yellow_tripdata.trip_distance"],
        expression="yellow_tripdata.trip_distance * 2",
    )
    assert not failed and done["errors"] == 0
    saved = load_project(root).project.model.entity("fct_trip")
    assert next(m for m in saved.mappings if m.target == "tip_share").expression.endswith("* 2")
    failed, text = call(root, "set_mapping", entity="fct_trip", column="tip_share", expression="((")
    assert failed and "expression" in text  # refused as the CLI refuses it
    failed, text = call(root, "get_entity", name="nope")
    assert failed and "does not exist" in text
    failed, text = call(root, "add_column", entity="fct_trip")
    assert failed and "cannot run add_column" in text
    failed, result = call(root, "generate", dry_run=True)
    assert not failed and result["written"] is False and result["files"] > 10
    failed, problems = call(root, "validate")
    assert not failed and problems["errors"] == 0
