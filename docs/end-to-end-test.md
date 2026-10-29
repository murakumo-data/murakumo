# End-to-end test: Murakumo 0.1.1

A step-by-step test of Murakumo 0.1.1 exactly as a new user meets it: install it with one command,
create a project folder, go into it, and build a data model on real data. It assumes no
experience with Python, dbt or warehouses: every command is given in full, along with what you
should see.

| Part | What it tests | Time |
| --- | --- | --- |
| 0-1 | Installing Murakumo with uv, pip or npm | 5-10 min |
| 2-4 | Creating a project folder with the guided setup, and going into it | 10 min |
| 5-9 | The canvas: viewing, editing, mapping, generating, migrations | 35 min |
| 10-11 | Importing a data dictionary; editing YAML by hand alongside the canvas | 10 min |
| 12-14 | Building and testing on DuckDB, with made-up data and then real data, and keeping the tables | 20 min |
| 15 | The checks a CI pipeline would run | 5 min |
| 16 | Optional: the same project on Snowflake, built and kept | 20 min |
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

## Part 0: Before you start

Open **Terminal** (press Cmd+Space, type *Terminal*, press Enter). Murakumo installs in one of
three ways. Pick **one** route and use it for the whole test:

| Route | Best for | You need |
| --- | --- | --- |
| **A. uv** (recommended) | Anyone: one command, and `murakumo` works in every Terminal window | uv |
| **B. pip** | People who already use Python and virtual environments | Python 3.11 or newer |
| **C. npm** | People who already use Node.js | Node.js 18 or newer, and uv |

**0.1** For routes A and C, install uv (a fast installer for Python programs), then close
Terminal and open a new window so it is found:

```bash
brew install uv
```

No Homebrew? Use `curl -LsSf https://astral.sh/uv/install.sh | sh` instead. Then check:

```bash
uv --version
```

You should see `uv 0.` followed by a version number.

