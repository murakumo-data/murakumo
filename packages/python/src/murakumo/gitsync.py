"""Keeping a project in step with its team, through git.

Everyone works on a branch and brings in the parent branch (``collaboration.base_branch`` in
murakumo.yaml: main, dev, ...) as it moves on. ``sync`` does that safely:

1. it refuses while the project has uncommitted changes, so nothing of yours is mixed into a merge;
2. it fetches the parent branch and says what changes in the model, in model terms;
3. it merges. Conflicts in model files are yours to resolve (``sync --continue`` finishes after);
   conflicts in ``generated/`` are not real conflicts: the files are generated again from the
   merged model;
4. migrations: when both sides wrote migrations since they split, yours were planned against a
   state the parent branch has moved on from, and may share its numbers. Yours are removed and
   one new migration is planned from the merged model against the parent's state, numbered after
   the parent's last one;
5. it regenerates ``generated/`` and commits the merge.
"""

from __future__ import annotations

import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

from murakumo.ir.model import Model
from murakumo.manifest import MANIFEST_FILE
from murakumo.modeldiff import Change, diff
from murakumo.project import Project, load_project

GENERATED = "generated/"
MIGRATIONS = "migrations/"


class GitError(Exception):
    pass


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    try:
        done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise GitError("git is not installed; see `murakumo setup`") from None
    if check and done.returncode != 0:
        message = (done.stderr or done.stdout).strip().splitlines()
        raise GitError(f"git {args[0]} failed: {message[-1] if message else 'no output'}")
    return done


def repository(project: Project) -> Path:
    done = _git(project.root, "rev-parse", "--show-toplevel", check=False)
    if done.returncode != 0:
        raise GitError("the project is not in a git repository yet: run `git init` in it first")
    return Path(done.stdout.strip()).resolve()


def _inside(project: Project, root: Path) -> str:
    """The project's folder inside the repository, as git names paths ('' at the root)."""
    relative = project.root.resolve().relative_to(root).as_posix()
    return "" if relative == "." else relative + "/"


@dataclass
class Where:
    root: Path
    folder: str  # the project's folder in the repository, with a trailing / ('' at the root)
    branch: str
    base: str  # the parent branch's name
    ref: str  # what to merge: origin/main, or main without a remote

    def path(self, inside: str) -> str:
        return self.folder + inside


def where(project: Project) -> Where:
    root = repository(project)
    settings = project.manifest.collaboration
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).stdout.strip() or "HEAD"
    remotes = _git(root, "remote").stdout.split()
    ref = f"{settings.remote}/{settings.base_branch}" if settings.remote in remotes else settings.base_branch
    return Where(root, _inside(project, root), branch, settings.base_branch, ref)


def _exists(w: Where, ref: str) -> bool:
    return _git(w.root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).returncode == 0


def model_at(project: Project, w: Where, ref: str) -> Model:
    """The model as it is at a commit (empty if the project did not exist there)."""
    if not _exists(w, ref):
        return Model()
    folder = w.folder.rstrip("/") or "."
    archive = subprocess.run(["git", "archive", ref, "--", folder], cwd=w.root, capture_output=True, timeout=120)
    if archive.returncode != 0:
        return Model()
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=BytesIO(archive.stdout)) as tar:
            tar.extractall(tmp, filter="data")
        result = load_project(Path(tmp) / folder)
        return result.project.model if result.project else Model()


def merge_base(w: Where) -> str | None:
    done = _git(w.root, "merge-base", "HEAD", w.ref, check=False)
    return done.stdout.strip() or None


def uncommitted(w: Where) -> list[str]:
    lines = _git(w.root, "status", "--porcelain", "--", w.folder or ".").stdout.splitlines()
    return [line[3:] for line in lines if line.strip()]


def conflicts(w: Where) -> list[str]:
    return _git(w.root, "diff", "--name-only", "--diff-filter=U").stdout.split()


def merging(w: Where) -> bool:
    return _git(w.root, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode == 0


def ahead_behind(w: Where) -> tuple[int, int]:
    if not _exists(w, w.ref):
        return 0, 0
    counts = _git(w.root, "rev-list", "--left-right", "--count", f"HEAD...{w.ref}").stdout.split()
    return int(counts[0]), int(counts[1])


def fetch(w: Where, remote: str) -> None:
    if remote in _git(w.root, "remote").stdout.split():
        _git(w.root, "fetch", "--quiet", remote, w.base)


def status(project: Project, refresh: bool = False) -> dict[str, Any]:
    """Branch, parent branch, how far apart they are, and the model changes on each side."""
    w = where(project)
    if refresh:
        try:
            fetch(w, project.manifest.collaboration.remote)
        except GitError:
            pass  # offline: show what is known
    ahead, behind = ahead_behind(w)
    base = merge_base(w)
    head = model_at(project, w, "HEAD")
    local = [c.as_dict() for c in diff(head, project.model)]
    incoming = (
        [c.as_dict() for c in diff(model_at(project, w, base), model_at(project, w, w.ref))] if base and behind else []
    )
    return {
        "branch": w.branch,
        "base": w.base,
        "ref": w.ref,
        "on_base": w.branch == w.base,
        "ahead": ahead,
        "behind": behind,
        "uncommitted": uncommitted(w),
        "local_changes": local,
        "incoming_changes": incoming,
        "merging": merging(w),
        "conflicts": conflicts(w),
    }


# sync -------------------------------------------------------------------------------------------


@dataclass
class SyncResult:
    state: str  # up_to_date | synced | conflicts
    incoming: list[Change] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    migrations: list[str] = field(default_factory=list)  # what happened to migrations, in words
    generated: int = 0  # files regenerated
    commit: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "incoming": [c.as_dict() for c in self.incoming],
            "conflicts": self.conflicts,
            "migrations": self.migrations,
            "generated": self.generated,
            "commit": self.commit,
        }


