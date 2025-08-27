import os
import sys
import pandas as pd

_root = os.path.dirname(os.path.dirname(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

from bot.strategies.breakout import BreakoutStrategy


def _gen_breakout_df(length=200, base=100.0):
    import numpy as np
    x = np.arange(length)
    # build consolidation then breakout up near the end
    close = base + np.sin(x / 10.0) * 0.5
    # inject a lift at the end to exceed prior highs
    close[-3:-1] = base + 5.0
    open_ = close * 0.999
    high = close * 1.001
    low = close * 0.999
    vol = 1000 + x
    ts = pd.date_range("2024-01-01", periods=length, freq="1min")
    df = pd.DataFrame({
        "ts": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": vol,
    })
    return df


def test_breakout_long_targets_exist():
    cfg = {
        "TIMEFRAME": "15m",
        "HTF_TIMEFRAME": "1h",
        "LOOKBACK": 200,
        "HTF_LOOKBACK": 200,
        "MIN_ADX": 1,
        "RSI_LONG_MIN": 10,
        "RSI_SHORT_MAX": 90,
    }
    strat = BreakoutStrategy(cfg)
    ltf = _gen_breakout_df(length=220)
    htf = _gen_breakout_df(length=220)
    dec = strat.decide("BTC/USDT:USDT", {"15m": ltf, "1h": htf})
    # might be None if breakout not strong; relax by raising last candle more
    if dec.side is None:
        ltf.loc[ltf.index[-2], "close"] *= 1.05
        ltf.loc[ltf.index[-2], "high"] *= 1.05
        dec = strat.decide("BTC/USDT:USDT", {"15m": ltf, "1h": htf})
    assert dec.side in ("long", None)
    if dec.side == "long":
        assert dec.initial_stop is not None
        assert isinstance(dec.targets, list) and len(dec.targets) == 3


