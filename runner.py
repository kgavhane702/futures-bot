import time
import traceback
from datetime import datetime, UTC

import pandas as pd
import ccxt

from config import (
    TIMEFRAME,
    HTF_TIMEFRAME,
    UNIVERSE_SIZE,
    MAX_POSITIONS,
    TP_R_MULT,
    POLL_SECONDS,
    LEVERAGE,
    MAX_NOTIONAL_FRACTION,
    MIN_NOTIONAL_USDT,
    TZ,
    DRY_RUN,
    ORPHAN_PROTECT_SECONDS,
    ORPHAN_MIN_AGE_SECONDS,
    SCAN_WHEN_FLAT_SECONDS,
)
from utils import log
from state import STATE
from exchange_client import exchange, set_leverage_and_margin
from market_data import top_usdt_perps, fetch_ohlcv_df
from indicators import add_indicators, valid_row
from signals import trend_and_signal, score_signal
from risk import equity_from_balance, size_position, round_qty, protective_prices
from orders import cancel_reduce_only_orders, place_bracket_orders, maybe_update_trailing, place_reduce_only_exits
from positions import get_open_positions, wait_for_position_visible
from storage import write_trade
from workers import pnl_worker
from workers import monitor_worker
import threading
import uvicorn
from ui.app import app as ui_app


