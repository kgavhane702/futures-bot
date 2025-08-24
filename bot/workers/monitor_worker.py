import threading
import time

from ..config import MONITOR_SECONDS, UNIVERSE_SIZE, ORPHAN_PROTECT_SECONDS, ORPHAN_MIN_AGE_SECONDS
from ..utils import log
from ..state import STATE
from ..market_data import top_usdt_perps


def _fetch_symbol_price(ex, symbol: str) -> float:
    try:
        t = ex.fetch_ticker(symbol)
        info = t.get("info", {}) or {}
        # Prefer mark price for PnL approximation; fallback to last/close
        p = info.get("markPrice") or t.get("last") or t.get("close") or info.get("lastPrice")
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

            # Phase C: Orphan cleanup and SL adjustments based on TP stages
            symbols_without_pos = [s for s in symbols if s not in positions]
            cancelled = _cancel_orphans(ex, symbols_without_pos)
            if cancelled:
                log("[Monitor] orphan cancelled count:", cancelled)

            # Adjust SL after partial TPs if needed
            try:
                for sym, pos in positions.items():
                    meta = STATE.get_strategy_meta(sym)
                    stage = STATE.get_exit_stage(sym)
                    # Follow-through / early-exit (scalping): if configured in meta
                    try:
                        ft_cfg = meta.get("scalp_follow_through")
                        if ft_cfg and sym in positions:
                            side = positions.get(sym, {}).get("side")
                            size = float(positions.get(sym, {}).get("size", 0))
                            if size > 0 and side in ("long", "short"):
                                # Trail SL per config
                                try:
                                    trail_mode = ft_cfg.get("trail_mode", "atr")
                                    atr_mult = float(ft_cfg.get("atr_mult", 1.0))
                                    entry = float(ft_cfg.get("entry_price", 0))
                                    sl_dist = float(ft_cfg.get("sl_dist", 0))
                                    last = STATE.snapshot().get("prices", {}).get(sym)
                                    if isinstance(last, (int, float)) and last > 0:
                                        if trail_mode == "atr":
                                            # Approximate ATR trail using sl_dist / ATR_MULT_SL ratio
                                            # Adjust SL a fraction toward price as it moves
                                            new_sl = None
                                            if side == "long":
                                                target = last - atr_mult * max(1e-9, sl_dist)
                                                new_sl = max(entry, target)
                                                side_o = "sell"
                                            else:
                                                target = last + atr_mult * max(1e-9, sl_dist)
                                                new_sl = min(entry, target)
                                                side_o = "buy"
                                            try:
                                                from ..orders import cancel_reduce_only_stop_orders
                                                cancel_reduce_only_stop_orders(ex, sym)
                                            except Exception:
                                                pass
                                            try:
                                                ex.create_order(sym, "STOP_MARKET", side_o, size, params={"reduceOnly": True, "stopPrice": float(new_sl)})
                                                log("[Monitor] trailing SL (scalp)", sym, new_sl)
                                            except Exception as e:
                                                log("[Monitor] trail place fail", sym, str(e))
                                except Exception:
                                    pass
                            # Early-exit (follow-through): if price fails to reach min R within N bars from entry ts
                            try:
                                ft_bars = int(ft_cfg.get("follow_through_bars", 3))
                                min_r = float(ft_cfg.get("min_follow_through_r", 0.5))
                                # If we had timestamps per entry, could compute bar count; simplified no-op for now
                            except Exception:
                                pass
                    except Exception:
                        pass
                    if not meta:
                        continue
                    # Check open reduce-only TPs to infer filled stages
                    try:
                        ro = ex.fetch_open_orders(sym)
                    except Exception:
                        ro = []
                    # Count remaining TP orders
                    remaining_tps = [o for o in ro if o.get("reduceOnly") and "TAKE_PROFIT" in (o.get("type", "").upper())]
                    total_expected = len(meta.get("targets", [])[:3])
                    if total_expected == 0:
                        continue
                    remaining = len(remaining_tps)
                    # Initialize baseline tp_remaining in meta and skip adjustments on first observation
                    prev_tp_rem = meta.get("tp_remaining")
                    if prev_tp_rem is None:
                        try:
                            cur = dict(meta)
                            cur["tp_remaining"] = remaining
                            STATE.set_strategy_meta(sym, cur)
                        except Exception:
                            pass
                        continue
                    # No change → no adjustment
                    if remaining >= prev_tp_rem:
                        continue
                    # One or more TP orders filled
                    fills_now = max(0, prev_tp_rem - remaining)
                    new_stage = min(total_expected, stage + fills_now)
                    log("[Monitor] TP fill detected", sym, f"prev_remaining={prev_tp_rem}", f"now={remaining}", f"stage {stage}->{new_stage}")
                    # Update meta with new tp_remaining
                    try:
                        cur = dict(meta)
                        cur["tp_remaining"] = remaining
                        STATE.set_strategy_meta(sym, cur)
                    except Exception:
                        pass
                    # Adjust SL per stage transitions
                    adj_sl = None
                    if new_stage >= 1 and stage < 1:
                        # After TP1 -> move SL to breakeven (entry)
                        entry_proxy = meta.get("entry") or positions.get(sym, {}).get("entryPrice") or STATE.snapshot().get("prices", {}).get(sym)
                        if entry_proxy:
                            adj_sl = float(entry_proxy)
                    if new_stage >= 2 and stage < 2:
                        # After TP2 -> move SL to TP1
                        t1s = meta.get("targets") or []
                        if t1s:
                            adj_sl = float(t1s[0])
                    if new_stage >= total_expected:
                        # All TPs filled; position should be closed by TPs soon
                        try:
                            STATE.set_exit_stage(sym, new_stage)
                            STATE.mark_close(sym)
                        except Exception:
                            pass
                        log("[Monitor] all TPs filled; awaiting position closure", sym)
                        continue
                    if adj_sl is not None and pos.get("size", 0) > 0:
                        # Cancel only SLs, keep TPs
                        try:
                            from ..orders import cancel_reduce_only_stop_orders
                            cancel_reduce_only_stop_orders(ex, sym)
                        except Exception:
                            pass
                        try:
                            side = "sell" if pos.get("side")=="long" else "buy"
                            # Avoid immediate trigger
                            last = STATE.snapshot().get("prices", {}).get(sym)
                            if isinstance(last, (int, float)):
                                if side == "sell" and adj_sl >= last:
                                    adj_sl = last * 0.999
                                elif side == "buy" and adj_sl <= last:
                                    adj_sl = last * 1.001
                            ex.create_order(sym, "STOP_MARKET", side, pos.get("size", 0), params={"reduceOnly": True, "stopPrice": float(adj_sl)})
                            log("[Monitor] SL adjusted", sym, adj_sl)
                        except Exception as e:
                            log("[Monitor] SL adjust fail", sym, str(e))
                    try:
                        STATE.set_exit_stage(sym, new_stage)
                    except Exception:
                        pass
            except Exception as e:
                log("[Monitor] stage adjust error:", str(e))

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


