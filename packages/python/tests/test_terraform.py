"""The platform as Terraform: a module per cloud, formatted as `terraform fmt` would, and (when
MURAKUMO_TERRAFORM_VALIDATE is set, since it downloads the providers) valid."""

import os
import shutil
import subprocess

import pytest

from murakumo import environments, stack
from murakumo.generate import generate, terraform
from murakumo.project import load_project
from murakumo.scaffold import init_project

REST = {
    "kind": "rest",
    "base_url": "https://api.example.com/v1",
    "auth": {"type": "bearer", "env": "TLC_TOKEN"},
    "endpoints": [{"table": "taxi_zone_lookup", "path": "/zones", "records": "data"}],
}
HAS_TERRAFORM = shutil.which("terraform") is not None


def reload(project):
    return load_project(project.root).project


@pytest.fixture
def project(tmp_path):
    project = init_project(tmp_path / "taxi")
    stack.set_connection(project, "nyc_tlc", REST, why="the TLC publishes an API")
    return reload(project)


def write(files, root):
    for path, content in files.items():
        if path.startswith("terraform/") or path.startswith("ingestion/"):
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
    return root / "terraform"


def check(folder):
    if not HAS_TERRAFORM:
        return
    result = subprocess.run(["terraform", "fmt", "-recursive", "-check", "-diff", str(folder)], capture_output=True)
    assert result.returncode == 0, result.stdout.decode()
    if os.environ.get("MURAKUMO_TERRAFORM_VALIDATE"):
        for module in sorted(p for p in folder.iterdir() if p.is_dir()):
            for command in (["init", "-backend=false", "-input=false"], ["validate"]):
                run = subprocess.run(["terraform", f"-chdir={module}", *command], capture_output=True)
                assert run.returncode == 0, run.stdout.decode() + run.stderr.decode()


def test_nothing_unless_chosen(project):
    assert not any(p.startswith("terraform/") for p in generate(project).files)


def test_snowflake_lambda_and_snowpipe(project, tmp_path):
    stack.set_parts(
        project,
        {
            "warehouse": "snowflake",
            "extract": "lambda",
            "load": "snowpipe",
            "landing": "s3://lake/taxi",
            "iac": "terraform",
        },
    )
    files = generate(reload(project)).files
    main = files["terraform/snowflake/main.tf"]
    # access roles hold the privileges; functional roles are made of them
    assert (
        'name     = "${each.key}_READ"' in main and 'resource "snowflake_grant_account_role" "deployer_writes"' in main
    )
    assert "SNOWFLAKE_PRIVATE_KEY" in main and "private_key" not in main.replace("SNOWFLAKE_PRIVATE_KEY", "")
    aws = files["terraform/aws/main.tf"]
    assert 'resource "aws_lambda_function" "extract"' in aws
    assert 'lookup(var.secrets, "TLC_TOKEN", "")' in aws
    assert 'resource "aws_iam_role" "snowflake"' in aws and "sts:ExternalId" in aws
    assert 'default     = "lake"' in files["terraform/aws/variables.tf"]
    assert "-var endpoint=http://localhost:4566" in files["terraform/README.md"]
    check(write(files, tmp_path / "out"))


def test_glue(project, tmp_path):
    stack.set_parts(project, {"extract": "glue", "landing": "s3://lake/taxi", "iac": "terraform"})
    files = generate(reload(project)).files
    assert 'resource "aws_glue_job" "extract"' in files["terraform/aws/main.tf"]
    assert "terraform/snowflake/main.tf" not in files
    check(write(files, tmp_path / "out"))


def test_databricks(project, tmp_path):
    stack.set_parts(project, {"warehouse": "databricks", "load": "autoloader", "iac": "terraform"})
    files = generate(reload(project)).files
    main = files["terraform/databricks/main.tf"]
    assert 'resource "databricks_catalog" "environment"' in main and '"ALL_PRIVILEGES"' in main
    assert "enable_serverless_compute = true" in main
    check(write(files, tmp_path / "out"))


def test_provisioning_sql_uses_the_same_roles(project):
    stack.set_parts(project, {"warehouse": "snowflake"})
    project = reload(project)
    sql = environments.provision(project)
    roles = terraform.roles(project)
    database = roles["deployers"]["prod"][1]
    assert f"create role if not exists {database}_READ;" in sql
    assert f"grant role {database}_WRITE to role {roles['deployers']['prod'][0]};" in sql
    assert f"grant role {database}_READ to role {roles['developer']};" in sql
