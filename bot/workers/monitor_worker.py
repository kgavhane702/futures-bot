import threading
import time

from ..config import MONITOR_SECONDS, UNIVERSE_SIZE, ORPHAN_PROTECT_SECONDS, ORPHAN_MIN_AGE_SECONDS
from ..utils import log
from ..state import STATE
from ..market_data import top_usdt_perps


def _fetch_symbol_price(ex, symbol: str) -> float:
    try:
        t = ex.fetch_ticker(symbol)
        p = t.get("last") or t.get("close") or t.get("info", {}).get("lastPrice")
        return float(p) if p is not None else None
    except Exception:
        return None


def _get_positions(ex) -> dict:
    try:
        poss = ex.fetch_positions()
        open_map = {}
        for p in poss:
            sym = p.get("symbol")
            amt = p.get("contracts") or p.get("positionAmt") or p.get("contractsAmount")
            sz = float(amt or 0)
            side = "long" if sz > 0 else "short" if sz < 0 else None
            if side:
                entry_price = p.get("entryPrice") or p.get("info", {}).get("entryPrice")
                try:
                    entry_price = float(entry_price) if entry_price is not None else None
                except Exception:
                    entry_price = None
                open_map[sym] = {"side": side, "size": abs(sz), "entryPrice": entry_price}
        return open_map
    except Exception:
        return {}


def _estimate_pnl_usdt(positions: dict, prices: dict) -> dict:
    pnl = {}
    for sym, pos in positions.items():
        size = float(pos.get("size", 0))
        side = pos.get("side")
        entry = pos.get("entryPrice")
        last = prices.get(sym)
        if size <= 0 or side not in ("long", "short") or entry is None or last is None:
            continue
        diff = (last - entry) if side == "long" else (entry - last)
        pnl[sym] = diff * size
    return pnl


def _cancel_orphans(ex, symbols_without_pos):
    cancelled = 0
    for sym in symbols_without_pos:
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
                cancelled += 1
                log("[Monitor] orphan cancelled", sym, o.get("id"))
            except Exception:
                pass
    return cancelled


def loop(ex):
    while True:
        try:
            STATE.set_thread_status("monitor_worker", {"status": "running"})

            # Phase A: Universe refresh
            try:
                universe = top_usdt_perps(ex, UNIVERSE_SIZE)
                if universe:
                    STATE.set_universe(universe)
            except Exception:
                universe = []

            # Phase B: Positions and prices
            positions = _get_positions(ex)
            STATE.set_positions(positions)

            symbols = set(positions.keys())
            symbols.update(STATE.snapshot().get("universe", []) or [])

            prices = {}
            for sym in list(symbols)[:60]:
                price = _fetch_symbol_price(ex, sym)
                if price is not None:
                    prices[sym] = price
                    STATE.set_price(sym, price)

            # Phase C: Orphan cleanup
            symbols_without_pos = [s for s in symbols if s not in positions]
            cancelled = _cancel_orphans(ex, symbols_without_pos)

            # Phase D: PnL compute
            pnl = _estimate_pnl_usdt(positions, prices or STATE.snapshot().get("prices", {}))
            STATE.set_pnl(pnl)

            total = round(sum(pnl.values()) if pnl else 0.0, 4)
            log("[Monitor] tick universe=", len(universe or []), "positions=", len(positions), "orphans_cancelled=", cancelled, "pnl_total=", total)

            time.sleep(MONITOR_SECONDS)
        except Exception as e:
            log("[Monitor] error:", str(e))
            time.sleep(MONITOR_SECONDS)


def start(ex) -> threading.Thread:
    t = threading.Thread(target=loop, args=(ex,), daemon=True)
    t.start()
    return t


