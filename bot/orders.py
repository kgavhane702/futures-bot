import time

from .config import DRY_RUN, MIN_NOTIONAL_USDT, BREAKEVEN_AFTER_R, TRAIL_AFTER_R, TRAIL_ATR_MULT, ATR_MULT_SL
from .risk import round_qty
from .state import STATE
from .utils import log


def get_open_orders(ex, symbol):
    try:
        return ex.fetch_open_orders(symbol)
    except Exception:
        return []


def get_all_open_orders(ex, symbols):
    all_orders = {}
    for sym in symbols:
        try:
            all_orders[sym] = ex.fetch_open_orders(sym)
        except Exception:
            all_orders[sym] = []
    return all_orders


def cancel_reduce_only_orders(ex, symbol):
    try:
        for o in get_open_orders(ex, symbol):
            if o.get("reduceOnly"):
                try:
                    ex.cancel_order(o["id"], symbol)
                except Exception:
                    pass
    except Exception:
        pass


def cancel_reduce_only_stop_orders(ex, symbol):
    """Cancel only reduce-only stop (SL) orders, keep take-profit orders intact."""
    try:
        for o in get_open_orders(ex, symbol):
            if not o.get("reduceOnly"):
                continue
            t = (o.get("type") or "").upper()
            if "TAKE_PROFIT" in t:
                continue
            try:
                ex.cancel_order(o["id"], symbol)
            except Exception:
                pass
    except Exception:
        pass

def place_bracket_orders(ex, symbol, side, qty, entry_price, sl_price, tp_price):
    opposite = "sell" if side == "buy" else "buy"

    notional = qty * entry_price
    if notional < MIN_NOTIONAL_USDT:
        log(f"SKIP {symbol}: notional {notional:.2f} < min {MIN_NOTIONAL_USDT:.2f}")
        return {"id": "skip_notional"}

    log(f"ORDER PREVIEW {symbol} side={side} qty={qty:.6f} entry≈{entry_price:.6f} "
        f"notional≈{notional:.2f} SL={sl_price:.6f} TP={tp_price:.6f}")

    if DRY_RUN:
        log(f"[DRY_RUN] ENTRY {side.upper()} {qty} {symbol} @~{entry_price}")
        log(f"[DRY_RUN] SL reduceOnly {opposite.upper()} @ {sl_price}")
        log(f"[DRY_RUN] TP reduceOnly {opposite.upper()} @ {tp_price}")
        return {"id": f"dry_{int(time.time())}"}

    entry = ex.create_order(symbol, type="market", side=side, amount=qty)
    entry_id = entry.get("id") or entry.get("orderId") or ""
    log("ENTRY", entry_id, side, qty, symbol)
    try:
        STATE.mark_entry(symbol)
    except Exception:
        pass

    params = {"reduceOnly": True}
    try:
        ex.create_order(symbol, type="STOP_MARKET", side=opposite, amount=qty,
                        params={**params, "stopPrice": float(sl_price)})
        log("SL placed", sl_price)
        try:
            STATE.mark_exits_placed(symbol)
        except Exception:
            pass
    except Exception as e:
        log("Failed to place SL:", str(e))
    try:
        ex.create_order(symbol, type="TAKE_PROFIT_MARKET", side=opposite, amount=qty,
                        params={**params, "stopPrice": float(tp_price)})
        log("TP placed", tp_price)
    except Exception as e:
        log("Failed to place TP:", str(e))
    return entry


