# End-to-end test: Murakumo 0.1.0

A step-by-step test of everything Murakumo 0.1.0 does, from installing it to building a model on
real data. It assumes no experience with Python environments, dbt or warehouses: every command is
given in full, along with what you should see.

| Part | What it tests | Time |
| --- | --- | --- |
| 1-2 | Building the release and installing it in a clean environment | 10 min |
| 3-4 | Creating a project with the guided setup | 10 min |
| 5-9 | The canvas: viewing, editing, mapping, generating, migrations | 35 min |
| 10-11 | Importing a data dictionary; editing YAML by hand alongside the canvas | 10 min |
| 12-14 | Building and testing on DuckDB, with made-up data and then real data | 15 min |
| 15 | The checks a CI pipeline would run | 5 min |
| 16 | Optional: the same project on Snowflake | 15 min |
| 17 | Cleaning up | 2 min |

## How to use this script

- Grey boxes are commands. Copy the whole box, paste it into Terminal, and press **Enter**. Paste
  one box at a time and wait for it to finish (the prompt comes back) before the next.
- After most steps, **You should see** says what to expect. Words may wrap differently on your
  screen; what matters is the content.
- Tick each **☐** as you go. If something differs, don't stop: write the step number and what
  you saw in the [results table](#results) at the end (copy the Terminal output or take a
  screenshot), then carry on unless the step says otherwise.
- `~` means your home folder (`/Users/<you>`). `Ctrl+C` means hold Control and press C.

---

## Part 0: Before you start (once)

You need a Mac with Terminal, Python 3.11 or newer, Node.js 22 or newer, git, and the Murakumo
repository on your computer. Open **Terminal** (press Cmd+Space, type *Terminal*, press Enter).

**0.1** Check the tools:

```bash
python3 --version
node --version
git --version
uv --version
```

You should see `Python 3.11` or higher, `v22` or higher, a git version and a uv version.
If one is missing: Python from <https://www.python.org/downloads/>, Node.js from
<https://nodejs.org> (the LTS version), uv with `brew install uv`, git with `xcode-select --install`.

☐ All four print a version.

**0.2** Tell Terminal where the repository is. Change the path if yours is elsewhere:

```bash
export REPO=~/Documents/Development/automate_data_engineering
ls $REPO
```

You should see folders including `apps`, `docs`, `examples` and `packages`.

> Every new Terminal window or tab forgets `REPO`. If a later command says
> `No such file or directory` with a path starting `/packages`, run the `export REPO=...` line again.

☐ The repository's folders are listed.

---

## Part 1: Build the release candidate

You are testing exactly what would be published to PyPI: a *wheel*, the file `pip` installs.

**1.1** Get the code under test. Before the release pull request is merged it lives on the
`m7-release` branch; after merging, use `main`.

```bash
cd $REPO
git fetch
git checkout m7-release
git pull
```

You should see `Switched to branch 'm7-release'` (or `Already on`), then `Already up to date` or a
list of updated files.

☐ On the `m7-release` branch.

**1.2** Build the canvas (the web app), then the Python package that contains it:

```bash
cd $REPO/apps/canvas
npm ci
npm run build
cd $REPO/packages/python
rm -rf dist
MURAKUMO_REQUIRE_CANVAS=1 uv build
```

You should see `✓ built in ...` from the canvas build, then
`Successfully built dist/murakumo-0.1.0.tar.gz` and
`Successfully built dist/murakumo-0.1.0-py3-none-any.whl`.

☐ Both files are built.

**1.3** Check the canvas is inside the wheel:

```bash
unzip -l dist/murakumo-0.1.0-py3-none-any.whl | grep web/
```

You should see three lines: `murakumo/web/index.html` and two files under `murakumo/web/assets/`.

☐ The canvas files are in the wheel.

---

## Part 2: Install it in a clean environment

A *virtual environment* is a private folder of Python packages, so this test cannot be affected by,
or affect, anything else on your computer.

**2.1** Create a test folder and an environment inside it:

```bash
mkdir -p ~/murakumo-e2e
cd ~/murakumo-e2e
python3 -m venv .venv
source .venv/bin/activate
```

Your prompt now starts with `(.venv)`. That means the environment is active.

> Each new Terminal tab needs `cd ~/murakumo-e2e && source .venv/bin/activate` before `murakumo`
> works there. `command not found: murakumo` almost always means this was skipped.

☐ The prompt starts with `(.venv)`.

**2.2** Install Murakumo from the wheel you built, with the canvas, DuckDB and testing extras:

