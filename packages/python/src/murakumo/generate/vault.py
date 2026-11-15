"""The Data Vault pack: hubs, links and satellites, insert-only, with hash keys.

- A **hub** holds each business key once: ``<hub>_hk`` (a hash of the business key), the key's
  columns, ``load_date`` (when it was first loaded) and ``record_source``.
- A **link** holds each combination of business keys once: ``<link>_hk``, a ``<hub>_hk`` for each
  hub it connects (through its relationships to them), the keys' columns, ``load_date`` and
  ``record_source``.
- A **satellite** holds the descriptive columns of a hub or a link (its relationship to it), with
  a new row each time they change: ``<parent>_hk``, ``hashdiff`` (a hash of the descriptive
  columns), ``load_date`` and ``record_source``. ``<satellite>_current`` is a view of the latest
  row per key, which is what other tables read.

A business key is an entity's natural or primary key columns, in the order they are listed. Each
is trimmed and upper-cased before hashing, so ``abc `` and ``ABC`` are the same key, and the hash
of a hub's key is the same wherever it is computed (in the hub, a link or a satellite).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp

from murakumo.diagnostics import error
from murakumo.ir.model import Entity, EntityKind, KeyRole

if TYPE_CHECKING:
    from murakumo.generate.plan import Column, DbtModel, Planner

KINDS = (EntityKind.HUB, EntityKind.LINK, EntityKind.SATELLITE)
CURRENT = "{name}_current"
SEPARATOR = "||"


def business_key(entity: Entity) -> list[str]:
    """The entity's business key columns: its natural or primary keys, else its grain."""
    keys = [a.name for a in entity.attributes if a.key in (KeyRole.NATURAL, KeyRole.PRIMARY)]
    return keys or list(entity.grain)


def hash_key(planner: Planner, expressions: list[str]) -> str:
    """md5 of the trimmed, upper-cased values joined by ||, in the target's SQL."""
    dialect = planner.target.dialect
    parts = []
    for sql in expressions:
        value = exp.Upper(this=exp.Trim(this=exp.cast(sqlglot.parse_one(sql, read=dialect), "VARCHAR")))
        parts.append(exp.Coalesce(this=value, expressions=[exp.Literal.string("")]))
    joined = parts[0]
    for part in parts[1:]:  # a || '||' || b: every part is already non-null
        joined = exp.DPipe(this=exp.DPipe(this=joined, expression=exp.Literal.string(SEPARATOR)), expression=part)
    hashed = exp.MD5(this=joined).sql(dialect=dialect)
    if dialect in ("fabric", "synapse", "tsql"):  # HASHBYTES gives bytes: as hex text, like md5 elsewhere
        return f"lower(convert(varchar(32), {hashed}, 2))"
    return hashed


def parent_of(planner: Planner, entity: Entity, kinds: tuple[EntityKind, ...]):
    """The relationships from a link or satellite to the hubs (or links) it belongs to."""
    out = []
    for rel in planner.model.relationships:
        if rel.from_.lower() != entity.name.lower():
            continue
        parent = planner.model.entity(rel.to)
        if parent is not None and parent.kind in kinds:
            out.append((rel, parent))
    return sorted(out, key=lambda p: p[1].name.lower())


def parent_hash(planner: Planner, entity: Entity, rel, parent: Entity, by_name: dict[str, Column]) -> str | None:
    """The parent's hash key, computed from this entity's columns that the relationship maps to the
    parent's business key (in the parent's order), so it equals the parent's own."""
    mapped = {k.to.lower(): k.from_ for k in rel.keys}
    columns = []
    for key in business_key(parent):
        own = mapped.get(key.lower())
        if own is None or own.lower() not in by_name:
            return None
        columns.append(by_name[own.lower()].sql)
    return hash_key(planner, columns)


