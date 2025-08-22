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
            # Only cancel reduceOnly orders; leave existing SL/TP untouched
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

def _r_unit_from_entry_and_stop(side: str, entry: float, stop: float) -> float:
    try:
        entry = float(entry)
        stop = float(stop)
    except Exception:
        return 0.0
    if side == "buy":
        return max(0.0, entry - stop)
    else:
        return max(0.0, stop - entry)

def _tp_prices_from_r_levels(side: str, entry: float, r_unit: float, r_levels: list[float]) -> list[float]:
    prices: list[float] = []
    for r_mult in r_levels:
        if side == "buy":
            prices.append(entry + r_mult * r_unit)
        else:
            prices.append(entry - r_mult * r_unit)
    return prices

def _split_amounts(ex, symbol: str, qty: float, splits: list[float]) -> list[float]:
    # Create amounts q1,q2,q3 that sum to <= qty after rounding
    rounded: list[float] = []
    remaining = qty
    for i, s in enumerate(splits):
        part = qty * s
        if i < len(splits) - 1:
            part = round_amount(ex, symbol, part)
            part = min(part, remaining)
            rounded.append(part)
            remaining = max(0.0, remaining - part)
        else:
            # Last leg takes the remainder
            part = round_amount(ex, symbol, remaining)
            rounded.append(part)
            remaining = max(0.0, remaining - part)
    # If rounding zeroed out a leg, shift remainder to first non-zero
    total = sum(rounded)
    if total <= 0 and len(rounded) > 0:
        rounded[0] = round_amount(ex, symbol, qty)
    return rounded

