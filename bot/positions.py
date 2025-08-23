from .utils import log
import time


def get_open_positions(ex):
    try:
        poss = ex.fetch_positions()
        open_map = {}
        for p in poss:
            sym = p.get("symbol")
            amt = p.get("contracts") or p.get("positionAmt") or p.get("contractsAmount")
            sz = float(amt or 0)
            side = "long" if sz > 0 else "short" if sz < 0 else None
            if side:
                # Try to grab entry price if present
                entry_price = p.get("entryPrice") or p.get("info", {}).get("entryPrice")
                try:
                    entry_price = float(entry_price) if entry_price is not None else None
                except Exception:
                    entry_price = None
                open_map[sym] = {"side": side, "size": abs(sz), "entryPrice": entry_price}
        return open_map
    except Exception as e:
        log("fetch_positions failed:", str(e))
        return {}


def wait_for_position_visible(ex, symbol: str, timeout_seconds: float = 8.0, poll_seconds: float = 0.5):
    """Polls the exchange until a position for symbol becomes visible or timeout is reached.
    Returns the latest positions map (may or may not include the symbol).
    """
    start = time.time()
    last = {}
    while time.time() - start < timeout_seconds:
        try:
            last = get_open_positions(ex)
            if symbol in last:
                return last
        except Exception:
            pass
        time.sleep(max(0.1, poll_seconds))
    return last


