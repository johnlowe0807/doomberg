"""Article extraction and terminal reader, with explicit browser confirmation."""
from __future__ import annotations

import asyncio
import json
import re
import webbrowser
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Input, Static

from .dialogs import ConfirmScreen
from .fundamentals import Pager, PagerArea


def web_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


from data_layer.normalization import readable_text, extract_article


class BrowserConfirm(ConfirmScreen):
    BINDINGS = [
        Binding("y,Y", "dismiss(True)", show=False, priority=True),
        Binding("enter", "dismiss(False)", show=False, priority=True),
    ]

    def __init__(self, url: str) -> None:
        super().__init__("Open this article in your browser?", "Y  Open browser     N / Esc / Enter  Cancel", url)


class ArticleArea(PagerArea):
    async def _on_key(self, event) -> None:
        if event.character == "o":
            event.stop()
            event.prevent_default()
            self.pager.open_browser()
        else:
            await super()._on_key(event)


class ArticleReader(Pager):
    BINDINGS = [Binding("escape", "dismiss", show=False, priority=True)]

    def __init__(self, item: dict, hub) -> None:
        self.item, self.hub = item, hub
        super().__init__(item["title"], self.content("Loading article…"))

    def content(self, body: str) -> str:
        stamp = self.item.get("ts")
        date = stamp.strftime("%Y-%m-%d %H:%M %Z") if stamp else ""
        return "\n".join(filter(None, [self.item["title"], self.item.get("src"), date,
                                        self.item.get("url")])) + "\n\n" + body

    def compose(self) -> ComposeResult:
        yield Static(self.ptitle, id="phdr", markup=False)
        yield ArticleArea(self.text, self)
        yield Input(placeholder="search…", id="psearch")
        yield Static("j/k scroll  space/b page  g/G top/bottom  / search  n/N next/prev  o browser (confirm)  q/Esc close",
                     id="phint", markup=False)

    def on_mount(self) -> None:
        super().on_mount()
        self.run_worker(self.load_article(), exclusive=True)

    async def load_article(self) -> None:
        url = self.item.get("url", "")
        body = ""
        try:
            if web_url(url):
                from data_layer.query_models import TextQuery
                result = await self.hub.query(TextQuery('articles', url))
                body = result.data or ''
            if not body:
                summary = await asyncio.to_thread(readable_text, self.item.get("summary") or "")
                body = "Article text is unavailable in the terminal."
                if summary:
                    body += "\n\nFeed summary:\n\n" + summary
                body += "\n\nPress o to request opening the original in your browser." if web_url(url) else "\n\nNo article link is available."
        except Exception as error:
            self.hub.warn(f"Article reader: {type(error).__name__}")
            body = "Could not load this article. Press o to request opening it in your browser."
        if self.is_mounted:
            self.text = self.content(body)
            self.lines = self.text.splitlines()
            self.query_one(PagerArea).load_text(self.text)
            self.update_hdr()

    def open_browser(self) -> None:
        url = self.item.get("url", "")
        if not web_url(url):
            self.hub.warn("No valid article link is available")
            return
        app = self.app

        def confirmed(accepted: bool | None) -> None:
            if accepted:
                app.run_worker(asyncio.to_thread(webbrowser.open, url))

        app.push_screen(BrowserConfirm(url), confirmed)
