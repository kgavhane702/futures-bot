from typing import Dict
import pandas as pd

from ..config import (
    TIMEFRAME,
    HTF_TIMEFRAME,
    EMA_FAST,
    EMA_SLOW,
    RSI_PERIOD,
    RSI_LONG_MIN,
    RSI_SHORT_MAX,
    ADX_PERIOD,
    MIN_ADX,
    TARGET_SPLITS,
)
from ..indicators import add_indicators, valid_row
from .base import Strategy, Decision


class BreakoutStrategy(Strategy):
    id = "breakout"

    def required_timeframes(self) -> Dict[str, int]:
        # Need enough bars to compute swings/indicators and volume averages
        return {TIMEFRAME: 400, HTF_TIMEFRAME: 400}

    def _swing_levels(self, df: pd.DataFrame, lookback: int = 50) -> Dict[str, float]:
        # Recent structural swing high/low within lookback window (excluding current forming bar)
        window = df.iloc[-(lookback + 2) : -2]
        if len(window) < 5:
            return {"high": None, "low": None}
        swing_high = float(window["high"].max())
        swing_low = float(window["low"].min())
        return {"high": swing_high, "low": swing_low}

    def _volume_ok(self, df: pd.DataFrame, mult: float = 1.5) -> bool:
        # Last closed bar volume above average * multiplier
        if len(df) < 30:
            return False
        vol = df["volume"].astype(float)
        v_avg = float(vol.iloc[-30:-2].mean()) if vol.iloc[-30:-2].size > 0 else 0.0
        v_last = float(vol.iloc[-2])
        return v_last > (mult * max(1e-9, v_avg))

    def decide(self, symbol: str, data: Dict[str, pd.DataFrame]) -> Decision:
        # Prepare data with indicators
        ltf_raw = data.get(TIMEFRAME)
        htf_raw = data.get(HTF_TIMEFRAME)
        if ltf_raw is None or htf_raw is None:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        ltf = add_indicators(ltf_raw)
        htf = add_indicators(htf_raw)
        if len(ltf) < 60 or len(htf) < 60:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        l = ltf.iloc[-2]
        h = htf.iloc[-2]
        if not (valid_row(l) and valid_row(h)):
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # HTF trend alignment + strength filter
        htf_up = h["ema_fast"] > h["ema_slow"] and h["adx"] >= MIN_ADX
        htf_down = h["ema_fast"] < h["ema_slow"] and h["adx"] >= MIN_ADX

        # LTF momentum filter
        ltf_mom_up = l["ema_fast"] > l["ema_slow"] and l["rsi"] >= RSI_LONG_MIN
        ltf_mom_down = l["ema_fast"] < l["ema_slow"] and l["rsi"] <= RSI_SHORT_MAX

        # Swing structure and volume confirmation
        swings = self._swing_levels(ltf, lookback=50)
        vol_ok = self._volume_ok(ltf, mult=1.5)
        if not vol_ok:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        entry = float(l["close"])  # reference entry
        atr = float(l["atr"]) if pd.notna(l["atr"]) else None
        if atr is None or atr <= 0:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        side = None
        initial_stop = None
        # ATR buffer to avoid stop hunts
        atr_buffer = 0.5 * atr

        # Breakout long: close above recent swing high with filters
        if htf_up and ltf_mom_up and swings.get("high") is not None:
            if entry > float(swings["high"]):
                side = "long"
                # SL just beyond last swing low + ATR buffer (tighter of swing low vs entry-ATR buffer)
                if swings.get("low") is not None:
                    initial_stop = float(swings["low"]) - atr_buffer
                else:
                    initial_stop = entry - 1.5 * atr

        # Breakout short: close below recent swing low with filters
        if side is None and htf_down and ltf_mom_down and swings.get("low") is not None:
            if entry < float(swings["low"]):
                side = "short"
                # SL just beyond last swing high + ATR buffer
                if swings.get("high") is not None:
                    initial_stop = float(swings["high"]) + atr_buffer
                else:
                    initial_stop = entry + 1.5 * atr

        if side is None or initial_stop is None:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Risk per unit (R)
        r = abs(entry - initial_stop)
        if r <= 0:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Targets at 1R/2R/3R
        if side == "long":
            t1 = entry + 1.0 * r
            t2 = entry + 2.0 * r
            t3 = entry + 3.0 * r
        else:
            t1 = entry - 1.0 * r
            t2 = entry - 2.0 * r
            t3 = entry - 3.0 * r

        # R:R to main target must be >= 1:2
        rr_main = (abs(t2 - entry)) / r  # 2R target
        if rr_main < 2.0 - 1e-6:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Score & confidence: combine volume surge and trend alignment
        # Volume factor
        vol = ltf["volume"].astype(float)
        v_avg = float(vol.iloc[-30:-2].mean()) if vol.iloc[-30:-2].size > 0 else 0.0
        v_last = float(vol.iloc[-2])
        vol_surge = (v_last / max(1e-9, v_avg)) if v_avg > 0 else 0.0
        ema_gap = abs(l["ema_fast"] - l["ema_slow"]) / max(1e-9, abs(l["ema_slow"]))
        adx_term = max(0.0, (l["adx"] - MIN_ADX) / 50.0)
        score = float(ema_gap * 1000 + max(0.0, (vol_surge - 1.0)) * 100 + adx_term * 10)
        confidence = float(max(0.0, min(1.0, 0.4 * min(2.0, vol_surge) + 0.4 * adx_term + 0.2 * min(1.0, ema_gap))))

        # Partial sizes
        splits = list(TARGET_SPLITS or [0.5, 0.3, 0.2])
        targets = [t1, t2, t3]

        return Decision(
            symbol,
            self.id,
            side,
            score,
            confidence,
            entry,
            atr,
            initial_stop,
            t3,
            {"rr_main": rr_main},
            initial_stop=initial_stop,
            targets=targets,
            splits=splits,
        )


