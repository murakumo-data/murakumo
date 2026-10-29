"""The `murakumo` command-line interface."""

from __future__ import annotations

import json
import sys
from enum import StrEnum
from pathlib import Path

import typer

from murakumo import __version__
from murakumo.console import LEGEND, console, err_console, print_checks, print_diagnostics
from murakumo.diagnostics import Level
from murakumo.guide import Status, run_checks
from murakumo.ir.model import Entity, Model, Relationship, SourceSystem
from murakumo.manifest import MANIFEST_FILE, Manifest, ModellingPattern, Orchestrator, Warehouse
from murakumo.project import find_root, load_project, save_model
from murakumo.scaffold import ProjectExistsError, configure_project, init_project, project_name
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
    console.print("Model-first data engineering: design the model, generate the platform.")
    console.print("Start with [bold]murakumo init[/bold]; check a project's setup with [bold]murakumo setup[/bold].")


def _can_ask() -> bool:
    """Whether someone is at a terminal to answer questions."""
    return sys.stdin.isatty()


_WAREHOUSES = {
    Warehouse.DUCKDB: "on this computer, no account needed (best to start with)",
    Warehouse.SNOWFLAKE: "Snowflake",
    Warehouse.DATABRICKS: "Databricks",
    Warehouse.BIGQUERY: "Google BigQuery",
    Warehouse.REDSHIFT: "Amazon Redshift",
    Warehouse.FABRIC: "Microsoft Fabric",
    Warehouse.SYNAPSE: "Azure Synapse",
    Warehouse.POSTGRES: "PostgreSQL",
}
_ORCHESTRATORS = {
    Orchestrator.AIRFLOW: "Apache Airflow, the most common (Astro CLI locally, or managed)",
    Orchestrator.DAGSTER: "Dagster",
    Orchestrator.PREFECT: "Prefect",
    Orchestrator.DATABRICKS_WORKFLOWS: "Databricks Workflows",
    Orchestrator.SNOWFLAKE_TASKS: "Snowflake tasks",
    Orchestrator.AWS_GLUE: "AWS Glue",
    Orchestrator.AWS_STEP_FUNCTIONS: "AWS Step Functions",
    Orchestrator.GCP_COMPOSER: "Google Cloud Composer (managed Airflow)",
    Orchestrator.GCP_WORKFLOWS: "Google Cloud Workflows",
    Orchestrator.AZURE_DATA_FACTORY: "Azure Data Factory",
}
_PATTERNS = {
    ModellingPattern.KIMBALL: "star schema: facts and dimensions, the most common",
    ModellingPattern.DATAVAULT: "Data Vault: hubs, links and satellites, for many changing sources",
    ModellingPattern.OBT: "one big table per subject, simple for dashboards",
    ModellingPattern.THIRD_NORMAL_FORM: "normalised (3NF), shaped like the source systems",
    ModellingPattern.MIXED: "a mix, chosen table by table",
}


def _choose(question: str, options: dict, default: StrEnum) -> StrEnum:
    """A numbered menu; the answer can be the number or the name."""
    items = list(options)
    width = max(len(item.value) for item in items)
    console.print(f"\n[bold]{question}[/bold]")
    for number, item in enumerate(items, 1):
        console.print(f"  [cyan]{number:>2}[/cyan]  {item.value:<{width}}  [dim]{options[item]}[/dim]")
    while True:
        answer = typer.prompt(f"Choose 1-{len(items)}", default=str(items.index(default) + 1)).strip().lower()
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return items[int(answer) - 1]
        for item in items:
            if answer == item.value:
                return item
        console.print(f"[red]Type a number from 1 to {len(items)}.[/red]")


