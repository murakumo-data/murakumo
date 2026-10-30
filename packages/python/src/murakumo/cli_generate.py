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
    apply: bool = typer.Option(
        False, "--apply", help="Run the migration files the warehouse has not run yet, instead of writing one."
    ),
    prefix: str = typer.Option("", "--prefix", help="With --apply: run them on the schemas under this prefix."),
    mark_applied: bool = typer.Option(
        False, "--mark-applied", help="With --apply: record them as run without running them (tables changed by hand)."
    ),
) -> None:
    """Write the next migration: ALTER statements for tables that keep data, from what changed in the model.

    With --apply, run the migrations the warehouse has not run yet instead, recording each in
    audit.murakumo_migrations; with --prefix, on a personal, pull-request or cloned environment.
    """
    project = open_project(project_path)
    if apply:
        _apply_migrations(project, warehouse, prefix, mark_applied)
        return
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
    prefix: str | None = typer.Option(
        None,
        "--prefix",
        help="Build into the schemas under this prefix, such as pr_42_. On a shared warehouse, dev builds go "
        "to dev_<your name>_ by default.",
    ),
    run_date: str | None = typer.Option(
        None, "--run-date", help="The day cumulative tables add (YYYY-MM-DD). Default: the latest day with data."
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

    from murakumo.environments import EnvError, prefix_for

    chosen = Warehouse(warehouse or project.manifest.targets.warehouse.type)
    target = chosen.value
    environment_name = environment or (project.manifest.targets.environments or ["dev"])[0]
    try:
        schema_prefix = prefix_for(project, environment_name, chosen, prefix)
    except EnvError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    if schema_prefix:
        console.print(f"Building into the schemas under [bold]{schema_prefix}[/bold] ({schema_prefix}staging, ...).")
    with console.status(f"Building on {target}: raw tables, then dbt build..."):
        try:
            result = build(
                project,
                warehouse,
                environment,
                data=files,
                limit=limit,
                sample=sample,
                schedule=schedule,
                schema_prefix=schema_prefix,
                dbt_vars={"run_date": run_date} if run_date else None,
            )
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


def export_command(
    what: str = typer.Argument("sql", help="What to export: sql, the project as plain SQL scripts."),
    project_path: Path = ProjectOption,
    warehouse: Warehouse | None = WarehouseOption,
    out: Path | None = typer.Option(None, "--out", help="Folder to write to. Default: export/sql/<warehouse>."),
) -> None:
    """Export the project as plain SQL: scripts that build it without dbt, in order, with tests as queries.

    Set `plain_sql: true` under generation in murakumo.yaml to keep them in generated/sql/ instead,
    written (and checked) with everything else.
    """
    from murakumo.generate import export_sql

    if what != "sql":
        fail(f"cannot export {what!r}; `murakumo export sql` is the one there is")
        raise typer.Exit(1)
    project = open_project(project_path)
    problems = validate_project(project)
    if any(d.level is Level.ERROR for d in problems):
        print_diagnostics([d for d in problems if d.level is Level.ERROR], project.root)
        fail("the model has errors; fix them before exporting")
        raise typer.Exit(1)
    output = generate(project, warehouse)
    files = export_sql(output.plan, output.files)
    folder = out or project.root / "export" / "sql" / output.plan.target.warehouse.value
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.rglob("*.sql"):
        stale.unlink()
    for path, content in files.items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    scripts = sum(1 for p in files if p.endswith(".sql") and "/" not in p)
    tests = sum(1 for p in files if p.startswith("tests/"))
    shown = folder.relative_to(project.root) if folder.is_relative_to(project.root) else folder
    console.print(f"Wrote {scripts} scripts and {tests} test files to {shown}/. README.md there says how to run them.")


def docs_command(
    project_path: Path = ProjectOption,
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the page in the browser."),
) -> None:
    """Show the pipeline docs: tables, columns, lineage, schedules and sources, on one page.

    `murakumo generate` keeps the same page in generated/docs/index.html, for committing; this
    shows it as the model is now, without writing anything to the project.
    """
    import tempfile
    import webbrowser

    from murakumo.generate import docs
    from murakumo.generate.plan import build_plan
    from murakumo.generate.targets import target_for

    project = open_project(project_path)
    plan = build_plan(project, target_for(project.manifest.targets.warehouse.type))
    folder = Path(tempfile.gettempdir()) / "murakumo-docs" / project.manifest.project.name
    folder.mkdir(parents=True, exist_ok=True)
    page = folder / "index.html"
    page.write_text(docs.html(project, plan), encoding="utf-8")
    console.print(f"Docs: {page}")
    if open_browser:
        webbrowser.open(page.as_uri())


def ci_command(
    project_path: Path = ProjectOption,
    provider: str = typer.Option("github", "--provider", help="github (GitHub Actions) or gitlab."),
    deploy: bool = typer.Option(
        False, "--deploy", help="Also build production on pushes to main, after approval, with the CI's secrets."
    ),
    preview: bool = typer.Option(
        False,
        "--preview",
        help="Also build each pull request on a clone of production, with its new migrations (Snowflake).",
    ),
    force: bool = typer.Option(False, "--force", help="Replace a CI file that has changes of its own."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the file instead of writing it."),
    run: bool = typer.Option(False, "--run", help="Run the CI's checks here instead, one after another."),
) -> None:
    """Write a CI workflow that checks every change: validate, generate --check, migrate --check, verify.

    The file goes at the root of the git repository. Once written it is yours: Murakumo will not
    overwrite changes you make to it without --force, and shows the difference instead.
    """
    from murakumo import ci

    project = open_project(project_path)
    if run:
        result = ci.run_checks(project.root, ci.Run())
        for step in result.steps:
            mark = "[green]✓[/green]" if step.state == "passed" else "[red]✗[/red]"
            console.print(f"{mark} {step.name}  [dim]{step.command}[/dim]")
            if step.state == "failed":
                console.print(escape(step.output))
        if any(s.state == "failed" for s in result.steps):
            raise typer.Exit(1)
        return
    try:
        info = ci.status(project, provider, deploy, preview)
    except ci.CIError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    if dry_run:
        typer.echo(info["content"], nl=False)
        return
    if deploy and not info["deploy_possible"]:
        console.print(
            "[yellow]![/yellow] The project builds into a DuckDB file, so there is no production to deploy to."
        )
    if info["state"] == "differs" and not force:
        typer.echo(info["diff"], nl=False)
        fail(f"{info['file']} has changes of its own (above); keep them, or replace it with --force")
        raise typer.Exit(1)
    if info["state"] == "current":
        console.print(f"{info['file']} is up to date.")
        return
    if preview and not info["preview_possible"]:
        console.print("[yellow]![/yellow] Pull-request previews clone production, which needs Snowflake; left out.")
    ci.write(project, provider, deploy, force=True, preview=preview)
    console.print(f"Wrote {info['file']} in {info['repository']}. Commit it, and the checks run on every push.")
    if deploy and info["deploy_possible"]:
        where = "Settings → Secrets and variables → Actions" if provider == "github" else "Settings → CI/CD → Variables"
        console.print(
            f"Add the warehouse credentials as secrets ({where}), and reviewers to the production environment."
        )
    if not info["in_git"]:
        console.print("[yellow]![/yellow] The project is not in a git repository yet: run `git init` first.")


def _apply_migrations(project, warehouse, prefix: str, mark_applied: bool) -> None:
    from murakumo.environments import EnvError, connect
    from murakumo.migrate import apply_migrations

    try:
        connection = connect(project, warehouse)
    except EnvError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    try:
        applied = apply_migrations(project, connection, prefix.lower(), mark_only=mark_applied)
    except Exception as exc:  # noqa: BLE001 - a warehouse error; show it and stop
        fail(f"a migration failed: {' '.join(str(exc).split())}")
        raise typer.Exit(1) from None
    finally:
        connection.close()
    where = f"the schemas under {prefix}" if prefix else "the shared schemas"
    verb = "Recorded" if mark_applied else "Applied"
    if applied:
        console.print(f"{verb} {len(applied)} migration(s) on {where}: {', '.join(applied)}")
    else:
        console.print(f"{where[0].upper() + where[1:]} have run every migration.")


env_app = typer.Typer(
    help="Your own schemas, and pull-request ones: see, clone and drop them.", invoke_without_command=True
)


@env_app.callback()
def env_show(
    ctx: typer.Context,
    project_path: Path = ProjectOption,
    environment: str | None = typer.Option(None, "--env", help="The environment. Default: the first one."),
    prefix: str | None = typer.Option(None, "--prefix", help="Look at this prefix instead of your own."),
) -> None:
    """Show the schemas your builds use, and what is in them."""
    if ctx.invoked_subcommand:
        return
    from murakumo.environments import EnvError, connect, describe, developer, prefix_for

    project = open_project(project_path)
    warehouse = project.manifest.targets.warehouse.type
    name = environment or (project.manifest.targets.environments or ["dev"])[0]
    chosen = prefix_for(project, name, warehouse, prefix)
    where = f"the schemas under [bold]{chosen}[/bold]." if chosen else "the shared schemas (no prefix)."
    console.print(f"You are [bold]{developer()}[/bold]; {name} builds go to {where}")
    try:
        connection = connect(project)
    except EnvError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    try:
        for row in describe(project, connection, chosen):
            state = f"{row['tables']} table(s)" if row["exists"] else "[dim]not created yet[/dim]"
            console.print(f"  {row['schema']:<32} {state}")
    finally:
        connection.close()


@env_app.command("clone")
def env_clone(
    prefix: str = typer.Option(..., "--prefix", help="The prefix to copy into, such as pr_42_ or dev_ana_."),
    source: str = typer.Option("", "--from-prefix", help="Copy these schemas instead of the shared ones."),
    project_path: Path = ProjectOption,
) -> None:
    """Copy the shared schemas into a prefix: zero-copy clones on Snowflake, table copies on DuckDB.

    Then `murakumo migrate --apply --prefix X` runs the branch's new migrations there, and
    `murakumo build --prefix X` builds and tests on real data, before anything reaches production.
    """
    from murakumo.environments import EnvError, clone, connect

    project = open_project(project_path)
    try:
        connection = connect(project)
        try:
            copied = clone(project, connection, prefix, source)
        finally:
            connection.close()
    except EnvError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    for line in copied:
        console.print(f"  {line}")
    console.print(
        f"Cloned {len(copied)} schema(s)." if copied else "Nothing to clone: the source schemas do not exist."
    )


@env_app.command("drop")
def env_drop(
    prefix: str | None = typer.Option(None, "--prefix", help="The prefix to drop. Default: your own."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask first."),
    project_path: Path = ProjectOption,
) -> None:
    """Drop every project schema under a prefix (your own by default). The shared schemas are never dropped."""
    from murakumo.environments import EnvError, connect, drop, prefix_for

    project = open_project(project_path)
    warehouse = project.manifest.targets.warehouse.type
    name = project.manifest.collaboration.personal_environment
    chosen = prefix if prefix is not None else prefix_for(project, name, warehouse)
    if not chosen:
        fail("name a prefix, such as --prefix pr_42_: your builds use the shared schemas, which are never dropped")
        raise typer.Exit(1)
    if not yes and not typer.confirm(f"Drop every schema under {chosen} and everything in them?"):
        raise typer.Exit(1)
    try:
        connection = connect(project)
        try:
            dropped = drop(project, connection, chosen)
        finally:
            connection.close()
    except EnvError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    console.print(f"Dropped {', '.join(dropped)}." if dropped else f"Nothing under {chosen} to drop.")


def runs_command(
    project_path: Path = ProjectOption,
    prefix: str | None = typer.Option(None, "--prefix", help="Read this environment's audit schema."),
    environment: str | None = typer.Option(None, "--env", help="Default: the first environment."),
    limit: int = typer.Option(10, help="How many runs to show."),
) -> None:
    """The latest runs, loads and failed tests, from the audit tables every run writes."""
    from rich.table import Table

    from murakumo.environments import EnvError, connect, prefix_for

    project = open_project(project_path)
    warehouse = project.manifest.targets.warehouse.type
    name = environment or (project.manifest.targets.environments or ["dev"])[0]
    chosen = prefix_for(project, name, warehouse, prefix)
    try:
        connection = connect(project)
    except EnvError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    audit = f"{chosen}audit" if warehouse is Warehouse.DUCKDB else f"{connection.database}.{chosen}audit"
    try:
        if f"{chosen}audit" not in connection.schemas():
            console.print(f"No runs recorded yet in {chosen}audit: build the project first (`murakumo build`).")
            return
        days = connection.execute(f"select * from {audit}.daily_summary order by day desc limit 7", fetch=True)
        runs = connection.execute(
            "select started_at, engine, environment, schedule, status, models_ok, models_failed, tests_passed, "
            f"tests_failed from {audit}.run_log order by started_at desc limit {int(limit)}",
            fetch=True,
        )
        loads = connection.execute(
            f"select loaded_at, table_name, rows_ingested, rows_rejected, reason from {audit}.row_counts "
            f"order by loaded_at desc limit {int(limit)}",
            fetch=True,
        )
        failed = connection.execute(
            f"select d.model, d.test, d.failures from {audit}.dq_results d join ("
            f"select run_id from {audit}.run_log order by started_at desc limit 1) r on d.run_id = r.run_id "
            "where d.status in ('fail', 'error') order by d.model, d.test",
            fetch=True,
        )
    finally:
        connection.close()

    def table(title: str, columns: list[str], rows: list[tuple]) -> None:
        view = Table(title=title, title_justify="left", box=None, pad_edge=False)
        for column in columns:
            view.add_column(column)
        for row in rows:
            view.add_row(*["" if v is None else str(v)[:60] for v in row])
        console.print(view)

    table("By day", ["Day", "Runs", "Rows in", "Rejected", "Models failed", "Tests failed"], days)
    table("Runs", ["Started", "Engine", "Env", "Schedule", "Status", "Models ok", "Failed", "Tests ok", "Failed"], runs)
    table("Loads", ["Loaded", "Table", "Rows in", "Rejected", "Why rejected"], loads)
    if failed:
        table("Failed tests in the last run", ["Model", "Test", "Failing rows"], failed)


def backfill_command(
    table: str = typer.Argument(..., help="A cumulative table."),
    start: str = typer.Option(..., "--from", help="The first day to add, YYYY-MM-DD."),
    end: str = typer.Option(..., "--to", help="The last day to add, YYYY-MM-DD."),
    project_path: Path = ProjectOption,
    environment: str | None = typer.Option(None, "--env"),
    prefix: str | None = typer.Option(None, "--prefix", help="Build under this schema prefix."),
) -> None:
    """Build a cumulative table day by day, from one day to another, as its daily runs would have."""
    from datetime import date, timedelta

    from murakumo.environments import prefix_for
    from murakumo.generate.verify import build
    from murakumo.importers.database import SourceUnavailable
    from murakumo.ir.model import EntityKind

    project = open_project(project_path)
    entity = project.model.entity(table)
    if entity is None or entity.kind is not EntityKind.CUMULATIVE:
        fail(f"{table} is not a cumulative table")
        raise typer.Exit(1)
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        fail("write the days as YYYY-MM-DD")
        raise typer.Exit(1) from None
    if last < first or (last - first).days > 3660:
        fail("--to must be on or after --from, and within ten years of it")
        raise typer.Exit(1)
    warehouse = project.manifest.targets.warehouse.type
    name = environment or (project.manifest.targets.environments or ["dev"])[0]
    schema_prefix = prefix_for(project, name, warehouse, prefix)
    day = first
    while day <= last:
        try:
            result = build(
                project,
                environment=environment,
                schema_prefix=schema_prefix,
                select=entity.name,
                dbt_vars={"run_date": day.isoformat()},
            )
        except SourceUnavailable as exc:
            fail(str(exc))
            raise typer.Exit(1) from None
        if result.error or not result.ok:
            fail(f"{day}: {result.error or 'a model or test failed'}")
            raise typer.Exit(1)
        console.print(f"  {day}: built")
        day += timedelta(days=1)
    console.print(f"[green]Backfilled[/green] {entity.name} from {first} to {last}.")