def replace_stop_loss_close_position(ex, symbol: str, side: str, new_stop_price: float):
    # Cancel existing SLs and place a new closePosition STOP_MARKET at new_stop_price
    position_side = "LONG" if side == "buy" else "SHORT" if HEDGE_MODE else None
    try:
        # Cancel all SL orders regardless of reduceOnly
        for o in get_open_orders(ex, symbol):
            if _is_stop_loss(o):
                try:
                    ex.cancel_order(o["id"], symbol)
                except Exception:
                    pass
    except Exception:
        pass
    params_ro = {"closePosition": True, "newClientOrderId": new_client_id("prot_sl")}
    if HEDGE_MODE and position_side:
        params_ro["positionSide"] = position_side
    ex.create_order(symbol, type="STOP_MARKET", side=("sell" if side == "buy" else "buy"), amount=None,
                    params={**params_ro, "stopPrice": float(new_stop_price)})

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
    # Multi-TP constants (fixed behavior)
    TP_SPLITS = [0.3, 0.3, 0.4]
    TP_R_LEVELS = [1.0, 2.0, 3.0]
    # Compute R unit and TP prices
    r_unit = _r_unit_from_entry_and_stop(side, entry_price, sl_price)
    tp_prices = _tp_prices_from_r_levels(side, entry_price, r_unit, TP_R_LEVELS)
    # Round and buffer each TP; also buffer SL
    tps_adjusted: list[float] = []
    try:
        tkr = ex.fetch_ticker(symbol)
        last_px = float(tkr.get("last") or tkr.get("close") or entry_price)
    except Exception:
        last_px = entry_price
    # Adjust SL vs entry and last
    sl_price, _tmp = adjust_protection_prices(ex, symbol, side, entry_price, sl_price, tp_prices[0])
    sl_price, _tmp = enforce_trigger_distance_with_last(ex, symbol, side, last_px, sl_price, _tmp)
    for tp in tp_prices:
        _s, tp_adj = adjust_protection_prices(ex, symbol, side, entry_price, sl_price, tp)
        _s2, tp_adj = enforce_trigger_distance_with_last(ex, symbol, side, last_px, _s, tp_adj)
        tps_adjusted.append(round_price(ex, symbol, tp_adj))

    notional = qty * entry_price
    if notional < MIN_NOTIONAL_USDT:
        log(f"SKIP {symbol}: notional {notional:.2f} < min {MIN_NOTIONAL_USDT:.2f}")
        return {"id": "skip_notional"}

    log(f"ORDER PREVIEW {symbol} side={side} qty={qty:.6f} entry≈{entry_price:.6f} "
        f"notional≈{notional:.2f} SL={sl_price:.6f} TP={tp_price:.6f}")

    if DRY_RUN:
        log(f"[DRY_RUN] ENTRY {side.upper()} {qty} {symbol} @~{entry_price}")
        log(f"[DRY_RUN] SL closePosition {opposite.upper()} @ {sl_price}")
        for i, tpp in enumerate(tps_adjusted, start=1):
            log(f"[DRY_RUN] TP{i} reduceOnly {opposite.upper()} @ {tpp}")
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
    params_sl = {"closePosition": True, "workingType": "MARK_PRICE", "priceProtect": True}
    params_tp_base = {"reduceOnly": True, "workingType": "MARK_PRICE", "priceProtect": True}
    if HEDGE_MODE:
        params_sl["positionSide"] = "LONG" if side == "buy" else "SHORT"
        params_tp_base["positionSide"] = "LONG" if side == "buy" else "SHORT"

    sl_ok = True
    tps_ok = True
    # Split amounts for TPs
    tp_amounts = _split_amounts(ex, symbol, qty, TP_SPLITS)
    with _lock_for(symbol):
        try:
            ex.create_order(symbol, type="STOP_MARKET", side=opposite, amount=None,
                            params={**params_sl, "newClientOrderId": new_client_id("prot_sl"),
                                    "stopPrice": float(sl_price)})
            log("SL placed", sl_price)
        except Exception as e:
            sl_ok = False
            log("Failed to place SL:", str(e))
        # 3 partial TPs
        for i, (tpp, amt) in enumerate(zip(tps_adjusted, tp_amounts), start=1):
            if amt <= 0:
                continue
            try:
                ex.create_order(symbol, type="TAKE_PROFIT_MARKET", side=opposite, amount=amt,
                                params={**params_tp_base, "newClientOrderId": new_client_id(f"prot_tp{i}"),
                                        "stopPrice": float(tpp)})
                log(f"TP{i} placed", tpp, amt)
            except Exception as e:
                tps_ok = False
                log(f"Failed to place TP{i}:", str(e))

    # Single retry with extra buffer if any placement failed (no background thread)
    if not sl_ok or not tps_ok:
        try:
            tick = get_price_increment(ex, symbol)
        except Exception:
            tick = 0.0
        # widen buffers
        if not sl_ok:
            if side == "buy":
                sl_retry = round_price(ex, symbol, min(sl_price, entry_price - 2 * tick))
            else:
                sl_retry = round_price(ex, symbol, max(sl_price, entry_price + 2 * tick))
            try:
                with _lock_for(symbol):
                    ex.create_order(symbol, type="STOP_MARKET", side=opposite, amount=None,
                                    params={**params_sl, "newClientOrderId": new_client_id("prot_slr"),
                                            "stopPrice": float(sl_retry)})
                log("SL retry placed", sl_retry)
                sl_ok = True
            except Exception as e:
                log("SL retry failed:", str(e))
        if not tps_ok:
            with _lock_for(symbol):
                for i, (tpp, amt) in enumerate(zip(tps_adjusted, tp_amounts), start=1):
                    if amt <= 0:
                        continue
                    try:
                        # nudge TP away by one extra tick
                        tpp_retry = round_price(ex, symbol, (tpp + tick) if side == "buy" else (tpp - tick))
                        ex.create_order(symbol, type="TAKE_PROFIT_MARKET", side=opposite, amount=amt,
                                        params={**params_tp_base, "newClientOrderId": new_client_id(f"prot_tp{i}r"),
                                                "stopPrice": float(tpp_retry)})
                        log(f"TP{i} retry placed", tpp_retry, amt)
                    except Exception as e:
                        log(f"TP{i} retry failed:", str(e))
    # Invariant check: ensure protections exist (non-fatal)
    try:
        open_after = get_open_orders(ex, symbol)
        # Count SLs
        sl_count = 0
        tp_count = 0
        for o in open_after:
            if _is_stop_loss(o):
                if HEDGE_MODE:
                    ps = ((o.get("params", {}) or {}).get("positionSide") or (o.get("info", {}) or {}).get("positionSide"))
                    desired = "LONG" if side == "buy" else "SHORT"
                    if ps and str(ps).upper() != desired:
                        continue
                sl_count += 1
            elif _is_take_profit(o):
                if HEDGE_MODE:
                    ps = ((o.get("params", {}) or {}).get("positionSide") or (o.get("info", {}) or {}).get("positionSide"))
                    desired = "LONG" if side == "buy" else "SHORT"
                    if ps and str(ps).upper() != desired:
                        continue
                tp_count += 1
        expected_tps = sum(1 for a in tp_amounts if a > 0)
        if sl_count < 1:
            log("WARN protections: SL not found right after placement", symbol)
        if tp_count < expected_tps:
            log("WARN protections: fewer TP orders than expected", symbol, tp_count, "<", expected_tps)
    except Exception:
        pass

    # Even if some TP failed, keep position open with SL.
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
