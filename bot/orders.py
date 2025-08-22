from .config import DRY_RUN, MIN_NOTIONAL_USDT, HEDGE_MODE, WORKING_TYPE, PRICE_PROTECT, PROTECTION_BUFFER_PCT, PROTECTION_COOLDOWN_SECS, PROTECTION_TOLERANCE_PCT
import threading
import time
from .logging_utils import log
from .exchange_client import round_amount, round_price, new_client_id, get_price_increment
from .risk import protective_prices

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
                # Try to capture the average/entry price from common ccxt fields
                ep = (
                    p.get("entryPrice")
                    or p.get("avgPrice")
                    or (p.get("info", {}) or {}).get("entryPrice")
                    or (p.get("info", {}) or {}).get("avgPrice")
                )
                try:
                    entry_price = float(ep) if ep is not None else None
                except Exception:
                    entry_price = None
                pos_dict = {"side": side, "size": abs(sz), "entry": entry_price}
                # If hedge mode, we may have both long and short for same symbol; keep both
                if sym in open_map:
                    existing = open_map[sym]
                    if isinstance(existing, list):
                        # Replace same-side if exists, otherwise append
                        replaced = False
                        for i, ex_pos in enumerate(existing):
                            if ex_pos.get("side") == side:
                                existing[i] = pos_dict
                                replaced = True
                                break
                        if not replaced:
                            existing.append(pos_dict)
                    else:
                        if existing.get("side") != side and HEDGE_MODE:
                            open_map[sym] = [existing, pos_dict]
                        else:
                            open_map[sym] = pos_dict
                else:
                    open_map[sym] = pos_dict
        return open_map
    except Exception as e:
        log("fetch_positions failed:", str(e))
        return {}

def get_open_orders(ex, symbol):
    try:
        return ex.fetch_open_orders(symbol)
    except Exception:
        return []

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

def _is_take_profit(o) -> bool:
    try:
        t = (o.get("type") or "").upper()
        it = (o.get("info", {}) or {}).get("type", "").upper()
        return ("TAKE_PROFIT" in t) or ("TAKE_PROFIT" in it)
    except Exception:
        return False

def _is_stop_loss(o) -> bool:
    try:
        t = (o.get("type") or "").upper()
        it = (o.get("info", {}) or {}).get("type", "").upper()
        # STOP_MARKET/STOP and not TAKE_PROFIT
        if ("TAKE_PROFIT" in t) or ("TAKE_PROFIT" in it):
            return False
        return ("STOP" in t) or ("STOP" in it)
    except Exception:
        return False

def _extract_stop_price(o):
    sp = o.get("stopPrice")
    if sp is None:
        sp = (o.get("info", {}) or {}).get("stopPrice")
    if sp is None:
        sp = (o.get("info", {}) or {}).get("triggerPrice")
    try:
        return float(sp) if sp is not None else None
    except Exception:
        return None

def cancel_stop_loss_orders(ex, symbol, *, position_side: str | None = None):
    try:
        for o in get_open_orders(ex, symbol):
            if _is_stop_loss(o):
                if position_side:
                    ps = ((o.get("params", {}) or {}).get("positionSide")
                          or (o.get("info", {}) or {}).get("positionSide"))
                    if ps and ps.upper() != position_side.upper():
                        continue
                try:
                    ex.cancel_order(o["id"], symbol)
                    log(f"Canceled SL order {o.get('id')} on {symbol}")
                except Exception:
                    pass
    except Exception:
        pass

def get_current_stop_loss_price(ex, symbol, *, position_side: str | None = None):
    try:
        sl_prices = []
        for o in get_open_orders(ex, symbol):
            if _is_stop_loss(o):
                if position_side:
                    ps = ((o.get("params", {}) or {}).get("positionSide")
                          or (o.get("info", {}) or {}).get("positionSide"))
                    if ps and ps.upper() != position_side.upper():
                        continue
                sp = _extract_stop_price(o)
                if sp is not None:
                    sl_prices.append(sp)
        if not sl_prices:
            return None
        return min(sl_prices), max(sl_prices)
    except Exception:
        return None

