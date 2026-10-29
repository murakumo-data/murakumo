"""CI: the checks that keep a model change and its code in step, run on every push.

``murakumo ci`` writes a workflow for GitHub Actions (``.github/workflows/murakumo.yml``) or GitLab
(``.gitlab-ci.yml``) at the root of the git repository. It runs, in the project's folder:

    murakumo validate --strict   the model has no errors or warnings
    murakumo generate --check    generated/ matches the model
    murakumo migrate --check     every table change has its migration
    murakumo verify              the dbt project builds and its tests pass, on DuckDB with made-up rows

With ``deploy``, a second job builds the production environment on pushes to main, after a
person approves it (a GitHub environment, or a manual GitLab job), with the warehouse's
credentials from the CI's secrets. The file is the user's once written: Murakumo never overwrites
a workflow that differs from what it would write, unless told to, and shows the difference.

The same checks can run on this machine (``run_checks``), as the canvas's CI panel does.
"""

from __future__ import annotations

import difflib
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from murakumo import __version__
from murakumo.manifest import Warehouse
from murakumo.project import Project

PROVIDERS = {"github": ".github/workflows/murakumo.yml", "gitlab": ".gitlab-ci.yml"}
HEADER = (
    "# Written by `murakumo ci`. It is yours to change: Murakumo will not overwrite it without asking.\n"
    "# Checks that the model, its generated code and its migrations agree, and that the dbt project builds.\n"
)
CHECKS = [
    ("Validate the model", ["validate", "--strict"]),
    ("Generated code is up to date", ["generate", "--check"]),
    ("Migrations are up to date", ["migrate", "--check"]),
    ("Build and test on DuckDB", ["verify"]),
]
_EXTRAS = {Warehouse.SNOWFLAKE: "snowflake", Warehouse.POSTGRES: "postgres", Warehouse.DUCKDB: "duckdb"}


class CIError(Exception):
    pass


