"""Which other tables a column relates to: "where else does this customer id appear?"

Searching every table for an id is the expensive way. This works in tiers, and each tier runs only
on what the one before could not rule out:

0. **Names and types** (``candidates``): columns of the raw and staging tables whose names mean the
   same once normalised (``customer_id``, ``CustomerID``, ``cust_id``, ``c_custkey``, a customer
   table's ``id``) and whose types can be joined, and the relationships the model already has.
   Reads only the model: no query.
1. **Column summaries** (``profiles``): for each candidate, from a sample of its table's rows, its
   smallest and largest value, distinct values and empty share. One query per table, kept in
   ``.murakumo/profiles.json`` and asked again only when the table's row count changes.
2. **Overlap** (``check``): for each pair whose ranges can meet, the share of a sample of distinct
   values on each side found on the other, which with uniqueness gives the direction and
   cardinality. Bounded by the sample, not the tables.
3. **One value** (``find``): which columns hold a given value, asking only the columns where it
   can be (a joinable type, inside the column's range), and stopping at the first row in each.

Only tiers 1 to 3 touch the warehouse, and only when asked.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from murakumo.explore import SAMPLE, ExploreError, Places, status
from murakumo.ir.model import Entity, KeyRole
from murakumo.ir.types import parse_type
from murakumo.project import Project

LAYERS = ("raw", "staging")  # where relations are looked for; later tables' joins come from the model
DISTINCT_SAMPLE = 1_000  # distinct values compared per side in an overlap check
MAX_PAIRS = 12
MAX_FIND = 40
PROFILE_DAYS = 1  # a profile without a row count to compare is asked again after this

_ID_WORDS = {"id", "key", "no", "num", "nbr", "number", "sk", "fk", "pk", "ref", "uuid", "guid"}
_SYNONYMS = {
    "cust": "customer",
    "client": "customer",
    "prod": "product",
    "emp": "employee",
    "acct": "account",
    "ord": "order",
    "addr": "address",
    "org": "organization",
    "cat": "category",
    "supp": "supplier",
    "vend": "vendor",
    "txn": "transaction",
    "trx": "transaction",
    "usr": "user",
    "loc": "location",
    "dept": "department",
}
_TABLE_PREFIXES = ("raw_", "stg_", "src_", "int_", "dim_", "fct_", "fact_", "base_")


# Tier 0: names ---------------------------------------------------------------------------------------


# Words that say which of several ids of the same thing a column is: a trip's pickup and dropoff
# locations are both location ids.
_ROLES = {
    "pickup",
    "dropoff",
    "pu",
    "do",
    "billing",
    "shipping",
    "parent",
    "child",
    "origin",
    "destination",
    "dest",
    "from",
    "to",
    "src",
    "dst",
    "source",
    "target",
    "primary",
    "secondary",
    "home",
    "work",
    "current",
    "previous",
}


def _words(name: str) -> list[str]:
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)  # PULocationID: PU, Location, ID
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", spaced)
    return [w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w]


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def subject(table: str) -> str:
    """What a table holds, in one word: ``stg_erp__orders`` → ``order``, ``dim_customer`` → ``customer``."""
    name = table.lower().split("__")[-1]
    for prefix in _TABLE_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix) :]
    words = _words(name) or [name]
    return _SYNONYMS.get(words[-1], _singular(words[-1]))


def canonical(table: str, column: str) -> str | None:
    """A column's name, normalised so names that mean the same id match: ``customer_id``,
    ``CustomerID``, ``cust_id``, ``c_custkey``, ``customer_key`` and a customer table's ``id`` are
    all ``customer_id``. None for a column that is not an id."""
    words = _words(column)
    if len(words) > 1 and len(words[0]) <= 2 and words[0] not in _ID_WORDS:
        words = words[1:]  # a table's own short prefix: c_custkey, o_orderdate
    while len(words) > 2 and words[0] in _ROLES:
        words = words[1:]  # pickup_location_id is a location id
    split: list[str] = []
    for word in words:
        for suffix in ("key", "id", "no", "num", "nbr"):
            if word.endswith(suffix) and len(word) > len(suffix) + 2:
                split += [word[: -len(suffix)], suffix]
                break
        else:
            split.append(word)
    if not split or split[-1] not in _ID_WORDS:
        return None
    stem = [_SYNONYMS.get(w, _singular(w)) for w in split[:-1]]
    if not stem:
        stem = [subject(table)]  # a table's own id: customer.id is customer_id
    return "_".join([*stem, "id"])


def _family(type_text: str) -> str:
    try:
        family = parse_type(type_text).family
    except ValueError:
        return "text"
    return "number" if family == "integer" else family


def _joinable(a: str, b: str) -> bool:
    fa, fb = _family(a), _family(b)
    return fa == fb or {fa, fb} == {"text", "number"}  # ids are often numbers in one table, text in another


@dataclass
class Candidate:
    entity: str
    column: str
    layer: str
    type: str
    why: str  # how it was found
    strength: str  # "name" (the same id), "related" (the model joins them), "maybe" (an id of a joinable type)
    unique: bool = False  # the model says its values are unique there (a key)
    overlap: dict[str, Any] | None = None  # tier 2's answer, when checked

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _searched(project: Project) -> list[Entity]:
    return [e for e in project.model.entities if e.layer.value in LAYERS]


def _unique(entity: Entity, column: str) -> bool:
    attribute = entity.attribute(column)
    keyed = attribute is not None and attribute.key in (KeyRole.PRIMARY, KeyRole.NATURAL, KeyRole.SURROGATE)
    return keyed or entity.grain == [column]


def candidates(project: Project, entity_name: str, column: str) -> dict[str, Any]:
    """Tier 0: where a column probably appears again, from the model alone."""
    entity = project.model.entity(entity_name)
    if entity is None:
        raise ExploreError(f"no table {entity_name!r} in the model")
    attribute = next((a for a in entity.attributes if a.name.lower() == column.lower()), None)
    if attribute is None:
        raise ExploreError(f"{entity.name} has no column {column!r}")
    name = canonical(entity.name, attribute.name)
    found: dict[tuple[str, str], Candidate] = {}

    for relationship in project.model.relationships:
        for key in relationship.keys:
            ends = [
                (relationship.from_, key.from_, relationship.to, key.to),
                (relationship.to, key.to, relationship.from_, key.from_),
            ]
            for here, here_column, there, there_column in ends:
                if here.lower() != entity.name.lower() or here_column.lower() != attribute.name.lower():
                    continue
                other = project.model.entity(there)
                other_column = other and next((a for a in other.attributes if a.name == there_column), None)
                if other is None or other_column is None:
                    continue
                found[(other.name, other_column.name)] = Candidate(
                    other.name,
                    other_column.name,
                    other.layer.value,
                    other_column.type,
                    f"the model joins them ({relationship.name or relationship.from_ + ' → ' + relationship.to})",
                    "related",
                    _unique(other, other_column.name),
                )

    for other in _searched(project):
        for other_column in other.attributes:
            if other.name == entity.name and other_column.name == attribute.name:
                continue
            if (other.name, other_column.name) in found or not _joinable(attribute.type, other_column.type):
                continue
            other_name = canonical(other.name, other_column.name)
            if other_name is None:
                continue
            if name and other_name == name:
                same = other_column.name.lower() == attribute.name.lower()
                why = "the same name" if same else f"both name a {name.removesuffix('_id').replace('_', ' ')} id"
                found[(other.name, other_column.name)] = Candidate(
                    other.name,
                    other_column.name,
                    other.layer.value,
                    other_column.type,
                    why,
                    "name",
                    _unique(other, other_column.name),
                )
            else:
                found[(other.name, other_column.name)] = Candidate(
                    other.name,
                    other_column.name,
                    other.layer.value,
                    other_column.type,
                    "an id of a type that joins",
                    "maybe",
                    _unique(other, other_column.name),
                )
    order = {"related": 0, "name": 1, "maybe": 2}
    listed = sorted(found.values(), key=lambda c: (order[c.strength], c.layer != "raw", c.entity, c.column))
    return {
        "entity": entity.name,
        "column": attribute.name,
        "canonical": name,
        "unique": _unique(entity, attribute.name),
        "candidates": [c.as_dict() for c in listed],
    }


# Tier 1: column summaries, cached -------------------------------------------------------------------


@dataclass
class Profile:
    rows: int  # rows sampled
    filled: int
    distinct: int
    smallest: Any
    largest: Any
    table_rows: int | None  # the table's rows when profiled, from the catalog: a change means new data
    at: float = field(default_factory=time.time)

    @property
    def looks_unique(self) -> bool:
        return self.filled > 0 and self.distinct == self.filled


def _cache_path(project: Project) -> Path:
    return project.root / ".murakumo" / "profiles.json"


def _read_cache(project: Project) -> dict[str, dict]:
    try:
        return json.loads(_cache_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cache(project: Project, cache: dict[str, dict]) -> None:
    path = _cache_path(project)
    try:
        path.parent.mkdir(exist_ok=True)
        ignore = path.parent / ".gitignore"
        if not ignore.exists():
            ignore.write_text("# Murakumo's local cache: nothing here belongs in git\n*\n", encoding="utf-8")
        path.write_text(json.dumps(cache, indent=1, default=str), encoding="utf-8")
    except OSError:
        pass  # a cache that cannot be written is only slower next time


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, int | float | str | bool):
        return value
    return str(value)


def profiles(
    project: Project, columns: dict[str, list[str]], environment: str | None = None, places: Places | None = None
) -> dict[tuple[str, str], Profile]:
    """Tier 1: a summary of each column, {entity: [columns]}, from the cache where the table has not
    changed since, else one sampled query per table."""
    from murakumo import query

    places = places or Places(project, environment)
    counts = status(project, environment)["tables"]
    cache = _read_cache(project)
    scope = f"{places.placement.environment}:{places.placement.prefix}"
    found: dict[tuple[str, str], Profile] = {}
    changed = False
    for entity, names in columns.items():
        located = places.get(entity)
        table_rows = (counts.get(located.entity) or {}).get("rows")
        wanted = []
        for name in names:
            key = f"{scope}:{located.entity}.{name}".lower()
            saved = cache.get(key)
            fresh = saved and (
                saved.get("table_rows") == table_rows
                if table_rows is not None
                else time.time() - saved.get("at", 0) < PROFILE_DAYS * 86_400
            )
            if fresh:
                found[(located.entity, name)] = Profile(**saved)
            else:
                wanted.append(name)
        if not wanted:
            continue
        q = places.quote
        parts = ["count(*) as n"]
        for i, name in enumerate(wanted):
            parts += [
                f"count({q(name)}) as f{i}",
                f"count(distinct {q(name)}) as d{i}",
                f"min({q(name)}) as lo{i}",
                f"max({q(name)}) as hi{i}",
            ]
        columns_sql = ", ".join(q(n) for n in wanted)
        sql = f"select {', '.join(parts)} from {places.sample(located.relation, SAMPLE, columns_sql)} as s"
        try:
            result = query.run(project, sql, "warehouse", environment, 1, 60)
        except query.QueryError:
            continue  # not loaded yet: no summary, so no pruning on it
        row = result.rows[0]
        for i, name in enumerate(wanted):
            profile = Profile(
                rows=int(row[0] or 0),
                filled=int(row[1 + 4 * i] or 0),
                distinct=int(row[2 + 4 * i] or 0),
                smallest=_plain(row[3 + 4 * i]),
                largest=_plain(row[4 + 4 * i]),
                table_rows=table_rows,
            )
            found[(located.entity, name)] = profile
            cache[f"{scope}:{located.entity}.{name}".lower()] = asdict(profile)
            changed = True
    if changed:
        _write_cache(project, cache)
    return found


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ranges_meet(a: Profile, b: Profile) -> bool:
    """Whether two columns' values can overlap at all, from their smallest and largest."""
    if a.filled == 0 or b.filled == 0:
        return False
    numbers = [_number(v) for v in (a.smallest, a.largest, b.smallest, b.largest)]
    if all(n is not None for n in numbers):
        return numbers[0] <= numbers[3] and numbers[2] <= numbers[1]  # type: ignore[operator]
    texts = [str(v) for v in (a.smallest, a.largest, b.smallest, b.largest)]
    return texts[0] <= texts[3] and texts[2] <= texts[1]


