import os
import math
from datetime import datetime, timezone

import ccxt
import pandas as pd

from bot.strategies.mtf_5m_high_conf import Mtf5mHighConfStrategy
from bot.strategies.registry import _file_cfg  # reuse loader for defaults


def exchange_from_env():
    ex_id = os.getenv("EXCHANGE", "binanceusdm")
    api_key = os.getenv("API_KEY", "")
    api_secret = os.getenv("API_SECRET", "")
    use_testnet = os.getenv("USE_TESTNET", "true").lower() == "true"
    klass = getattr(ccxt, ex_id)
    ex = klass({
        "apiKey": api_key,
        "secret": api_secret,
        "enableRateLimit": True,
        "options": {"defaultType": "future", "adjustForTimeDifference": True},
    })
    try:
        ex.set_sandbox_mode(use_testnet)
    except Exception:
        pass
    return ex


def fetch_ohlcv_df(ex, symbol: str, tf: str, limit: int) -> pd.DataFrame:
    data = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df


def simulate_trade(entry: float, stop: float, targets: list, bars: pd.DataFrame) -> dict:
    # Simple bar-by-bar sim: entry at next bar open, then check stop/targets intrabar
    if len(bars) < 2:
        return {"outcome": "none", "pnl_r": 0.0}
    side = "long" if entry < targets[-1] else "short"
    next_open = float(bars.iloc[0]["open"])
    # Use next_open as fill proxy
    fill = next_open
    r = abs(fill - stop)
    if r <= 0:
        return {"outcome": "invalid", "pnl_r": 0.0}
    for _, row in bars.iterrows():
        hi = float(row["high"])
        lo = float(row["low"])
        # check stop first (conservative)
        if side == "long" and lo <= stop:
            return {"outcome": "sl", "pnl_r": -1.0}
        if side == "short" and hi >= stop:
            return {"outcome": "sl", "pnl_r": -1.0}
        # then check targets in order
        for i, tp in enumerate(targets, start=1):
            hit = (hi >= tp) if side == "long" else (lo <= tp)
            if hit:
                return {"outcome": f"tp{i}", "pnl_r": float(abs(tp - fill) / r)}
    return {"outcome": "open", "pnl_r": 0.0}


def backtest_symbols(symbols, base_limit=800, walk_forward=300):
    ex = exchange_from_env()
    ex.load_markets()
    sid = "mtf_5m_high_conf"
    cfg = _file_cfg(sid)
    strat = Mtf5mHighConfStrategy(cfg)
    tfs = strat.required_timeframes()

    results = []
    for sym in symbols:
        try:
            # Preload data
            data_by_tf = {}
            for tf, lb in tfs.items():
                data_by_tf[tf] = fetch_ohlcv_df(ex, sym, tf, limit=max(base_limit, lb))
            # Walk-forward
            num_bars = min(*(len(df) for df in data_by_tf.values()))
            # For each step, slice last lb bars for each tf
            wins = 0
            losses = 0
            sum_r = 0.0
            tests = 0
            for idx in range(num_bars - walk_forward, num_bars - 1):
                window = {}
                for tf, lb in tfs.items():
                    df = data_by_tf[tf].iloc[: idx+1]
                    window[tf] = df.tail(lb)
                d = strat.decide(sym, window)
                if not d or d.side not in ("long", "short"):
                    continue
                # simulate
                base_tf = str(cfg.get("BASE_TF", "5m"))
                future_bars = data_by_tf[base_tf].iloc[idx+1 : idx+1+30]  # look ahead 30 bars
                sim = simulate_trade(d.entry_price, d.initial_stop or d.stop, (d.targets or [])[:3], future_bars)
                tests += 1
                sum_r += sim.get("pnl_r", 0.0)
                if sim["outcome"].startswith("tp"):
                    wins += 1
                elif sim["outcome"] == "sl":
                    losses += 1
            rate = (wins / max(1, wins + losses)) * 100.0
            results.append({"symbol": sym, "tests": tests, "win_rate": round(rate,2), "avg_r": round(sum_r / max(1, tests), 3)})
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})
    return pd.DataFrame(results)


if __name__ == "__main__":
    # Example symbols; replace with your test set
    symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT", "BNB/USDT:USDT"]
    df = backtest_symbols(symbols)
    print(df.to_string(index=False))