class _Dumper(yaml.SafeDumper):
    """Indented lists, and multi-line commands as | blocks, as CI files are usually written."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def _text(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


_Dumper.add_representer(str, _text)


def _yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, width=120)


def repository_root(project: Project) -> Path:
    """The git repository the project is in (the project folder itself if it is not in one)."""
    for folder in [project.root, *project.root.parents]:
        if (folder / ".git").exists():
            return folder
    return project.root


def _install(extras: list[str]) -> str:
    version = __version__ if __version__ != "0.0.0" else None
    package = f"murakumo[{','.join(sorted(set(extras)))}]" + (f"=={version}" if version else "")
    return f'uv tool install "{package}"'


def _deploy_parts(project: Project) -> tuple[list[str], dict[str, str], bool] | None:
    """Commands, secret variables, and whether a key file is needed, for the production build.

    None where a production build in CI makes no sense.
    """
    from murakumo.generate.targets import target_for
    from murakumo.guide import profile_variables

    warehouse = project.manifest.targets.warehouse.type
    if warehouse is Warehouse.DUCKDB:
        return None  # a DuckDB file built in CI disappears with the runner
    required, _ = profile_variables(project, warehouse)
    environments = project.manifest.targets.environments or ["dev"]
    target = "prod" if "prod" in environments else environments[-1]
    secrets = {name: name for name in required if name != "SNOWFLAKE_PRIVATE_KEY_PATH"}
    key_file = warehouse is Warehouse.SNOWFLAKE
    if key_file:  # the key's contents are a secret; the job writes them to a file only it can read
        secrets.setdefault("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_ACCOUNT")
        secrets.setdefault("SNOWFLAKE_USER", "SNOWFLAKE_USER")
        secrets["SNOWFLAKE_PRIVATE_KEY"] = "SNOWFLAKE_PRIVATE_KEY"
    if warehouse is Warehouse.SNOWFLAKE:
        commands = [_install([_EXTRAS[warehouse], "verify"]) + " --with " + target_for(warehouse).adapter]
        commands.append(f"murakumo build --env {target}")
    else:
        adapter = target_for(warehouse).adapter
        commands = [
            f"pip install {adapter}",
            f"dbt build --project-dir generated/dbt --profiles-dir generated/dbt --target {target}",
        ]
    return commands, secrets, key_file


_GITHUB_KEY = (
    'umask 077 && printf "%s" "$SNOWFLAKE_PRIVATE_KEY" > "$RUNNER_TEMP/snowflake_key.p8"\n'
    'echo "SNOWFLAKE_PRIVATE_KEY_PATH=$RUNNER_TEMP/snowflake_key.p8" >> "$GITHUB_ENV"'
)
_GITLAB_KEY = [
    'umask 077 && printf "%s" "$SNOWFLAKE_PRIVATE_KEY" > "$CI_PROJECT_DIR/snowflake_key.p8"',
    'export SNOWFLAKE_PRIVATE_KEY_PATH="$CI_PROJECT_DIR/snowflake_key.p8"',
]


def preview_possible(project: Project) -> bool:
    """Pull-request previews clone production, which needs a shared warehouse that clones: Snowflake."""
    return project.manifest.targets.warehouse.type is Warehouse.SNOWFLAKE


def _preview_commands(prefix: str, target: str) -> list[str]:
    return [
        f"murakumo env clone --prefix {prefix}",
        f"murakumo migrate --apply --prefix {prefix}",
        f"murakumo build --env {target} --prefix {prefix}",
    ]


def _target(project: Project) -> str:
    environments = project.manifest.targets.environments or ["dev"]
    return "prod" if "prod" in environments else environments[-1]


def github_workflow(project: Project, deploy: bool, preview: bool = False) -> str:
    root = repository_root(project)
    folder = project.root.relative_to(root).as_posix() if project.root != root else "."
    check_steps: list[dict[str, Any]] = [
        {"uses": "actions/checkout@v4", "with": {"fetch-depth": 0}},  # the whole history, to compare with main
        {"uses": "astral-sh/setup-uv@v6"},
        {"name": "Install Murakumo", "run": _install(["duckdb", "verify"])},
        {
            "name": "Model changes and their impact",
            "if": "github.event_name == 'pull_request'",
            "run": 'murakumo diff "origin/${{ github.base_ref }}" --format markdown >> "$GITHUB_STEP_SUMMARY"',
        },
    ]
    check_steps += [{"name": name, "run": "murakumo " + " ".join(args)} for name, args in CHECKS]
    preview = preview and preview_possible(project)
    jobs: dict[str, Any] = {
        "check": {
            "name": "Model, code and migrations agree",
            **({"if": "github.event.action != 'closed'"} if preview else {}),
            "runs-on": "ubuntu-latest",
            "defaults": {"run": {"working-directory": folder}},
            "steps": check_steps,
        }
    }
    parts = _deploy_parts(project) if (deploy or preview) else None

    def warehouse_job(name: str, condition: str, commands: list[str], **extra: Any) -> dict[str, Any]:
        install, secrets, key_file = parts
        steps: list[dict[str, Any]] = [{"uses": "actions/checkout@v4"}, {"uses": "astral-sh/setup-uv@v6"}]
        if key_file:
            steps.append({"name": "Key file", "run": _GITHUB_KEY})
        steps.append({"name": "Install", "run": install[0]})
        steps.append({"name": name, "run": "\n".join(commands)})
        return {
            "name": name,
            "if": condition,
            "runs-on": "ubuntu-latest",
            **extra,
            "defaults": {"run": {"working-directory": folder}},
            "env": {name: "${{ secrets." + secret + " }}" for name, secret in sorted(secrets.items())},
            "steps": steps,
        }

    if parts and deploy:
        jobs["deploy"] = warehouse_job(
            "Build production",
            "github.event_name == 'push' && github.ref == 'refs/heads/main'",
            parts[0][1:],
            needs="check",
            # Add required reviewers to this environment (Settings → Environments) to approve each deploy.
            environment="production",
        )
    if parts and preview:
        prefix = "pr_${{ github.event.number }}_"
        jobs["preview"] = warehouse_job(
            "Preview the pull request on a clone of production",
            "github.event_name == 'pull_request' && github.event.action != 'closed'",
            _preview_commands(prefix, _target(project)),
            needs="check",
            concurrency={"group": "preview-${{ github.event.number }}", "cancel-in-progress": True},
        )
        jobs["preview-cleanup"] = warehouse_job(
            "Drop the pull request's schemas",
            "github.event_name == 'pull_request' && github.event.action == 'closed'",
            [f"murakumo env drop --prefix {prefix} --yes"],
        )
    pull_request = {"types": ["opened", "synchronize", "reopened", "closed"]} if preview else None
    workflow = {
        "name": "murakumo",
        "on": {"pull_request": pull_request, "push": {"branches": ["main"]}, "workflow_dispatch": None},
        "jobs": jobs,
    }
    text = _yaml(workflow)
    return HEADER + text.replace("'on':", "on:").replace(": null", ":")


def gitlab_pipeline(project: Project, deploy: bool, preview: bool = False) -> str:
    root = repository_root(project)
    folder = project.root.relative_to(root).as_posix() if project.root != root else "."
    before = ["pip install uv", "export PATH=$HOME/.local/bin:$PATH", f"cd {folder}"]
    preview = preview and preview_possible(project)
    stages = ["check", *(["preview"] if preview else []), *(["deploy"] if deploy else [])]
    pipeline: dict[str, Any] = {
        "stages": stages,
        "murakumo-check": {
            "stage": "check",
            "image": "python:3.12",
            "before_script": [*before, _install(["duckdb", "verify"])],
            "variables": {"GIT_DEPTH": "0"},
            "script": [
                'if [ -n "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" ]; then '
                'git fetch origin "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME" && '
                'murakumo diff "origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"; fi',
                *("murakumo " + " ".join(args) for _, args in CHECKS),
            ],
            "rules": [
                {"if": "$CI_PIPELINE_SOURCE == 'merge_request_event'"},
                {"if": "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH"},
            ],
        },
    }
    parts = _deploy_parts(project) if (deploy or preview) else None
    if parts:
        install, _secrets, key_file = parts
        # The warehouse credentials come from CI/CD variables (Settings → CI/CD → Variables).
        setup = [*before, *(_GITLAB_KEY if key_file else []), install[0]]
        if deploy:
            pipeline["murakumo-deploy"] = {
                "stage": "deploy",
                "image": "python:3.12",
                "before_script": setup,
                "script": install[1:],
                "rules": [{"if": "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH", "when": "manual"}],
                "environment": {"name": "production"},
            }
        if preview:
            prefix = "pr_${CI_MERGE_REQUEST_IID}_"
            review = {
                "name": "review/$CI_MERGE_REQUEST_IID",
                "on_stop": "murakumo-preview-stop",
                "auto_stop_in": "1 week",
            }
            pipeline["murakumo-preview"] = {
                "stage": "preview",
                "image": "python:3.12",
                "before_script": setup,
                "script": _preview_commands(prefix, _target(project)),
                "rules": [{"if": "$CI_PIPELINE_SOURCE == 'merge_request_event'"}],
                "environment": review,
            }
            pipeline["murakumo-preview-stop"] = {
                "stage": "preview",
                "image": "python:3.12",
                "before_script": setup,
                "script": [f"murakumo env drop --prefix {prefix} --yes"],
                "rules": [{"if": "$CI_PIPELINE_SOURCE == 'merge_request_event'", "when": "manual"}],
                "environment": {"name": "review/$CI_MERGE_REQUEST_IID", "action": "stop"},
            }
    return HEADER + _yaml(pipeline)


def render(project: Project, provider: str, deploy: bool, preview: bool = False) -> tuple[Path, str]:
    if provider not in PROVIDERS:
        raise CIError(f"unknown provider {provider!r}; use github or gitlab")
    render_one = github_workflow if provider == "github" else gitlab_pipeline
    content = render_one(project, deploy, preview)
    return repository_root(project) / PROVIDERS[provider], content


def status(project: Project, provider: str, deploy: bool, preview: bool = False) -> dict[str, Any]:
    """Whether the CI file is there, as Murakumo would write it, or changed; with the diff."""
    path, content = render(project, provider, deploy, preview)
    root = repository_root(project)
    existing = path.read_text(encoding="utf-8") if path.is_file() else None
    state = "missing" if existing is None else "current" if existing == content else "differs"
    diff = ""
    if existing is not None and state == "differs":
        diff = "".join(
            difflib.unified_diff(
                existing.splitlines(keepends=True),
                content.splitlines(keepends=True),
                f"{PROVIDERS[provider]} (now)",
                f"{PROVIDERS[provider]} (Murakumo's)",
            )
        )
    found = {name: (root / file).is_file() for name, file in PROVIDERS.items()}
    return {
        "provider": provider,
        "file": PROVIDERS[provider],
        "path": str(path),
        "repository": str(root),
        "in_git": (root / ".git").exists(),
        "state": state,
        "content": content,
        "diff": diff,
        "found": found,
        "deploy_possible": _deploy_parts(project) is not None,
        "preview_possible": preview_possible(project),
    }


def write(project: Project, provider: str, deploy: bool, force: bool = False, preview: bool = False) -> dict[str, Any]:
    info = status(project, provider, deploy, preview)
    if info["state"] == "differs" and not force:
        raise CIError(f"{info['file']} has changes that are not Murakumo's; see the difference, then replace it")
    if info["state"] != "current":
        path = Path(info["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(info["content"], encoding="utf-8")
    return status(project, provider, deploy, preview)


# Running the checks here ---------------------------------------------------------------------


@dataclass
class Step:
    name: str
    command: str
    state: str = "waiting"  # waiting | running | passed | failed | skipped
    output: str = ""


@dataclass
class Run:
    steps: list[Step] = field(default_factory=list)
    running: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"running": self.running, "steps": [s.__dict__ for s in self.steps]}


def _command(args: list[str]) -> list[str]:
    return [sys.executable, "-m", "murakumo", *args]


def run_checks(root: Path, run: Run) -> Run:
    """Run the CI checks one after another, as the CI would, recording each as it finishes."""
    run.steps = [Step(name, "murakumo " + " ".join(args)) for name, args in CHECKS]
    run.running = True
    try:
        for step, (_, args) in zip(run.steps, CHECKS, strict=True):
            step.state = "running"
            try:
                done = subprocess.run(  # from the project's folder, as the CI runs them
                    _command(args),
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=900,
                    env={**os.environ, "NO_COLOR": "1", "COLUMNS": "100"},
                )
                step.output = (done.stdout + done.stderr).strip()[-4000:]
                step.state = "passed" if done.returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                step.state, step.output = "failed", "timed out after 15 minutes"
    finally:
        run.running = False
    return run


class Runner:
    """One background run of the checks at a time, for the canvas."""

    def __init__(self, root: Path):
        self.root = root
        self.run = Run()
        self.lock = threading.Lock()

    def start(self) -> Run:
        with self.lock:
            if self.run.running:
                return self.run
            self.run = Run(steps=[Step(name, "murakumo " + " ".join(args)) for name, args in CHECKS], running=True)
            threading.Thread(target=run_checks, args=(self.root, self.run), daemon=True).start()
            return self.run


__all__ = ["CHECKS", "CIError", "PROVIDERS", "Runner", "render", "repository_root", "run_checks", "status", "write"]