@app.command()
def init(
    path: Path = typer.Argument(Path("."), help="Folder to create the project in."),
    name: str | None = typer.Option(None, help="Project name. Defaults to the folder name."),
    target: Warehouse | None = typer.Option(None, "--target", "-t", help="The warehouse to build on."),
    orchestrator: Orchestrator | None = typer.Option(None, help="What will schedule the pipelines."),
    pattern: ModellingPattern | None = typer.Option(None, help="The modelling pattern for marts."),
    empty: bool = typer.Option(False, "--empty", help="Start with an empty model instead of the NYC taxi example."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Ask nothing; use the defaults for anything not given."),
) -> None:
    """Create a new project, then show what it needs set up on this machine."""
    if (path / MANIFEST_FILE).exists():
        err_console.print(f"[red]error[/red] {(path / MANIFEST_FILE).resolve()} already exists")
        raise typer.Exit(1)
    interactive = not yes and _can_ask() and target is None
    if interactive:
        console.print(
            "A few questions about the project. Press Enter to accept the suggestion shown in square brackets; "
            "you can change any answer later with [bold]murakumo config[/bold].\n"
        )
        name = name or typer.prompt("Project name", default=project_name(path))
        target = _choose("Where will the data be stored and transformed? (warehouse)", _WAREHOUSES, Warehouse.DUCKDB)
        orchestrator = orchestrator or _choose(
            "What will run the pipelines on a schedule? (orchestrator)", _ORCHESTRATORS, Orchestrator.AIRFLOW
        )
        pattern = pattern or _choose(
            "How should the reporting tables be shaped? (modelling pattern)", _PATTERNS, ModellingPattern.KIMBALL
        )
        console.print()
        empty = empty or not typer.confirm(
            "Add a small example model (NYC taxi trips) to explore? You can delete it later", default=True
        )
    try:
        project = init_project(
            path,
            name=name,
            example=not empty,
            warehouse=target or Warehouse.DUCKDB,
            orchestrator=orchestrator or Orchestrator.AIRFLOW,
            pattern=pattern or ModellingPattern.KIMBALL,
        )
    except ProjectExistsError as exc:
        err_console.print(f"[red]error[/red] {exc}")
        raise typer.Exit(1) from None

    model = project.model
    console.print(f"\nCreated project [bold]{project.manifest.project.name}[/bold] in {project.root}")
    if model.entities:
        console.print(
            f"Example model: {len(model.entities)} entities, {len(model.relationships)} relationships, "
            f"{len(model.metrics)} metric."
        )
    console.print("Change these answers later with [bold]murakumo config[/bold].")
    console.print("\n[bold underline]What this project needs on this machine[/bold underline]")
    console.print(f"[dim]{LEGEND}. Only what is not done yet is listed; `murakumo setup` shows everything.[/dim]")
    checks = run_checks(project)
    print_checks(checks, compact=True)
    todo = sum(c.status is Status.TODO for c in checks)
    here = Path.cwd().resolve()
    cd = f"[bold]cd {path}[/bold], then " if project.root != here else ""
    if todo:
        console.print(f"\n[bold red]{todo} thing(s) to do first[/bold red] (the ✗ above).")
        console.print(f"[bold cyan]Next:[/bold cyan] {cd}do them, then run [bold]murakumo setup[/bold] to check.")
    else:
        console.print("\n[bold green]Ready.[/bold green]")
        console.print(
            f"[bold cyan]Next:[/bold cyan] {cd}[bold]murakumo canvas[/bold] to see and edit the model, "
            "or [bold]murakumo generate[/bold] to write the code."
        )
    console.print("[dim]Every command, with what it does: murakumo --help[/dim]")


@app.command()
def setup(
    path: Path = typer.Argument(Path("."), help="Project folder, or any folder inside it."),
    connect: bool = typer.Option(False, "--connect", help="Also open the warehouse and source connections."),
    check: bool = typer.Option(False, "--check", help="Exit 1 if anything required is missing (for scripts)."),
    output: str = typer.Option("text", "--format", help="Output format: text or json."),
) -> None:
    """Check what this project needs on this machine (drivers, variables, keys, connections) and how to set it up."""
    root = find_root(path)
    if root is None:
        err_console.print(f"[red]error[/red] no murakumo.yaml in {path.resolve()} or above; run `murakumo init` first")
        raise typer.Exit(1)
    result = load_project(root)
    if result.project is None:
        print_diagnostics(result.diagnostics, root)
        raise typer.Exit(1)
    checks = run_checks(result.project, connect=connect)
    todo = sum(c.status is Status.TODO for c in checks)
    if output == "json":
        typer.echo(json.dumps({"ready": todo == 0, "checks": [c.as_dict() for c in checks]}, indent=2))
    else:
        manifest = result.project.manifest
        console.print(
            f"[bold]{manifest.project.name}[/bold]: builds on {manifest.targets.warehouse.type.value}, "
            f"scheduled by {manifest.generation.orchestrator.value}"
        )
        console.print(f"[dim]{LEGEND}[/dim]")
        print_checks(checks)
        warnings = sum(c.status is Status.WARN for c in checks)
        if todo:
            console.print(
                f"\n[red]{todo} thing(s) to do[/red] before the project can build, and {warnings} to consider."
            )
        else:
            console.print(f"\n[bold green]Ready to build.[/bold green] {warnings} thing(s) worth considering.")
        if not connect and manifest.targets.warehouse.type is not Warehouse.DUCKDB:
            console.print("Run [bold]murakumo setup --connect[/bold] to test the connections too.")
    if check and todo:
        raise typer.Exit(1)


@app.command()
def config(
    path: Path = typer.Argument(Path("."), help="Project folder, or any folder inside it."),
    target: Warehouse | None = typer.Option(None, "--target", "-t", help="The warehouse to build on."),
    orchestrator: Orchestrator | None = typer.Option(None, help="What will schedule the pipelines."),
    pattern: ModellingPattern | None = typer.Option(None, help="The modelling pattern for marts."),
) -> None:
    """Change the answers you gave `murakumo init`: warehouse, orchestrator, modelling pattern."""
    root = find_root(path)
    if root is None:
        err_console.print(f"[red]error[/red] no murakumo.yaml in {path.resolve()} or above")
        raise typer.Exit(1)
    result = load_project(root)
    if result.project is None:
        print_diagnostics(result.diagnostics, root)
        raise typer.Exit(1)
    manifest = result.project.manifest
    if target is None and orchestrator is None and pattern is None:
        if not _can_ask():
            err_console.print("[red]error[/red] say what to change: --target, --orchestrator or --pattern")
            raise typer.Exit(1)
        console.print("Press Enter to keep the current answer.")
        target = _choose("Warehouse", _WAREHOUSES, manifest.targets.warehouse.type)
        orchestrator = _choose("Orchestrator", _ORCHESTRATORS, manifest.generation.orchestrator)
        pattern = _choose("Modelling pattern", _PATTERNS, manifest.modelling.pattern)
    try:
        changed = configure_project(root, target, orchestrator, pattern)
    except ValueError as exc:
        err_console.print(f"[red]error[/red] {exc}")
        raise typer.Exit(1) from None
    if not changed:
        console.print("Nothing changed.")
        return
    for line in changed:
        console.print(f"  [green]✓[/green] {line}")
    console.print(
        "Saved in murakumo.yaml. Next: [bold]murakumo setup[/bold] to see what the new choices need, "
        "then [bold]murakumo generate[/bold]."
    )


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
app.add_typer(cli_model.rename_app, name="rename")
app.add_typer(cli_model.remove_app, name="remove")
app.add_typer(cli_model.move_app, name="move")

from murakumo import cli_generate  # noqa: E402

app.command("generate")(cli_generate.generate_command)
app.command("migrate")(cli_generate.migrate_command)
app.command("verify")(cli_generate.verify_command)
app.command("build")(cli_generate.build_command)
app.command("lineage")(cli_model.lineage_command)


@app.command()
def canvas(
    path: Path = typer.Argument(Path("."), help="Project folder, or any folder inside it."),
    port: int = typer.Option(8765, help="Port to listen on, on 127.0.0.1."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the canvas in your browser."),
) -> None:
    """Open the visual model editor. Changes are saved to the model's YAML files as you make them."""
    root = find_root(path)
    if root is None:
        err_console.print(f"[red]error:[/red] no murakumo.yaml in {path.resolve()} or above")
        raise typer.Exit(1)
    try:
        import uvicorn

        from murakumo import server
    except ImportError:
        err_console.print("[red]error:[/red] the canvas is not installed: pip install 'murakumo[canvas]'")
        raise typer.Exit(1) from None

    static = server.canvas_files()
    url = f"http://127.0.0.1:{port}"
    console.print(f"Editing [bold]{root}[/bold]")
    if static is None:
        console.print(
            f"API on {url}/api. The canvas is not built: run [bold]npm run dev[/bold] in apps/canvas "
            "and open http://localhost:5173."
        )
    else:
        console.print(f"Canvas on [bold]{url}[/bold]. Press Ctrl+C to stop.")
        if open_browser:
            import threading
            import webbrowser

            threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(server.create_app(root, static), host="127.0.0.1", port=port, log_level="warning")
