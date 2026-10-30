"""``murakumo sync`` and ``murakumo diff``: working on one project with other people, through git."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape

from murakumo.cli_model import ProjectOption, open_project
from murakumo.console import console, fail

CLASS_STYLE = {"additive": "green", "compatible": "yellow", "breaking": "red"}


def print_changes(changes: list[dict], empty: str) -> None:
    if not changes:
        console.print(f"  [dim]{empty}[/dim]")
    for change in changes:
        style = CLASS_STYLE.get(change["class"], "white")
        console.print(f"  [{style}]{change['class']:<10}[/{style}] {escape(change['summary'])}")


def sync_command(
    project_path: Path = ProjectOption,
    resume: bool = typer.Option(False, "--continue", help="Finish a sync after resolving its conflicts."),
    stop: bool = typer.Option(False, "--abort", help="Give up a sync that has conflicts, as if it never started."),
    check: bool = typer.Option(False, "--check", help="Only say what would come in; change nothing."),
) -> None:
    """Bring in the latest from the parent branch (collaboration.base_branch, main by default).

    Shows what changes in the model, merges, regenerates the code and commits. When both branches
    wrote migrations, yours are planned again after the parent's, so their numbers never clash.
    """
    from murakumo import gitsync

    project = open_project(project_path)
    try:
        if stop:
            gitsync.abort(project)
            console.print("Sync given up; the branch is as it was.")
            return
        if check:
            info = gitsync.status(project, refresh=True)
            console.print(
                f"[bold]{info['branch']}[/bold]: {info['behind']} commit(s) behind {info['ref']}, "
                f"{info['ahead']} ahead."
            )
            console.print(f"Coming in from {info['base']}:")
            print_changes(info["incoming_changes"], "no model changes")
            return
        result = gitsync.sync(project, resume=resume)
    except gitsync.GitError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    if result.state == "up_to_date":
        console.print(f"Already up to date with {project.manifest.collaboration.base_branch}.")
        return
    if result.incoming:
        console.print(f"Brought in from {project.manifest.collaboration.base_branch}:")
        print_changes([c.as_dict() for c in result.incoming], "")
    if result.state == "conflicts":
        fail(
            "these files changed on both branches; resolve them in your editor, `git add` them, then run "
            "`murakumo sync --continue` (or `murakumo sync --abort` to give up):"
        )
        for path in result.conflicts:
            console.print(f"  {path}")
        raise typer.Exit(1)
    for note in result.migrations:
        console.print(f"Migrations: {escape(note)}")
    console.print(f"[green]Synced[/green]: regenerated {result.generated} file(s) and committed {result.commit}.")


def diff_command(
    ref: str = typer.Argument("", help="Compare with this branch or commit. Default: the parent branch."),
    project_path: Path = ProjectOption,
    output: str = typer.Option("text", "--format", help="text, json, or markdown (for a pull request's summary)."),
    fail_on: str = typer.Option(
        "", "--fail-on", help="Exit 1 if a change is at least this risky: breaking, compatible or additive."
    ),
) -> None:
    """What changed in the model compared with a branch or commit, how risky each change is, and what it reaches."""
    import json

    from murakumo import gitsync, impact
    from murakumo.modeldiff import _ORDER

    project = open_project(project_path)
    try:
        w = gitsync.where(project)
        against = ref or w.ref
        old = gitsync.model_at(project, w, against)
    except gitsync.GitError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    result = impact.report(project, old, project.model)
    if output == "json":
        typer.echo(json.dumps({"against": against, **result.as_dict()}, indent=2))
    elif output == "markdown":
        typer.echo(impact.markdown(result, against), nl=False)
    else:
        console.print(f"Model changes since [bold]{against}[/bold]:")
        if not result.impacts:
            console.print("  [dim]none[/dim]")
        for item in result.impacts:
            print_changes([item.change.as_dict()], "")
            reach = []
            if item.columns:
                reach.append(
                    f"{len(item.columns)} column(s): {', '.join(item.columns[:6])}"
                    + (" ..." if len(item.columns) > 6 else "")
                )
            if item.tests:
                reach.append(f"{len(item.tests)} test(s)")
            if item.metrics:
                reach.append("metric(s) " + ", ".join(item.metrics))
            if item.schedules:
                reach.append("schedule " + ", ".join(item.schedules))
            if reach:
                console.print(f"  {'':<10} [dim]reaches {escape('; '.join(reach))}[/dim]")
        if result.migration:
            console.print("Needs a migration: " + escape("; ".join(result.migration)))
    if fail_on and result.worst and _ORDER[result.worst] >= _ORDER.get(fail_on, 99):
        raise typer.Exit(1)
