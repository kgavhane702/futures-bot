from .config import DRY_RUN, MIN_NOTIONAL_USDT, HEDGE_MODE
from .logging_utils import log
from .exchange_client import round_amount, round_price, new_client_id

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
                open_map[sym] = {"side": side, "size": abs(sz)}
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

def reconcile_orphan_reduce_only_orders(ex, symbol, pos):
    try:
        open_orders = get_open_orders(ex, symbol)
        reduce_only = [o for o in open_orders if o.get("reduceOnly")]
        pos_size = 0.0 if (pos is None) else float(pos.get("size", 0.0))
        if pos_size <= 0 and reduce_only:
            for o in reduce_only:
                try:
                    ex.cancel_order(o["id"], symbol)
                    log(f"Canceled orphan reduceOnly order {o['id']} on {symbol}")
                except Exception as e:
                    log(f"Failed to cancel orphan order {o.get('id')} on {symbol}: {e}")
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
    params_ro = {"reduceOnly": True, "newClientOrderId": new_client_id("prot")}
    if HEDGE_MODE:
        params_ro["positionSide"] = "LONG" if side == "buy" else "SHORT"

    sl_ok = True
    tp_ok = True
    try:
        ex.create_order(symbol, type="STOP_MARKET", side=opposite, amount=qty,
                        params={**params_ro, "stopPrice": float(sl_price)})
        log("SL placed", sl_price)
    except Exception as e:
        sl_ok = False
        log("Failed to place SL:", str(e))
    try:
        ex.create_order(symbol, type="TAKE_PROFIT_MARKET", side=opposite, amount=qty,
                        params={**params_ro, "stopPrice": float(tp_price)})
        log("TP placed", tp_price)
    except Exception as e:
        tp_ok = False
        log("Failed to place TP:", str(e))

    if (not sl_ok) or (not tp_ok):
        log("Protection failed, attempting immediate safe close...", symbol)
        try:
            ex.create_order(symbol, type="market", side=opposite, amount=qty, params={"reduceOnly": True})
            log("Emergency close placed for", symbol)
        except Exception as e:
            log("Emergency close FAILED", symbol, str(e))
        raise RuntimeError("Protection placement failed; entry closed/attempted close.")

    return entry

def maybe_update_trailing(ex, symbol, side, qty, entry, atr, last_price,
                          ATR_MULT_SL, BREAKEVEN_AFTER_R, TRAIL_AFTER_R, TRAIL_ATR_MULT):
    if DRY_RUN:
        return
    try:
        if side == "buy":
            r = ATR_MULT_SL * atr
            be_trigger = entry + BREAKEVEN_AFTER_R * r
            trail_trigger = entry + TRAIL_AFTER_R * r
            if last_price >= be_trigger:
                new_sl = entry if last_price < trail_trigger else (last_price - TRAIL_ATR_MULT * atr)
                cancel_reduce_only_orders(ex, symbol)
                ex.create_order(symbol, "STOP_MARKET", "sell", qty,
                                params={"reduceOnly": True, "stopPrice": float(new_sl)})
                log("Trailing/BE SL updated", symbol, new_sl)
        else:
            r = ATR_MULT_SL * atr
            be_trigger = entry - BREAKEVEN_AFTER_R * r
            trail_trigger = entry - TRAIL_AFTER_R * r
            if last_price <= be_trigger:
                new_sl = entry if last_price > trail_trigger else (last_price + TRAIL_ATR_MULT * atr)
                cancel_reduce_only_orders(ex, symbol)
                ex.create_order(symbol, "STOP_MARKET", "buy", qty,
                                params={"reduceOnly": True, "stopPrice": float(new_sl)})
                log("Trailing/BE SL updated", symbol, new_sl)
    except Exception as e:
        log("Failed trailing/BE update", symbol, str(e))