def place_multi_target_orders(ex, symbol: str, side: str, qty: float, entry_price: float,
                              initial_sl: float, targets: list, splits: list):
    """Place entry, 3 reduce-only TPs (partial) and initial SL."""
    opposite = "sell" if side == "buy" else "buy"
    if DRY_RUN:
        log(f"[DRY_RUN] ENTRY {side.upper()} {qty} {symbol} @~{entry_price}")
        for i, (t, s) in enumerate(zip(targets, splits), start=1):
            log(f"[DRY_RUN] TP{i} reduceOnly {opposite.upper()} {qty * float(s):.6f} @ {t}")
        log(f"[DRY_RUN] SL reduceOnly {opposite.upper()} @ {initial_sl}")
        try:
            STATE.mark_entry(symbol)
        except Exception:
            pass
        return {"id": f"dry_{int(time.time())}"}

    entry = ex.create_order(symbol, type="market", side=side, amount=qty)
    log("ENTRY", entry.get("id"), side, qty, symbol)
    try:
        STATE.mark_entry(symbol)
    except Exception:
        pass

    params = {"reduceOnly": True}
    try:
        sl = ex.create_order(symbol, type="STOP_MARKET", side=opposite, amount=qty,
                             params={**params, "stopPrice": float(initial_sl)})
        log("SL placed", initial_sl, (sl.get("id") or sl.get("orderId") or ""))
        try:
            STATE.mark_exits_placed(symbol)
        except Exception:
            pass
    except Exception as e:
        log("Failed to place SL:", str(e))
    # Compute rounded partial quantities respecting precision and min step
    # Limit number of partials by min amount step, always ensure a TP at the final target exists
    try:
        m = ex.market(symbol)
        step = float((m.get("limits", {}).get("amount", {}).get("min", 0)) or 0)
    except Exception:
        step = 0.0
    max_parts = len(targets or [])
    if step and step > 0:
        try:
            max_parts = min(max_parts, max(1, int(qty / step)))
        except Exception:
            pass
    t1, t2, t3 = (targets or [None, None, None])[:3] + [None] * (3 - len(targets or []))
    s1, s2, s3 = (splits or [0.5, 0.3, 0.2])[:3] + [0.0] * (3 - len(splits or []))
    # Choose which targets to place given max_parts, prefer to always include the final target
    if max_parts <= 1:
        used_targets = [t3] if t3 is not None else [x for x in [t2, t1] if x is not None][:1]
        used_splits = [1.0]
    elif max_parts == 2:
        # keep T1 and T3, reweight splits proportionally to preserve intent
        used_targets = [t1, t3] if (t1 is not None and t3 is not None) else [x for x in [t1, t2, t3] if x is not None][:2]
        base = max(1e-9, s1 + s3)
        used_splits = [s1 / base, s3 / base]
    else:
        used_targets = [x for x in [t1, t2, t3] if x is not None][:3]
        used_splits = [s1, s2, s3][:len(used_targets)]
        # normalize to sum <= 1, last gets remainder
        if len(used_splits) >= 2:
            s_sum = sum(used_splits[:-1])
            used_splits[-1] = max(0.0, 1.0 - s_sum)
    rounded_parts = []
    placed_sum = 0.0
    leftover = 0.0
    num = min(len(used_targets), len(used_splits))
    for idx in range(num):
        s = float(used_splits[idx])
        # Reserve minimum step for the last TP if needed
        reserve = step if (idx < num - 1 and step and step > 0) else 0.0
        # Accumulate any leftover from previous rounding into the next allocation
        if idx == num - 1:
            alloc = max(0.0, qty - placed_sum)
        else:
            alloc = max(0.0, min(qty - placed_sum - reserve, qty * s + leftover))
        r = round_qty(ex, symbol, alloc)
        rounded_parts.append(r)
        placed_sum += max(0.0, r)
        leftover = max(0.0, alloc - r)

    # Place TPs using rounded amounts
    for i, (t, part_qty) in enumerate(zip(used_targets, rounded_parts), start=1):
        if part_qty <= 0:
            continue
        try:
            tp = ex.create_order(symbol, type="TAKE_PROFIT_MARKET", side=opposite, amount=part_qty,
                                  params={**params, "stopPrice": float(t)})
            log(f"TP{i} placed", t, part_qty, (tp.get("id") or tp.get("orderId") or ""))
        except Exception as e:
            log(f"Failed to place TP{i}:", str(e))
    if len(used_targets) < len(targets or []):
        log("Some TPs merged due to min amount; placed:", len(used_targets), "of", len(targets or []), "(final TP preserved)")
    return entry

def maybe_update_trailing(ex, symbol, side, qty, entry, atr, last_price):
    if DRY_RUN:
        return
    try:
        orders = get_open_orders(ex, symbol)
    except Exception:
        orders = []

    if side == "buy":
        r = ATR_MULT_SL * atr
        be_trigger = entry + BREAKEVEN_AFTER_R * r
        trail_trigger = entry + TRAIL_AFTER_R * r
        if last_price >= be_trigger:
            new_sl = entry if last_price < trail_trigger else (last_price - TRAIL_ATR_MULT * atr)
            cancel_reduce_only_orders(ex, symbol)
            try:
                ex.create_order(symbol, "STOP_MARKET", "sell", qty, params={"reduceOnly": True, "stopPrice": float(new_sl)})
                log("Trailing/BE SL updated", symbol, new_sl)
            except Exception as e:
                log("Failed trailing SL:", str(e))
    else:
        r = ATR_MULT_SL * atr
        be_trigger = entry - BREAKEVEN_AFTER_R * r
        trail_trigger = entry - TRAIL_AFTER_R * r
        if last_price <= be_trigger:
            new_sl = entry if last_price > trail_trigger else (last_price + TRAIL_ATR_MULT * atr)
            cancel_reduce_only_orders(ex, symbol)
            try:
                ex.create_order(symbol, "STOP_MARKET", "buy", qty, params={"reduceOnly": True, "stopPrice": float(new_sl)})
                log("Trailing/BE SL updated", symbol, new_sl)
            except Exception as e:
                log("Failed trailing SL:", str(e))


def place_reduce_only_exits(ex, symbol, position_side: str, qty: float, sl_price: float, tp_price: float):
    """
    Place reduce-only SL/TP for an existing position.
    position_side: "long" or "short"
    """
    opposite = "sell" if position_side == "long" else "buy"
    if DRY_RUN:
        log(f"[DRY_RUN] EXIT SL/TP reduceOnly {opposite.upper()} {qty} {symbol} SL={sl_price} TP={tp_price}")
        return
    params = {"reduceOnly": True}
    try:
        ex.create_order(symbol, type="STOP_MARKET", side=opposite, amount=qty,
                        params={**params, "stopPrice": float(sl_price)})
        log("SL placed (reconcile)", symbol, sl_price)
    except Exception as e:
        log("Failed to place SL (reconcile):", symbol, str(e))
    try:
        ex.create_order(symbol, type="TAKE_PROFIT_MARKET", side=opposite, amount=qty,
                        params={**params, "stopPrice": float(tp_price)})
        log("TP placed (reconcile)", symbol, tp_price)
    except Exception as e:
        log("Failed to place TP (reconcile):", symbol, str(e))


