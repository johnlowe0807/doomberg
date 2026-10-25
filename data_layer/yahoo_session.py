"""Bounded recovery for Yahoo's shared anonymous cookie/crumb session.

Run in ingestion workers. yfinance already retries its two cookie strategies;
only a final 401 gets one clean session attempt, shared across resource calls.
"""
from __future__ import annotations

import re
import threading
import time


class YahooSessionError(RuntimeError):
    pass


def failure_status(error):
    response = getattr(error, 'response', None)
    status = getattr(response, 'status_code', None)
    if status in (401, 403, 429):
        return status
    if type(error).__name__ == 'YFRateLimitError':
        return 429
    # Some yfinance operations replace the HTTP exception with a ValueError.
    match = re.search(r'\b(401|403|429)\b', str(error))
    return int(match[1]) if match else None


class YahooSession:
    def __init__(self):
        self._lock = threading.RLock()
        self.retry_after = 0.0
        self.last_recovery = float('-inf')
        self.status = None
        self.owns_cookie_cache = False

    def check(self):
        remaining = self.retry_after - time.monotonic()
        if remaining > 0:
            raise YahooSessionError(f'Yahoo {self.status}; retry in {remaining:.0f}s (cached data retained)')

    def _reset(self, *, cookies):
        # Isolate SDK internals here; do not edit yfinance or log cookie/crumb values.
        from yfinance import cache
        from yfinance._http import cookie_jar
        from yfinance.data import YfData
        data = YfData()
        with data._cookie_lock:
            jar = cookie_jar(data._session)
            logged_in = getattr(data, '_logged_in', False) or any(c.name in ('T', 'Y') for c in jar)
            data._crumb = None
            if cookies and not logged_in:
                if self.owns_cookie_cache:
                    cache.get_cookie_cache().store('curlCffi', None)
                jar.clear()
                data._cookie = None
                data._cookie_strategy = 'basic'
        data.cache_get.cache_clear()

    def _cooldown(self, status):
        self.status = status
        self.retry_after = time.monotonic() + (300 if status == 429 else 60)

    def run(self, fetch):
        # Prevent a burst of financial enrichments from each resetting the singleton.
        with self._lock:
            self.check()
            try:
                return fetch()
            except Exception as error:
                status = failure_status(error)
                if status is None:
                    raise
                if status == 401 and time.monotonic() - self.last_recovery >= 60:
                    self.last_recovery = time.monotonic()
                    try:
                        self._reset(cookies=True)
                        return fetch()
                    except Exception as retry_error:
                        status = failure_status(retry_error)
                        if status is None:
                            raise
                # A 429 can leave an error page cached as the SDK crumb. Clear only
                # that state, then wait; never renew cookies to evade a rate limit.
                self._cooldown(status)
                if status == 429:
                    self._reset(cookies=False)
                raise YahooSessionError(f'Yahoo {status}; requests cooling down for '
                                        f'{300 if status == 429 else 60}s (cached data retained)') from None