# track last protection update per symbol
_last_prot_ts = {}

# per-symbol locks to avoid race across entry/protection/sweeper
_locks: dict[str, threading.Lock] = {}

def _lock_for(symbol: str) -> threading.Lock:
    lk = _locks.get(symbol)
    if lk is None:
        lk = threading.Lock()
        _locks[symbol] = lk
    return lk

def adjust_protection_prices(ex, symbol: str, side: str, entry: float, stop: float, tp: float):
    try:
        entry = float(entry)
        stop = float(stop)
        tp = float(tp)
    except Exception:
        return stop, tp
    tick = get_price_increment(ex, symbol)
    if side == "buy":
        stop = min(stop, entry * (1 - PROTECTION_BUFFER_PCT))
        tp   = max(tp,   entry * (1 + PROTECTION_BUFFER_PCT))
        stop = round_price(ex, symbol, min(stop, entry - tick))
        tp   = round_price(ex, symbol, max(tp,   entry + tick))
    else:
        stop = max(stop, entry * (1 + PROTECTION_BUFFER_PCT))
        tp   = min(tp,   entry * (1 - PROTECTION_BUFFER_PCT))
        stop = round_price(ex, symbol, max(stop, entry + tick))
        tp   = round_price(ex, symbol, min(tp,   entry - tick))
    return stop, tp

def enforce_trigger_distance_with_last(ex, symbol: str, side: str, last_price: float, stop: float, tp: float):
    try:
        last_price = float(last_price)
        stop = float(stop)
        tp = float(tp)
    except Exception:
        return stop, tp
    tick = get_price_increment(ex, symbol)
    if side == "buy":
        stop = min(stop, last_price * (1 - PROTECTION_BUFFER_PCT))
        tp   = max(tp,   last_price * (1 + PROTECTION_BUFFER_PCT))
        stop = round_price(ex, symbol, min(stop, last_price - tick))
        tp   = round_price(ex, symbol, max(tp,   last_price + tick))
    else:
        stop = max(stop, last_price * (1 + PROTECTION_BUFFER_PCT))
        tp   = min(tp,   last_price * (1 - PROTECTION_BUFFER_PCT))
        stop = round_price(ex, symbol, max(stop, last_price + tick))
        tp   = round_price(ex, symbol, min(tp,   last_price - tick))
    return stop, tp

def _matches_position_side(o, position_side: str | None) -> bool:
    if not position_side:
        return True
    ps = ((o.get("params", {}) or {}).get("positionSide")
          or (o.get("info", {}) or {}).get("positionSide"))
    return (ps is None) or (str(ps).upper() == position_side.upper())

def has_stop_loss(ex, symbol, *, position_side: str | None = None) -> bool:
    try:
        for o in get_open_orders(ex, symbol):
            if _is_stop_loss(o) and _matches_position_side(o, position_side):
                return True
    except Exception:
        pass
    return False

def has_take_profit(ex, symbol, *, position_side: str | None = None) -> bool:
    try:
        for o in get_open_orders(ex, symbol):
            if _is_take_profit(o) and _matches_position_side(o, position_side):
                return True
    except Exception:
        pass
    return False

def is_within_tolerance(current: float, target: float, tol_frac: float) -> bool:
    try:
        if current is None or target is None:
            return False
        return abs(current - target) / max(1e-9, abs(target)) <= tol_frac
    except Exception:
        return False

def ensure_protection_orders(*args, **kwargs):
    # Disabled in simplified mode
    return

