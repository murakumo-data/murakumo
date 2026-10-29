"""``murakumo generate``, ``migrate``, ``verify`` and ``build``."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape

from murakumo.cli_model import ProjectOption, open_project
from murakumo.console import console, fail, print_diagnostics
from murakumo.diagnostics import Level
from murakumo.generate import GENERATED_DIR, compare, generate, write
from murakumo.manifest import Warehouse
from murakumo.migrate import MIGRATIONS_DIR, plan_migration, render, write_migration
from murakumo.validate import validate_project

WarehouseOption = typer.Option(None, "--warehouse", help="Generate for this warehouse instead of the manifest's.")


def generate_command(
    project_path: Path = ProjectOption,
    check: bool = typer.Option(False, "--check", help="Write nothing; fail if generated/ is out of date (for CI)."),
    warehouse: Warehouse | None = WarehouseOption,
) -> None:
    """Compile the model into generated/: a dbt project and DDL."""
    project = open_project(project_path)
    problems = validate_project(project)
    if any(d.level is Level.ERROR for d in problems):
        print_diagnostics([d for d in problems if d.level is Level.ERROR], project.root)
        fail("the model has errors; fix them (see `murakumo validate`) before generating")
        raise typer.Exit(1)

    output = generate(project, warehouse)
    print_diagnostics(output.diagnostics, project.root)
    if any(d.level is Level.ERROR for d in output.diagnostics):
        console.print("[yellow]Entities with errors were skipped; the rest was generated.[/yellow]")

    if check:
        changes = compare(output, project.root)
        if changes.any:
            for path in changes.added + changes.changed + changes.removed:
                console.print(f"  out of date: {GENERATED_DIR}/{path}")
            fail("generated/ is out of date; run `murakumo generate` and commit the result")
            raise typer.Exit(1)
        console.print(f"generated/ is up to date ({len(output.files)} files).")
        return

    changes = write(output, project.root)
    plan = output.plan
    staging = sum(m.layer == "staging" for m in plan.models)
    console.print(
        f"Generated {len(output.files)} files for {plan.target.warehouse.value}: "
        f"{staging} staging and {len(plan.models) - staging} modelled dbt models, "
        f"{len(plan.snapshots)} snapshot(s), and DDL."
    )
    if changes.any:
        console.print(f"  {len(changes.added)} added, {len(changes.changed)} changed, {len(changes.removed)} removed")
    else:
        console.print("  No changes.")
    for path in changes.edited_by_hand:
        console.print(f"  [yellow]overwrote hand edits in {GENERATED_DIR}/{path}[/yellow]; put custom code in custom/")


def migrate_command(
    project_path: Path = ProjectOption,
    name: str | None = typer.Option(None, "--name", help="Name the migration file, e.g. split_customer_name."),
    check: bool = typer.Option(False, "--check", help="Write nothing; fail if the model needs a migration (for CI)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the migration instead of writing it."),
    warehouse: Warehouse | None = WarehouseOption,
) -> None:
    """Write the next migration: ALTER statements for tables that keep data, from what changed in the model."""
    project = open_project(project_path)
    problems = validate_project(project)
    if any(d.level is Level.ERROR for d in problems):
        print_diagnostics([d for d in problems if d.level is Level.ERROR], project.root)
        fail("the model has errors; fix them (see `murakumo validate`) before writing a migration")
        raise typer.Exit(1)

    pending = plan_migration(project, warehouse)
    if name:
        pending.title = name
    where = f"{MIGRATIONS_DIR}/{pending.warehouse.value}"
    if check:
        if pending.baseline:
            console.print(f"No migrations in {where}; nothing to check.")
        elif pending.changes:
            for change in pending.changes:
                console.print(f"  needs a migration: {escape(change.summary)}")
            fail("the model changed since the last migration; run `murakumo migrate` and commit the result")
            raise typer.Exit(1)
        else:
            console.print(f"{where} is up to date.")
        return
    for note in pending.notes:
        console.print(f"  [dim]note: {escape(note)}[/dim]")
    if not pending.changes and not pending.baseline:
        console.print("No changes to tables that keep data; no migration needed.")
        return
    if dry_run:
        typer.echo(render(pending), nl=False)
        return
    path = write_migration(project, pending)
    console.print(f"Wrote {path.relative_to(project.root)} ({len(pending.changes)} change(s)).")
    for change in pending.changes:
        marker = "[yellow]![/yellow]" if change.destructive else "-"
        console.print(f"  {marker} {escape(change.summary)}")
    if any(c.destructive for c in pending.changes):
        console.print("[yellow]Statements that would lose data are commented out; review them.[/yellow]")


def _data_files(project, data: list[str] | None) -> dict[str, str]:
    files: dict[str, str] = {}
    for item in data or []:
        table, sep, location = (part.strip() for part in item.partition("="))
        if not sep or not table or not location:
            fail(f"cannot read --data {item!r}; write TABLE=PATH")
            raise typer.Exit(1)
        entity = project.model.entity(table.rsplit(".", 1)[-1])
        if entity is None or entity.layer.value != "raw":
            fail(f"--data {table}: no raw table of that name in the model")
            raise typer.Exit(1)
        files[table] = location
    return files


def _report(result, files: dict[str, str]) -> None:
    for table, count in sorted(result.loaded.items()):
        source = {k.lower(): v for k, v in files.items()}.get(table.lower())
        how = f"from {source}" if source else "synthetic"
        console.print(f"  loaded {table}: {count} rows ({how})")
    for node in result.nodes:
        if node.status not in ("success", "pass"):
            colour = "yellow" if node.status == "warn" else "red"
            detail = f" ({node.failures} failing rows)" if node.failures else ""
            console.print(f"  [{colour}]{node.status}[/{colour}] {node.kind} {node.name}{detail}")
            if node.message and node.status not in ("warn",):
                console.print(f"      {escape(node.message.strip().splitlines()[0][:300])}")


def verify_command(
    project_path: Path = ProjectOption,
    rows: int = typer.Option(200, help="Synthetic rows per raw table."),
    data: list[str] = typer.Option(
        None, "--data", help="Load a real file into a raw table: TABLE=PATH_OR_URL. Repeat for several."
    ),
    limit: int | None = typer.Option(None, help="Load at most this many rows from each --data file."),
    keep: Path | None = typer.Option(None, help="Keep the build folder here, to inspect the project and database."),
    once: bool = typer.Option(False, "--once", help="Build once; skip the second, incremental build."),
    warehouse: Warehouse = typer.Option(
        Warehouse.DUCKDB, "--warehouse", help="duckdb (local, default) or snowflake (uses SNOWFLAKE_* variables)."
    ),
    keep_tables: bool = typer.Option(
        False, "--keep-tables", help="On Snowflake, keep the run's schemas instead of dropping them."
    ),
) -> None:
    """Build the generated dbt project and run every test, twice: on DuckDB, or on Snowflake."""
    from murakumo.generate.verify import verify
    from murakumo.importers.database import SourceUnavailable

    project = open_project(project_path)
    problems = validate_project(project)
    if any(d.level is Level.ERROR for d in problems):
        print_diagnostics([d for d in problems if d.level is Level.ERROR], project.root)
        fail("the model has errors; fix them before verifying")
        raise typer.Exit(1)
    files = _data_files(project, data)

    with console.status(f"Generating for {warehouse.value}, loading raw tables and running dbt build..."):
        try:
            result = verify(
                project,
                rows=rows,
                data=files,
                limit=limit,
                keep=keep,
                rerun=not once,
                warehouse=warehouse,
                keep_tables=keep_tables,
            )
        except SourceUnavailable as exc:
            fail(str(exc))
            raise typer.Exit(1) from None

    _report(result, files)
    if result.error:
        fail(result.error)
        raise typer.Exit(1)
    models = result.count("model") + result.count("snapshot")
    tests = result.count("test")
    passed = result.count("test", "pass")
    summary = f"{models} models and snapshots built, {passed}/{tests} tests passed"
    if result.rerun is not None:
        summary += ", incremental rebuild " + ("passed" if result.rerun else "failed")
    summary += f" on {result.warehouse}"
    if result.ok:
        console.print(f"[green]verified[/green]: {summary}.")
    else:
        console.print(f"[red]failed[/red]: {summary}.")
    if keep:
        console.print(f"dbt project kept in {keep}.")
    if result.location and (keep_tables or warehouse is Warehouse.DUCKDB):
        console.print(f"Tables kept in {result.location}.")
    if not result.ok:
        raise typer.Exit(1)


def build_command(
    project_path: Path = ProjectOption,
    environment: str | None = typer.Option(
        None, "--env", help="Environment to build (its schemas and profile). Defaults to the first in murakumo.yaml."
    ),
    sample: int = typer.Option(0, help="Load this many synthetic rows into raw tables that are still empty."),
    data: list[str] = typer.Option(
        None, "--data", help="Load a real file into a raw table: TABLE=PATH_OR_URL. Repeat for several."
    ),
    limit: int | None = typer.Option(None, help="Load at most this many rows from each --data file."),
    warehouse: Warehouse | None = typer.Option(
        None, "--warehouse", help="duckdb or snowflake. Defaults to the project's warehouse."
    ),
    schedule: str | None = typer.Option(
        None, "--schedule", help="Build only what this schedule refreshes, as its orchestrator would."
    ),
) -> None:
    """Build the project into the warehouse and keep it: raw tables, then every dbt model and test.

    Unlike `verify`, nothing is dropped afterwards; the tables are there to query.
    """
    from murakumo.generate.verify import build
    from murakumo.importers.database import SourceUnavailable

    project = open_project(project_path)
    problems = validate_project(project)
    if any(d.level is Level.ERROR for d in problems):
        print_diagnostics([d for d in problems if d.level is Level.ERROR], project.root)
        fail("the model has errors; fix them before building")
        raise typer.Exit(1)
    files = _data_files(project, data)
    if warehouse in (None, project.manifest.targets.warehouse.type) and compare(generate(project), project.root).any:
        console.print(
            f"[yellow]![/yellow] {GENERATED_DIR}/ is out of date with the model; building the model as it is now. "
            "Run [bold]murakumo generate[/bold] to bring the files in line."
        )
    if (project.root / MIGRATIONS_DIR).is_dir():
        pending = plan_migration(project, warehouse)
        if pending.changes:
            console.print(
                f"[yellow]![/yellow] {len(pending.changes)} table change(s) have no migration yet. Existing raw "
                "tables keep their old shape until you run [bold]murakumo migrate[/bold] and apply the SQL."
            )

    target = (warehouse or project.manifest.targets.warehouse.type).value
    with console.status(f"Building on {target}: raw tables, then dbt build..."):
        try:
            result = build(project, warehouse, environment, data=files, limit=limit, sample=sample, schedule=schedule)
        except SourceUnavailable as exc:
            fail(str(exc))
            raise typer.Exit(1) from None

    _report(result, files)
    if result.skipped:
        console.print(f"  kept the rows already in {', '.join(sorted(result.skipped))} (no sample added)")
    if result.error:
        fail(result.error)
        raise typer.Exit(1)
    models = result.count("model") + result.count("snapshot")
    tests = result.count("test")
    passed = result.count("test", "pass")
    summary = f"{models} models and snapshots, {passed}/{tests} tests passed"
    colour, word = ("green", "built") if result.ok else ("red", "failed")
    console.print(f"[{colour}]{word}[/{colour}]: {summary}. Tables are in {result.location}.")
    if not result.ok:
        raise typer.Exit(1)