# Tier 2: overlap -------------------------------------------------------------------------------------


def _overlap_sql(places: Places, a, a_column: str, b, b_column: str) -> str:
    q = places.quote
    x, y = q(a_column), q(b_column)
    return places.sql(
        f"with a as (select distinct cast({x} as varchar) as v from "
        f"(select {x} from {a.relation} where {x} is not null limit {SAMPLE}) s limit {DISTINCT_SAMPLE}), "
        f"b as (select distinct cast({y} as varchar) as v from "
        f"(select {y} from {b.relation} where {y} is not null limit {SAMPLE}) s limit {DISTINCT_SAMPLE}) "
        f"select (select count(*) from a) as a_sampled, "
        f"(select count(*) from a where v in (select cast({y} as varchar) from {b.relation})) as a_found, "
        f"(select count(*) from b) as b_sampled, "
        f"(select count(*) from b where v in (select cast({x} as varchar) from {a.relation})) as b_found"
    )


def _verdict(entity: str, column: str, unique: bool, other: Candidate, a_share: float, b_share: float) -> dict:
    """What an overlap means: which side points at which, and how many rows match each."""
    if a_share >= 0.9 and other.unique and unique:
        cardinality, kind = "one_to_one", "one to one"
    elif a_share >= 0.9 and other.unique:
        cardinality, kind = "many_to_one", f"many {entity} rows to one {other.entity} row"
    elif b_share >= 0.9 and unique:
        cardinality, kind = "one_to_many", f"one {entity} row to many {other.entity} rows"
    elif max(a_share, b_share) >= 0.5:
        cardinality, kind = "many_to_many", "some values in common, unique on neither side"
    else:
        cardinality, kind = None, "few values in common"
    overlaps = max(a_share, b_share) >= 0.8
    if overlaps and other.strength == "maybe":
        # Small whole-number ids (1, 2, 3...) overlap by chance: with names that differ, only a person can say.
        return {"cardinality": cardinality, "kind": kind, "likely": False, "chance": True, "shared": False}
    if overlaps and cardinality == "many_to_many":
        # The same values, unique on neither side: both point at something else (a trip's pickup and
        # dropoff locations), so neither joins the other.
        kind = "the same values, unique on neither side: both probably point at a third table"
        return {"cardinality": cardinality, "kind": kind, "likely": False, "chance": False, "shared": True}
    return {"cardinality": cardinality, "kind": kind, "likely": overlaps, "chance": False, "shared": False}


