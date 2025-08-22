from .config import RSI_LONG_MIN, RSI_SHORT_MAX, MIN_ADX, ALLOW_SHORTS

def trend_and_signal(ltf, htf, valid_row):
    min_len = 202
    if len(ltf) < min_len or len(htf) < min_len:
        return "none", None
    l = ltf.iloc[-2]
    h = htf.iloc[-2]
    if not (valid_row(l) and valid_row(h)):
        return "none", None

    long_ok  = (l["ema_fast"] > l["ema_slow"]) and (l["rsi"] >= RSI_LONG_MIN)  and (l["adx"] >= MIN_ADX)                and (h["ema_fast"] > h["ema_slow"]) and (h["adx"] >= MIN_ADX)

    short_ok = (l["ema_fast"] < l["ema_slow"]) and (l["rsi"] <= RSI_SHORT_MAX) and (l["adx"] >= MIN_ADX)                and (h["ema_fast"] < h["ema_slow"]) and (h["adx"] >= MIN_ADX)

    if long_ok:
        return "up", "long"
    if ALLOW_SHORTS and short_ok:
        return "down", "short"
    return "none", None

def score_signal(side, lrow):
    ema_gap = abs(lrow["ema_fast"] - lrow["ema_slow"]) / max(1e-9, abs(lrow["ema_slow"]))
    rsi_term = (lrow["rsi"] - 50) if side == "long" else (50 - lrow["rsi"])
    score = float(ema_gap * 1000 + max(0.0, rsi_term) + max(0.0, lrow["adx"] - MIN_ADX))
    return score