def reconcile_orphan_reduce_only_orders(ex, symbol, pos, grace_cutoff_ms: float | None = None):
    try:
        with _lock_for(symbol):
            open_orders = get_open_orders(ex, symbol)
        reduce_only = [o for o in open_orders if o.get("reduceOnly")]
        if isinstance(pos, list):
            pos_size = sum(float(p.get("size", 0.0) or 0.0) for p in pos)
        else:
            pos_size = 0.0 if (pos is None) else float(pos.get("size", 0.0))
        # If no position at all, cancel only reduceOnly orders (and honor grace window if provided)
        if pos_size <= 0 and reduce_only:
            for o in reduce_only:
                try:
                    if grace_cutoff_ms is not None:
                        ts = (o.get("timestamp") or (o.get("info", {}) or {}).get("time"))
                        if ts and ts >= grace_cutoff_ms:
                            continue
                    with _lock_for(symbol):
                        ex.cancel_order(o["id"], symbol)
                    log(f"Canceled orphan reduceOnly order {o.get('id')} on {symbol}")
                except Exception as e:
                    log(f"Failed to cancel orphan order {o.get('id')} on {symbol}: {e}")
        elif pos_size > 0 and reduce_only:
            # Hedge hygiene: if a specific side is open, cancel reduceOnly orders that target the opposite side
            # Determine live sides
            live_sides = set([p.get("side") for p in (pos if isinstance(pos, list) else [pos]) if p])
            for o in reduce_only:
                try:
                    if grace_cutoff_ms is not None:
                        ts = (o.get("timestamp") or (o.get("info", {}) or {}).get("time"))
                        if ts and ts >= grace_cutoff_ms:
                            continue
                    ps = ((o.get("params", {}) or {}).get("positionSide") or (o.get("info", {}) or {}).get("positionSide"))
                    # If exchange embeds side via order side, infer intended position side
                    o_side = (o.get("side") or "").lower()
                    intended = None
                    if ps:
                        intended = "long" if str(ps).upper() == "LONG" else "short" if str(ps).upper() == "SHORT" else None
                    elif o_side in ("buy","sell"):
                        # For reduceOnly: buy reduces short, sell reduces long
                        intended = "short" if o_side == "buy" else "long"
                    if intended and intended not in live_sides:
                        with _lock_for(symbol):
                            ex.cancel_order(o["id"], symbol)
                        log(f"Canceled mismatched RO order {o.get('id')} ({intended}) on {symbol}")
                except Exception:
                    pass
    except Exception as e:
        log("reconcile_orphan_reduce_only_orders error", symbol, str(e))

def place_bracket_orders(ex, symbol, side, qty, entry_price, sl_price, tp_price):
    opposite = "sell" if side == "buy" else "buy"

    qty = round_amount(ex, symbol, qty)
    entry_price = float(entry_price)
    sl_price = round_price(ex, symbol, sl_price)
    tp_price = round_price(ex, symbol, tp_price)
    # Centralized buffer/tick adjustment to avoid immediate trigger
    sl_price, tp_price = adjust_protection_prices(ex, symbol, side, entry_price, sl_price, tp_price)
    # Enforce additional distance against current market
    try:
        tkr = ex.fetch_ticker(symbol)
        last_px = float(tkr.get("last") or tkr.get("close") or entry_price)
        sl_price, tp_price = enforce_trigger_distance_with_last(ex, symbol, side, last_px, sl_price, tp_price)
    except Exception:
        pass

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
        return {"id": f"dry_{new_client_id()}"}  # simulate

    # ENTRY
    try:
        params = {"newClientOrderId": new_client_id("entry")}
        if HEDGE_MODE:
            params["positionSide"] = "LONG" if side == "buy" else "SHORT"
        entry = ex.create_order(symbol, type="market", side=side, amount=qty, params=params)
        log("ENTRY", entry.get("id"), side, qty, symbol)
    except Exception as e:
        log("ENTRY failed:", symbol, str(e))
        raise

    # Protective orders
    params_ro = {"closePosition": True}
    if HEDGE_MODE:
        params_ro["positionSide"] = "LONG" if side == "buy" else "SHORT"

    sl_ok = True
    tp_ok = True
    with _lock_for(symbol):
        try:
            ex.create_order(symbol, type="STOP_MARKET", side=opposite, amount=None,
                            params={**params_ro, "newClientOrderId": new_client_id("prot_sl"),
                                    "stopPrice": float(sl_price)})
            log("SL placed", sl_price)
        except Exception as e:
            sl_ok = False
            log("Failed to place SL:", str(e))
        try:
            ex.create_order(symbol, type="TAKE_PROFIT_MARKET", side=opposite, amount=None,
                            params={**params_ro, "newClientOrderId": new_client_id("prot_tp"),
                                    "stopPrice": float(tp_price)})
            log("TP placed", tp_price)
        except Exception as e:
            tp_ok = False
            log("Failed to place TP:", str(e))

    # If SL failed, do not auto-close; let protection checker recreate with cooldown
    if not sl_ok:
        log("SL placement failed; will rely on protection checker to recreate", symbol)
        try:
            from .config import ENTRY_PROTECTION_GRACE_SECS
            _last_prot_ts[symbol] = int(time.time() * 1000) + ENTRY_PROTECTION_GRACE_SECS * 1000
        except Exception:
            _last_prot_ts[symbol] = int(time.time() * 1000) + 5000
        return entry
    # If only TP failed, keep the position open with SL and allow protection checker to recreate TP.
    try:
        from .config import ENTRY_PROTECTION_GRACE_SECS
        _last_prot_ts[symbol] = int(time.time() * 1000) + ENTRY_PROTECTION_GRACE_SECS * 1000
    except Exception:
        _last_prot_ts[symbol] = int(time.time() * 1000) + 5000

    return entry

