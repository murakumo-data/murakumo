"""The git flow: work starts on a branch of its own and moves up through the environments.

New work (``feature/...``, ``fix/...``, ``chore/...``) starts from the first environment's branch,
``dev``, and comes back to it through a pull request. Each environment's branch is promoted to the
next by a pull request too: ``dev`` into ``stage``, ``stage`` into ``main``. Merging into an
environment's branch deploys to that environment (the CI workflow does it). A ``hotfix/...`` can go
straight to the last branch, and back into the first.

The branches come from murakumo.yaml: each environment's ``branch``, in the order the
environments are listed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from murakumo.gitsync import GitError, _exists, _git, model_at, where
from murakumo.modeldiff import diff
from murakumo.project import Project

KINDS = ("feature", "fix", "chore", "hotfix")
SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,60}$")


@dataclass(frozen=True)
class Stage:
    environment: str
    branch: str


def flow(project: Project) -> list[Stage]:
    """The environments that deploy from a branch, in order: dev, stage, prod."""
    return [Stage(e.name, e.branch) for e in project.manifest.targets.environments if e.branch]


def environment_for(project: Project, branch: str) -> str:
    """The environment a branch deploys to (or a pull request into it previews against); the last
    environment for any other branch."""
    for stage in flow(project):
        if stage.branch == branch:
            return stage.environment
    return project.manifest.targets.names[-1]


def check(project: Project, head: str, base: str) -> list[str]:
    """What is wrong with a pull request from ``head`` into ``base`` under the flow (nothing: [])."""
    stages = flow(project)
    branches = [s.branch for s in stages]
    if not stages:
        return []
    kind = head.split("/", 1)[0] if "/" in head else ""
    if base not in branches:
        return [f"pull requests go into one of {', '.join(branches)}, not {base}"]
    position = branches.index(base)
    if position == 0:
        if head in branches:
            return [f"{head} is promoted into the branch after it, not back into {base}"]
        if kind not in KINDS:
            return [f"name the branch {' / '.join(k + '/...' for k in KINDS[:3])}: {head} says neither what nor why"]
        return []
    expected = branches[position - 1]
    if head == expected or (kind == "hotfix" and position == len(branches) - 1):
        return []
    return [f"{base} takes {expected} (promoted once it is tested there), not {head}"]


def _remote(project: Project) -> str | None:
    w = where(project)
    remote = project.manifest.collaboration.remote
    return remote if remote in _git(w.root, "remote").stdout.split() else None


def start(project: Project, push: bool = False) -> list[str]:
    """Create the first environment's branch (dev) from main if it does not exist yet."""
    stages = flow(project)
    if not stages:
        return ["No environment in murakumo.yaml has a branch, so there is no flow to start."]
    w = where(project)
    first, last = stages[0].branch, stages[-1].branch
    remote = _remote(project)
    done: list[str] = []
    created: list[str] = []
    for stage in stages:
        if _exists(w, stage.branch) or (remote and _exists(w, f"{remote}/{stage.branch}")):
            continue
        origin = f"{remote}/{last}" if remote and _exists(w, f"{remote}/{last}") else last
        if not _exists(w, origin):
            origin = "HEAD"
        _git(w.root, "branch", stage.branch, origin)
        created.append(stage.branch)
        done.append(f"created {stage.branch} from {origin}")
        if push and remote:
            _git(w.root, "push", "--quiet", "-u", remote, stage.branch)
            done.append(f"pushed {stage.branch} to {remote}")
    if not done:
        done.append(f"{', '.join(s.branch for s in stages)} already exist; new work starts from {first}.")
    elif remote and not push:
        done.append(f"push them so the team has them: git push -u {remote} " + " ".join(created))
    return done


def new(project: Project, kind: str, name: str) -> str:
    """Start new work: a branch kind/name from the latest of the first environment's branch."""
    if kind not in KINDS:
        raise GitError(f"a branch is one of {', '.join(KINDS)}")
    slug = re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower()).strip("-")
    if not SLUG.match(slug):
        raise GitError("name it in a few words, like add-zone-codes")
    stages = flow(project)
    w = where(project)
    base = stages[-1].branch if kind == "hotfix" else (stages[0].branch if stages else w.base)
    remote = _remote(project)
    if remote:
        try:
            _git(w.root, "fetch", "--quiet", remote, base)
        except GitError:
            pass  # offline, or not pushed yet: start from what is here
    origin = f"{remote}/{base}" if remote and _exists(w, f"{remote}/{base}") else base
    if not _exists(w, origin):
        raise GitError(f"there is no {base} branch yet: run `murakumo branch start` first")
    branch = f"{kind}/{slug}"
    if _exists(w, branch):
        raise GitError(f"{branch} already exists")
    _git(w.root, "checkout", "--quiet", "-b", branch, origin)
    return branch


def _compare_url(project: Project, head: str, base: str) -> str | None:
    """Where to open the pull request, on GitHub or GitLab, from the remote's address."""
    remote = _remote(project)
    if not remote:
        return None
    url = _git(where(project).root, "remote", "get-url", remote).stdout.strip()
    match = re.match(r"^(?:https://|git@)([^/:]+)[/:](.+?)(?:\.git)?$", url)
    if not match:
        return None
    host, path = match.groups()
    if "gitlab" in host:
        return (
            f"https://{host}/{path}/-/merge_requests/new?merge_request[source_branch]={quote(head)}"
            f"&merge_request[target_branch]={quote(base)}"
        )
    return f"https://{host}/{path}/compare/{quote(base)}...{quote(head)}?expand=1"


def promotion(project: Project, branch: str) -> dict[str, Any]:
    """What promoting an environment's branch to the next one would move: the model changes, and
    where to open the pull request."""
    stages = flow(project)
    branches = [s.branch for s in stages]
    if branch not in branches[:-1]:
        raise GitError(f"promote one of {', '.join(branches[:-1])}" if len(branches) > 1 else "there is no next branch")
    target = branches[branches.index(branch) + 1]
    w = where(project)
    remote = _remote(project)
    if remote:
        for b in (branch, target):
            try:
                _git(w.root, "fetch", "--quiet", remote, b)
            except GitError:
                pass
    ref = lambda b: f"{remote}/{b}" if remote and _exists(w, f"{remote}/{b}") else b  # noqa: E731
    changes = [c.as_dict() for c in diff(model_at(project, w, ref(target)), model_at(project, w, ref(branch)))]
    ahead = int(_git(w.root, "rev-list", "--count", f"{ref(target)}..{ref(branch)}", check=False).stdout.strip() or 0)
    return {
        "from": branch,
        "to": target,
        "from_environment": environment_for(project, branch),
        "to_environment": environment_for(project, target),
        "commits": ahead,
        "changes": changes,
        "url": _compare_url(project, branch, target),
    }


__all__ = ["KINDS", "check", "environment_for", "flow", "new", "promotion", "start"]
