import pandas as pd

from utils import log


def top_usdt_perps(ex, n: int = 12):
    ex.load_markets()
    symbols = []
    for s, m in ex.markets.items():
        if m.get("swap") and m.get("linear") and m.get("quote") == "USDT":
            symbols.append(s)
    tickers = ex.fetch_tickers(symbols)
    scored = []
    for sym, t in tickers.items():
        qv = t.get("quoteVolume")
        if qv is None:
            qv = float(t.get("info", {}).get("quoteVolume", 0) or 0)
        scored.append((sym, qv))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in scored[:n]]


def fetch_ohlcv_df(ex, symbol: str, timeframe: str, limit: int = 400) -> pd.DataFrame:
    data = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df


