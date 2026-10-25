"""Dataset-independent validation and selection. Never evaluates Python or raw SQL."""
from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, replace
from typing import Callable

from .query_models import (BooleanExpression, Condition, Result, SortOrder, TableData)
from .serialization import key


@dataclass(frozen=True)
class Field:
    kind: str  # text, number, boolean
    title: str = ''
    dependencies: tuple[str, ...] = ()
    compute: Callable | None = None  # pure row calculation, run in a worker
    unit: str = ''
    period: str = ''


@dataclass(frozen=True)
class Dataset:
    fields: dict[str, Field]
    defaults: tuple[str, ...]
    search_fields: tuple[str, ...]
    loader: Callable  # async (service, required_fields, force, stored_only) -> Result[TableBatch]
    executor: Callable | None = None  # optional provider planner; returns Result[TableData]
    validate: Callable | None = None  # provider capability validation; no I/O


def normalize(query, dataset):
    """Validate before loading anything. Identifiers normalize; literal values do not."""
    if type(query.limit) is not int or not 1 <= query.limit <= 10000:
        raise ValueError('LIMIT must be an integer from 1 to 10000')
    used = set()

    def field(name):
        name = name.casefold()
        if name not in dataset.fields:
            raise ValueError(f'Unknown field {name}; available: ' + ', '.join(dataset.fields))
        used.add(name)
        return name

    def expression(node, depth=0):
        if depth > 32:
            raise ValueError('WHERE expressions support at most 32 levels')
        if isinstance(node, BooleanExpression):
            op = node.operator.upper()
            if op not in ('AND', 'OR', 'NOT') or not node.children or (op == 'NOT' and len(node.children) != 1):
                raise ValueError('Use AND/OR groups or NOT with one expression')
            return BooleanExpression(op, tuple(expression(c, depth + 1) for c in node.children))
        if not isinstance(node, Condition):
            raise ValueError('Invalid WHERE expression')
        name, op = field(node.field), node.operator.upper()
        kind, value = dataset.fields[name].kind, node.value
        if op not in ('=', '!=', '<>', '>', '>=', '<', '<=', 'IS NULL', 'IS NOT NULL', 'IN', 'LIKE', 'CONTAINS'):
            raise ValueError(f'Unsupported operator {op}')
        if op in ('IS NULL', 'IS NOT NULL'):
            if value is not None:
                raise ValueError(f'{op} takes no value')
            return Condition(name, op)
        if op in ('LIKE', 'CONTAINS') and kind != 'text':
            raise ValueError(f'{op} requires a text field')
        if op in ('>', '>=', '<', '<=') and kind == 'boolean':
            raise ValueError('Boolean fields support equality, IN and IS NULL')
        values = value if op == 'IN' else (value,)
        if not isinstance(values, (tuple, list)) or not values or len(values) > 1000:
            raise ValueError('IN needs 1–1000 values')
        for v in values:
            valid = (isinstance(v, str) if kind == 'text' else type(v) is bool if kind == 'boolean' else
                     type(v) in (int, float) and math.isfinite(v))
            if not valid:
                raise ValueError(f'{name} expects {kind} values; use IS NULL for missing data')
        return Condition(name, op, tuple(values) if op == 'IN' else value)

    where = expression(query.where) if query.where is not None else None
    fields = tuple(field(f) for f in (query.fields or dataset.defaults))
    if len(fields) != len(set(fields)):
        raise ValueError('FIELDS must list each field once')
    sort = tuple(SortOrder(field(s.field), s.descending) for s in query.sort)
    if any(type(s.descending) is not bool for s in sort) or len({s.field for s in sort}) != len(sort):
        raise ValueError('SORT must list each field once with ASC or DESC')
    if not isinstance(query.search, tuple) or any(not isinstance(s, str) or not s.strip() for s in query.search):
        raise ValueError('SEARCH requires nonempty text')
    if query.search:
        used.update(field(f) for f in dataset.search_fields)

    visiting, ordered = set(), []

    def dependency(name):
        if name in visiting:
            raise ValueError('Derived field dependency cycle: ' + name)
        if name in ordered:
            return
        visiting.add(name)
        for dep in dataset.fields[name].dependencies:
            dependency(field(dep))
        visiting.remove(name)
        ordered.append(name)

    for name in sorted(used):
        dependency(name)
    normalized = replace(query, dataset=query.dataset.casefold(), where=where, fields=fields, sort=sort)
    if dataset.validate:
        dataset.validate(normalized, dataset)
    return normalized, tuple(ordered)


