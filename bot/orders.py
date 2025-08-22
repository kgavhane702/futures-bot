from .config import DRY_RUN, MIN_NOTIONAL_USDT, HEDGE_MODE, WORKING_TYPE, PRICE_PROTECT
from .logging_utils import log
from .exchange_client import round_amount, round_price, new_client_id
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

def ensure_protection_orders(ex, symbol, side, qty, entry, atr,
                             ATR_MULT_SL, TP_R_MULT):
    if DRY_RUN:
        return
    try:
        position_side = "LONG" if side == "buy" else "SHORT" if HEDGE_MODE else None
        # Compute desired protective levels
        stop, tp, _ = protective_prices(side, float(entry), float(atr), ATR_MULT_SL, TP_R_MULT)
        stop = round_price(ex, symbol, stop)
        tp = round_price(ex, symbol, tp)

        need_sl = not has_stop_loss(ex, symbol, position_side=position_side)
        need_tp = not has_take_profit(ex, symbol, position_side=position_side)
        if not (need_sl or need_tp):
            return

        opposite = "sell" if side == "buy" else "buy"
        params = {"reduceOnly": True}
        if HEDGE_MODE and position_side:
            params["positionSide"] = position_side

        if need_sl:
            ex.create_order(symbol, type="STOP_MARKET", side=opposite, amount=qty,
                            params={**params, "newClientOrderId": new_client_id("reprot_sl"),
                                    "stopPrice": float(stop),
                                    "workingType": WORKING_TYPE, "priceProtect": PRICE_PROTECT})
            log("Recreated SL", symbol, stop)
        if need_tp:
            ex.create_order(symbol, type="TAKE_PROFIT_MARKET", side=opposite, amount=qty,
                            params={**params, "newClientOrderId": new_client_id("reprot_tp"),
                                    "stopPrice": float(tp),
                                    "workingType": WORKING_TYPE, "priceProtect": PRICE_PROTECT})
            log("Recreated TP", symbol, tp)
    except Exception as e:
        log("ensure_protection_orders failed", symbol, str(e))

def reconcile_orphan_reduce_only_orders(ex, symbol, pos):
    try:
        open_orders = get_open_orders(ex, symbol)
        reduce_only = [o for o in open_orders if o.get("reduceOnly")]
        if isinstance(pos, list):
            pos_size = sum(float(p.get("size", 0.0) or 0.0) for p in pos)
        else:
            pos_size = 0.0 if (pos is None) else float(pos.get("size", 0.0))
        # If no position at all, cancel ALL open orders (reduceOnly and non-reduceOnly)
        if pos_size <= 0 and open_orders:
            for o in open_orders:
                try:
                    ex.cancel_order(o["id"], symbol)
                    log(f"Canceled orphan order {o.get('id')} on {symbol}")
                except Exception as e:
                    log(f"Failed to cancel orphan order {o.get('id')} on {symbol}: {e}")
        elif pos_size > 0 and reduce_only:
            # Hedge hygiene: if a specific side is open, cancel reduceOnly orders that target the opposite side
            # Determine live sides
            live_sides = set([p.get("side") for p in (pos if isinstance(pos, list) else [pos]) if p])
            for o in reduce_only:
                try:
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
    params_ro = {"reduceOnly": True}
    if HEDGE_MODE:
        params_ro["positionSide"] = "LONG" if side == "buy" else "SHORT"

    sl_ok = True
    tp_ok = True
    try:
        ex.create_order(symbol, type="STOP_MARKET", side=opposite, amount=qty,
                        params={**params_ro, "newClientOrderId": new_client_id("prot_sl"),
                                "stopPrice": float(sl_price),
                                "workingType": WORKING_TYPE, "priceProtect": PRICE_PROTECT})
        log("SL placed", sl_price)
    except Exception as e:
        sl_ok = False
        log("Failed to place SL:", str(e))
    try:
        ex.create_order(symbol, type="TAKE_PROFIT_MARKET", side=opposite, amount=qty,
                        params={**params_ro, "newClientOrderId": new_client_id("prot_tp"),
                                "stopPrice": float(tp_price),
                                "workingType": WORKING_TYPE, "priceProtect": PRICE_PROTECT})
        log("TP placed", tp_price)
    except Exception as e:
        tp_ok = False
        log("Failed to place TP:", str(e))

    # If SL failed, we cannot remain unprotected: attempt emergency close.
    if not sl_ok:
        log("SL placement failed; attempting immediate safe close...", symbol)
        try:
            ex.create_order(symbol, type="market", side=opposite, amount=qty, params={"reduceOnly": True})
            log("Emergency close placed for", symbol)
        except Exception as e:
            log("Emergency close FAILED", symbol, str(e))
        raise RuntimeError("SL placement failed; entry closed/attempted close.")
    # If only TP failed, keep the position open with SL and allow protection checker to recreate TP.

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
