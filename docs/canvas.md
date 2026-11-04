# The canvas

The canvas is a visual editor for the model: entities as boxes, relationships as lines between
their columns. It reads and writes the same YAML files as the CLI, through the same edit
functions, so a change made on the canvas is exactly the change `murakumo add`, `rename` or
`remove` would make, and it shows up in `git diff` the same way.

```bash
pip install "murakumo[canvas]"
murakumo canvas                 # in a project folder; opens http://127.0.0.1:8765
murakumo canvas path/to/project --port 9000 --no-open
```

## Finding your way around

- **The top bar**: the project, **Find a table or column** (⌘K), how tables show (all columns,
  keys only, names only, descriptions: ⌘1 to ⌘4, and when tables collapse), **undo** and
  **redo**, whether the generated code is up to date, and the problems.
- **The tool rail**, on the left: add a table (T), a relationship (R) or a note (N), arrange the
  tables, export the canvas as an image; then the panels (Explorer, Sources, Schedules, Team,
  CI, Runs, Activity, Changes, Problems), settings, the pipeline docs, light or dark, and help.
  Hover an icon for its name.
- **The Explorer** lists every table by domain, layer, colour label and source, each opening to
  its tables and their columns; its search box finds tables and columns. Click a table to go to
  it; click a domain to show only its tables; the eye beside a layer hides it.
- **Diagram tabs**, above the canvas: **All tables**, and any diagrams saved for a part of the
  model.
- **The inspector**, on the right, opens when you click a table or a line.
- **Help** (the ? at the bottom of the rail): a short tour, which also opens the first time, the
  keyboard shortcuts (press ?), and the notation.

## What you can do

- **See the model.** Raw tables are grouped by source, then staging, intermediate, marts and
  semantic, left to right. Inside a group, a table sits to the left of the tables it points at,
  so `lineitem → orders → customer → nation → region` reads left to right. Each table's header is
  its layer's colour (or its colour label's), with key icons for primary and foreign keys and ƒ
  for calculated columns. Drag boxes to arrange them; positions are saved to `model/layout.yaml`,
  keyed by entity id, so a rename keeps its place. Commit the file to share the layout.
  **Shift**+drag on the background selects several tables (⌘/Ctrl+click adds one), and dragging
  any of them moves them all.
- **Undo anything.** Every change is saved to the model files as you make it, and **⌘Z** (or ↶ in
  the top bar) takes it back; **⇧⌘Z** redoes it. The button says what it would undo: "table
  dim_zone removed". A removed table comes back with its columns, mappings, relationships and
  place. Undo goes back up to 100 changes made in this canvas; if the files were changed some
  other way since (your editor, `murakumo sync`), it stops rather than overwrite that work, and
  git still has every version. Moving tables is not undone this way; **Arrange** has its own
  **Put them back**.
- **Show tables your way.** All columns, keys only, names only, or each table's description (⌘1
  to ⌘4). Each table's ▾ collapses it to its name and "+12 columns"; lines then meet its header.
  The slider button beside the view modes collapses every table with more than a number of
  columns, or all of them. Collapsed tables are saved in `model/layout.yaml`, so the team sees
  the same.
- **Organise a large model.**
  - **Colour labels**: name a colour (Finance, PII, Deprecated) in the Explorer and give it to
    tables in the inspector, or to several at once; their headers take its colour, and clicking
    a label shows only its tables.
  - **Notes**: N adds a sticky note; double-click to write, drag to move, pick its colour.
  - **Diagrams**: select tables, then **+ Diagram** above the canvas, to save a view of them as a
    tab. Double-click a tab to rename it. The inspector's **Diagrams** row puts a table in or out
    of each one. A note belongs to the diagram it was written on.
  - **Arrange**: lays the tables out left to right or top to bottom, the dimensions before the
    facts that point at them, so lines run one way.
  - **Export**: the tables shown, as a PNG or SVG image.
  - **Several tables at once**: with two or more selected, a bar sets their domain, layer or
    colour label, arranges them, or removes them, each as one change that one undo takes back.

  Labels, notes and diagrams live in `model/layout.yaml` beside the positions; the model files
  do not change.
- **Read the relationships.** Lines use crow's-foot notation: at each end, ‖ exactly one,
  ○| zero or one, a crow's foot for many (with ○ when it may be none); a dashed line is optional.
  Hover over a line to see its role and cardinality (`pickup · N:1`) and the joined columns
  highlighted in both tables; click it to keep them highlighted and edit it. **Help → Notation and
  tips** explains the symbols. When two tables are dragged one above the other, the
  line goes round them rather than behind them.
