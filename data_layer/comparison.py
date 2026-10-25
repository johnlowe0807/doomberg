"""Typed graph references and date-aligned comparisons across free data sources."""
from __future__ import annotations

import re
import numpy as np
import pandas as pd
from .windows import is_range, candle_interval, slice_range

MODES = ('NORMALIZED', 'REAL', 'STACKED')


def mode(value):
    result = {'NORM': 'NORMALIZED', 'NORMAL': 'NORMALIZED', 'RAW': 'REAL', 'DUAL': 'STACKED', 'STACK': 'STACKED'}.get(value.upper(), value.upper())
    if result not in MODES:
        raise ValueError('Comparison mode must be NORMALIZED, REAL or STACKED')
    return result


def reference(value, aliases):
    value = value.upper()
    source, _, symbol = value.partition(':')
    if not symbol:
        source, symbol = 'YF', source
    if source in ('YAHOO', 'SECURITY'):
        source = 'YF'
    if source not in ('YF', 'FRED', 'ERN'):
        raise ValueError('Graph source must be FRED:, ERN: or a security ticker')
    symbol = symbol if source == 'FRED' else aliases.get(symbol, symbol)
    pattern = r'[A-Z0-9_.-]{1,80}' if source == 'FRED' else r'[A-Z0-9.^=\-]{1,32}'
    if not re.fullmatch(pattern, symbol):
        raise ValueError(f'Invalid {source} graph identifier: {symbol}')
    return symbol if source == 'YF' else f'{source}:{symbol}'


def parse(args, aliases, defaults=(), default_mode='NORMALIZED', options=None):
    words, settings = [], dict(options or {})
    for arg in args:
        if arg.startswith('_') and '=' in arg:
            key, value = arg[1:].split('=', 1); key = key.lower()
            if key in settings or not value:
                raise ValueError(f'Supply one nonempty _{key}= argument')
            settings[key] = value
        else:
            words.append(arg)
    refs = [reference(word, aliases) for word in words if not is_range(word)]
    selected_mode, seen_mode = default_mode, False
    additions = []
    for key, value in settings.items():
        key = key.lower()
        if key in ('mode', 'm', 't', 'type'):
            if seen_mode:
                raise ValueError('Supply only one comparison mode argument')
            selected_mode, seen_mode = mode(value), True
        elif key in ('fred', 'ern'):
            symbols = value.strip('[]').split(',')
            if any(not symbol.strip() for symbol in symbols):
                raise ValueError(f'_{key}= needs comma-separated identifiers')
            additions.extend(reference(key.upper() + ':' + symbol.strip(), aliases) for symbol in symbols)
        else:
            raise ValueError('Comparison arguments: _fred=SERIES[,SERIES] _ern=TICKER _mode=NORMALIZED|REAL|STACKED')
    if not refs:
        refs = list(defaults)
    refs = list(dict.fromkeys(refs + additions))
    if not 1 <= len(refs) <= 8:
        raise ValueError('Compare 1–8 graph series, e.g. /COMP AAPL _fred=DGS10')
    validate_mode(selected_mode, refs)
    return refs, selected_mode


def validate_mode(selected_mode, refs):
    if not 1 <= len(refs) <= 8:
        raise ValueError('Compare 1–8 graph series')


def securities(refs):
    return [ref for ref in refs if not ref.startswith(('FRED:', 'ERN:'))]


async def fetch(hub, ref, rng, interval):
    if ref.startswith('FRED:'):
        from .query_models import SeriesQuery
        return (await hub.query(SeriesQuery(ref[5:]))).data
    if ref.startswith('ERN:'):
        symbol = ref[4:]
        frame = await hub.resource('yf', 'earnings', symbol=symbol)
        if frame is None or 'Surprise(%)' not in frame:
            return None
        values = pd.to_numeric(frame['Surprise(%)'], errors='coerce').dropna().sort_index()
        return values[values.index <= pd.Timestamp.now(tz=values.index.tz)]
    frame = await hub.bars(ref, rng, interval)
    return frame.Close if frame is not None and len(frame) else None


def align(columns, rng, interval):
    """Union observation dates, carrying values only between actual observations; never backfill."""
    series = {}
    for ref, values in columns.items():
        values = values.copy()
        index = pd.DatetimeIndex(values.index)
        if index.tz is not None:
            index = index.tz_convert('America/New_York').tz_localize(None)
        if candle_interval(interval).minutes >= 1440:
            index = index.normalize()
        values.index = index
        values = pd.to_numeric(values, errors='coerce').replace([np.inf, -np.inf], np.nan).dropna()
        values = values[~values.index.duplicated(keep='last')].sort_index()
        if len(values):
            series[ref] = values
    if not series:
        return pd.DataFrame()
    dates = pd.DatetimeIndex(sorted(set().union(*(s.index for s in series.values()))))
    aligned = {ref: values.reindex(dates).ffill().where((dates >= values.index[0]) & (dates <= values.index[-1]))
               for ref, values in series.items()}
    return slice_range(pd.DataFrame(aligned, index=dates), rng).dropna(how='all')


def normalized(frame):
    overlap = frame.dropna()
    if overlap.empty:
        raise ValueError('No overlapping observations to normalize; choose REAL or STACKED')
    baseline = overlap.iloc[0]
    zero = baseline.index[baseline == 0].tolist()
    if zero:
        raise ValueError('Zero normalization baseline for ' + ', '.join(zero) + '; choose REAL or STACKED')
    return (frame.loc[overlap.index[0]:] / baseline - 1) * 100
