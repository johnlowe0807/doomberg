"""Safe, explicit JSON encoding for provider responses and derived result values."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import zlib
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd


def encode(value):
    if value is pd.NA or value is pd.NaT:
        return {'@': 'nan'}
    if isinstance(value, np.generic):
        return encode(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return {'@': 'float', 'v': str(value)}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, pd.DataFrame):
        return {'@': 'frame', 'index': encode(value.index), 'columns': encode(value.columns),
                'dtypes': [str(t) for t in value.dtypes], 'data': encode(value.to_numpy().tolist())}
    if isinstance(value, pd.Series):
        return {'@': 'series', 'index': encode(value.index), 'name': encode(value.name),
                'dtype': str(value.dtype), 'data': encode(value.tolist())}
    if isinstance(value, pd.DatetimeIndex):
        return {'@': 'datetime-index', 'v': value.as_unit('ns').asi8.tolist(), 'unit': value.unit, 'freq': value.freqstr, 'tz': str(value.tz) if value.tz else None,
                'name': encode(value.name)}
    if isinstance(value, pd.MultiIndex):
        return {'@': 'multi-index', 'v': encode(value.tolist()), 'names': encode(value.names)}
    if isinstance(value, pd.Index):
        return {'@': 'index', 'v': encode(value.tolist()), 'name': encode(value.name)}
    if isinstance(value, (datetime, date)):
        return {'@': 'datetime' if isinstance(value, datetime) else 'date', 'v': value.isoformat()}
    if hasattr(value, 'calls') and hasattr(value, 'puts'):
        return {'@': 'chain', 'calls': encode(value.calls), 'puts': encode(value.puts),
                'underlying': encode(getattr(value, 'underlying', {}))}
    if is_dataclass(value):
        return {'@': 'record', 'v': encode(asdict(value))}
    if isinstance(value, dict):
        return {'@': 'mapping', 'v': [[encode(k), encode(v)] for k, v in sorted(value.items(), key=lambda item: json.dumps(encode(item[0]), sort_keys=True))]}
    if isinstance(value, (tuple, list, set)):
        return {'@': 'tuple' if isinstance(value, tuple) else 'list', 'v': [encode(v) for v in value]}
    if isinstance(value, bytes):
        return {'@': 'bytes', 'v': base64.b64encode(value).decode()}
    raise TypeError(f'Unsupported data value: {type(value).__name__}')


def decode(value):
    if not isinstance(value, dict) or '@' not in value:
        return value
    kind = value['@']
    if kind in ('nan', 'float'):
        return float(value.get('v', 'nan'))
    if kind == 'mapping':
        return {decode(k): decode(v) for k, v in value['v']}
    if kind == 'record':
        return decode(value['v'])
    if kind in ('list', 'tuple'):
        values = [decode(v) for v in value['v']]
        return tuple(values) if kind == 'tuple' else values
    if kind == 'datetime-index':
        idx = pd.to_datetime(value['v'], utc=bool(value['tz']))
        if value['tz']:
            idx = idx.tz_convert(value['tz'])
        return pd.DatetimeIndex(idx, name=decode(value['name']), freq=value.get('freq')).as_unit(value.get('unit', 'ns'))
    if kind == 'multi-index':
        return pd.MultiIndex.from_tuples(decode(value['v']), names=decode(value['names']))
    if kind == 'index':
        return pd.Index(decode(value['v']), name=decode(value['name']))
    if kind == 'frame':
        frame = pd.DataFrame(decode(value['data']), index=decode(value['index']), columns=decode(value['columns']))
        for i, dtype in enumerate(value['dtypes']):
            frame.isetitem(i, frame.iloc[:, i].astype(dtype))
        return frame
    if kind == 'series':
        return pd.Series(decode(value['data']), index=decode(value['index']), name=decode(value['name']), dtype=value['dtype'])
    if kind == 'chain':
        return SimpleNamespace(calls=decode(value['calls']), puts=decode(value['puts']), underlying=decode(value['underlying']))
    if kind == 'datetime':
        return datetime.fromisoformat(value['v'])
    if kind == 'date':
        return date.fromisoformat(value['v'])
    if kind == 'bytes':
        return base64.b64decode(value['v'])
    raise ValueError(f'Unknown serialized type: {kind}')


def canonical(value):
    return json.dumps(encode(value), sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def key(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def pack(value):
    return zlib.compress(canonical(value).encode())


def unpack(value):
    return decode(json.loads(zlib.decompress(value)))
