import time, traceback, os
from datetime import datetime, UTC
import pandas as pd
import ccxt

from .config import (TIMEFRAME, HTF_TIMEFRAME, UNIVERSE_SIZE, MAX_POSITIONS,
                     ATR_MULT_SL, TP_R_MULT, POLL_SECONDS, TRADES_CSV, DRY_RUN,
                     BREAKEVEN_AFTER_R, TRAIL_AFTER_R, TRAIL_ATR_MULT)
from .logging_utils import log
from .exchange_client import get_exchange, ensure_symbol_config, round_amount
from .universe import top_usdt_perps
from .indicators import fetch_ohlcv_df, add_indicators, valid_row
from .signals import trend_and_signal, score_signal
from .risk import equity_from_balance, size_position, protective_prices
from .orders import (get_open_positions, cancel_reduce_only_orders, place_bracket_orders,
                     maybe_update_trailing, reconcile_orphan_reduce_only_orders)

def write_trade(row):
    header = not os.path.exists(TRADES_CSV)
    pd.DataFrame([row]).to_csv(TRADES_CSV, mode="a", index=False, header=header)

def main():
    ex = get_exchange()
    ex.load_markets()
    last_candle_time = None

    while True:
        try:
            # Wait for new closed LTF candle
            hb = ex.fetch_ohlcv("BTC/USDT", timeframe=TIMEFRAME, limit=3)
            hb_df = pd.DataFrame(hb, columns=["ts","o","h","l","c","v"])
            hb_df["ts"] = pd.to_datetime(hb_df["ts"], unit="ms")
            latest_closed_ts = hb_df.iloc[-2]["ts"]
            if last_candle_time == latest_closed_ts:
                log("waiting for next candle...")
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

            # Reconcile orphan reduceOnly orders (no pos -> cancel RO)
            for sym in set(universe) | open_syms:
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
                    if not valid_row(prev):
                        continue
                    side = "buy" if pos["side"]=="long" else "sell"
                    # Trail / BE
                    maybe_update_trailing(ex, sym, side, pos["size"], prev["close"], prev["atr"], last["close"],
                                          ATR_MULT_SL, BREAKEVEN_AFTER_R, TRAIL_AFTER_R, TRAIL_ATR_MULT)
                    # Flip exit on EMA cross
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
    main()
