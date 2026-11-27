"""Secrets kept in the project's ``.env`` file, so nobody has to export them by hand.

The file holds ``NAME=value`` lines, the same format python-dotenv, Docker Compose and most tools
read. Murakumo:

- keeps it out of git (``.env`` is added to ``.gitignore``) and readable only by its owner;
- reads it when a command, the canvas or an extract run starts, for the variables not already set:
  a variable set in the environment (by CI, a scheduler or a secret store) always wins;
- never writes a secret anywhere else: murakumo.yaml only names the variable.

On every platform the file is plain text, like any ``.env``: it is as safe as the folder it is in.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

FILE = ".env"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SecretError(ValueError):
    pass


def path(root: Path) -> Path:
    return Path(root) / FILE


def parse(text: str) -> dict[str, str]:
    """``NAME=value`` lines: comments, blank lines and ``export `` are allowed, spaces around ``=``
    too, and a value may be quoted (single quotes keep it exactly as written)."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, value = (part.strip() for part in line.split("=", 1))
        if not _NAME.match(name):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            quote, value = value[0], value[1:-1]
            if quote == '"':
                value = value.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def read(root: Path) -> dict[str, str]:
    file = path(root)
    try:
        return parse(file.read_text(encoding="utf-8"))
    except (FileNotFoundError, NotADirectoryError):
        return {}


def load(root: Path) -> list[str]:
    """Put the file's variables into this process's environment, except those already set there.
    Returns the names it set."""
    added = []
    for name, value in read(root).items():
        if name not in os.environ:
            os.environ[name] = value
            added.append(name)
    return added


def _quote(value: str) -> str:
    if value and re.fullmatch(r"[A-Za-z0-9_./:@+,~-]*", value):
        return value
    if "'" not in value and "\n" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def save(root: Path, name: str, value: str) -> None:
    """Set one secret in the file (adding the file, and its line in .gitignore, if need be), and in
    this process's environment, so it is used straight away."""
    if not _NAME.match(name or ""):
        raise SecretError(f"{name!r} is not a variable name: letters, digits and _, not starting with a digit")
    file = path(root)
    lines = file.read_text(encoding="utf-8").splitlines() if file.is_file() else []
    entry = f"{name}={_quote(value)}"
    replaced = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].lstrip()
        if stripped.split("=", 1)[0].strip() == name and "=" in stripped:
            lines[i] = entry
            replaced = True
    if not replaced:
        if not lines:
            lines = ["# Secrets for this project, read by Murakumo. Kept out of git: never commit this file."]
        lines.append(entry)
    _write_private(file, "\n".join(lines) + "\n")
    ignore(root)
    os.environ[name] = value


def remove(root: Path, name: str) -> bool:
    file = path(root)
    if not file.is_file():
        return False
    lines = file.read_text(encoding="utf-8").splitlines()
    kept = [
        line
        for line in lines
        if line.strip().removeprefix("export ").split("=", 1)[0].strip() != name or "=" not in line
    ]
    if len(kept) == len(lines):
        return False
    _write_private(file, "\n".join(kept) + "\n")
    if read(root).get(name) is None:
        os.environ.pop(name, None)
    return True


def status(root: Path, names: list[str]) -> list[dict[str, object]]:
    """Each named variable: whether it is set, and where from (the environment or the file).
    Never the value."""
    stored = read(root)
    out = []
    for name in dict.fromkeys(names):
        if name in stored:
            where = "file"
        elif os.environ.get(name):
            where = "environment"
        else:
            where = None
        out.append({"name": name, "set": where is not None, "from": where})
    return out


def ignore(root: Path) -> None:
    """``.env`` is in the project's .gitignore."""
    file = Path(root) / ".gitignore"
    text = file.read_text(encoding="utf-8") if file.is_file() else ""
    if any(line.strip() in {FILE, f"/{FILE}", f"{FILE}*", "*.env"} for line in text.splitlines()):
        return
    prefix = "" if text.endswith("\n") or not text else "\n"
    file.write_text(f"{text}{prefix}# Secrets: never committed\n{FILE}\n", encoding="utf-8")


def _write_private(file: Path, text: str) -> None:
    """Write the file readable and writable only by its owner (where the system has such modes)."""
    tmp = file.with_name(file.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - systems without these modes
        pass
    os.replace(tmp, file)


def variable(source: str, what: str) -> str:
    """The variable a source's secret goes in: DYNAMICS_CLIENT_SECRET."""
    stem = re.sub(r"[^A-Za-z0-9]+", "_", source).strip("_").upper() or "SOURCE"
    if stem[0].isdigit():
        stem = "S_" + stem
    return f"{stem}_{what.upper()}"
