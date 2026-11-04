# Working as a team

Murakumo projects live in git, so a team works the usual way: everyone on a branch, changes merged
through pull requests, environments deployed from their branches. Murakumo adds these to make that
safe for a data model:

1. **`murakumo sync`** brings in the parent branch and keeps generated code and migrations in step;
2. **typed diffs and impact reports** say what a change is and what it reaches, before anyone merges it;
3. **environments** (dev, stage, prod), each a database of its own, deployed from its branch;
4. **everyone's own schema**, so each person builds without touching anyone else's work;
5. **the git flow**, checked on every pull request;
6. **pull-request previews** build each pull request on a clone of the environment it goes into,
   with its new migrations, so a migration that would fail there fails in the pull request instead.

## Staying in sync

```yaml
collaboration:
  base_branch: dev           # the branch new work starts from (main, for a team without the flow)
  remote: origin
```

```bash
murakumo sync --check        # what is coming in from main, in model terms
murakumo sync                # bring it in
murakumo sync --continue     # after resolving conflicts
murakumo sync --abort        # give up, as if it never started
```

`sync` refuses while the project has uncommitted changes, so nothing of yours is mixed into the
merge. It fetches the parent branch, says what changes in the model (tables and columns added,
renamed, removed...), merges, regenerates `generated/` and commits.

- **Conflicts in model files** (two people changed the same table) are yours to resolve: open the
  file, keep the right lines, `git add` it, then `murakumo sync --continue`.
- **Conflicts in `generated/`** are not real conflicts: those files are generated again from the
  merged model.
- **Migrations**: when both branches wrote migrations since they split, yours were planned against a
  state the parent has moved on from, and may share its numbers (both wrote `0005_...`). Yours are
  removed and one new migration is planned from the merged model against the parent's state,
  numbered after its last one. `sync` says what it did: *0005_zone_code.sql replaced by
  0007_zone_code.sql, planned after main's migrations*.

The canvas's **Team** panel shows the same: your branch, how far it is behind and ahead of the
parent, what is coming in, your uncommitted model changes, **Get latest from main**, and **Commit**.
Pushing stays with you and git.

## What a change is, and what it reaches

```bash
murakumo diff                       # compared with the parent branch
murakumo diff v0.3.0                # or any branch, tag or commit
murakumo diff --format markdown     # for a pull request
murakumo diff --fail-on breaking    # exit 1 on a breaking change, for CI
```

Every change is matched by id (a rename is a rename) and classed, as in the project brief:

| Class | Means | For example |
| --- | --- | --- |
| additive | nothing that reads the model breaks | a new table or nullable column, a wider type, nullability loosened |
| compatible | nothing breaks, but data must be rebuilt or backfilled | a rename, a changed mapping, SCD1 to SCD2 |
| breaking | something that reads the model may fail or change meaning | a dropped column, a narrower type, nullability tightened, a key or grain change |

Under each change, the impact report lists what it reaches through the column lineage: the
columns built from it (in the model as it was, for something removed), their tables, the tests on
them, metrics and schedules, plus the generated files that change and the migration it needs.

The CI workflow writes this report on every pull request's summary page. On the canvas, an edit
that would break something (removing a column, narrowing a type, tightening nullability...) is
previewed first and asks before saving, with the same report.

## Environments

Each shared environment has a database of its own and the git branch that deploys it. `murakumo
init` sets up three:

```yaml
targets:
  warehouse:
    type: snowflake
    database: TAXI              # the default, for anything without a database of its own
  environments:                 # merging into an environment's branch deploys to it
    - name: dev
      database: TAXI_DEV
      branch: dev
    - name: stage
      database: TAXI_STAGE
      branch: stage
    - name: prod
      database: TAXI_PROD
      branch: main

collaboration:
  base_branch: dev              # new work starts from dev
  personal_database: MURAKUMO   # everyone's own schema lives here
```

On DuckDB each environment is a file (`taxi_dev.duckdb`, `taxi_prod.duckdb`) and your own builds use
`taxi.duckdb`. Projects from before 0.7, with `environments: [dev, prod]`, keep working: every
environment then uses the warehouse's database.

`murakumo env provision` prints what an administrator runs once on Snowflake: the databases, a
deployer role per environment (CI uses it), a developer role for people (they build in
`MURAKUMO` and read the environments, to copy them), an x-small warehouse that suspends itself, and
the grants. Settings → Project → Environments in the canvas shows the same, with **Setup SQL…**.

## Your own schema

`murakumo build` with no `--env` builds your own copy of the project, in a schema named after you
(git's user name, or `MURAKUMO_DEVELOPER`): `MURAKUMO.ANA`, holding every layer, as dbt does it.
Nobody overwrites anybody else's work, and the environments are only ever changed by deploys.
Because one schema holds every layer, two tables of the same name in different layers would
collide there; `murakumo validate` warns about that (MK108). A DuckDB file is already personal, so
there your own builds use the usual schemas.

```bash
murakumo env                       # where your own builds go, and what is there
murakumo build                     # into your schema
murakumo env clone                 # copy prod's tables into your schema (zero-copy on Snowflake)
murakumo env clone --env dev       # or dev's
murakumo env drop                  # drop your schema (an environment's own are never dropped)
murakumo build --env dev           # build an environment, as its deploy does
```

```yaml
collaboration:
  personal_schemas: true           # false: your own builds go to the default database's schemas
```

## The git flow

New work starts on a branch of its own, from the latest `dev`, and goes back into `dev` through a
pull request. `dev` is promoted to `stage`, and `stage` to `main`, by pull requests too. Merging
into an environment's branch deploys to it: the CI workflow applies that environment's new
migrations, then builds and tests it.

```bash
murakumo branch start --push       # once: create dev and stage from main, and push them
murakumo branch new feature add-zone-codes   # or fix/..., chore/..., hotfix/... (from main)
murakumo branch promote dev        # what moving dev into stage would bring, and where to open it
murakumo branch check --head feature/x --base dev   # what CI checks on every pull request
```

The CI workflow refuses a pull request that skips a step: new work goes into `dev`, `dev` into
`stage`, `stage` into `main`; a `hotfix/...` can go straight to `main`. The canvas's **Team** panel
has **Start new work** (pick feature, fix, chore or hotfix, and a name) and **Promote**, which
lists what would move and opens the pull request on GitHub or GitLab.

## Testing a change before it is merged

```bash
murakumo env clone --env prod --prefix pr_42_          # zero-copy clones of prod on Snowflake
murakumo migrate --apply --env prod --prefix pr_42_    # the branch's new migrations, on the clone
murakumo build --env prod --prefix pr_42_              # build and test there
murakumo env drop --env prod --prefix pr_42_ --yes
```

`migrate --apply` runs the migration files an environment has not run yet, in order, and records
each in its `audit.murakumo_migrations`; a clone carries the environment's record, so only the
branch's new migrations run. `--mark-applied` records them without running them, for tables brought
up to date by hand.

`murakumo ci --preview` puts this in the CI workflow: each pull request is built on a clone of the
environment it goes into (`murakumo branch env` says which: a pull request into `dev` is tried on
dev), with its new migrations, under `pr_<number>_*`, dropped when the pull request closes. On
GitLab, the preview is a review environment that stops itself after a week. Previews need
Snowflake, whose clones are instant and take no space until rows change. The canvas's
**Test my change** does the same, step by step, from the Team panel.
