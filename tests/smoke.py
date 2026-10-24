"""Headless smoke test: opens every function against live free data and reports rows/warnings.
Run: FREETERM_HOME=/tmp/ftsmoke .venv/bin/python -W ignore tests/smoke.py [CMD ...]"""
import asyncio
import logging
import sys

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
from freeterm.app import FreeTerm, register  # noqa: E402

register()
CMDS = sys.argv[1:] or ["AAPL", "AAPL /GIP", "/COMP SPY QQQ", "/WEI", "/CMDTY", "/CRYPTO", "/MOST", "/FXC", "/GC", "/BTMM",
                        "/AUCT", "/SPRD", "/FRED DGS10", "AAPL /DES", "AAPL /HP", "AAPL /CN", "/TOP", "AAPL /OMON",
                        "AAPL /FA", "AAPL /FIL", "AAPL /ERN", "/EVTS", "/? apple", "ZZZZQ", "/IEX", "/MON", "/HOME"]


async def main():
    app = FreeTerm()
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause(4)
        for c in CMDS:
            app.hub.warnings.clear()
            await app.run_command_async(c)
            await pilot.pause(7)
            v = app.active_pane().view
            n = sum(t.row_count for t in v.query("DataTable"))
            info = v.query("#info")
            extra = str(info.first().render())[:90] if info else ""
            print(f"R {c:<14} -> {v.title:<34} rows {n:<4} {extra} {app.hub.warnings[-2:]}")
        print("R status:", app.hub.status())


asyncio.run(main())
