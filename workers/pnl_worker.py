import threading
import time

from config import PNL_MONITOR_SECONDS
from utils import log
from state import STATE


def _fetch_symbol_price(ex, symbol: str) -> float:
    try:
        t = ex.fetch_ticker(symbol)
        p = t.get("last") or t.get("close") or t.get("info", {}).get("lastPrice")
        return float(p) if p is not None else None
    except Exception:
        return None


def _estimate_pnl_usdt(positions: dict, price_lookup: callable) -> dict:
    pnl = {}
    for sym, pos in positions.items():
        size = float(pos.get("size", 0))
        side = pos.get("side")
        if size <= 0 or side not in ("long", "short"):
            continue
        last = price_lookup(sym)
        entry = pos.get("entryPrice")
        if last is None or entry is None:
            continue
        diff = (last - entry) if side == "long" else (entry - last)
        pnl[sym] = diff * size
    return pnl


def loop(ex, get_positions_callable, get_symbols_callable):
    while True:
        try:
            STATE.set_thread_status("pnl_worker", {"status": "running"})
            positions = get_positions_callable()
            try:
                STATE.set_positions(positions)
            except Exception:
                pass

            # Refresh prices for active symbols (positions + universe from state)
            symbols = set(positions.keys())
            try:
                snap_for_syms = STATE.snapshot()
                symbols.update(snap_for_syms.get("universe", []) or [])
            except Exception:
                pass
            for sym in list(symbols)[:50]:
                price = _fetch_symbol_price(ex, sym)
                if price is not None:
                    try:
                        STATE.set_price(sym, price)
                    except Exception:
                        pass

            snap = STATE.snapshot()
            pnl = _estimate_pnl_usdt(positions, lambda s: snap["prices"].get(s))
            try:
                STATE.set_pnl(pnl)
            except Exception:
                pass
            log("[PNLWorker] tick symbols=", len(symbols), "positions=", len(positions), "pnl_total=", round(sum(pnl.values()) if pnl else 0.0, 4))

            time.sleep(PNL_MONITOR_SECONDS)
        except Exception as e:
            log("[PNL Worker] error:", str(e))
            time.sleep(PNL_MONITOR_SECONDS)


def start(ex, get_positions_callable, get_symbols_callable) -> threading.Thread:
    t = threading.Thread(target=loop, args=(ex, get_positions_callable, get_symbols_callable), daemon=True)
    t.start()
    return t


