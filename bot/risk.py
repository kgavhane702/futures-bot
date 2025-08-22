from .config import ACCOUNT_EQUITY_USDT, RISK_PER_TRADE, ABS_RISK_USDT,                     LEVERAGE, MAX_NOTIONAL_FRACTION, MARGIN_BUFFER_FRAC

def equity_from_balance(ex):
    try:
        b = ex.fetch_balance()
        free = b.get("free", {}).get("USDT", None)
        if free is not None:
            return float(free)
        total = b.get("total", {}).get("USDT", None)
        if total is not None:
            return float(total)
    except Exception:
        pass
    return ACCOUNT_EQUITY_USDT

def compute_risk_usdt(equity_usdt):
    if ABS_RISK_USDT and ABS_RISK_USDT > 0:
        return float(ABS_RISK_USDT)
    return max(1.0, equity_usdt * RISK_PER_TRADE)

def size_position(entry, stop, equity_usdt):
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return 0.0
    risk_usdt = compute_risk_usdt(equity_usdt)
    qty_risk = risk_usdt / stop_dist
    notional_cap = equity_usdt * LEVERAGE * MAX_NOTIONAL_FRACTION * MARGIN_BUFFER_FRAC
    qty_cap = notional_cap / entry if entry > 0 else 0.0
    qty = min(qty_risk, qty_cap)
    return max(qty, 0.0)

def protective_prices(side, entry, atr, ATR_MULT_SL, TP_R_MULT):
    if side == "buy":
        stop = entry - ATR_MULT_SL * atr
        r = entry - stop
        tp = entry + TP_R_MULT * r
    else:
        stop = entry + ATR_MULT_SL * atr
        r = stop - entry
        tp = entry - TP_R_MULT * r
    return stop, tp, r
