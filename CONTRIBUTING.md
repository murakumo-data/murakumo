# Contributing to Murakumo

Thank you for helping. Murakumo is Apache-2.0 licensed, and contributions are accepted under the
same licence.

## Set up

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 22+.

```bash
cd packages/python && uv sync
cd ../../apps/canvas && npm install
```

## Before you open a pull request

```bash
cd packages/python
uv run ruff check && uv run ruff format --check
uv run pytest
cd ../../apps/canvas
npm run lint && npm test && npm run build
```

CI runs the same commands, on Python 3.11 and 3.13, with a PostgreSQL service for the tests that
need one (`MURAKUMO_TEST_POSTGRES_URL`).

## What every change needs

- **Tests.** A generator change is tested by what it generates, and where it can be, by running
  it: `murakumo verify` builds the generated project on DuckDB and runs every test.
- **The example stays current.** `examples/commerce/generated` is committed. After a generator
  change, run `uv run murakumo generate -p ../../examples/commerce` and commit the result; a test
  fails when it is out of date.
- **A canvas path.** Anything the CLI can do, the canvas can do. A new command or setting comes
  with its place in the canvas.
- **A place in the replication guide.** A project's guide (`generated/guide/`) must be enough to
  rebuild the platform by hand, so generated code that the guide does not explain is not done.
- **Docs and the changelog.** The page in `docs/` that covers the area, and a line in
  `CHANGELOG.md` under the unreleased version.
- **Say what was not verified.** Code for a service you could not run against (a cloud account,
  say) is welcome; say so in the pull request and the changelog, as the changelog already does.

## Adding a warehouse, an orchestrator or a source

- A warehouse is one `Target` in `packages/python/src/murakumo/generate/targets.py` (types,
  dialect, dbt adapter, incremental strategy), a profile in `generate/dbt.py`, and a backend in
  `generate/sqlbackend.py` so `verify` and `build` can run on it.
- An orchestrator is a renderer in `generate/orchestration.py` (or `orchestration_cloud.py`), its
  steps in `guide.py`, and its option in `stack.py`.
- A source kind is a connection in `manifest.py`, its fetch in `ingest.py`, and its form in the
  canvas's `Connection.tsx`.

## Style

Python is formatted and linted by ruff (120 columns). Comments say why, not what. Messages to the
user are plain sentences that say what to do next.
