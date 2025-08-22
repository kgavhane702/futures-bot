import time, traceback, os, threading
from datetime import datetime, UTC
import pandas as pd
import ccxt

from .config import (TIMEFRAME, HTF_TIMEFRAME, UNIVERSE_SIZE, MAX_POSITIONS,
                     ATR_MULT_SL, TP_R_MULT, POLL_SECONDS, TRADES_CSV, DRY_RUN,
                     BREAKEVEN_AFTER_R, TRAIL_AFTER_R, TRAIL_ATR_MULT, ORPHAN_SWEEP_SECONDS)
from .logging_utils import log
from .exchange_client import get_exchange, ensure_symbol_config, round_amount
from .universe import top_usdt_perps
from .indicators import fetch_ohlcv_df, add_indicators, valid_row
from .signals import trend_and_signal, score_signal
from .risk import equity_from_balance, size_position, protective_prices
from .orders import (get_open_positions, get_open_orders, cancel_reduce_only_orders, place_bracket_orders,
                     maybe_update_trailing, reconcile_orphan_reduce_only_orders,
                     ensure_protection_orders)

def write_trade(row):
    header = not os.path.exists(TRADES_CSV)
    pd.DataFrame([row]).to_csv(TRADES_CSV, mode="a", index=False, header=header)

