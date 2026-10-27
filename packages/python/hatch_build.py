"""Put the built canvas (apps/canvas/dist) inside the package, at murakumo/web.

From the repository, the canvas must be built first (``npm run build`` in apps/canvas). The
sdist carries the built files in src/murakumo/web, so a wheel built from the sdist has them too.
Set MURAKUMO_REQUIRE_CANVAS=1 (the release workflow does) to fail instead of building without it.
"""

from __future__ import annotations

import os
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CanvasBuildHook(BuildHookInterface):
    PLUGIN_NAME = "canvas"

    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        built = root.parent.parent / "apps" / "canvas" / "dist"
        packaged = root / "src" / "murakumo" / "web"
        target = "src/murakumo/web" if self.target_name == "sdist" else "murakumo/web"
        # force_include, because src/murakumo/web is git-ignored and ignored files are left out.
        if (built / "index.html").is_file():
            build_data["force_include"][str(built)] = target
        elif (packaged / "index.html").is_file():  # building the wheel from the sdist
            build_data["force_include"][str(packaged)] = target
        elif os.environ.get("MURAKUMO_REQUIRE_CANVAS") == "1":
            raise RuntimeError("the canvas is not built: run `npm ci && npm run build` in apps/canvas first")