```bash
pip install --upgrade pip
pip install "$REPO/packages/python/dist/murakumo-0.1.0-py3-none-any.whl[canvas,verify,duckdb]"
```

This takes a minute or two and ends with `Successfully installed ...`, a long list including
`murakumo-0.1.0`, `dbt-core` and `dbt-duckdb`.

☐ Installed without errors.

**2.3** Check it runs:

```bash
murakumo --version
murakumo info
```

You should see `murakumo 0.1.0`, then a short description that mentions `murakumo init` and
`murakumo setup`.

☐ Version 0.1.0.

---

## Part 3: Create a project with the guided setup

**3.1** Start a new project. Murakumo asks a few questions; press **Enter** to accept each default
(shown in square brackets), except where noted:

```bash
murakumo init taxi_platform
```

| Question | Answer |
| --- | --- |
| `Project name [taxi_platform]` | Enter |
| `Warehouse (duckdb runs on this computer, no account needed) (...) [duckdb]` | Enter |
| `Orchestrator (...) [airflow]` | Enter |
| `Modelling pattern (...) [kimball]` | Enter |
| `Start with the NYC taxi example model? [Y/n]` | Enter |

You should see:

- `Created project taxi_platform in /Users/<you>/murakumo-e2e/taxi_platform`
- `Example model: 4 entities, 4 relationships, 1 metric.`
- **What this project needs on this machine**, with sections:
  - **Orchestrator: airflow**: *airflow is not installed (not needed yet)*, with how to install it
    and a note that Murakumo does not generate Airflow pipelines yet.
  - **Project**: *Not a git repository yet*, *generated/ is out of date*, *Migrations not started*,
    each with the command to fix it.
- It ends with `Ready. Next: murakumo canvas or murakumo generate.`

☐ The project is created and the checklist has no red ✗.

**3.2** Look at what was created:

```bash
cd taxi_platform
ls
cat murakumo.yaml
```

You should see `custom`, `generated`, `model` and `murakumo.yaml`. The manifest says
`type: duckdb`, `database: taxi_platform.duckdb`, `pattern: kimball` and `orchestrator: airflow`:
your answers.

☐ The manifest matches your answers.

---

## Part 4: The setup checklist

`murakumo setup` checks what the project needs on this machine and says what to do about anything
missing. Run it whenever something does not work.

**4.1**

```bash
murakumo setup
```

You should see sections for **Tools** (Python, the canvas), **Warehouse: duckdb** (dbt and
dbt-duckdb installed; *DuckDB runs locally: no account or credentials needed*), **Orchestrator**
and **Project**, ending with `Ready to build.`

☐ `Ready to build.`

**4.2** Do what the **Project** section says about git. If git asks who you are, run the two
`git config` lines first, with your own name and email:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

```bash
git init
git add .
git commit -m "Start taxi_platform"
murakumo setup
```

You should see `[main (root-commit) ...] Start taxi_platform`, and **Project** now shows
✓ *In a git repository*.

☐ The git warning is gone.

**4.3** Check the project is valid:

```bash
murakumo validate
```

You should see `taxi_platform: 1 sources, 4 entities, ... columns, 4 relationships` and
`valid: 0 error(s), 0 warning(s)`.

☐ Valid.

---

## Part 5: Open the canvas

**5.1** Open a **second Terminal tab** (Cmd+T), so the canvas can keep running while you use the
first one:

```bash
cd ~/murakumo-e2e && source .venv/bin/activate && cd taxi_platform
murakumo canvas
```

You should see `Editing /Users/<you>/murakumo-e2e/taxi_platform` and
`Canvas on http://127.0.0.1:8765. Press Ctrl+C to stop.`, and your browser opens the canvas. If it
does not, open <http://127.0.0.1:8765> yourself.

