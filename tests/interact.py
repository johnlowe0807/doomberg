"""Headless interaction test: FA keys/year jump → FIL → pager (G, search, n, TOC), help overlay, OMON expiry, ERN 8-K.
Run: FREETERM_HOME=/tmp/ftsmoke .venv/bin/python -u -W ignore tests/interact.py"""
import asyncio
import logging

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
from freeterm.app import FreeTerm, register  # noqa: E402

register()


def hdr(scr, sel):
    return str(scr.query_one(sel).render())[:120]


async def main():
    app = FreeTerm()
    async with app.run_test(size=(180, 50)) as pilot:
        await pilot.pause(3)
        await app.run_command_async("AAPL /FA")
        await pilot.pause(6)
        v = app.active_pane().view
        print("R FA:", hdr(v, "#fahdr"))
        await pilot.press("t"); await pilot.pause(1)
        print("R FA qtr:", hdr(v, "#fahdr"))
        await pilot.press("b"); await pilot.pause(1)
        print("R FA bal:", hdr(v, "#fahdr"))
        await pilot.press("1", "9", "9", "8"); await pilot.pause(1)
        print("R FA 1998:", hdr(v, "#fahdr"))
        await pilot.press("f"); await pilot.pause(8)
        v = app.active_pane().view
        t = v.query_one("#fil")
        print("R FIL:", v.title, hdr(v, "#filhdr"), "row", t.get_row_at(t.cursor_row)[:2])
        await pilot.press("enter"); await pilot.pause(15)
        scr = app.screen
        if type(scr).__name__ == "Pager":
            print("R pager:", hdr(scr, "#phdr"))
            await pilot.press("G"); await pilot.pause(0.5)
            print("R G:", hdr(scr, "#phdr"))
            await pilot.press("g", "/"); await pilot.pause(0.3)
            await pilot.press(*"net sales", "enter"); await pilot.pause(0.5)
            print("R search:", hdr(scr, "#phdr"))
            await pilot.press("n"); await pilot.pause(0.3)
            print("R n:", hdr(scr, "#phdr"))
            await pilot.press("t"); await pilot.pause(0.5)
            print("R toc:", type(app.screen).__name__)
            if type(app.screen).__name__ == "TocScreen":
                await pilot.press("down", "down", "enter"); await pilot.pause(0.5)
            print("R toc jump:", hdr(scr, "#phdr"))
            await pilot.press("q"); await pilot.pause(0.5)
        else:
            print("R pager FAILED:", app.hub.warnings[-3:])
        print("R after close:", type(app.screen).__name__, app.active_pane().view.title)
        await pilot.press("question_mark"); await pilot.pause(0.5)
        print("R help:", type(app.screen).__name__)
        await pilot.press("escape"); await pilot.pause(0.3)
        await app.run_command_async("AAPL /OMON"); await pilot.pause(6)
        await pilot.press("]"); await pilot.pause(6)
        print("R OMON next:", hdr(app.active_pane().view, "#hdr"))
        await app.run_command_async("AAPL /ERN"); await pilot.pause(7)
        v = app.active_pane().view
        v.query_one("#k8").focus()
        await pilot.press("enter"); await pilot.pause(12)
        if type(app.screen).__name__ == "Pager":
            print("R ERN 8-K:", hdr(app.screen, "#phdr"))
            print("R text:", app.screen.text[:260].replace("\n", " | "))
        else:
            print("R ERN 8-K FAILED:", app.hub.warnings[-3:])


asyncio.run(main())
