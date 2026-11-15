# The assistant, and the model for your own agent

Two ways to put a language model to work on a project. Both are optional, and neither is needed
for anything else Murakumo does.

- **The assistant** (`murakumo ai`) asks a model for suggestions and shows them to you.
- **The MCP server** (`murakumo mcp`) gives an agent you already use (Claude Code, Claude Desktop,
  anything that speaks the Model Context Protocol) tools to read and change the model.

## The assistant

It is off until a project turns it on:

```bash
pip install "murakumo[ai]"
export ANTHROPIC_API_KEY=...      # your own key with the provider
murakumo ai enable                # assistant.enabled in murakumo.yaml
murakumo ai status
```

```yaml
assistant:
  enabled: true
  provider: anthropic
  model: claude-opus-5-5          # any model your key can use
  key_env: ANTHROPIC_API_KEY      # the variable holding the key: the key itself is never written down
  effort: medium                  # optional: low | medium | high | xhigh | max
```

What it is sent is the model: table and column names, types, keys and descriptions. **Never rows
of data.** `murakumo ai status` says so, and `assistant.describe_model` in the code is exactly
what leaves your computer.

| Command | What it suggests |
| --- | --- |
| `murakumo ai describe [-e table]` | Descriptions for the tables and columns that have none |
| `murakumo ai classify [-e table]` | Which columns hold personal data, and a classification for each |
| `murakumo ai map <table>` | Where each unmapped column of a modelled table comes from |
| `murakumo ai draft "a fact of daily sales by store"` | New tables, with columns, keys and mappings |

Each prints its suggestions with the reason for each, and changes nothing. Add `--apply` to make
them (`--json` prints them for a script):

```bash
murakumo ai describe -e orders            # look first
murakumo ai describe -e orders --apply    # then make them
```

A suggestion is applied through the same edits as the CLI and the canvas, so one the model would
not take by hand is **refused**, with the reason, and the rest still apply: a mapping whose
expression does not parse or reads a column that does not exist, a column type that is not a type.
A drafted table is added whole or not at all. Afterwards `validate` runs as after any change.

When the provider's model declines a request, the API passes it to a fallback model where the
provider offers that; when it does not, the command says the request was declined.

**In the canvas:** the **Assistant** panel. Turn it on, choose what to suggest and for which table,
tick the suggestions to keep, and apply them.

## The MCP server

```bash
murakumo mcp -p /path/to/project
```

It speaks the Model Context Protocol over stdin and stdout. Add it to an agent as a command; for
Claude Code:

```bash
claude mcp add murakumo -- murakumo mcp -p /path/to/project
```

| Tool | What it does |
| --- | --- |
| `model_summary` | The tables, relationships, warehouse and orchestrator |
| `get_entity` | One table in full: columns, keys, descriptions, mappings |
| `validate` | The model's problems, each with its code and file |
| `lineage` | Where a table or column comes from, and what is built from it |
| `add_entity`, `add_column`, `add_relationship` | Add to the model |
| `update_entity`, `update_column`, `set_mapping` | Change it |
| `generate` | Write the generated code (or, with `dry_run`, say what would change) |

Every change is validated and saved to the YAML files, as from the CLI, and answers with what the
model now needs. A change the CLI would refuse is refused with the same message. The server calls
no language model itself: the agent brings its own, so no key is needed and nothing is sent
anywhere by Murakumo.
