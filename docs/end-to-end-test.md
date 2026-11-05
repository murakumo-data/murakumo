# End-to-end test: Murakumo 1.0.0

A step-by-step test of Murakumo 1.0.0 exactly as a new user meets it: install it with one command,
create a project folder, go into it, and build a data model on real data. It assumes no
experience with Python, dbt or warehouses: every command is given in full, along with what you
should see.

| Part | What it tests | Time |
| --- | --- | --- |
| 0-1 | Installing Murakumo with uv, pip or npm | 5-10 min |
| 2-4 | Creating a project folder with the guided setup, and going into it | 10 min |
| 5-9 | The canvas: the tour, finding and showing tables, editing, mapping, calculated columns, lineage, domains, undo, labels, notes and diagrams, generating, migrations | 65 min |
| 10-11 | Importing a data dictionary; editing YAML by hand alongside the canvas | 10 min |
| 12-14 | Building and testing on DuckDB, with made-up data and real data; schedules, pipelines, docs, plain SQL, cumulative tables, the run log, Polars and PySpark, loading only new files, running schedules here | 60 min |
| 15 | The checks a CI pipeline would run, CI from the canvas, working with a teammate in the git flow | 25 min |
| 16 | Optional: the same project on Snowflake: environments, your own schema, production, a pull request's clone | 35 min |
| 17 | Choosing the stack: connecting an API in the canvas, fetching and loading, the code for another stack, the decisions log | 25 min |
| 18 | The replication guide: reading it, proving it, loading by hand; unknown rows, the bus matrix, exposures, access and masking | 20 min |
| 19 | SCD types 3, 4 and 6, a Data Vault, a metric, Terraform on LocalStack, Iceberg in Polaris, Kafka through the consumer and Flink | 40 min |
| 20 | Cleaning up | 2 min |

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

You should see `murakumo 1.0.0`, then a short description that mentions `murakumo init` and
`murakumo setup`.

☐ Version 1.0.0.

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

**In the canvas** (once it is open, in Part 5): the gear at the bottom of the rail opens
**Settings**. **This machine** shows the same checklist, with **Test the connections**; **Project**
changes the warehouse, orchestrator, modelling pattern and the rest, as `murakumo config` does.

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

**5.1a The tour.** The first time, a short tour opens: a card points at each part of the canvas
in turn (the tool rail, the Explorer, how tables show, diagrams, undo) while the rest dims. Read
each and click **Next**, then **Done**. (It does not open again; **?** at the bottom left of the
canvas, then **Tour of the canvas**, starts it again.)

☐ The tour walks through the canvas.

**5.2** Look around. You should see:

- the top bar: **taxi_platform**, `4 tables · 4 relationships`, a **Find a table or column** box,
  four small view buttons, undo and redo arrows (greyed: nothing to undo yet), a blue chip like
  **15 files to write** and a green **Valid** chip;
- a narrow rail of icons down the left edge (hover one for its name), and next to it the
  **Explorer**: **Domains** (`mobility 4`), **Layers** (`raw 2`, `marts 2`), **Colour labels** and
  **Sources**;
- above the canvas, a tab **All tables** and **+ Diagram**;
- two raw tables with dark grey headers on the left (`yellow_tripdata`, `taxi_zone_lookup`) and two
  marts with blue headers on the right (`fct_trip`, `dim_taxi_zone`), joined by dashed lines. Keys
  have a small key icon, foreign keys a link icon. Each line has a crow's foot (three prongs) at the
  many end, and a circle and bar (*zero or one*) at the other end.

Zoom with the scroll wheel or the **+/−** buttons at the bottom left; drag the background to move
around. The sun/moon icon near the bottom of the rail switches between light, dark and your
computer's setting.

☐ Four tables and four lines.

**5.2a Read the lines.** Click **?** at the bottom of the rail, then **Notation and tips**. A panel
explains each symbol: ‖ exactly one, ○| zero or one, a crow's foot for many, and a dashed line for optional.
Close it with ×. Now hover over the line between `fct_trip` and `dim_taxi_zone`: a label
`pickup · N:1` appears, and the joined columns (`pickup_zone_id` and `zone_id`) are highlighted
in both tables. Click the line: they stay highlighted while it is selected. Click the empty
background to clear it.

☐ The legend opens; hovering or clicking a line highlights its columns.

**5.3** In the Explorer, under **Layers**, click the eye beside **marts**. The two marts disappear;
click it again to bring them back. Click the ▸ beside **marts**: it opens to its two tables; click
`dim_taxi_zone`, and the canvas moves to it and selects it. Press Esc or click the background.

☐ Layers hide and show, and the Explorer finds tables.

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

**5.7 Show tables your way.** Press **⌘2** (or the key icon among the view buttons): each table
shows only its key columns, with **+N columns** under them. **⌘3** shows only the tables' names,
**⌘4** their descriptions, and **⌘1** every column again.

Now click the ▾ at the left of `yellow_tripdata`'s header: it collapses to its name and
**+7 columns**, and its lines now meet its header. Click **+7 columns** to open it again.

Click the sliders button (the last of the view buttons). Type `6` in *Collapse tables with more
than … columns* and press **Enter**: `yellow_tripdata` and `fct_trip`, which have more, collapse.
Click **Expand all**, then clear the box and press **Enter**. In the first Terminal tab,
`cat model/layout.yaml` now shows a `collapsed:` list beside the positions.

☐ View modes switch, and tables collapse one by one or by size.

**5.8 Find anything.** Press **⌘K**. Type `dropoff`: the list shows `dropoff_at` (in `fct_trip`) and
`tpep_dropoff_datetime` (in `yellow_tripdata`). Press **↓** then **Enter**: the canvas moves to
`yellow_tripdata`, opens it in the inspector, and the `tpep_dropoff_datetime` row flashes blue.

☐ ⌘K finds tables and columns.

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

You should see `trip_minutes` in the columns and in the box on the canvas, and the chip in the top
bar turns yellow: **1 warning**. On the canvas, the `trip_minutes` row in `fct_trip` is tinted
amber with a small amber dot; hover over it to read the message. Click the chip: the Problems panel
says *fct_trip.trip_minutes: MK503 fct_trip.trip_minutes has no mapping; it will be null*. Click
it: the canvas goes to `fct_trip` and the `trip_minutes` row flashes. In the inspector's
**Mappings** section, `trip_minutes` is highlighted. Click the Explorer icon (the top one in the
middle group of the rail) to bring the Explorer back.

☐ The column is added, MK503 is shown, and the column itself is marked.

**6.4 Try a mistake.** In the `trip_minutes` row of **Mappings**, click the right-hand button,
**ƒx expression**. An expression editor opens under the row, with buttons for common functions
above it. Type the incomplete expression below:

```text
date_diff('minute',
```

Under the box, in red: *the expression is incomplete*, and **Save** is greyed out. Nothing can be
saved until the expression reads correctly.

☐ The bad expression is caught as you type.

**6.5 Write it with suggestions.** Select everything in the box (Cmd+A) and delete it. Now type
`date_diff('minute', yellow_tripdata.` (with the dot): a list of `yellow_tripdata`'s columns
appears under the box, each with its type. Type `tpep_p`; the list narrows to
`tpep_pickup_datetime`; press **Tab** to put it in. Type `, yellow_tripdata.tpep_d`, press **Tab**,
and type `)`. The box now reads, with keywords, functions, text and columns in different colours:

```text
date_diff('minute', yellow_tripdata.tpep_pickup_datetime, yellow_tripdata.tpep_dropoff_datetime)
```

