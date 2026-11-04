"""``murakumo guide --check``: prove the replication guide is complete by following it.

In a scratch folder, on DuckDB, it lands sample files for every raw table, then runs only the
guide's scripts (generated/guide/sql) with DuckDB itself, as someone rebuilding by hand would:
the schemas and raw tables, the loads, every model in order, and every test query. Then it runs
the loads and models a second time, which must change nothing. Finally it builds the same files
with Murakumo (``murakumo load`` and dbt) and compares every table's row count.

If the guide's SQL builds the same tables with the same rows, and its tests pass, nothing in the
guide depends on Murakumo.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from murakumo.manifest import MANIFEST_FILE
from murakumo.project import MODEL_DIR, Project, load_project

RUNS = ("20260101T000000000000Z", "20260102T000000000000Z")  # two runs' sample files: the second changes rows


class RebuildError(Exception):
    pass


@dataclass
class RebuildResult:
    # name, (rows, checksum) built by the guide, and by Murakumo
    tables: list[tuple[str, tuple | None, tuple | None]] = field(default_factory=list)
    failed_tests: list[tuple[str, int]] = field(default_factory=list)  # test, failing rows
    changed_on_rerun: list[tuple[str, int, int]] = field(default_factory=list)  # name, first run, second run
    rounds: int = 0  # runs of new files compared
    scripts: int = 0
    error: str | None = None

    @property
    def mismatched(self) -> list[tuple[str, tuple | None, tuple | None]]:
        return [t for t in self.tables if t[1] != t[2]]

    @property
    def ok(self) -> bool:
        return not (self.error or self.mismatched or self.failed_tests or self.changed_on_rerun)


def _statements(sql: str) -> list[str]:
    """The statements of a script, comments dropped (DuckDB runs them one at a time)."""
    body = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in re.split(r";\s*(?:\n|$)", body) if s.strip()]


def _copy(project: Project, into: Path) -> Project:
    """The project's model and manifest in a scratch folder, landing in a folder there."""
    into.mkdir(parents=True)
    shutil.copy2(project.root / MANIFEST_FILE, into / MANIFEST_FILE)
    shutil.copytree(project.root / MODEL_DIR, into / MODEL_DIR)
    copied = load_project(into).project
    if copied is None:
        raise RebuildError("the project does not load; run `murakumo validate`")
    copied.manifest.stack.landing = "landing"
    copied.manifest.targets.warehouse.database = None  # <project>.duckdb in the scratch folder
    return copied


def _land(project: Project, plan, rows: int, run: int = 0) -> dict[str, str]:
    """A run's sample files for every raw table, where the guide's loads look; {table: files} for
    Murakumo (a full table takes only that run's files, as `murakumo load` does)."""
    from murakumo.generate import loads
    from murakumo.generate.sample import sample_rows
    from murakumo.ir.model import Layer

    raw = [e for e in project.model.entities if e.layer is Layer.RAW]
    data = sample_rows(project.model, raw, rows=rows)
    if run:
        data = _changes(project, raw, data, rows)
    out: dict[str, str] = {}
    for _source, _schema, entity, _mode, fmt, files in loads.tables(plan):
        folder = project.root / files.rsplit("/", 1)[0]
        folder.mkdir(parents=True, exist_ok=True)
        records = data.get(entity.name, [])
        if not records:
            continue  # nothing new for it in this run
        names = [a.name for a in entity.attributes]
        suffix = files.rsplit(".", 1)[-1]
        path = folder / f"{entity.name}_{RUNS[run]}_0001.{suffix}"
        if fmt == "csv":
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(names)
                writer.writerows([["" if r.get(n) is None else r.get(n) for n in names] for r in records])
        elif fmt == "parquet":
            import duckdb

            json_path = path.with_suffix(".json")
            json_path.write_text("\n".join(json.dumps(r, default=str) for r in records), encoding="utf-8")
            con = duckdb.connect()
            con.execute(f"copy (select * from read_json('{json_path}', format = 'newline_delimited')) to '{path}'")
            con.close()
            json_path.unlink()
        else:
            path.write_text("\n".join(json.dumps(r, default=str) for r in records) + "\n", encoding="utf-8")
        name = f"{entity.name}_{RUNS[run]}_*" if _mode == "full" else f"{entity.name}_*.{suffix}"
        out[entity.name.lower()] = str(folder / name)
    return out


def _changes(project: Project, raw, data: dict[str, list[dict]], rows: int) -> dict[str, list[dict]]:
    """The second run: the same rows changed (full and incremental tables, so merges and snapshots
    have something to do), and new rows with new keys (append tables whose keys are numbers that
    no other table points at; the others get nothing new)."""
    from murakumo import loading

    pointed = {(r.from_.lower(), k.from_.lower()) for r in project.model.relationships for k in r.keys}
    out: dict[str, list[dict]] = {}
    for entity in raw:
        keys = loading.keys(entity)
        mode = loading.mode(entity)
        records = [dict(r) for r in data.get(entity.name, [])]
        if mode == "append":
            numeric = keys and all(entity.attribute(k).logical_type.family == "integer" for k in keys)
            if not numeric or any((entity.name.lower(), k.lower()) in pointed for k in keys):
                out[entity.name] = []
                continue
            for record in records:
                for key in keys:
                    if record.get(key) is not None:
                        record[key] = record[key] + rows
            out[entity.name] = records
            continue
        changeable = [
            a.name
            for a in entity.attributes
            if a.logical_type.family == "text"
            and not a.accepted_values
            and a.name not in keys
            and (entity.name.lower(), a.name.lower()) not in pointed
        ]
        for record in records[::2]:  # every other row changes
            for name in changeable[:1]:
                if record.get(name) is not None:
                    record[name] = f"{record[name]} (changed)"
        out[entity.name] = records
    return out


