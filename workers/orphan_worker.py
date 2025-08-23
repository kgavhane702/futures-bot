import threading
import time

from config import ORPHAN_MONITOR_SECONDS, ORPHAN_PROTECT_SECONDS, ORPHAN_MIN_AGE_SECONDS
from utils import log
from state import STATE


def _all_usdt_perp_symbols(ex):
    try:
        ex.load_markets()
    except Exception:
        pass
    syms = []
    for s, m in ex.markets.items():
        if m.get("swap") and m.get("linear") and m.get("quote") == "USDT":
            syms.append(s)
    return syms


def _cancel_orphans_for_symbol(ex, symbol: str, has_position: bool):
    try:
        orders = ex.fetch_open_orders(symbol)
    except Exception:
        orders = []
    for o in orders:
        reduce_only = bool(o.get("reduceOnly"))
        if reduce_only and not has_position:
            try:
                ex.cancel_order(o["id"], symbol)
                log("[Orphan] cancelled reduceOnly", symbol, o.get("id"))
            except Exception:
                pass


def loop(ex, get_positions_callable):
    # Scan all USDT perps and cancel reduce-only exits with no corresponding positions
    all_symbols = _all_usdt_perp_symbols(ex)
    while True:
        try:
            STATE.set_thread_status("orphan_worker", {"status": "running"})
            pos = get_positions_callable() or {}
            pos_syms = set(pos.keys())
            if not all_symbols:
                all_symbols = _all_usdt_perp_symbols(ex)
            for sym in all_symbols:
                if sym in pos_syms:
                    continue
                # Protect just-placed exits
                try:
                    if STATE.is_exits_protected(sym, ORPHAN_PROTECT_SECONDS):
                        continue
                except Exception:
                    pass
                try:
                    orders = ex.fetch_open_orders(sym)
                except Exception:
                    orders = []
                for o in orders:
                    if not o.get("reduceOnly"):
                        continue
                    ts = o.get("timestamp")
                    if ts is not None:
                        age = (time.time() - ts/1000.0)
                        if age < ORPHAN_MIN_AGE_SECONDS:
                            continue
                    try:
                        ex.cancel_order(o["id"], sym)
                        log("[OrphanWorker] cancelled", sym, o.get("id"))
                    except Exception:
                        pass
            time.sleep(ORPHAN_MONITOR_SECONDS)
        except Exception as e:
            log("[Orphan Worker] error:", str(e))
            time.sleep(ORPHAN_MONITOR_SECONDS)


def start(ex, get_positions_callable) -> threading.Thread:
    t = threading.Thread(target=loop, args=(ex, get_positions_callable), daemon=True)
    t.start()
    return t


