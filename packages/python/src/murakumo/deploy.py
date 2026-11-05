"""Building the platform for you: ``murakumo stack deploy``.

Deploying an environment is a list of steps, in order, for the stack chosen: create the warehouse's
databases and roles, let it read the landing bucket, create the pipes, write the CI workflow, and
deploy the extract functions or jobs. ``plan`` lists them, with the SQL or commands each runs;
nothing happens until ``apply``, and the canvas shows the same list before anything is run.

Murakumo runs the steps that run in the warehouse (over the project's connection, with the role
each step needs) and writes the files that belong in the repository. Steps in a cloud account (AWS
Lambda and Glue, Databricks bundles) are commands for you to run with your own credentials:
Murakumo never holds cloud keys.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from murakumo.manifest import Warehouse
from murakumo.project import Project


class DeployError(Exception):
    pass


@dataclass
class DeployStep:
    key: str
    title: str
    why: str
    kind: str  # sql: Murakumo runs it in the warehouse; file: Murakumo writes it; commands: you run them
    content: str = ""
    role: str | None = None  # sql: the role it runs as
    done: bool = False
    result: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "why": self.why,
            "kind": self.kind,
            "content": self.content,
            "role": self.role,
            "done": self.done,
            "result": self.result,
        }


def _admin_role() -> str | None:
    return os.environ.get("SNOWFLAKE_ADMIN_ROLE")


def plan(project: Project, environment: str) -> list[DeployStep]:
    from murakumo.environments import provision, role_for
    from murakumo.generate import generate
    from murakumo.generate.ingestion import snowpipe_sql, steps

    manifest = project.manifest
    if manifest.targets.environment(environment) is None:
        raise DeployError(f"no environment {environment!r}; the project has {', '.join(manifest.targets.names)}")
    stack = manifest.stack
    warehouse = manifest.targets.warehouse.type
    out: list[DeployStep] = []
    if warehouse is Warehouse.SNOWFLAKE:
        out.append(
            DeployStep(
                "provision",
                "Create the databases, warehouse and roles",
                "A database per environment, one for everyone's own schemas, a role to deploy each environment"
                " and one for developers; the spending cap if monthly_credits is set. Safe to run again.",
                "sql",
                provision(project),
                role=_admin_role() or "your default role (set SNOWFLAKE_ADMIN_ROLE to use another)",
            )
        )
    from murakumo.generate import terraform

    for module in terraform.modules(project):
        what = {
            "snowflake": "The same databases, warehouse and roles as the provision step, as Terraform: run one or"
            " the other.",
            "databricks": "A catalog per environment, the groups and their grants, and a serverless SQL warehouse.",
            "aws": "The landing bucket, and what fetches into it or loads from it. Add -var endpoint=... to try it"
            " on LocalStack.",
        }[module]
        folder = f"generated/{terraform.FOLDER}/{module}"
        out.append(
            DeployStep(
                f"terraform-{module}",
                f"Apply the {module} Terraform module",
                what + " It runs with your own credentials, from the environment.",
                "commands",
                f"terraform -chdir={folder} init\nterraform -chdir={folder} apply",
            )
        )
    output = generate(project)
    if stack.load == "snowpipe" and warehouse is Warehouse.SNOWFLAKE:
        out.append(
            DeployStep(
                "integration",
                "Let Snowflake read the landing bucket",
                "A storage integration (ACCOUNTADMIN). Afterwards, put the ARN and external id it describes into"
                " the AWS role's trust policy.",
                "sql",
                output.files.get("ingestion/snowpipe/integration.sql", ""),
                role=_admin_role() or "your default role (ACCOUNTADMIN is needed)",
            )
        )
        out.append(
            DeployStep(
                "pipes",
                f"Create the stage and pipes in {environment}",
                "One pipe per raw table, loading each file as it lands. Then point the bucket's event"
                " notification at the queue `show pipes` reports.",
                "sql",
                snowpipe_sql(project, output.plan, environment),
                role=role_for(environment) or f"SNOWFLAKE_ROLE_{environment.upper()} is not set: your default role",
            )
        )
    if warehouse is Warehouse.SNOWFLAKE:
        from murakumo.generate.ingestion import _environment_databases
        from murakumo.masking import policy_sql

        database = dict(_environment_databases(project)).get(environment)
        policies = policy_sql(project, database)
        if policies:
            out.append(
                DeployStep(
                    "masking",
                    f"Create the masking policies in {environment}",
                    "What roles outside governance.unmasked_roles see in personal and classified columns. dbt sets"
                    " them on the marts after each build; this creates them first, and sets them on marts that exist.",
                    "sql",
                    f"use database {database};\n" + policies,
                    role=role_for(environment) or f"SNOWFLAKE_ROLE_{environment.upper()} is not set: your default role",
                )
            )
    if stack.ci != "none":
        from murakumo import ci

        try:
            path, content = ci.render(project, stack.ci, deploy=True, preview=ci.preview_possible(project))
        except ci.CIError as exc:
            raise DeployError(str(exc)) from None
        out.append(
            DeployStep(
                "ci",
                f"Write the {'GitHub Actions workflow' if stack.ci == 'github' else 'GitLab pipeline'}",
                "Checks each pull request, and deploys each environment when its branch changes. Commit it, and"
                " add the secrets it names to the repository.",
                "file",
                content,
                extra={"provider": stack.ci, "path": str(path)},
            )
        )
    for step in steps(project, output.plan):
        if step.sql is not None:
            continue  # the warehouse steps above
        out.append(
            DeployStep(
                step.key,
                step.title,
                step.why,
                "commands",
                "\n".join(step.commands),
            )
        )
    orchestrator = manifest.generation.orchestrator.value
    out.append(
        DeployStep(
            "orchestrator",
            f"Deploy the {orchestrator} pipelines",
            f"generated/orchestration/{orchestrator}/README.md says how; the pipelines fetch, load and build on each"
            " schedule.",
            "commands",
            f"cat generated/orchestration/{orchestrator}/README.md",
        )
    )
    return out


def _statements(sql: str) -> list[str]:
    """The statements in a script, without its comments."""
    statements, current = [], []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).rstrip().rstrip(";"))
            current = []
    if current:
        statements.append("\n".join(current))
    return statements


def apply(
    project: Project,
    environment: str,
    only: list[str] | None = None,
    say: Callable[[str], None] = print,
    connect: Callable[..., Any] | None = None,
) -> list[DeployStep]:
    """Run the steps Murakumo can run (``only`` these keys, if given), in order; stop at the first failure."""
    from murakumo import ci
    from murakumo.environments import EnvError
    from murakumo.environments import connect as connect_to

    connect = connect or connect_to
    steps = plan(project, environment)
    for step in steps:
        if only and step.key not in only:
            continue
        if step.kind == "sql":
            say(f"{step.title} ...")
            try:
                if step.key == "pipes":
                    connection = connect(project, environment=environment)
                else:
                    connection = connect(project, database="SNOWFLAKE")
                    if _admin_role():
                        connection.execute(f"use role {_admin_role()}")
            except EnvError as exc:
                step.result = str(exc)
                say(f"  failed: {exc}")
                return steps
            try:
                statements = _statements(step.content)
                for statement in statements:
                    connection.execute(statement)
                step.done, step.result = True, f"{len(statements)} statement(s) run"
                say(f"  done: {step.result}")
            except Exception as exc:  # noqa: BLE001 - the warehouse's error, said plainly
                step.result = " ".join(str(exc).split())
                say(f"  failed: {step.result}")
                return steps
            finally:
                close = getattr(connection.raw, "close", None)
                if close:
                    close()
        elif step.kind == "file" and step.key == "ci":
            try:
                preview = ci.preview_possible(project)
                written = ci.write(project, step.extra["provider"], True, force=False, preview=preview)
            except ci.CIError as exc:
                step.result = str(exc)
                say(f"  {step.title}: {exc}")
                continue
            step.done, step.result = True, f"{written['file']} written"
            say(f"{step.title}: {step.result}")
        else:
            say(f"{step.title}: run these yourself, with your own credentials:\n{step.content}")
    return steps


__all__ = ["DeployError", "DeployStep", "apply", "plan"]
