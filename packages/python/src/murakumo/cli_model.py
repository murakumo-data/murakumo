"""``murakumo import``, ``add``, ``rename`` and ``remove``: change the model from the command line."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer
from rich.markup import escape

from murakumo import edit
from murakumo.console import console, fail, print_diagnostics
from murakumo.diagnostics import Diagnostic, Level
from murakumo.importers import SourceImport, import_declared, merge
from murakumo.importers.base import MergeReport, safe_name
from murakumo.importers.database import SourceUnavailable
from murakumo.ir.model import Cardinality, Classification, EntityKind, Layer, SourceType
from murakumo.project import Project, find_root, load_project, save_model
from murakumo.validate import validate_project

import_app = typer.Typer(
    help="Import metadata from dictionaries, databases, files and APIs. "
    "With no subcommand, imports every source in murakumo.yaml that has a from: line.",
    invoke_without_command=True,
)
add_app = typer.Typer(help="Add a source, domain, entity, column or relationship to the model.", no_args_is_help=True)
remove_app = typer.Typer(help="Remove a domain, entity or column from the model.", no_args_is_help=True)
rename_app = typer.Typer(
    help="Rename a domain, entity or column, and every reference to it in the model.", no_args_is_help=True
)
move_app = typer.Typer(help="Change the order of an entity's columns.", no_args_is_help=True)

ProjectOption = typer.Option(Path("."), "--project", "-p", help="Project folder, or any folder inside it.")
DryRunOption = typer.Option(False, "--dry-run", help="Show what would change without writing anything.")
PruneOption = typer.Option(False, "--prune", help="Also remove tables and foreign keys the source no longer has.")


def open_project(path: Path) -> Project:
    """Load a project for editing. Refuses if any file failed to load, so nothing is overwritten."""
    root = find_root(path)
    if root is None:
        fail(f"no murakumo.yaml in {path.resolve()} or above; run `murakumo init` first")
        raise typer.Exit(1)
    result = load_project(root)
    blocking = [d for d in result.diagnostics if d.level is Level.ERROR]
    if result.project is None or blocking:
        print_diagnostics(blocking, root)
        fail("fix the errors above before changing the model")
        raise typer.Exit(1)
    return result.project


def finish(project: Project, prune: bool = False, extra: list[Diagnostic] | None = None) -> None:
    """Save, then validate and show what is left to fix."""
    save_model(project, prune=prune)
    diagnostics = (extra or []) + validate_project(project)
    print_diagnostics(diagnostics, project.root)
    errors = sum(d.level is Level.ERROR for d in diagnostics)
    warnings = len(diagnostics) - errors
    if diagnostics:
        console.print(f"Saved. The model has {errors} error(s) and {warnings} warning(s).")
    else:
        console.print("Saved. The model is valid.")


def _guarded(action: Callable[[], list[SourceImport]]) -> list[SourceImport]:
    try:
        return action()
    except (SourceUnavailable, ValueError, OSError) as exc:
        fail(str(exc))
        raise typer.Exit(1) from None


def _print_report(report: MergeReport) -> None:
    lines: list[str] = []
    if report.added_source:
        lines.append(f"[green]+[/green] source {report.source}")
    lines += [f"[green]+[/green] table {name}" for name in report.added]
    lines += [f"[yellow]~[/yellow] {escape(change)}" for change in report.changed]
    lines += [f"[red]-[/red] column {name}" for name in report.removed_columns]
    lines += [f"[red]-[/red] table {name}" for name in report.removed]
    lines += [f"[green]+[/green] relationship {escape(r)}" for r in report.added_relationships]
    lines += [f"[red]-[/red] relationship {escape(r)}" for r in report.removed_relationships]
    lines += [
        f"[dim]?[/dim] table {name} is no longer in the source; kept (use --prune to remove)" for name in report.missing
    ]
    console.print(f"[bold]{report.source}[/bold]: " + ("no changes" if not lines else f"{len(lines)} change(s)"))
    for line in lines:
        console.print(f"  {line}")


def apply_imports(project: Project, imports: list[SourceImport], dry_run: bool, prune: bool) -> None:
    diagnostics: list[Diagnostic] = []
    changed = False
    for imported in imports:
        report = merge(project, imported, prune=prune)
        _print_report(report)
        diagnostics += report.diagnostics
        changed |= report.has_changes
    if any(d.level is Level.ERROR for d in diagnostics) or dry_run:
        print_diagnostics(diagnostics, project.root)
        if dry_run:
            console.print("Dry run: nothing was written.")
        else:
            fail("nothing was written: fix the import errors above")
            raise typer.Exit(1)
        return
    if not changed:
        print_diagnostics(diagnostics, project.root)
        console.print("The model is already up to date.")
        return
    finish(project, prune=prune, extra=diagnostics)


# murakumo import ------------------------------------------------------------------------------


@import_app.callback()
def import_declared_sources(
    ctx: typer.Context,
    project_path: Path = ProjectOption,
    dry_run: bool = DryRunOption,
    prune: bool = PruneOption,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    project = open_project(project_path)
    declared = [s for s in project.manifest.sources if s.from_]
    if not declared:
        fail("no sources with a from: line in murakumo.yaml; use a subcommand, for example `murakumo import csv`")
        console.print("Run `murakumo import --help` to see them.")
        raise typer.Exit(1)
    imports: list[SourceImport] = []
    for declaration in declared:
        imports += _guarded(lambda d=declaration: import_declared(d, project.root))
    apply_imports(project, imports, dry_run, prune)


def _dictionary(path: Path, source: str | None, sheet: str | None, project_path: Path, dry_run: bool, prune: bool):
    from murakumo.importers.dictionary import import_dictionary

    project = open_project(project_path)
    imports = _guarded(lambda: import_dictionary(path, source=source, sheet=sheet))
    apply_imports(project, imports, dry_run, prune)


@import_app.command("csv")
def import_csv(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, help="The dictionary CSV file."),
    source: str | None = typer.Option(
        None, help="Source name. Defaults to the source_system column, then the file name."
    ),
    project_path: Path = ProjectOption,
    dry_run: bool = DryRunOption,
    prune: bool = PruneOption,
) -> None:
    """Import a data dictionary from a CSV file (one row per column)."""
    _dictionary(path, source, None, project_path, dry_run, prune)


@import_app.command("excel")
def import_excel(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, help="The dictionary workbook (.xlsx)."),
    sheet: str | None = typer.Option(None, help="Read only this sheet. By default every dictionary sheet is read."),
    source: str | None = typer.Option(
        None, help="Source name. Defaults to the source_system column, then the file name."
    ),
    project_path: Path = ProjectOption,
    dry_run: bool = DryRunOption,
    prune: bool = PruneOption,
) -> None:
    """Import a data dictionary from an Excel workbook."""
    _dictionary(path, source, sheet, project_path, dry_run, prune)


@import_app.command("database")
def import_database(
    url: str = typer.Argument(..., help="duckdb:///file.duckdb, postgresql://user@host/db, or env:VARIABLE."),
    source: str = typer.Option(..., help="Name for this source system."),
    schema: list[str] = typer.Option(None, help="Only these schemas. Repeat for several. Default: all."),
    project_path: Path = ProjectOption,
    dry_run: bool = DryRunOption,
    prune: bool = PruneOption,
) -> None:
    """Read tables, columns, keys and comments from a live database (metadata only, never rows)."""
    from murakumo.importers.database import introspect

    project = open_project(project_path)
    imports = _guarded(lambda: [introspect(url, safe_name(source, "source"), schemas=schema or None)])
    apply_imports(project, imports, dry_run, prune)


@import_app.command("file")
def import_file_command(
    location: str = typer.Argument(..., help="Path, glob or https:// URL of a Parquet, CSV or JSON file."),
    source: str = typer.Option(..., help="Source system the file belongs to."),
    name: str | None = typer.Option(None, help="Table name. Defaults to the file name without dates."),
    format: str | None = typer.Option(None, "--format", help="parquet, csv or json, if the extension does not say."),
    project_path: Path = ProjectOption,
    dry_run: bool = DryRunOption,
) -> None:
    """Read a data file's schema and add it as a table."""
    from murakumo.importers.files import import_file

    project = open_project(project_path)
    imports = _guarded(lambda: [import_file(location, safe_name(source, "source"), name=name, fmt=format)])
    apply_imports(project, imports, dry_run, prune=False)


