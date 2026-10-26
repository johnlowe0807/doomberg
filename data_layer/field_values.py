"""Canonical scalar values at provider boundaries, independent of clients/storage.

Dates are ISO calendar dates; timestamps are fixed-precision, UTC ISO instants.
Epoch inputs are seconds unless an adapter explicitly supplies a different unit.
Naive timestamps require the adapter to declare their timezone. Unknowns stay null.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from numbers import Real

import pandas as pd
import numpy as np

VERSION = 'fields-v1'
TYPE_ALIASES = {
    'STOCK': 'EQUITY', 'STOCKS': 'EQUITY', 'EQUITIES': 'EQUITY',
    'FUTURES': 'FUTURE', 'COMMODITY': 'FUTURE', 'COMMODITIES': 'FUTURE',
    'CRYPTO': 'CRYPTOCURRENCY', 'CRYPTOCURRENCIES': 'CRYPTOCURRENCY',
    'FX': 'CURRENCY', 'FOREX': 'CURRENCY', 'CURRENCIES': 'CURRENCY',
    'INDICES': 'INDEX', 'INDEXES': 'INDEX', 'ETFS': 'ETF',
    'MUTUALFUNDS': 'MUTUALFUND', 'MUTUAL FUND': 'MUTUALFUND', 'OPTIONS': 'OPTION',
}


def raw(value):
    return value.get('raw') if isinstance(value, dict) else value


def text(value):
    value = raw(value)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return None if value.casefold() in ('', 'n/a', 'nan', 'nat', 'none', 'null', '--', '-') else value


def number(value, scale=1):
    value = raw(value)
    if isinstance(value, (bool, np.bool_, date)):
        return None
    try:
        value = float(value) * scale
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _stamp(value, epoch_unit='s'):
    value = raw(value)
    if value is None or isinstance(value, (bool, np.bool_)):
        return None
    try:
        stamp = (pd.Timestamp(value, unit=epoch_unit, tz='UTC') if isinstance(value, Real)
                 else pd.Timestamp(value))
        return None if pd.isna(stamp) or not 1 <= stamp.year <= 9999 else stamp
    except (ValueError, TypeError, OverflowError):
        return None


def calendar_date(value, *, epoch_unit='s'):
    stamp = _stamp(value, epoch_unit)
    return stamp.date().isoformat() if stamp is not None else None


def timestamp(value, *, assume_timezone=None, epoch_unit='s'):
    value = raw(value)
    # A calendar date is not an observation at midnight.
    if isinstance(value, date) and not isinstance(value, datetime):
        return None
    if isinstance(value, str) and len(value.strip()) == 10:
        return None
    stamp = _stamp(value, epoch_unit)
    if stamp is None:
        return None
    try:
        if stamp.tzinfo is None:
            if assume_timezone is None:
                return None
            stamp = stamp.tz_localize(assume_timezone, ambiguous='raise', nonexistent='raise')
        return stamp.tz_convert('UTC').isoformat(timespec='microseconds')
    except (ValueError, TypeError):
        return None


def security_type(value):
    value = text(value)
    return TYPE_ALIASES.get(value.upper(), value.upper()) if value else None


def boolean(value):
    value = raw(value)
    return bool(value) if isinstance(value, (bool, np.bool_)) else None


def currency(value):
    value = text(value)
    if not value:
        return None
    # Quoted pence/cents must never become pounds/rand by changing case.
    return value if value in ('GBp', 'ZAc') else {'GBX': 'GBp', 'ZAX': 'ZAc'}.get(value.upper(), value.upper())


def source(value):
    value = text(value)
    if not value:
        return None
    return {'yahoo': 'yf', 'yfinance': 'yf', 'edgar': 'sec', 'coinbase exchange': 'coinbase'}.get(value.casefold(), value.casefold())
