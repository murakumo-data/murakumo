#!/usr/bin/env bash
# Install a built Murakumo wheel into a fresh virtual environment and run it like a new user would:
# init, setup, validate, generate, migrate, verify on DuckDB, and the canvas.
#
#   tools/smoke_test.sh packages/python/dist/murakumo-0.1.0-py3-none-any.whl
set -euo pipefail

wheel="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
work="$(mktemp -d)"
trap 'kill "${canvas_pid:-}" 2>/dev/null || true; rm -rf "$work"' EXIT

python3 -m venv "$work/venv"
"$work/venv/bin/pip" install --quiet --upgrade pip
"$work/venv/bin/pip" install --quiet "${wheel}[canvas,verify]"
murakumo="$work/venv/bin/murakumo"

cd "$work"
"$murakumo" --version
"$murakumo" init demo --yes
cd demo
"$murakumo" setup --check
"$murakumo" validate
"$murakumo" generate
"$murakumo" generate --check
"$murakumo" migrate
"$murakumo" migrate --check
"$murakumo" verify --rows 50

"$murakumo" canvas --no-open --port 8799 > "$work/canvas.log" 2>&1 &
canvas_pid=$!
disown "$canvas_pid"  # stopped on exit; no "Terminated" message
for _ in $(seq 1 30); do
  curl -sf http://127.0.0.1:8799/api/revision > /dev/null && break
  sleep 1
done
curl -sf http://127.0.0.1:8799/api/state | grep -q '"project":"demo"'
curl -sf http://127.0.0.1:8799/ | grep -q '<div id="root">'
echo "Smoke test passed: the wheel installs, builds the example on DuckDB and serves the canvas."
