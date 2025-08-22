import time, traceback, os, threading
from datetime import datetime, UTC
import pandas as pd
import ccxt

from .config import (TIMEFRAME, HTF_TIMEFRAME, UNIVERSE_SIZE, MAX_POSITIONS,
                     ATR_MULT_SL, TP_R_MULT, POLL_SECONDS, TRADES_CSV, DRY_RUN,
                     ORPHAN_SWEEP_SECONDS, ORPHAN_SWEEP_GRACE_SECONDS,
                     PROTECTION_CHECK_SECONDS, ENTRY_SLIPPAGE_MAX_PCT, TOTAL_NOTIONAL_CAP_FRACTION, LEVERAGE)
from .logging_utils import log
from .web import get_control_flags
import os, sys
try:
    from .web import start_web_server, update_state
except Exception:
    def start_web_server():
        pass
    def update_state(**kwargs):
        return {}
from .exchange_client import get_exchange, ensure_symbol_config, round_amount
from .universe import top_usdt_perps
from .indicators import fetch_ohlcv_df, add_indicators, valid_row
from .signals import trend_and_signal, score_signal
from .risk import equity_from_balance, size_position, protective_prices
from .orders import (get_open_positions, get_open_orders, cancel_reduce_only_orders, place_bracket_orders,
                     reconcile_orphan_reduce_only_orders, replace_stop_loss_close_position,
                     get_current_stop_loss_price, cancel_stop_loss_orders,
                     adjust_protection_prices, enforce_trigger_distance_with_last)

def write_trade(row):
    header = not os.path.exists(TRADES_CSV)
    pd.DataFrame([row]).to_csv(TRADES_CSV, mode="a", index=False, header=header)

