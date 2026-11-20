"""MkDocs hooks for the documentation site.

The pages in docs/ are written to read on GitHub, so they link to files beside the docs folder
(`../examples/README.md`, `../apps/canvas`). On the site those files are not pages: the links go
to the same files on GitHub instead. The changelog, kept at the top of the repository, becomes the
site's changelog page.
"""

from __future__ import annotations

import re
from pathlib import Path

from mkdocs.structure.files import File

REPOSITORY = "https://github.com/murakumo-data/murakumo"
ROOT = Path(__file__).resolve().parent.parent

# A Markdown link or image whose target leaves the docs folder: ](../path#anchor)
OUTSIDE = re.compile(r"\]\(\.\./([^)\s#]+)(#[^)\s]*)?\)")


def _to_github(match: re.Match[str]) -> str:
    path, anchor = match.group(1), match.group(2) or ""
    kind = "tree" if (ROOT / path).is_dir() else "blob"
    return f"]({REPOSITORY}/{kind}/main/{path}{anchor})"


# A relative link in a file at the top of the repository: ](path#anchor)
OUTSIDE_FROM_ROOT = re.compile(r"\]\((?!https?:|mailto:|#)([^)\s#]+)(#[^)\s]*)?\)")


def _from_root(match: re.Match[str]) -> str:
    path, anchor = match.group(1), match.group(2) or ""
    if path.startswith("docs/"):
        return f"]({path.removeprefix('docs/')}{anchor})"
    return _to_github(match)


def on_files(files, config):
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # It sits beside docs/ in the repository, and inside it on the site.
    changelog = OUTSIDE_FROM_ROOT.sub(_from_root, changelog)
    files.append(File.generated(config, "changelog.md", content=changelog))
    return files


def on_page_markdown(markdown: str, page, config, files) -> str:
    return OUTSIDE.sub(_to_github, markdown)
