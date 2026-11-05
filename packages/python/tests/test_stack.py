"""The stack (choices, checks, decisions), connections, the ingestion code for each tool, the
Snowflake platform settings, deploying, and the canvas's endpoints and jobs for them."""

import json
import py_compile

import pytest
import yaml
from typer.testing import CliRunner

from murakumo import deploy, stack
from murakumo.cli import app
from murakumo.generate import generate
from murakumo.project import load_project
from murakumo.scaffold import init_project
from murakumo.validate import validate_project

runner = CliRunner()

REST = {
    "kind": "rest",
    "base_url": "https://api.example.com/v1",
    "auth": {"type": "bearer", "env": "TLC_TOKEN"},
    "paging": {"type": "page", "size_param": "per_page", "size": 500},
    "endpoints": [{"table": "taxi_zone_lookup", "path": "/zones", "records": "data", "since_param": "since"}],
}


def reload(project):
    return load_project(project.root).project


@pytest.fixture
def project(tmp_path):
    project = init_project(tmp_path / "taxi")
    stack.set_connection(project, "nyc_tlc", REST, why="the TLC publishes an API")
    return reload(project)


def test_stack_shows_every_part_and_changes_record_why(project):
    assert stack.current(project) == {
        "warehouse": "duckdb",
        "extract": "python",
        "landing": "landing",
        "load": "build",
        "transform": "dbt",
        "python": "none",
        "orchestrator": "airflow",
        "ci": "github",
        "stream": "consumer",
        "iac": "none",
    }
    changed = stack.set_parts(
        project, {"warehouse": "snowflake", "load": "snowpipe", "landing": "s3://lake/taxi"}, "on Snowflake"
    )
    assert changed == ["warehouse: duckdb → snowflake", "load: build → snowpipe", "landing: landing → s3://lake/taxi"]
    project = reload(project)
    assert project.manifest.stack.load == "snowpipe" and project.manifest.targets.warehouse.type.value == "snowflake"
    assert "# Murakumo project manifest" in (project.root / "murakumo.yaml").read_text()  # comments kept
    log = stack.decisions(project.root)
    assert [d["what"] for d in log] == ["sources.nyc_tlc.connection", "stack.warehouse", "stack.load", "stack.landing"]
    assert log[-1] == {**log[-1], "from": "landing", "to": "s3://lake/taxi", "why": "on Snowflake"}
    with pytest.raises(stack.StackError, match="load is one of build, snowpipe, autoloader"):
        stack.set_parts(project, {"load": "kafka"})
    with pytest.raises(stack.StackError, match="no compute"):
        stack.set_parts(project, {"compute": "x"})


def test_choices_that_do_not_go_together(project):
    stack.set_parts(project, {"load": "snowpipe"})
    project = reload(project)
    problems = stack.check(project)
    assert ("error", "load", "Snowpipe loads into Snowflake; the warehouse is duckdb") in problems
    assert any(p[:2] == ("error", "landing") for p in problems)
    codes = {d.code for d in validate_project(project)}
    assert "MK701" in codes
    stack.set_parts(project, {"load": "build", "extract": "lambda"})
    assert any("lands files in S3" in m for _, _, m in stack.check(reload(project)))


def test_a_connection_to_a_table_not_in_the_model_is_a_warning(project):
    stack.set_connection(project, "nyc_tlc", {**REST, "endpoints": [{"table": "nowhere"}]})
    diagnostics = validate_project(reload(project))
    assert any(d.code == "MK702" and "nowhere" in d.message for d in diagnostics)


