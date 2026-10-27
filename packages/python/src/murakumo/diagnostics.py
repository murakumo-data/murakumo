"""Problems found while loading or validating a project."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class Level(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Diagnostic:
    level: Level
    code: str
    message: str
    file: Path | None = None
    where: str | None = None

    def location(self, root: Path | None = None) -> str:
        if self.file is None:
            return self.where or ""
        file = self.file
        if root is not None and file.is_relative_to(root):
            file = file.relative_to(root)
        return f"{file}: {self.where}" if self.where else str(file)


def error(code: str, message: str, file: Path | None = None, where: str | None = None) -> Diagnostic:
    return Diagnostic(Level.ERROR, code, message, file, where)


def warning(code: str, message: str, file: Path | None = None, where: str | None = None) -> Diagnostic:
    return Diagnostic(Level.WARNING, code, message, file, where)
