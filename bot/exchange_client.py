import time, random, string
import ccxt
from .config import EXCHANGE_ID, API_KEY, API_SECRET, USE_TESTNET, LEVERAGE, MARGIN_MODE, HEDGE_MODE
from .logging_utils import log

def get_exchange():
    klass = getattr(ccxt, EXCHANGE_ID)
    ex = klass({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "enableRateLimit": True,
        "options": {
            "defaultType": "future",     # Futures only
            "adjustForTimeDifference": True
        }
    })
    try:
        ex.set_sandbox_mode(USE_TESTNET)
        log("Sandbox mode:", USE_TESTNET)
    except Exception:
        pass
    # Only attempt to change position mode if hedge mode requested
    if HEDGE_MODE:
        try:
            if hasattr(ex, "set_position_mode"):
                ex.set_position_mode(hedged=True)
                log("set_position_mode ok", True)
        except Exception as e:
            log("set_position_mode failed", EXCHANGE_ID, str(e))
    return ex

def ensure_symbol_config(ex, symbol):
    ok = True
    try:
        if hasattr(ex, "set_leverage"):
            ex.set_leverage(LEVERAGE, symbol=symbol)
            log("set_leverage ok", symbol, LEVERAGE)
    except Exception as e:
        log("set_leverage failed", symbol, str(e))
        ok = False
    try:
        if hasattr(ex, "set_margin_mode"):
            ex.set_margin_mode(MARGIN_MODE, symbol=symbol)
            log("set_margin_mode ok", symbol, MARGIN_MODE)
    except Exception as e:
        log("set_margin_mode failed", symbol, str(e))
        ok = False
    return ok

def round_amount(ex, symbol, qty: float) -> float:
    try:
        return float(ex.amount_to_precision(symbol, qty))
    except Exception:
        m = ex.market(symbol)
        step = (m.get("limits", {}).get("amount", {}).get("min", 0)) or 0.0001
        from math import floor
        return floor(qty / step) * step

def round_price(ex, symbol, price: float) -> float:
    try:
        return float(ex.price_to_precision(symbol, price))
    except Exception:
        return float(price)

def new_client_id(prefix="bot"):
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{prefix}_{int(time.time()*1000)}_{suffix}"

def get_price_increment(ex, symbol) -> float:
    try:
        m = ex.market(symbol)
        # Try filters (Binance-like)
        flt = (m.get("info", {}) or {}).get("filters", [])
        for f in flt:
            if f.get("filterType") == "PRICE_FILTER":
                ts = f.get("tickSize")
                if ts:
                    return float(ts)
        # Try precision -> derive increment
        prec = (m.get("precision", {}) or {}).get("price")
        if prec is not None:
            return 10 ** (-int(prec))
        # Try limits
        inc = (m.get("limits", {}) or {}).get("price", {}).get("min")
        if inc:
            return float(inc)
    except Exception:
        pass
    return 1e-6
