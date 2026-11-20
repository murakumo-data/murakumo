#!/bin/sh
# Install Murakumo: model-first data engineering. https://murakumo.ai
#
#   curl -fsSL https://murakumo.sh | sh
#
# What it does, and nothing else:
#   1. Finds uv (https://docs.astral.sh/uv/), or installs it for you with uv's own installer,
#      into your home folder. No sudo, and nothing system-wide.
#   2. Runs `uv tool install "murakumo[canvas,verify,duckdb]"`: Murakumo in its own environment,
#      with a Python uv downloads if you have no suitable one, and the `murakumo` command on your
#      PATH. Running it again upgrades Murakumo.
#
# Options, after `sh -s --`:   curl -fsSL https://murakumo.sh | sh -s -- --version 1.1.0
#   --version X     install that version (default: the latest)
#   --extras A,B    add extras, e.g. snowflake,postgres (canvas, verify and duckdb are always in;
#                   running it again without --extras goes back to just those)
#   --no-uv         do not install uv; stop and say how if it is missing
#   --help          show this
# The same as environment variables: MURAKUMO_VERSION, MURAKUMO_EXTRAS, MURAKUMO_NO_UV=1.
#
# Uninstall: uv tool uninstall murakumo
# The source: https://github.com/murakumo-data/murakumo/blob/main/website/home/install.sh

# Everything is inside main(), which runs on the last line, so a download cut short runs nothing.

set -eu

BASE_EXTRAS="canvas,verify,duckdb"

say() { printf '%s\n' "$*"; }
step() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() {
    printf '\033[1;31merror:\033[0m %s\n' "$*" >&2
    exit 1
}
has() { command -v "$1" >/dev/null 2>&1; }

usage() {
    cat <<'EOF'
Install Murakumo with uv (installing uv first if it is missing).

  curl -fsSL https://murakumo.sh | sh
  curl -fsSL https://murakumo.sh | sh -s -- [options]

  --version X     install that version (default: the latest)
  --extras A,B    add extras, e.g. snowflake,postgres (canvas, verify and duckdb are always in;
                  running it again without --extras goes back to just those)
  --no-uv         do not install uv; stop and say how if it is missing
  --help          show this

Environment variables do the same: MURAKUMO_VERSION, MURAKUMO_EXTRAS, MURAKUMO_NO_UV=1.
More: https://murakumo.ai/docs/setup/
EOF
}

# The extras, base ones first and each once: "snowflake, postgres" -> canvas,verify,duckdb,snowflake,postgres
extras_list() {
    printf '%s,%s' "$BASE_EXTRAS" "$1" | tr -d ' ' | tr ',' '\n' | awk 'NF && !seen[$0]++' | paste -sd, -
}

find_uv() {
    if has uv; then
        UV=uv
        return 0
    fi
    for candidate in "${XDG_BIN_HOME:-}/uv" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            UV=$candidate
            return 0
        fi
    done
    return 1
}

install_uv() {
    step "Installing uv, which installs and runs Murakumo (https://docs.astral.sh/uv/)"
    if has curl; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif has wget; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        fail "neither curl nor wget is installed, so uv cannot be downloaded"
    fi
    find_uv || fail "uv was installed, but cannot be found. Open a new terminal and run this again."
}

main() {
    version=${MURAKUMO_VERSION:-}
    extras=${MURAKUMO_EXTRAS:-}
    no_uv=${MURAKUMO_NO_UV:-}

    while [ $# -gt 0 ]; do
        case $1 in
            --version) [ $# -ge 2 ] || fail "--version needs a version, e.g. --version 1.1.0"; version=$2; shift 2 ;;
            --version=*) version=${1#*=}; shift ;;
            --extras) [ $# -ge 2 ] || fail "--extras needs a list, e.g. --extras snowflake,postgres"; extras=$2; shift 2 ;;
            --extras=*) extras=${1#*=}; shift ;;
            --no-uv) no_uv=1; shift ;;
            -h | --help) usage; exit 0 ;;
            *) fail "unknown option: $1 (see --help)" ;;
        esac
    done

    case $(uname -s) in
        Linux | Darwin) ;;
        MINGW* | MSYS* | CYGWIN*)
            fail "on Windows, install with uv in PowerShell:
  powershell -ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 | iex\"
  uv tool install \"murakumo[$BASE_EXTRAS]\"" ;;
        *) say "This system ($(uname -s)) is not one Murakumo is tested on; trying anyway." ;;
    esac

    case $version in
        "" | [0-9]*) ;;
        *) fail "--version takes a number, e.g. 1.1.0, not '$version'" ;;
    esac

    if ! find_uv; then
        if [ -n "$no_uv" ]; then
            fail "uv is not installed. Install it (https://docs.astral.sh/uv/getting-started/installation/), then run this again.
Or install with pip: pip install \"murakumo[$BASE_EXTRAS]\""
        fi
        install_uv
    fi

    requirement="murakumo[$(extras_list "$extras")]"
    [ -n "$version" ] && requirement="$requirement==$version"

    step "Installing $requirement"
    # Murakumo needs Python 3.11 or later: use one you have, or let uv download one. Without this,
    # uv takes the first Python it finds, such as macOS's 3.9.
    "$UV" tool install --upgrade --python '>=3.11' "$requirement"

    # Where uv put the command, and whether a new shell will find it.
    bin_dir=$("$UV" tool dir --bin 2>/dev/null || printf '%s' "$HOME/.local/bin")
    if has murakumo && [ "$(command -v murakumo)" = "$bin_dir/murakumo" ]; then
        installed=$(murakumo --version 2>/dev/null || true)
        ready=1
    else
        installed=$("$bin_dir/murakumo" --version 2>/dev/null || true)
        ready=
    fi
    [ -n "$installed" ] || fail "Murakumo was installed, but '$bin_dir/murakumo --version' did not run"

    say ""
    step "Installed: $installed"
    if [ -z "$ready" ]; then
        "$UV" tool update-shell >/dev/null 2>&1 || true
        say ""
        say "  $bin_dir is not on your PATH yet. Open a new terminal, or run:"
        say "    export PATH=\"$bin_dir:\$PATH\""
    fi
    say ""
    say "  Start a project:   murakumo init my_platform"
    say "  Open the canvas:   murakumo canvas my_platform"
    say "  The docs:          https://murakumo.ai/docs/"
}

main "$@"