def check(
    project: Project,
    entity_name: str,
    column: str,
    environment: str | None = None,
    only: list[str] | None = None,
    wide: bool = False,
) -> dict[str, Any]:
    """Tiers 1 and 2: summarise the candidates, drop those whose values cannot meet, and measure
    the overlap of the rest. By default only the columns named like it, and those the model already
    joins, are checked; ``wide`` adds ids with other names. ``only`` picks candidates ("table.column")."""
    from murakumo import query

    found = candidates(project, entity_name, column)
    places = Places(project, environment)
    listed = [Candidate(**c) for c in found["candidates"]]
    if only:
        wanted = {o.lower() for o in only}
        listed = [c for c in listed if f"{c.entity}.{c.column}".lower() in wanted]
    elif not wide:
        listed = [c for c in listed if c.strength != "maybe"]
    entity, column = found["entity"], found["column"]
    columns: dict[str, list[str]] = {entity: [column]}
    for candidate in listed:
        columns.setdefault(candidate.entity, []).append(candidate.column)
    summaries = profiles(project, columns, environment, places)
    mine = summaries.get((places.get(entity).entity, column))
    if mine is None:
        raise ExploreError(f"{entity} is not in the warehouse yet: load the raw tables first")
    unique = found["unique"] or mine.looks_unique
    ruled_out: list[dict] = []
    checked: list[Candidate] = []
    for candidate in listed:
        theirs = summaries.get((places.get(candidate.entity).entity, candidate.column))
        if theirs is None:
            ruled_out.append({**candidate.as_dict(), "reason": "not loaded yet"})
            continue
        candidate.unique = candidate.unique or theirs.looks_unique
        if not _ranges_meet(mine, theirs):
            ruled_out.append({**candidate.as_dict(), "reason": "their values cannot overlap (different ranges)"})
            continue
        checked.append(candidate)
    order = {"related": 0, "name": 1, "maybe": 2}
    checked.sort(key=lambda c: order[c.strength])
    skipped = checked[MAX_PAIRS:]
    here = places.get(entity)
    for candidate in checked[:MAX_PAIRS]:
        there = places.get(candidate.entity)
        try:
            result = query.run(
                project, _overlap_sql(places, here, column, there, candidate.column), "warehouse", environment, 1, 60
            )
        except query.QueryError as exc:
            ruled_out.append({**candidate.as_dict(), "reason": f"could not compare: {exc}"})
            continue
        a_sampled, a_found, b_sampled, b_found = (int(v or 0) for v in result.rows[0])
        a_share = a_found / a_sampled if a_sampled else 0.0
        b_share = b_found / b_sampled if b_sampled else 0.0
        candidate.overlap = {
            "here_in_there": round(a_share, 3),
            "there_in_here": round(b_share, 3),
            "sampled": a_sampled,
            **_verdict(entity, column, unique, candidate, a_share, b_share),
        }
    measured = [c for c in checked[:MAX_PAIRS] if c.overlap]
    measured.sort(
        key=lambda c: (
            not c.overlap["likely"],  # type: ignore[index]
            order[c.strength],
            -max(c.overlap["here_in_there"], c.overlap["there_in_here"]),  # type: ignore[index]
        )
    )
    return {
        "entity": entity,
        "column": column,
        "unique": unique,
        "profile": asdict(mine),
        "matches": [c.as_dict() for c in measured],
        "ruled_out": ruled_out,
        "not_checked": [c.as_dict() for c in skipped],
        "sample": SAMPLE,
        "wide": wide,
        "others": sum(1 for c in found["candidates"] if c["strength"] == "maybe"),
    }


