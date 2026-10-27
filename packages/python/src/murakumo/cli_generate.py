"""``murakumo generate`` and ``murakumo verify``."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape

from murakumo.cli_model import ProjectOption, open_project
from murakumo.console import console, fail, print_diagnostics
from murakumo.diagnostics import Level
from murakumo.generate import GENERATED_DIR, compare, generate, write
from murakumo.manifest import Warehouse
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