def clean(value, kind):
    if value is None:
        return None
    if kind == 'number':
        try:
            return float(value) if not isinstance(value, bool) and math.isfinite(float(value)) else None
        except (ValueError, TypeError, OverflowError):
            return None
    return value if ((isinstance(value, str) and kind == 'text') or (type(value) is bool and kind == 'boolean')) else None


def matches(node, row):
    """SQL three-valued logic: missing operands are unknown, including under NOT."""
    if isinstance(node, BooleanExpression):
        values = [matches(c, row) for c in node.children]
        if node.operator == 'NOT':
            return None if values[0] is None else not values[0]
        if node.operator == 'AND':
            return False if False in values else None if None in values else True
        return True if True in values else None if None in values else False
    value, op, wanted = row.get(node.field), node.operator, node.value
    if op == 'IS NULL':
        return value is None
    if op == 'IS NOT NULL':
        return value is not None
    if value is None:
        return None
    if isinstance(value, str):
        value = value.casefold()
        wanted = tuple(v.casefold() for v in wanted) if op == 'IN' else wanted.casefold()
    if op == 'IN':
        return value in wanted
    if op == 'CONTAINS':
        return wanted in value
    if op == 'LIKE':
        pattern = ''.join('.*' if c == '%' else '.' if c == '_' else re.escape(c) for c in wanted)
        return re.fullmatch(pattern, value, re.DOTALL) is not None
    if op == '=':
        return value == wanted
    if op in ('!=', '<>'):
        return value != wanted
    if op == '>':
        return value > wanted
    if op == '>=':
        return value >= wanted
    if op == '<':
        return value < wanted
    return value <= wanted


def select(query, dataset, required, batch):
    rows = []
    missing = dict.fromkeys(required, 0)
    for original in batch.rows:
        row = dict(original)
        for name in required:
            spec = dataset.fields[name]
            if spec.compute:
                row[name] = spec.compute(row)
            row[name] = clean(row.get(name), spec.kind)
            if row[name] is None:
                missing[name] += 1
        rows.append(row)
    selected = [r for r in rows if (query.where is None or matches(query.where, r) is True) and
                all(term.casefold() in ' '.join(str(r.get(f) or '') for f in dataset.search_fields).casefold()
                    for term in query.search)]
    # Stable sorting; missing data is last in either direction. Never limit before selection.
    for order in reversed(query.sort):
        known = [r for r in selected if r[order.field] is not None]
        absent = [r for r in selected if r[order.field] is None]
        known.sort(key=lambda r: r[order.field].casefold() if isinstance(r[order.field], str) else r[order.field],
                   reverse=order.descending)
        selected = known + absent
    return TableData(tuple({f: r.get(f) for f in query.fields} for r in selected[:query.limit]), query.fields,
                     batch.universe, batch.expected, len(rows), len(selected),
                     batch.complete and len(rows) == batch.expected, {f: n for f, n in missing.items() if n})


async def execute(service, query, *, force=False, stored_only=False):
    dataset = service.table_dataset(query.dataset)
    query, required = normalize(query, dataset)
    if dataset.executor:
        result = await dataset.executor(service, query, dataset, required, force, stored_only)
        if result.data is None:
            return result
        version = await asyncio.to_thread(key, (query, result.data, result.meta.version))
        return Result(result.data, replace(result.meta, version=version), result.inputs)
    result = await dataset.loader(service, required, force, stored_only)
    if result.data is None:
        return Result(None, result.meta, result.inputs)
    data = await asyncio.to_thread(select, query, dataset, required, result.data)
    version = await asyncio.to_thread(key, (query, data, result.meta.version))
    return Result(data, replace(result.meta, version=version), result.inputs)
