# Working as a team

Murakumo projects live in git, so a team works the usual way: everyone on a branch, changes merged
through pull requests. Murakumo adds four things to make that safe for a data model:

1. **`murakumo sync`** brings in the parent branch and keeps generated code and migrations in step;
2. **typed diffs and impact reports** say what a change is and what it reaches, before anyone merges it;
3. **personal environments** let each person build into schemas of their own;
4. **pull-request previews** build each pull request on a clone of production, with its new
   migrations, so a migration that would fail in production fails in the pull request instead.

## Staying in sync

```yaml
collaboration:
  base_branch: main          # or dev: the branch people branch from
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

## Your own schemas

On a shared warehouse (Snowflake), everyone's `dev` builds go to schemas of their own, named after
git's user name (or `MURAKUMO_DEVELOPER`): `dev_ana_raw_shop`, `dev_ana_staging`, `dev_ana_marts`.
Nobody overwrites anybody else's work, and the shared schemas are production's. A DuckDB file is
already personal, so it gets no prefix.

```bash
murakumo env                        # where your builds go, and what is there
murakumo build                      # into your schemas
murakumo env clone --prefix dev_ana_     # copy the shared schemas into yours, to test on real data
murakumo env drop                   # drop your schemas (the shared ones are never dropped)
```

```yaml
collaboration:
  personal_schemas: true           # false: everyone builds into the shared schemas
  personal_environment: dev
```

## Testing a change before it is merged

```bash
murakumo env clone --prefix pr_42_          # zero-copy clones of production on Snowflake
murakumo migrate --apply --prefix pr_42_    # the branch's new migrations, on the clone
murakumo build --env prod --prefix pr_42_   # build and test there
murakumo env drop --prefix pr_42_ --yes
```

`migrate --apply` runs the migration files a warehouse has not run yet, in order, and records each
in `audit.murakumo_migrations`; a clone carries production's record, so only the branch's new
migrations run. `--mark-applied` records them without running them, for tables brought up to date
by hand.

`murakumo ci --preview` puts this in the CI workflow: each pull request is built on a clone of
production with its new migrations (`pr_<number>_*`), and dropped when the pull request closes. On
GitLab, the preview is a review environment that stops itself after a week. Previews need
Snowflake, whose clones are instant and take no space until rows change.