def run():
    ex = exchange()
    ex.load_markets()
    last_candle_time = None
    last_flat_scan_ts = 0.0

    # Background workers (read-only) — unified monitor + pnl
    monitor_worker.start(ex)
    pnl_worker.start(ex, lambda: get_open_positions(ex), lambda: universe if 'universe' in locals() else [])

    # Start UI server (non-blocking) inside same process
    def _serve_ui():
        uvicorn.run(ui_app, host="0.0.0.0", port=8000, log_level="warning")

    threading.Thread(target=_serve_ui, daemon=True).start()

    while True:
        try:
            # Heartbeat: detect if a new closed LTF candle is available
            hb = ex.fetch_ohlcv("BTC/USDT", timeframe=TIMEFRAME, limit=3)
            hb_df = pd.DataFrame(hb, columns=["ts","o","h","l","c","v"])
            hb_df["ts"] = pd.to_datetime(hb_df["ts"], unit="ms", utc=True)
            latest_closed_ts = hb_df.iloc[-2]["ts"]
            new_candle = last_candle_time != latest_closed_ts
            if new_candle:
                last_candle_time = latest_closed_ts
                log(f"New {TIMEFRAME} close @ {latest_closed_ts.tz_convert(TZ)}")

            # Orphan cleanup is handled by monitor_worker every few seconds

            # Build universe and persist to state for UI/PNL worker
            universe = top_usdt_perps(ex, UNIVERSE_SIZE)
            try:
                from state import STATE as _S
                _S.set_universe(universe)
            except Exception:
                pass
            log("[Orchestrator] Universe:", ", ".join(universe))

            # Existing positions
            open_pos = get_open_positions(ex)
            open_syms = set(open_pos.keys())
            log("[Orchestrator] Open positions:", open_pos)

            # Phase 2: Reconcile exits for existing positions
            for sym, pos in open_pos.items():
                try:
                    ltf = fetch_ohlcv_df(ex, sym, TIMEFRAME, limit=200)
                    ltf = add_indicators(ltf)
                    prev = ltf.iloc[-2]
                    if not valid_row(prev):
                        continue
                    entry_proxy = prev["close"]
                    stop, tp, _ = protective_prices("buy" if pos["side"]=="long" else "sell", entry_proxy, prev["atr"], TP_R_MULT)
                    # If no reduce-only orders exist, place them
                    has_reduce_only = False
                    try:
                        for o in ex.fetch_open_orders(sym):
                            if o.get("reduceOnly"):
                                has_reduce_only = True
                                break
                    except Exception:
                        has_reduce_only = False
                    if not has_reduce_only and pos.get("size", 0) > 0:
                        place_reduce_only_exits(ex, sym, pos["side"], pos["size"], stop, tp)
                except Exception as e:
                    log("reconcile fail", sym, str(e))

            # Scan + act on new candle, OR do a lightweight scan if under capacity for too long
            should_flat_scan = False
            now_ts = time.time()
            capacity_remaining = max(0, MAX_POSITIONS - len(open_syms))
            if not new_candle and capacity_remaining > 0 and (now_ts - last_flat_scan_ts) >= SCAN_WHEN_FLAT_SECONDS:
                should_flat_scan = True
                last_flat_scan_ts = now_ts

            if new_candle or should_flat_scan:
                # Scan signals
                cands = []
                for sym in universe:
                    try:
                        ltf = fetch_ohlcv_df(ex, sym, TIMEFRAME, limit=400)
                        htf = fetch_ohlcv_df(ex, sym, HTF_TIMEFRAME, limit=400)
                        ltf = add_indicators(ltf)
                        htf = add_indicators(htf)
                        tr, side = trend_and_signal(ltf, htf)
                        if side is None:
                            continue
                        lrow = ltf.iloc[-2]
                        score = score_signal(side, lrow)
                        cands.append((sym, side, float(lrow["close"]), float(lrow["atr"]), score))
                    except Exception as e:
                        log("scan fail", sym, str(e))

                cands.sort(key=lambda x: x[4], reverse=True)
                log("Top signals (flat-scan):" if (should_flat_scan and not new_candle) else "Top signals:", cands[:5])

                equity = equity_from_balance(ex)
                placed = 0
                for sym, side_sig, entry_price, atr, _ in cands:
                    if sym in open_syms:
                        continue
                    if placed >= max(0, MAX_POSITIONS - len(open_syms)):
                        break
                    if atr <= 0:
                        continue

                    stop, tp, r_per_unit = protective_prices("buy" if side_sig=="long" else "sell",
                                                             entry_price, atr, TP_R_MULT)

                    qty = size_position(entry_price, stop, equity)
                    qty = round_qty(ex, sym, qty)
                    if qty <= 0:
                        log("qty after rounding <= 0, skip", sym)
                        continue

                    notional = qty * entry_price
                    if notional > equity * LEVERAGE * MAX_NOTIONAL_FRACTION:
                        log(f"SKIP {sym}: notional {notional:.2f} exceeds cap {equity*LEVERAGE*MAX_NOTIONAL_FRACTION:.2f}")
                        continue
                    if notional < MIN_NOTIONAL_USDT:
                        log(f"SKIP {sym}: notional {notional:.2f} < MIN_NOTIONAL_USDT {MIN_NOTIONAL_USDT}")
                        continue

                    set_leverage_and_margin(ex, sym)
                    cancel_reduce_only_orders(ex, sym)

                    side_ex = "buy" if side_sig == "long" else "sell"
                    try:
                        place_bracket_orders(ex, sym, side_ex, qty, entry_price, stop, tp)
                        # After entry, poll briefly so positions become visible ASAP for workers/UI
                        wait_for_position_visible(ex, sym, timeout_seconds=6.0, poll_seconds=0.5)
                        write_trade({
                            "time": datetime.now(UTC).astimezone(TZ).isoformat(),
                            "symbol": sym,
                            "side": side_sig,
                            "qty": qty,
                            "entry": entry_price,
                            "stop": stop,
                            "take_profit": tp,
                            "atr": atr,
                            "equity_snapshot": equity,
                            "dry_run": DRY_RUN,
                        })
                        placed += 1
                    except ccxt.BaseError as e:
                        log("Order rejected:", sym, str(e))

            # Phase 5: Manage open positions: breakeven / trailing, and flip exits
            for sym, pos in get_open_positions(ex).items():
                try:
                    ltf = fetch_ohlcv_df(ex, sym, TIMEFRAME, limit=200)
                    ltf = add_indicators(ltf)
                    last = ltf.iloc[-1]
                    prev = ltf.iloc[-2]
                    if not valid_row(prev):
                        continue
                    # Trail / BE (approximation) — preserve original behavior using prev close as entry proxy
                    maybe_update_trailing(ex, sym, "long" if pos["side"]=="long" else "short",
                                          pos["size"], prev["close"], prev["atr"], last["close"])

                    # Flip exit: simple EMA flip on LTF
                    tr = "up" if prev["ema_fast"] > prev["ema_slow"] else "down"
                    if pos["side"] == "long" and tr == "down":
                        log("Flip out of long — closing", sym)
                        if not DRY_RUN:
                            ex.create_order(sym, "market", "sell", pos["size"], params={"reduceOnly": True})
                        else:
                            log("[DRY_RUN] close long", sym)
                    elif pos["side"] == "short" and tr == "up":
                        log("Flip out of short — closing", sym)
                        if not DRY_RUN:
                            ex.create_order(sym, "market", "buy", pos["size"], params={"reduceOnly": True})
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
    run()


