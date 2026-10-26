"""Opt-in Yahoo price collection with normalized, durable observations.

Uses yfinance's subscription/decoder transport. Connection lifetime is owned here
so a closed SDK socket is replaced, resubscribed and backed off deterministically.
No terminal feed is enabled or replaced by this module.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time

from . import field_values as s
from .query_models import Result, Metadata
from .serialization import key
from .tabular import Field, normalize_record

VERSION='yahoo-stream-v2-'+s.VERSION

SCHEMA = {
    'symbol':Field('text','Ticker'), 'name':Field('text','Name'), 'source':Field('text','Source',normalizer=s.source),
    'observed_at':Field('timestamp','Observed UTC'), 'received_at':Field('timestamp','Received UTC'),
    'fetched_at':Field('timestamp','Fetched UTC'), 'available_at':Field('timestamp','Available UTC'),
    'source_timezone':Field('text','Source Timezone'), 'currency':Field('text','Currency',normalizer=s.currency,case_sensitive=True),
    'exchange':Field('text','Exchange'), 'type_code':Field('number','Provider Type Code'),
    'market_hours_code':Field('number','Provider Market Hours Code'), 'price':Field('number','Price',unit='quoted_price'),
    'change':Field('number','Change',unit='quoted_price'), 'pct_change':Field('number','Change %',unit='percent'),
    'volume':Field('number','Session Volume',unit='provider_volume'),
    'volume_24h':Field('number','Rolling 24h Volume',unit='provider_volume'),
    'volume_all_currencies':Field('number','Volume All Currencies',unit='provider_volume'),
    'circulating_supply':Field('number','Circulating Supply',unit='provider_supply'),
    'market_cap':Field('number','Market Cap',unit='currency'),
    'normalization_version':Field('text','Normalization Version'),
    'day_low':Field('number','Day Low'), 'day_high':Field('number','Day High'),
    'bid':Field('number','Bid'), 'ask':Field('number','Ask'), 'bid_size':Field('number','Bid Size'),
    'ask_size':Field('number','Ask Size'), 'previous_close':Field('number','Previous Close'),
    'observation_age_seconds':Field('number','Observation Age',unit='seconds'),
    'freshness':Field('text','Freshness'), 'transport':Field('text','Transport'),
    'coverage':Field('text','Coverage'),
}


def normalize_tick(message, received=None):
    received=time.time() if received is None else received
    symbol=s.text(message.get('id') or message.get('symbol'))
    observed_ms=s.number(message.get('time'))
    price=s.number(message.get('price'))
    observed=s.timestamp(observed_ms,epoch_unit='ms') if observed_ms is not None and observed_ms>0 else None
    if not symbol or price is None or observed is None:
        return None
    row=dict(symbol=symbol,name=message.get('short_name'),source='yf',observed_at=observed,
        received_at=s.timestamp(received),fetched_at=s.timestamp(received),available_at=s.timestamp(received),
        source_timezone='UTC',price=price,currency=message.get('currency'),exchange=message.get('exchange'),
        type_code=s.number(message.get('quote_type')),market_hours_code=s.number(message.get('market_hours')),
        normalization_version=VERSION,raw=dict(message),
        transport='stream',coverage='Yahoo stream; instrument coverage and exchange delay are provider-dependent')
    for field,remote in (('change','change'),('pct_change','change_percent'),('volume','day_volume'),('day_low','day_low'),
                         ('day_high','day_high'),('bid','bid'),('ask','ask'),('bid_size','bid_size'),('ask_size','ask_size'),('previous_close','previous_close'),
                         ('volume_24h','vol_24hr'),('volume_all_currencies','vol_all_currencies'),
                         ('circulating_supply','circulating_supply'),('market_cap','market_cap')):
        row[field]=s.number(message.get(remote))
    return normalize_record(row,SCHEMA)


def connection():
    import yfinance as yf
    class Connection(yf.AsyncWebSocket):
        async def listen(self, message_handler):
            await self._connect()
            async for message in self._ws:
                payload=json.loads(message)
                decoded=self._decode_message(payload.get('message',''))
                await message_handler(decoded)
        async def close(self):
            heartbeat=self._heartbeat_task
            await super().close()
            if heartbeat:await asyncio.gather(heartbeat,return_exceptions=True)
    return Connection(verbose=False)


class YahooPriceStream:
    def __init__(self,service,symbols,on_record=None,*,connection_factory=None):
        self.service=service
        self.on_record=on_record
        self.factory=connection_factory or connection
        self.symbols=self._symbols(symbols)
        self.connected=False
        self.error=''
        self.last_received=None
        self.latest={}
        self.dropped=0
        self.malformed=0
        self.queue=asyncio.Queue(maxsize=2000)
        self.changed=asyncio.Event()
        self.stopped=False
        self.task=self.writer=None

    @staticmethod
    def _symbols(symbols):
        from .yahoo_datasets import identifier
        if not isinstance(symbols,(tuple,list,set)) or not 1<=len(symbols)<=100:
            raise ValueError('A Yahoo stream needs 1–100 symbols')
        return set(identifier(v) for v in symbols)

    async def start(self):
        if self.task is not None and not self.task.done():return self
        self.stopped=False
        self.writer=asyncio.create_task(self._write())
        self.task=asyncio.create_task(self._run())
        return self

    async def set_symbols(self,symbols):
        self.symbols=self._symbols(symbols)
        self.changed.set()

    async def _receive(self,message):
        row=normalize_tick(message)
        if row is None:
            self.malformed+=1
            return
        if row['symbol'] not in self.symbols:return
        self.last_received=row['received_at']
        previous=self.latest.get(row['symbol'])
        if not previous or (row['observed_at'],row['received_at'])>=(previous['observed_at'],previous['received_at']):
            self.latest[row['symbol']]=row
        if self.queue.full():
            self.queue.get_nowait();self.queue.task_done()
            self.dropped+=1
            self.error='Yahoo price buffer overflow; some observations were dropped'
            if self.dropped==1:self.service.warn(self.error)
        self.queue.put_nowait(row)

    async def _write(self):
        while True:
            first=await self.queue.get()
            if first is None:
                self.queue.task_done()
                break
            batch=[first]
            while len(batch)<200 and not self.queue.empty():
                row=self.queue.get_nowait()
                if row is None:
                    self.queue.task_done()
                    self.queue.put_nowait(None)
                    break
                batch.append(row)
            try:
                await asyncio.to_thread(self.service.repository.put_yahoo_prices,batch)
            except Exception as error:
                self.error=f'Yahoo price persistence failed ({type(error).__name__}); latest values remain session-only'
                self.service.warn(self.error)
            if self.on_record:
                for row in batch:
                    try:
                        result=self.on_record(dict(row))
                        if inspect.isawaitable(result):await result
                    except Exception as error:
                        self.error=f'Yahoo price consumer failed ({type(error).__name__})'
                        self.service.warn(self.error)
            for _ in batch:self.queue.task_done()

    async def _run(self):
        delay=1
        while not self.stopped:
            client=None
            listen=change=None
            self.changed.clear()
            try:
                client=self.factory()
                await client.subscribe(sorted(self.symbols))
                self.connected=True
                self.error=''
                listen=asyncio.create_task(client.listen(self._receive))
                change=asyncio.create_task(self.changed.wait())
                done,_=await asyncio.wait((listen,change),return_when=asyncio.FIRST_COMPLETED)
                if change in done:
                    delay=1
                    continue
                await listen
                raise ConnectionError('Yahoo stream closed')
            except asyncio.CancelledError:
                break
            except Exception as error:
                self.error=f'Yahoo stream unavailable: {type(error).__name__}: {str(error)[:160]}'
                self.service.warn(self.error)
            finally:
                self.connected=False
                for task in (listen,change):
                    if task:task.cancel()
                await asyncio.gather(*(t for t in (listen,change) if t),return_exceptions=True)
                try:
                    if client:await client.close()
                except Exception:pass
            if not self.stopped:
                try:await asyncio.wait_for(self.changed.wait(),timeout=delay)
                except asyncio.TimeoutError:pass
                delay=min(30,delay*2)

    async def stop(self):
        if self.stopped:return
        self.stopped=True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
        if self.writer:
            await self.queue.put(None)
            await self.writer

    def status(self):
        return dict(connected=self.connected,symbols=sorted(self.symbols),last_received=self.last_received,
                    dropped=self.dropped,malformed=self.malformed,error=self.error)


async def stored_prices(service,query):
    from .yahoo_datasets import instant,pd_epoch
    start,end=instant(query.start) if query.start else None,instant(query.end) if query.end else None
    rows,complete=await asyncio.to_thread(service.repository.yahoo_prices,query.symbols,start,end,query.limit)
    if not start and not end:
        latest={r['symbol']:r for r in rows}
        for stream in service.yahoo_streams:
            for symbol,row in stream.latest.items():
                if symbol in query.symbols and (symbol not in latest or (row['observed_at'],row['received_at'])>(latest[symbol]['observed_at'],latest[symbol]['received_at'])):
                    latest[symbol]=dict(row)
        for symbol in query.symbols:
            if symbol in latest:continue
            old=await asyncio.to_thread(service.repository.get,'quote:'+symbol)
            if old and old[0].get('source')=='yf':
                q=old[0]
                latest[symbol]=dict(symbol=symbol,source='yf',price=q.get('price'),observed_at=s.timestamp(q.get('ts')),
                    received_at=s.timestamp(q.get('fetched')),fetched_at=s.timestamp(q.get('fetched')),available_at=s.timestamp(q.get('fetched')),
                    transport='stored_snapshot',coverage='Stored Yahoo quote fallback; no provider refresh performed')
        rows=list(latest.values())
    now=time.time()
    active={symbol for stream in service.yahoo_streams if stream.connected for symbol in stream.symbols}
    decorated=[]
    for original in rows:
        row=dict(original)
        age=now-pd_epoch(row['observed_at']) if row.get('observed_at') else None
        received_age=now-pd_epoch(row['received_at']) if row.get('received_at') else float('inf')
        row['observation_age_seconds']=age
        row['freshness']='STALE' if received_age>30 or row['symbol'] not in active else 'DLY' if age is None or age>60 or age < -60 else 'LIVE'
        decorated.append(normalize_record(row,SCHEMA))
    decorated=sorted(decorated,key=lambda r:r.get('observed_at') or '',reverse=True)[:query.limit]
    fetched=max((pd_epoch(r['received_at']) for r in decorated if r.get('received_at')),default=0)
    reasons=[]
    if not complete:reasons.append('Observation limit reached')
    missing=set(query.symbols)-{r['symbol'] for r in decorated}
    if missing:reasons.append('No retained Yahoo prices for '+', '.join(sorted(missing)))
    meta=Metadata('yf',fetched_at=fetched,version=key((VERSION,decorated)),freshness='fresh' if decorated and all(r['freshness']=='LIVE' for r in decorated) else 'stale' if decorated else 'unavailable',
        cache='observations',reason='; '.join(reasons),complete=False,
        coverage='Captured Yahoo observations only; transport activity does not imply exchange-grade or complete market coverage')
    return Result(decorated,meta)
