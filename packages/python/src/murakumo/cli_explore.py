"""Exploring the data before modelling it: `murakumo preview`, `murakumo relations`, `murakumo find`
and `murakumo suggest`."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.markup import escape
from rich.table import Table

from murakumo.cli_model import ProjectOption, open_project
from murakumo.console import console, fail

EnvOption = typer.Option(None, "--env", help="The environment to read. Default: your own.")


def _rows(columns: list[str], rows: list[list]) -> None:
    table = Table(box=None, pad_edge=False)
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(*("null" if v is None else escape(str(v)) for v in row))
    console.print(table)


def preview_command(
    table: str = typer.Argument(..., help="The table to preview, as named in the model."),
    full: bool = typer.Option(False, "--full", help="Read every row of the raw tables, not a sample of each."),
    sql_only: bool = typer.Option(False, "--sql", help="Print the query instead of running it."),
    rows: int = typer.Option(20, "--rows", help="Rows to show (at most 1,000)."),
    environment: str | None = EnvOption,
    project_path: Path = ProjectOption,
) -> None:
    """A table's rows as its model would build them, before anything is built.

    The models it reads are folded into one query down to the raw tables, which need only be
    loaded (`murakumo build --raw-only`). Each raw table is read as a sample of its first 10,000
    rows, so the cost does not grow with the data; joins on samples can find fewer matches than
    the full tables would. --full reads everything, after saying what that will read.
    """
    from murakumo import explore

    project = open_project(project_path)
    try:
        if sql_only:
            sql, _ = explore.preview_sql(project, table, environment, None if full else explore.SAMPLE)
            print(sql)
            return
        if full:
            estimate = explore.estimate(project, table, environment)
            console.print(f"[dim]{estimate['text']}[/dim]")
        result = explore.preview(project, table, environment, full=full, limit=max(1, min(rows, 1000)))
    except explore.ExploreError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    _rows(result["columns"], result["rows"])
    how = "on every row" if full else f"on samples of {explore.SAMPLE:,} rows of {', '.join(result['reads'])}"
    console.print(f"[dim]{len(result['rows']):,} row(s), {how}, in {result['elapsed_ms']} ms[/dim]")


def relations_command(
    column: str = typer.Argument(..., help="TABLE.COLUMN, such as orders.customer_id."),
    check: bool = typer.Option(
        False, "--check", help="Compare the values too: a sample of each column, on the warehouse."
    ),
    wide: bool = typer.Option(False, "--wide", help="With --check: also ids with other names (slower)."),
    environment: str | None = EnvOption,
    as_json: bool = typer.Option(False, "--json", help="Print the result as JSON."),
    project_path: Path = ProjectOption,
) -> None:
    """Which raw and staging columns a column probably joins to.

    Without --check this reads only the model (names and types): no query runs. --check summarises
    each candidate from a sample (kept until its table's row count changes), skips columns whose
    values cannot overlap, and measures the overlap of the rest.
    """
    from murakumo import relations
    from murakumo.explore import ExploreError

    if "." not in column:
        fail("give the column as TABLE.COLUMN, such as orders.customer_id")
        raise typer.Exit(1)
    table, name = column.split(".", 1)
    project = open_project(project_path)
    try:
        result = (
            relations.check(project, table, name, environment, wide=wide)
            if check
            else relations.candidates(project, table, name)
        )
    except ExploreError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    if as_json:
        console.print_json(json.dumps(result, default=str))
        return
    if not check:
        listed = result["candidates"]
        strong = [c for c in listed if c["strength"] != "maybe"]
        for c in strong:
            console.print(f"  {c['entity']}.{c['column']}  [dim]{c['type']}, {escape(c['why'])}[/dim]")
        others = len(listed) - len(strong)
        if not strong:
            console.print("No column is named like it.")
        if others:
            console.print(f"[dim]{others} other id column(s) of a type that joins: --check --wide compares them.[/dim]")
        console.print("[dim]Add --check to compare the values on the warehouse.[/dim]")
        return
    for match in result["matches"]:
        overlap = match["overlap"]
        if overlap["likely"]:
            mark = "[green]likely[/green] "
        elif overlap.get("shared"):
            mark = "[cyan]shared[/cyan] "
        elif overlap.get("chance"):
            mark = "[yellow]chance?[/yellow]"
        else:
            mark = "[dim]weak[/dim]   "
        console.print(
            f"  {mark} {match['entity']}.{match['column']}: {overlap['here_in_there']:.0%} of "
            f"{result['entity']}.{result['column']} found there, {overlap['there_in_here']:.0%} the other way; "
            f"{overlap['kind']}"
        )
    for skipped in result["ruled_out"]:
        console.print(f"  [dim]no      {skipped['entity']}.{skipped['column']}: {skipped['reason']}[/dim]")
    if not result["matches"] and not result["ruled_out"]:
        console.print("No candidate to compare.")
    if not result["wide"] and result["others"]:
        console.print(f"[dim]{result['others']} id column(s) with other names were not compared: add --wide.[/dim]")
    console.print(f"[dim]Compared samples of up to {result['sample']:,} rows per table.[/dim]")


def find_command(
    value: str = typer.Argument(..., help="The value to look for, such as a customer id."),
    every_column: bool = typer.Option(
        False, "--every-column", help="Search every text and number column, not only ids."
    ),
    environment: str | None = EnvOption,
    project_path: Path = ProjectOption,
) -> None:
    """Which raw and staging tables hold a value: only the id columns it could be in (by type, and
    inside each column's known range), each searched until its first match."""
    from murakumo import relations
    from murakumo.explore import ExploreError

    try:
        result = relations.find(open_project(project_path), value, environment, ids_only=not every_column)
    except ExploreError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    for hit in result["found"]:
        console.print(f"  {hit['entity']}.{hit['column']}")
    if not result["found"]:
        console.print(f"{value!r} is in none of the columns searched.")
    console.print(
        f"[dim]Searched {result['searched']} column(s); {result['skipped']} skipped, the value being outside "
        "their range.[/dim]"
    )


def suggest_command(
    table: str = typer.Argument(..., help="The table, as named in the model."),
    environment: str | None = EnvOption,
    project_path: Path = ProjectOption,
) -> None:
    """Queries worth running on a table first, written from the model: its key, dates, categories
    and joins. Run one with `murakumo query`."""
    from murakumo import explore

    try:
        found = explore.suggested(open_project(project_path), table, environment)
    except explore.ExploreError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    for item in found:
        console.print(f"[bold]{escape(item['title'])}[/bold]  [dim]{escape(item['plain'])}[/dim]")
        console.print(f"  {escape(item['sql'])}\n")
