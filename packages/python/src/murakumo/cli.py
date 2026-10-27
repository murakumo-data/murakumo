"""The `murakumo` command-line interface."""

from __future__ import annotations

import typer
from rich.console import Console

from murakumo import __version__

app = typer.Typer(
    name="murakumo",
    help="Model-first data engineering: design the logical model, generate the platform.",
    no_args_is_help=True,
)
console = Console()


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