- **Edit an entity.** Click it to open the inspector: rename it, change its layer, kind, source,
  domain and description, and add, rename, retype or remove columns and set primary keys and
  NOT NULL. **+ From a source table** brings in columns of other tables: the tables it most
  likely reads from come first (those its mappings and relationships name, the raw table a
  staging table is named after, then the layer before), and you tick the columns you want, or a
  whole table. They keep their names, types and descriptions and, outside the raw layer, are
  mapped to where they came from, all in one change. A new table with no columns offers this
  straight away. Tables with more than six columns get a **Find a column** box, which filters the
  columns and mappings. Drag the inspector's left edge to make it wider. **+** on the rail (or T)
  adds a new table.
- **Reorder columns.** Drag a column by its **⋮⋮** handle in the inspector, or focus the handle
  and press Alt+↑ or Alt+↓. Generated tables and dbt models list columns in the new order. Tables
  that already exist keep theirs, because no warehouse reorders columns in place; the Changes
  panel says so, and nothing needs a migration.
- **Add a relationship.** Press R (or the line icon on the rail) and pick both tables and
  columns; or, in the inspector's **Relationships** section, click **+ Relationship** and pick the
  column, the table it points to, its column and the cardinality. Or hover over an
  entity to show the handles beside each column, then drag from the foreign key column to the
  column it points at, on another entity. A dragged relationship is many-to-one; click the line to change the cardinality, make it optional, or
  give it a role (needed when two relationships join the same tables, like pickup and drop-off
  zones).
- **Map columns.** For entities in staging, intermediate, marts and semantic, the inspector lists
  every column with where it comes from: `entity.column` sources (suggested as you type, or
  ticked in a list with **☰**) and an optional SQL expression (**ƒx**), in DuckDB SQL and
  translated for your warehouse. Columns without a mapping are highlighted.
