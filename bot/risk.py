import math

from .config import (
    ACCOUNT_EQUITY_USDT,
    RISK_PER_TRADE,
    ABS_RISK_USDT,
    LEVERAGE,
    MAX_NOTIONAL_FRACTION,
    MARGIN_BUFFER_FRAC,
    ATR_MULT_SL,
    TP_R_MULT,
)


def equity_from_balance(ex) -> float:
    try:
        b = ex.fetch_balance()
        total = b.get("total", {}).get("USDT", None)
        if total is not None:
            return float(total)
    except Exception:
        pass
    return ACCOUNT_EQUITY_USDT


def compute_risk_usdt(equity_usdt: float) -> float:
    if ABS_RISK_USDT and ABS_RISK_USDT > 0:
        return float(ABS_RISK_USDT)
    return max(1.0, equity_usdt * RISK_PER_TRADE)


def size_position(entry: float, stop: float, equity_usdt: float) -> float:
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return 0.0
    risk_usdt = compute_risk_usdt(equity_usdt)
    qty_risk = risk_usdt / stop_dist

    notional_cap = equity_usdt * LEVERAGE * MAX_NOTIONAL_FRACTION * MARGIN_BUFFER_FRAC
    qty_cap = notional_cap / entry if entry > 0 else 0.0
    qty = min(qty_risk, qty_cap)
    return max(qty, 0.0)


def round_qty(ex, symbol: str, qty: float) -> float:
    try:
        return float(ex.amount_to_precision(symbol, qty))
    except Exception:
        m = ex.market(symbol)
        step = (m.get("limits", {}).get("amount", {}).get("min", 0)) or 0.0001
        return math.floor(qty / step) * step


def protective_prices(side: str, entry: float, atr: float, r_mult: float = TP_R_MULT):
    if side == "buy":
        stop = entry - ATR_MULT_SL * atr
        r = entry - stop
        tp = entry + r_mult * r
    else:
        stop = entry + ATR_MULT_SL * atr
        r = stop - entry
        tp = entry - r_mult * r
    return stop, tp, r