Under it, in green: *✓ Reads yellow_tripdata.tpep_pickup_datetime,
yellow_tripdata.tpep_dropoff_datetime · returns bigint*.

Click **Preview**. A small table shows a dozen made-up pickup and drop-off times and, in bold, the
minutes between them. (*Made-up rows, run in DuckDB on this computer*: no warehouse is involved.)

Click **Save**. The editor closes; the middle box (*sources*) now lists the two columns the
expression reads, and the chip says **Valid**. On the **Code** tab, the SQL has a line with
`DATE_DIFF('MINUTE', ...) as trip_minutes`.

(The **☰** button next to the sources box still ticks sources in a list, for a column that copies
one column as it is.)

☐ The expression is checked, previewed and saved, and appears in the SQL.

**6.6 Add a table.** Click the **+** at the top of the rail (or press **T** with nothing selected).
Name: `fhv_tripdata`. Layer: `raw`. Source: `nyc_tlc`. Click **Add**.

A new box appears and is selected. Add three columns, one at a time, as in 6.3:

| Name | Type |
| --- | --- |
| `dispatching_base_num` | `string` |
| `pickup_datetime` | `timestamp` |
| `pickup_location_id` | `integer` |

Now copy a column from a table that already has it. Click **+ From a source table** (next to
**Columns**). Type `trip_distance` in the search box, tick `trip_distance` under
`yellow_tripdata`, and click **Add columns**. `fhv_tripdata` gets `trip_distance` with the same
type, `decimal(10,2)`.

☐ `fhv_tripdata` has four columns.

**6.6a Columns from the source.** On a table outside the raw layer, picking columns starts with
the tables it reads from. Press **T** and add `int_taxi_zone_lookup`, Layer `intermediate`. The
inspector says **No columns yet** with **Pick them from its source tables**: click it. At the top,
under *Where it most likely reads from*, is `taxi_zone_lookup` (the new table carries its name);
the other tables follow. (Do not call it `stg_nyc_tlc__taxi_zone_lookup`: that is the staging model
Murakumo already generates for the raw table, and the canvas says so, MK107.) Tick the box beside `taxi_zone_lookup` to take all four columns, and click
**Add columns**. All four arrive with their types, and its **Mappings** already say where each comes
from (`taxi_zone_lookup.LocationID`, ...). Three of them are marked amber on the canvas: their names
are not snake_case (MK307), which you could fix by renaming them here.

Now take it all back: press **⌘Z** (with nothing selected; or the ↶ in the top bar, which says
*Undo: int_taxi_zone_lookup: new column ... (+3 more)*). The four columns go, in one step.
Press **⌘Z** again: the table goes too.

☐ Columns come from the likely source, mapped, and undo takes them back.

**6.7 Add a relationship.** Click `fhv_tripdata`, scroll down the inspector to
**Relationships** and click **+ Relationship**. Choose:

| Field | Choose |
| --- | --- |
| This column | `pickup_location_id` |
| Points to table | `taxi_zone_lookup` |
| Its column | `LocationID (PK)` |
| Cardinality | `many_to_one` |

