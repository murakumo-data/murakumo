# Murakumo

**Model-first data engineering.** Design the logical model (entities, relationships, mappings) on a
canvas, and Murakumo generates and maintains what comes after it: dbt projects, DDL, tests and
migrations, verified on DuckDB or Snowflake.

Murakumo is a Python program, [published on PyPI](https://pypi.org/project/murakumo/). This npm
package gives you the same `murakumo` command, and runs it through [uv](https://docs.astral.sh/uv/),
which downloads Python and Murakumo the first time. You only need uv:

```bash
brew install uv          # or: curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then:

```bash
npx murakumo init my_platform      # or: npm install -g murakumo, then murakumo init my_platform
cd my_platform
npx murakumo setup                 # checks what the project needs and says how to set it up
npx murakumo canvas                # the visual editor
```

The first run takes a minute while uv downloads everything; later runs start straight away.

For Snowflake or Postgres, `murakumo setup` tells you what to add, for example:

```bash
export MURAKUMO_EXTRAS="snowflake" MURAKUMO_WITH="dbt-snowflake"
```

Documentation: [github.com/murakumo-data/murakumo](https://github.com/murakumo-data/murakumo).