def main():
    ex = get_exchange()
    ex.load_markets()
    last_candle_time = None
    last_orphan_sweep_ts = 0

    # No background threads in simplified mode (avoid automatic repairs/sweeps)

    # Start web UI if enabled
    try:
        from .config import ENABLE_WEB
        if ENABLE_WEB:
            start_web_server()
    except Exception:
        pass

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
            update_state(last_candle_time=str(latest_closed_ts))

            # Universe & snapshot
            universe = top_usdt_perps(ex, UNIVERSE_SIZE)
            log("Universe:", ", ".join(universe))
            update_state(universe=universe)

            open_pos = get_open_positions(ex)
            open_syms = set(open_pos.keys())
            log("Open positions:", open_pos)
            update_state(positions=open_pos)

            # Simplified orphan logic: only when flat cancel reduceOnly for that symbol
            for sym in open_syms:
                try:
                    reconcile_orphan_reduce_only_orders(ex, sym, open_pos.get(sym), grace_cutoff_ms=None)
                except Exception:
                    pass

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
            update_state(signals=cands[:10])

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
                if stop is None or tp is None or r_per_unit <= 0:
                    log("Skip due to STOP_CAP_BEHAVIOR=skip and SL too wide", sym)
                    continue

                qty = size_position(entry_price, stop, equity)
                qty = round_amount(ex, sym, qty)
                if qty <= 0:
                    log("qty after rounding <= 0, skip", sym)
                    continue

                # Entry slippage guard (compare latest ticker to planned entry)
                try:
                    tkr = ex.fetch_ticker(sym)
                    last_px = float(tkr.get("last") or tkr.get("close") or entry_price)
                    slip = abs(last_px - entry_price) / max(1e-9, entry_price)
                    if slip > ENTRY_SLIPPAGE_MAX_PCT:
                        log("Skip due to slippage", sym, f"slip={(slip*100):.2f}% > {(ENTRY_SLIPPAGE_MAX_PCT*100):.2f}%")
                        continue
                except Exception:
                    pass

                # Total notional exposure cap across open positions
                try:
                    open_notional = 0.0
                    for osym, opos in open_pos.items():
                        plist = opos if isinstance(opos, list) else [opos]
                        # Approx notional by last known price
                        tkr_os = ex.fetch_ticker(osym)
                        last_os = float(tkr_os.get("last") or tkr_os.get("close") or 0)
                        for p in plist:
                            open_notional += float(p.get("size", 0.0)) * last_os
                    next_notional = qty * entry_price
                    total_cap = equity * LEVERAGE * TOTAL_NOTIONAL_CAP_FRACTION
                    if (open_notional + next_notional) > total_cap:
                        log("Skip due to total notional cap", sym)
                        continue
                except Exception:
                    pass

                try:
                    # Clear existing reduceOnly orders before placing a fresh bracket (older flow)
                    try:
                        cancel_reduce_only_orders(ex, sym)
                    except Exception:
                        pass
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

            # Manage open positions (multi-TP SL management)
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
                        side = p.get("side")
                        if side not in ("long", "short"):
                            continue
                        ex_side = "buy" if side == "long" else "sell"
                        opp_side = "sell" if ex_side == "buy" else "buy"
                        entry = p.get("entry")
                        if entry is None:
                            continue
                        entry = float(entry)
                        # Inspect open orders to infer TP stage
                        olist = get_open_orders(ex, sym)
                        # Filter TPs and extract prices
                        tp_prices = []
                        for o in olist:
                            try:
                                t = (o.get("type") or "").upper()
                                it = (o.get("info", {}) or {}).get("type", "").upper()
                                if ("TAKE_PROFIT" in t) or ("TAKE_PROFIT" in it):
                                    sp = o.get("stopPrice")
                                    if sp is None:
                                        sp = (o.get("info", {}) or {}).get("stopPrice")
                                    if sp is None:
                                        sp = (o.get("info", {}) or {}).get("triggerPrice")
                                    if sp is not None:
                                        tp_prices.append(float(sp))
                            except Exception:
                                continue
                        # Get current SL range for comparison
                        sl_range = get_current_stop_loss_price(ex, sym, position_side=("LONG" if ex_side=="buy" else "SHORT") if False else None)
                        current_sl_min = None
                        current_sl_max = None
                        if isinstance(sl_range, tuple):
                            current_sl_min, current_sl_max = sl_range
                        num_tp_open = len(tp_prices)
                        if num_tp_open >= 3:
                            # Nothing to do
                            continue
                        try:
                            tkr = ex.fetch_ticker(sym)
                            last_px = float(tkr.get("last") or tkr.get("close") or entry)
                        except Exception:
                            last_px = entry
                        if num_tp_open == 2:
                            # After TP1 fill: move SL to entry
                            target_sl = entry
                            # Align to exchange constraints
                            target_sl, _tmp = adjust_protection_prices(ex, sym, ex_side, entry, target_sl, entry)
                            target_sl, _tmp = enforce_trigger_distance_with_last(ex, sym, ex_side, last_px, target_sl, _tmp)
                            # Skip if already at/ beyond target (with tolerance)
                            if ex_side == "buy" and current_sl_max is not None and current_sl_max >= target_sl - 1e-9:
                                continue
                            if ex_side == "sell" and current_sl_min is not None and current_sl_min <= target_sl + 1e-9:
                                continue
                            replace_stop_loss_close_position(ex, sym, ex_side, target_sl)
                            log("SL moved to entry after TP1", sym, target_sl)
                        elif num_tp_open == 1:
                            # After TP2 fill: move SL to TP1 price = entry +/- 1R inferred from remaining TP3
                            only_tp = float(tp_prices[0])
                            # Infer 1R
                            r_unit = abs(only_tp - entry) / 3.0
                            target_sl = entry + (r_unit if ex_side == "buy" else -r_unit)
                            target_sl, _tmp = adjust_protection_prices(ex, sym, ex_side, entry, target_sl, only_tp)
                            target_sl, _tmp = enforce_trigger_distance_with_last(ex, sym, ex_side, last_px, target_sl, _tmp)
                            if ex_side == "buy" and current_sl_max is not None and current_sl_max >= target_sl - 1e-9:
                                continue
                            if ex_side == "sell" and current_sl_min is not None and current_sl_min <= target_sl + 1e-9:
                                continue
                            replace_stop_loss_close_position(ex, sym, ex_side, target_sl)
                            log("SL moved to TP1 after TP2", sym, target_sl)
                        else:
                            # After TP3 fill: cancel SL
                            try:
                                cancel_stop_loss_orders(ex, sym, position_side=("LONG" if ex_side=="buy" else "SHORT") if False else None)
                                log("SL canceled after TP3", sym)
                            except Exception:
                                pass
                except Exception as e:
                    log("manage fail", sym, str(e))

            time.sleep(POLL_SECONDS)

            # Check restart flag from admin
            try:
                flags = get_control_flags()
                if flags.get("restart"):
                    log("Restart flag detected; re-exec process…")
                    python = sys.executable
                    os.execv(python, [python, "run.py"])  # replace current process
            except Exception:
                pass

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