# Columns that hold when or by which run a row was written: they differ between any two builds.
_WHEN = {
    "_ingested_at",
    "_batch_id",
    "_updated_at",
    "_loaded_at",
    "valid_from",
    "valid_to",
    "is_current",
    "dbt_valid_from",
    "dbt_valid_to",
    "dbt_updated_at",
    "dbt_scd_id",
    "snapshot_date",
}


def _counts(connection, skip: tuple[str, ...] = ("audit",)) -> dict[str, tuple[int, int | None]]:
    """Each table's rows, and a checksum of its values (whatever order the rows are in)."""
    found = connection.execute(
        "select table_schema, table_name from information_schema.tables "
        "where table_type in ('BASE TABLE', 'VIEW') and table_schema not like '%audit%'"
    ).fetchall()
    counts: dict[str, tuple[int, int | None]] = {}
    for schema, name in found:
        if schema in skip:
            continue
        columns = [
            r[0]
            for r in connection.execute(
                "select column_name from information_schema.columns where table_schema = ? and table_name = ? "
                "order by column_name",
                [schema, name],
            ).fetchall()
            if r[0].lower() not in _WHEN
        ]
        listed = ", ".join(f'"{c}"' for c in columns)
        row = connection.execute(
            f"select count(*), {'sum(hash(' + listed + ') % 1000000007)' if columns else 'null'} "
            f'from "{schema}"."{name}"'
        ).fetchone()
        counts[name] = (row[0], None if row[1] is None else int(row[1]))
    return counts


def _run(connection, script: Path, root: Path) -> None:
    import duckdb

    for statement in _statements(script.read_text(encoding="utf-8")):
        try:
            connection.execute(statement)
        except duckdb.Error as exc:
            raise RebuildError(f"{script.relative_to(root)}: {exc}") from None


def _tests(connection, sql: Path) -> list[tuple[str, int]]:
    """The guide's test queries that return rows: (test, rows that fail it)."""
    failed = []
    for script in sorted((sql / "tests").glob("*.sql")):
        for block in re.split(r"\n(?=-- )", script.read_text(encoding="utf-8")):
            lines = block.strip().splitlines()
            if len(lines) < 2 or not lines[0].startswith("-- ") or lines[0].startswith("-- Each query"):
                continue
            query = "\n".join(lines[1:]).rstrip().rstrip(";")
            failing = connection.execute(f"select count(*) from ({query})").fetchone()[0]
            if failing:
                failed.append((lines[0][3:], failing))
    return failed


def check(project: Project, rows: int = 50, keep: Path | None = None) -> RebuildResult:
    import duckdb

    from murakumo.generate import generate
    from murakumo.generate.verify import build

    result = RebuildResult()
    scratch = Path(tempfile.mkdtemp(prefix="murakumo-guide-")) if keep is None else keep
    try:
        guided = _copy(project, scratch / "guide")
        output = generate(guided, "duckdb")
        if not project.manifest.generation.guide:
            from murakumo.generate import replication

            output.files.update(replication.render(guided, output.plan, output.files))
        for path, content in output.files.items():
            if path.startswith("guide/"):
                target = guided.root / "generated" / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
        _land(guided, output.plan, rows)

        sql = guided.root / "generated" / "guide" / "sql"
        build_scripts = sorted((sql / "build").glob("*.sql"))
        load_scripts = sorted((sql / "load").glob("*.sql"))
        landing, models = build_scripts[:1], build_scripts[1:]
        built = _copy(project, scratch / "murakumo")
        from murakumo.generate.verify import _duckdb_path

        connection = duckdb.connect(str(guided.root / "guide.duckdb"))
        here = os.getcwd()
        os.chdir(guided.root)  # the scripts read landing/ from the project folder
        try:
            for run in range(len(RUNS)):
                _land(guided, output.plan, rows, run)
                scripts = [*landing, *load_scripts, *models] if run == 0 else [*load_scripts, *models]
                for script in scripts:
                    _run(connection, script, guided.root)
                    result.scripts += 1
                result.failed_tests += [(f"{t} (run {run + 1})", n) for t, n in _tests(connection, sql)]
                first = _counts(connection)
                for script in [*load_scripts, *models]:  # again, with nothing new: nothing changes
                    _run(connection, script, guided.root)
                second = _counts(connection)
                result.changed_on_rerun += [
                    (f"{n} (run {run + 1})", first[n], second.get(n)) for n in first if first[n] != second.get(n)
                ]
                data = _land(built, output.plan, rows, run)
                outcome = build(built, warehouse="duckdb", data=data)
                if outcome.error:
                    raise RebuildError(f"the Murakumo build failed: {outcome.error}")
                theirs = duckdb.connect(str(_duckdb_path(built)), read_only=True)
                try:
                    counts = _counts(theirs)
                finally:
                    theirs.close()
                label = "" if run == 0 else f" (run {run + 1})"
                result.tables += [(n + label, second[n], counts.get(n)) for n in sorted(second)]
                result.rounds += 1
        finally:
            os.chdir(here)
            connection.close()
    except RebuildError as exc:
        result.error = str(exc)
    finally:
        if keep is None:
            shutil.rmtree(scratch, ignore_errors=True)
    return result


__all__ = ["RebuildResult", "check"]
