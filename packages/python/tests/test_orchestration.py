import ast
import json
import shutil
import subprocess

import pytest
import yaml

from murakumo import schedules
from murakumo.generate import generate
from murakumo.manifest import Orchestrator
from murakumo.scaffold import init_project


@pytest.fixture
def project(tmp_path):
    project = init_project(tmp_path / "taxi")
    schedules.add_schedule(project, "zones", "0 * * * *", ["+dim_taxi_zone"])
    schedules.add_schedule(project, "nightly", "0 6 * * 1-5")
    return project


def files_for(project, orchestrator):
    project.manifest.generation.orchestrator = orchestrator
    folder = f"orchestration/{orchestrator.value}/"
    return {k.removeprefix(folder): v for k, v in generate(project).files.items() if k.startswith(folder)}


@pytest.mark.parametrize("orchestrator", list(Orchestrator))
def test_every_orchestrator_gets_files_that_parse(project, orchestrator):
    files = files_for(project, orchestrator)
    assert "README.md" in files and "`zones`" in files["README.md"]
    for path, content in files.items():
        if path.endswith(".py"):
            ast.parse(content)
        elif path.endswith((".yml", ".yaml")):
            yaml.safe_load(content)
        elif path.endswith(".sh"):
            assert subprocess.run(["bash", "-n"], input=content, text=True).returncode == 0


def test_only_the_chosen_orchestrator_is_generated(project):
    paths = [p for p in generate(project).files if p.startswith("orchestration/")]
    assert paths and all(p.startswith("orchestration/airflow/") for p in paths)


def test_airflow(project):
    files = files_for(project, Orchestrator.AIRFLOW)
    dag = files["dags/taxi__zones.py"]
    assert 'schedule="0 * * * *"' in dag and 'selector="zones"' in dag
    assert "parents[3]" in dag  # dags/ -> airflow/ -> orchestration/ -> generated/
    assert "astronomer-cosmos" in files["requirements.txt"]


def test_dagster(project):
    code = files_for(project, Orchestrator.DAGSTER)["definitions.py"]
    assert 'job_name="zones"' in code and 'dbt_select="fqn:dim_taxi_zone fqn:stg_nyc_tlc__taxi_zone_lookup"' in code
    assert 'cron_schedule="0 6 * * 1-5"' in code


def test_prefect(project):
    files = files_for(project, Orchestrator.PREFECT)
    assert 'dbt(["build", "--selector", "zones"])' in files["flows.py"]
    deployment = yaml.safe_load(files["prefect.yaml"])["deployments"][1]
    assert deployment["schedules"] == [{"cron": "0 6 * * 1-5", "timezone": "UTC"}]


def test_databricks_uses_quartz(project):
    bundle = yaml.safe_load(files_for(project, Orchestrator.DATABRICKS_WORKFLOWS)["databricks.yml"])
    job = bundle["resources"]["jobs"]["taxi_nightly"]
    assert job["schedule"]["quartz_cron_expression"] == "0 0 6 ? * 2-6"
    assert "dbt build --selector nightly --target prod" in job["tasks"][0]["dbt_task"]["commands"]


def test_snowflake_tasks(project):
    sql = files_for(project, Orchestrator.SNOWFLAKE_TASKS)["tasks.sql"]
    assert "schedule = 'USING CRON 0 * * * * UTC'" in sql
    assert "args = 'build --selector zones --target prod'" in sql


CLOUD = [
    Orchestrator.AWS_GLUE,
    Orchestrator.AWS_STEP_FUNCTIONS,
    Orchestrator.GCP_WORKFLOWS,
    Orchestrator.AZURE_DATA_FACTORY,
]


@pytest.mark.parametrize("orchestrator", CLOUD)
def test_cloud_orchestrators_get_a_script_and_definitions(project, orchestrator, tmp_path):
    files = files_for(project, orchestrator)
    assert "dbt build --selector nightly" in files["run.sh"] and "zones)" in files["run.sh"]
    assert "does not generate" not in files["README.md"]
    for path, content in files.items():
        if path.endswith(".json"):
            json.loads(content.replace("${subnets}", "[]").replace("${security_groups}", "[]"))
    if "main.tf" in files and shutil.which("terraform"):  # the HCL is well formed
        for path, content in files.items():
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text(content)
        done = subprocess.run(
            ["terraform", "fmt", "-check", "-diff", "main.tf"], cwd=tmp_path, capture_output=True, text=True
        )
        assert "Error" not in done.stderr, done.stderr


def test_aws_schedules(project):
    files = files_for(project, Orchestrator.AWS_STEP_FUNCTIONS)
    assert 'schedule_expression          = "cron(0 6 ? * 2-6 *)"' in files["main.tf"]
    assert 'jsonencode({ schedule = "nightly" })' in files["main.tf"]
    assert "States.Array($.schedule)" in files["state_machine.asl.json"]
    assert 'schedule      = "cron(0 * * * ? *)"' in files_for(project, Orchestrator.AWS_GLUE)["main.tf"]


def test_gcp_workflow_runs_the_job_per_schedule(project):
    files = files_for(project, Orchestrator.GCP_WORKFLOWS)
    assert (
        'schedule    = "0 6 * * 1-5"' in files["main.tf"]
        and "googleapis.run.v1.namespaces.jobs.run" in files["workflow.yaml"]
    )


def test_data_factory_triggers(project):
    files = files_for(project, Orchestrator.AZURE_DATA_FACTORY)
    nightly = json.loads(files["triggers/taxi_nightly.json"])["properties"]
    recurrence = nightly["typeProperties"]["recurrence"]
    assert recurrence["frequency"] == "Week" and recurrence["schedule"] == {
        "minutes": [0],
        "hours": [6],
        "weekDays": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    }
    hourly = json.loads(files["triggers/taxi_zones.json"])["properties"]["typeProperties"]["recurrence"]
    assert hourly["frequency"] == "Hour" and hourly["schedule"] == {"minutes": [0]}
    assert nightly["pipelines"][0]["parameters"]["schedule"] == "nightly"