Click **Add relationship**. You should see a new line from `fhv_tripdata` to `taxi_zone_lookup`
(a crow's foot at the `fhv_tripdata` end, ‖ at the other), the top bar says
**5 relationships**, and in the `fhv_tripdata` box `pickup_location_id` now has the link icon of a
foreign key.

There are two other ways: press **R** for a form that picks both tables and columns, or hover over
a table and drag from the small round handle at the end of a column to a column of another table.

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

**6.10 Reorder columns.** Click `fct_trip`. In **Columns**, each row starts with a **⋮⋮** handle.
Drag `trip_minutes` by its handle up to just below `dropoff_at` (a blue line shows where it will
go) and let go. The column moves there, in the inspector and in the box on the canvas. (With the
keyboard: Tab to a handle and press Alt+↑ or Alt+↓.)

☐ The column moved.

**6.11 Add a calculated column.** Still on `fct_trip`, click **+ Calculated** (next to
**Columns**). Type `amount_per_mile` in the name box and leave the type box empty. In the
expression box type:

```text
round(yellow_tripdata.total_amount / nullif(yellow_tripdata.trip_distance, 0), 2)
```

Under it: *✓ Reads yellow_tripdata.total_amount, yellow_tripdata.trip_distance · returns double*.
The type box would use `double`; type `decimal(10,2)` in it instead, to choose the type yourself.
Click **Preview** to see it on made-up rows, then **Add column**.

`amount_per_mile` appears at the bottom of the columns, typed `decimal(10,2)`, with its mapping
already filled in, and the chip stays **Valid**.

Now try one that cannot work: click **+ Calculated** again and type `fct_trip.total_amount * 2`.
Under it, in red: *fct_trip.total_amount: read the tables fct_trip is built from, not its own
columns*. Click **Cancel**.

☐ The calculated column is added with its mapping; reading its own table is refused.

**6.12 Follow lineage.** In `fct_trip`'s columns, click **⇄** in the `trip_id` row. On the canvas,
`fct_trip.trip_id` and the seven `yellow_tripdata` columns it is made from are highlighted, joined
by moving dashed lines, and the other tables fade. A bar at the top says *Lineage of
fct_trip.trip_id*, with seven columns under **Comes from** and nothing under **Used by**. Hover
over a dashed line: the expression, `md5(concat_ws('|', ...))`, shows in a box drawn over the
tables. (`trip_id` also has a small ƒ beside its name on the canvas: it is calculated; hover the ƒ
to read the expression.)

In the bar, click `yellow_tripdata.total_amount`. Now it is that column's lineage: **Used by**
lists `fct_trip.amount_per_mile`, `fct_trip.total_amount` and `fct_trip.trip_id`. Close the bar
with ×.

Then in the first Terminal tab:

```bash
murakumo lineage fct_trip.amount_per_mile
murakumo lineage yellow_tripdata.total_amount --down
```

You should see trees: the first shows `fct_trip.amount_per_mile` with its expression, coming from
`yellow_tripdata.total_amount` and `yellow_tripdata.trip_distance` (both `raw`); the second shows
the three `fct_trip` columns that use `total_amount`.

☐ Lineage shows on the canvas and in the terminal, and they agree.

**6.13 Domains.** In the Explorer, **Domains** lists `mobility 4` and `(no domain) 1`
(`fhv_tripdata`). Click ▸ beside `mobility` to see its four tables; click the word `mobility` to
show only them on the canvas (a chip *only mobility ×* appears in the top bar; click it to show
everything again).

Click the ⚙ beside **Domains** to manage them. Type `Taxi analytics` in `mobility`'s **owner** box
and press **Enter**. In the empty row at the bottom, add a domain `finance` with owner
`Finance team`.

Close the panel (×, back to the Explorer) and click the eye beside **Domains**: each table gets a
coloured ring, and a dashed area labelled *mobility · Taxi analytics* is drawn behind the
`mobility` tables. Click `fhv_tripdata`: its **Domain** is empty; pick `mobility` from the list.
The area grows to include it.

Open the ⚙ again: `finance` has no tables, so its **×** works: click it. (`mobility`'s ×
is greyed out, because tables use it.) In the first Terminal tab:

```bash
grep -A3 "domains" murakumo.yaml
```

You should see `mobility` with `owner: Taxi analytics`, and the rest of the file, comments
included, as it was.

☐ Domains are managed from the canvas and shown as areas.

**6.14 Undo a removal.** Click `fhv_tripdata`, scroll to the bottom of the inspector and click
**Remove entity**. A confirmation lists what removing it reaches; click **OK**. The table and its
line to `taxi_zone_lookup` are gone. Now press **⌘Z**: a banner says *Undid: table fhv_tripdata
removed*, and it is back where it was, with its columns, its role `pickup` on the relationship and
its domain. In the first Terminal tab, `cat model/entities/nyc_tlc/fhv_tripdata.yaml` shows its
columns again.

Undo stops rather than overwrite work made outside the canvas. Open `model/entities/marts/fct_trip.yaml`
in a text editor, add a blank line at the end, and save. Back in the canvas, press **⌘Z**: it says
*the model files changed outside the canvas since, so undo stops here*, changes nothing, and the ↶
in the top bar is greyed out. (Your edit is kept; git still has every version.)

☐ Undo brings back a removed table, and never overwrites outside edits.

**6.15 Organise the model.** Three ways to keep a large model readable; all of them are saved in
`model/layout.yaml`, so the team sees them too, and none changes the model.

- **Colour labels.** In the Explorer, open **Colour labels** and click **+ Colour label**. Name it
  `Trip data`, pick a colour, and click **Add label**. Click `yellow_tripdata`, then hold **⌘** and
  click `fhv_tripdata`: a bar appears above the canvas, *2 tables*. Set its **Colour** to
  `Trip data`: both headers take the label's colour, and their meta line ends in `· Trip data`.
  Click the label's name in the Explorer to show only those two tables; click the *only Trip data*
  chip to show everything.
- **A note.** Click the empty background, then press **N**: a yellow note appears. Type
  `Trips land hourly from the TLC feed` and click elsewhere. Drag it next to `yellow_tripdata`.
- **A diagram.** Select `fct_trip` and `dim_taxi_zone` (⌘+click), then click **+ Diagram** above
  the canvas. Type `Trips` and press **Enter**: a tab **Trips 2** opens with just those two tables.
  In the bar, click **Arrange**: they line up, the dimension before the fact; **Put them back** in
  the banner undoes it. Click **All tables** to see everything again; the inspector's **Diagrams**
  row for any table puts it in or out of `Trips`.

Click the image icon on the rail, then **PNG image**: your browser saves `taxi_platform.png`, a
picture of the tables shown. In the first Terminal tab:

```bash
cat model/layout.yaml
```

You should see `labels:`, `colours:`, `notes:` and `diagrams:` after the positions.

☐ Labels, notes and diagrams are saved with the layout, and the canvas exports as an image.

---

## Part 7: Generate from the canvas

**7.1** Click the blue **N files to write** chip in the top bar. The **Changes** panel opens on the left. At the top
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

The chip in the top bar now says **... files to write · migration**. Open the Changes panel. At the top it
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

**In the canvas**, instead: **Sources** → **+ Add source**, Kind **Data dictionary (Excel)**, then
**Choose a file from this computer…** and pick the downloaded file. It is copied into `data/` (which
git ignores) and the name and path fill themselves in; **Add source**, then **Re-import** →
**Preview import** → **Import into the model**.

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

**In the canvas**: the pulse icon on the rail opens **Activity**; **Verify** runs the same command,
and its output appears as it comes. The command is shown above the output, to copy into a
terminal.

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

**In the canvas**: **Activity** → **Build…**. **Add a file…** adds each downloaded file (they go to
`data/`); pick each raw table's file in its list, leave **Made-up rows** at 0, and click **Build**.
The dialog shows the same `murakumo build --data ...` command. Rows a file could not fit into the
model's types are reported as rejected, with why.

☐ The tables are there to query.

**13.5 Browse a database from the canvas.** The project's DuckDB file is a database like any
other, so it can be browsed as a source. The canvas reads connection strings from environment
variables, so restart it with one set. In the **canvas tab**, press **Ctrl+C**, then:

```bash
export WAREHOUSE_DB="duckdb:///$PWD/taxi_platform.duckdb"
murakumo canvas
```

In the canvas, click **Sources** on the rail (the database icon; hover the icons for their names). You should see `nyc_tlc` and `open_meteo`, each
with its tables. Click **+ Add source**: Name `warehouse`, Kind **Database (PostgreSQL, DuckDB)**,
Variable `WAREHOUSE_DB` (the variable's name, not the connection string). Click **Add source**.
`warehouse` appears with `introspect:env:WAREHOUSE_DB`.

Click **Browse tables**, then **Connect and list tables**. You should see the schemas
(`marts`, `raw_nyc_tlc`, `raw_open_meteo`, `staging`, ...) with their table counts. Open
`staging` (click its name) and tick `stg_nyc_tlc__taxi_zone_lookup`; click its name to see its
columns and types. Click **Preview import**: *+ source warehouse* and
*+ table stg_nyc_tlc__taxi_zone_lookup*. Don't import it: this was only a look.

Now tick `fct_trip` under `marts` as well and click **Preview import** again. It refuses:
*MK401 fct_trip already exists in the marts layer; not overwritten by warehouse*, and
**Import into the model** is greyed out. An import never overwrites a table that belongs elsewhere.

Click **← All sources**. `warehouse` has no tables, so it has a **×**: click it to remove it from
`murakumo.yaml`.

☐ The database's tables are listed and previewed, a clash is refused, and the source is removed.

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

**14.2 Schedules.** In the canvas, click **Schedules** on the rail (the calendar icon). It says there are no
schedules yet, so everything is refreshed once a day at 06:00, and that pipelines for **airflow**
are written to `generated/orchestration/airflow/`. Click **+ Schedule**:

| Field | Enter |
| --- | --- |
| Name | `zones` |
| When | **Every hour** (the cron box shows `0 * * * *`) |
| Refreshes | `+dim_taxi_zone` |

Click **Add schedule**. It reads *every hour, on the hour · next ...*, with **2 tables**. Click
**2 tables**: on the canvas only `dim_taxi_zone` and `taxi_zone_lookup` stay bright. Now add a
second schedule, `nightly`, choose **Weekdays at 06:00**, and leave **Refreshes** empty: it reads
*every weekday at 06:00 UTC* and refreshes the rest (the other tables). Click `fct_trip`: the
inspector's **Refreshed** row says `nightly: every weekday at 06:00 UTC`.

In the first Terminal tab:

```bash
murakumo schedules
murakumo build --schedule zones
```

You should see the two schedules with their next runs, then a build of only the zone tables:
`built: 2 models and snapshots, N/N tests passed`.

**The evidence a schedule ran.** In the Schedules panel each schedule now says when it last ran
here, and how it ended. Click **Run now** on `nightly`: **Activity** shows `murakumo build
--schedule nightly` running; when it ends, `nightly` says *Last ran ...: succeeded*, and the clock
icon's **Runs** panel lists the run with its schedule. (An orchestrator's runs record themselves
the same way, in the warehouse's audit tables.)

☐ Schedules are added on the canvas, listed in the terminal, and one builds on its own.

**14.3 Pipelines.** Click the blue **N files to write** chip and write the files (the schedules
changed the dbt selectors and the pipelines). Then:

```bash
ls generated/orchestration/airflow generated/orchestration/airflow/dags
cat generated/dbt/selectors.yml
```

You should see `README.md`, `requirements.txt` and `dags/` with `taxi_platform__zones.py` and
`taxi_platform__nightly.py`; and in `selectors.yml`, a selector per schedule, selecting the models tagged with it (`schedule_nightly`).
Open `generated/orchestration/airflow/README.md` in a text editor: it lists the schedules and how
to run the DAGs in Airflow.

Optional (5 minutes; downloads Airflow, about 300 MB): run the `zones` DAG once, for real, on the
DuckDB file. Routes A and C have uv; route B: `pip install uv` first.

```bash
export AIRFLOW_HOME=~/murakumo-e2e/airflow AIRFLOW__CORE__DAGS_FOLDER=$PWD/generated/orchestration/airflow/dags AIRFLOW__CORE__LOAD_EXAMPLES=False AIRFLOW__COSMOS__ENABLE_DATASET_ALIAS=False MURAKUMO_DUCKDB_PATH=$PWD/taxi_platform.duckdb MURAKUMO_TARGET=dev
uv run --no-project --python 3.12 --with "apache-airflow==2.10.5" --with "astronomer-cosmos>=1.8" --with dbt-duckdb airflow db migrate
uv run --no-project --python 3.12 --with "apache-airflow==2.10.5" --with "astronomer-cosmos>=1.8" --with dbt-duckdb airflow dags test taxi_platform__zones
```

The last lines should include `state=success`.

☐ A pipeline per schedule is generated (and, optionally, the DAG runs).

**14.4 Pipeline docs.** Click **Pipeline docs** on the canvas rail (the book icon near the bottom). A new tab opens with the project's
docs: the domains, the two schedules and what each refreshes, the sources, and a section per
table. Type `fct` in **Find a table**, then click `fct_trip`: its grain, load, schedule, the tables
it comes from, and each column with where it comes from (`trip_id` shows its `md5(...)`
expression). The same page is in `generated/docs/index.html`, with a Markdown page per table:

```bash
ls generated/docs
```

☐ The docs show the model, its lineage and its schedules.

**14.5 Plain SQL.** Export the project as scripts that run without dbt:

```bash
murakumo export sql
ls export/sql/duckdb
```

You should see `Wrote N scripts and N test files to export/sql/duckdb/`, then `00_landing.sql`
and numbered scripts in the order they run (staging first, then `dim_taxi_zone` and `fct_trip`),
`tests/` and a `README.md`. Look at the incremental one:

```bash
cat export/sql/duckdb/*fct_trip.sql
```

It creates `marts.fct_trip` in full the first time, then adds only trips past the latest
`pickup_at`, replacing rows with the same `trip_id`. Remove the folder: `rm -rf export`.

**In the canvas**: **Activity** → **Export as plain SQL** writes the same files.

☐ The project exports as plain SQL.

**14.6 A cumulative table.** A cumulative table keeps each key's whole history, one row per key per
day. In the canvas, press **T** (or the **+** on the rail): Name `zone_activity`, Layer `marts`, **Add**. In the
inspector:

1. **Kind**: `cumulative`. Three new rows appear: **Per**, **Active on** and **Each day**.
2. Add a column `zone_id`, type `integer` (as in 6.3), and tick **NN**.
3. **Per**: `zone_id`. **Active on**: `fct_trip.pickup_at`. **Each day**: `trips = count(*)`.
4. In **Mappings**, set `zone_id`'s source to `fct_trip.pickup_zone_id`.

The chip says **Valid**. Write the files (the blue chip), then in the first tab:

```bash
murakumo migrate --name zone_activity
murakumo build
murakumo backfill zone_activity --from 2024-01-25 --to 2024-01-31
```

`build` adds the latest day with trips; `backfill` adds a week, one day after another (`2024-01-25:
built` ... `Backfilled zone_activity`). Look at one zone (routes A and C: start with
`uv run --with duckdb `, as in 13.3):

```bash
python -c "
import duckdb
con = duckdb.connect('taxi_platform.duckdb', read_only=True)
print(con.sql(\"select zone_id, days_active, dates_active[1:3], trips[1:3] from marts.zone_activity where snapshot_date = '2024-01-31' order by days_active desc limit 3\"))
"
```

You should see the busiest zones with `days_active` 7, their last three active days, newest first,
and the trips on each.

**In the canvas**: click `zone_activity`; under **Each day**, **Backfill** takes a first and last day
and runs the same backfill in **Activity**.

☐ The cumulative table builds and backfills.

**14.7 The run log.** Every run records itself in the `audit` schema:

```bash
murakumo runs
```

You should see **By day** (runs, rows ingested and rejected, failed tests), **Runs** (each build,
with its engine, status and test counts) and **Loads** (the rows each raw table took in 13.4).

**In the canvas**: the clock icon on the rail opens **Runs**: the same days, runs, loads (hover a
load for why rows were rejected) and the last run's failed tests; click a failed test to go to its
table.

☐ Runs and loads are recorded.

**14.8 The pipeline as Polars and PySpark jobs.** The same models can run as Python, reading the raw
tables from `taxi_platform.duckdb` and writing Parquet files. Route B: first
`pip install "murakumo[polars]"`; routes A and C use uv, which installs what the jobs need.

```bash
murakumo run --engine polars
```

You should see `Running the polars pipeline...` then `success: N models built, N/N tests passed.
Tables are in .../lake/polars.` Now PySpark. The first run downloads Spark and a Java runtime
(about 400 MB), so it takes a few minutes; later runs take seconds:

```bash
murakumo run --engine pyspark
```

`success: ...` again, in `lake/pyspark`. Compare both with the tables dbt built (routes A and C:
start with `uv run --with duckdb `):

```bash
python -c "
import duckdb
con = duckdb.connect('taxi_platform.duckdb', read_only=True)
print('dbt', con.execute('select count(*) from marts.fct_trip').fetchone()[0])
print('polars', con.execute(\"select count(*) from 'lake/polars/marts/fct_trip.parquet'\").fetchone()[0])
print('pyspark', con.execute(\"select count(*) from 'lake/pyspark/marts/fct_trip/*.parquet'\").fetchone()[0])
"
```

The three numbers are the same. Remove the output: `rm -rf lake`.

**In the canvas**: **Activity** → **Run as Python…**, pick the engine, **Run**.

☐ Polars and PySpark build the same tables as dbt.

**14.9 Load only what is new.** Real data arrives a file at a time. Point a build at a folder, and
each file goes in once. Put January's file (loaded in 13.4) in a landing folder, and build from it:

```bash
mkdir -p landing
cp ~/murakumo-e2e/data/yellow_tripdata_2024-01.parquet landing/
murakumo build --data yellow_tripdata=landing/
```

You should see `yellow_tripdata: nothing new (1 file(s) already loaded)`: the file is recognised by
its content, under its new name too. Now February arrives:

```bash
curl -L -o landing/yellow_tripdata_2024-02.parquet https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-02.parquet
murakumo build --data yellow_tripdata=landing/
```

You should see `loaded yellow_tripdata: ... rows (from landing/: 1 new file(s), 1 already loaded)`,
about 3 million rows. `fct_trip` is incremental: it takes the rows loaded since its last run, by load time, so
late trips with old dates are not missed. Check both months are there (routes A and C: start with
`uv run --with duckdb `):

```bash
python -c "
import duckdb
con = duckdb.connect('taxi_platform.duckdb', read_only=True)
print(con.execute(\"select strftime(pickup_at, '%Y-%m') m, count(*) from marts.fct_trip where pickup_at >= '2024-01-01' group by 1 order by 1\").fetchall()[:3])
print(con.execute('select table_name, path from audit.loaded_files').fetchall())
"
```

You should see January and February with about 3 million trips each, and `audit.loaded_files` with
the files loaded. In the canvas, click `yellow_tripdata`: **New rows** says `append`, and
`fct_trip`'s **Each run** says `load_time`.

☐ Only the new file was loaded, and the fact table took it.

**14.10 Run a schedule on this computer.** Add a schedule that runs every minute, then let
Murakumo run it here:

```bash
murakumo add schedule every_minute --cron "* * * * *" --select layer:raw
murakumo scheduler
```

You should see `Next: every_minute at ...`, then within a minute `Running every_minute: murakumo
build --schedule every_minute`, the build, and `every_minute succeeded; recorded in the run log.`
Press **Ctrl+C** to stop it. In the canvas, the Schedules panel says *Last ran ...: succeeded* for
`every_minute`, and **Run them here** does the same as `murakumo scheduler`, with **Stop**. Remove
the schedule: `murakumo remove schedule every_minute`.

☐ A schedule ran on its own, and the run log shows it.

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

**15.4 CI from the canvas.** In the canvas, click **CI** on the rail. It shows **GitHub Actions** selected,
`.github/workflows/murakumo.yml` **not written yet**, and the production option greyed out (a
DuckDB file has no production to deploy to). Click **Show the file**: the four checks above, as
steps. Click **Create CI workflow**: the badge says **up to date**.

Click **Run checks now**. The four checks run one after another, each ticked as it passes;
building on DuckDB takes a minute or so. At the end: **All checks passed.**

Now change the workflow by hand and see that Murakumo leaves it alone:

```bash
echo "# my own note" >> .github/workflows/murakumo.yml
murakumo ci; echo $?
```

You should see the difference (your line, marked `-`), an error saying the file has changes of its
own, and `1`. In the canvas (close and reopen **CI**), the badge says **changed by hand**. Put
Murakumo's version back with `murakumo ci --force`, then commit it:
`git add .github && git commit -m "CI"`.

☐ The workflow is written, its checks pass here, and changes made by hand are kept.

**15.5 Work with a teammate, in the git flow.** New work starts on a branch of its own from `dev`,
goes back into `dev` through a pull request, and `dev` is promoted to `stage`, then `main`. To try
it without a second person, make a shared repository on this computer and a teammate's copy:

```bash
git add -A && git commit -qm "Work so far"
git branch -M main
git init --bare ../team.git
git remote add origin ../team.git
git push -u origin main
murakumo branch start --push
murakumo branch new feature trip-note
git clone --quiet -b dev ../team.git ../teammate
```

`branch start` says `created dev from origin/main`, `created stage ...`, and that it pushed them;
`branch new` says `On feature/trip-note`. As the teammate, whose own work has been merged into `dev`
already, add a column to the zones there, with its code and migration:

```bash
cd ../teammate
git config user.name "Teammate" && git config user.email teammate@example.com
murakumo add column taxi_zone_lookup zone_code string
murakumo generate && murakumo migrate --name zone_code
git add -A && git commit -qm "Zone code" && git push -q
cd ../taxi_platform
```

Back as yourself, on `feature/trip-note`, add a different column, with its migration, and commit:

```bash
murakumo add column fct_trip note string
murakumo generate && murakumo migrate --name trip_note
git add -A && git commit -qm "Trip note"
murakumo sync --check
```

You should see `feature/trip-note: 1 commit(s) behind origin/dev, 1 ahead.` and, coming in from
dev, `additive  taxi_zone_lookup: new column zone_code (string)`. Now bring it in:

```bash
murakumo sync
ls migrations/duckdb
```

You should see the change brought in; *Migrations: 0006_trip_note.sql replaced by
0007_trip_note.sql, planned after dev's migrations* (the numbers depend on how many you wrote
before); and `Synced: ... committed ...`. `ls` shows the teammate's `zone_code` migration followed
by yours, with no number used twice. In the canvas, click **Team** on the rail (the branch icon):
`feature/trip-note`, up to date with dev, 2 commits ahead.

Now check what a pull request would be allowed to do, as the CI does on each one:

```bash
murakumo branch check --head feature/trip-note --base main
murakumo branch check --head feature/trip-note --base dev
murakumo branch promote dev
```

The first fails (*main takes stage*): new work goes into `dev`. The second passes. The third says
what promoting `dev` to `stage` would bring: the teammate's commit, with `zone_code`. In the canvas's
Team panel, **Start new work** makes branches the same way, and **Promote** → `dev → stage` shows
the same list (on GitHub or GitLab, with a button that opens the pull request).

☐ The teammate's change came in, and the migrations stay in order.

**15.6 What a change reaches.** In the canvas, click `taxi_zone_lookup` and click × next to
`LocationID` to remove it. Before anything is saved, a confirmation says it is a breaking change,
that it reaches the zone dimension's `zone_id` and the tests on it, and that it needs a migration.
Click **Cancel**: nothing changed. Then in the terminal:

```bash
murakumo diff
```

You should see `Model changes since origin/dev:` and `additive  fct_trip: new column note
(string)` (your change; the teammate's is already in dev), with the metrics and schedules it
reaches.

**In the canvas**: **Team** → **Compare with** `dev` → **Compare** lists the same changes and what
they reach; **Copy the report for a pull request** copies it as Markdown.

☐ Breaking edits are caught before they are saved.

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

**16.3** Look at the environments, and test the connection:

```bash
grep -A12 "environments:" murakumo.yaml
murakumo setup --connect
```

`murakumo.yaml` lists `dev`, `stage` and `prod`, each with a database (`SNOW_TEST_DEV`,
`SNOW_TEST_STAGE`, `SNOW_TEST_PROD`) and the branch that deploys it, and `personal_database:
MURAKUMO`, where everyone's own builds go. `setup --connect` says ✓ *Connected to Snowflake as
MURAKUMO_DBT* (role TRANSFORMER, warehouse TRANSFORMING), then a red ✗ *Databases MURAKUMO,
SNOW_TEST_DEV, SNOW_TEST_STAGE, SNOW_TEST_PROD do not exist*. That is expected: nobody has created
them yet. Under it is a **Run this SQL** block, the same as `murakumo env provision` prints: the
databases, a deployer role per environment, a developer role, and the grants.

**16.4** Create them. In Snowsight (<https://app.snowflake.com>, signed in as yourself):
**Projects → Worksheets → +** (top right) **→ SQL Worksheet**. Paste the SQL from `murakumo env
provision`, and add these two lines at the end, which let this test's one user act as a developer
and as production's deployer (in real use, people get the developer role and the CI user the
deployer roles):

```sql
grant role SNOW_TEST_DEVELOPER to role TRANSFORMER;
grant role SNOW_TEST_DEPLOYER_PROD to role TRANSFORMER;
```

Select it all (Cmd+A) and click **Run** (▶, top right). Back in Terminal:

```bash
murakumo env provision | head -3
murakumo setup --connect
```

✓ *Databases are there: MURAKUMO, SNOW_TEST_DEV, SNOW_TEST_STAGE, SNOW_TEST_PROD* and `Ready to
build.`

**In the canvas**, Settings → Project → **Environments** shows the same databases and branches,
and **Setup SQL…** the same SQL.

☐ Connected, and the databases exist.

**16.5** Test on Snowflake:

```bash
murakumo verify --warehouse snowflake
```

About 20-30 seconds. You should see `verified: 4 models and snapshots built, 16/16 tests passed,
incremental rebuild passed on snowflake.` `verify` works in temporary `VERIFY_...` schemas and
drops them at the end, so **this leaves nothing behind in Snowflake**: that is on purpose.

☐ 16/16 on Snowflake.

**16.6** Build your own copy, and keep the tables:

```bash
murakumo build --sample 200
```

`--sample 200` puts 200 made-up rows in each raw table that is still empty (in real use, ingestion
fills them, or `--data` loads files). Your own builds go to a schema of your own, so first you
should see `Building your own copy: the schema <your name> in MURAKUMO.`, then two
`loaded ...: 200 rows (synthetic)` lines and `built: 4 models and snapshots, 16/16 tests passed.`

In Snowsight, open **Data → Databases → MURAKUMO** (refresh with ⟳ if it was open). You should see
one schema named after you, holding every table: the raw ones, `STG_...`, `FCT_TRIP`,
`DIM_TAXI_ZONE`, and the audit tables (`RUN_LOG`, `ROW_COUNTS`...). Open **FCT_TRIP → Data Preview**
to see rows.

**16.6a Production, and a pull request on a clone of it.** Build production (its own database),
then do what a pull request's preview does: clone production, apply the branch's new migrations
on the clone, and build there.

```bash
murakumo build --env prod --sample 200
murakumo migrate --apply --env prod --mark-applied
murakumo env clone --env prod --prefix pr_1_
murakumo migrate --apply --env prod --prefix pr_1_
murakumo build --env prod --prefix pr_1_
murakumo env --env prod --prefix pr_1_
```

You should see the clones (`raw_nyc_tlc -> pr_1_raw_nyc_tlc`, ...), `... have run every migration`
(the clone carries production's record), a build into `pr_1_*`, and the `pr_1_*` schemas with
their tables, all in `SNOW_TEST_PROD`. Snowflake's clones are instant and take no extra space. Then
drop them, and your own:

```bash
murakumo env drop --env prod --prefix pr_1_ --yes
murakumo env drop --yes
```

**In the canvas**, the same pull-request steps without remembering the commands: **Team** →
**Test my change…**. It suggests a prefix from the branch name and copies `prod` (or the environment
you pick); **Run** each step in turn (copy, apply this branch's migrations, build and test there,
drop the copy). Each step shows its command and whether it worked, and lists what is under the
prefix after it.

☐ Your own schema, production and a pull request's clone sit apart, and drop cleanly.

☐ The tables are in Snowflake.

**16.7** Clean up, so the trial does not keep the test data. In a Snowsight worksheet:

```sql
use role accountadmin;
drop database if exists SNOW_TEST_DEV;
drop database if exists SNOW_TEST_STAGE;
drop database if exists SNOW_TEST_PROD;
drop database if exists MURAKUMO;   -- only if no other project uses it
drop role if exists SNOW_TEST_DEVELOPER;
drop role if exists SNOW_TEST_DEPLOYER_DEV;
drop role if exists SNOW_TEST_DEPLOYER_STAGE;
drop role if exists SNOW_TEST_DEPLOYER_PROD;
```

☐ Cleaned up.

---

## Part 17: Choose the stack, connect an API, fetch and load

The data so far came from files you downloaded. Here Murakumo fetches it: from Open-Meteo's
public weather API (no key needed), through the extract code it writes, into a landing folder,
then into a raw table. Then you try another stack (Snowflake, Snowpipe and Lambda) to see the
code it would write, and look at the decisions log. Work in `~/murakumo-e2e/taxi_platform`, with
the canvas running ([stack.md](stack.md) describes everything here).

**17.1** Look at the stack:

```bash
murakumo stack
```

You should see a table of the parts: Warehouse `duckdb`, Extract `python`, Landing `landing`,
Load `build`, Transform `dbt`, Python, Orchestrator `airflow` and CI `github`, each with what it
means. Then a warning that no source has a connection yet.

☐ The stack is listed.

**17.2** Connect the API in the canvas. Open **Sources** (the database icon) → **+ Add source**:

- **Name:** `open_meteo`.
- **Kind:** **Fetched by Murakumo (REST API, SFTP, database, files, protobuf)**.

Click **Next: its connection**. Leave **Kind** on **REST API**, and fill in:

| Field | Value |
| --- | --- |
| Base URL | `https://archive-api.open-meteo.com/v1` |
| Request 1: path (beside GET) | `/archive` |
| Raw table | `weather_hourly` |
| Parameters (one a line) | `latitude=40.78`, `longitude=-73.97`, `start_date=2024-01-01`, `end_date=2024-01-07`, `hourly=temperature_2m,precipitation` |
| Records at | `hourly` |

Click **Send**.

You should see **200**, the time it took, and **168 records on the first page**, with a table of
`time`, `temperature_2m` and `precipitation`. **Raw response** shows the JSON as it came back.

☐ The request works.

**17.3** Click **Make weather_hourly from these records**. Then type a reason in **Why?** (`Hourly
weather to explain demand`) and click **Save connection**.

You should see:

- a `weather_hourly` table on the canvas, from source `open_meteo`, with `time` as a timestamp and
  the other two as doubles;
- the message *Saved open_meteo's connection in murakumo.yaml*;
- the source in **Sources** marked **Fetched by Murakumo: rest**.

☐ The table and connection are saved.

**17.4** Click **Test**.

You should see: ✓ *weather_hourly: 200 in … ms, 168 record(s) on the first page*.

Then click **Fetch now**.

You should see **Activity** open on `murakumo ingest --source open_meteo`, ending with
*open_meteo.weather_hourly: 168 new, 1 file(s) landed*.

☐ Tested and fetched from the canvas.

**17.5** The same from the terminal, and load it:

```bash
murakumo source request open_meteo
ls landing/open_meteo/weather_hourly
murakumo load
```

You should see:

- the first five records as JSON;
- one `weather_hourly_<date and time>_0001.jsonl` file;
- *loaded weather_hourly: 168 rows*.

Fetch and load again:

```bash
murakumo ingest
murakumo load
```

A second file lands, but the API sent the same records, so its content matches the file already
loaded: *weather_hourly: nothing new (1 file(s) already loaded)*. A table loaded in full takes only
the latest fetch, and only when it changed. `landing/` is in `.gitignore`.

☐ Fetched and loaded from the terminal.

**17.6** The code it writes. Generate, then run the extract script on its own, with no Murakumo
involved:

```bash
murakumo generate
cat generated/ingestion/README.md
python3 generated/ingestion/python/extract.py generated/ingestion/python/sources/open_meteo.json
grep -n "extract_open_meteo" generated/orchestration/airflow/dags/*.py
```

You should see:

- the README's steps: set the secrets (none here), fetch, load;
- the script printing *open_meteo.weather_hourly: 168 new row(s), 1 file(s) landed*;
- in the DAG of the schedule that refreshes `weather_hourly`, an `extract_open_meteo` task, then a
  `load` task, before the build: `extract_open_meteo >> load >> …`.

☐ The extract code runs on its own, and the DAG fetches and loads first.

**17.7** Choices that do not go together. In the canvas, open **Stack** (the layers icon). Under
**Load**, pick **snowpipe**. Type a reason under **Why?** and click **Save 1 change**.

You should see two red problems under Load and Landing: *Snowpipe loads into Snowflake; the
warehouse is duckdb*, and that Snowpipe needs files in S3. The top bar shows errors (MK701 in
**Problems**).

Press **⌘Z** (Ctrl+Z).

The load is **build** again and the errors are gone.

☐ The stack is checked, and the change undoes.

**17.8** See what another stack would write. Switch to Snowflake with Lambda and Snowpipe, look at
the code, then switch back:

```bash
murakumo stack set warehouse=snowflake extract=lambda load=snowpipe landing=s3://example-bucket/taxi --why "Trying the cloud stack"
murakumo generate
grep -n "url =\|create pipe\|temperature_2m" generated/ingestion/snowpipe/prod.sql
grep -n "Handler\|NoEcho\|Schedule:" generated/ingestion/lambda/template.yaml
murakumo stack deploy --env prod
```

You should see:

- the stage on `s3://example-bucket/taxi/`;
- the raw tables;
- a pipe per raw table whose COPY reads `$1:"temperature_2m"::FLOAT` and fills `_ingested_at`
  and `_batch_id` from the file's metadata;
- a Lambda function per source, `extract.handler`, on a schedule;
- the deploy plan, with nothing run: provisioning, the storage integration and the pipes
  (Murakumo runs these), the CI workflow (Murakumo writes it), and the Lambda deploy (you run it).

Put the project back:

```bash
murakumo stack set warehouse=duckdb extract=python load=build landing=landing --why "Back to DuckDB for the test"
murakumo generate
```

☐ The cloud stack's code is written; the project is back on DuckDB.

**17.9** The decisions log. In the canvas, open **Stack** → **Decisions**:

You should see, newest first, each change with what it was before and after, when, by whom and
why: the stack changes, and *sources.open_meteo.connection* with *Hourly weather to explain
demand*.

Add one of your own: type `Weather is fetched for one week only` in **Add a decision**, a reason,
and click **Record it**. Then:

```bash
murakumo decisions
murakumo generate
sed -n '/## Decisions/,$p' generated/docs/stack.md
```

The decision is listed in the terminal, and in the platform page of the pipeline docs.

☐ Decisions are recorded, with their reasons.

---

## Part 18: The replication guide, and the modelling patterns

Everything Murakumo built so far can be rebuilt by hand, without it: the replication guide says how,
step by step. Here you read it, prove it is complete, follow a few steps yourself, and try the
modelling patterns it documents. Work in `~/murakumo-e2e/taxi_platform`, with the canvas running
([guide.md](guide.md) and [modelling-patterns.md](modelling-patterns.md) describe everything here).

**18.1** Write the guide and look at it:

```bash
murakumo guide
ls generated/guide generated/guide/sql
```

You should see the chapters listed with how many steps each has, from `0. Overview` to
`11. Troubleshooting`, and the path of the guide's page. The folder has a Markdown file per chapter,
`README.md`, `index.html`, `rebuild.sh`, and `sql/` with `build`, `load` and `tests`.

☐ The guide is written.

**18.2** Open it as one page:

```bash
murakumo guide --open
```

Your browser opens *Rebuilding taxi_platform by hand*:

- the chapters down the side, with *0 of N steps done*;
- each step with **Why:**, the commands or SQL in full (each with a **Copy** button), **You should
  see:**, and a **Done** box.

Tick the box under **0.1 Read the architecture**. The count goes up by one. Reload the page: it
is still ticked.

Open **5. Transformations**, then **Build fct_trip**. You should see:

- a table of its columns: where each comes from, and its expression;
- its dbt SQL;
- the same model as plain SQL;
- its tests, as queries.

☐ The guide reads well, and the ticks stay.

**18.3** Prove the guide is complete:

```bash
murakumo guide --check
```

It takes about a minute. It rebuilds the project in a scratch folder from the guide's scripts alone,
then with Murakumo, twice, and compares them. You should see:

*The guide rebuilds the project: all N tables match a Murakumo build, in rows and values, after each
of 2 runs of new files; every test passes, and running it again changes nothing.*

☐ The check passes.

**18.4** Follow a few steps by hand, with the DuckDB command-line client. If `duckdb --version`
says the command is not found, install it first (`brew install duckdb`, or see
<https://duckdb.org/docs/installation>). Then build the raw tables, and load the weather that
Part 17 fetched, into a new file:

```bash
duckdb handmade.duckdb < generated/guide/sql/build/00_landing.sql
duckdb handmade.duckdb < generated/guide/sql/load/00_audit.sql
duckdb handmade.duckdb < generated/guide/sql/load/*weather_hourly.sql
duckdb handmade.duckdb -c "select count(*) from raw_open_meteo.weather_hourly"
```

You should see `168`. Murakumo took no part: this is the guide's own SQL. Run the load line again,
then the count: still `168`, because the file was already loaded. `audit.loaded_files` remembers
it:

```bash
duckdb handmade.duckdb -c "select table_name, path, rows_ingested from audit.loaded_files"
```

Then delete the file: `rm handmade.duckdb`.

☐ The raw table loads by hand, once.

**18.5** Unknown members. Open `generated/dbt/models/marts/mobility/dim_taxi_zone.sql`, and scroll
to the end:

```bash
tail -12 generated/dbt/models/marts/mobility/dim_taxi_zone.sql
grep -n coalesce generated/dbt/models/marts/mobility/fct_trip.sql
```

You should see:

- `union all`, then a row with `-1` and `'Unknown'`: the zone that trips point at when their zone
  is missing;
- in `fct_trip`, `coalesce(..., cast(-1 as INTEGER))` for `pickup_zone_id` and `dropoff_zone_id`.

New projects have this on (`unknown_members: true` in `murakumo.yaml`). In the canvas, click
`dim_taxi_zone`: the inspector has **Unknown row** (*as the project*) and **Conformed**.

☐ Dimensions have an unknown row, and facts point at it.

**18.6** The bus matrix and an exposure. Open **Bus matrix** on the rail (the grid icon):

- **Bus matrix** shows `fct_trip` with a tick under `dim_taxi_zone`.
- **Exposures** → **+ Add an exposure**: name `trips_dashboard`, owner `Ops`, link
  `https://bi.example.com/trips`, and tick `fct_trip` and `dim_taxi_zone` under **Reads**. Click
  **Save exposures**.

Then:

```bash
cat model/exposures.yaml
murakumo generate
cat generated/dbt/models/_exposures.yml
```

You should see `trips_dashboard` in both, the second with `depends_on: ref('fct_trip')` and
`ref('dim_taxi_zone')`. The guide's architecture diagram and reference chapter list it too.

☐ The exposure is saved and generated.

**18.7** Access and masking, in the canvas:

- Click `fct_trip`. In the inspector, set **Access** to `public`. `model/entities/marts/fct_trip.yaml`
  now has `access: public`.
- Open **Settings** → **Personal data**. Set **PII columns** to *redacted (\*\*\*)*, and type
  `ANALYST_PII` in **Roles that see them**. `murakumo.yaml` now has a `governance:` section with
  both.

The masking policies are for Snowflake. To see them (nothing is run):

```bash
murakumo generate --warehouse snowflake --check
```

It reports files out of date, including `ddl/snowflake/masking.sql`: the policies a Snowflake
project would get. Then put the canvas back: **Access** to empty, and **PII columns** to *shown as
they are*.

☐ Access and masking are saved from the canvas.

**18.8** In the canvas, open **Guide** on the rail (the book with a tick). The same chapters as
the page, as the model is now. The tick you made in 18.2 is not here: the page and the canvas keep
their ticks in different places. Click **Check it rebuilds the project**: **Activity** runs
`murakumo guide --check`, and ends with the same message as 18.3.

☐ The guide is in the canvas.

---

## Part 19: History types, the Data Vault, metrics, Terraform, open tables and streaming

The rest of the platform: three more ways to keep a dimension's history, a Data Vault, metrics
defined once, the platform as Terraform, tables any engine can read, and Kafka. Work in
`~/murakumo-e2e/taxi_platform`, with the canvas running. Steps 19.4 to 19.6 need Docker
(Docker Desktop, free for personal use): nothing in the cloud, nothing to pay for.
[modelling-patterns.md](modelling-patterns.md) and [stack.md](stack.md) describe everything here.

**19.1** SCD type 3. In the canvas, select **dim_taxi_zone**. In the inspector, set **History** to
`scd3`, and **Tracked** to `zone_name`. Then:

```bash
murakumo generate
grep -n "previous_zone_name" generated/dbt/models/marts/*/dim_taxi_zone.sql | head -3
murakumo verify
```

You should see `previous_zone_name` in the model, and `verified: ... passed`. Try `scd6` (a
`current_zone_name` on every version) and `scd4` (a `dim_taxi_zone_history` table beside it) the
same way, then set it back to `scd1`.

☐ The history types generate and verify.

**19.2** A Data Vault. Create `model/entities/vault/hub_zone.yaml`:

```yaml
name: hub_zone
layer: intermediate
kind: hub
attributes:
  - {name: zone_number, type: bigint, nullable: false, key: natural}
mappings:
  - {target: zone_number, sources: [taxi_zone_lookup.LocationID]}
```

and `model/entities/vault/sat_zone.yaml`:

```yaml
name: sat_zone
layer: intermediate
kind: satellite
attributes:
  - {name: zone_number, type: bigint, nullable: false, key: natural}
  - {name: borough, type: string}
  - {name: zone_name, type: string}
mappings:
  - {target: zone_number, sources: [taxi_zone_lookup.LocationID]}
  - {target: borough, sources: [taxi_zone_lookup.Borough]}
  - {target: zone_name, sources: [taxi_zone_lookup.Zone]}
```

In the canvas, draw a relationship from **sat_zone** to **hub_zone** on `zone_number`. Then:

```bash
murakumo validate --fix
murakumo verify
```

You should see `valid: 0 error(s)`, then `verified`, with tests on `hub_zone_hk`. Open
`generated/dbt/models/intermediate/core/hub_zone.sql`: the hash key is md5 of the trimmed,
upper-cased key, and each run adds only keys not seen before.

☐ The hub and satellite build and test.

**19.3** A metric. In the canvas, open **Bus matrix** (the grid icon) → **Metrics** → **Add a
metric**: name `trips`, counted on `fct_trip`, expression `count(*)`, by date `pickup_at`, grain
`day`. **Save metrics**. Then:

```bash
murakumo generate
sed -n '1,40p' generated/dbt/models/semantic/_semantic.yml
murakumo verify
```

You should see a semantic model `fct_trip` with a measure `trips`, the metric `trips`, and
`metricflow_time_spine`. `verify` passes (dbt checks the semantic layer when it reads the
project). Optionally, query it:

```bash
pip install "dbt-metricflow[dbt-duckdb]"
murakumo build --sample 200
cd generated/dbt && DBT_PROFILES_DIR=. mf query --metrics trips --group-by metric_time__month; cd ../..
```

☐ The metric is in the semantic layer.

**19.4** Terraform, tried on LocalStack. In **Stack**, set **Infrastructure as code** to
Terraform, **Extract** to `lambda` and **Landing** to `s3://taxi-landing/raw`. Then:

```bash
murakumo generate
ls generated/terraform
docker run -d --rm --name e2e-localstack -p 4566:4566 -v /var/run/docker.sock:/var/run/docker.sock localstack/localstack:4.0
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test
terraform -chdir=generated/terraform/aws init
terraform -chdir=generated/terraform/aws apply -var endpoint=http://localhost:4566
```

Type `yes` when it asks. You should see `Apply complete! Resources: ... added`: the bucket, the
extract function and its schedule, on LocalStack. Remove them, then put the stack back:

```bash
terraform -chdir=generated/terraform/aws destroy -var endpoint=http://localhost:4566
docker stop e2e-localstack
murakumo stack set extract=python landing=landing iac=none --why "end-to-end test"
```

☐ Terraform applies and destroys on LocalStack.

**19.5** Open tables: Iceberg in a local catalog. In **Settings** → **Open tables**, set **Table
format** to Apache Iceberg, **Catalog** to Apache Polaris, and **Catalog warehouse** to `taxi`.
Then:

```bash
murakumo generate
docker compose -f generated/lakehouse/docker-compose.yml up -d
bash generated/lakehouse/setup.sh
murakumo build --sample 50
```

You should see *The catalog taxi is ready*, then `built: ... passed`. The marts are Iceberg tables
now, in Polaris, with their files in LocalStack's S3:

```bash
curl -s http://localhost:4566/taxi?list-type=2 | grep -o '<Key>marts/[^/]*' | sort -u
```

You should see `marts/dim_taxi_zone` and `marts/fct_trip`. Stop it, and set **Table format** back
to the warehouse's own tables:

```bash
docker compose -f generated/lakehouse/docker-compose.yml down
```

☐ The marts are written as Iceberg tables.

**19.6** Streaming from Kafka. In **Sources**, give `nyc_tlc` a connection of kind **Kafka**:
brokers `localhost:19092`, a topic `zones` for the table `taxi_zone_lookup`. Set that table's
**New rows** to `append` in the inspector. In **Stack**, set **Streaming** to Flink. Then:

```bash
murakumo generate
docker compose -f generated/streaming/docker-compose.yml up -d
docker compose -f generated/streaming/docker-compose.yml exec redpanda rpk topic create zones
echo '{"LocationID": 501, "Borough": "Queens", "Zone": "Test zone", "service_zone": "Boro Zone"}' | \
  docker compose -f generated/streaming/docker-compose.yml exec -T redpanda rpk topic produce zones
murakumo ingest
murakumo load
```

You should see `nyc_tlc.taxi_zone_lookup: 1 new` (the consumer reads what is new since its last
run), then the load. Now continuously, with Flink: the job reads the topic from inside Docker, so
set the connection's brokers to `redpanda:9092`, then:

```bash
murakumo generate
bash generated/streaming/flink-jars.sh
docker compose -f generated/streaming/docker-compose.yml exec jobmanager ./bin/sql-client.sh \
  -j /opt/flink/usrlib/flink-sql-connector-kafka.jar -f /project/generated/streaming/flink/nyc_tlc.sql
```

You should see `Job ID: ...`, and <http://localhost:8081> lists the running job. Produce another
message as above; within two minutes a `part-...` file appears in
`landing/nyc_tlc/taxi_zone_lookup/`, and `murakumo load` loads it. Stop it all:

```bash
docker compose -f generated/streaming/docker-compose.yml down
```

Then remove the Kafka connection and set **Streaming** back to the consumer.

☐ Messages reach the raw table, by the consumer and by Flink.

## Part 20: Clean up

**20.1** In the canvas tab, press **Ctrl+C** to stop it.

**20.2** Optional: delete the test folder, and uninstall Murakumo the way you installed it:

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
| 5 Canvas: tour, view, find, view modes, collapse | | |
| 6 Canvas: edit, map, relate, calculate, lineage, domains, undo, organise | | |
| 7 Generate and diff | | |
| 8 Migrations on the canvas | | |
| 9 Editing alongside the canvas | | |
| 10 Import a dictionary | | |
| 11 Fix by hand in a text editor | | |
| 12 Verify with made-up data | | |
| 13 Verify with real data, build, browse as a source | | |
| 14 Generated code, schedules, pipelines, docs, plain SQL, cumulative, run log, Polars and PySpark, new files only, schedules here | | |
| 15 CI checks, CI from the canvas, git flow with a teammate, impact | | |
| 16 Snowflake (optional): environments, own schema, pull-request clone | | |
| 17 Stack: connect an API, fetch, load, another stack's code, decisions | | |
| 18 Replication guide: read, check, load by hand; unknown rows, bus matrix, exposures, access, masking | | |
| 19 History types, vault, metrics, Terraform, Iceberg, Kafka and Flink | | |
| 20 Clean up | | |

Also note anything that was confusing, slow or surprising, even if it worked. That is as useful
as a failure.
