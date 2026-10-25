"""UI-independent requests and provenance for the query/analysis boundary."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

T = TypeVar('T')
Freshness = Literal['fresh', 'stale', 'unavailable']


@dataclass(frozen=True)
class Metadata:
    source: str
    observed_at: float | None = None
    fetched_at: float = 0
    version: str = ''
    freshness: Freshness = 'unavailable'
    cache: str = 'none'
    reason: str = ''


@dataclass(frozen=True)
class Result(Generic[T]):
    data: T
    meta: Metadata
    inputs: tuple[Metadata, ...] = ()


@dataclass(frozen=True)
class HistoryQuery:
    symbol: str
    period: str = '1y'
    interval: str = '1d'


@dataclass(frozen=True)
class SeriesQuery:
    series: str


@dataclass(frozen=True)
class QuotesQuery:
    symbols: tuple[str, ...]


@dataclass(frozen=True)
class ResourceQuery:
    """Provider resource with named operation; no callable supplied by the UI."""
    source: str
    operation: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FactsQuery:
    cik: int
    concept: str | None = None
    start: str | None = None
    end: str | None = None


@dataclass(frozen=True)
class FilingsQuery:
    cik: int
    form: str | None = None
    year: int | None = None


@dataclass(frozen=True)
class NewsQuery:
    symbol: str | None = None
    feeds: tuple[str, ...] = ()
    limit: int = 200


@dataclass(frozen=True)
class SearchQuery:
    domain: Literal['security', 'fred']
    text: str
    filters: dict[str, str] = field(default_factory=dict)
    limit: int = 100


@dataclass(frozen=True)
class AnalysisQuery:
    name: str
    params: dict[str, Any]
    input_versions: tuple[str, ...]
    calculation_version: str


@dataclass
class Quote:
    symbol: str
    price: float | None = None
    prev_close: float | None = None
    volume: float | None = None
    day_low: float | None = None
    day_high: float | None = None
    ts: float | None = None        # time of last trade/bar (epoch)
    fetched: float = 0.0           # when we got it
    source: str = "yf"
    stale: bool = False
    live: bool = False             # came from a stream

    @property
    def change(self):
        return None if self.price is None or self.prev_close is None else self.price - self.prev_close

    @property
    def pct(self):
        c = self.change
        return None if c is None or not self.prev_close else 100 * c / self.prev_close

    @property
    def tag(self) -> str:
        if self.stale:
            return "STALE"
        age = time.time() - self.fetched
        if self.live and age < 30:
            return "LIVE"
        return "STALE" if age > 300 else "DLY"



@dataclass(frozen=True)
class BarsQuery:
    symbol: str
    range: str
    interval: str

@dataclass(frozen=True)
class StoredBarsQuery:
    """Captured observations only, including per-row fetch times; never fetches."""
    symbol: str
    interval: str = '1d'
    start: str | None = None
    end: str | None = None


@dataclass(frozen=True)
class TextQuery:
    source: Literal['sec', 'articles']
    url: str


@dataclass(frozen=True)
class ComparisonQuery:
    references: tuple[str, ...]
    range: str = '1Y'
    interval: str = '1d'
    mode: str = 'NORMALIZED'


Query = (HistoryQuery | SeriesQuery | QuotesQuery | ResourceQuery | FactsQuery | FilingsQuery | NewsQuery |
         SearchQuery | BarsQuery | StoredBarsQuery | TextQuery | ComparisonQuery)
