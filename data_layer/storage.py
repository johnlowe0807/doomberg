"""Transactional local repositories. SQLite I/O is called only in worker threads.

Observations are durable domain records, independent of the disposable response and
analysis caches. Dataset versions hash normalized content, not wall-clock fetch time.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from .query_models import Metadata
from .serialization import key, pack, unpack

SCHEMA_VERSION = 3


class Repository:
    def __init__(self, path: Path | str):
        self.path = path
        self.lock = threading.RLock()
        self.db = None

    def initialize(self):
        with self.lock:
            if self.db is not None:
                return
            if str(self.path) != ':memory:':
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(self.path), timeout=20, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA foreign_keys=ON')
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2, SCHEMA_VERSION):
                raise RuntimeError(f'Unsupported data schema {version}')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS datasets (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, source TEXT NOT NULL,
                    observed REAL, fetched REAL NOT NULL, version TEXT NOT NULL,
                    header BLOB, accessed REAL NOT NULL, size INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS dataset_kind ON datasets(kind, accessed);
                CREATE TABLE IF NOT EXISTS bars (
                    dataset TEXT REFERENCES datasets(id) ON DELETE CASCADE,
                    stamp INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL, bad_volume INTEGER, fetched REAL,
                    PRIMARY KEY(dataset, stamp)) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS series (
                    dataset TEXT REFERENCES datasets(id) ON DELETE CASCADE,
                    stamp INTEGER, value REAL, fetched REAL, PRIMARY KEY(dataset, stamp)) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS series_catalog (
                    dataset TEXT PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
                    symbol TEXT NOT NULL, interval TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS series_lookup ON series_catalog(symbol, interval);
                CREATE TABLE IF NOT EXISTS quotes (
                    dataset TEXT PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
                    symbol TEXT, price REAL, previous REAL, volume REAL, low REAL, high REAL,
                    observed REAL, fetched REAL, source TEXT, live INTEGER);
                CREATE TABLE IF NOT EXISTS documents (
                    dataset TEXT PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
                    content BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS facts (
                    dataset TEXT REFERENCES datasets(id) ON DELETE CASCADE,
                    ordinal INTEGER, taxonomy TEXT, concept TEXT, unit TEXT, start TEXT, end TEXT,
                    filed TEXT, value REAL, record BLOB, PRIMARY KEY(dataset, ordinal)) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS fact_lookup ON facts(dataset, concept, end);
                CREATE TABLE IF NOT EXISTS filings (
                    dataset TEXT REFERENCES datasets(id) ON DELETE CASCADE,
                    accession TEXT, date TEXT, form TEXT, document TEXT, description TEXT, items TEXT, period TEXT,
                    PRIMARY KEY(dataset, accession)) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS filing_lookup ON filings(dataset, form, date);
                CREATE TABLE IF NOT EXISTS news (
                    id TEXT PRIMARY KEY, title TEXT, publisher TEXT, published REAL, url TEXT, summary TEXT,
                    body TEXT DEFAULT '', source TEXT NOT NULL, fetched REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS news_time ON news(published);
                CREATE TABLE IF NOT EXISTS news_topics (
                    dataset TEXT REFERENCES datasets(id) ON DELETE CASCADE,
                    article TEXT REFERENCES news(id) ON DELETE CASCADE,
                    PRIMARY KEY(dataset, article)) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS search_documents (
                    id INTEGER PRIMARY KEY, domain TEXT, symbol TEXT, name TEXT, kind TEXT, source TEXT,
                    row BLOB NOT NULL, fetched REAL NOT NULL, UNIQUE(domain, symbol, source));
                CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(symbol, name, kind, tokenize='unicode61');
                CREATE TABLE IF NOT EXISTS discovery (
                    domain TEXT, query TEXT, document INTEGER REFERENCES search_documents(id) ON DELETE CASCADE,
                    PRIMARY KEY(domain, query, document)) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS discovery_runs (
                    domain TEXT, query TEXT, fetched REAL, PRIMARY KEY(domain, query)) WITHOUT ROWID;
            ''')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS fundamental_metrics (
                    dataset TEXT REFERENCES datasets(id) ON DELETE CASCADE, ordinal INTEGER,
                    symbol TEXT, metric TEXT, value REAL, unit TEXT, period TEXT, period_end TEXT,
                    filed TEXT, record BLOB NOT NULL, PRIMARY KEY(dataset, ordinal)) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS metrics_lookup ON fundamental_metrics(symbol, metric, period_end);
                CREATE TABLE IF NOT EXISTS analyst_estimates (
                    dataset TEXT REFERENCES datasets(id) ON DELETE CASCADE, ordinal INTEGER,
                    symbol TEXT, kind TEXT, period TEXT, period_end TEXT, record BLOB NOT NULL,
                    PRIMARY KEY(dataset, ordinal)) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS estimates_lookup ON analyst_estimates(symbol, period);
                CREATE TABLE IF NOT EXISTS fund_holdings (
                    dataset TEXT REFERENCES datasets(id) ON DELETE CASCADE, ordinal INTEGER,
                    fund TEXT, symbol TEXT, kind TEXT, weight REAL, report_date TEXT, record BLOB NOT NULL,
                    PRIMARY KEY(dataset, ordinal)) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS holdings_lookup ON fund_holdings(fund, symbol, report_date);
            ''')
            for table in ('bars', 'series'):
                if 'fetched' not in {r[1] for r in self.db.execute(f'PRAGMA table_info({table})')}:
                    self.db.execute(f'ALTER TABLE {table} ADD COLUMN fetched REAL')
                    self.db.execute(f'UPDATE {table} SET fetched=(SELECT fetched FROM datasets WHERE id=dataset)')
            self.db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
            self.db.commit()

    def get(self, identity, **filters):
        with self.lock, self.db:
            row = self.db.execute('SELECT * FROM datasets WHERE id=?', (identity,)).fetchone()
            if row is None:
                return None
            self.db.execute('UPDATE datasets SET accessed=? WHERE id=?', (time.time(), identity))
            kind, header = row['kind'], unpack(row['header']) if row['header'] else None
            if kind in ('fundamental_metrics', 'analyst_estimates', 'fund_holdings'):
                data = [unpack(r[0]) for r in self.db.execute(f'SELECT record FROM {kind} WHERE dataset=? ORDER BY ordinal', (identity,))]
            elif kind in ('bars', 'series'):
                values = self.db.execute(f'SELECT * FROM {kind} WHERE dataset=? ORDER BY stamp', (identity,)).fetchall()
                index = pd.to_datetime([v['stamp'] for v in values], utc=bool(header['tz']))
                if header['tz']:
                    index = index.tz_convert(header['tz'])
                index = pd.DatetimeIndex(index, name=header.get('index_name'), freq=header.get('freq')).as_unit(header.get('unit', 'ns'))
                if kind == 'bars':
                    data = pd.DataFrame([[v[c] for c in ('open', 'high', 'low', 'close', 'volume', 'bad_volume')]
                                         for v in values], index=index,
                                        columns=['Open', 'High', 'Low', 'Close', 'Volume', 'BadVol'])
                    data['BadVol'] = data.BadVol.astype(bool)
                    for column, dtype in header.get('dtypes', {}).items():
                        data[column] = data[column].astype(dtype)
                else:
                    data = pd.Series([v['value'] for v in values], index=index, name=header['name'], dtype=float)
            elif kind == 'quote':
                q = self.db.execute('SELECT * FROM quotes WHERE dataset=?', (identity,)).fetchone()
                data = dict(symbol=q['symbol'], price=q['price'], prev_close=q['previous'], volume=q['volume'],
                            day_low=q['low'], day_high=q['high'], ts=q['observed'], fetched=q['fetched'],
                            source=q['source'], live=bool(q['live']))
            elif kind == 'facts':
                data = header
                data['facts'] = {tax: {concept: dict(meta, units={}) for concept, meta in concepts.items()}
                                 for tax, concepts in header['facts'].items()}
                sql, args = 'SELECT * FROM facts WHERE dataset=?', [identity]
                for field, op in (('concept', '='), ('start', '>='), ('end', '<=')):
                    if filters.get(field):
                        sql += f' AND {field}{op}?'
                        args.append(filters[field])
                for fact in self.db.execute(sql + ' ORDER BY ordinal', args):
                    units = data['facts'][fact['taxonomy']][fact['concept']]['units']
                    units.setdefault(fact['unit'], []).append(unpack(fact['record']))
            elif kind == 'filings':
                sql, args = 'SELECT * FROM filings WHERE dataset=?', [identity]
                if filters.get('form'):
                    sql += ' AND form=?'; args.append(filters['form'])
                if filters.get('year'):
                    sql += ' AND date LIKE ?'; args.append(str(filters['year']) + '%')
                data = [dict(acc=f['accession'], date=f['date'], form=f['form'], doc=f['document'],
                             desc=f['description'], items=f['items'], period=f['period'])
                        for f in self.db.execute(sql + ' ORDER BY date DESC, accession DESC', args)]
            elif kind == 'news':
                from datetime import datetime, timezone
                data = [dict(title=n['title'], src=n['publisher'],
                             ts=datetime.fromtimestamp(n['published'], timezone.utc) if n['published'] else None,
                             url=n['url'], summary=n['summary'], provider=n['source']) for n in self.db.execute(
                    'SELECT n.* FROM news n JOIN news_topics t ON t.article=n.id WHERE t.dataset=? '
                    'ORDER BY n.published DESC LIMIT ?', (identity, filters.get('limit', 200)))]
            else:
                content = self.db.execute('SELECT content FROM documents WHERE dataset=?', (identity,)).fetchone()
                data = unpack(content[0])
            return data, Metadata(row['source'], row['observed'], row['fetched'], row['version'], 'fresh', 'store')

    def put(self, identity, kind, source, data, fetched, observed=None):
        """Atomically publish a complete normalized dataset and its content version."""
        incoming = None
        previous_fetches = {}
        catalog = None
        if kind in ('bars', 'series'):
            incoming = set(map(int, data.index.as_unit('ns').asi8))
            with self.lock:
                old = self.get(identity)
                if old is not None:
                    previous_fetches = {r[0]: r[1] for r in self.db.execute(f'SELECT stamp,fetched FROM {kind} WHERE dataset=?', (identity,))}
                    catalog = self.db.execute('SELECT symbol,interval FROM series_catalog WHERE dataset=?', (identity,)).fetchone()
                    combined = pd.concat([old[0], data])
                    combined = combined[~combined.index.duplicated(keep='last')].sort_index()
                    # Preserve exact source index metadata when the observation set is unchanged.
                    if combined.index.equals(data.index):
                        combined.index = data.index
                    data = combined.iloc[-100000:]
        version = key(data)
        header = None
        if kind in ('bars', 'series'):
            header = dict(tz=str(data.index.tz) if data.index.tz else None, index_name=data.index.name,
                          name=data.name if kind == 'series' else None, unit=data.index.unit, freq=data.index.freqstr,
                          dtypes={c: str(t) for c, t in data.dtypes.items()} if kind == 'bars' else {})
            observed = data.index[-1].timestamp() if len(data) else None
        elif kind == 'facts':
            header = {**data, 'facts': {tax: {c: {k: v for k, v in item.items() if k != 'units'}
                                            for c, item in concepts.items()} for tax, concepts in data.get('facts', {}).items()}}
        size = len(pack(data))
        with self.lock, self.db:
            previous = self.db.execute('SELECT fetched FROM datasets WHERE id=?', (identity,)).fetchone()
            if previous and previous[0] > fetched:
                return self.get(identity)
            self.db.execute('DELETE FROM datasets WHERE id=?', (identity,))
            self.db.execute('INSERT INTO datasets VALUES(?,?,?,?,?,?,?,?,?)',
                            (identity, kind, source, observed, fetched, version, pack(header), time.time(), size))
            if kind == 'bars':
                records = []
                for stamp, values in zip(data.index.as_unit('ns').asi8, data.itertuples(index=False, name=None)):
                    row = dict(zip(data.columns, values))
                    num = lambda name: None if pd.isna(row.get(name)) else float(row[name])
                    records.append((identity, int(stamp), *(num(c) for c in ('Open', 'High', 'Low', 'Close', 'Volume')),
                                    int(bool(row.get('BadVol', False))), fetched if int(stamp) in incoming else previous_fetches[int(stamp)]))
                self.db.executemany('INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?)', records)
            elif kind == 'series':
                self.db.executemany('INSERT INTO series VALUES(?,?,?,?)',
                                    ((identity, int(t), float(v), fetched if int(t) in incoming else previous_fetches[int(t)])
                                     for t, v in zip(data.index.as_unit('ns').asi8, data)))
            if catalog:
                self.db.execute('INSERT INTO series_catalog VALUES(?,?,?)', (identity, catalog[0], catalog[1]))
            if kind == 'quote':
                self.db.execute('INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                (identity, data['symbol'], data.get('price'), data.get('prev_close'), data.get('volume'),
                                 data.get('day_low'), data.get('day_high'), data.get('ts'), data['fetched'], source, int(data.get('live', False))))
            elif kind == 'facts':
                records = []
                for tax, concepts in data.get('facts', {}).items():
                    for concept, item in concepts.items():
                        for unit, facts in item.get('units', {}).items():
                            for fact in facts:
                                records.append((identity, len(records), tax, concept, unit, fact.get('start'), fact.get('end'),
                                                fact.get('filed'), fact.get('val'), pack(fact)))
                self.db.executemany('INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?)', records)
            elif kind == 'filings':
                self.db.executemany('INSERT OR REPLACE INTO filings VALUES(?,?,?,?,?,?,?,?)',
                                    ((identity, f['acc'], f['date'], f['form'], f['doc'], f['desc'], f['items'], f['period']) for f in data))
            elif kind == 'news':
                for item in data:
                    article = key((item.get('url'), item.get('title')))
                    ts = item.get('ts')
                    self.db.execute('''INSERT INTO news(id,title,publisher,published,url,summary,source,fetched)
                        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                        publisher=excluded.publisher,published=excluded.published,summary=excluded.summary,
                        source=excluded.source,fetched=excluded.fetched''',
                        (article, item['title'], item.get('src'), ts.timestamp() if ts is not None and not pd.isna(ts) else None,
                         item.get('url'), item.get('summary'), item.get('provider') or source, fetched))
                    self.db.execute('INSERT OR IGNORE INTO news_topics VALUES(?,?)', (identity, article))
            elif kind == 'document':
                self.db.execute('INSERT INTO documents VALUES(?,?)', (identity, pack(data)))
            elif kind == 'fundamental_metrics':
                self.db.executemany('INSERT INTO fundamental_metrics VALUES(?,?,?,?,?,?,?,?,?,?)',
                    ((identity, i, r['symbol'], r['metric'], r.get('value'), r.get('unit'), r.get('period'),
                      r.get('period_end'), r.get('filing_date'), pack(r)) for i, r in enumerate(data)))
            elif kind == 'analyst_estimates':
                self.db.executemany('INSERT INTO analyst_estimates VALUES(?,?,?,?,?,?,?)',
                    ((identity, i, r['symbol'], r['kind'], r.get('period'), r.get('period_end'), pack(r)) for i, r in enumerate(data)))
            elif kind == 'fund_holdings':
                self.db.executemany('INSERT INTO fund_holdings VALUES(?,?,?,?,?,?,?,?)',
                    ((identity, i, r['fund'], r['symbol'], r['kind'], r.get('weight'), r.get('report_date'), pack(r)) for i, r in enumerate(data)))
        return data, Metadata(source, observed, fetched, version, 'fresh', 'provider')

    def register_series(self, identity, symbol, interval):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO series_catalog VALUES(?,?,?)', (identity, symbol, interval))

    def stored_bars(self, symbol, interval, start=None, end=None):
        with self.lock:
            sql = """SELECT b.*,d.source,d.fetched AS dataset_fetched,d.version,
                ROW_NUMBER() OVER (PARTITION BY b.stamp ORDER BY b.fetched DESC) AS priority
                FROM bars b JOIN series_catalog c ON c.dataset=b.dataset JOIN datasets d ON d.id=b.dataset
                WHERE c.symbol=? AND c.interval=?"""
            args = [symbol, interval]
            for bound, op in ((start, '>='), (end, '<')):
                if bound:
                    sql += f' AND b.stamp{op}?'
                    args.append(pd.Timestamp(bound).value)
            rows = self.db.execute('SELECT * FROM (' + sql + ') WHERE priority=1 ORDER BY stamp', args).fetchall()
            if not rows:
                return None
            frame = pd.DataFrame([[r[k] for k in ('open','high','low','close','volume','bad_volume','fetched','source')]
                                  for r in rows], columns=['Open','High','Low','Close','Volume','BadVol','FetchedAt','Source'],
                                 index=pd.to_datetime([r['stamp'] for r in rows], utc=True))
            frame['BadVol'] = frame.BadVol.astype(bool)
            fetched = max(r['fetched'] for r in rows)
            meta = Metadata('yf', frame.index[-1].timestamp(), fetched, key(tuple(sorted({r['version'] for r in rows}))),
                            'stale' if time.time()-fetched > 60 else 'fresh', 'observations',
                            'Stored observations only; no provider refresh performed')
            return frame, meta

    def quote_snapshot(self):
        with self.lock:
            return [self.get(r[0]) for r in self.db.execute("SELECT id FROM datasets WHERE kind='quote'").fetchall()]

    def index_rows(self, domain, rows, query=None, fetched=None, reset_query=True):
        fetched = fetched or time.time()
        with self.lock, self.db:
            if query is not None and reset_query:
                self.db.execute('DELETE FROM discovery WHERE domain=? AND query=?', (domain, query.casefold()))
            for row in rows:
                symbol, name = row[:2]
                kind, source = (row[2], row[3]) if domain == 'security' else (row[2], 'FRED' if query is not None else 'local')
                existing = self.db.execute('SELECT id FROM search_documents WHERE domain=? AND symbol=? AND source=?',
                                           (domain, symbol, source)).fetchone()
                self.db.execute('''INSERT INTO search_documents(domain,symbol,name,kind,source,row,fetched) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(domain,symbol,source) DO UPDATE SET name=excluded.name,kind=excluded.kind,
                    row=excluded.row,fetched=excluded.fetched''', (domain, symbol, name, kind, source, pack(tuple(row)), fetched))
                doc = existing[0] if existing else self.db.execute('SELECT last_insert_rowid()').fetchone()[0]
                self.db.execute('DELETE FROM search_fts WHERE rowid=?', (doc,))
                categories = {'FUTURE': 'commodity commodities futures', 'CRYPTOCURRENCY': 'crypto cryptocurrency cryptocurrencies',
                              'CURRENCY': 'forex fx currency currencies', 'INDEX': 'index indices indexes',
                              'ETF': 'etf exchange traded fund funds', 'EQUITY': 'stock stocks equity equities',
                              'MUTUALFUND': 'mutual fund funds'}
                self.db.execute('INSERT INTO search_fts(rowid,symbol,name,kind) VALUES(?,?,?,?)',
                                (doc, symbol, name, kind + ' ' + categories.get(kind, '')))
                if query is not None:
                    self.db.execute('INSERT OR IGNORE INTO discovery VALUES(?,?,?)', (domain, query.casefold(), doc))

    def search(self, domain, text):
        """FTS prefix candidates plus indexed symbols and recorded fuzzy-provider hits."""
        import re
        words = re.findall(r'\w+', text, re.UNICODE)
        with self.lock:
            if not text:
                candidates = self.db.execute('SELECT row FROM search_documents WHERE domain=? ORDER BY symbol LIMIT 50000', (domain,))
            else:
                phrase = ' AND '.join('"' + w.replace('"', '""') + '"*' for w in words)
                if not phrase:
                    phrase = '""'
                candidates = self.db.execute('''SELECT DISTINCT d.row FROM search_documents d WHERE d.domain=? AND
                    (d.id IN (SELECT rowid FROM search_fts WHERE search_fts MATCH ?) OR d.symbol LIKE ? OR d.name LIKE ? OR
                     d.id IN (SELECT document FROM discovery WHERE domain=? AND query=?)) LIMIT 50000''',
                    (domain, phrase, '%' + text + '%', '%' + text + '%', domain, text.casefold()))
            return [unpack(r[0]) for r in candidates]

    def discovery_time(self, domain, query):
        with self.lock:
            row = self.db.execute('SELECT fetched FROM discovery_runs WHERE domain=? AND query=?', (domain, query.casefold())).fetchone()
            return row[0] if row else 0

    def mark_discovery(self, domain, query, fetched):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO discovery_runs VALUES(?,?,?)', (domain, query.casefold(), fetched))

    def cleanup(self):
        with self.lock, self.db:
            now = time.time()
            self.db.execute('DELETE FROM datasets WHERE accessed<?', (now - 730 * 86400,))
            rows = self.db.execute('SELECT id,size FROM datasets ORDER BY accessed DESC').fetchall()
            total = 0
            for index, row in enumerate(rows):
                total += row['size']
                if index >= 1500 or total > 256 * 1024 * 1024:
                    self.db.execute('DELETE FROM datasets WHERE id=?', (row['id'],))
            count = self.db.execute('SELECT COUNT(*) FROM bars').fetchone()[0]
            if count > 2000000:
                for row in self.db.execute("SELECT id FROM datasets WHERE kind='bars' ORDER BY accessed").fetchall():
                    if count <= 2000000:
                        break
                    removed = self.db.execute('SELECT COUNT(*) FROM bars WHERE dataset=?', (row[0],)).fetchone()[0]
                    self.db.execute('DELETE FROM datasets WHERE id=?', (row[0],))
                    count -= removed
            self.db.execute('DELETE FROM news WHERE fetched<? OR id NOT IN (SELECT article FROM news_topics)', (now - 90 * 86400,))
            self.db.execute('DELETE FROM search_documents WHERE fetched<? OR id NOT IN '
                            '(SELECT id FROM search_documents ORDER BY fetched DESC LIMIT 50000)', (now - 365 * 86400,))
            self.db.execute('DELETE FROM search_fts WHERE rowid NOT IN (SELECT id FROM search_documents)')
            self.db.execute('DELETE FROM discovery_runs WHERE fetched<?', (now - 30 * 86400,))
        # Reclaim free pages when the file grows beyond the logical retention budget.
        with self.lock:
            pages = self.db.execute('PRAGMA page_count').fetchone()[0]
            free = self.db.execute('PRAGMA freelist_count').fetchone()[0]
            if free > pages / 3 and pages > 4096:
                self.db.execute('VACUUM')

    def close(self):
        with self.lock:
            if self.db:
                self.db.close()
                self.db = None


class ResultCache:
    """Disposable cache DB; responses and derived results have separate namespaces/budgets."""
    def __init__(self, path):
        self.repo = Repository(path)

    def initialize(self):
        # Cache has its own schema; deliberately never opens the observation repository.
        repo = self.repo
        with repo.lock:
            if repo.db is not None:
                return
            if str(repo.path) != ':memory:':
                Path(repo.path).parent.mkdir(parents=True, exist_ok=True)
            repo.db = sqlite3.connect(str(repo.path), check_same_thread=False, timeout=20)
            repo.db.execute('PRAGMA journal_mode=WAL')
            repo.db.execute('CREATE TABLE IF NOT EXISTS entries(namespace TEXT, id TEXT, content BLOB, created REAL, expires REAL, '
                            'PRIMARY KEY(namespace,id)) WITHOUT ROWID')
            repo.db.commit()

    def get(self, namespace, identity):
        with self.repo.lock:
            row = self.repo.db.execute('SELECT content,created FROM entries WHERE namespace=? AND id=? AND expires>?',
                                       (namespace, identity, time.time())).fetchone()
            return (unpack(row[0]), row[1]) if row else None

    def put(self, namespace, identity, data, ttl, created=None):
        with self.repo.lock, self.repo.db:
            now = created or time.time()
            self.repo.db.execute('INSERT OR REPLACE INTO entries VALUES(?,?,?,?,?)',
                                 (namespace, identity, pack(data), now, now + ttl))

    def cleanup(self, clear_derived=False):
        with self.repo.lock, self.repo.db:
            self.repo.db.execute('DELETE FROM entries WHERE expires<?', (time.time(),))
            if clear_derived:
                self.repo.db.execute("DELETE FROM entries WHERE namespace='analysis'")
            for ns, limit, budget in [('provider', 1000, 64 * 1024 * 1024), ('analysis', 512, 64 * 1024 * 1024)]:
                rows = self.repo.db.execute('SELECT id,length(content) FROM entries WHERE namespace=? ORDER BY created DESC', (ns,)).fetchall()
                total = 0
                for index, (identity, size) in enumerate(rows):
                    total += size
                    if index >= limit or total > budget:
                        self.repo.db.execute('DELETE FROM entries WHERE namespace=? AND id=?', (ns, identity))
            pages = self.repo.db.execute('PRAGMA page_count').fetchone()[0]
            free = self.repo.db.execute('PRAGMA freelist_count').fetchone()[0]
        if free > pages / 3 and pages > 4096:
            with self.repo.lock:
                self.repo.db.execute('VACUUM')