@import_app.command("openapi")
def import_openapi_command(
    location: str = typer.Argument(..., help="Path or URL of an OpenAPI (or Swagger 2) document, JSON or YAML."),
    source: str = typer.Option(..., help="Name for this API source."),
    only: list[str] = typer.Option(None, "--schema", help="Only these schemas. Repeat for several."),
    project_path: Path = ProjectOption,
    dry_run: bool = DryRunOption,
    prune: bool = PruneOption,
) -> None:
    """Add one table per object schema in an OpenAPI spec."""
    from murakumo.importers.api import import_openapi

    project = open_project(project_path)
    imports = _guarded(lambda: [import_openapi(location, safe_name(source, "source"), only=only or None)])
    apply_imports(project, imports, dry_run, prune)


@import_app.command("json")
def import_json_command(
    location: str = typer.Argument(..., help="Path or URL of a sample JSON response."),
    source: str = typer.Option(..., help="Name for this API source."),
    name: str = typer.Option(..., help="Table name for these records."),
    path: str | None = typer.Option(None, "--path", help="Dotted path to the rows, such as data.items or hourly."),
    project_path: Path = ProjectOption,
    dry_run: bool = DryRunOption,
) -> None:
    """Infer a table from a sample JSON response: a list of objects, one object, or columnar arrays."""
    from murakumo.importers.api import import_json

    project = open_project(project_path)
    imports = _guarded(lambda: [import_json(location, safe_name(source, "source"), name, path=path)])
    apply_imports(project, imports, dry_run, prune=False)


