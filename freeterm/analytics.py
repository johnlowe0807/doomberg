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
