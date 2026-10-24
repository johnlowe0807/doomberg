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


def readable_text(raw: str) -> str:
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup.select("script, style, nav, aside, footer, form, button, noscript, [hidden], [aria-hidden='true']"):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "blockquote", "pre"]):
        tag.append("\n\n")
    text = re.sub(r"[ \t\xa0]+", " ", soup.get_text())
    text = "\n".join(line.strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_article(raw: str) -> str:
    """Prefer article containers and structured article bodies, never whole-page chrome."""
    soup = BeautifulSoup(raw, "lxml")
    for selector in (".caas-body", '[data-testid="article-body"]', '[itemprop="articleBody"]',
                     ".article-body", ".article-content", ".entry-content"):
        node = soup.select_one(selector)
        if node:
            text = readable_text(str(node))
            if len(text) >= 120:
                return text

    def bodies(value):
        if isinstance(value, list):
            for item in value:
                yield from bodies(item)
        elif isinstance(value, dict):
            # Don't use embedded paid article bodies to get around publisher restrictions.
            if str(value.get("isAccessibleForFree", True)).lower() == "false":
                return
            body = value.get("articleBody")
            if isinstance(body, str):
                yield body
            for key in ("@graph", "mainEntity"):
                yield from bodies(value.get(key))

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            for body in bodies(json.loads(script.string or script.get_text())):
                text = readable_text(body)
                if len(text) >= 120:
                    return text
        except (ValueError, TypeError):
            continue
    node = soup.find("article")
    if node:
        blocks = node.select("p, h2, h3, h4, li, blockquote, pre")
        block_ids = {id(block) for block in blocks}
        blocks = [b for b in blocks if not b.find_parent(["nav", "aside", "footer", "header", "form"])
                  and not any(id(parent) in block_ids for parent in b.parents)]
        text = readable_text("\n".join(str(b) for b in blocks))
        if len(text) >= 120:
            return text
    return ""


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
                body = self.hub.mem.get("article-text:" + url) or ""
                if not body:
                    raw = await self.hub.http("articles", url, 1, {"User-Agent": "Mozilla/5.0 freeterm"},
                                              ttl=3600, as_json=False, retries=0)
                    body = await asyncio.to_thread(extract_article, raw) if raw else ""
                    if body:
                        self.hub.mem.put("article-text:" + url, body, 3600)
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
