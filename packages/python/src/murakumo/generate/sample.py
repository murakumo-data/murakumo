"""Synthetic rows for raw tables that respect the model, for ``murakumo verify``.

The rows follow every rule the model states, so the generated tests should pass on them:
primary keys are unique (composite ones too), foreign keys point at parent rows that exist,
accepted values come from the list, NOT NULL columns are never null, and numbers are not
negative. A failing test on this data therefore means the generated code is wrong.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from murakumo.ir.model import Attribute, Entity, Model, RuleType
from murakumo.ir.types import LogicalType, TypeName

T = TypeName
START = datetime(2024, 1, 1)


def _bounds(attribute: Attribute) -> tuple[float, float]:
    """The range numbers may take: 0 to 1000, narrowed by any range test on the column."""
    low, high = 0.0, 1000.0
    for rule in attribute.tests:
        if rule.type is RuleType.RANGE:
            low = rule.min if rule.min is not None else low
            high = rule.max if rule.max is not None else max(high, low + 1000)
    return low, high


def _value(attribute: Attribute, rng: random.Random, index: int) -> Any:
    logical: LogicalType = attribute.logical_type
    name = logical.name
    low, high = _bounds(attribute)
    if name in (T.SMALLINT, T.INTEGER, T.BIGINT):
        return rng.randint(int(low), int(high))
    if name in (T.FLOAT, T.DOUBLE):
        return round(rng.uniform(low, high), 3)
    if name in (T.DECIMAL, T.MONEY):
        params = logical.params
        if name is T.MONEY:
            precision, scale = 18, 4
        elif not params:
            precision, scale = 38, 9
        elif len(params) == 1:
            precision, scale = params[0], 0
        else:
            precision, scale = params[0], params[1]
        high = min(high, 10 ** (precision - scale) - 1)
        return Decimal(str(round(rng.uniform(low, high), min(scale, 6))))
    if name is T.BOOLEAN:
        return rng.random() < 0.5
    if name is T.DATE:
        return (START + timedelta(days=rng.randint(0, 365))).date()
    if name is T.TIMESTAMP:
        return START + timedelta(seconds=rng.randint(0, 365 * 86400))
    if name is T.TIMESTAMP_TZ:
        return (START + timedelta(seconds=rng.randint(0, 365 * 86400))).replace(tzinfo=UTC)
    if name is T.TIME:
        return (START + timedelta(seconds=rng.randint(0, 86399))).time()
    if name is T.UUID:
        return str(uuid.UUID(int=rng.getrandbits(128)))
    if name in (T.JSON, T.STRUCT):
        return f'{{"sample": {index}}}'
    if name is T.ARRAY:
        return []
    if name is T.BINARY:
        return bytes([index % 256])
    text = f"{attribute.name.lower()}_{index}"
    if name is T.STRING and logical.params:
        text = text[: logical.params[0]]
    return text


def _sequence(attribute: Attribute, index: int) -> Any:
    """The index-th unique value for a key column."""
    name = attribute.logical_type.name
    if name in (T.SMALLINT, T.INTEGER, T.BIGINT, T.DECIMAL, T.MONEY, T.FLOAT, T.DOUBLE):
        return index + 1
    if name is T.UUID:
        return str(uuid.UUID(int=index + 1))
    if name is T.DATE:
        return (START + timedelta(days=index)).date()
    if name in (T.TIMESTAMP, T.TIMESTAMP_TZ):
        return START + timedelta(minutes=index)
    text = f"{attribute.name.lower()}_{index + 1}"
    if attribute.logical_type.params and name is T.STRING:
        length = attribute.logical_type.params[0]
        text = str(index + 1).zfill(length)[-length:] if len(text) > length else text
    return text


def _accepted(attribute: Attribute, code: str) -> Any:
    family = attribute.logical_type.family
    if family == "integer":
        return int(float(code))
    if family == "number":
        return Decimal(code) if attribute.logical_type.name in (T.DECIMAL, T.MONEY) else float(code)
    if attribute.logical_type.name is T.BOOLEAN:
        return code.lower() in ("true", "t", "y", "yes", "1")
    return code


def _order(model: Model, entities: list[Entity]) -> list[Entity]:
    """Parents before children, so foreign keys have rows to point at."""
    names = {e.name.lower(): e for e in entities}
    parents = {e.name.lower(): set() for e in entities}
    for rel in model.relationships:
        child, parent = rel.from_.lower(), rel.to.lower()
        if child in names and parent in names and child != parent:
            parents[child].add(parent)
    ordered: list[Entity] = []
    done: set[str] = set()
    while len(ordered) < len(entities):
        ready = sorted(n for n in names if n not in done and parents[n] <= done)
        if not ready:  # a cycle: break it at the alphabetically first table
            ready = [sorted(n for n in names if n not in done)[0]]
        for name in ready:
            done.add(name)
            ordered.append(names[name])
    return ordered


def sample_rows(model: Model, entities: list[Entity], rows: int = 200, seed: int = 7) -> dict[str, list[dict]]:
    """Rows per raw entity name, columns keyed by attribute name."""
    rng = random.Random(seed)
    data: dict[str, list[dict]] = {}
    for entity in _order(model, entities):
        own = entity.name.lower()
        foreign = [r for r in model.relationships if r.from_.lower() == own and r.to.lower() != own]
        # Composite foreign keys first, so their columns come from one parent row.
        foreign.sort(key=lambda r: -len(r.keys))
        pk = entity.primary_key
        count = rows

        pk_rows: list[dict] = []
        if pk:
            pools: list[list[Any]] = []
            for attribute in pk:
                parent_values = _parent_pool(model, data, foreign, attribute)
                if parent_values is None:
                    parent_values = [_sequence(attribute, i) for i in range(rows)]
                pools.append(parent_values)
            if len(pk) == 1:
                combos = [(value,) for value in pools[0][:rows]]
            else:  # random combinations, so every parent row gets children
                combos = list(dict.fromkeys(tuple(rng.choice(pool) for pool in pools) for _ in range(rows * 3)))
            pk_rows = [dict(zip((a.name for a in pk), combo, strict=True)) for combo in combos[:rows]]
            count = len(pk_rows)

        table: list[dict] = []
        for index in range(count):
            row: dict[str, Any] = dict(pk_rows[index]) if pk_rows else {}
            for rel in foreign:
                if all(k.from_ in row for k in rel.keys):
                    continue
                parents = data.get(_real_name(model, rel.to), [])
                optional = rel.optional or any(_attr(entity, k.from_).nullable for k in rel.keys)
                if not parents or (optional and rng.random() < 0.05):
                    for key in rel.keys:
                        row.setdefault(key.from_, None if optional else _value(_attr(entity, key.from_), rng, index))
                    continue
                parent = rng.choice(parents)
                for key in rel.keys:
                    row.setdefault(key.from_, parent.get(key.to))
            for attribute in entity.attributes:
                if attribute.name in row:
                    continue
                if attribute.accepted_values:
                    row[attribute.name] = _accepted(attribute, rng.choice(sorted(attribute.accepted_values)))
                elif attribute.nullable and rng.random() < 0.05:
                    row[attribute.name] = None
                else:
                    row[attribute.name] = _value(attribute, rng, index)
            table.append(row)
        data[entity.name] = table
    return data


def _attr(entity: Entity, name: str) -> Attribute:
    found = entity.attribute(name)
    assert found is not None, f"{entity.name}.{name}"
    return found


def _real_name(model: Model, name: str) -> str:
    entity = model.entity(name)
    return entity.name if entity else name


def _parent_pool(model: Model, data: dict, foreign: list, attribute: Attribute) -> list[Any] | None:
    """Values a key column may take when it is also a foreign key: the parent's keys."""
    for rel in foreign:
        for key in rel.keys:
            if key.from_.lower() == attribute.name.lower():
                parents = data.get(_real_name(model, rel.to))
                if parents:
                    return list(dict.fromkeys(p[key.to] for p in parents if p.get(key.to) is not None))
    return None