def main():
    ex = get_exchange()
    ex.load_markets()
    last_candle_time = None
    last_orphan_sweep_ts = 0

    # Background orphan-order sweeper thread
    def orphan_sweeper():
        while True:
            try:
                open_pos_bg = get_open_positions(ex)
                open_syms_bg = set(open_pos_bg.keys())
                try:
                    all_markets_bg = list(ex.load_markets().keys())
                except Exception:
                    all_markets_bg = []
                for sym in set(all_markets_bg) | open_syms_bg:
                    try:
                        reconcile_orphan_reduce_only_orders(ex, sym, open_pos_bg.get(sym))
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(ORPHAN_SWEEP_SECONDS)

    threading.Thread(target=orphan_sweeper, daemon=True).start()

    while True:
        try:
            # Wait for new closed LTF candle
            hb = ex.fetch_ohlcv("BTC/USDT", timeframe=TIMEFRAME, limit=3)
            hb_df = pd.DataFrame(hb, columns=["ts","o","h","l","c","v"])
            hb_df["ts"] = pd.to_datetime(hb_df["ts"], unit="ms")
            latest_closed_ts = hb_df.iloc[-2]["ts"]
            if last_candle_time == latest_closed_ts:
                # Periodic orphan-order sweep (every ~3 minutes)
                try:
                    now_ts = time.time()
                    if now_ts - last_orphan_sweep_ts >= 180:
                        log("Orphan sweep while waiting…")
                        open_pos_wait = get_open_positions(ex)
                        open_syms_wait = set(open_pos_wait.keys())
                        try:
                            all_markets_wait = list(ex.load_markets().keys())
                        except Exception:
                            all_markets_wait = []
                        for sym in set(all_markets_wait) | open_syms_wait:
                            try:
                                reconcile_orphan_reduce_only_orders(ex, sym, open_pos_wait.get(sym))
                            except Exception:
                                pass
                        last_orphan_sweep_ts = now_ts
                except Exception:
                    pass
                log("waiting for next candle…")
                time.sleep(POLL_SECONDS)
                continue
            last_candle_time = latest_closed_ts
            log(f"New {TIMEFRAME} close @ {latest_closed_ts}")

            # Universe & snapshot
            universe = top_usdt_perps(ex, UNIVERSE_SIZE)
            log("Universe:", ", ".join(universe))

            open_pos = get_open_positions(ex)
            open_syms = set(open_pos.keys())
            log("Open positions:", open_pos)

            # Reconcile orphan open orders (no pos -> cancel all orders)
            # Include any symbols that have open orders even if not in universe
            try:
                all_markets = list(ex.load_markets().keys())
            except Exception:
                all_markets = list(set(universe))
            for sym in set(universe) | open_syms | set(all_markets):
                try:
                    has_orders = bool(get_open_orders(ex, sym))
                except Exception:
                    has_orders = False
                if has_orders or (sym in open_syms) or (sym in universe):
                    reconcile_orphan_reduce_only_orders(ex, sym, open_pos.get(sym))

            # Scan signals
            cands = []
            for sym in universe:
                try:
                    ltf = fetch_ohlcv_df(ex, sym, TIMEFRAME, limit=400)
                    htf = fetch_ohlcv_df(ex, sym, HTF_TIMEFRAME, limit=400)
                    ltf = add_indicators(ltf)
                    htf = add_indicators(htf)
                    tr, side = trend_and_signal(ltf, htf, valid_row)
                    if side is None:
                        continue
                    lrow = ltf.iloc[-2]
                    score = score_signal(side, lrow)
                    cands.append((sym, side, float(lrow["close"]), float(lrow["atr"]), score))
                except Exception as e:
                    log("scan fail", sym, str(e))

            cands.sort(key=lambda x: x[4], reverse=True)
            log("Top signals:", cands[:5])

            equity = equity_from_balance(ex)
            placed = 0
            for sym, side_sig, entry_price, atr, _ in cands:
                if sym in open_syms:
                    continue
                if placed >= max(0, MAX_POSITIONS - len(open_syms)):
                    break
                if atr <= 0:
                    continue

                # ensure symbol config ok
                if not ensure_symbol_config(ex, sym):
                    log("Skip symbol due to leverage/margin config failure:", sym)
                    continue

                side_ex = "buy" if side_sig == "long" else "sell"
                stop, tp, r_per_unit = protective_prices(side_ex, entry_price, atr, ATR_MULT_SL, TP_R_MULT)

                qty = size_position(entry_price, stop, equity)
                qty = round_amount(ex, sym, qty)
                if qty <= 0:
                    log("qty after rounding <= 0, skip", sym)
                    continue

                try:
                    place_bracket_orders(ex, sym, side_ex, qty, entry_price, stop, tp)
                    write_trade({
                        "time": datetime.now(UTC).isoformat(),
                        "symbol": sym,
                        "side": side_sig,
                        "qty": qty,
                        "entry": entry_price,
                        "stop": stop,
                        "take_profit": tp,
                        "atr": atr,
                        "equity_snapshot": equity,
                        "dry_run": DRY_RUN
                    })
                    placed += 1
                except ccxt.BaseError as e:
                    log("Order rejected:", sym, str(e))
                except Exception as e:
                    log("Order flow error:", sym, str(e))

            # Manage open positions (coarse trailing / flip exits)
            current_pos = get_open_positions(ex)
            for sym, pos in current_pos.items():
                try:
                    ltf = fetch_ohlcv_df(ex, sym, TIMEFRAME, limit=200)
                    ltf = add_indicators(ltf)
                    last = ltf.iloc[-1]
                    prev = ltf.iloc[-2]
                    # Support hedge mode: pos can be dict or list of dicts
                    pos_list = pos if isinstance(pos, list) else [pos]
                    # Compute robust ATR for protection even if prev row is invalid
                    try:
                        atr_prev = float(prev["atr"]) if not pd.isna(prev["atr"]) else None
                    except Exception:
                        atr_prev = None
                    try:
                        atr_last = float(last["atr"]) if not pd.isna(last["atr"]) else None
                    except Exception:
                        atr_last = None
                    atr_for_prot = atr_prev if (atr_prev is not None and atr_prev > 0) else (atr_last if (atr_last is not None and atr_last > 0) else 0.0)
                    for p in pos_list:
                        side = "buy" if p["side"]=="long" else "sell"
                        # Reference entry for protection/trailing
                        entry_ref = p.get("entry") or (prev["close"] if not pd.isna(prev["close"]) else last["close"])
                        # Ensure SL/TP exist if user cancelled them on exchange (run regardless of prev validity)
                        ensure_protection_orders(ex, sym, side, p["size"], entry_ref, atr_for_prot,
                                                 ATR_MULT_SL, TP_R_MULT)
                        # Trail / BE only when indicators are valid
                        if not valid_row(prev):
                            continue
                        maybe_update_trailing(ex, sym, side, p["size"], entry_ref, float(prev["atr"]), last["close"],
                                              ATR_MULT_SL, BREAKEVEN_AFTER_R, TRAIL_AFTER_R, TRAIL_ATR_MULT)
                    # Flip exit requires two consecutive EMA bars against position
                    tr_prev = "up" if prev["ema_fast"] > prev["ema_slow"] else "down"
                    tr_last = "up" if last["ema_fast"] > last["ema_slow"] else "down"
                    flip_against_long = (tr_prev == "down" and tr_last == "down")
                    flip_against_short = (tr_prev == "up" and tr_last == "up")
                    # In hedge mode, pos might be a list; here we only act on symbol-level when single dict
                    if isinstance(pos, dict):
                        if pos["side"] == "long" and flip_against_long:
                            log("Flip out of long — closing", sym)
                            if not DRY_RUN:
                                try:
                                    ex.create_order(sym, "market", "sell", pos["size"], params={"reduceOnly": True})
                                except Exception:
                                    try:
                                        ex.create_order(sym, "market", "sell", None, params={"reduceOnly": True, "closePosition": True})
                                    except Exception:
                                        pass
                            else:
                                log("[DRY_RUN] close long", sym)
                        elif pos["side"] == "short" and flip_against_short:
                            log("Flip out of short — closing", sym)
                            if not DRY_RUN:
                                try:
                                    ex.create_order(sym, "market", "buy", pos["size"], params={"reduceOnly": True})
                                except Exception:
                                    try:
                                        ex.create_order(sym, "market", "buy", None, params={"reduceOnly": True, "closePosition": True})
                                    except Exception:
                                        pass
                            else:
                                log("[DRY_RUN] close short", sym)
                except Exception as e:
                    log("manage fail", sym, str(e))

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            log("Stopping…")
            break
        except ccxt.RateLimitExceeded:
            log("Rate limit; sleeping 10s")
            time.sleep(10)
        except Exception as e:
            log("Loop error:", str(e))
            traceback.print_exc()
            time.sleep(5)

if __name__ == "__main__":
    main()