def test_connect_from_the_command_line(tmp_path):
    project = init_project(tmp_path / "taxi")
    result = runner.invoke(
        app,
        [
            "source", "connect", "nyc_tlc", "rest", "base_url=https://x.example/v1", "auth.type=api_key",
            "auth.env=KEY", "endpoints.0.table=taxi_zone_lookup", "endpoints.0.params.limit=50",
            "-p", str(project.root), "--why", "testing",
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    declaration = next(d for d in reload(project).manifest.sources if d.name == "nyc_tlc")
    assert declaration.connection.auth.type == "api_key" and declaration.connection.endpoints[0].params == {"limit": 50}
    result = runner.invoke(app, ["source", "connect", "nyc_tlc", "rest", "base_url=ftp://x", "-p", str(project.root)])
    assert result.exit_code == 1 and "https://" in result.output
    shown = runner.invoke(app, ["stack", "-p", str(project.root)])
    assert shown.exit_code == 0 and "Snowpipe" not in shown.output and "python" in shown.output
    decided = runner.invoke(
        app, ["decisions", "add", "Zones load daily", "--why", "they rarely change", "-p", str(project.root)]
    )
    assert decided.exit_code == 0
    listed = runner.invoke(app, ["decisions", "-p", str(project.root)])
    assert "Zones load daily" in listed.output and "they rarely change" in listed.output


def test_python_extract_code_and_orchestration(project):
    files = generate(project).files
    settings = json.loads(files["ingestion/python/sources/nyc_tlc.json"])
    assert settings["root"] == "../../../.." and settings["connection"]["auth"] == {
        "type": "bearer",
        "env": "TLC_TOKEN",
        "name": "Authorization",
        "location": "header",
    }
    assert "TLC_TOKEN" not in files["ingestion/python/extract.py"]
    assert "export TLC_TOKEN=..." in files["ingestion/README.md"]
    dag = next(c for p, c in files.items() if p.startswith("orchestration/airflow/dags/"))
    assert "extract_nyc_tlc >> load >>" in dag and "murakumo load --env" in dag
    assert "## Sources" in files["docs/stack.md"] and "TLC publishes an API" in files["docs/stack.md"]


def test_every_extract_and_load_tool_writes_code_that_parses(project, tmp_path):
    for orchestrator in ("dagster", "prefect"):
        stack.set_parts(project, {"orchestrator": orchestrator})
        files = generate(reload(project)).files
        for path, content in files.items():
            if path.endswith(".py") and path.startswith("orchestration/"):
                target = tmp_path / path.replace("/", "_")
                target.write_text(content)
                py_compile.compile(str(target), doraise=True)
        if orchestrator == "dagster":
            assert '"load": True' in files["orchestration/dagster/definitions.py"]
        else:
            assert 'fetch("nyc_tlc")' in files["orchestration/prefect/flows.py"]

    stack.set_parts(
        project, {"warehouse": "snowflake", "extract": "lambda", "load": "snowpipe", "landing": "s3://lake/taxi"}
    )
    files = generate(reload(project)).files
    template = yaml.safe_load(files["ingestion/lambda/template.yaml"].split("\n", 1)[1])
    assert template["Parameters"]["TlcToken"]["NoEcho"] is True
    assert template["Resources"]["ExtractNycTlc"]["Properties"]["Handler"] == "extract.handler"
    pipes = files["ingestion/snowpipe/prod.sql"]
    assert "create pipe if not exists TAXI_PROD.RAW_NYC_TLC.TAXI_ZONE_LOOKUP_PIPE auto_ingest = true" in pipes
    assert "METADATA$FILENAME" in pipes and "CREATE TABLE IF NOT EXISTS raw_nyc_tlc.taxi_zone_lookup" in pipes
    assert "storage_allowed_locations = ('s3://lake/taxi/')" in files["ingestion/snowpipe/integration.sql"]

    stack.set_parts(project, {"extract": "glue"})
    files = generate(reload(project)).files
    assert json.loads(files["ingestion/glue/jobs/nyc_tlc.json"])["Command"]["Name"] == "pythonshell"
    assert "aws glue create-job" in files["ingestion/glue/deploy.sh"]

    stack.set_parts(project, {"warehouse": "databricks", "load": "autoloader"})
    files = generate(reload(project)).files
    assert 'format("cloudFiles")' in files["ingestion/autoloader/nyc_tlc.py"]
    assert "file_arrival" in files["ingestion/autoloader/databricks.yml"]


def test_snowflake_platform_settings(tmp_path):
    project = init_project(tmp_path / "taxi")
    stack.set_parts(project, {"warehouse": "snowflake"})
    from murakumo.manifest_edit import write_section

    write_section(project.root, ("targets", "warehouse", "time_travel_days"), {"raw": 7, "marts": 30, "staging": 5})
    write_section(project.root, ("targets", "warehouse", "monthly_credits"), 20)
    project = reload(project)
    files = generate(project).files
    dbt_project = yaml.safe_load(files["dbt/dbt_project.yml"])
    layers = dbt_project["models"]["taxi"]
    assert layers["staging"]["+transient"] is True and layers["marts"]["+transient"] is False
    assert layers["marts"]["+post-hook"] == ["{{ murakumo_retention(30) }}"]
    assert dbt_project["snapshots"]["taxi"]["+transient"] is False
    assert "DATA_RETENTION_TIME_IN_DAYS = 7" in files["ddl/snowflake/raw.sql"]
    assert "murakumo_retention" in files["dbt/macros/murakumo/retention.sql"]
    from murakumo.environments import provision

    sql = provision(project)
    assert "create resource monitor if not exists TAXI_MONTHLY with credit_quota = 20" in sql
    assert "alter warehouse TRANSFORMING set resource_monitor = TAXI_MONTHLY;" in sql
    assert any(d.code == "MK703" and "staging" in d.message for d in validate_project(project))


def test_duckdb_projects_get_no_snowflake_settings(tmp_path):
    files = generate(init_project(tmp_path / "taxi")).files
    layers = yaml.safe_load(files["dbt/dbt_project.yml"])["models"]["taxi"]
    assert "+transient" not in layers["staging"] and "snapshots" not in yaml.safe_load(files["dbt/dbt_project.yml"])
    assert "ingestion/README.md" not in files  # no source has a connection


def test_deploy_plans_then_runs_the_warehouse_steps(project):
    stack.set_parts(project, {"warehouse": "snowflake", "load": "snowpipe", "landing": "s3://lake/taxi"})
    project = reload(project)
    steps = deploy.plan(project, "dev")
    assert [s.key for s in steps][:4] == ["provision", "integration", "pipes", "ci"]
    assert [s.kind for s in steps[:4]] == ["sql", "sql", "sql", "file"]
    with pytest.raises(deploy.DeployError, match="no environment 'qa'"):
        deploy.plan(project, "qa")

    ran: list[str] = []

    class Fake:
        raw = type("Raw", (), {"close": lambda self: None})()

        def execute(self, sql):
            ran.append(sql)

    done = deploy.apply(project, "dev", only=["pipes"], say=lambda m: None, connect=lambda *a, **k: Fake())
    assert next(s for s in done if s.key == "pipes").done
    assert any(sql.startswith("create pipe if not exists TAXI_DEV.RAW_NYC_TLC") for sql in ran)
    assert not any("--" in sql.splitlines()[0] for sql in ran)  # comments are not sent


def test_canvas_endpoints(project, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from murakumo.server import create_app

    client = TestClient(create_app(project.root), base_url="http://localhost")
    info = client.get("/api/stack").json()
    assert [p["key"] for p in info["parts"]][:2] == ["warehouse", "extract"] and info["connected"] == ["nyc_tlc"]
    changed = client.put("/api/stack", json={"values": {"orchestrator": "prefect"}, "why": "we run Prefect"}).json()
    assert changed["changed"] == ["orchestrator: airflow → prefect"]
    assert changed["decisions"][-1]["why"] == "we run Prefect"
    assert client.post("/api/undo").status_code == 200
    parts = {p["key"]: p["value"] for p in client.get("/api/stack").json()["parts"]}
    assert parts["orchestrator"] == "airflow"

    bad = client.put("/api/sources/nyc_tlc/connection", json={"connection": {"kind": "rest", "base_url": "x"}})
    assert bad.status_code == 400 and "https://" in bad.json()["message"]
    gone = client.put("/api/sources/nyc_tlc/connection", json={"connection": None}).json()
    assert gone["connected"] == []

    monkeypatch.setattr(
        "murakumo.extract.preview", lambda c, e: {"status": 200, "records": 1, "sample": [{"a": 1}], "milliseconds": 1}
    )
    tried = client.post("/api/sources/request", json={"connection": REST, "endpoint": {"table": "zones", "path": "/z"}})
    assert tried.status_code == 200 and tried.json()["records"] == 1

    made = client.post(
        "/api/sources/table-from-records",
        json={
            "source": "zones_api",
            "table": "zones",
            "records": [{"id": 1, "name": "Astoria"}, {"id": 2, "name": None}],
        },
    )
    assert made.status_code == 200
    zones = load_project(project.root).project.model.entity("zones")
    assert [(a.name, a.type) for a in zones.attributes] == [("id", "bigint"), ("name", "string")]

    plan = client.get("/api/stack/deploy?env=prod").json()["steps"]
    assert plan[0]["key"] == "ci" and plan[-1]["key"] == "orchestrator"
    assert client.post("/api/decisions", json={"what": "Zones are an API now"}).json()["decisions"][-1]["what"]


def test_jobs_for_fetching_loading_and_deploying(tmp_path):
    from murakumo.jobs import JobError, command

    assert command(tmp_path, "ingest", {"source": "nyc_tlc"}) == ("Fetch nyc_tlc", ["ingest", "--source", "nyc_tlc"])
    assert command(tmp_path, "load", {"environment": "prod"})[1] == ["load", "--env", "prod"]
    assert command(tmp_path, "build", {"ingest": True})[1] == ["build", "--ingest"]
    assert command(tmp_path, "deploy", {"environment": "dev", "steps": ["pipes"]})[1] == [
        "stack", "deploy", "--env", "dev", "--apply", "--step", "pipes",
    ]  # fmt: skip
    with pytest.raises(JobError):
        command(tmp_path, "deploy", {"environment": "dev", "steps": ["rm -rf"]})
    with pytest.raises(JobError):
        command(tmp_path, "ingest", {"source": "x; rm"})


def test_protobuf_messages_become_tables(tmp_path):
    from murakumo.importers.protobuf import import_proto

    proto = tmp_path / "orders.proto"
    proto.write_text(
        """syntax = "proto3";
import "google/protobuf/timestamp.proto";
// An order placed in the shop.
message Order {
  string order_id = 1; // The order's id.
  int64 customer_id = 2;
  google.protobuf.Timestamp placed_at = 3;
  repeated Line lines = 4;
  Status status = 5;
  map<string, string> labels = 6;
  repeated string tags = 7;
  oneof payment { string card_token = 8; string voucher = 9; }
  enum Status { UNKNOWN = 0; PLACED = 1; }
  message Line { string sku = 1; int32 quantity = 2; }
}
message Refund { string order_id = 1; double amount = 2 [deprecated = true]; }
service Shop { rpc Place(Order) returns (Refund); }
"""
    )
    imported = import_proto(str(proto), "shop")
    order, refund = imported.entities
    assert order.name == "order" and order.description == "An order placed in the shop."
    assert [(a.name, a.type) for a in order.attributes] == [
        ("order_id", "string"),
        ("customer_id", "bigint"),
        ("placed_at", "timestamp_tz"),
        ("lines", "json"),
        ("status", "string"),
        ("labels", "json"),
        ("tags", "array<string>"),
        ("card_token", "string"),
        ("voucher", "string"),
    ]
    assert order.attributes[0].description == "The order's id."
    assert [(a.name, a.type) for a in refund.attributes] == [("order_id", "string"), ("amount", "double")]
    assert [e.name for e in import_proto(str(proto), "shop", only=["Line"]).entities] == ["line"]
