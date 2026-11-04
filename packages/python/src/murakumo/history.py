"""Undo and redo for the canvas: the model files as they were before and after each change.

Every change the canvas makes goes through one place (``server.create_app``'s ``change``), which
takes a snapshot of the manifest and the model files before and after it. Undo writes the
snapshot from before back, redo the one from after, so any change can be taken back the same way:
a removed table comes back with its columns, mappings, relationships and position.

Undo refuses when the files changed some other way since (a text editor, ``murakumo sync``): it
never overwrites work it did not make. Moving tables around is not a change here: undo keeps the
current positions, and only brings back the position of a table it brings back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from murakumo.manifest import MANIFEST_FILE
from murakumo.project import LAYOUT_FILE, MODEL_DIR, dump_yaml

LIMIT = 100


class HistoryError(Exception):
    """Nothing to undo or redo, or the files changed since."""


@dataclass(frozen=True)
class Snapshot:
    """The text of the manifest and every model file; None for a file that does not exist."""

    files: dict[str, str | None]

    def content(self) -> dict[str, object]:
        """What undo compares: everything but the positions of tables, which moving changes."""
        out: dict[str, object] = {k: v for k, v in self.files.items() if k != LAYOUT_FILE}
        out[LAYOUT_FILE] = {k: v for k, v in _layout(self.files.get(LAYOUT_FILE)).items() if k != "positions"}
        return out


def _layout(text: str | None) -> dict:
    try:
        data = yaml.safe_load(text) if text else None
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def take(root: Path) -> Snapshot:
    paths = [root / MANIFEST_FILE, *sorted(p for p in (root / MODEL_DIR).rglob("*") if p.is_file())]
    return Snapshot({p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in paths if p.is_file()})


def _restore(root: Path, target: Snapshot, current: Snapshot) -> None:
    for name in current.files.keys() - target.files.keys():
        (root / name).unlink(missing_ok=True)
    if LAYOUT_FILE not in target.files and LAYOUT_FILE in current.files:
        target = Snapshot({**target.files, LAYOUT_FILE: None})
    for name, text in target.files.items():
        if name == LAYOUT_FILE:
            text = _merge_layout(text, current.files.get(LAYOUT_FILE), target)
        path = root / name
        if text is None:
            path.unlink(missing_ok=True)
        elif not path.is_file() or path.read_text(encoding="utf-8") != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    for folder in sorted((root / MODEL_DIR).rglob("*"), reverse=True):  # folders a removal emptied
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def _merge_layout(target: str | None, current: str | None, snapshot: Snapshot) -> str | None:
    """The target layout, with every table it keeps left where it is now."""
    data, now = _layout(target), _layout(current)
    text = "\n".join(t for k, t in snapshot.files.items() if k != LAYOUT_FILE and t)
    positions = dict(data.get("positions") or {})
    positions.update({k: v for k, v in (now.get("positions") or {}).items() if str(k) in text})
    data.pop("positions", None)
    if positions:
        data["positions"] = positions
    return dump_yaml(data) if data else None


@dataclass
class Entry:
    label: str
    before: Snapshot
    after: Snapshot


@dataclass
class History:
    done: list[Entry] = field(default_factory=list)
    undone: list[Entry] = field(default_factory=list)

    def record(self, label: str, before: Snapshot, after: Snapshot) -> None:
        if before.content() == after.content():
            return  # nothing but positions changed
        self.done.append(Entry(label, before, after))
        del self.done[:-LIMIT]
        self.undone.clear()

    def undo(self, root: Path) -> str:
        if not self.done:
            raise HistoryError("nothing to undo")
        entry = self.done[-1]
        self._check(root, entry.after)
        _restore(root, entry.before, take(root))
        self.undone.append(self.done.pop())
        return entry.label

    def redo(self, root: Path) -> str:
        if not self.undone:
            raise HistoryError("nothing to redo")
        entry = self.undone[-1]
        self._check(root, entry.before)
        _restore(root, entry.after, take(root))
        self.done.append(self.undone.pop())
        return entry.label

    def _check(self, root: Path, expected: Snapshot) -> None:
        if take(root).content() != expected.content():
            self.done.clear()
            self.undone.clear()
            raise HistoryError(
                "the model files changed outside the canvas since, so undo stops here (git still has every version)"
            )

    def summary(self) -> dict[str, str | None]:
        return {
            "undo": self.done[-1].label if self.done else None,
            "redo": self.undone[-1].label if self.undone else None,
        }


def label(old_model, new_model, old_manifest=None, new_manifest=None) -> str:
    """What a change did, in a few words: the first model change, and how many more."""
    from murakumo.modeldiff import diff

    changes = [
        f"{c.entity}: {c.summary}" if c.entity and c.entity not in c.summary else c.summary
        for c in diff(old_model, new_model)
    ]
    if old_manifest is not None and new_manifest is not None:
        for kind, before, after in (
            ("domain", old_manifest.project.domains, new_manifest.project.domains),
            ("schedule", old_manifest.orchestration.schedules, new_manifest.orchestration.schedules),
            ("source", old_manifest.sources, new_manifest.sources),
        ):
            was, now = {x.name for x in before}, {x.name for x in after}
            changes += [f"new {kind} {n}" for n in sorted(now - was)]
            changes += [f"removed {kind} {n}" for n in sorted(was - now)]
            if was == now and [x.model_dump() for x in before] != [x.model_dump() for x in after]:
                changes.append(f"changed a {kind}")
    if not changes:
        return "the last change"
    return changes[0] if len(changes) == 1 else f"{changes[0]} (+{len(changes) - 1} more)"


__all__ = ["History", "HistoryError", "Snapshot", "label", "take"]
