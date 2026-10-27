"""The `murakumo` command-line interface."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

import typer

from murakumo import __version__
from murakumo.console import console, err_console, print_diagnostics
from murakumo.diagnostics import Level
from murakumo.ir.model import Entity, Model, Relationship, SourceSystem
from murakumo.manifest import Manifest
from murakumo.project import find_root, load_project, save_model
from murakumo.scaffold import ProjectExistsError, init_project
from murakumo.validate import validate_project

app = typer.Typer(
    name="murakumo",
    help="Model-first data engineering: design the logical model, generate the platform.",
    no_args_is_help=True,
)


def _show_version(value: bool) -> None:
    if value:
        console.print(f"murakumo {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", help="Show the version and exit.", callback=_show_version, is_eager=True
    ),
) -> None:
    """Murakumo command-line interface."""


@app.command()
def info() -> None:
    """Show what this installation of Murakumo can do."""
    console.print(f"[bold]murakumo[/bold] {__version__}")
    console.print("Model-first data engineering. Commands arrive milestone by milestone.")


@app.command()
def init(
    path: Path = typer.Argument(Path("."), help="Folder to create the project in."),
    name: str | None = typer.Option(None, help="Project name. Defaults to the folder name."),
    empty: bool = typer.Option(False, "--empty", help="Start with an empty model instead of the NYC taxi example."),
) -> None:
    """Create a new project: manifest, model folder and an example model."""
    try:
        project = init_project(path, name=name, example=not empty)
    except ProjectExistsError as exc:
        err_console.print(f"[red]error[/red] {exc}")
        raise typer.Exit(1) from None

    model = project.model
    console.print(f"Created project [bold]{project.manifest.project.name}[/bold] in {project.root}")
    if model.entities:
        console.print(
            f"Example model: {len(model.entities)} entities, {len(model.relationships)} relationships, "
            f"{len(model.metrics)} metric."
        )
    console.print("Next: [bold]murakumo validate[/bold]")


@app.command()
def validate(
    path: Path = typer.Argument(Path("."), help="Project folder, or any folder inside it."),
    fix: bool = typer.Option(False, "--fix", help="Write stable ids for objects that have none."),
    strict: bool = typer.Option(False, "--strict", help="Fail on warnings as well as errors."),
    output: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Check the manifest and model for errors and modelling problems."""
    root = find_root(path) or path
    result = load_project(root)
    diagnostics = list(result.diagnostics)
    project = result.project

    if project is not None:
        load_errors = any(d.level is Level.ERROR for d in diagnostics)
        if fix and result.missing_ids and not load_errors:
            save_model(project)
            diagnostics = [d for d in diagnostics if d.code != "MK005"]
            if output == "text":
                console.print(f"Wrote ids for {result.missing_ids} object(s).")
        elif fix and load_errors and output == "text":
            err_console.print("[yellow]Not writing ids: fix the errors below first.[/yellow]")
        diagnostics += validate_project(project)

    errors = sum(d.level is Level.ERROR for d in diagnostics)
    warnings = len(diagnostics) - errors
    base = project.root if project else root.resolve()

    if output == "json":
        payload = {
            "ok": errors == 0 and not (strict and warnings),
            "errors": errors,
            "warnings": warnings,
            "diagnostics": [
                {
                    "level": d.level.value,
                    "code": d.code,
                    "message": d.message,
                    "file": _relative(d.file, base),
                    "where": d.where,
                }
                for d in diagnostics
            ],
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        print_diagnostics(diagnostics, base)
        if project is not None:
            m = project.model
            console.print(
                f"{project.manifest.project.name}: {len(m.sources)} sources, {len(m.entities)} entities, "
                f"{sum(len(e.attributes) for e in m.entities)} columns, {len(m.relationships)} relationships"
            )
        status = "[red]failed[/red]" if errors or (strict and warnings) else "[green]valid[/green]"
        console.print(f"{status}: {errors} error(s), {warnings} warning(s)")

    if errors or (strict and warnings):
        raise typer.Exit(1)


def _relative(file: Path | None, base: Path) -> str | None:
    if file is None:
        return None
    return str(file.relative_to(base)) if file.is_relative_to(base) else str(file)


class SchemaKind(StrEnum):
    MANIFEST = "manifest"
    MODEL = "model"
    ENTITY = "entity"
    SOURCE = "source"
    RELATIONSHIP = "relationship"


_SCHEMAS = {
    SchemaKind.MANIFEST: Manifest,
    SchemaKind.MODEL: Model,
    SchemaKind.ENTITY: Entity,
    SchemaKind.SOURCE: SourceSystem,
    SchemaKind.RELATIONSHIP: Relationship,
}


@app.command()
def schema(kind: SchemaKind = typer.Argument(SchemaKind.MODEL, help="Which schema to print.")) -> None:
    """Print the JSON Schema of the IR, for editors and the canvas. `manifest` describes murakumo.yaml."""
    typer.echo(json.dumps(_SCHEMAS[kind].model_json_schema(by_alias=True), indent=2))


# Model commands live in their own module; registered here so `murakumo --help` lists them.
from murakumo import cli_model  # noqa: E402

app.add_typer(cli_model.import_app, name="import")
app.add_typer(cli_model.add_app, name="add")
app.add_typer(cli_model.remove_app, name="remove")
