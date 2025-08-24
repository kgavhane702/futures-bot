from typing import Dict, Optional
import pandas as pd

from ..config import TARGET_SPLITS, ALLOW_SHORTS
from ..indicators import valid_row
from ..risk import protective_prices
from .base import Strategy, Decision
from ..state import STATE
from ..utils import log


class Scalping5mStrategy(Strategy):
    id = "scalping"

    def required_timeframes(self) -> Dict[str, int]:
        base_tf = str(self.cfg.get("BASE_TF", "5m"))
        trend_tf = str(self.cfg.get("TREND_TF", "15m"))
        lookback = int(self.cfg.get("LOOKBACK", 400))
        hlook = int(self.cfg.get("HTF_LOOKBACK", lookback))
        tfs = {base_tf: lookback, trend_tf: hlook}
        if bool(self.cfg.get("USE_MICRO_TF", False)):
            micro_tf = str(self.cfg.get("MICRO_TF", "1m"))
            tfs[micro_tf] = int(self.cfg.get("MICRO_LOOKBACK", 400))
        return tfs

    # --- Helpers ---
    def _swing_levels(self, df: pd.DataFrame, lookback: int = 20) -> Dict[str, Optional[float]]:
        window = df.iloc[-(lookback + 2) : -2]
        if len(window) < 5:
            return {"high": None, "low": None}
        return {"high": float(window["high"].max()), "low": float(window["low"].min())}

    def _volume_ok(self, df: pd.DataFrame, len_sma: int, mult: float, z_min: float) -> (bool, float):
        if len(df) < max(30, len_sma + 5):
            return False, 0.0
        vol = df["volume"].astype(float)
        sma = vol.rolling(len_sma).mean()
        std = vol.rolling(len_sma).std()
        vz = 0.0
        try:
            vz = float((vol.iloc[-2] - sma.iloc[-2]) / max(1e-9, std.iloc[-2]))
        except Exception:
            vz = 0.0
        ok = (vol.iloc[-2] > mult * max(1e-9, sma.iloc[-2])) and (vz >= z_min)
        return ok, vz

    def _body_ratio(self, row: pd.Series) -> float:
        high, low, open_, close = float(row["high"]), float(row["low"]), float(row["open"]), float(row["close"])
        rng = max(1e-9, high - low)
        body = abs(close - open_)
        return float(body / rng)

    def decide(self, symbol: str, data: Dict[str, pd.DataFrame]) -> Decision:
        # Timeframes
        base_tf = str(self.cfg.get("BASE_TF", "5m"))
        trend_tf = str(self.cfg.get("TREND_TF", "15m"))
        df_b = data.get(base_tf)
        df_t = data.get(trend_tf)
        if df_b is None or df_t is None:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        if len(df_b) < 60 or len(df_t) < 60:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Indicators with per-strategy windows
        ema_fast = int(self.cfg.get("EMA_FAST", 20))
        ema_slow = int(self.cfg.get("EMA_SLOW", 50))
        rsi_len = int(self.cfg.get("RSI_LEN", 14))
        adx_len = int(self.cfg.get("ADX_LEN", 14))
        atr_len = int(self.cfg.get("ATR_LEN", 14))
        try:
            from ta.trend import EMAIndicator, ADXIndicator
            from ta.momentum import RSIIndicator
            from ta.volatility import AverageTrueRange
            # Base TF
            df_b = df_b.copy()
            df_b["ema_fast"] = EMAIndicator(df_b["close"], window=ema_fast).ema_indicator()
            df_b["ema_slow"] = EMAIndicator(df_b["close"], window=ema_slow).ema_indicator()
            df_b["rsi"] = RSIIndicator(df_b["close"], window=rsi_len).rsi()
            df_b["atr"] = AverageTrueRange(df_b["high"], df_b["low"], df_b["close"], window=atr_len).average_true_range()
            df_b["adx"] = ADXIndicator(df_b["high"], df_b["low"], df_b["close"], window=adx_len).adx()
            # Trend TF
            df_t = df_t.copy()
            df_t["ema_fast"] = EMAIndicator(df_t["close"], window=ema_fast).ema_indicator()
            df_t["ema_slow"] = EMAIndicator(df_t["close"], window=ema_slow).ema_indicator()
            df_t["rsi"] = RSIIndicator(df_t["close"], window=rsi_len).rsi()
            df_t["atr"] = AverageTrueRange(df_t["high"], df_t["low"], df_t["close"], window=atr_len).average_true_range()
            df_t["adx"] = ADXIndicator(df_t["high"], df_t["low"], df_t["close"], window=adx_len).adx()
        except Exception:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        l = df_b.iloc[-2]
        h = df_t.iloc[-2]
        if not (valid_row(l) and valid_row(h)):
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Liquidity filter (approx: rolling vol) and blacklist
        bl_raw = str(self.cfg.get("SYMBOL_BLACKLIST", "")).strip()
        if bl_raw:
            bl = {s.strip().upper() for s in bl_raw.split(",") if s.strip()}
            if symbol.upper() in bl:
                return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Liquidity filter (approx: rolling vol)
        liq_min = self.cfg.get("LIQ_FILTER_MIN_VOL", "auto")
        if liq_min != "auto":
            try:
                v_sma = float(df_b["volume"].astype(float).rolling(int(self.cfg.get("VOL_SMA_LEN", 20))).mean().iloc[-2])
                if v_sma < float(liq_min):
                    return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
            except Exception:
                pass

        # Spread check (use STATE quotes if available)
        try:
            q = STATE.get_quote(symbol)
            max_spread_pct = float(self.cfg.get("MAX_SPREAD_PCT", 0.10)) / 100.0
            if q and q.get("bid") and q.get("ask") and q["ask"] > 0:
                spread_pct = (q["ask"] - q["bid"]) / q["ask"]
                if spread_pct > max_spread_pct:
                    return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        except Exception:
            pass

        # Trend alignment
        use_adx = bool(self.cfg.get("USE_ADX", True))
        adx_min = float(self.cfg.get("ADX_MIN", 20))
        trend_up = (h["ema_fast"] > h["ema_slow"]) and (not use_adx or h["adx"] >= adx_min)
        trend_down = (h["ema_fast"] < h["ema_slow"]) and (not use_adx or h["adx"] >= adx_min)

        # Momentum / body
        rsi_long_min = float(self.cfg.get("RSI_LONG_MIN", 55))
        rsi_short_max = float(self.cfg.get("RSI_SHORT_MAX", 45))
        rsi_cap = float(self.cfg.get("RSI_MAX", 75))
        body_min = float(self.cfg.get("BODY_MIN", 0.60))
        body_ratio = self._body_ratio(l)

        # Swings and breakout
        swings = self._swing_levels(df_b, int(self.cfg.get("LOOKBACK_SWINGS", 20)))
        vol_ok, vol_z = self._volume_ok(
            df_b,
            int(self.cfg.get("VOL_SMA_LEN", 20)),
            float(self.cfg.get("VOL_MULT", 1.5)),
            float(self.cfg.get("VOL_Z_MIN", 1.0)),
        )
        atr_breakout_mult = float(self.cfg.get("ATR_BREAKOUT_MULT", 1.5))
        atr = float(l["atr"]) if pd.notna(l["atr"]) else None
        if atr is None or atr <= 0:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Long/short candidates
        long_ok = trend_up and (l["rsi"] >= rsi_long_min) and (l["rsi"] <= rsi_cap) and (body_ratio >= body_min) and vol_ok
        short_ok = ALLOW_SHORTS and trend_down and (l["rsi"] <= rsi_short_max) and (body_ratio >= body_min) and vol_ok

        side: Optional[str] = None
        # Breakout conditions relative to recent swings with ATR cushion
        if long_ok and swings.get("high") is not None:
            if float(l["close"]) > float(swings["high"]) + atr_breakout_mult * atr * 0.0:  # cushion optional
                side = "long"
        if side is None and short_ok and swings.get("low") is not None:
            if float(l["close"]) < float(swings["low"]) - atr_breakout_mult * atr * 0.0:
                side = "short"
        if side is None:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        # Score with weights
        W_BREAKOUT = float(self.cfg.get("W_BREAKOUT", 0.25))
        W_VOLZ = float(self.cfg.get("W_VOLZ", 0.20))
        W_BODY = float(self.cfg.get("W_BODY", 0.15))
        W_TREND = float(self.cfg.get("W_TREND", 0.20))
        W_RSI = float(self.cfg.get("W_RSI", 0.10))
        W_ATRREG = float(self.cfg.get("W_ATRREG", 0.05))
        W_SWEEP = float(self.cfg.get("W_SWEEP", 0.05))
        # Simple features (0..1)
        f_breakout = 1.0
        f_volz = max(0.0, min(1.0, (vol_z / 3.0)))
        f_body = max(0.0, min(1.0, body_ratio))
        f_trend = 1.0
        # RSI closeness to desired band
        if side == "long":
            f_rsi = max(0.0, min(1.0, float(l["rsi"] - rsi_long_min) / max(1.0, rsi_cap - rsi_long_min)))
        else:
            f_rsi = max(0.0, min(1.0, float(rsi_short_max - l["rsi"]) / max(1.0, rsi_short_max)))
        # ATR regime (prefer mid range)
        atr_reg_lo = float(self.cfg.get("ATR_REGIME_LOW", 0.5))
        atr_reg_hi = float(self.cfg.get("ATR_REGIME_HIGH", 1.5))
        try:
            atr_ma = float(pd.to_numeric(df_b["atr"], errors="coerce").rolling(50).mean().iloc[-2])
        except Exception:
            atr_ma = atr
        base = atr_ma if atr_ma and atr_ma > 0 else atr
        atr_norm = max(0.0, min(1.0, (atr / max(1e-9, base))))
        f_atrreg = 1.0 if (atr_reg_lo <= atr_norm <= atr_reg_hi) else 0.5
        # Sweep detection: last bar takes prior swing and rejects
        f_sweep = 0.0
        try:
            look_sw = int(self.cfg.get("LOOKBACK_SWINGS", 20))
            win = df_b.iloc[-(look_sw + 2) : -2]
            last = df_b.iloc[-2]
            prev_high = float(win["high"].max()) if len(win) > 0 else None
            prev_low = float(win["low"].min()) if len(win) > 0 else None
            if prev_high is not None and prev_low is not None:
                # Long sweep: pierce below prev_low then close strong up
                if (float(last["low"]) < prev_low) and (last["close"] > last["open"]) and (self._body_ratio(last) >= body_min):
                    f_sweep = 1.0 if side == "long" else f_sweep
                # Short sweep: pierce above prev_high then close strong down
                if (float(last["high"]) > prev_high) and (last["close"] < last["open"]) and (self._body_ratio(last) >= body_min):
                    f_sweep = 1.0 if side == "short" else f_sweep
        except Exception:
            f_sweep = 0.0
        score = 100.0 * (
            W_BREAKOUT * f_breakout
            + W_VOLZ * f_volz
            + W_BODY * f_body
            + W_TREND * f_trend
            + W_RSI * f_rsi
            + W_ATRREG * f_atrreg
            + W_SWEEP * f_sweep
        )
        if score < float(self.cfg.get("MIN_SCORE", 70)):
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})

        entry = float(l["close"])

        # Stop-loss: swing-based with ATR buffer and clamps
        sl_mode = str(self.cfg.get("SL_MODE", "swing")).lower()
        atr_buf_mult = float(self.cfg.get("ATR_BUFFER_MULT", 0.5))
        min_sl_pct = float(self.cfg.get("MIN_SL_PCT", 0.10)) / 100.0
        max_sl_pct = float(self.cfg.get("MAX_SL_PCT", 3.00)) / 100.0

        initial_stop: Optional[float] = None
        if sl_mode == "swing":
            if side == "long":
                base = swings.get("low")
                if base is not None:
                    initial_stop = float(base) - atr_buf_mult * atr
            else:
                base = swings.get("high")
                if base is not None:
                    initial_stop = float(base) + atr_buf_mult * atr
        # Fallback to ATR protective
        if initial_stop is None:
            s, _, _ = protective_prices("buy" if side == "long" else "sell", entry, atr, 2.0)
            initial_stop = s
        # Clamp SL distance + min ticks
        sl_dist = abs(entry - initial_stop)
        sl_dist = max(sl_dist, entry * min_sl_pct)
        sl_dist = min(sl_dist, entry * max_sl_pct)
        min_ticks = float(self.cfg.get("MIN_SL_TICKS", 1))
        # Approx tick using price precision proxy (1e-6 fallback)
        tick = max(1e-6, entry * 1e-6)
        sl_dist = max(sl_dist, min_ticks * tick)
        if side == "long":
            initial_stop = entry - sl_dist
        else:
            initial_stop = entry + sl_dist

        # Targets: use R multiples; core supports multi-target
        tp1_r = float(self.cfg.get("TP1_R", 1.0))
        tp2_r = float(self.cfg.get("TP2_R", 2.0))
        tp3_r = float(self.cfg.get("TP3_R", 3.0))
        r = sl_dist
        if side == "long":
            t1 = entry + tp1_r * r
            t2 = entry + tp2_r * r
            t3 = entry + tp3_r * r
        else:
            t1 = entry - tp1_r * r
            t2 = entry - tp2_r * r
            t3 = entry - tp3_r * r

        # Snap targets to SR levels if within tolerance
        if bool(self.cfg.get("SNAP_TO_SR", True)):
            tol = float(self.cfg.get("SNAP_TOL_PCT", 0.20)) / 100.0
            # Use recent swing high/low as SR proxies
            sh = swings.get("high")
            slv = swings.get("low")
            def _snap(val: float, lvl: Optional[float]) -> float:
                if lvl is None:
                    return val
                if lvl > 0 and abs(val - float(lvl)) / float(lvl) <= tol:
                    return float(lvl)
                return val
            if side == "long":
                t1 = _snap(t1, sh)
                t2 = _snap(t2, sh)
                t3 = _snap(t3, sh)
            else:
                t1 = _snap(t1, slv)
                t2 = _snap(t2, slv)
                t3 = _snap(t3, slv)

        # Confidence proxy from score (0..1)
        confidence = max(0.0, min(1.0, score / 100.0))

        # TP sizes: prefer global TARGET_SPLITS; optionally override using TPx_PCT
        if all(k in self.cfg for k in ("TP1_PCT", "TP2_PCT", "TP3_PCT")):
            total = max(1e-9, float(self.cfg.get("TP1_PCT", 50)) + float(self.cfg.get("TP2_PCT", 30)) + float(self.cfg.get("TP3_PCT", 20)))
            splits = [float(self.cfg.get("TP1_PCT", 50))/total, float(self.cfg.get("TP2_PCT", 30))/total, float(self.cfg.get("TP3_PCT", 20))/total]
        else:
            splits = list(TARGET_SPLITS or [0.5, 0.3, 0.2])
        d = Decision(
            symbol,
            self.id,
            side,
            float(score),
            float(confidence),
            entry,
            atr,
            initial_stop,
            t3,
            {"vol_z": vol_z, "body": body_ratio},
            initial_stop=initial_stop,
            targets=[t1, t2, t3],
            splits=splits,
        )

        # Cooldown after recent close/SL
        try:
            cd_bars = int(self.cfg.get("COOLDOWN_BARS_AFTER_SL", 5))
            if cd_bars > 0 and base_tf.endswith("m"):
                minutes = int(base_tf[:-1])
                cd_seconds = cd_bars * minutes * 60
                last_close = STATE.get_last_close_ts(symbol)
                if last_close and (pd.Timestamp.utcnow().timestamp() - last_close) < cd_seconds:
                    return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        except Exception:
            pass

        # Provide follow-through/trailing hints via meta for monitor
        try:
            d.meta = d.meta or {}
            d.meta.update({
                "scalp_follow_through": {
                    "trail_mode": str(self.cfg.get("TRAIL_MODE", "atr")),
                    "atr_mult": float(self.cfg.get("ATR_TRAIL_MULT", 1.0)),
                    "ema_trail_len": int(self.cfg.get("EMA_TRAIL_LEN", 20)),
                    "trail_buffer_ticks": int(self.cfg.get("TRAIL_BUFFER_TICKS", 0)),
                    "follow_through_bars": int(self.cfg.get("FOLLOW_THROUGH_BARS", 3)),
                    "min_follow_through_r": float(self.cfg.get("MIN_FOLLOW_THROUGH_R", 0.5)),
                    "entry_price": float(entry),
                    "sl_dist": float(sl_dist),
                }
            })
        except Exception:
            pass

        return d


