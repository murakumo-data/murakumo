"""``murakumo ai``: suggestions for the model from a language model, when the project turns it on,
and ``murakumo mcp``: the model as tools for an agent of your own."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.markup import escape

from murakumo.cli_model import ProjectOption, finish, open_project
from murakumo.console import console, fail

ai_app = typer.Typer(
    help="Suggestions from a language model: descriptions, personal data, mappings, new tables. Off until enabled.",
    no_args_is_help=True,
)

ApplyOption = typer.Option(False, "--apply", help="Make the suggested changes, instead of only showing them.")
JsonOption = typer.Option(False, "--json", help="Print the suggestions as JSON.")
EntityOption = typer.Option(None, "--entity", "-e", help="Only these tables. Repeat for several.")


def _run(project_path: Path, kind: str, apply: bool, as_json: bool, **options) -> None:
    from murakumo import assistant

    project = open_project(project_path)
    try:
        with console.status("Asking the model..."):
            suggestions = assistant.suggest(project, kind, **options)
    except assistant.AssistantError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    if as_json and not apply:
        console.print_json(json.dumps([s.as_dict() for s in suggestions]))
        return
    if not suggestions:
        console.print("Nothing to suggest.")
        return
    if not apply:
        for s in suggestions:
            console.print(f"  {escape(str(s))}\n    [dim]{escape(s.reason)}[/dim]")
        console.print(f"\n{len(suggestions)} suggestion(s). Run it again with [bold]--apply[/bold] to make them.")
        return
    result = assistant.apply(project, suggestions)
    for s in result.applied:
        console.print(f"  [green]applied[/green] {escape(str(s))}")
    for s in result.refused:
        console.print(f"  [yellow]refused[/yellow] {escape(str(s))}: {escape(s.refused or '')}")
    if result.applied:
        finish(project)


@ai_app.command("status")
def status(project_path: Path = ProjectOption) -> None:
    """Whether the assistant is on, which model it uses, and whether its key is set."""
    import os

    settings = open_project(project_path).manifest.assistant
    console.print(f"Assistant: {'[green]on[/green]' if settings.enabled else 'off'}")
    console.print(f"Provider:  {settings.provider}, model {settings.model}")
    console.print(f"Key:       {settings.key_env} is {'set' if os.environ.get(settings.key_env) else 'not set'}")
    console.print("It is sent the model (names, types, descriptions), never rows of data.")


def _switch(project_path: Path, changes: dict) -> None:
    from murakumo.manifest_edit import write_section

    project = open_project(project_path)
    settings = {**project.manifest.assistant.model_dump(exclude_none=True), **changes}
    write_section(project.root, ("assistant",), settings)


@ai_app.command("enable")
def enable(
    model: str | None = typer.Option(None, help="The model to use. Default: claude-opus-5-5."),
    key_env: str | None = typer.Option(None, "--key-env", help="The variable holding the provider's key."),
    project_path: Path = ProjectOption,
) -> None:
    """Turn the assistant on for this project (assistant.enabled in murakumo.yaml)."""
    changes: dict = {"enabled": True}
    if model:
        changes["model"] = model
    if key_env:
        changes["key_env"] = key_env
    _switch(project_path, changes)
    console.print("The assistant is on. It uses your own key, and is sent the model, never rows of data.")


@ai_app.command("disable")
def disable(project_path: Path = ProjectOption) -> None:
    """Turn the assistant off."""
    _switch(project_path, {"enabled": False})
    console.print("The assistant is off.")


@ai_app.command("describe")
def describe(
    entity: list[str] = EntityOption,
    apply: bool = ApplyOption,
    as_json: bool = JsonOption,
    project_path: Path = ProjectOption,
) -> None:
    """Suggest descriptions for the tables and columns that have none."""
    _run(project_path, "describe", apply, as_json, entities=entity or None)


@ai_app.command("classify")
def classify(
    entity: list[str] = EntityOption,
    apply: bool = ApplyOption,
    as_json: bool = JsonOption,
    project_path: Path = ProjectOption,
) -> None:
    """Suggest which columns hold personal data, and how sensitive each is."""
    _run(project_path, "classify", apply, as_json, entities=entity or None)


@ai_app.command("map")
def map_(
    entity: str = typer.Argument(..., help="The modelled table whose unmapped columns to place."),
    apply: bool = ApplyOption,
    as_json: bool = JsonOption,
    project_path: Path = ProjectOption,
) -> None:
    """Suggest where each unmapped column of a modelled table comes from."""
    _run(project_path, "map", apply, as_json, entity=entity)


@ai_app.command("draft")
def draft(
    request: str = typer.Argument(..., help="What is needed: 'a fact of daily sales by store and product'."),
    apply: bool = ApplyOption,
    as_json: bool = JsonOption,
    project_path: Path = ProjectOption,
) -> None:
    """Suggest new tables from a sentence or two about what is needed."""
    _run(project_path, "draft", apply, as_json, request=request)


catalog_app = typer.Typer(help="Lineage for a data catalog, in OpenLineage's format.", no_args_is_help=True)


@catalog_app.command("export")
def catalog_export(
    output: Path = typer.Option(Path("lineage.jsonl"), "--output", "-o", help="The file to write."),
    format: str = typer.Option("openlineage", "--format", help="openlineage (one event per line) or json (a graph)."),
    project_path: Path = ProjectOption,
) -> None:
    """Write where every table and column comes from, for a catalog to read."""
    from murakumo import catalog

    project = open_project(project_path)
    if format == "json":
        output.write_text(json.dumps(catalog.graph(project), indent=2) + "\n", encoding="utf-8")
        console.print(f"Wrote the lineage of {len(project.model.entities)} table(s) to {output}.")
        return
    if format != "openlineage":
        fail("--format is openlineage or json")
        raise typer.Exit(1)
    found = catalog.events(project)
    output.write_text("".join(json.dumps(event) + "\n" for event in found), encoding="utf-8")
    console.print(f"Wrote {len(found)} OpenLineage event(s) to {output}: one per modelled table.")


@catalog_app.command("send")
def catalog_send(
    url_env: str = typer.Option("OPENLINEAGE_URL", "--url-env", help="The variable holding the catalog's address."),
    key_env: str | None = typer.Option(None, "--key-env", help="The variable holding its key, if it needs one."),
    project_path: Path = ProjectOption,
) -> None:
    """Send the lineage to a catalog's OpenLineage endpoint (Marquez, DataHub, Atlan, Purview...)."""
    from murakumo import catalog

    project = open_project(project_path)
    try:
        sent = catalog.send(catalog.events(project), url_env, key_env)
    except catalog.CatalogError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    console.print(f"Sent {sent} table(s) to the catalog.")


def mcp(project_path: Path = ProjectOption) -> None:
    """Serve the project to an agent as tools (the Model Context Protocol, over stdin and stdout)."""
    from murakumo import mcp as server
    from murakumo.project import find_root

    root = find_root(project_path)
    if root is None:
        fail(f"no murakumo.yaml in {project_path.resolve()} or above; run `murakumo init` first")
        raise typer.Exit(1)
    server.serve(root)
