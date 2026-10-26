"""Indexed domain record stores, financial captures and Yahoo stream observations.

Called under Repository's lock/transaction in workers. Raw responses remain separate.
Each domain has its own table and useful typed indexes; no provider formats enter SQL.
"""
import time

from .field_values import timestamp
from .serialization import key, pack, unpack

TABLES = {
    'ownership_records': ('symbol', 'kind', 'report_date'),
    'action_records': ('symbol', 'kind', 'event_date'),
    'share_records': ('symbol', 'observation_date'),
    'research_records': ('scope', 'scope_key', 'kind', 'symbol'),
    'event_records': ('kind', 'symbol', 'event_date', 'event_at', 'region'),
    'article_records': ('article_id', 'symbol', 'published_at', 'published_date', 'publisher'),
    'statement_records': ('symbol', 'statement', 'period', 'period_end', 'metric'),
    'company_records': ('symbol', 'kind', 'field', 'period_end', 'event_date'),
}
FINANCIAL = {'fundamental_metrics': 'fundamentals', 'analyst_estimates': 'estimates', 'fund_holdings': 'holdings'}
MERGED = {'action_records', 'share_records', 'article_records', 'statement_records'}


def initialize(db):
    for table, columns in TABLES.items():
        db.execute(f'''CREATE TABLE IF NOT EXISTS {table} (
            dataset TEXT REFERENCES datasets(id) ON DELETE CASCADE,
            record_id TEXT, ordinal INTEGER, {','.join(c + ' TEXT' for c in columns)},
            record BLOB NOT NULL, PRIMARY KEY(dataset,record_id)) WITHOUT ROWID''')
        db.execute(f'CREATE INDEX IF NOT EXISTS {table}_lookup ON {table}({",".join(columns)})')
        existing={r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        for field in ('fetched_at','available_at'):
            if field not in existing:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {field} TEXT')
                for row in db.execute(f'SELECT dataset,record_id,record FROM {table}').fetchall():
                    db.execute(f'UPDATE {table} SET {field}=? WHERE dataset=? AND record_id=?',
                               (unpack(row['record']).get(field),row['dataset'],row['record_id']))
        db.execute(f'CREATE INDEX IF NOT EXISTS {table}_availability ON {table}(available_at,fetched_at)')
        db.execute(f'CREATE INDEX IF NOT EXISTS {table}_record_version ON {table}(record_id,fetched_at)')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS financial_captures (
            id TEXT PRIMARY KEY, symbol TEXT, dataset TEXT, source TEXT, captured_at TEXT,
            version TEXT, content BLOB NOT NULL, size INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS capture_lookup ON financial_captures(symbol,dataset,source,captured_at);
        CREATE TABLE IF NOT EXISTS yahoo_prices (
            symbol TEXT, observed_at TEXT, received_at TEXT, price REAL, record BLOB NOT NULL,
            PRIMARY KEY(symbol,observed_at)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS yahoo_price_time ON yahoo_prices(observed_at,received_at);
    ''')


def get(db, table, identity):
    return [unpack(row[0]) for row in db.execute(f'SELECT record FROM {table} WHERE dataset=? ORDER BY ordinal', (identity,))]


def merge(db, table, identity, rows):
    if table not in MERGED:
        return rows
    # Stable IDs replace corrections while retaining observations outside rolling downloads.
    values = {row['record_id']: row for row in get(db, table, identity)}
    values.update({row['record_id']: row for row in rows})
    if table == 'article_records':
        cutoff = timestamp(time.time() - 90 * 86400)
        values = {k: r for k, r in values.items() if (r.get('fetched_at') or '') >= cutoff}
    return list(values.values())[-100000:]


def prepare(db, table, identity, rows):
    """Preserve first availability of unchanged records; corrections start a new version.

    Compare only the latest compatible observation, so a value that reverts after
    a correction doesn't acquire the availability date of a much older version.
    """
    prefix=':'.join(identity.split(':')[:2])+':'
    transient={'fetched_at','available_at','freshness','normalization_version'}
    payload=lambda row:{k:v for k,v in row.items() if k not in transient}
    prepared=[]
    for original in rows:
        row=dict(original)
        old=db.execute(f'SELECT record FROM {table} WHERE record_id=? AND dataset LIKE ? ORDER BY fetched_at DESC LIMIT 1',
                       (row['record_id'],prefix+'%')).fetchone()
        if old:
            previous=unpack(old[0])
            if payload(row)==payload(previous) and previous.get('available_at'):
                row['available_at']=min(row.get('available_at') or previous['available_at'],previous['available_at'])
        prepared.append(row)
    return prepared


def put(db, table, identity, rows):
    columns = TABLES[table]
    names=('dataset','record_id','ordinal',*columns,'record','fetched_at','available_at')
    sql = f'INSERT INTO {table} ({",".join(names)}) VALUES({",".join("?" for _ in names)})'
    db.executemany(sql, ((identity,r['record_id'],i,*(r.get(c) for c in columns),pack(r),r.get('fetched_at'),r.get('available_at')) for i,r in enumerate(rows)))


def observations(db, table, *, symbol=None, kind=None, start=None, end=None, date_field=None, prefix=None, limit=100000):
    columns=TABLES[table]
    conditions,args=[],[]
    if prefix:
        conditions.append('dataset LIKE ?');args.append(prefix+'%')
    for name,value in (('symbol',symbol),('kind',kind)):
        if value is not None and name in columns:
            conditions.append(f'{name}=?');args.append(value)
    if date_field and date_field not in columns:raise ValueError('Unknown observation date field')
    for bound,operator in ((start,'>='),(end,'<')):
        if bound and date_field:
            conditions.append(f'{date_field}{operator}?');args.append(bound)
    sql=f'SELECT *,ROW_NUMBER() OVER (PARTITION BY record_id ORDER BY fetched_at DESC) AS priority FROM {table}'
    if conditions:sql+=' WHERE '+' AND '.join(conditions)
    rows=db.execute('SELECT record FROM ('+sql+') WHERE priority=1 ORDER BY fetched_at DESC LIMIT ?',(*args,limit)).fetchall()
    return [unpack(r[0]) for r in rows]


def capture(db, kind, rows, fetched, source):
    if not rows or source!='yf':
        return
    symbol = rows[0].get('fund') if kind == 'fund_holdings' else rows[0].get('symbol')
    version = key(rows)
    content = pack(rows)
    captured = timestamp(fetched)
    identity = key((symbol, FINANCIAL[kind], source, captured))
    db.execute('INSERT OR REPLACE INTO financial_captures VALUES(?,?,?,?,?,?,?,?)',
               (identity, symbol, FINANCIAL[kind], source, captured, version, content, len(content)))


def captures(db, symbol, dataset, source, as_of=None, limit=10000):
    sql = 'SELECT * FROM financial_captures WHERE symbol=? AND dataset=? AND source=?'
    args = [symbol, dataset, source]
    if as_of:
        sql += ' AND captured_at<=?'
        args.append(as_of)
    sql += ' ORDER BY captured_at DESC'
    if as_of:
        sql += ' LIMIT 1'
    else:
        sql += ' LIMIT ?'
        args.append(limit + 1)
    results = db.execute(sql, args).fetchall()
    records = []
    for row in results:
        records.extend(dict(r, capture_id=row['id'], captured_at=row['captured_at'], available_at=row['captured_at'],
                            availability_basis='collected_snapshot') for r in unpack(row['content']))
        if len(records) > limit:
            break
    return records[:limit], len(records) <= limit and len(results) <= limit


def put_prices(db, rows):
    db.executemany('''INSERT INTO yahoo_prices VALUES(?,?,?,?,?) ON CONFLICT(symbol,observed_at)
        DO UPDATE SET received_at=excluded.received_at,price=excluded.price,record=excluded.record
        WHERE excluded.received_at>=yahoo_prices.received_at''',
        ((r['symbol'], r['observed_at'], r['received_at'], r['price'], pack(r)) for r in rows))


def prices(db, symbols, start=None, end=None, limit=10000):
    sql = 'SELECT * FROM yahoo_prices WHERE symbol IN (' + ','.join('?' for _ in symbols) + ')'
    args = list(symbols)
    for bound, op in ((start, '>='), (end, '<')):
        if bound:
            sql += f' AND observed_at{op}?'
            args.append(bound)
    if not start and not end:
        sql = 'SELECT * FROM (SELECT *,ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY observed_at DESC) AS rank FROM (' + sql + ')) WHERE rank=1'
    rows = db.execute(sql + ' ORDER BY observed_at DESC LIMIT ?', (*args, limit + 1)).fetchall()
    return [unpack(r['record']) for r in rows[:limit]], len(rows) <= limit


def cleanup(db):
    now = time.time()
    db.execute('DELETE FROM yahoo_prices WHERE received_at<?', (timestamp(now - 90 * 86400),))
    db.execute('DELETE FROM yahoo_prices WHERE (symbol,observed_at) IN '
               '(SELECT symbol,observed_at FROM yahoo_prices ORDER BY received_at DESC LIMIT -1 OFFSET 200000)')
    db.execute('DELETE FROM financial_captures WHERE captured_at<?', (timestamp(now - 3650 * 86400),))
    total = 0
    for i, row in enumerate(db.execute('SELECT id,size FROM financial_captures ORDER BY captured_at DESC').fetchall()):
        total += row['size']
        if i >= 10000 or total > 128 * 1024 * 1024:
            db.execute('DELETE FROM financial_captures WHERE id=?', (row['id'],))
