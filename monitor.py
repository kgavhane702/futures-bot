import threading
import time

from bot.config import MONITOR_SECONDS, DRY_RUN
from bot.utils import log
from bot.state import STATE


def _fetch_symbol_price(ex, symbol: str) -> float:
    try:
        t = ex.fetch_ticker(symbol)
        p = t.get("last") or t.get("close") or t.get("info", {}).get("lastPrice")
        price = float(p) if p is not None else None
        if price is not None:
            try:
                STATE.set_price(symbol, price)
            except Exception:
                pass
        return price
    except Exception:
        return None


def _estimate_pnl_usdt(ex, positions: dict) -> dict:
    pnl = {}
    for sym, pos in positions.items():
        size = float(pos.get("size", 0))
        side = pos.get("side")
        if size <= 0 or side not in ("long", "short"):
            continue
        last = _fetch_symbol_price(ex, sym)
        entry = pos.get("entryPrice")
        if last is None or entry is None:
            continue
        diff = (last - entry) if side == "long" else (entry - last)
        pnl[sym] = diff * size
    return pnl


def _cancel_orphan_reduce_only(ex, symbol: str, has_position: bool):
    try:
        orders = ex.fetch_open_orders(symbol)
    except Exception:
        orders = []
    for o in orders:
        reduce_only = bool(o.get("reduceOnly"))
        if reduce_only and not has_position:
            try:
                ex.cancel_order(o["id"], symbol)
                log("Cancelled orphan reduceOnly order", symbol, o.get("id"))
            except Exception:
                pass


def monitor_loop(ex, get_positions_callable):
    while True:
        try:
            positions = get_positions_callable()
            # PnL snapshot
            pnl = _estimate_pnl_usdt(ex, positions)
            if pnl:
                total = sum(pnl.values())
                log("[Monitor] PnL USDT:", {k: round(v, 4) for k, v in pnl.items()}, "total=", round(total, 4))
            try:
                STATE.set_positions(positions)
                STATE.set_pnl(pnl)
            except Exception:
                pass

            # Orphan reduce-only orders cleanup
            # If a symbol has no position, cancel reduce-only SL/TP left behind
            try:
                markets = list(positions.keys())
                # If no positions, still scan top markets quickly (use exchange markets keys)
                if not markets:
                    markets = [s for s, m in ex.markets.items() if m.get("swap") and m.get("linear") and m.get("quote") == "USDT"][:20]
            except Exception:
                markets = []
            for sym in markets:
                _cancel_orphan_reduce_only(ex, sym, sym in positions)

            time.sleep(MONITOR_SECONDS)
        except Exception as e:
            log("[Monitor] error:", str(e))
            time.sleep(MONITOR_SECONDS)


def start_monitor_thread(ex, get_positions_callable) -> threading.Thread:
    t = threading.Thread(target=monitor_loop, args=(ex, get_positions_callable), daemon=True)
    t.start()
    return t


