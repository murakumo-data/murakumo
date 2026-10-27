#!/usr/bin/env node
// The `murakumo` command for npm users. Murakumo itself is a Python program (published on PyPI);
// this runs the same version of it through uv's `uvx`, which downloads Python and Murakumo the
// first time and reuses them after that. Nothing else to install but uv.
//
//   npx murakumo init my_platform
//   npm install -g murakumo && murakumo canvas
//
// MURAKUMO_EXTRAS adds extras (e.g. "snowflake,postgres") and MURAKUMO_WITH adds packages
// (e.g. "dbt-snowflake"); `murakumo setup` says when a project needs them. MURAKUMO_SPEC replaces
// the whole requirement, for testing an unpublished build.
"use strict";

const { spawnSync } = require("node:child_process");
const { version } = require("../package.json");

const BASE_EXTRAS = ["canvas", "verify", "duckdb"];

function list(value, separator) {
  return (value || "")
    .split(separator)
    .map((item) => item.trim())
    .filter(Boolean);
}

function requirement() {
  if (process.env.MURAKUMO_SPEC) return process.env.MURAKUMO_SPEC;
  const extras = [...new Set([...BASE_EXTRAS, ...list(process.env.MURAKUMO_EXTRAS, ",")])].sort();
  return `murakumo[${extras.join(",")}]==${version}`;
}

function found(command) {
  const result = spawnSync(command, ["--version"], { stdio: "ignore" });
  return !result.error && result.status === 0;
}

if (!found("uvx")) {
  console.error(
    [
      "Murakumo runs on Python, and the npm package starts it with uv. Install uv first:",
      "",
      "  macOS:    brew install uv",
      "  macOS / Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh",
      '  Windows:  powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"',
      "",
      "Then open a new terminal and run the murakumo command again.",
      "Or install it with pip instead: pip install \"murakumo[canvas,verify,duckdb]\"",
    ].join("\n"),
  );
  process.exit(1);
}

const args = ["--from", requirement()];
for (const extra of list(process.env.MURAKUMO_WITH, /[\s,]+/)) args.push("--with", extra);
args.push("murakumo", ...process.argv.slice(2));

const result = spawnSync("uvx", args, {
  stdio: "inherit",
  // Lets `murakumo setup` give install advice that works through this launcher.
  env: { ...process.env, MURAKUMO_LAUNCHER: "npm" },
});
if (result.error) {
  console.error(`Could not start uvx: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status === null ? 1 : result.status);
