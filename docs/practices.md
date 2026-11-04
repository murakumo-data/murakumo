# Murakumo against established practice

A review of Murakumo against the practices data teams rely on, from four sources: Kimball's
dimensional modelling, dbt's own guidance, Snowflake's, and the principles in *Designing
Data-Intensive Applications*. Each practice says what Murakumo does about it today (0.9) and,
where it falls short, what would close the gap. The review works from the published practices;
it does not quote the books.

**Done** means Murakumo does it for you. **Partly** means it can be done, by hand or only in some
cases. **Not yet** is a gap, with the milestone it fits.

## Dimensional modelling (Kimball)

| Practice | Murakumo | Status |
| --- | --- | --- |
| Declare each fact table's grain before its columns | `grain:` on an entity; tested as a unique combination; the canvas shows it | Done |
| Surrogate keys for dimensions | Hash keys (`modelling.surrogate_keys: hash`), computed from the natural key | Done |
| Slowly changing dimensions (type 1, 2) | `history: scd1 | scd2` per entity; SCD2 by dbt snapshot, with `valid_from`, `valid_to`, `is_current` | Done |
| Types 3, 4 and 6 | Accepted (`history: scd3 | scd4 | scd6`); 3 and 4 are built as type 1 (overwritten), 6 as type 2 without its current-value columns | Partly |
| Role-playing dimensions (pickup and drop-off zone) | Relationships carry a `role`; one dimension joined twice | Done |
| Conformed dimensions shared across facts and domains | `conformed: true` on a dimension; a fact using another domain's dimension that is not conformed is reported (MK316), and so is a copy of a conformed one (MK317) (0.9) | Done |
| Bus matrix (which facts use which dimensions) | Drawn from the relationships, in the replication guide and the canvas's **Bus matrix** panel (0.9) | Done |
| Fact table types: transaction, periodic snapshot, accumulating snapshot | Transaction facts; cumulative tables (a periodic, per-key history); accumulating snapshots, a row per key that keeps each milestone's date and the time between them (0.9) | Done |
| Late-arriving dimensions: an "unknown" member, so facts never lose rows | An unknown (-1) row in each dimension, and facts point a missing key at it (`modelling.unknown_members`, on for new projects; per dimension too) (0.9). A key that is present but not yet in the dimension is still reported by the relationship test | Done |
| Late-arriving facts | Picked up by load time since 0.7 (`incremental_by: load_time`), with a look-back | Done |
| Degenerate dimensions (an order number on the fact) | A fact column with no dimension behind it | Done |
| Junk dimensions (flags gathered into one small dimension) | Not generated | Not yet |
| An audit dimension: which load and run each row came from | `_ingested_at`, `_batch_id` on raw rows; `_loaded_at`, `_updated_at` on modelled ones; the run log | Done (as columns rather than a dimension) |
| Data Vault (hubs, links, satellites) | Entity kinds exist (`hub`, `link`, `satellite`); generation treats them as tables | Partly |

## dbt

| Practice | Murakumo | Status |
| --- | --- | --- |
| Staging, intermediate and marts layers, each with its job | The model's layers; staging generated one-to-one from each raw table | Done |
| Sources declared, with freshness | `sources.yml` per source, `loaded_at_field: _ingested_at`, freshness from the model | Done |
| Every model tested: unique and not-null keys, relationships, accepted values | Generated from keys, relationships and column rules | Done |
| Model contracts on what others read | Enforced on marts (`contracts.enforce_on`) | Done |
| Groups and access (private, protected, public) | A dbt group per domain, and `access:` per table (0.9) | Done |
| Incremental models that are idempotent | `delete+insert` by unique key; load-time increments re-run safely | Done |
| Snapshots for history | Generated for SCD2 entities | Done |
| Exposures (dashboards and apps that read the marts) | `model/exposures.yaml`, written into dbt, lineage and the guide; edited in the canvas (0.9) | Done |
| Semantic layer (metrics defined once) | Metrics in `model/metrics.yaml`; dbt semantic models not generated | Partly |
| Slim CI: build only what changed (`state:modified+ --defer`) | CI verifies everything on DuckDB; previews build the whole project | Not yet: worthwhile once projects are large |
| Documentation that stays current | `murakumo docs` and the pipeline docs, from the model | Done |
| One project, one source of truth, no hand edits to generated code | `generated/` written from the model; `custom/` for hand-written code; `generate --check` in CI | Done |

