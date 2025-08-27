import time
import traceback
from datetime import datetime, UTC

import pandas as pd
import ccxt

from bot.config import (
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
    NON_SCALP_ENABLED,
)
from bot.utils import log
from bot.state import STATE
from bot.exchange_client import exchange, set_leverage_and_margin
from bot.market_data import top_usdt_perps, fetch_ohlcv_df
from bot.indicators import add_indicators, valid_row
from bot.signals import trend_and_signal, score_signal
from bot.risk import equity_from_balance, size_position, round_qty, protective_prices
from bot.strategies import load_strategies
from bot.orders import cancel_reduce_only_orders, place_bracket_orders, maybe_update_trailing, place_reduce_only_exits, place_multi_target_orders
from bot.positions import get_open_positions, wait_for_position_visible
from bot.storage import write_trade
from bot.workers import pnl_worker
from bot.workers import monitor_worker
from bot.workers import scalp1m_worker
import threading
import uvicorn
from bot.ui.app import app as ui_app
from bot.config import VERTEX_ENABLED, VERTEX_MODE, VERTEX_MIN_CONF
try:
    from bot.ai.vertex.client import VERTEX
except Exception:
    VERTEX = None


def run():
    ex = exchange()
    ex.load_markets()
    strategies = load_strategies()
    try:
        log("Enabled strategies:", ", ".join(s.id for s in strategies))
    except Exception:
        pass
    last_candle_time = None
    last_flat_scan_ts = 0.0

    # Background workers (read-only) — unified monitor + pnl
    monitor_worker.start(ex)
    try:
        scalp1m_worker.start(ex)
        log("scalp_1m_trail worker started")
    except Exception:
        pass
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
                from bot.state import STATE as _S
                _S.set_universe(universe)
            except Exception:
                pass
            log("[Orchestrator] Universe:", ", ".join(universe))

            # Existing positions
            open_pos = get_open_positions(ex)
            open_syms = set(open_pos.keys())
            # Exclude scalp_1m_trail positions from the global capacity count
            core_open_syms = {s for s in open_syms if (STATE.get_strategy_meta(s) or {}).get("strategy") != "scalp_1m_trail"}
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
            capacity_remaining = max(0, MAX_POSITIONS - len(core_open_syms))
            if not new_candle and capacity_remaining > 0 and (now_ts - last_flat_scan_ts) >= SCAN_WHEN_FLAT_SECONDS:
                should_flat_scan = True
                last_flat_scan_ts = now_ts

            if NON_SCALP_ENABLED and (new_candle or should_flat_scan):
                # Strategy-based scan (reuse loaded strategies)
                # Prefetch data per symbol/timeframe for strategies' needs
                symbol_to_tf_data = {}
                # Collect max requirements across strategies
                reqs = {}
                for s in strategies:
                    for tf, lookback in s.required_timeframes().items():
                        reqs[tf] = max(reqs.get(tf, 0), lookback)
                for sym in universe:
                    tf_map = {}
                    for tf, lookback in reqs.items():
                        try:
                            tf_map[tf] = fetch_ohlcv_df(ex, sym, tf, limit=lookback)
                        except Exception as e:
                            tf_map[tf] = None
                    symbol_to_tf_data[sym] = tf_map

                # Let strategies prepare
                for s in strategies:
                    try:
                        s.prepare(symbol_to_tf_data)
                    except Exception:
                        pass

                # Evaluate decisions
                decisions = []
                for sym in universe:
                    for s in strategies:
                        try:
                            data = {tf: df for tf, df in symbol_to_tf_data[sym].items() if df is not None}
                            d = s.decide(sym, data)
                            if d and d.side in ("long", "short"):
                                # Optional Vertex AI confirmation/blend
                                if VERTEX_ENABLED and VERTEX and VERTEX.ready:
                                    inst = {
                                        "symbol": sym,
                                        "strategy": s.id,
                                        "side": d.side,
                                        "entry": float(d.entry_price or 0.0),
                                        "atr": float(d.atr or 0.0),
                                        "score": float(d.score or 0.0),
                                        "confidence": float(d.confidence or 0.0),
                                    }
                                    vp = VERTEX.predict(inst) or {}
                                    v_side = (vp.get("side") or "none").lower()
                                    v_conf = float(vp.get("confidence") or 0.0)
                                    if VERTEX_MODE == "confirm":
                                        if v_side != d.side or v_conf < VERTEX_MIN_CONF:
                                            d = None
                                    elif VERTEX_MODE == "blend":
                                        # Blend confidence (capped 0..1)
                                        try:
                                            d.confidence = max(0.0, min(1.0, 0.5 * (d.confidence or 0.0) + 0.5 * v_conf))
                                            d.score = max(0.0, min(100.0, d.confidence * 100.0))
                                        except Exception:
                                            pass
                                    else:
                                        # signals mode: allow as-is (decision stands)
                                        pass
                                if not d:
                                    continue
                                decisions.append(d)
                        except Exception as e:
                            log("strategy decide fail", s.id, sym, str(e))

                # Rank using confidence (0..1) first, then normalized score (score clamped to 0..100)
                def _rank_key(d):
                    try:
                        conf = float(d.confidence or 0.0)
                    except Exception:
                        conf = 0.0
                    try:
                        norm = float(d.score or 0.0) / 100.0
                    except Exception:
                        norm = 0.0
                    if norm < 0.0:
                        norm = 0.0
                    if norm > 1.0:
                        norm = 1.0
                    return (conf, norm)

                # Build per-strategy groups
                strat_to_ds = {}
                for d in decisions:
                    strat_to_ds.setdefault(d.strategy_id, []).append(d)
                for sid in strat_to_ds:
                    strat_to_ds[sid].sort(key=_rank_key, reverse=True)

                capacity = max(0, MAX_POSITIONS - len(core_open_syms))
                selected = []
                if capacity > 0:
                    # First pass: give mtf_5m_high_conf explicit priority if present
                    preferred = "mtf_5m_high_conf"
                    if preferred in strat_to_ds and capacity > 0 and len(strat_to_ds[preferred]) > 0:
                        selected.append(strat_to_ds[preferred][0])
                        capacity -= 1
                    # Then pick best from each remaining strategy (diversity)
                    ordered_sids = sorted(
                        [sid for sid in strat_to_ds.keys() if sid != preferred],
                        key=lambda s: _rank_key(strat_to_ds[s][0]),
                        reverse=True,
                    )
                    for sid in ordered_sids:
                        if capacity <= 0:
                            break
                        best = strat_to_ds[sid][0]
                        selected.append(best)
                        capacity -= 1
                    # Second pass: fill remaining from the pool of leftover candidates by rank
                    if capacity > 0:
                        leftovers = []
                        for sid, arr in strat_to_ds.items():
                            leftovers.extend(arr[1:])
                        leftovers.sort(key=_rank_key, reverse=True)
                        for d in leftovers:
                            if capacity <= 0:
                                break
                            selected.append(d)
                            capacity -= 1
                # Fall back if capacity was zero or no groups
                if not selected:
                    selected = sorted(decisions, key=_rank_key, reverse=True)

                log(
                    "Top decisions (balanced):",
                    [
                        (d.symbol, d.strategy_id, d.side, round(d.score or 0.0, 2), round((d.confidence or 0.0), 2))
                        for d in selected[:5]
                    ],
                )

                equity = equity_from_balance(ex)
                placed = 0
                for d in selected:
                    sym, side_sig, entry_price, atr = d.symbol, d.side, d.entry_price, d.atr
                    if sym in open_syms:
                        continue
                    if placed >= max(0, MAX_POSITIONS - len(core_open_syms)):
                        break
                    if atr is None or atr <= 0 or entry_price is None:
                        continue

                    stop, tp = d.stop, d.take_profit
                    if stop is None or tp is None:
                        stop, tp, _ = protective_prices("buy" if side_sig=="long" else "sell",
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
                    # Global spread guard
                    try:
                        from bot.state import STATE as _S
                        from bot.config import MAX_SPREAD_PCT_GLOBAL
                        q = _S.get_quote(sym)
                        if q and q.get("bid") and q.get("ask") and q["ask"] > 0:
                            sp = (q["ask"] - q["bid"]) / q["ask"]
                            if sp > (MAX_SPREAD_PCT_GLOBAL / 100.0):
                                log("skip by spread guard", sym, round(sp*100, 4), "%")
                                continue
                    except Exception:
                        pass
                    try:
                        # Prefer multi-target if provided by strategy
                        if d.targets and d.splits:
                            place_multi_target_orders(ex, sym, side_ex, qty, entry_price, d.initial_stop or stop, d.targets, d.splits)
                            try:
                                from bot.state import STATE as _S
                                base_meta = {
                                    "strategy": d.strategy_id,
                                    "confidence": round((d.confidence or 0.0), 4),
                                    "targets": [float(x) for x in (d.targets or [])],
                                    "splits": [float(x) for x in (d.splits or [])],
                                    "initial_stop": float(d.initial_stop or stop),
                                    "entry": float(entry_price),
                                    "qty": float(qty),
                                }
                                # Merge any strategy-provided meta hints
                                if getattr(d, 'meta', None):
                                    try:
                                        m = dict(d.meta)
                                        base_meta.update(m)
                                    except Exception:
                                        pass
                                _S.set_strategy_meta(sym, base_meta)
                            except Exception:
                                pass
                        else:
                            place_bracket_orders(ex, sym, side_ex, qty, entry_price, stop, tp)
                        # After entry, poll briefly so positions become visible ASAP for workers/UI
                        wait_for_position_visible(ex, sym, timeout_seconds=6.0, poll_seconds=0.5)
                        write_trade({
                            "time": datetime.now(UTC).astimezone(TZ).isoformat(),
                            "symbol": sym,
                            "side": side_sig,
                            "strategy": d.strategy_id,
                            "confidence": round((d.confidence or 0.0), 4),
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


