"""`murakumo secret`: the project's secrets, kept in its .env (out of git) instead of exported."""

from __future__ import annotations

from pathlib import Path

import typer

from murakumo.cli_model import ProjectOption, open_project
from murakumo.console import console, fail

secret_app = typer.Typer(
    help="Secrets for the sources and the warehouse, kept in the project's .env: never committed, read by "
    "Murakumo itself. A variable set in the environment (by CI, say) wins over the file.",
    no_args_is_help=True,
)


@secret_app.command("set")
def set_command(
    name: str = typer.Argument(..., help="The variable, such as CRM_CLIENT_SECRET."),
    value: str | None = typer.Option(None, "--value", help="The value. Default: asked for, without showing it."),
    project_path: Path = ProjectOption,
) -> None:
    """Keep a secret in the project's .env (and add .env to .gitignore)."""
    from murakumo import secret_file

    project = open_project(project_path)
    if value is None:
        value = typer.prompt(name, hide_input=True)
    try:
        secret_file.save(project.root, name, value)
    except secret_file.SecretError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    console.print(f"Kept {name} in {secret_file.FILE}, which git ignores.")


@secret_app.command("list")
def list_command(project_path: Path = ProjectOption) -> None:
    """The secrets the connections read, and where each is set. Never their values."""
    from murakumo import secret_file

    project = open_project(project_path)
    names: list[str] = []
    for declaration in project.manifest.sources:
        if declaration.connection is None:
            continue
        data = declaration.connection.model_dump(exclude_none=True)
        for holder in (data, data.get("auth") or {}):
            names += [str(v) for k, v in holder.items() if k.endswith("_env") or k == "env"]
    names += [n for n in secret_file.read(project.root) if n not in names]
    if not names:
        console.print("No secrets yet. Connect a source on the canvas, or: murakumo secret set NAME")
        return
    for item in secret_file.status(project.root, names):
        where = {"file": "in .env", "environment": "in the environment"}.get(str(item["from"]), "[red]not set[/red]")
        console.print(f"  {item['name']}: {where}")


@secret_app.command("remove")
def remove_command(name: str = typer.Argument(...), project_path: Path = ProjectOption) -> None:
    """Take a secret out of the project's .env."""
    from murakumo import secret_file

    project = open_project(project_path)
    if secret_file.remove(project.root, name):
        console.print(f"Removed {name} from {secret_file.FILE}.")
    else:
        console.print(f"{name} is not in {secret_file.FILE}.")