def _added(w: Where, since: str, until: str, inside: str) -> list[str]:
    out = _git(w.root, "diff", "--name-only", "--diff-filter=A", since, until, "--", w.path(inside)).stdout
    return [p for p in out.split() if p.endswith(".sql")]


def sync(project: Project, *, resume: bool = False) -> SyncResult:
    """Bring the parent branch into this one. See the module docs for each step."""
    w = where(project)
    if merging(w) and not resume:
        raise GitError("a merge is in progress: resolve it, then run `murakumo sync --continue` (or `--abort`)")
    if resume:
        if not merging(w):
            raise GitError("no sync to continue")
        left = conflicts(w)
        if left:
            raise GitError(f"still unresolved: {', '.join(left)}; resolve them, `git add` them, then continue")
        result = SyncResult("synced")
        _rebuild_migrations(project, w, *_sides(w, "MERGE_HEAD"), result)
        return _finish(project, w, result)

    if uncommitted(w):
        raise GitError(
            "the project has uncommitted changes: commit them first, so nothing of yours is mixed into the merge"
        )
    fetch(w, project.manifest.collaboration.remote)
    if not _exists(w, w.ref):
        raise GitError(f"there is no branch {w.ref} to sync with; set collaboration.base_branch in {MANIFEST_FILE}")
    if w.branch == w.base and w.ref == w.base:
        raise GitError(f"you are on {w.base} itself; sync brings {w.base} into another branch")
    _, behind = ahead_behind(w)
    if behind == 0:
        return SyncResult("up_to_date")
    base = merge_base(w)
    result = SyncResult("synced", incoming=diff(model_at(project, w, base), model_at(project, w, w.ref)))

    done = _git(w.root, "merge", "--no-ff", "--no-commit", w.ref, check=False)
    if done.returncode != 0 and not merging(w):
        raise GitError(f"git merge failed: {(done.stderr or done.stdout).strip().splitlines()[-1]}")

    for path in conflicts(w):
        inside = path[len(w.folder) :] if path.startswith(w.folder) else path
        if inside.startswith(GENERATED) or (inside.startswith(MIGRATIONS) and inside.endswith("schema.json")):
            _git(w.root, "checkout", "--theirs", "--", path)  # generated again below
            _git(w.root, "add", "--", path)
    result.conflicts = conflicts(w)
    if result.conflicts:
        result.state = "conflicts"
        return result
    _rebuild_migrations(project, w, *_sides(w, "MERGE_HEAD"), result)
    return _finish(project, w, result)


def _sides(w: Where, other: str) -> tuple[list[str], list[str]]:
    """The migrations each side added since the branches split: this branch's, then the other's."""
    base = _git(w.root, "merge-base", "HEAD", other).stdout.strip()
    return _added(w, base, "HEAD", MIGRATIONS), _added(w, base, other, MIGRATIONS)


def _rebuild_migrations(project: Project, w: Where, ours: list[str], theirs: list[str], result: SyncResult) -> None:
    """Replace this branch's migrations with one planned against the parent branch's state."""
    from murakumo.migrate import plan_migration, write_migration

    folders = {Path(p).parent for p in ours} & {Path(p).parent for p in theirs}
    for folder in sorted(folders):
        mine = [p for p in ours if Path(p).parent == folder]
        title = Path(mine[0]).stem.split("_", 1)[1] if mine else "after_sync"
        state = (folder / "schema.json").as_posix()
        _git(w.root, "checkout", "MERGE_HEAD", "--", state)  # the parent's record of the tables
        for path in mine:
            (w.root / path).unlink(missing_ok=True)
            _git(w.root, "rm", "--cached", "--quiet", "--ignore-unmatch", "--", path)
        merged = load_project(project.root).project
        warehouse = folder.name
        pending = plan_migration(merged, warehouse)
        written = write_migration(merged, pending, title) if pending.changes else None
        names = ", ".join(Path(p).name for p in mine)
        if written:
            result.migrations.append(f"{names} replaced by {written.name}, planned after {w.base}'s migrations")
        else:
            result.migrations.append(f"{names} removed: {w.base} already makes those changes")


def _finish(project: Project, w: Where, result: SyncResult) -> SyncResult:
    from murakumo.generate import compare, generate, write

    merged = load_project(project.root)
    if merged.project is None:
        raise GitError("the merged model does not load; fix it, `git add` it, then `murakumo sync --continue`")
    output = generate(merged.project)
    changes = compare(output, merged.project.root)
    write(output, merged.project.root)
    result.generated = len(changes.added) + len(changes.changed) + len(changes.removed)
    _git(w.root, "add", "-A", "--", w.folder or ".")
    message = f"Sync with {w.base} (murakumo sync)"
    _git(w.root, "commit", "--quiet", "--no-edit", "-m", message)
    result.commit = _git(w.root, "rev-parse", "--short", "HEAD").stdout.strip()
    return result


def abort(project: Project) -> None:
    w = where(project)
    if not merging(w):
        raise GitError("no sync in progress")
    _git(w.root, "merge", "--abort")


def commit(project: Project, message: str) -> str:
    """Commit everything in the project's folder: model, manifest, generated code, migrations."""
    w = where(project)
    if not message.strip():
        raise GitError("write a message saying what changed")
    if not uncommitted(w):
        raise GitError("nothing to commit")
    _git(w.root, "add", "-A", "--", w.folder or ".")
    _git(w.root, "commit", "--quiet", "-m", message.strip())
    return _git(w.root, "rev-parse", "--short", "HEAD").stdout.strip()


__all__ = ["GitError", "SyncResult", "abort", "commit", "model_at", "status", "sync", "where"]
