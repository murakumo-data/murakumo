"""Terminal output shared by the CLI commands."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from murakumo.diagnostics import Diagnostic, Level

console = Console()
err_console = Console(stderr=True)


def print_diagnostics(diagnostics: list[Diagnostic], base: Path) -> None:
    if not diagnostics:
        return
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("")
    table.add_column("Code")
    table.add_column("Where", overflow="fold")
    table.add_column("Problem", overflow="fold")
    for d in sorted(diagnostics, key=lambda d: (d.level is not Level.ERROR, str(d.file), d.code)):
        mark = "[red]error[/red]" if d.level is Level.ERROR else "[yellow]warn[/yellow]"
        table.add_row(mark, d.code, escape(d.location(base)), escape(d.message))
    console.print(table)


def fail(message: str) -> None:
    err_console.print(f"[red]error[/red] {escape(message)}")


_MARKS = {"ok": "[green]✓[/green]", "todo": "[red]✗[/red]", "warn": "[yellow]![/yellow]", "info": "[blue]•[/blue]"}


def print_checks(checks, compact: bool = False) -> None:
    """The setup checklist, grouped by area. ``compact`` leaves out what is already done."""
    area = None
    for check in checks:
        if compact and check.status.value == "ok":
            continue
        if check.area != area:
            area = check.area
            console.print(f"\n[bold]{escape(area)}[/bold]")
        console.print(f"  {_MARKS[check.status.value]} {escape(check.title)}")
        if check.detail:
            console.print(f"    [dim]{escape(check.detail)}[/dim]")
        for step in check.steps:
            console.print(f"    [cyan]→[/cyan] {escape(step)}", soft_wrap=True)