- **Calculate columns.** **ƒx** opens the expression editor, and **+ Calculated** in the Columns
  section adds a new column with its expression in one step. The editor highlights the SQL,
  suggests tables, their columns and functions as you type (type a table name and a dot; Tab or
  Enter accepts; Ctrl+Space asks), and checks the expression as you go: which columns it reads,
  whether they all exist, the type it returns (offered as the new column's type) and how it reads
  in your warehouse's SQL. **Preview** runs it on a dozen made-up rows in DuckDB on your computer,
  showing the inputs and the result; nothing touches the warehouse. Saving sets the mapping's
  sources to the columns the expression reads. An expression reads the tables an entity is built
  from, not the entity's own columns.
- **Follow lineage.** **⇄** beside a column in the inspector (or **Lineage**, for the whole
  table) shows where the column comes from and what uses it: those columns are highlighted,
  linked by moving dashed lines, and everything else fades. Hover a link to see the expression
  that makes it, drawn over the tables and wrapped when it is long. The bar at the top lists the columns it comes from and the columns that use it;
  click one to follow it. `murakumo lineage` shows the same in a terminal.
- **Group by domain.** The Explorer's **Domains** lists each domain and its tables; click one to
  show only its tables. Its ⚙ manages the domains in `murakumo.yaml`: add one, rename one (its
  tables follow), set its owner, or remove one no table uses. The eye beside it shades an area,
  with the domain's name and owner, behind each domain's tables. An entity's domain is picked from a list
  in the inspector. Each domain becomes a dbt group; see [generating](generating.md#domains).
- **Add and import sources.** **Sources** lists the sources, with where each is read from and how
  many tables it has. **+ Add source** declares a new one in `murakumo.yaml`: a database by the
  name of an environment variable that holds its connection string (the string itself never goes
  into a file), Snowflake by its database name (the rest comes from the `SNOWFLAKE_*` variables),
  or a data dictionary, data file or OpenAPI spec by its path or URL. **Browse tables** connects
  to a database source and lists its schemas, tables and columns, read from the catalogue, never
  the rows; tick the tables you want (tables already in the model start ticked), **Preview
  import** shows what would be added, changed or removed, and **Import into the model** saves it.
  Other sources have **Re-import**, with the same preview. Connection errors are shown with any
  password removed.
- **See the generated code.** The inspector's **Code** tab shows the dbt SQL generated for the
  entity and its table's DDL, updated as you edit.
- **Write the code.** The top bar says how many files in `generated/` are out of date with the
  model. Click it for the **Changes** panel: every file that would change, each with its diff,
  and one button, **Write N files to generated/**, that writes them, as `murakumo generate` does.
- **Write migrations.** When a change affects a table that keeps data (a landing table, an
  incremental model or a snapshot), the Changes panel explains that it needs a migration too,
  shows it with its SQL, and the button becomes **Write migration #N and M files**: it writes the
  migration to `migrations/` first, then the code, so the two never get out of step. See
  [migrations](generating.md#migrations). The first migration, the baseline, has its own button.
- **Schedule refreshes.** **Schedules** lists when each part of the project is refreshed, each time
  in words with its next run: add one with a preset or a cron time, choose what it refreshes
  (`domain:`, `layer:`, `source:` or a table, suggested as you type), set the time zone, and
  click a schedule's table count to highlight its tables. The inspector says which schedule
  refreshes the selected table. Each schedule becomes a pipeline for your orchestrator; see
  [orchestration](orchestration.md).
- **Read the docs.** **Docs** opens the pipeline docs for the model as it is now: domains,
  schedules, sources, and every table's columns, lineage and tests, on one page.
- **Set up CI.** **CI** shows whether the repository has a Murakumo workflow for GitHub Actions or
  GitLab, writes one (optionally with an approved production build), shows the difference when the
  file was changed by hand instead of overwriting it, and **Run checks now** runs the same four
  checks on your computer, each shown as it passes or fails.
- **Work as a team.** **Team** shows your branch against the parent branch (`collaboration.base_branch`):
  how far behind and ahead it is, what is coming in, in model terms, and your uncommitted model
  changes. **Get latest from main** runs `murakumo sync`; **Commit** commits the project. See
  [teamwork.md](teamwork.md).
- **Be warned before breaking something.** An edit that would break what reads the model (removing a
  column or table, narrowing a type, tightening nullability, changing a key) is previewed first: a
  confirmation lists what it reaches (columns built from it, tests, schedules) and the migration it
  needs, before anything is saved.
- **Fix problems.** The top bar shows the problems `murakumo validate` and `murakumo generate`
  report, such as a column with no mapping (MK503). Click it for the list; click an item to go to
  its table and column, which is pointed out. Tables with problems carry a badge, the columns
  they are about are marked red or amber (hover for the message), and the inspector shows them.

## Running the project

Everything `murakumo` does in a terminal, the canvas does too. Long commands run as **jobs**: the
canvas starts the same `murakumo` command in the project's folder, in the background, and shows
its output as it comes in **Activity** (the pulse icon), with the command itself to copy. One job
runs at a time; **Stop** stops it. The last twenty are kept with their output.

- **Settings** (the gear): **Project** sets the warehouse, orchestrator and modelling pattern (as
  `murakumo config`), the Python pipeline, plain-SQL output, the run log, the parent branch and
  personal schemas, each saved to `murakumo.yaml` at once with its comments kept, and undoable.
  **This machine** is `murakumo setup`: what the project needs here (drivers, variables, key
  files, connections) and how to fix each, with **Test the connections**. Passwords and keys are
  never typed into the canvas: they stay in environment variables and key files.
- **Build…** (in Activity) loads the raw tables and builds every model and test into the
  warehouse: from files you add (**Add a file…**; they go to `data/`, which git ignores), from what
  is already loaded, or from made-up rows; into dev or prod, one schedule or everything, a day for
  cumulative tables, and a schema prefix. **Verify** builds on a throwaway DuckDB and runs every
  test twice. **Run as Python…** runs the Polars or PySpark pipeline. **Generate** and **Export as
  plain SQL** write `generated/` and `export/`.
- **Runs** (the clock) reads the audit tables every run writes: by day, each run with its engine,
  schedule and counts, each load's rows taken in and rejected (hover for why), and the last run's
  failed tests (click one to go to its table). **Schedules** says when each schedule last ran here
  and how it ended, and **Run now** builds it.
- **Backfill**: a cumulative table's inspector takes a first and last day and fills them in, one
  day after another.
- **Data dictionaries**: **+ Add source** with a CSV or Excel dictionary has **Choose a file from
  this computer…**, which copies it into `data/` and fills in the path; then import as usual.
- **Test my change** (in **Team**, or Activity): what a pull request's preview does, one step at a
  time: copy production into a prefix (`pr_<branch>_`), apply this branch's new migrations there,
  build and test there, and drop the copy. Each step shows its command and result, and what is
  under the prefix after it. On Snowflake the copy is a zero-copy clone; on DuckDB, the tables are
  copied.
- **Compare with** (in **Team**): `murakumo diff` against any branch, tag or commit: each change, how
  risky, what it reaches, the migration it needs, and **Copy the report for a pull request**.

Renames follow references, as with `murakumo rename`: renaming `lineitem` updates the
relationships, mappings and metrics that use it, and renames `lineitem.yaml` too; renaming a
column updates keys, grain, watermark, tests, relationships, and mappings, including
expressions such as `l_extendedprice * (1 - l_discount)`. Ids never change.

## Working alongside an editor

Every request reads the project from disk, and the canvas checks for changes every two seconds,
so edits made in your editor, with the CLI, or by `git pull` appear on the canvas straight away;
so does running `murakumo generate` or `murakumo migrate` in a terminal.
If a file changed after the canvas last loaded it, the canvas refuses to save over it, shows the
latest version and asks you to make the change again. If a file stops loading (a YAML syntax
error, say), the canvas turns read-only until it is fixed, so nothing is ever saved over a file
it could not read.

## The API

`murakumo canvas` serves the canvas and a JSON API under `/api`; interactive documentation is
at `/api/docs`. Every change returns the whole new state, validated.

| Method | Path | Does |
| --- | --- | --- |
| GET | `/api/state` | The model, layout, canvas settings, sources, domains, schedules, diagnostics (with the column each is about), what undo and redo would do, revision, and the choices for each field |
| GET | `/api/revision` | Just the revision, a fingerprint of the model files: cheap to poll |
| POST | `/api/entities` | Add an entity: `{name, layer, kind, source, domain, description, position}` |
| PATCH | `/api/entities/{id}` | Change or rename it: any of `name`, `description`, `layer`, `kind`, `source`, `domain`, `owner`, `history`, `load`, `watermark`, `tags` |
| DELETE | `/api/entities/{id}` | Remove it, its file and its relationships |
| POST | `/api/entities/{id}/columns` | Add a column: `{name, type, primary_key, nullable, description, pii, classification}` |
| PATCH | `/api/entities/{id}/columns/{column_id}` | Change or rename it |
| DELETE | `/api/entities/{id}/columns/{column_id}` | Remove it |
| POST | `/api/relationships` | Add one: `{from, to, keys: [{from, to}], cardinality, name, optional, role}` |
| PATCH | `/api/relationships/{id}` | Change it: `name`, `cardinality`, `optional`, `role`, `description`, `keys` |
| DELETE | `/api/relationships/{id}` | Remove it |
| PUT | `/api/entities/{id}/mappings/{column_id}` | Set a column's mapping: `{sources: ["entity.column"], expression}`; both empty removes it |
| PUT | `/api/entities/{id}/columns/order` | Put the columns in this order: `{columns: [column_id, ...]}`, every column once |
| POST | `/api/entities/{id}/calculated` | Add a column worked out by an expression: `{name, expression, type}` (type optional) |
| POST | `/api/expressions/check` | Check an expression: `{expression, sources, entity}` gives what it reads, problems, its type and its warehouse SQL |
| POST | `/api/expressions/preview` | Run it on made-up rows in DuckDB: the input columns, the result and a note |
| GET | `/api/lineage?entity=&column=` | Where a column (or every column of an entity) comes from and what uses it: nodes with their distance, links with their expressions, and the joins between them |
| POST | `/api/domains` | Add a domain to `project.domains`: `{name, owner, description}` |
| PATCH | `/api/domains/{name}` | Change its `owner` or `description`, or rename it with `name` (its entities follow) |
| DELETE | `/api/domains/{name}` | Remove a domain no entity uses |
| POST | `/api/sources` | Declare a source in `murakumo.yaml`: `{name, kind, location}`; kind is `database` (location: an environment variable's name), `snowflake` (a database name), `csv`, `excel`, `file` or `openapi` (a path or URL) |
| GET | `/api/sources/{name}/browse` | A database source's schemas, tables and columns, each table marked if it is in the model |
| POST | `/api/sources/{name}/import/preview` | What importing would change: `{tables: ["schema.table"], prune}`; `tables: null` imports the whole source |
| POST | `/api/sources/{name}/import` | Import it and save; returns the new state and the reports |
| POST | `/api/schedules` | Add a schedule: `{name, cron, select, description}` |
| PATCH | `/api/schedules/{name}` | Change its `name`, `cron`, `select` or `description` |
| DELETE | `/api/schedules/{name}` | Remove it |
| PUT | `/api/orchestration/timezone` | Set the time zone schedules run in: `{timezone}` |
| GET | `/api/pipeline-docs` | The pipeline docs page, as HTML |
| GET | `/api/ci?provider=&deploy=` | The CI file: missing, current or changed, with its content or difference, and the last local run |
| POST | `/api/ci` | Write it: `{provider, deploy, force}`; a file changed by hand needs `force` |
| POST, GET | `/api/ci/run` | Start the checks here in the background; follow them |
| GET | `/api/git?refresh=` | Branch, parent branch, ahead and behind, incoming and uncommitted model changes, your schema prefix |
| POST | `/api/git/sync?resume=` | Bring in the parent branch, as `murakumo sync` does |
| POST | `/api/git/abort` | Give up a sync with conflicts |
| POST | `/api/git/commit` | Commit the project: `{message}` |
| PUT | `/api/layout` | Save positions: `{positions: {entity_id: {x, y}}}` |
| POST | `/api/jobs` | Start a job: `{kind, options}`; kind is `build`, `verify`, `run`, `backfill`, `migrate_apply`, `env_clone`, `env_drop`, `export` or `generate`, and each option is checked before the command is built |
| GET | `/api/jobs`, `/api/jobs/{id}?since=` | The jobs, newest first; one job and its output from line `since` on |
| POST | `/api/jobs/{id}/cancel` | Stop a job, and what it started |
| GET, PUT | `/api/settings` | The project's settings and their choices; change any of `warehouse`, `orchestrator`, `pattern`, `python_engine`, `plain_sql`, `run_log`, `base_branch`, `personal_schemas` |
| GET | `/api/setup?connect=` | What `murakumo setup` checks, each with how to fix it |
| GET, PUT, DELETE | `/api/uploads`, `/api/uploads/{name}` | Files in `data/`; add one as its raw bytes (`application/octet-stream` only), or remove one |
| GET | `/api/runs?environment=&prefix=` | The audit tables: days, runs, loads, the last run's failed tests, each schedule's last run |
| GET | `/api/env?environment=&prefix=` | Where an environment's builds go, and each schema's tables |
| GET | `/api/diff?ref=` | Model changes since a branch, tag or commit, what each reaches, and the report as Markdown |
| PUT | `/api/canvas` | Save what the canvas draws besides the model, in `model/layout.yaml`: any of `collapse_over`, `collapsed: {entity_id: bool}`, `labels: [{name, colour, description}]`, `colours: {entity_id: label}`, `notes: [{id, text, x, y, width, colour, diagram}]`, `diagrams: [{id, name, entities}]`, and `label` (what undo calls it) |
| POST | `/api/undo`, `/api/redo` | Take back the last change, or make it again; refused (409) when the files changed some other way since |
| PATCH | `/api/entities` | The same change to several tables: `{ids, changes}`, one undo |
| POST | `/api/entities/remove` | Remove several tables: `{ids}`, one undo |
| POST | `/api/entities/{id}/columns/copy` | Bring in other tables' columns, mapped: `{columns: ["entity.column"]}` |
| GET | `/api/generated` | What generating would change: each file's status and entities, generate diagnostics, the next migration |
| GET | `/api/generated/file?path=` | One generated file and its diff against `generated/` |
| GET | `/api/entities/{id}/code` | The dbt SQL and DDL generated for one entity |
| POST | `/api/generate` | Write `generated/` |
| POST | `/api/migrations` | Write the next migration: `{name}` (optional) |

The generated endpoints take `?warehouse=` to generate for another warehouse than the manifest's.

Send `X-Murakumo-Preview: 1` with an edit to an entity, column or relationship to get what it would
reach (`{preview: {worst, impacts, files, migration}}`) instead of making it.

Send the revision you last saw in an `X-Murakumo-Revision` header to have a change refused
(409, with the current state) if the files changed since. A change the model rejects, such as a
duplicate name or an unknown type, returns 400 with a message and changes nothing.

The server listens on 127.0.0.1 only, answers only requests addressed to `localhost` or
`127.0.0.1`, and accepts changes only as JSON, which a web page on another site cannot send to
it without its consent.

## Developing the canvas

The canvas lives in [apps/canvas](../apps/canvas). Run the API and Vite side by side; Vite
forwards `/api` to port 8765:

```bash
cd packages/python && uv run murakumo canvas ../../examples/commerce --no-open
cd apps/canvas && npm run dev        # http://localhost:5173
```

`npm run build` writes `apps/canvas/dist`. `murakumo canvas` serves the copy built into the
Python package, or `apps/canvas/dist` when it runs from the repository; building the package
(`uv build`) puts the built canvas inside it.
