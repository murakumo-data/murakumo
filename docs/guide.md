# The replication guide

Murakumo builds a whole data platform for you, and **the replication guide says how to build all
of it by hand, without Murakumo**:

- every account, role and key;
- every source connection, and how to test it;
- every raw table, what each column holds, and how its files are loaded;
- every model, as dbt and as plain SQL, with where each column comes from;
- every test, schedule, pipeline and CI step;
- and why it is built the way it is.

Anyone can then rebuild the platform, change it outside Murakumo, or stop using Murakumo, and lose
nothing.

```bash
murakumo guide                  # write generated/guide/, and list the chapters
murakumo guide --open           # and open it in the browser, as one page
murakumo guide --check          # prove it: rebuild the project from the guide alone
```

`murakumo generate` writes the guide too, so it always matches the model. Turn it off with
`generation.guide: false`.

## What is in it

`generated/guide/` holds:

| File | What it is |
| --- | --- |
| `README.md` | How to use the guide, the chapters, and every step as a checklist |
| `00-overview.md` … `11-troubleshooting.md` | A chapter per part of the platform, in the order you build it |
| `index.html` | The whole guide on one page, with the checklist kept in your browser |
| `sql/build/` | The project as plain SQL: `00_landing.sql` (schemas and raw tables), then a script per model, in the order they must run |
| `sql/load/` | Loading the landed files into each raw table, each its own way |
| `sql/tests/` | Every test as a query: rows returned are failures |
| `sql/masking.sql` | The masking policies (Snowflake, when `governance.masking` is set) |
| `rebuild.sh` | Every script, in order, through the warehouse's own client (`duckdb`, or `snow sql`) |

The chapters:

0. **Overview.** The stack, an architecture diagram, how many tables and tests, and the tools to
   install, with versions.
1. **Accounts and access.** Snowflake: the key pair, the provisioning SQL, the service user, the
   connection variables, and the storage integration for Snowpipe. DuckDB: the file.
2. **Repository and environments.** Where everything is, profiles.yml, the environments and their
   branches.
3. **Sources.** For each connection:
   - its secrets;
   - how to reach it by hand (`curl` with its auth, `sftp`, a database query, `aws s3 ls`);
   - its settings file;
   - the command that fetches it into the landing area.
   
   Lambda, Glue and Auto Loader deployments come next. Last, where to put files for raw tables
   nothing fetches.
4. **The raw layer.** The raw tables' DDL. For each table: its data dictionary and its load SQL.
   Snowpipe's pipes, when it is chosen.
5. **Transformations.** Every model and snapshot, in the order they run. For each:
   - what it is for;
   - where each column comes from;
   - its dbt SQL, and the same as plain SQL;
   - its tests.
6. **Running it.** `dbt build`, the plain scripts in order, the schedules and their selectors,
   the orchestrator, backfills and full refreshes.
7. **Quality and observability.** The tests, source freshness, and queries over the run log.
8. **CI/CD.** The workflow file, and the secrets it needs.
9. **Reference.** The bus matrix, lineage, a data dictionary per mart, the exposures, and what is
   masked.
10. **Decisions.** The [decisions log](stack.md#the-decisions-log), with when, who and why.
11. **Troubleshooting.** What usually goes wrong, and what to do.

Every step has the same shape:

- **Why** it is there;
- the commands, SQL or files in full;
- **You should see** what tells you it worked;
- a box to tick.

## Loading without Murakumo

The load scripts in `sql/load/` do what `murakumo load` does, with nothing but SQL. They read the
files under `<landing>/<source>/<table>/`, so run them from the project folder.

| Raw table's `load` | What the script does |
| --- | --- |
| `full` | Takes only the latest run's files (by the run time in their names), and replaces the table with them once that run is new |
| `append` (and `cdc`, `streaming`) | Adds every file not loaded before |
| `incremental` | The new rows replace the rows with the same key; the latest of each key is kept (by the watermark column) |

- **DuckDB** remembers each file it loaded in `audit.loaded_files`, by name. Landed file names
  carry the run's time, so a name is never reused.
- **Snowflake** uploads local files to the table's own stage with PUT (in SnowSQL or the
  Snowflake CLI). It reads S3 landing through the stage Snowpipe uses. COPY remembers which files
  it has loaded, so running a script again loads nothing twice.

## Proving it: `murakumo guide --check`

The guide is only useful if nothing is missing from it. `murakumo guide --check` follows the guide
the way someone rebuilding by hand would, in a scratch folder, on DuckDB:

1. It lands sample files for every raw table where the loads look.
2. It runs only the guide's scripts, with DuckDB itself: the raw tables, the loads, every model in
   order, then every test query.
3. It runs the loads and models again, which must change nothing.
4. It builds the same files with Murakumo (`murakumo load` and dbt).
5. It compares every table, raw to marts, in rows and in a checksum of their values. The columns
   that record when a row was written are left out, since they differ between any two builds.
6. It does it all again with a second run of files: changed rows for full and incremental
   tables, new rows for append tables. Merges, snapshots and replaced tables then have something
   to do.

```text
$ murakumo guide --check
Rebuilding from the guide alone, on DuckDB, then with Murakumo, twice over...
The guide rebuilds the project: all 31 tables match a Murakumo build, in rows and values, after
each of 2 runs of new files; every test passes, and running it again changes nothing (65 scripts
run).
```

It exits with 1, and says what differs, when the guide would build something else. CI can run
it.

## In the canvas

The **Guide** panel (the book-with-a-tick icon on the rail) shows the chapters, as the model is
now:

- Open a step for its why, its SQL or commands (with a copy button) and what you should see.
- Tick the steps you have done. The ticks stay in your browser, as on the guide's own page.
- **Check it rebuilds the project** runs `murakumo guide --check` in **Activity**.
