"""``murakumo stack``, ``ingest``, ``load``, ``source`` and ``decisions``: choosing the platform's
tools, fetching the sources' data, and recording why."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.markup import escape
from rich.table import Table

from murakumo.cli_model import ProjectOption, open_project
from murakumo.console import console, fail

stack_app = typer.Typer(
    help="The data platform's tools: warehouse, extract, load, transform, orchestrator and CI.",
    invoke_without_command=True,
)
source_app = typer.Typer(help="A source's connection: set it, test it, try a request.", no_args_is_help=True)
decisions_app = typer.Typer(help="Why the platform is built the way it is.", invoke_without_command=True)

WhyOption = typer.Option(None, "--why", help="Why: recorded in the decisions log, model/decisions.yaml.")


def _print_problems(problems: list[tuple[str, str, str]]) -> None:
    for level, part, message in problems:
        mark = "[red]error[/red]" if level == "error" else "[yellow]warn[/yellow]"
        console.print(f"  {mark} {part}: {escape(message)}")


@stack_app.callback()
def stack_show(ctx: typer.Context, project_path: Path = ProjectOption) -> None:
    """Show the stack, and anything in it that does not go together."""
    if ctx.invoked_subcommand is not None:
        return
    from murakumo import stack

    project = open_project(project_path)
    values = stack.current(project)
    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_column(style="dim")
    for part in stack.PARTS:
        value = values[part.key]
        table.add_row(part.title, value, part.options.get(value, part.question))
    console.print(table)
    problems = stack.check(project)
    if problems:
        console.print()
        _print_problems(problems)
    console.print("\nChange a part with [bold]murakumo stack set PART=VALUE --why '...'[/bold].")


@stack_app.command("set")
def stack_set(
    values: list[str] = typer.Argument(..., help="PART=VALUE, such as load=snowpipe. Several at once are fine."),
    why: str | None = WhyOption,
    project_path: Path = ProjectOption,
) -> None:
    """Change parts of the stack in murakumo.yaml; each change goes in the decisions log."""
    from murakumo import stack

    project = open_project(project_path)
    wanted = {}
    for item in values:
        key, sep, value = item.partition("=")
        if not sep:
            fail(f"write {item!r} as PART=VALUE, such as extract=lambda")
            raise typer.Exit(1)
        wanted[key.strip()] = value.strip()
    try:
        changed = stack.set_parts(project, wanted, why)
    except stack.StackError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    for line in changed or ["nothing changed"]:
        console.print(f"  {line}")
    if changed:
        from murakumo.project import load_project

        problems = stack.check(load_project(project.root).project)
        _print_problems(problems)
        console.print("Run [bold]murakumo generate[/bold] to write the code for it.")


@stack_app.command("check")
def stack_check(project_path: Path = ProjectOption) -> None:
    """Check that the stack's choices go together. Exits 1 on an error."""
    from murakumo import stack

    problems = stack.check(open_project(project_path))
    if not problems:
        console.print("The stack's choices go together.")
    _print_problems(problems)
    if any(level == "error" for level, _, _ in problems):
        raise typer.Exit(1)


@stack_app.command("deploy")
def stack_deploy(
    environment: str = typer.Option(..., "--env", help="The environment to deploy: dev, stage, prod."),
    apply: bool = typer.Option(False, "--apply", help="Run the steps Murakumo can run. Without it, only show them."),
    step: list[str] = typer.Option(None, "--step", help="Only this step (provision, integration, pipes, ci)."),
    show_sql: bool = typer.Option(False, "--sql", help="Print each step's SQL in full."),
    project_path: Path = ProjectOption,
) -> None:
    """Build the platform for an environment: show the steps, then run them with --apply."""
    from murakumo import deploy

    project = open_project(project_path)
    try:
        steps = deploy.plan(project, environment)
    except deploy.DeployError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    if not apply:
        for number, s in enumerate(steps, 1):
            who = {"sql": "Murakumo runs it", "file": "Murakumo writes it", "commands": "you run it"}[s.kind]
            console.print(f"[bold]{number}. {escape(s.title)}[/bold] [dim]({s.key}; {who})[/dim]")
            console.print(f"   {escape(s.why)}")
            if s.kind == "sql":
                console.print(f"   [dim]as {escape(s.role or '')}[/dim]")
            if s.content and (show_sql or s.kind == "commands"):
                for line in s.content.splitlines():
                    console.print(f"     {escape(line)}", highlight=False)
        console.print(
            "\nNothing has run. Run it with [bold]--apply[/bold]; add [bold]--sql[/bold] to read the SQL first."
        )
        return
    try:
        done = deploy.apply(project, environment, only=step or None, say=lambda m: console.print(escape(m)))
    except deploy.DeployError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    failed = [s for s in done if s.kind in {"sql", "file"} and s.result and not s.done]
    if failed:
        raise typer.Exit(1)


def ingest_command(
    source: str | None = typer.Option(None, "--source", help="Only this source."),
    table: str | None = typer.Option(None, "--table", help="Only this table."),
    project_path: Path = ProjectOption,
) -> None:
    """Fetch what is new from the sources into the landing area, with the generated extract code."""
    from murakumo import ingest

    project = open_project(project_path)
    try:
        with console.status("Fetching..."):
            results = ingest.run(project, source, table)
    except ingest.IngestError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    failed = False
    for fetched in results:
        if fetched.error:
            failed = True
            fail(f"{fetched.source}: {fetched.error}")
            continue
        for name, (count, files) in fetched.tables.items():
            console.print(f"  {fetched.source}.{name}: {count} new, {len(files)} file(s) landed")
    console.print(f"Landed under {project.manifest.stack.landing}/. Load it with [bold]murakumo load[/bold].")
    if failed:
        raise typer.Exit(1)