# Tier 3: one value --------------------------------------------------------------------------------


def find(project: Project, value: str, environment: str | None = None, ids_only: bool = True) -> dict[str, Any]:
    """Which raw and staging columns hold ``value``: only columns of a type it can be, inside their
    known range, each searched until its first match."""
    from murakumo import query

    value = value.strip()
    if not value:
        raise ExploreError("give a value to look for")
    places = Places(project, environment)
    loaded = {k for k, v in status(project, environment)["tables"].items() if v["exists"]}
    numeric = _number(value) is not None
    raw_text = project.manifest.modelling.raw_columns == "text"
    cache = _read_cache(project)
    scope = f"{places.placement.environment}:{places.placement.prefix}"
    searched: list[tuple[Any, str, str]] = []
    skipped = 0
    for entity in _searched(project):
        if entity.layer.value == "raw" and entity.name not in loaded:
            continue
        for attribute in entity.attributes:
            family = _family(attribute.type)
            if family not in ("text", "number") or (family == "number" and not numeric):
                continue
            if ids_only and canonical(entity.name, attribute.name) is None and attribute.key is None:
                continue
            saved = cache.get(f"{scope}:{entity.name}.{attribute.name}".lower())
            if saved and family == "number" and numeric:
                low, high = _number(saved.get("smallest")), _number(saved.get("largest"))
                if low is not None and high is not None and not low <= float(value) <= high:
                    skipped += 1
                    continue
            as_text = family == "text" or (raw_text and entity.layer.value == "raw")
            searched.append((places.get(entity.name), attribute.name, "text" if as_text else family))
    if not searched:
        return {"value": value, "found": [], "searched": 0, "skipped": skipped}
    hits: list[dict[str, str]] = []
    literal = "'" + value.replace("'", "''") + "'"
    for start in range(0, min(len(searched), MAX_FIND * 5), MAX_FIND):
        parts = []
        for located, column, family in searched[start : start + MAX_FIND]:
            q = places.quote(column)
            match = f"{q} = {value}" if family == "number" else f"cast({q} as varchar) = {literal}"
            parts.append(
                f"select * from (select '{located.entity}' as table_name, '{column}' as column_name "
                f"from {located.relation} where {match} limit 1) as hit"
            )
        sql = places.sql(" union all ".join(parts))
        try:
            result = query.run(project, sql, "warehouse", environment, MAX_FIND, 60)
        except query.QueryError as exc:
            raise ExploreError(str(exc)) from None
        hits += [{"entity": r[0], "column": r[1]} for r in result.rows]
    return {"value": value, "found": hits, "searched": len(searched), "skipped": skipped}


__all__ = ["candidates", "canonical", "check", "find", "profiles", "subject"]
