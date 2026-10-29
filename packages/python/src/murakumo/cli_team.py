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
    output: str = typer.Option("text", "--format", help="text or json."),
) -> None:
    """What changed in the model compared with a branch or commit, change by change, with how risky each is."""
    import json

    from murakumo import gitsync
    from murakumo.modeldiff import diff

    project = open_project(project_path)
    try:
        w = gitsync.where(project)
        against = ref or w.ref
        changes = [c.as_dict() for c in diff(gitsync.model_at(project, w, against), project.model)]
    except gitsync.GitError as exc:
        fail(str(exc))
        raise typer.Exit(1) from None
    if output == "json":
        typer.echo(json.dumps({"against": against, "changes": changes}, indent=2))
        return
    console.print(f"Model changes since [bold]{against}[/bold]:")
    print_changes(changes, "none")
