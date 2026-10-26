"""Pure, existing technical calculations; invoked by versioned worker analyses."""
import numpy as np
import pandas as pd

def ints(s: str) -> list[float]:
    return [float(x) for x in str(s).replace(" ", "").split(",") if x]


def sma(c, n): return c.rolling(int(n)).mean()
def ema(c, n): return c.ewm(span=int(n), adjust=False).mean()


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def macd(c, f=12, s=26, sig=9):
    m = ema(c, f) - ema(c, s)
    return m, ema(m, sig)


def vwap(df):
    tp = (df.High + df.Low + df.Close) / 3
    day = df.index.date if hasattr(df.index, "date") else 0
    pv = (tp * df.Volume).groupby(day).cumsum()
    v = df.Volume.groupby(day).cumsum().replace(0, np.nan)
    return pv / v



def indicators(frame, active, parameters):
    overlays = []
    result = {'overlays': overlays}
    if 's' in active:
        overlays += [(f'SMA{int(p)}', sma(frame.Close, p)) for p in ints(parameters['sma'])]
    if 'e' in active:
        overlays += [(f'EMA{int(p)}', ema(frame.Close, p)) for p in ints(parameters['ema'])]
    if 'b' in active:
        period, k = (ints(parameters['bb']) + [20, 2])[:2]
        mid, sd = sma(frame.Close, period), frame.Close.rolling(int(period)).std()
        overlays += [('BB+', mid + k * sd), ('BBm', mid), ('BB-', mid - k * sd)]
    if 'w' in active and 'Volume' in frame:
        overlays.append(('VWAP', vwap(frame)))
    if 'r' in active:
        result['rsi'] = rsi(frame.Close, ints(parameters['rsi'])[0])
    if 'm' in active:
        fast, slow, signal = (ints(parameters['macd']) + [12, 26, 9])[:3]
        result['macd'] = macd(frame.Close, fast, slow, signal)
    return result


import math

def _ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def _npdf(x): return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def bs_greeks(S, K, T, r, sigma, call: bool):
    """Black-Scholes greeks. theta per calendar day, vega per 1 vol point."""
    if not (S and K and T > 0 and sigma and sigma > 0):
        return None
    st = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + sigma * sigma / 2) * T) / st
    d2 = d1 - st
    gamma = _npdf(d1) / (S * st)
    vega = S * _npdf(d1) * math.sqrt(T) / 100
    if call:
        delta = _ncdf(d1)
        theta = (-S * _npdf(d1) * sigma / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * _ncdf(d2)) / 365
    else:
        delta = _ncdf(d1) - 1
        theta = (-S * _npdf(d1) * sigma / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * _ncdf(-d2)) / 365
    return delta, gamma, theta, vega



def statistics_records(frame, interval):
    """Unformatted selected-range statistics, with units; shared by the client and extraction."""
    data=frame.replace([np.inf,-np.inf],np.nan).dropna(subset=['Close'])
    if data.empty:return []
    first,last=data.iloc[0],data.iloc[-1]
    volume=data.get('Volume',pd.Series(np.nan,index=data.index))
    delta=float(last.Close-first.Close)
    values=[('bars',len(data),'count'),('first_close',first.Close,'native_price'),('last_close',last.Close,'native_price'),
        ('change',delta,'native_price'),('return',100*delta/first.Close if first.Close else None,'percent'),
        ('open',first.Open,'native_price'),('high',data.High.max(),'native_price'),('low',data.Low.min(),'native_price'),
        ('mean_close',data.Close.mean(),'native_price'),('median_close',data.Close.median(),'native_price'),
        ('total_volume',volume.sum(min_count=1),'provider_volume'),('mean_volume',volume.mean(),'provider_volume'),
        ('median_volume',volume.median(),'provider_volume'),('max_volume',volume.max(),'provider_volume'),
        ('mean_bar_range',(data.High-data.Low).mean(),'native_price'),
        ('return_volatility',data.Close.pct_change(fill_method=None).std()*100,'percent'),
        ('up_bars',int((data.Close>data.Open).sum()),'count'),('down_bars',int((data.Close<data.Open).sum()),'count')]
    if interval!='1wk':values.append(('mean_daily_volume',volume.groupby(volume.index.date).sum(min_count=1).mean(),'provider_volume'))
    bad=int(data.BadVol.fillna(False).sum()) if 'BadVol' in data else 0
    if bad:values.append(('bad_volume',bad,'count'))
    from .field_values import number
    return [dict(metric=metric,value=number(value),unit=unit) for metric,value,unit in values]
