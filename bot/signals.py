from .config import ALLOW_SHORTS, EMA_SLOW, RSI_PERIOD, RSI_LONG_MIN, RSI_SHORT_MAX, MIN_ADX


def trend_and_signal(ltf, htf):
    """
    Multi-timeframe confluence with ADX:
      LONG:  LTF ema_fast>ema_slow & RSI>=RSI_LONG_MIN & ADX>=MIN_ADX
             AND HTF ema_fast>ema_slow & ADX>=MIN_ADX
      SHORT: LTF ema_fast<ema_slow & RSI<=RSI_SHORT_MAX & ADX>=MIN_ADX
             AND HTF ema_fast<ema_slow & ADX>=MIN_ADX
    """
    if len(ltf) < max(EMA_SLOW, RSI_PERIOD) + 2 or len(htf) < max(EMA_SLOW, RSI_PERIOD) + 2:
        return "none", None

    l = ltf.iloc[-2]  # last closed candle
    h = htf.iloc[-2]
    if not (ltf.notna().iloc[-2][["ema_fast","ema_slow","rsi","atr","adx"]].all() and htf.notna().iloc[-2][["ema_fast","ema_slow","rsi","atr","adx"]].all()):
        return "none", None

    long_ok  = (l["ema_fast"] > l["ema_slow"]) and (l["rsi"] >= RSI_LONG_MIN)  and (l["adx"] >= MIN_ADX) \
               and (h["ema_fast"] > h["ema_slow"]) and (h["adx"] >= MIN_ADX)

    short_ok = (l["ema_fast"] < l["ema_slow"]) and (l["rsi"] <= RSI_SHORT_MAX) and (l["adx"] >= MIN_ADX) \
               and (h["ema_fast"] < h["ema_slow"]) and (h["adx"] >= MIN_ADX)

    if long_ok:
        return "up", "long"
    if ALLOW_SHORTS and short_ok:
        return "down", "short"
    return "none", None


def score_signal(side, lrow):
    # Rank stronger trends first: EMA separation + ADX + RSI distance from 50 (favor momentum)
    ema_gap = abs(lrow["ema_fast"] - lrow["ema_slow"]) / max(1e-9, abs(lrow["ema_slow"]))
    rsi_term = (lrow["rsi"] - 50) if side == "long" else (50 - lrow["rsi"])
    score = float(ema_gap * 1000 + max(0.0, rsi_term) + max(0.0, lrow["adx"] - MIN_ADX))
    return score


