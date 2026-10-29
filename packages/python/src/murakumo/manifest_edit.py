"""Change one section of murakumo.yaml and keep the rest of the file, comments included, as it was.

A section is a key at a path, such as ``project.domains`` or ``sources``. Its lines are replaced
with the new value written as YAML; every other line stays byte for byte. Comments inside the
replaced section are lost, so only sections the canvas and CLI manage are written this way.
The result is checked by loading it as a manifest before anything is written.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from murakumo.manifest import MANIFEST_FILE, Manifest


class ManifestEditError(Exception):
    pass


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _find(lines: list[str], key: str, start: int, end: int, indent: int) -> int | None:
    pattern = re.compile(rf"^ {{{indent}}}{re.escape(key)}:(\s|$)")
    return next((i for i in range(start, end) if pattern.match(lines[i])), None)


def _block_end(lines: list[str], start: int, indent: int) -> int:
    """The line after the block that starts at ``start``: the next content line at or left of ``indent``."""
    end = start + 1
    while end < len(lines):
        if _content(lines[end]) and _indent(lines[end]) <= indent:
            break
        end += 1
    while end > start + 1 and not _content(lines[end - 1]):  # leave trailing blank lines and comments
        end -= 1
    return end


class _Dumper(yaml.SafeDumper):
    """Indents list items under their key, as the rest of murakumo.yaml is written."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        return super().increase_indent(flow, False)


def _dump(key: str, value: Any, indent: int) -> list[str]:
    pad = " " * indent
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [f"{pad}{key}: [{', '.join(value)}]"]
    text = yaml.dump({key: value}, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100)
    return [pad + line for line in text.rstrip("\n").splitlines()]


def set_section(text: str, path: tuple[str, ...], value: Any) -> str:
    """``text`` with the section at ``path`` replaced by ``value`` (added if it is missing)."""
    lines = text.splitlines()
    start, end, indent = 0, len(lines), 0
    for depth, key in enumerate(path):
        found = _find(lines, key, start, end, indent)
        last = depth == len(path) - 1
        if found is None:
            if not last:
                raise ManifestEditError(f"{MANIFEST_FILE} has no {'.'.join(path[: depth + 1])} section")
            insert_at = end
            while insert_at > start and not _content(lines[insert_at - 1]):
                insert_at -= 1
            new = _dump(key, value, indent)
            if indent == 0 and insert_at > 0:
                new = ["", *new]
            lines[insert_at:insert_at] = new
            return "\n".join(lines) + "\n"
        block_end = _block_end(lines, found, indent)
        if last:
            lines[found:block_end] = _dump(key, value, indent)
            return "\n".join(lines) + "\n"
        start, end, indent = found + 1, block_end, indent + 2
    raise ManifestEditError("empty path")  # pragma: no cover


def read_manifest(root: Path) -> dict[str, Any]:
    return yaml.safe_load((root / MANIFEST_FILE).read_text(encoding="utf-8")) or {}


def write_section(root: Path, path: tuple[str, ...], value: Any) -> Manifest:
    """Replace one section of the project's murakumo.yaml, after checking the result loads."""
    file = root / MANIFEST_FILE
    text = set_section(file.read_text(encoding="utf-8"), path, value)
    try:
        manifest = Manifest.model_validate(yaml.safe_load(text))
    except ValidationError as exc:
        problem = exc.errors()[0]
        where = ".".join(map(str, problem["loc"]))
        raise ManifestEditError(f"{where}: {problem['msg'].removeprefix('Value error, ')}") from None
    file.write_text(text, encoding="utf-8")
    return manifest