def maybe_update_trailing(ex, symbol, side, qty, entry, atr, last_price,
                          ATR_MULT_SL, BREAKEVEN_AFTER_R, TRAIL_AFTER_R, TRAIL_ATR_MULT):
    if DRY_RUN:
        return
    try:
        r = ATR_MULT_SL * atr
        position_side = "LONG" if side == "buy" else "SHORT" if HEDGE_MODE else None
        current_sl_range = get_current_stop_loss_price(ex, symbol, position_side=position_side)
        current_sl_min = None
        current_sl_max = None
        if isinstance(current_sl_range, tuple):
            current_sl_min, current_sl_max = current_sl_range

        if side == "buy":
            be_trigger = entry + BREAKEVEN_AFTER_R * r
            trail_trigger = entry + TRAIL_AFTER_R * r
            if last_price < be_trigger:
                return
            candidate = entry if last_price < trail_trigger else (last_price - TRAIL_ATR_MULT * atr)
            new_sl = candidate
            if current_sl_max is not None:
                new_sl = max(current_sl_max, candidate)
            new_sl = max(new_sl, entry)
            new_sl = round_price(ex, symbol, new_sl)
            if (current_sl_max is None) or (new_sl > current_sl_max + 1e-8):
                # Place new SL first to avoid gap, then cancel older ones
                params = {"reduceOnly": True, "newClientOrderId": new_client_id("trail")}
                if HEDGE_MODE:
                    params["positionSide"] = "LONG"
                ex.create_order(symbol, "STOP_MARKET", "sell", qty, params={**params, "stopPrice": float(new_sl)})
                cancel_stop_loss_orders(ex, symbol, position_side=position_side)
                log("Trailing/BE SL updated", symbol, new_sl)
        else:
            be_trigger = entry - BREAKEVEN_AFTER_R * r
            trail_trigger = entry - TRAIL_AFTER_R * r
            if last_price > be_trigger:
                return
            candidate = entry if last_price > trail_trigger else (last_price + TRAIL_ATR_MULT * atr)
            new_sl = candidate
            if current_sl_min is not None:
                new_sl = min(current_sl_min, candidate)
            new_sl = min(new_sl, entry)
            new_sl = round_price(ex, symbol, new_sl)
            if (current_sl_min is None) or (new_sl < current_sl_min - 1e-8):
                # Place new SL first to avoid gap, then cancel older ones
                params = {"reduceOnly": True, "newClientOrderId": new_client_id("trail")}
                if HEDGE_MODE:
                    params["positionSide"] = "SHORT"
                ex.create_order(symbol, "STOP_MARKET", "buy", qty, params={**params, "stopPrice": float(new_sl)})
                cancel_stop_loss_orders(ex, symbol, position_side=position_side)
                log("Trailing/BE SL updated", symbol, new_sl)
    except Exception as e:
        log("Failed trailing/BE update", symbol, str(e))
