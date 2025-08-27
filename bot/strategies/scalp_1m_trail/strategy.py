from typing import Dict, Optional
import time
import pandas as pd

from ..base import Strategy, Decision


class Scalp1mTrailStrategy(Strategy):
    id = "scalp_1m_trail"

    def required_timeframes(self) -> Dict[str, int]:
        return {"1m": int(self.cfg.get("LOOKBACK", 300))}

    def decide(self, symbol: str, data: Dict[str, pd.DataFrame]) -> Decision:
        df = data.get("1m")
        if df is None or len(df) < 50:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        from ta.trend import EMAIndicator
        from ta.momentum import RSIIndicator
        close = df["close"]
        ema_fast = EMAIndicator(close, window=int(self.cfg.get("EMA_FAST", 9))).ema_indicator()
        ema_slow = EMAIndicator(close, window=int(self.cfg.get("EMA_SLOW", 21))).ema_indicator()
        rsi = RSIIndicator(close, window=int(self.cfg.get("RSI_LEN", 14))).rsi()
        l = df.iloc[-2]
        ef = float(ema_fast.iloc[-2])
        es = float(ema_slow.iloc[-2])
        side = None
        if ef > es:
            side = "long"
        elif ef < es:
            side = "short"
        if side is None:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        entry = float(l["close"])
        atr_len = int(self.cfg.get("ATR_LEN", 14))
        from ta.volatility import AverageTrueRange
        atr = AverageTrueRange(df["high"], df["low"], df["close"], window=atr_len).average_true_range().iloc[-2]
        if pd.isna(atr) or atr <= 0:
            return Decision(symbol, self.id, None, 0.0, 0.0, None, None, None, None, {})
        # Initial SL at 1%
        sl_pct = float(self.cfg.get("SL_INIT_PCT", 1.0)) / 100.0
        if side == "long":
            sl = entry * (1.0 - sl_pct)
        else:
            sl = entry * (1.0 + sl_pct)
        # Unlimited target: we still provide a placeholder t3=entry for API but we rely on trailing in worker
        return Decision(symbol, self.id, side, 80.0, 0.8, entry, float(atr), float(sl), float(entry), {}, initial_stop=float(sl), targets=[entry, entry, entry], splits=[1.0, 0.0, 0.0])


