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

## What you can do

- **See the model.** Raw tables are grouped by source, then staging, intermediate, marts and
  semantic, left to right. Inside a group, a table sits to the left of the tables it points at,
  so `lineitem → orders → customer → nation → region` reads left to right. Drag boxes to
  arrange them; positions are saved to `model/layout.yaml`, keyed by entity id, so a rename
  keeps its place. Commit the file to share the layout. The layer chips in the toolbar show and
  hide layers. **Shift**+drag on the background selects several tables (⌘/Ctrl+click adds one),
  and dragging any of them moves them all.
- **Read the relationships.** Lines use crow's-foot notation: at each end, ‖ exactly one,
  ○| zero or one, a crow's foot for many (with ○ when it may be none); a dashed line is optional.
  Hover over a line to see its role and cardinality (`pickup · N:1`) and the joined columns
  highlighted in both tables; click it to keep them highlighted and edit it. **Notation & tips**,
  at the bottom left, explains the symbols. When two tables are dragged one above the other, the
  line goes round them rather than behind them.
- **Edit an entity.** Click it to open the inspector: rename it, change its layer, kind, source,
  domain and description, and add, rename, retype or remove columns and set primary keys and
  NOT NULL. **+ From another table** adds columns copied from other tables: tick them in a
  searchable list; they keep their names and types and, outside the raw layer, are mapped to where
  they came from. Tables with more than six columns get a **Find a column** box, which filters the
  columns and mappings. Drag the inspector's left edge to make it wider. **+ Entity** adds a new
  entity.
- **Reorder columns.** Drag a column by its **⋮⋮** handle in the inspector, or focus the handle
  and press Alt+↑ or Alt+↓. Generated tables and dbt models list columns in the new order. Tables
  that already exist keep theirs, because no warehouse reorders columns in place; the Changes
  panel says so, and nothing needs a migration.
- **Add a relationship.** In the inspector's **Relationships** section, click **+ Relationship**
  and pick the column, the table it points to, its column and the cardinality. Or hover over an
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
  that makes it. The bar at the top lists the columns it comes from and the columns that use it;
  click one to follow it. `murakumo lineage` shows the same in a terminal.
- **Group by domain.** **Domains** lists the domains in `murakumo.yaml`: add one, rename one (its
  tables follow), set its owner, or remove one no table uses; click a domain's table count to show
  only its tables. **Show domains** colours each table by its domain and shades an area, with the
  domain's name and owner, behind each domain's tables. An entity's domain is picked from a list
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
- **Write the code.** The toolbar says how many files in `generated/` are out of date with the
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
- **Fix problems.** The toolbar shows the problems `murakumo validate` and `murakumo generate`
  report, such as a column with no mapping (MK503). Click it for the list; click an item to go to
  its entity. Entities with problems carry a badge, and the inspector shows their messages.

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
| GET | `/api/state` | The model, layout, sources, domains, schedules, diagnostics, revision, and the choices for each field |
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
| PUT | `/api/layout` | Save positions: `{positions: {entity_id: {x, y}}}` |
| GET | `/api/generated` | What generating would change: each file's status and entities, generate diagnostics, the next migration |
| GET | `/api/generated/file?path=` | One generated file and its diff against `generated/` |
| GET | `/api/entities/{id}/code` | The dbt SQL and DDL generated for one entity |
| POST | `/api/generate` | Write `generated/` |
| POST | `/api/migrations` | Write the next migration: `{name}` (optional) |

The generated endpoints take `?warehouse=` to generate for another warehouse than the manifest's.

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