For route B, check Python instead (it must say 3.11 or higher; if not, install it from
<https://www.python.org/downloads/>):

```bash
python3 --version
```

For route C, also check Node.js (v18 or higher; if not, install the LTS version from
<https://nodejs.org>):

```bash
node --version
```

☐ The tools for your route print their versions.

---

## Part 1: Install Murakumo

Follow **only your route**.

**Route A: uv**

```bash
uv tool install "murakumo[canvas,verify,duckdb]"
```

You should see `Installed 1 executable: murakumo`. If the next step says
`command not found: murakumo`, run `uv tool update-shell`, then close Terminal and open a new
window.

**Route B: pip**, inside a *virtual environment* (a private folder of Python packages, so nothing
else on your computer is affected):

```bash
mkdir -p ~/murakumo-e2e
python3 -m venv ~/murakumo-e2e/.venv
source ~/murakumo-e2e/.venv/bin/activate
pip install --upgrade pip
pip install "murakumo[canvas,verify,duckdb]"
```

Your prompt now starts with `(.venv)`, and the install ends with `Successfully installed ...`.

> With route B, every new Terminal tab or window needs
> `source ~/murakumo-e2e/.venv/bin/activate` before `murakumo` works there.

**Route C: npm**

```bash
npm install -g murakumo
```

You should see `added 1 package`. The npm package runs the real Murakumo through uv, which
downloads it the first time you use it (about a minute); after that it starts straight away.

**1.1** All routes: check it works.

```bash
murakumo --version
murakumo info
```

You should see `murakumo 0.1.1`, then a short description that mentions `murakumo init` and
`murakumo setup`.

☐ Version 0.1.1.

---

## Part 2: Create the project folder

A Murakumo project is a folder. You create it with `murakumo init`, then go into it with `cd`, and
run everything else from inside it.

**2.1** Make a folder to hold this test, and go into it:

```bash
mkdir -p ~/murakumo-e2e
cd ~/murakumo-e2e
```

**2.2** Create the project. Murakumo asks a few questions. Each list is numbered: type the number
of your choice, or press **Enter** to accept the suggestion in square brackets:

```bash
murakumo init taxi_platform
```

| Question | What you see | Answer |
| --- | --- | --- |
| Project name | `Project name [taxi_platform]` | Enter |
| Warehouse | a list from `1 duckdb  on this computer, no account needed` to `8 postgres`, then `Choose 1-8 [1]` | Enter (1, duckdb) |
| Orchestrator | a list from `1 airflow` to `10 azure_data_factory`, then `Choose 1-10 [1]` | Enter (1, airflow) |
| Modelling pattern | a list from `1 kimball  star schema: facts and dimensions` to `5 mixed`, then `Choose 1-5 [1]` | Enter (1, kimball) |
| Example model | `Add a small example model (NYC taxi trips) to explore? You can delete it later [Y/n]` | Enter |

The example model gives you four tables to explore straight away; a real project starts without
it (answer `n`) and imports its own.

You should see:

- `Created project taxi_platform in /Users/<you>/murakumo-e2e/taxi_platform`
- `Example model: 4 entities, 4 relationships, 1 metric.`
- `Change these answers later with murakumo config.`
- **What this project needs on this machine**, with a key to the symbols (✓ done, ✗ needed before
  building, ! worth doing, • for later), then only what is not done yet:
  - **Tools** (only if needed): the macOS developer tools or git's name and email; Part 4 does
    these.
  - **Orchestrator: airflow**: *airflow is not installed (not needed yet)*, with numbered steps to
    install it (Docker Desktop, the Astro CLI, ...) and links to Astronomer's, Cosmos's, Airflow's
    and Docker's documentation. You do not need it for this test.
  - **Project**: *Not a git repository yet*, *generated/ is out of date with the model* and
    *Migrations not started*, each with what it means and the command for it.
- **Ready.** in bold green, then **Next:** in blue: `cd taxi_platform, then murakumo canvas ...`.
- Last, `Every command, with what it does: murakumo --help`.

☐ The project is created and the checklist has no red ✗.

**2.3** Go into the project folder. From now on, every command runs from inside it:

```bash
cd taxi_platform
pwd
```

You should see `/Users/<you>/murakumo-e2e/taxi_platform`.

> If you open a new Terminal tab later, go back here first with `cd ~/murakumo-e2e/taxi_platform`
> (and, on route B, activate the environment as in Part 1).

☐ Inside `taxi_platform`.

---

## Part 3: Look at the project

**3.1**

```bash
ls
cat murakumo.yaml
```

You should see `custom`, `generated`, `model` and `murakumo.yaml`. The manifest says
`type: duckdb`, `database: taxi_platform.duckdb`, `pattern: kimball` and `orchestrator: airflow`:
your answers.

☐ The manifest matches your answers.

**3.2 Change an answer.** Say you picked the wrong modelling pattern. Change it, look, and change it
back:

```bash
murakumo config --pattern datavault
grep pattern murakumo.yaml
murakumo config --pattern kimball
```

You should see `✓ modelling pattern: kimball → datavault`, then the line
`pattern: datavault           # kimball | datavault | ...` (the comment listing the options is
kept), then `✓ modelling pattern: datavault → kimball`. Run `murakumo config` on its own to be
asked all three questions again, with your current answers as the suggestions.

☐ The answer changed and changed back.

**3.3 Every command.**

```bash
murakumo --help
```

You should see a list of commands, each with a one-line description: `init`, `config`, `setup`,
`validate`, `import`, `add`, `rename`, `generate`, `migrate`, `verify`, `build`, `canvas` and more.
Any command takes `--help` too, for example `murakumo build --help`.

☐ The command list shows.

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

**4.2** Set up git, which keeps the history of your model. Look at the **Tools** section:

- *Install the macOS command line developer tools (git comes with them)*: run
  `xcode-select --install`, click **Install** in the window that opens, accept, and wait for it to
  finish (a few minutes). Then run `murakumo setup` again.
- *Tell git who you are*: run the two lines it shows, with your own name and email:

  ```bash
  git config --global user.name "Your Name"
  git config --global user.email "you@example.com"
  ```

- ✓ *git is installed and knows who you are*: nothing to do.

Then do what the **Project** section says about git:

```bash
git init
git add .
git commit -m "Start taxi_platform"
murakumo setup
```

You should see `[main (root-commit) ...] Start taxi_platform`, and **Project** now shows
✓ *In a git repository*.

> Already have a repository for your data work? Create the project inside it instead
> (`cd your-repo && murakumo init analytics`) and skip `git init`: **Project** then shows
> ✓ *Inside your existing git repository*, and you commit the project there as usual.

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
cd ~/murakumo-e2e/taxi_platform
murakumo canvas
```

(Route B: run `source ~/murakumo-e2e/.venv/bin/activate` first.)

You should see `Editing /Users/<you>/murakumo-e2e/taxi_platform` and
`Canvas on http://127.0.0.1:8765. Press Ctrl+C to stop.`, and your browser opens the canvas. If it
does not, open <http://127.0.0.1:8765> yourself.

Leave this tab running and go back to the first tab (Cmd+Shift+[ or click it) for Terminal
commands.

☐ The canvas opens in the browser.

**5.2** Look around. You should see:

- the toolbar: **taxi_platform**, `4 entities · 4 relationships`, layer chips **raw 2** and
  **marts 2**, a blue chip like **15 files to write**, a green **Valid** chip, and **+ Entity**;
- two grey-topped raw tables on the left (`yellow_tripdata`, `taxi_zone_lookup`) and two
  blue-topped marts on the right (`fct_trip`, `dim_taxi_zone`), joined by dashed lines. Each line
  has a crow's foot (three prongs) at the many end, and a circle and bar (*zero or one*) at the
  other end.

Zoom with the scroll wheel or the **+/−** buttons at the bottom left; drag the background to move
around.

☐ Four tables and four lines.

**5.2a Read the lines.** Click **Notation & tips** at the bottom left. A panel explains each
symbol: ‖ exactly one, ○| zero or one, a crow's foot for many, and a dashed line for optional.
Close it with ×. Now hover over the line between `fct_trip` and `dim_taxi_zone`: a label
`pickup · N:1` appears, and the joined columns (`pickup_zone_id` and `zone_id`) are highlighted
in both tables. Click the line: they stay highlighted while it is selected. Click the empty
background to clear it.

☐ The legend opens; hovering or clicking a line highlights its columns.

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

**5.5 Move several tables at once.** Hold **Shift** and drag a box on the empty background around
`fct_trip` and `dim_taxi_zone`: both get a blue outline. Drag one of them: both move together.
(**⌘**+click also adds a table to the selection.)

**5.6** Drag `dim_taxi_zone` straight above `fct_trip`, so one sits over the other. The lines between
them go round the right-hand side of the tables instead of disappearing behind them. Put it back
where it was.

☐ Several tables move together, and lines stay visible wherever the tables are.

---

## Part 6: Edit the model on the canvas

**6.1 Look at an entity.** Click `fct_trip`. A panel opens on the right with two tabs, **Model**
and **Code**. **Model** shows its layer, kind, domain and description, its columns (name, type,
**PK**, **NN** = not null) and **Mappings**: where each column comes from, such as
`yellow_tripdata.tpep_pickup_datetime`, and **Relationships**: the two that join it to
`dim_taxi_zone`.

Make the panel wider: point at its left edge (the pointer becomes ↔) and drag left. It keeps that
width next time.

Above the columns there is a **Find a column** box (it appears once a table has more than six
columns). Type `zone`: only `pickup_zone_id` and `dropoff_zone_id` are left, in **Columns** and in
**Mappings**. Clear the box.

☐ The inspector shows columns, mappings and relationships; it resizes, and the search filters.

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

**6.5 Map it properly.** In the `trip_minutes` row, click **☰** next to the middle box
(*sources*). A list of every other table's columns opens, with tick boxes and a search box. Type
`yellow_tripdata.tpep` in the search box, tick `tpep_pickup_datetime` and
`tpep_dropoff_datetime`, and click **Use these**. (You can also type sources straight into the
box, separated by commas; suggestions appear as you type.)

The middle box now reads
`yellow_tripdata.tpep_pickup_datetime, yellow_tripdata.tpep_dropoff_datetime`, and the chip turns red for a moment: **1 error**, *MK502 fct_trip.trip_minutes maps 2 columns; combine
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

Now copy a column from a table that already has it. Click **+ From another table** (next to
**Columns**). Type `trip_distance` in the search box, tick `trip_distance` under
`yellow_tripdata`, and click **Add columns**. `fhv_tripdata` gets `trip_distance` with the same
type, `decimal(10,2)`. (On a staging or marts table, copied columns are also mapped to where they
came from.)

☐ `fhv_tripdata` has four columns.

**6.7 Add a relationship.** With `fhv_tripdata` still selected, scroll down the inspector to
**Relationships** and click **+ Relationship**. Choose:

| Field | Choose |
| --- | --- |
| This column | `pickup_location_id` |
| Points to table | `taxi_zone_lookup` |
| Its column | `LocationID (PK)` |
| Cardinality | `many_to_one` |

Click **Add relationship**. You should see a new line from `fhv_tripdata` to `taxi_zone_lookup`
(a crow's foot at the `fhv_tripdata` end, ‖ at the other), the toolbar says
**5 relationships**, and in the `fhv_tripdata` box `pickup_location_id` is now marked **FK**.

You can also draw one: hover over a table and small round handles appear at both ends of each
column; drag from one to a column of another table. The form is easier with many tables.

☐ The relationship is added.

**6.8 Edit the relationship.** Click the new line. The inspector shows
`fhv_tripdata → taxi_zone_lookup` with the join, and both joined columns are highlighted on the
canvas. Type `pickup` in **Role** and press **Enter**; hover over the line and its label reads
`pickup · N:1`.

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

**7.1** Click the blue **N files to write** chip. The **Changes** panel opens on the left. At the top
it says your model has changes not written as code yet, with one blue button,
**Write N files to generated/**. Under **generated/ for duckdb**, every file is marked **NEW**
(nothing has been written yet). Click a file to see what it will contain.

☐ The Changes panel lists new files.

**7.2** Click **Write N files to generated/**. A banner says
`Wrote N files to generated/. Review them (git diff), then commit them with the model.`; the panel
says *Everything is written* and the chip says **Code up to date**.

☐ Generated.

**7.3** Make a change to see a diff. Click `dim_taxi_zone`, and change its **Description** to
`Taxi zones, one row per zone.`, then click elsewhere. The chip shows **2 files to write**:
`_mobility__models.yml` (dbt's documentation of the model) and `marts.sql` (the table comment).
Open the Changes panel and click `_mobility__models.yml`: a diff appears, with the old description
in red and the new one in green.

☐ The diff shows exactly your change.

**7.4** Click **Write 2 files to generated/**.

☐ Back to **Code up to date**.

---

## Part 8: Migrations from the canvas

Tables that keep data between runs (raw landing tables, incremental models, snapshots) need their
structure changed in place when the model changes. That is what migrations are.

**8.1** In the **Changes** panel, the **Migration baseline** section says *Optional for now: no
migrations yet*. Click **Write baseline**. A banner says
`Wrote the migration to migrations/duckdb/0001_initial.sql`.

☐ The baseline is written.

**8.2** Rename a raw column: click `fhv_tripdata`, and in its columns change
`dispatching_base_num` to `base_number` (click the name, edit, press **Enter**).

The toolbar chip now says **... files to write · migration**. Open the Changes panel. At the top it
explains that the change alters a table that already holds data, so it needs a migration as well
as new code, lists both, and offers one button: **Write migration #2 and N files**. Under
**Migration #2** you should see *fhv_tripdata: rename dispatching_base_num to base_number*. Click
**Show SQL**:

```sql
ALTER TABLE raw_nyc_tlc.fhv_tripdata RENAME COLUMN dispatching_base_num TO base_number;
```

A rename, so the column's data would be kept, not dropped.

☐ The migration preview is a RENAME.

**8.3** Click **Write migration #2 and N files**. The banner says it wrote the migration to
`migrations/duckdb/0002_...sql` and N files to generated/, and to review them, run the migration on
the warehouse and commit everything together. (Writing both at once, migration first, means the
code never expects a column the tables do not have yet.)

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
generated files updated. The canvas chip shows **Code up to date** again.

☐ Migration 0003 written.

---

## Part 10: Import a data dictionary

**10.1** Download a sample dictionary (a spreadsheet describing tables and columns, from
Murakumo's examples) and import it:

```bash
curl -L -o open_meteo.csv https://raw.githubusercontent.com/murakumo-data/murakumo/main/examples/sample-dictionaries/open_meteo.csv
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
> Both were fixed in 0.1.0, which is why it should pass now.

☐ All tests pass on 2,964,624 real trips.

**13.3** Apply the migrations to a real DuckDB database, the way you would on a warehouse, and
check the renamed column is there:

Routes A and C: start this command with `uv run --with duckdb ` (so the first line reads
`uv run --with duckdb python -c "`); route B runs it as it is.

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

**13.4 Build for real, and keep the tables.** `verify` builds in a throwaway database and deletes
it. `murakumo build` builds into the project's own warehouse (here `taxi_platform.duckdb`, which
the migrations above just brought up to date) and keeps everything:

```bash
murakumo build --data yellow_tripdata=$HOME/murakumo-e2e/data/yellow_tripdata_2024-01.parquet --data taxi_zone_lookup=$HOME/murakumo-e2e/data/taxi_zone_lookup.csv
```

You should see the two `loaded ...` lines, then
`built: ... models and snapshots, N/N tests passed. Tables are in .../taxi_platform.duckdb.`
Look inside (routes A and C: start with `uv run --with duckdb `, as in 13.3):

```bash
python -c "
import duckdb
con = duckdb.connect('taxi_platform.duckdb', read_only=True)
print(con.sql('select table_schema, table_name from information_schema.tables order by 1, 2'))
print(con.sql('select count(*) from marts.fct_trip'))
"
```

You should see schemas `raw_nyc_tlc`, `raw_open_meteo`, `staging` and `marts` with their tables,
and about 2.9 million rows in `fct_trip`. Running `build` again without `--data` rebuilds the
models on the rows already landed; it never deletes them.

☐ The tables are there to query.

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

**16.1** Add Snowflake support to Murakumo. Your route decides how (`murakumo setup` would tell
you the same):

Route A:

```bash
uv tool install --force "murakumo[canvas,verify,duckdb,snowflake]" --with dbt-snowflake
```

Route B:

```bash
pip install "murakumo[snowflake]" dbt-snowflake
```

Route C (add the line to `~/.zshrc` to keep it):

```bash
export MURAKUMO_EXTRAS="snowflake" MURAKUMO_WITH="dbt-snowflake"
```

**16.2** Load your Snowflake variables (they are in `~/.zshrc`; a new Terminal tab has them
already), and create a Snowflake project next to the first one:

```bash
source ~/.zshrc
cd ~/murakumo-e2e
murakumo init snow_test --target snowflake --yes
cd snow_test
```

The checklist after `init` shows only what still needs attention; there should be no red ✗ under
**Warehouse: snowflake**, just the optional variables.

☐ Created, with no red ✗.

**16.3** Test the connection:

```bash
murakumo setup --connect
```

You should see ✓ *Connected to Snowflake as MURAKUMO_DBT* (role TRANSFORMER, warehouse
TRANSFORMING), then a red ✗ *Database SNOW_TEST does not exist, or TRANSFORMER cannot see it*.
That is expected: the database is named after the project, and nobody has created it yet. Under it
is a **Run this SQL** block:

```sql
use role accountadmin;
create database if not exists SNOW_TEST;
grant usage, create schema on database SNOW_TEST to role TRANSFORMER;
```

**16.4** Create the database. In Snowsight (<https://app.snowflake.com>, signed in as yourself):
**Projects → Worksheets → +** (top right) **→ SQL Worksheet**. Paste the three lines, select them
all (Cmd+A) and click **Run** (▶, top right). You should see
`Statement executed successfully.` Back in Terminal:

```bash
murakumo setup --connect
```

✓ *Database SNOW_TEST is there* and `Ready to build.`

> Rather use a database you already have? `export SNOWFLAKE_DATABASE=COMMERCE` builds there
> instead, and step 16.7 then drops schemas rather than the whole database.

☐ Connected, and the database check passes.

**16.5** Test on Snowflake:

```bash
murakumo verify --warehouse snowflake
```

About 20-30 seconds. You should see `verified: 4 models and snapshots built, 16/16 tests passed,
incremental rebuild passed on snowflake.` `verify` works in temporary `VERIFY_...` schemas and
drops them at the end, so **this leaves nothing behind in Snowflake**: that is on purpose.

☐ 16/16 on Snowflake.

**16.6** Build for real, and keep the tables:

```bash
murakumo build --sample 200
```

`--sample 200` puts 200 made-up rows in each raw table that is still empty (in real use, ingestion
fills them, or `--data` loads a file). You should see two `loaded ...: 200 rows (synthetic)` lines
and `built: 4 models and snapshots, 16/16 tests passed. Tables are in Snowflake database SNOW_TEST.`

In Snowsight, open **Data → Databases → SNOW_TEST** (refresh with ⟳ if it was open). You should
see the schemas **RAW_NYC_TLC**, **STAGING** and **MARTS**. Open **MARTS → Tables → FCT_TRIP →
Data Preview** to see rows.

☐ The tables are in Snowflake.

**16.7** Clean up, so the trial does not keep the test data. In a Snowsight worksheet:

```sql
use role accountadmin;
drop database if exists SNOW_TEST;
```

(If you built in `COMMERCE` instead: `drop schema if exists COMMERCE.RAW_NYC_TLC;` and the same
for `STAGING` and `MARTS`, only if they hold nothing else of yours.)

☐ Cleaned up.

---

## Part 17: Clean up

**17.1** In the canvas tab, press **Ctrl+C** to stop it.

**17.2** Optional: delete the test folder, and uninstall Murakumo the way you installed it:

```bash
rm -rf ~/murakumo-e2e
```

Route A: `uv tool uninstall murakumo`. Route B: `deactivate` (deleting the folder removed the
environment). Route C: `npm uninstall -g murakumo`.

☐ Done.

---

## If something goes wrong

| What you see | What to do |
| --- | --- |
| `command not found: murakumo` | Route A: `uv tool update-shell`, then open a new Terminal window. Route B: `source ~/murakumo-e2e/.venv/bin/activate`. Route C: check `npm install -g murakumo` finished without errors |
| Route C: *Murakumo runs on Python, and the npm package starts it with uv* | uv is not installed or not found: do 0.1, then open a new Terminal window |
| `no murakumo.yaml in ... or above` | You are not inside the project folder: `cd ~/murakumo-e2e/taxi_platform` |
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
| Route used (A uv / B pip / C npm) | | |
| 0 Tools | | |
| 1 Install | | |
| 2 Create the project folder | | |
| 3 Look at the project | | |
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
