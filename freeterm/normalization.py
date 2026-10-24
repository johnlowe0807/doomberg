"""Provider-specific normalization. No UI, storage or network dependencies."""
from __future__ import annotations
import re
import io
import calendar
import json
from datetime import datetime, timezone
import pandas as pd
from bs4 import BeautifulSoup

def norm_yf_news(items) -> list[dict]:
    """yfinance has shipped two news shapes: flat dicts and {'content': {...}}. Handle both."""
    out = []
    for it in items or []:
        c = it.get("content") or it
        url = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url")
               or c.get("link") or "")
        t = c.get("pubDate") or c.get("displayTime") or c.get("providerPublishTime")
        try:
            ts = datetime.fromtimestamp(t, timezone.utc) if isinstance(t, (int, float)) else pd.Timestamp(t).to_pydatetime()
            if pd.isna(ts):
                ts = None
            elif ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            ts = None
        src = (c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo"
        if c.get("title"):
            out.append({"title": c["title"], "src": src, "ts": ts, "url": url,
                        "summary": c.get("summary") or c.get("description") or "", "provider": "yf"})
    return out



def html_to_text(raw: str) -> str:
    from bs4 import BeautifulSoup
    raw = re.sub(r"<TEXT>\s*begin \d{3} .*?end\s*</TEXT>", "<TEXT>[binary attachment omitted]</TEXT>", raw, flags=re.S)
    if not re.search(r"<(html|body|div|p|table)\b", raw[:200000], re.I):
        return raw
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup.find_all(["script", "style", "ix:header", "head"]):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display:\s*none", re.I)):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for td in soup.find_all(["td", "th"]):
        td.append("  ")
    for tag in soup.find_all(["p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "document"]):
        tag.append("\n")
    lines = [re.sub(r"[ \t\xa0​]+", " ", l).strip() for l in soup.get_text("").splitlines()]
    out, blank = [], 0
    for l in lines:
        blank = blank + 1 if not l else 0
        if blank <= 1:
            out.append(l)
    return "\n".join(out).strip()



def parse_page(html):
    soup = BeautifulSoup(html, 'lxml')
    rows = []
    for block in soup.select('.search-series'):
        title = block.select_one('a.series-title[href*="/series/"]')
        if title is None:
            continue
        sid = title['href'].split('/series/')[-1].split('?')[0]
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', sid):
            continue
        meta = block.select_one('.search-result-meta')
        metadata = meta.get_text(' ', strip=True) if meta else ''
        found = re.fullmatch(r'(.*?),\s*((?:Daily|Weekly|Biweekly|Monthly|Quarterly|Annual|Semiannual)\b.*),\s*([^,]+)', metadata, re.I)
        units, frequency, adjustment = found.groups() if found else ('N/A', 'N/A', 'N/A')
        dates = block.select_one('.search-result-meta-dates')
        date_text = dates.get_text(' ', strip=True) if dates else ''
        period = re.match(r'(.+?)\s+to\s+(.+?)(?:\s*\(.*\))?$', date_text)
        start, end = period.groups() if period else ('N/A', 'N/A')
        rows.append((sid.upper(), title.get_text(' ', strip=True), frequency.strip(), units.strip(),
                     adjustment.strip(), start.strip(), end.strip()))
    pages = [int(m.group(1)) for a in soup.select('a[href]')
             if (m := re.search(r'[?&]pageID=(\d+)', a['href']))]
    return rows, max(pages, default=1)



def parse_description(html):
    soup = BeautifulSoup(html, 'lxml')
    def text(selector):
        node = soup.select_one(selector)
        return node.get_text(' ', strip=True) if node else ''
    fields = {'Title': text('h1'), 'Units': text('.series-meta-value-units'),
              'Frequency': text('.series-meta-value-frequency'),
              'Updated': ' '.join(filter(None, (text('.series-meta-updated-date'), text('.series-meta-updated-time')))),
              'Source': text('.series-source'), 'Release': text('.series-release')}
    notes = soup.select_one('#notes-content')
    description = []
    if notes:
        for paragraph in notes.find_all('p', recursive=False):
            if paragraph.find('strong') or paragraph.find_parent(class_='series-citation'):
                continue
            value = paragraph.get_text(' ', strip=True)
            if value:
                description.append(value)
        units = next((p for p in notes.select('p') if p.find('strong') and
                      p.find('strong').get_text(strip=True) == 'Units:'), None)
        if units:
            value = units.get_text(' ', strip=True).removeprefix('Units:').strip()
            fields['Adjustment'] = value.split(',', 1)[-1].strip() if ',' in value else ''
    return fields, '\n\n'.join(description)




def fred_series(raw, sid):
    df = pd.read_csv(io.StringIO(raw), na_values='.')
    values = pd.Series(pd.to_numeric(df.iloc[:, 1], errors='coerce').values,
                       index=pd.to_datetime(df.iloc[:, 0]), name=sid)
    return values.dropna().sort_index()


def rss_items(raw, url):
    import feedparser
    feed = feedparser.parse(raw)
    name = (feed.feed.get('title') or url.split('/')[2])[:18]
    out = []
    for item in feed.entries:
        stamp = item.get('published_parsed') or item.get('updated_parsed')
        ts = datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc) if stamp else None
        out.append(dict(title=item.get('title', ''), src=name, ts=ts, url=item.get('link', ''), summary=item.get('summary', ''), provider='rss:' + url))
    return out


def commodity_snapshot(frame, symbols, date):
    import time
    from dataclasses import asdict
    from .ingestion import _sub
    from .query_models import Quote
    today = pd.Timestamp(date).date()
    snapshot = {'quotes': {}, 'volumes': {}, 'rank_date': date}
    for symbol in symbols:
        bars = _sub(frame, symbol)
        if bars is None or 'Close' not in bars:
            continue
        bars = bars.dropna(subset=['Close'])
        if bars.empty:
            continue
        last = bars.iloc[-1]
        number = lambda v: float(v) if pd.notna(v) else None
        q = Quote(symbol, price=number(last.Close), prev_close=number(bars.iloc[-2].Close) if len(bars) > 1 else None,
                  volume=number(last.get('Volume')), day_low=number(last.get('Low')), day_high=number(last.get('High')),
                  ts=bars.index[-1].timestamp(), fetched=time.time())
        snapshot['quotes'][symbol] = asdict(q)
        prior = bars[bars.index.date < today]
        if not prior.empty and 'Volume' in prior:
            snapshot['volumes'][symbol] = {'volume': number(prior.iloc[-1].Volume), 'date': str(prior.index[-1].date())}
    return snapshot if snapshot['quotes'] else None


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