Leave this tab running and go back to the first tab (Cmd+Shift+[ or click it) for Terminal
commands.

☐ The canvas opens in the browser.

**5.2** Look around. You should see:

- the toolbar: **taxi_platform**, `4 entities · 4 relationships`, layer chips **raw 2** and
  **marts 2**, a blue chip like **15 files to generate**, a green **Valid** chip, and **+ Entity**;
- two grey-topped raw tables on the left (`yellow_tripdata`, `taxi_zone_lookup`) and two
  blue-topped marts on the right (`fct_trip`, `dim_taxi_zone`), joined by lines labelled
  `pickup · N:1` and `dropoff · N:1`.

Zoom with the scroll wheel or the **+/−** buttons at the bottom left; drag the background to move
around.

☐ Four tables and four lines.

**5.3** Click the **marts** chip in the toolbar. The two marts disappear; click it again to bring
them back.

☐ Layers hide and show.

**5.4** Drag `dim_taxi_zone` somewhere else. Reload the page (Cmd+R): it stays where you put it.
In the first Terminal tab:

```bash
cat model/layout.yaml
```

You should see `positions:` with an id and `x`/`y` numbers.

☐ The position is saved.

---

## Part 6: Edit the model on the canvas

**6.1 Look at an entity.** Click `fct_trip`. A panel opens on the right with two tabs, **Model**
and **Code**. **Model** shows its layer, kind, domain and description, its columns (name, type,
**PK**, **NN** = not null) and **Mappings**: where each column comes from, such as
`yellow_tripdata.tpep_pickup_datetime`.

☐ The inspector shows columns and mappings.

**6.2 See its code.** Click the **Code** tab. You should see the dbt SQL generated for `fct_trip`
(`select ... from yellow_tripdata left join ...`) and its `CREATE TABLE` statement. Click
**Model** to go back.

☐ The Code tab shows SQL.

**6.3 Add a column.** In the **Columns** list, in the empty row at the bottom, type `trip_minutes`
in the name box, replace `string` with `integer` in the type box, and click **Add**.

You should see `trip_minutes` in the columns and in the box on the canvas, and the toolbar chip
turns yellow: **1 warning**. Click it: *MK503 fct_trip.trip_minutes has no mapping; it will be
null*. In the **Mappings** section, `trip_minutes` is highlighted.

☐ The column is added and MK503 is shown.

**6.4 Try a mistake.** In the `trip_minutes` row of **Mappings**, click the right-hand box
(*expression*), type the incomplete expression below and press **Enter**:

```text
date_diff('minute',
```

You should see a red banner starting *cannot read the expression*. Nothing is saved: the model
still has no mapping for `trip_minutes`. Click in the box and press **Esc** to clear what you typed.

☐ The bad expression is refused.

**6.5 Map it properly.** In the middle box (*sources*) of the `trip_minutes` row, type the
following and press **Enter** (suggestions appear as you type; you can pick from them):

```text
yellow_tripdata.tpep_pickup_datetime, yellow_tripdata.tpep_dropoff_datetime
```

The chip turns red for a moment: **1 error**, *MK502 fct_trip.trip_minutes maps 2 columns; combine
them with an expression*. That is right: two columns need an expression saying how to combine them.
In the *expression* box type this and press **Enter**:

```text
date_diff('minute', tpep_pickup_datetime, tpep_dropoff_datetime)
```

You should see the error disappear (the chip says **Valid** again). On the **Code** tab, the SQL
now has a line with `DATE_DIFF('MINUTE', ...) as trip_minutes`.

☐ The mapping is saved and appears in the SQL.

**6.6 Add a table.** Click **+ Entity** (top right). Name: `fhv_tripdata`. Layer: `raw`. Source:
`nyc_tlc`. Click **Add**.

A new box appears and is selected. Add three columns, one at a time, as in 6.3:

| Name | Type |
| --- | --- |
| `dispatching_base_num` | `string` |
| `pickup_datetime` | `timestamp` |
| `pickup_location_id` | `integer` |

☐ `fhv_tripdata` has three columns.

**6.7 Draw a relationship.** Close the inspector (× at its top right) so you can see both tables.
Move `fhv_tripdata` near `taxi_zone_lookup`. Hover over `fhv_tripdata`: small round handles
appear at both ends of each column row. Press on the handle at the end of the
`pickup_location_id` row, drag to the `LocationID` row of `taxi_zone_lookup`, and let go on its
handle.

You should see a new line `N:1` between them, and the toolbar says **5 relationships**. In the
`fhv_tripdata` box, `pickup_location_id` is now marked **FK**.

Tip: zoom in first (scroll up) so the handles are bigger. If the line does not appear, try again,
letting go exactly on the small circle.

☐ The relationship is drawn.

**6.8 Edit the relationship.** Click the new line. The inspector shows
`fhv_tripdata → taxi_zone_lookup` with the join. Type `pickup` in **Role** and press **Enter**;
the line's label becomes `pickup · N:1`.

☐ The role is saved.

**6.9 Check the files changed.** In the first Terminal tab:

```bash
git status
git diff model/relationships.yaml
```

You should see `model/entities/nyc_tlc/fhv_tripdata.yaml` as new, `fct_trip.yaml` and
`relationships.yaml` modified, and in the diff a new relationship from `fhv_tripdata` to
`taxi_zone_lookup` with `role: pickup`. The canvas writes plain YAML, the same as the CLI.

☐ The changes are on disk.

---

## Part 7: Generate from the canvas

**7.1** Click the blue **N files to generate** chip. The **Changes** panel opens on the left: under
**generated/ for duckdb**, every file is marked **NEW** (nothing has been generated yet).

☐ The Changes panel lists new files.

**7.2** Click **Generate**. A banner says `Wrote generated/.`; the panel says *Up to date with the
model* and the chip says **Generated**.

☐ Generated.

**7.3** Make a change to see a diff. Click `dim_taxi_zone`, and change its **Description** to
`Taxi zones, one row per zone.`, then click elsewhere. The chip shows **2 files to generate**:
`_mobility__models.yml` (dbt's documentation of the model) and `marts.sql` (the table comment).
Open the Changes panel and click `_mobility__models.yml`: a diff appears, with the old description
in red and the new one in green.

☐ The diff shows exactly your change.

**7.4** Click **Generate** again.

☐ Back to **Generated**.

---

## Part 8: Migrations from the canvas

Tables that keep data between runs (raw landing tables, incremental models, snapshots) need their
structure changed in place when the model changes. That is what migrations are.

**8.1** In the **Changes** panel, the **Migration** section says *No migrations yet*. Click
**Write baseline**. A banner names `migrations/duckdb/0001_initial.sql`.

☐ The baseline is written.

**8.2** Rename a raw column: click `fhv_tripdata`, and in its columns change
`dispatching_base_num` to `base_number` (click the name, edit, press **Enter**).

The toolbar chip now says **... files to generate · migration**. Open the Changes panel. Under
**Migration** you should see *fhv_tripdata: rename dispatching_base_num to base_number*. Click
**Show SQL**:

```sql
ALTER TABLE raw_nyc_tlc.fhv_tripdata RENAME COLUMN dispatching_base_num TO base_number;
```

A rename, so the column's data would be kept, not dropped.

☐ The migration preview is a RENAME.

**8.3** Click **Write #2**, then **Generate**.

```bash
ls migrations/duckdb
cat migrations/duckdb/0002_*.sql
```

You should see `0001_initial.sql`, `0002_rename_fhv_tripdata_dispatching_base_num.sql` and
`schema.json`, and the file holds the `ALTER TABLE ... RENAME COLUMN` statement.

☐ Migration 0002 is on disk.

---

## Part 9: Work alongside the canvas

The canvas reloads by itself when files change, whoever changes them.

**9.1** With the canvas visible, run in the first tab:

```bash
murakumo rename column fhv_tripdata pickup_location_id pickup_zone_id
```

You should see `Renamed fhv_tripdata.pickup_location_id to pickup_zone_id.` and, within two
seconds, the canvas shows `pickup_zone_id` in `fhv_tripdata`, still marked **FK**, with the
`pickup · N:1` line still attached to it: the relationship followed the rename. Click the line:
the join now reads `fhv_tripdata.pickup_zone_id = taxi_zone_lookup.LocationID`.

> Raw tables mirror their source, so in real life you would only rename a raw column when the
> source renames it. `fhv_tripdata` is made up for this test, so here it is fine.

☐ The canvas updated on its own, and the relationship followed.

**9.2** Write the migration for it from the command line this time:

```bash
murakumo migrate
murakumo generate
```

You should see `Wrote migrations/duckdb/0003_rename_fhv_tripdata_pickup_location_id.sql`, then the
generated files updated. The canvas chip shows **Generated** again.

☐ Migration 0003 written.

---

## Part 10: Import a data dictionary

**10.1** Copy a sample dictionary (a spreadsheet describing tables and columns) and import it:

```bash
cp $REPO/examples/sample-dictionaries/open_meteo.csv .
murakumo import csv open_meteo.csv
```

You should see two new tables, `weather_daily` and `weather_hourly`, and
`Saved. The model has 0 error(s) and 2 warning(s).`: two **MK308** warnings, *an incremental load
needs a watermark column to know what is new*. The canvas shows a new raw group with two tables
and the yellow **2 warnings** chip.

☐ Two weather tables and two MK308 warnings.

---

## Part 11: Fix something by hand in a text editor

**11.1** Open the daily weather table's file in TextEdit:

```bash
open -e model/entities/open_meteo/weather_daily.yaml
```

Find the line `load: incremental` near the top. Below it, add this line (no spaces before it), then
save (Cmd+S) and close the window:

```yaml
watermark: observed_date
```

**11.2** Do the same for the hourly table, with `observed_at`:

```bash
open -e model/entities/open_meteo/weather_hourly.yaml
```

```yaml
watermark: observed_at
```

Within two seconds of each save, the canvas updates: the chip goes from **2 warnings** to
**1 warning** to **Valid**. If it shows an error instead, check the line has no spaces before
`watermark` and is spelled exactly as above.

```bash
murakumo validate
```

You should see `valid: 0 error(s), 0 warning(s)`.

☐ Both warnings fixed from the text editor.

---

## Part 12: Build and test on DuckDB with made-up data

`murakumo verify` builds the whole generated project in a throwaway DuckDB database, loads
made-up rows that respect every key and rule in the model, runs every dbt test, then builds again
to test the incremental path.

**12.1**

```bash
murakumo generate
murakumo migrate
murakumo verify
```

`migrate` writes migration 0004 for the new weather tables (`create schema raw_open_meteo` and two
tables). `verify` takes about half a minute, prints dbt's progress, and ends with:

```text
verified: ... models and snapshots built, N/N tests passed, incremental rebuild passed on duckdb.
```

The two numbers in `N/N` must be the same.

☐ All tests passed.

---

## Part 13: Build and test with real data

Real data is where tests find real problems. You'll use one month of New York taxi trips (about
3 million rows, 50 MB) and the zone list, both published by the city.

**13.1** Download them:

```bash
mkdir -p ~/murakumo-e2e/data
curl -L -o ~/murakumo-e2e/data/yellow_tripdata_2024-01.parquet https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet
curl -L -o ~/murakumo-e2e/data/taxi_zone_lookup.csv https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv
ls -lh ~/murakumo-e2e/data
```

You should see a file of about 48 MB and a small CSV.

☐ Both files downloaded.

**13.2** Build and test with them. One command, on one line:

```bash
murakumo verify --data yellow_tripdata=$HOME/murakumo-e2e/data/yellow_tripdata_2024-01.parquet --data taxi_zone_lookup=$HOME/murakumo-e2e/data/taxi_zone_lookup.csv
```

Wait about 10 seconds. You should see `loaded taxi_zone_lookup: 265 rows`,
`loaded yellow_tripdata: 2964624 rows`, made-up rows for the other tables, and
`verified: ... N/N tests passed, incremental rebuild passed on duckdb.`

> While preparing this release, this exact step found two real problems in Murakumo's own example
> model (a taxi vendor code added in 2024, and fare corrections that look like duplicate trips).
> Both are fixed in 0.1.0, which is why it should pass now.

☐ All tests pass on 2,964,624 real trips.

**13.3** Apply the migrations to a real DuckDB database, the way you would on a warehouse, and
check the renamed column is there:

```bash
python -c "
import duckdb, glob
con = duckdb.connect('taxi_platform.duckdb')
for f in sorted(glob.glob('migrations/duckdb/0*.sql')):
    con.execute(open(f).read())
    print('applied', f)
print(con.sql(\"select column_name from information_schema.columns where table_name = 'fhv_tripdata'\").fetchall())
"
```

You should see four `applied migrations/duckdb/000N_...sql` lines, then a list that includes
`base_number` and `pickup_zone_id`, and not the old names.

☐ Migrations apply in order; the rename took.

---

## Part 14: Look at what was generated

**14.1**

```bash
find generated -name "*.sql" | sort
cat generated/dbt/models/marts/mobility/fct_trip.sql
```

You should see staging models for each raw table (`stg_nyc_tlc__...`, `stg_open_meteo__...`), the
two marts, DDL files, and in `fct_trip.sql` the `trip_minutes` line you mapped on the canvas.

☐ The generated SQL matches the model.

---

## Part 15: The checks a CI pipeline would run

These are the commands to put in CI (GitHub Actions or similar) so nobody merges a model change
without its generated code and migrations. Each should print a success message, and `echo $?`
should print `0`.

**15.1**

```bash
murakumo validate --strict; echo $?
murakumo generate --check; echo $?
murakumo migrate --check; echo $?
murakumo setup --check > /dev/null; echo $?
```

You should see `valid: 0 error(s), 0 warning(s)`, `generated/ is up to date`,
`migrations/duckdb is up to date`, and `0` after each.

☐ All four print 0.

**15.2** See one fail on purpose: change something without regenerating.

```bash
murakumo rename column fhv_tripdata base_number base_id
murakumo migrate --check; echo $?
```

You should see `needs a migration: fhv_tripdata: rename base_number to base_id`, an error, and `1`.
Put it back:

```bash
murakumo rename column fhv_tripdata base_id base_number
murakumo migrate --check; echo $?
```

`0` again.

☐ The check fails when it should, and passes again.

**15.3** Commit your work:

```bash
git add .
git commit -m "Model trips, FHV and weather"
```

☐ Committed.

---

## Part 16 (optional): The same on Snowflake

Only if your Snowflake trial is still active ([setup guide](snowflake.md)). It costs a few cents
of credits.

**16.1** Load your Snowflake variables (they are in `~/.zshrc`; a new Terminal tab has them
already), install the Snowflake pieces, and create a Snowflake project:

```bash
cd ~/murakumo-e2e
source ~/.zshrc
source .venv/bin/activate
pip install dbt-snowflake "$REPO/packages/python/dist/murakumo-0.1.0-py3-none-any.whl[canvas,verify,duckdb,snowflake]"
murakumo init snow_test --target snowflake --yes
cd snow_test
```

The checklist after `init` shows only what still needs attention; there should be no red ✗ under
**Warehouse: snowflake**, just the optional variables.

☐ Created, with no red ✗.

**16.2** Test the connection:

```bash
murakumo setup --connect
```

You should see ✓ *Connected to Snowflake as MURAKUMO_DBT* (role TRANSFORMER, warehouse
TRANSFORMING), then a red ✗ *Database SNOW_TEST does not exist*, with the SQL to create it.
That is expected: the project is named `snow_test`, and your account has a `COMMERCE` database.
Point the project at it instead:

```bash
export SNOWFLAKE_DATABASE=COMMERCE
murakumo setup --connect
```

✓ *Database COMMERCE is there* and `Ready to build.`

☐ Connected, and the database check passes.

**16.3** Build and test on Snowflake:

```bash
murakumo verify --warehouse snowflake
```

About 20-30 seconds. You should see `verified: 4 models and snapshots built, 16/16 tests passed,
incremental rebuild passed on snowflake.` The run uses its own temporary schemas and drops them
afterwards; in Snowsight, under **COMMERCE**, no `VERIFY_...` schemas are left.

☐ 16/16 on Snowflake.

---

## Part 17: Clean up

**17.1** In the canvas tab, press **Ctrl+C** to stop it. In each tab:

```bash
deactivate
```

**17.2** Optional: delete the test folder (it is not part of the repository):

```bash
rm -rf ~/murakumo-e2e
```

☐ Done.

---

## If something goes wrong

| What you see | What to do |
| --- | --- |
| `command not found: murakumo` | The environment is not active in this tab: `cd ~/murakumo-e2e && source .venv/bin/activate` |
| `No such file or directory` with a path starting `/packages` | `REPO` is not set in this tab: run the `export REPO=...` line from 0.2 |
| `address already in use` when starting the canvas | Another canvas is running. Stop it with Ctrl+C in its tab, or use `murakumo canvas --port 8766` |
| The browser doesn't open | Open <http://127.0.0.1:8765> yourself |
| The canvas says *Cannot reach the Murakumo server* | The canvas tab was stopped; start it again (5.1) |
| The canvas is read-only with a red banner | A model file has a YAML mistake; the Problems list says which file and line |
| `error the model has errors` | Run `murakumo validate`; it lists each problem with its file |
| Anything else | Run `murakumo setup`: it checks the usual causes and says what to do |

---

## Results

Copy this table into a note or reply, and fill it in. For anything that is not ✓, add the Terminal
output or a screenshot.

| Part | Result (✓ / ✗ / skipped) | Notes |
| --- | --- | --- |
| 0 Tools | | |
| 1 Build | | |
| 2 Install | | |
| 3 Guided init | | |
| 4 Setup checklist | | |
| 5 Canvas: view | | |
| 6 Canvas: edit, map, relate | | |
| 7 Generate and diff | | |
| 8 Migrations on the canvas | | |
| 9 Editing alongside the canvas | | |
| 10 Import a dictionary | | |
| 11 Fix by hand in a text editor | | |
| 12 Verify with made-up data | | |
| 13 Verify with real data | | |
| 14 Generated code | | |
| 15 CI checks | | |
| 16 Snowflake (optional) | | |
| 17 Clean up | | |

Also note anything that was confusing, slow or surprising, even if it worked. That is as useful
as a failure.