## Snowflake

| Practice | Murakumo | Status |
| --- | --- | --- |
| A database per environment | Since 0.7: `TAXI_DEV`, `TAXI_STAGE`, `TAXI_PROD`, and `MURAKUMO` for people's own schemas | Done |
| Role-based access: a role per job, granted to people and services | `murakumo env provision`: a deployer role per environment, a developer role | Done |
| Functional and access roles kept apart | One role per job, not split into access roles | Partly |
| Key-pair authentication for services | The profiles and CI use key pairs; keys stay in files and secrets | Done |
| Warehouses that suspend themselves | Provisioning creates an x-small warehouse that suspends after 60 seconds | Done |
| Resource monitors to cap spend | `targets.warehouse.monthly_credits`: provisioning creates a monitor that notifies at 75% and 90% and suspends at 100% (0.8) | Done |
| Zero-copy clones for testing | Pull-request previews and copies into your own schema clone | Done |
| Time travel and fail-safe settings per layer | `targets.warehouse.time_travel_days` per layer, set on raw tables and by dbt after each build; MK703 when a transient layer asks for more than a day (0.8) | Done |
| Transient tables for what can be rebuilt (staging, intermediate) | `transient_layers` (staging and intermediate by default); marts and snapshots kept permanent, where dbt alone would make every table transient (0.8) | Done |
| Clustering keys on large tables | `cluster_by:` on an entity | Done |
| Masking and row-access policies for personal data | Masking policies from PII and classification (`governance.masking`), set after each build, with the roles that see the real values (0.9). Row-access policies are not generated | Partly: masking done; row access not yet |
| Loading with COPY and Snowpipe | COPY for files from the build; with `stack.load: snowpipe`, a pipe per raw table from an S3 stage through a storage integration (0.8) | Done |

## Designing data-intensive applications

| Principle | Murakumo | Status |
| --- | --- | --- |
| Keep raw data immutable; derive everything else from it | Raw tables append (or merge, as declared); every other table is rebuilt from them | Done |
| Make every step idempotent, so it can safely run again | Loads remember files by content; incremental models replace by key; backfills re-run days | Done |
| Event time is not processing time: late data happens | Increments by load time, with a look-back; cumulative tables by event day | Done |
| Reprocessing: rebuild derived data from the raw history | `build` from scratch, `backfill` by day; full refresh by dropping a table | Done |
| Evolve schemas compatibly; know what a change breaks | Typed diffs and impact reports; migrations planned and applied per environment | Done |
| Exactly-once results without distributed transactions | Through idempotence (above), not transactions | Done |
| Lineage: know where every value comes from | Column lineage in the model, the canvas and the docs | Done |
| Validate at the edges: reject or quarantine bad input | Rejected rows counted with the reason; raw columns can be kept as text and converted in staging | Done |
| Backpressure and streaming | Batch only; streaming is declared in the model but not generated | Not yet |
| Observability: know what ran, what it did, and when it failed | The run log: runs, models, tests, rows in and rejected, by schedule | Done |

## What this says about the roadmap

The gaps fell in three groups:

- **Platform settings**, closed in 0.8 with the stack choice:
  - resource monitors
  - retention and time travel per layer
  - transient tables for rebuildable layers
  - Snowpipe
- **Modelling patterns**, closed in 0.9 with the replication guide:
  - unknown members for late-arriving dimensions
  - accumulating snapshots
  - conformed dimensions and the bus matrix
  - access levels
  - exposures
  - masking policies (row-access policies are still to come)
- **Scale**, for when projects are large: Slim CI that builds only what changed.
