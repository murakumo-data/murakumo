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
  hide layers.
- **Edit an entity.** Click it to open the inspector: rename it, change its layer, kind, source,
  domain and description, and add, rename, retype or remove columns and set primary keys and
  NOT NULL. **+ Entity** adds a new one.
- **Add a relationship.** Hover over an entity to show the handles beside each column, then
  drag from the foreign key column to the column it points at, on another entity. The new
  relationship is many-to-one; click the line to change the cardinality, make it optional, or
  give it a role (needed when two relationships join the same tables, like pickup and drop-off
  zones).
- **Fix problems.** The toolbar shows validation errors and warnings, the same as
  `murakumo validate`. Click it for the list; click an item to go to its entity. Entities with
  problems carry a badge, and the inspector shows their messages.

Renames follow references, as with `murakumo rename`: renaming `lineitem` updates the
relationships, mappings and metrics that use it, and renames `lineitem.yaml` too; renaming a
column updates keys, grain, watermark, tests, relationships, and mappings, including
expressions such as `l_extendedprice * (1 - l_discount)`. Ids never change.

## Working alongside an editor

Every request reads the project from disk, and the canvas checks for changes every two seconds,
so edits made in your editor, with the CLI, or by `git pull` appear on the canvas straight away.
If a file changed after the canvas last loaded it, the canvas refuses to save over it, shows the
latest version and asks you to make the change again. If a file stops loading (a YAML syntax
error, say), the canvas turns read-only until it is fixed, so nothing is ever saved over a file
it could not read.

## The API

`murakumo canvas` serves the canvas and a JSON API under `/api`; interactive documentation is
at `/api/docs`. Every change returns the whole new state, validated.

| Method | Path | Does |
| --- | --- | --- |
| GET | `/api/state` | The model, layout, diagnostics, revision, and the choices for each field |
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
| PUT | `/api/layout` | Save positions: `{positions: {entity_id: {x, y}}}` |

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

`npm run build` writes `apps/canvas/dist`, which `murakumo canvas` serves when it runs from
the repository. Bundling it into the Python package comes with the first release.