def load_command(
    environment: str | None = typer.Option(None, "--env", help="The environment to load. Default: your own."),
    prefix: str | None = typer.Option(None, "--prefix", help="Load into the schemas under this prefix."),
    project_path: Path = ProjectOption,
) -> None:
    """Load the landed files into the raw tables, each file once, without running dbt."""
    from murakumo import ingest
    from murakumo.cli_generate import _report
    from murakumo.generate.verify import build
    from murakumo.importers.database import SourceUnavailable

    project = open_project(project_path)
    files = ingest.landed(project)
    if not files:
        console.print("Nothing has landed yet: run [bold]murakumo ingest[/bold] first.")
        return
    try:
        with console.status("Loading the landed files..."):
            result = build(project, environment=environment, data=files, schema_prefix=prefix, load_only=True)
    except SourceUnavailable as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    _report(result, files)
    if result.error:
        fail(result.error)
        raise typer.Exit(1)


@source_app.command("connect")
def source_connect(
    name: str = typer.Argument(..., help="The source, as in murakumo.yaml (added if it is not there)."),
    kind: str = typer.Argument(..., help="rest, sftp, database, files or protobuf."),
    settings: list[str] = typer.Argument(
        None, help="key=value: base_url=https://... auth.type=bearer auth.env=TOKEN endpoints.0.table=hourly"
    ),
    from_file: Path | None = typer.Option(None, "--file", help="The connection as a JSON or YAML file instead."),
    why: str | None = WhyOption,
    project_path: Path = ProjectOption,
) -> None:
    """Say how a source's data is fetched. Secrets are never given here: name the variables holding them."""
    import yaml

    from murakumo import stack

    project = open_project(project_path)
    try:
        if from_file:
            connection = yaml.safe_load(from_file.read_text(encoding="utf-8")) or {}
        else:
            connection = stack.parse_assignments(settings or [])
        connection = {"kind": kind, **{k: v for k, v in connection.items() if k != "kind"}}
        console.print(stack.set_connection(project, name, connection, why))
    except (stack.StackError, OSError, yaml.YAMLError) as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    console.print(f"Test it with [bold]murakumo source test {name}[/bold].")


@source_app.command("disconnect")
def source_disconnect(name: str = typer.Argument(...), why: str | None = WhyOption, project_path: Path = ProjectOption):
    """Take a source's connection away: Murakumo stops fetching it."""
    from murakumo import stack

    try:
        console.print(stack.set_connection(open_project(project_path), name, None, why))
    except stack.StackError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None


@source_app.command("test")
def source_test(name: str = typer.Argument(...), project_path: Path = ProjectOption) -> None:
    """Check a source's connection: reach it, and see what it has, without landing anything."""
    from murakumo import ingest

    project = open_project(project_path)
    try:
        with console.status(f"Trying {name}..."):
            lines = ingest.test(project, name)
    except ingest.IngestError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    for line in lines:
        console.print(f"  [green]✓[/green] {escape(line)}")


@source_app.command("request")
def source_request(
    name: str = typer.Argument(...),
    table: str | None = typer.Option(None, "--table", help="The endpoint's table. Default: the first."),
    full: bool = typer.Option(False, "--full", help="Print the whole response, not the first records."),
    project_path: Path = ProjectOption,
) -> None:
    """Send one request of a REST source's endpoint, and show what came back."""
    from murakumo import ingest

    project = open_project(project_path)
    declaration = next((d for d in project.manifest.sources if d.name.lower() == name.lower()), None)
    if declaration is None or declaration.connection is None or declaration.connection.kind != "rest":
        fail(f"{name} has no REST connection")
        raise typer.Exit(1)
    connection = declaration.connection.model_dump(exclude_none=True)
    endpoints = connection.get("endpoints") or []
    endpoint = next((e for e in endpoints if not table or e["table"] == table), None)
    if endpoint is None:
        fail(f"{name} has no endpoint for {table}" if table else f"{name} has no endpoints yet")
        raise typer.Exit(1)
    try:
        result = ingest.preview(connection, endpoint)
    except ingest.IngestError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    console.print(f"{result['status']} in {result['milliseconds']} ms: {result['records']} record(s)")
    shown = result["response"] if full else result["sample"][:5]
    console.print_json(json.dumps(shown, default=str))


@decisions_app.callback()
def decisions_list(ctx: typer.Context, project_path: Path = ProjectOption) -> None:
    """List the decisions, oldest first."""
    if ctx.invoked_subcommand is not None:
        return
    from murakumo import stack

    project = open_project(project_path)
    entries = stack.decisions(project.root)
    if not entries:
        console.print("No decisions recorded yet. Changing the stack records one; or add one with `decisions add`.")
    for entry in entries:
        change = f" ({entry.get('from')} → {entry.get('to')})" if "from" in entry or "to" in entry else ""
        console.print(
            f"[dim]{entry['at']} {entry.get('by', '')}[/dim]  [bold]{escape(str(entry['what']))}[/bold]{escape(change)}"
        )
        if entry.get("why"):
            console.print(f"    {escape(entry['why'])}")


@decisions_app.command("add")
def decisions_add(
    what: str = typer.Argument(..., help="The decision, in a few words: 'Trips land hourly, not daily'."),
    why: str | None = WhyOption,
    project_path: Path = ProjectOption,
) -> None:
    """Record a decision of your own in the log."""
    from murakumo import stack

    project = open_project(project_path)
    entry = stack.record(project.root, what, why=why)
    console.print(f"Recorded at {entry['at']}.")