# murakumo add / remove ------------------------------------------------------------------------


def _edit(project_path: Path, change: Callable[[Project], str], prune: bool = False) -> None:
    project = open_project(project_path)
    try:
        message = change(project)
    except edit.EditError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    console.print(message)
    finish(project, prune=prune)


@add_app.command("source")
def add_source(
    name: str,
    type: SourceType = typer.Option(SourceType.DATABASE, "--type", help="database, file, api, stream or manual."),
    connection: str | None = typer.Option(None, help="Connection string; use env:NAME for anything secret."),
    description: str | None = typer.Option(None),
    project_path: Path = ProjectOption,
) -> None:
    """Add a source system."""
    _edit(project_path, lambda p: f"Added source {edit.add_source(p, name, type, connection, description).name}.")


@add_app.command("domain")
def add_domain(
    name: str,
    owner: str | None = typer.Option(None, help="The team or person responsible for it."),
    description: str | None = typer.Option(None),
    project_path: Path = ProjectOption,
) -> None:
    """Add a domain to project.domains in murakumo.yaml. Put entities in it with --domain."""
    _edit(project_path, lambda p: f"Added domain {edit.add_domain(p, name, owner, description).name}.")


@add_app.command("entity")
def add_entity(
    name: str,
    layer: Layer = typer.Option(Layer.RAW, help="raw, staging, intermediate, marts or semantic."),
    kind: EntityKind = typer.Option(EntityKind.TABLE, help="table, fact, dimension, bridge, hub, link, satellite..."),
    source: str | None = typer.Option(None, help="Source system, for raw tables."),
    domain: str | None = typer.Option(None),
    description: str | None = typer.Option(None),
    project_path: Path = ProjectOption,
) -> None:
    """Add an entity (a table). Add its columns with `murakumo add column`."""
    _edit(
        project_path,
        lambda p: f"Added entity {edit.add_entity(p, name, layer, kind, source, domain, description).name}.",
    )


@add_app.command("column")
def add_column(
    entity: str,
    name: str,
    type: str = typer.Argument(..., help="Logical type: string, integer, decimal(10,2), timestamp, money..."),
    primary_key: bool = typer.Option(False, "--pk", help="Part of the primary key (implies not null)."),
    not_null: bool = typer.Option(False, "--not-null"),
    pii: bool = typer.Option(False, "--pii", help="Holds personal data."),
    classification: Classification | None = typer.Option(None),
    description: str | None = typer.Option(None),
    project_path: Path = ProjectOption,
) -> None:
    """Add a column to an entity."""

    def change(project: Project) -> str:
        nullable = False if (not_null or primary_key) else None
        column = edit.add_column(project, entity, name, type, primary_key, nullable, description, pii, classification)
        return f"Added {entity}.{column.name} ({column.type})."

    _edit(project_path, change)


