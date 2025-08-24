import pandas as pd

import re
from .utils import log
from .config import MIN_24H_QUOTE_VOLUME_USDT, SYMBOL_BLACKLIST as GLOBAL_BLACKLIST, SYMBOL_WHITELIST as GLOBAL_WHITELIST, SYMBOL_EXCLUDE_REGEX


def top_usdt_perps(ex, n: int = 12):
    ex.load_markets()
    symbols = []
    for s, m in ex.markets.items():
        if m.get("swap") and m.get("linear") and m.get("quote") == "USDT":
            symbols.append(s)
    tickers = ex.fetch_tickers(symbols)
    scored = []
    rx = re.compile(SYMBOL_EXCLUDE_REGEX) if SYMBOL_EXCLUDE_REGEX else None
    for sym, t in tickers.items():
        qv = t.get("quoteVolume")
        if qv is None:
            qv = float(t.get("info", {}).get("quoteVolume", 0) or 0)
        if rx and rx.search(sym):
            continue
        if GLOBAL_BLACKLIST and sym in GLOBAL_BLACKLIST:
            continue
        if GLOBAL_WHITELIST and sym not in GLOBAL_WHITELIST:
            continue
        if MIN_24H_QUOTE_VOLUME_USDT and qv < MIN_24H_QUOTE_VOLUME_USDT:
            continue
        scored.append((sym, qv))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in scored[:n]]


def fetch_ohlcv_df(ex, symbol: str, timeframe: str, limit: int = 400) -> pd.DataFrame:
    data = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df


