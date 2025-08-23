from typing import Dict
import pandas as pd

from ..config import TIMEFRAME, HTF_TIMEFRAME, TP_R_MULT, ALLOW_SHORTS, TARGET_SPLITS
from ..indicators import add_indicators, valid_row
from ..signals import score_signal
from ..risk import protective_prices
from .base import Strategy, Decision


class MtfEmaRsiAdxStrategy(Strategy):
    id = "mtf_ema_rsi_adx"

    def required_timeframes(self) -> Dict[str, int]:
        return {TIMEFRAME: 400, HTF_TIMEFRAME: 400}

    def decide(self, symbol: str, data: Dict[str, pd.DataFrame]) -> Decision:
        # Guard for minimal data length before indicator access
        ltf_raw = data.get(TIMEFRAME)
        htf_raw = data.get(HTF_TIMEFRAME)
        if ltf_raw is None or htf_raw is None:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        min_len = 60
        if len(ltf_raw) < min_len or len(htf_raw) < min_len:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        ltf = add_indicators(ltf_raw)
        htf = add_indicators(htf_raw)
        if len(ltf) < min_len or len(htf) < min_len:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        l = ltf.iloc[-2]
        h = htf.iloc[-2]
        if not (valid_row(l) and valid_row(h)):
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        long_ok = (l["ema_fast"] > l["ema_slow"]) and (l["rsi"] >= 52) and (l["adx"] >= 18) \
                  and (h["ema_fast"] > h["ema_slow"]) and (h["adx"] >= 18)
        short_ok = (l["ema_fast"] < l["ema_slow"]) and (l["rsi"] <= 48) and (l["adx"] >= 18) \
                   and (h["ema_fast"] < h["ema_slow"]) and (h["adx"] >= 18)

        side = None
        if long_ok:
            side = "long"
        elif ALLOW_SHORTS and short_ok:
            side = "short"
        if side is None:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        score = score_signal(side, l)
        entry = float(l["close"])
        atr = float(l["atr"]) if pd.notna(l["atr"]) else None
        if atr is None or atr <= 0:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        stop, tp, _ = protective_prices("buy" if side == "long" else "sell", entry, atr, TP_R_MULT)
        # A simple confidence: normalized EMA gap + ADX distance and RSI distance from threshold
        ema_gap = abs(l["ema_fast"] - l["ema_slow"]) / max(1e-9, abs(l["ema_slow"]))
        adx_term = max(0.0, (l["adx"] - 18) / 50.0)
        if side == "long":
            rsi_term = max(0.0, (l["rsi"] - 52) / 48.0)
        else:
            rsi_term = max(0.0, (48 - l["rsi"]) / 48.0)
        confidence = float(max(0.0, min(1.0, 0.5 * ema_gap + 0.3 * adx_term + 0.2 * rsi_term)))
        # Build targets from main TP using cumulative TARGET_SPLITS as level percentages.
        # Example: TARGET_SPLITS=0.5,0.3,0.2 → cumulative=0.5,0.8,1.0 (T1,T2,T3)
        splits = list(TARGET_SPLITS or [0.5, 0.3, 0.2])
        cum = []
        s = 0.0
        for v in splits[:3]:
            s = min(1.0, max(0.0, s + float(v)))
            cum.append(s)
        # Ensure we always include 100% target as the last
        if not cum or cum[-1] < 1.0:
            if len(cum) < 3:
                cum += [1.0] * (3 - len(cum))
            else:
                cum[-1] = 1.0
        cum = cum[:3]
        if side == "long":
            delta = tp - entry
            t1 = entry + cum[0] * delta
            t2 = entry + cum[1] * delta
            t3 = entry + cum[2] * delta
        else:
            delta = entry - tp
            t1 = entry - cum[0] * delta
            t2 = entry - cum[1] * delta
            t3 = entry - cum[2] * delta
        return Decision(symbol, self.id, side, score, confidence, entry, atr, stop, tp, {
        }, initial_stop=stop, targets=[t1, t2, t3], splits=splits)


