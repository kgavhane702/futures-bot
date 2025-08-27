import os
import sys
import pandas as pd

_root = os.path.dirname(os.path.dirname(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

from bot.strategies.mtf_ema_rsi_adx import MtfEmaRsiAdxStrategy


def _gen_trend_df(up=True, length=200, base=100.0):
    import numpy as np
    # simple monotonic sequence to make EMA_fast>EMA_slow and RSI non-extreme
    x = np.arange(length)
    if up:
        close = base * (1.0 + x * 0.002)
    else:
        close = base * (1.0 - x * 0.002)
    open_ = close * (1.0 + (0.0005 if not up else -0.0005))
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
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


def test_mtf_long_decision_produces_targets():
    cfg = {
        "TIMEFRAME": "15m",
        "HTF_TIMEFRAME": "1h",
        "LOOKBACK": 200,
        "HTF_LOOKBACK": 200,
        # loosen thresholds so synthetic indicators pass
        "RSI_LONG_MIN": 10,
        "RSI_SHORT_MAX": 90,
        "MIN_ADX": 1,
        "REGIME_ADX_MIN": 0,
    }
    strat = MtfEmaRsiAdxStrategy(cfg)
    ltf = _gen_trend_df(up=True, length=220)
    htf = _gen_trend_df(up=True, length=220)
    dec = strat.decide("BTC/USDT:USDT", {"15m": ltf, "1h": htf})
    assert dec.side == "long"
    assert dec.initial_stop is not None and dec.entry_price is not None
    assert isinstance(dec.targets, list) and len(dec.targets) == 3
    # for long, targets should be non-decreasing and last equals take_profit around
    assert dec.targets[0] <= dec.targets[1] <= dec.targets[2]


def test_mtf_micro_confirm_blocks_when_disagrees():
    cfg = {
        "TIMEFRAME": "15m",
        "HTF_TIMEFRAME": "1h",
        "LOOKBACK": 200,
        "HTF_LOOKBACK": 200,
        "USE_MICRO_TF": True,
        "MICRO_CONFIRM": True,
        "MICRO_TF": "1m",
        "RSI_LONG_MIN": 10,
        "RSI_SHORT_MAX": 90,
        "MIN_ADX": 1,
        "REGIME_ADX_MIN": 0,
    }
    strat = MtfEmaRsiAdxStrategy(cfg)
    ltf = _gen_trend_df(up=True, length=220)
    htf = _gen_trend_df(up=True, length=220)
    micro = _gen_trend_df(up=False, length=220)  # micro disagrees
    dec = strat.decide("BTC/USDT:USDT", {"15m": ltf, "1h": htf, "1m": micro})
    assert dec.side is None


