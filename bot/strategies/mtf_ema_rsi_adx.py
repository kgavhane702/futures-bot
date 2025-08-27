from typing import Dict
import pandas as pd

from ..config import TP_R_MULT, ALLOW_SHORTS, TARGET_SPLITS
from ..indicators import add_indicators, valid_row
from ..signals import score_signal
from ..risk import protective_prices
from .base import Strategy, Decision


class MtfEmaRsiAdxStrategy(Strategy):
    id = "mtf_ema_rsi_adx"

    def required_timeframes(self) -> Dict[str, int]:
        return super().required_timeframes() or {
            str(self.cfg.get("TIMEFRAME", "15m")): int(self.cfg.get("LOOKBACK", 400)),
            str(self.cfg.get("HTF_TIMEFRAME", "1h")): int(self.cfg.get("HTF_LOOKBACK", self.cfg.get("LOOKBACK", 400))),
        }

    def decide(self, symbol: str, data: Dict[str, pd.DataFrame]) -> Decision:
        # Guard for minimal data length before indicator access
        tf = str(self.cfg.get("TIMEFRAME", "15m"))
        htf = str(self.cfg.get("HTF_TIMEFRAME", "1h"))
        ltf_raw = data.get(tf)
        htf_raw = data.get(htf)
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

        rsi_long_min = float(self.cfg.get("RSI_LONG_MIN", 55))
        rsi_short_max = float(self.cfg.get("RSI_SHORT_MAX", 45))
        min_adx = float(self.cfg.get("MIN_ADX", 22))
        long_ok = (l["ema_fast"] > l["ema_slow"]) and (l["rsi"] >= rsi_long_min) and (l["adx"] >= min_adx) \
                  and (h["ema_fast"] > h["ema_slow"]) and (h["adx"] >= min_adx)
        short_ok = (l["ema_fast"] < l["ema_slow"]) and (l["rsi"] <= rsi_short_max) and (l["adx"] >= min_adx) \
                   and (h["ema_fast"] < h["ema_slow"]) and (h["adx"] >= min_adx)

        side = None
        if long_ok:
            side = "long"
        elif ALLOW_SHORTS and short_ok:
            side = "short"
        # Optional micro-TF confirmation (e.g., 1m pullback break in trend)
        if side is not None and bool(self.cfg.get("MICRO_CONFIRM", False)) and bool(self.cfg.get("USE_MICRO_TF", False)):
            micro_tf = str(self.cfg.get("MICRO_TF", "1m"))
            mdf = data.get(micro_tf)
            if mdf is None or len(mdf) < 50:
                side = None
            else:
                mdf = add_indicators(mdf)
                ml = mdf.iloc[-2]
                if side == "long" and not (ml["ema_fast"] > ml["ema_slow"]):
                    side = None
                if side == "short" and not (ml["ema_fast"] < ml["ema_slow"]):
                    side = None
        if side is None:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Regime gating (skip very low-trend regimes)
        regime_adx_min = float(self.cfg.get("REGIME_ADX_MIN", 18))
        if l["adx"] < regime_adx_min and h["adx"] < regime_adx_min:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        score = score_signal(side, l)
        entry = float(l["close"])
        atr = float(l["atr"]) if pd.notna(l["atr"]) else None
        if atr is None or atr <= 0:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        stop, tp, _ = protective_prices("buy" if side == "long" else "sell", entry, atr, TP_R_MULT)
        # A simple confidence: normalized EMA gap + ADX distance and RSI distance from threshold
        ema_gap = abs(l["ema_fast"] - l["ema_slow"]) / max(1e-9, abs(l["ema_slow"]))
        adx_term = max(0.0, (l["adx"] - min_adx) / 50.0)
        if side == "long":
            rsi_term = max(0.0, (l["rsi"] - rsi_long_min) / 48.0)
        else:
            rsi_term = max(0.0, (rsi_short_max - l["rsi"]) / 48.0)
        confidence = float(max(0.0, min(1.0, 0.5 * ema_gap + 0.3 * adx_term + 0.2 * rsi_term)))
        # Normalize score to confidence scale (0..100) to avoid biasing selection unfairly
        score = float(max(0.0, min(100.0, confidence * 100.0)))
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


