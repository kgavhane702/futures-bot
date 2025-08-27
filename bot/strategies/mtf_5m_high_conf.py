from typing import Dict, Optional, Tuple
import pandas as pd

from ..config import TARGET_SPLITS, ALLOW_SHORTS
from .base import Strategy, Decision


def _add_indicators(df: pd.DataFrame, ema_fast: int, ema_slow: int, rsi_len: int, adx_len: int, atr_len: int) -> pd.DataFrame:
    from ta.trend import EMAIndicator, ADXIndicator
    from ta.momentum import RSIIndicator
    from ta.volatility import AverageTrueRange
    x = df.copy()
    x["ema_fast"] = EMAIndicator(x["close"], window=ema_fast).ema_indicator()
    x["ema_slow"] = EMAIndicator(x["close"], window=ema_slow).ema_indicator()
    x["rsi"] = RSIIndicator(x["close"], window=rsi_len).rsi()
    x["atr"] = AverageTrueRange(x["high"], x["low"], x["close"], window=atr_len).average_true_range()
    x["adx"] = ADXIndicator(x["high"], x["low"], x["close"], window=adx_len).adx()
    return x


class Mtf5mHighConfStrategy(Strategy):
    id = "mtf_5m_high_conf"

    def required_timeframes(self) -> Dict[str, int]:
        base_tf = str(self.cfg.get("BASE_TF", "5m"))
        trend_tf = str(self.cfg.get("TREND_TF", "15m"))
        htf_tf = str(self.cfg.get("HTF_TF", "1h"))
        lb = int(self.cfg.get("LOOKBACK", 400))
        return {base_tf: lb, trend_tf: lb, htf_tf: lb}

    def _recent_swing(self, df: pd.DataFrame, lookback: int) -> Tuple[Optional[float], Optional[float]]:
        window = df.iloc[-(lookback + 2): -2]
        if len(window) < 5:
            return None, None
        return float(window["high"].max()), float(window["low"].min())

    def decide(self, symbol: str, data: Dict[str, pd.DataFrame]) -> Decision:
        base_tf = str(self.cfg.get("BASE_TF", "5m"))
        trend_tf = str(self.cfg.get("TREND_TF", "15m"))
        htf_tf = str(self.cfg.get("HTF_TF", "1h"))
        b = data.get(base_tf)
        t = data.get(trend_tf)
        h = data.get(htf_tf)
        if b is None or t is None or h is None:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        if len(b) < 60 or len(t) < 60 or len(h) < 60:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        ema_fast = int(self.cfg.get("EMA_FAST", 20))
        ema_slow = int(self.cfg.get("EMA_SLOW", 50))
        rsi_len = int(self.cfg.get("RSI_LEN", 14))
        adx_len = int(self.cfg.get("ADX_LEN", 14))
        atr_len = int(self.cfg.get("ATR_LEN", 14))

        b = _add_indicators(b, ema_fast, ema_slow, rsi_len, adx_len, atr_len)
        t = _add_indicators(t, ema_fast, ema_slow, rsi_len, adx_len, atr_len)
        h = _add_indicators(h, ema_fast, ema_slow, rsi_len, adx_len, atr_len)

        l = b.iloc[-2]
        lt = t.iloc[-2]
        lh = h.iloc[-2]
        if any(pd.isna(l[["ema_fast","ema_slow","rsi","atr","adx"]])) or \
           any(pd.isna(lt[["ema_fast","ema_slow","rsi","atr","adx"]])) or \
           any(pd.isna(lh[["ema_fast","ema_slow","rsi","atr","adx"]])):
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Trend confluence
        min_adx = float(self.cfg.get("MIN_ADX", 20))
        trend_up = (lt["ema_fast"] > lt["ema_slow"]) and (lh["ema_fast"] > lh["ema_slow"]) and (lt["adx"] >= min_adx) and (lh["adx"] >= min_adx)
        trend_down = (lt["ema_fast"] < lt["ema_slow"]) and (lh["ema_fast"] < lh["ema_slow"]) and (lt["adx"] >= min_adx) and (lh["adx"] >= min_adx)

        # Momentum gate on base TF
        rsi_long_min = float(self.cfg.get("RSI_LONG_MIN", 55))
        rsi_short_max = float(self.cfg.get("RSI_SHORT_MAX", 45))
        body_ratio = abs(float(l["close"]) - float(l["open"])) / max(1e-9, float(l["high"]) - float(l["low"]))
        body_min = float(self.cfg.get("BODY_MIN", 0.60))

        side = None
        if trend_up and (l["ema_fast"] > l["ema_slow"]) and (l["rsi"] >= rsi_long_min) and (body_ratio >= body_min):
            side = "long"
        elif ALLOW_SHORTS and trend_down and (l["ema_fast"] < l["ema_slow"]) and (l["rsi"] <= rsi_short_max) and (body_ratio >= body_min):
            side = "short"
        if side is None:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        entry = float(l["close"])
        atr = float(l["atr"]) if pd.notna(l["atr"]) else None
        if atr is None or atr <= 0:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Structure-based SL/TP: last swing as SL, next swing as TP; require RR >= min
        sw_h, sw_l = self._recent_swing(b, int(self.cfg.get("LOOKBACK_SWINGS", 30)))
        if side == "long":
            if sw_l is None:
                return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
            initial_stop = float(sw_l) - float(self.cfg.get("SL_BUFFER_ATR", 0.25)) * atr
            # Target from recent swing high region
            if sw_h is None or sw_h <= entry:
                return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
            main_target = float(sw_h)
        else:
            if sw_h is None:
                return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
            initial_stop = float(sw_h) + float(self.cfg.get("SL_BUFFER_ATR", 0.25)) * atr
            if sw_l is None or sw_l >= entry:
                return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
            main_target = float(sw_l)

        stop_dist = abs(entry - initial_stop)
        tp_dist = abs(main_target - entry)
        if stop_dist <= 0 or tp_dist <= 0:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        min_rr = float(self.cfg.get("MIN_RR", 2.0))
        if (tp_dist / stop_dist) < (min_rr - 1e-6):
            # Require TP further than SL; skip otherwise
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Confidence: EMA gap + ADX strength + RSI band proximity
        ema_gap = abs(l["ema_fast"] - l["ema_slow"]) / max(1e-9, abs(l["ema_slow"]))
        adx_term = max(0.0, (min(lt["adx"], lh["adx"]) - min_adx) / 50.0)
        if side == "long":
            rsi_term = max(0.0, (l["rsi"] - rsi_long_min) / 45.0)
        else:
            rsi_term = max(0.0, (rsi_short_max - l["rsi"]) / 45.0)
        confidence = float(max(0.0, min(1.0, 0.5 * ema_gap + 0.3 * adx_term + 0.2 * rsi_term)))
        conf_min = float(self.cfg.get("CONF_MIN", 0.75))
        if confidence < conf_min:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Targets: split to three around main target by fractions
        # Ensure main target is last (TP3)
        if side == "long":
            t1 = entry + 0.5 * (main_target - entry)
            t2 = entry + 0.8 * (main_target - entry)
            t3 = main_target
        else:
            t1 = entry - 0.5 * (entry - main_target)
            t2 = entry - 0.8 * (entry - main_target)
            t3 = main_target

        splits = list(TARGET_SPLITS or [0.5, 0.3, 0.2])
        return Decision(
            symbol,
            self.id,
            side,
            float(confidence * 100.0),  # use a higher numeric score from confidence
            float(confidence),
            float(entry),
            float(atr),
            float(initial_stop),
            float(t3),
            {},
            initial_stop=float(initial_stop),
            targets=[float(t1), float(t2), float(t3)],
            splits=splits,
        )


