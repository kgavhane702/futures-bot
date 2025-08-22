from .config import UNIVERSE_SYMBOLS

def top_usdt_perps(ex, n=12):
    if UNIVERSE_SYMBOLS:
        return UNIVERSE_SYMBOLS
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
    syms = [s for s, _ in scored[:n]]
    return syms