@add_app.command("relationship")
def add_relationship(
    from_entity: str = typer.Argument(..., metavar="FROM", help="The entity holding the foreign key."),
    to_entity: str = typer.Argument(..., metavar="TO", help="The entity it points to."),
    on: list[str] = typer.Option(..., "--on", help="Join as from_column=to_column. Repeat for composite keys."),
    cardinality: Cardinality = typer.Option(Cardinality.MANY_TO_ONE),
    name: str | None = typer.Option(None),
    optional: bool = typer.Option(False, "--optional", help="A row may have no match."),
    role: str | None = typer.Option(None, help="Tells apart two links between the same tables."),
    project_path: Path = ProjectOption,
) -> None:
    """Add a relationship between two entities."""

    def change(project: Project) -> str:
        keys = [edit.parse_join(text) for text in on]
        rel = edit.add_relationship(project, from_entity, to_entity, keys, cardinality, name, optional, role)
        return f"Added relationship {rel.from_} -> {rel.to}."

    _edit(project_path, change)


@remove_app.command("entity")
def remove_entity(name: str, project_path: Path = ProjectOption) -> None:
    """Remove an entity, its file and the relationships that use it."""

    def change(project: Project) -> str:
        removed = edit.remove_entity(project, name)
        extra = f" and {len(removed)} relationship(s)" if removed else ""
        return f"Removed {name}{extra}."

    _edit(project_path, change, prune=True)


@remove_app.command("column")
def remove_column(entity: str, name: str, project_path: Path = ProjectOption) -> None:
    """Remove a column from an entity."""
    _edit(project_path, lambda p: f"Removed {entity}.{edit.remove_column(p, entity, name).name}.")


@remove_app.command("domain")
def remove_domain(name: str, project_path: Path = ProjectOption) -> None:
    """Remove a domain that no entity uses."""
    _edit(project_path, lambda p: f"Removed domain {edit.remove_domain(p, name).name}.")


@rename_app.command("domain")
def rename_domain(name: str, new_name: str, project_path: Path = ProjectOption) -> None:
    """Rename a domain, and move every entity in it to the new name."""

    def change(project: Project) -> str:
        count = len(edit.domain_usage(project, name))
        renamed = edit.update_domain(project, name, {"name": new_name})
        return f"Renamed domain {name} to {renamed.name} ({count} entities)."

    _edit(project_path, change)


@rename_app.command("entity")
def rename_entity(name: str, new_name: str, project_path: Path = ProjectOption) -> None:
    """Rename an entity. Relationships, mappings and metrics that use it follow, and so does its file."""
    _edit(project_path, lambda p: f"Renamed {name} to {edit.rename_entity(p, name, new_name).name}.", prune=True)


@rename_app.command("column")
def rename_column(entity: str, name: str, new_name: str, project_path: Path = ProjectOption) -> None:
    """Rename a column. Keys, grain, tests, mappings and relationships that use it follow."""
    _edit(
        project_path,
        lambda p: f"Renamed {entity}.{name} to {edit.rename_column(p, entity, name, new_name).name}.",
    )


@move_app.command("column")
def move_column(
    entity: str,
    name: str,
    after: str | None = typer.Option(None, "--after", help="Put it straight after this column."),
    before: str | None = typer.Option(None, "--before", help="Put it straight before this column."),
    project_path: Path = ProjectOption,
) -> None:
    """Move a column. With neither --after nor --before, it moves to the start.

    Generated tables and models list columns in this order. Tables that already exist keep theirs:
    no warehouse reorders columns in place, and nothing needs to read them by position.
    """

    def change(project: Project) -> str:
        moved = edit.move_column(project, entity, name, after, before)
        return f"{moved.name}: {', '.join(a.name for a in moved.attributes)}"

    _edit(project_path, change)
