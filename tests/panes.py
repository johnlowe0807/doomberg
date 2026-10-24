"""Headless pane/focus test: typing from a pane keeps the first letter, Tab cycles panes (not widgets),
commands open in the active pane, Tab in the command line autocompletes, `AAPL AMZN` compares.
Run: FREETERM_HOME=/tmp/ftsmoke .venv/bin/python -u -W ignore tests/panes.py"""
import asyncio
import logging

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
from freeterm.app import FreeTerm, Pane, register  # noqa: E402

register()


def where(app):
    f = app.focused
    node = f
    while node is not None and not isinstance(node, Pane):
        node = node.parent
    idx = app.panes.index(node) if node is not None else None
    return f"{type(f).__name__}@pane{idx} active={app.panes.index(app.active_pane())}"


async def main():
    app = FreeTerm()
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause(3)
        await app.run_command_async("/LAYOUT 4")
        await pilot.pause(3)
        app.panes[0].focus_view()
        await pilot.pause(0.3)
        seq = []
        for _ in range(5):
            await pilot.press("tab")
            await pilot.pause(0.3)
            seq.append(app.panes.index(app.active_pane()))
        print("R tab cycle (expect 1 2 3 0 1):", seq)
        await pilot.press("shift+tab")
        await pilot.pause(0.3)
        print("R shift+tab (expect 0):", app.panes.index(app.active_pane()))
        await pilot.press("tab", "tab")
        await pilot.pause(0.3)
        print("R now on pane:", app.panes.index(app.active_pane()))
        await pilot.press(*"AAPL AMZN", "enter")
        await pilot.pause(7)
        titles = [p.view.title if p.view else "" for p in app.panes]
        print("R typed 'AAPL AMZN' from pane 2 ->", titles, app.hub.warnings[-1:])
        print("R focus after command:", where(app))
        await pilot.press("/")
        await pilot.press(*"WE", "tab")
        await pilot.pause(0.3)
        print("R '/WE'+Tab autocompletes:", repr(app.query_one("#cmd").value))
        await pilot.press("enter")
        await pilot.pause(5)
        print("R WEI opened in pane", [i for i, p in enumerate(app.panes) if p.view and p.view.FUNC == "WEI"])
        await pilot.press("tab")
        await pilot.pause(0.3)
        print("R tab from pane after cmd:", where(app))
        await app.run_command_async("/LAYOUT 2")
        await pilot.pause(2)
        seq = []
        for _ in range(3):
            await pilot.press("tab")
            await pilot.pause(0.3)
            seq.append(app.panes.index(app.active_pane()))
        print("R 2-pane tab cycle:", seq)
        await app.run_command_async("AAPL")
        await pilot.pause(5)
        v = app.active_pane().view
        await pilot.press("t")
        await pilot.pause(0.5)
        print("R 't' on chart in 2-pane:", v.ctype, app.hub.warnings[-1:])


asyncio.run(main())
