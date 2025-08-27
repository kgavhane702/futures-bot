import ccxt

from .config import EXCHANGE_ID, API_KEY, API_SECRET, USE_TESTNET, LEVERAGE, MARGIN_MODE
from .utils import log


def exchange():
    klass = getattr(ccxt, EXCHANGE_ID)
    ex = klass({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "enableRateLimit": True,
        "options": {
            "defaultType": "future",
            "adjustForTimeDifference": True,
        },
    })
    try:
        ex.set_sandbox_mode(USE_TESTNET)
        log("Sandbox mode:", USE_TESTNET)
    except Exception:
        pass
    return ex


def set_leverage_and_margin(ex, symbol: str):
    try:
        if hasattr(ex, "set_leverage"):
            ex.set_leverage(LEVERAGE, symbol=symbol)
            log("set_leverage ok", symbol, LEVERAGE)
    except Exception as e:
        log("set_leverage failed", symbol, str(e))
    try:
        if hasattr(ex, "set_margin_mode"):
            ex.set_margin_mode(MARGIN_MODE, symbol=symbol)
            log("set_margin_mode ok", symbol, MARGIN_MODE)
    except Exception as e:
        log("set_margin_mode failed", symbol, str(e))


