"""Headless test: chart opens with 4 base downloads, then flipping every range/interval makes zero new requests.
Also checks the pane title and range bar follow the selection.
Run: FREETERM_HOME=/tmp/ftsmoke .venv/bin/python -u -W ignore tests/ranges.py [SYMBOL]"""
import asyncio
import logging
import sys
import time

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
from freeterm.app import FreeTerm, register  # noqa: E402
from freeterm.data import IVS, RANGES, YFProvider  # noqa: E402

register()
SYM = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
calls = []
_orig = YFProvider.history


async def counting_history(self, symbol, period="1y", interval="1d"):
    calls.append((symbol, period, interval))
    return await _orig(self, symbol, period, interval)


YFProvider.history = counting_history


async def main():
    app = FreeTerm()
    async with app.run_test(size=(160, 46)) as pilot:
        await pilot.pause(3)
        calls.clear()
        await app.run_command_async(SYM)
        await pilot.pause(10)  # first paint + background prefetch
        v = app.active_pane().view
        print(f"R opened {SYM}: {len(calls)} history requests {calls}")
        print("R title:", app.active_pane().query_one('.title').render(), "| rangebar:", str(v.query_one('#rangebar').render()))
        calls.clear()
        t0 = time.time()
        combos = 0
        for i, r in enumerate(RANGES, 1):
            await pilot.press(str(i))
            await pilot.pause(0.4)
            for _ in IVS:
                await pilot.press("[")
                await pilot.pause(0.15)
            seen = []
            for _ in IVS:
                seen.append(v.iv)
                await pilot.press("]")
                await pilot.pause(0.25)
                combos += 1
            ok = sorted(set(seen), key=IVS.index)
            print(f"R {r:<4} intervals reachable: {ok}  bars={len(v.df) if v.df is not None else 0}  "
                  f"title={str(app.active_pane().query_one('.title').render())!r}")
        print(f"R flipped {combos} range/interval steps in {time.time() - t0:.1f}s with {len(calls)} new history requests {calls}")
        print("R warnings:", app.hub.warnings[-2:])


asyncio.run(main())
