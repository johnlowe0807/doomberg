"""Data-only plotting instructions. No providers, application state or quant calculations."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Sequence

import numpy as np

PALETTE = ('#5FD7FF', '#00FF87', '#FFD700', '#FF5FD2', '#FF4D6D', '#AAAAFF', '#FFFFFF', '#FFAA55')


@dataclass
class Series:
    label: str
    values: Sequence
    color: str | tuple = PALETTE[0]
    axis: str = 'left'
    x: Sequence | None = None
    kind: str = 'line'
    marker: str = 'circle'
    point_size: float | None = None
    line_width: float | None = None
    width: float = .65
    colors: Sequence | None = None
    baseline: float | None = 0.


@dataclass
class Guide:
    value: float
    color: str | tuple = '#3A6B80'
    label: str = ''
    marker_last: bool = False
    include_bounds: bool = False


@dataclass
class Axes:
    title: str = ''
    traces: list[Series] = field(default_factory=list)
    limits: tuple[float, float] | None = None
    guides: list[Guide] = field(default_factory=list)
    log: bool = False
    independent: bool = False
    xlabel: str = ''
    ylabel: str = ''
    xlimits: tuple[float, float] | None = None
    xlog: bool = False
    weight: float = 1.
    # 'datetime' also accepts normalized ISO date/timestamp strings and epoch seconds.
    xkind: str = 'auto'
    tick_labels: tuple[str, ...] = field(default=(), init=False)

    def _add(self, kind, x, y, **style):
        style.setdefault('color', PALETTE[len(self.traces) % len(PALETTE)])
        trace = Series(style.pop('label', ''), y, x=x, kind=kind, **style)
        self.traces.append(trace)
        return trace

    def plot(self, x, y, **style):
        return self._add('line', x, y, **style)

    def scatter(self, x, y, *, groups=None, **style):
        """Supplied group labels produce one legend entry/color per group, in encounter order."""
        if groups is None:
            return self._add('scatter', x, y, **style)
        x, y, groups = list(x), list(y), list(groups)
        if len(x) != len(y) or len(y) != len(groups):
            raise ValueError('x, y and groups must have equal lengths')
        result = []
        for group in dict.fromkeys(groups):
            indices = [i for i, value in enumerate(groups) if value == group]
            options = dict(style)
            options['label'] = f"{style.get('label', 'Group')} {group}"
            result.append(self._add('scatter', [x[i] for i in indices], [y[i] for i in indices], **options))
        return result

    def bar(self, x, y, **style):
        return self._add('bar', x, y, **style)

    def area(self, x, y, **style):
        return self._add('area', x, y, **style)

    def candles(self, x, ohlc, *, ohlc_ticks=False, **style):
        return self._add('ohlc' if ohlc_ticks else 'candle', x, ohlc, **style)

    def hline(self, value, **style):
        guide = Guide(value, **style)
        self.guides.append(guide)
        return guide


@dataclass
class Figure:
    panels: list[Axes] = field(default_factory=lambda: [Axes()])
    labels: Sequence[str] = field(default_factory=tuple)
    crosshair: float | None = None
    font_size: int = 12
    line_width: float = 1.
    point_size: float = 0.
    font_path: str = ''
    sharex: bool = False


def subplots(rows=1, *, title='', **style):
    """Create a vertical figure and axes (one Axes for rows=1; otherwise a list)."""
    if not isinstance(rows, int) or rows < 1:
        raise ValueError('rows must be a positive integer')
    axes = [Axes(title=title if i == 0 else '') for i in range(rows)]
    return Figure(axes, **style), axes[0] if rows == 1 else axes


def _time(value):
    if isinstance(value, str):
        value = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if isinstance(value, np.datetime64):
        return float(value.astype('datetime64[us]').astype('int64')) / 1e6 if not np.isnat(value) else np.nan
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('datetime coordinates must include a timezone')
        return value.timestamp()
    return datetime.combine(value, datetime.min.time(), timezone.utc).timestamp()


def _limits(values, name, log):
    if values is not None and (len(values) != 2 or not np.isfinite(values).all() or
                               values[0] >= values[1] or (log and values[0] <= 0)):
        raise ValueError(f'{name} must be finite, increasing and positive on a log axis')


def _missing(value):
    return value is None or isinstance(value, (float, np.floating)) and not np.isfinite(value)


def snapshot(figure: Figure) -> Figure:
    """Validate and detach a figure from caller-owned arrays before any queued rendering."""
    figure = deepcopy(figure)
    figure.labels = tuple(str(v) for v in figure.labels)
    for name in ('font_size', 'line_width', 'point_size'):
        value = getattr(figure, name)
        if not np.isfinite(value) or value < (1 if name == 'font_size' else 0):
            raise ValueError(f'{name} must be finite and nonnegative')
    if figure.crosshair is not None and not np.isfinite(figure.crosshair):
        raise ValueError('crosshair must be finite')
    for panel in figure.panels:
        _limits(panel.limits, 'y limits', panel.log)
        _limits(panel.xlimits, 'x limits', panel.xlog)
        if not np.isfinite(panel.weight) or panel.weight <= 0:
            raise ValueError('panel weight must be positive')
        categories = list(figure.labels or panel.tick_labels)
        if panel.xkind not in ('auto', 'numeric', 'category', 'datetime'):
            raise ValueError('xkind must be auto, numeric, category or datetime')
        kind = 'category' if categories else panel.xkind if panel.xkind != 'auto' else None
        for trace in panel.traces:
            if trace.kind not in ('line', 'scatter', 'area', 'bar', 'candle', 'ohlc'):
                raise ValueError(f'unsupported series kind: {trace.kind}')
            if trace.axis not in ('left', 'right') or trace.marker not in ('circle', 'cross', 'square', 'diamond'):
                raise ValueError('invalid axis or marker')
            values = np.array(trace.values, dtype=float, copy=True)
            if values.ndim != (2 if trace.kind in ('candle', 'ohlc') else 1) or (values.ndim == 2 and values.shape[1] != 4):
                raise ValueError('series must be 1D; candles require N×4 open/high/low/close rows')
            if figure.labels and len(values) != len(figure.labels):
                raise ValueError('series and shared labels must have equal lengths')
            raw = list(trace.x) if trace.x is not None else list(range(len(values)))
            if len(raw) != len(values):
                raise ValueError('x and y must have equal lengths')
            finite_types = [v for v in raw if not _missing(v)]
            xtype = ('datetime' if finite_types and isinstance(finite_types[0], (date, np.datetime64)) else
                     'category' if finite_types and isinstance(finite_types[0], str) else 'numeric')
            # Explicit datetime axes accept ISO strings and numeric epoch seconds as well.
            if kind == 'datetime' and xtype == 'category':
                xtype = 'datetime'
            # Prepared category/datetime coordinates are numeric on subsequent snapshots.
            if kind and xtype != kind and not (xtype == 'numeric' and (figure.labels or panel.xkind in ('category', 'datetime'))):
                raise ValueError('cannot mix coordinate types on one x axis')
            kind = kind or xtype
            if xtype == 'category':
                for value in raw:
                    if not _missing(value) and not isinstance(value, str):
                        raise ValueError('category coordinates must be strings or missing')
                    if not _missing(value) and value not in categories:
                        categories.append(value)
                x = np.array([np.nan if _missing(v) else categories.index(v) for v in raw], dtype=float)
            elif xtype == 'datetime':
                x = np.array([_time(v) if not _missing(v) else np.nan for v in raw])
            else:
                x = np.array(raw, dtype=float)
            if trace.colors is not None and len(trace.colors) != len(values):
                raise ValueError('per-point colors and values must have equal lengths')
            for value in (trace.width, trace.point_size, trace.line_width):
                if value is not None and (not np.isfinite(value) or value < 0):
                    raise ValueError('series sizes must be finite and nonnegative')
            if trace.baseline is not None and not np.isfinite(trace.baseline):
                raise ValueError('baseline must be finite')
            x.setflags(write=False)
            values.setflags(write=False)
            trace.x, trace.values = x, values
        panel.xkind, panel.tick_labels = kind or 'numeric', tuple(categories)
        if panel.xlog and panel.xkind != 'numeric':
            raise ValueError('log x requires numeric coordinates')
        if panel.independent and (len(figure.panels) != 1 or panel.log or panel.xlog or
                                  any(t.kind not in ('line', 'area') or t.axis != 'left' for t in panel.traces)):
            raise ValueError('independent scales require one panel of linear left-axis curves')
        for guide in panel.guides:
            if not np.isfinite(guide.value):
                raise ValueError('guide must be finite')
    if figure.sharex and figure.panels:
        first = figure.panels[0]
        for panel in figure.panels[1:]:
            if panel.xkind != first.xkind or panel.tick_labels != first.tick_labels or panel.xlog != first.xlog:
                raise ValueError('shared x axes must use the same coordinate type, labels and scale')
        limits = [p.xlimits for p in figure.panels if p.xlimits is not None]
        if limits and any(limit != limits[0] for limit in limits):
            raise ValueError('shared x axes must use the same limits')
        if limits:
            shared = limits[0]
        elif first.xkind == 'category' and first.tick_labels:
            shared = (-.5, len(first.tick_labels)-.5)
        else:
            values = np.concatenate([t.x for p in figure.panels for t in p.traces]) if any(p.traces for p in figure.panels) else np.array([])
            values = values[np.isfinite(values) & ((values > 0) if first.xlog else True)]
            lo, hi = (float(values.min()), float(values.max())) if len(values) else ((1., 10.) if first.xlog else (0., 1.))
            if lo == hi:
                lo, hi = (lo/1.1, hi*1.1) if first.xlog else (lo-.5, hi+.5)
            shared = (lo, hi)
        for panel in figure.panels:
            panel.xlimits = shared
    return figure