def plan(planner: Planner, entity: Entity, model: DbtModel) -> list[DbtModel]:
    from murakumo.generate.plan import Column, DbtModel, Test, _Skip, snake

    loaded = next((c for c in model.columns if c.name == "_loaded_at"), None)
    if loaded is None:
        planner.plan.diagnostics.append(
            error(
                "MK509",
                f"{entity.name} is a {entity.kind.value}, so it needs to read a raw table (for its load date)",
                planner.file(entity),
            )
        )
        raise _Skip
    attributes = [c for c in model.columns if c.name not in ("_updated_at", "_loaded_at")]
    by_name = {c.name.lower(): c for c in attributes}
    string = planner.target.physical_name("string")
    load_date = Column("load_date", loaded.sql, loaded.data_type, "When it was first loaded.")
    source = Column(
        "record_source",
        f"'{snake(planner.referenced_entities(entity)[0].source or 'raw')}'",
        string,
        "The source system it was loaded from.",
    )
    keys = business_key(entity)
    tests: list[Test] = []
    columns: list[Column] = []

    def needs(what: str) -> None:
        planner.plan.diagnostics.append(error("MK509", f"{entity.name}: {what}", planner.file(entity)))
        raise _Skip

    if entity.kind is EntityKind.HUB:
        if not keys:
            needs("a hub needs a business key: mark its columns key: natural")
        hk = f"{entity.name}_hk"
        columns.append(
            Column(
                hk,
                hash_key(planner, [by_name[k.lower()].sql for k in keys]),
                string,
                f"Hash of the business key ({', '.join(keys)}).",
            )
        )
        columns += [by_name[k.lower()] for k in keys]
        partition, compare = hk, None
        tests.append(Test("murakumo_unique_combination", None, {"columns": [hk]}))
    elif entity.kind is EntityKind.LINK:
        hubs = parent_of(planner, entity, (EntityKind.HUB,))
        if len(hubs) < 2:
            needs("a link needs relationships to two or more hubs")
        hub_keys = []
        for rel, hub in hubs:
            sql = parent_hash(planner, entity, rel, hub, by_name)
            if sql is None:
                needs(f"its relationship to {hub.name} must map {hub.name}'s business key")
            hub_keys.append(Column(f"{hub.name}_hk", sql, string, f"The {hub.name} it links."))
        hk = f"{entity.name}_hk"
        link_columns = keys or [k.from_ for rel, _ in hubs for k in rel.keys]
        columns.append(
            Column(
                hk,
                hash_key(planner, [by_name[k.lower()].sql for k in link_columns]),
                string,
                "Hash of the business keys it links.",
            )
        )
        columns += hub_keys + [by_name[k.lower()] for k in link_columns if k.lower() in by_name]
        partition, compare = hk, None
        tests.append(Test("murakumo_unique_combination", None, {"columns": [hk]}))
        for column, (_, hub) in zip(hub_keys, hubs, strict=True):
            column.tests.append(Test("relationships", column.name, {"to": f"ref('{hub.name}')", "field": column.name}))
    else:
        parents = parent_of(planner, entity, (EntityKind.HUB, EntityKind.LINK))
        if len(parents) != 1:
            needs("a satellite needs one relationship, to its hub or link")
        rel, parent = parents[0]
        sql = parent_hash(planner, entity, rel, parent, by_name)
        if sql is None:
            needs(f"its relationship to {parent.name} must map {parent.name}'s business key")
        hk = f"{parent.name}_hk"
        own_keys = {k.from_.lower() for k in rel.keys}
        descriptive = [c for c in attributes if c.name.lower() not in own_keys]
        columns.append(
            Column(
                hk,
                sql,
                string,
                f"The {parent.name} it describes.",
                tests=[Test("relationships", hk, {"to": f"ref('{parent.name}')", "field": hk})],
            )
        )
        columns += [c for c in attributes if c.name.lower() in own_keys] + descriptive
        columns.append(
            Column(
                "hashdiff",
                hash_key(planner, [c.sql for c in descriptive] or ["''"]),
                string,
                "Hash of the descriptive columns: a new row is added when it changes.",
            )
        )
        partition, compare = hk, "hashdiff"
        tests.append(Test("murakumo_unique_combination", None, {"columns": [hk, "load_date"]}))
    for column in columns:
        if column.name == hk:
            column.tests = [Test("not_null", hk), *column.tests]
    columns += [load_date, source]

    model.columns = columns
    model.tests = tests
    model.materialized = "incremental"
    model.unique_key = []  # insert-only
    model.contract = False
    model.template = "vault.sql.j2"
    # each run adds what was loaded since the last one, by its load date
    floor = planner.cast("'1900-01-01'", loaded.data_type)
    model.incremental_filter = f"{loaded.sql} > (select coalesce(max(load_date), {floor}) from {{{{ this }}}})"
    model.extra = {"kind": entity.kind.value, "key": partition, "compare": compare}
    built = [model]
    if entity.kind is EntityKind.SATELLITE:
        current = DbtModel(
            name=CURRENT.format(name=entity.name),
            layer=model.layer,
            folder=model.folder,
            entity=entity,
            relation=f"{{{{ ref('{entity.name}') }}}}",
            alias="versions",
            columns=[
                Column(c.name, f"versions.{planner.quote(c.name)}", c.data_type, c.description, meta=c.meta)
                for c in columns
            ],
            materialized="view",
            where=(
                f"versions.load_date = (select max(latest.load_date) from {{{{ ref('{entity.name}') }}}} as latest "
                f"where latest.{planner.quote(partition)} = versions.{planner.quote(partition)})"
            ),
            tests=[Test("murakumo_unique_combination", None, {"columns": [partition]})],
            description=f"The latest row of {entity.name} for each {parent.name}: what other tables read.",
            extra={"history": True},
        )
        built.append(current)
    return built
