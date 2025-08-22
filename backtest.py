# backtest.py
# Simple bar-based backtester that reuses your live modules.
# Outputs win rate (accuracy), avg win/loss in R, expectancy, max drawdown, and a rough Sharpe-like metric.

import math
import statistics
from datetime import datetime, timedelta, timezone
import pandas as pd

from bot.config import get_config
from bot.exchange_client import get_exchange
from bot.universe import top_usdt_perps
from bot.indicators import fetch_ohlcv_df, add_indicators, valid_row
from bot.signals import trend_and_signal, score_signal
from bot.risk import protective_prices

# ---- Load config from .env via get_config() ----
cfg = get_config()

TIMEFRAME = cfg["TIMEFRAME"]
HTF_TIMEFRAME = cfg["HTF_TIMEFRAME"]
UNIVERSE_SIZE = cfg["UNIVERSE_SIZE"]

ATR_MULT_SL = cfg["ATR_MULT_SL"]
TP_R_MULT = cfg["TP_R_MULT"]
BREAKEVEN_AFTER_R = cfg["BREAKEVEN_AFTER_R"]
TRAIL_AFTER_R = cfg["TRAIL_AFTER_R"]
TRAIL_ATR_MULT = cfg["TRAIL_ATR_MULT"]
MAX_SL_PCT = cfg["MAX_SL_PCT"]
STOP_CAP_BEHAVIOR = cfg["STOP_CAP_BEHAVIOR"]

ENTRY_SLIPPAGE_MAX_PCT = cfg["ENTRY_SLIPPAGE_MAX_PCT"]
TOTAL_NOTIONAL_CAP_FRACTION = cfg["TOTAL_NOTIONAL_CAP_FRACTION"]

# ========== Helpers ==========
def _drawdown(series):
    peak = float("-inf")
    dd = []
    for x in series:
        peak = max(peak, x)
        dd.append(0 if peak == 0 else (x - peak) / peak)
    return min(dd) if dd else 0.0

def _sharpe(returns_daily):
    if not returns_daily:
        return 0.0
    mean = statistics.fmean(returns_daily)
    stdev = statistics.pstdev(returns_daily) or 1e-12
    # daily → annualized (365)
    return (mean / stdev) * math.sqrt(365)

# ========== Backtest Core ==========
def run_backtest(
    ex,
    symbols,
    timeframe=TIMEFRAME,
    htf_timeframe=HTF_TIMEFRAME,
    lookback_bars=1200,
    start_days_ago=90,
    end_days_ago=0,
    use_trailing=True,
    use_flip_exit=True,  # reserved for future refinement; not strictly required here
):
    """
    Bar-based simulator:
      - Signals computed on prev bar; entries at next bar open
      - protective_prices() used for SL/TP (respects MAX_SL_PCT & STOP_CAP_BEHAVIOR)
      - Optional BE/trailing (bar-resolution approximation)
      - One position per symbol (no pyramiding); MAX_POSITIONS not enforced in this basic tester
      - Results in R-units (scale-free)
    """
    start_ts = datetime.now(timezone.utc) - timedelta(days=start_days_ago)
    end_ts = datetime.now(timezone.utc) - timedelta(days=end_days_ago)
    # Normalize to tz-naive for comparison with OHLCV 'ts' (naive UTC)
    try:
        start_naive = pd.Timestamp(start_ts).tz_convert(None)
    except Exception:
        start_naive = pd.Timestamp(start_ts)
    try:
        end_naive = pd.Timestamp(end_ts).tz_convert(None)
    except Exception:
        end_naive = pd.Timestamp(end_ts)

    trades = []           # list of {"symbol","R","time"}
    equity_r = [0.0]      # equity curve in R-units
    open_positions = {}   # sym -> {"side","entry","sl","tp","r"}

    def maybe_close(sym, price):
        pos = open_positions.get(sym)
        if not pos:
            return False, 0.0
        side, entry, sl, tp, r = pos["side"], pos["entry"], pos["sl"], pos["tp"], pos["r"]
        if r <= 0:
            open_positions.pop(sym, None)
            return True, 0.0

        if side == "buy":
            if price <= sl:
                open_positions.pop(sym, None)
                return True, -1.0
            if price >= tp:
                open_positions.pop(sym, None)
                return True, +TP_R_MULT
        else:
            if price >= sl:
                open_positions.pop(sym, None)
                return True, -1.0
            if price <= tp:
                open_positions.pop(sym, None)
                return True, +TP_R_MULT
        return False, 0.0

    def maybe_trail(sym, last_price, prev_atr):
        if not use_trailing:
            return
        pos = open_positions.get(sym)
        if not pos:
            return
        side, entry, sl, tp, r = pos["side"], pos["entry"], pos["sl"], pos["tp"], pos["r"]
        if r <= 0 or (prev_atr is None or prev_atr <= 0):
            return

        r_unit = ATR_MULT_SL * prev_atr
        if side == "buy":
            be_trigger = entry + BREAKEVEN_AFTER_R * r_unit
            trail_trigger = entry + TRAIL_AFTER_R * r_unit
            if last_price < be_trigger:
                return
            candidate = entry if last_price < trail_trigger else (last_price - TRAIL_ATR_MULT * prev_atr)
            new_sl = max(sl, candidate, entry)
            if new_sl > sl:
                pos["sl"] = new_sl
        else:
            be_trigger = entry - BREAKEVEN_AFTER_R * r_unit
            trail_trigger = entry - TRAIL_AFTER_R * r_unit
            if last_price > be_trigger:
                return
            candidate = entry if last_price > trail_trigger else (last_price + TRAIL_ATR_MULT * prev_atr)
            new_sl = min(sl, candidate, entry)
            if new_sl < sl:
                pos["sl"] = new_sl

    # Build candidates (mimic live scoring)
    cands = []
    for sym in symbols:
        try:
            ltf = fetch_ohlcv_df(ex, sym, timeframe, limit=lookback_bars)
            htf_df = fetch_ohlcv_df(ex, sym, htf_timeframe, limit=min(lookback_bars, 1000))
            ltf = add_indicators(ltf)
            htf_df = add_indicators(htf_df)
            # range filter (use 'ts' column from indicators.fetch_ohlcv_df)
            ltf = ltf[(ltf["ts"] >= start_naive) & (ltf["ts"] <= end_naive)]
            if len(ltf) < 50:
                continue
            tr, side = trend_and_signal(ltf, htf_df, valid_row)
            if side is None:
                continue
            lrow = ltf.iloc[-2]
            score = score_signal(side, lrow)
            cands.append((sym, side, float(lrow["close"]), float(lrow.get("atr", 0) or 0), score, ltf))
        except Exception as e:
            print("scan fail", sym, e)

    cands.sort(key=lambda x: x[4], reverse=True)

    # Iterate symbol-by-symbol; within each, bar-by-bar
    for sym, _seed_side, _seed_ep, _seed_atr, _score, df in cands:
        for i in range(2, len(df)):
            prev = df.iloc[i-1]
            row  = df.iloc[i]

            # Manage open pos first
            if sym in open_positions:
                prev_atr = float(prev.get("atr") or 0)
                maybe_trail(sym, float(row["close"]), prev_atr)
                closed, rres = maybe_close(sym, float(row["close"]))
                if closed:
                    trades.append({"symbol": sym, "R": rres, "time": row["ts"]})
                    equity_r.append(equity_r[-1] + rres)

            # If flat, check entry
            if sym not in open_positions and valid_row(prev):
                side = (
                    "long" if (prev["ema_fast"] > prev["ema_slow"] and prev["rsi"] >= 52)
                    else "short" if (prev["ema_fast"] < prev["ema_slow"] and prev["rsi"] <= 48)
                    else None
                )
                if side is None:
                    continue

                entry = float(row["open"])   # next bar open
                atr   = float(prev["atr"])
                if atr <= 0:
                    continue

                side_ex = "buy" if side == "long" else "sell"
                stop, tp, r = protective_prices(side_ex, entry, atr, ATR_MULT_SL, TP_R_MULT)

                # Respect STOP_CAP_BEHAVIOR=skip (protective_prices will return None/invalid r)
                if stop is None or tp is None or r <= 0:
                    continue

                # Entry slippage check (approx vs prev close)
                slip = abs(entry - float(prev["close"])) / max(1e-9, float(prev["close"]))
                if slip > ENTRY_SLIPPAGE_MAX_PCT:
                    continue

                open_positions[sym] = {"side": side_ex, "entry": entry, "sl": stop, "tp": tp, "r": r}

            # If still open after entry, see if SL/TP got hit by close
            if sym in open_positions:
                closed, rres = maybe_close(sym, float(row["close"]))
                if closed:
                    trades.append({"symbol": sym, "R": rres, "time": row["ts"]})
                    equity_r.append(equity_r[-1] + rres)

        # Force-close any leftover at end (mark to market 0R)
        if sym in open_positions:
            trades.append({"symbol": sym, "R": 0.0, "time": df.iloc[-1]["ts"]})
            open_positions.pop(sym, None)

    # ---- Metrics ----
    wins     = [t for t in trades if t["R"] > 0]
    losses   = [t for t in trades if t["R"] < 0]
    scratches= [t for t in trades if t["R"] == 0]
    total    = max(1, len(trades))

    win_rate = (len(wins) / total) * 100
    avg_win  = statistics.fmean([t["R"] for t in wins]) if wins else 0.0
    avg_loss = statistics.fmean([t["R"] for t in losses]) if losses else 0.0
    expectancy = (win_rate/100.0) * avg_win + (1 - win_rate/100.0) * avg_loss

    # drawdown on equity-in-R (shift to avoid 0 division)
    max_dd = _drawdown([x + 100 for x in equity_r])

    # Rough daily R returns: assume ~96 bars/day for 15m
    returns_daily = []
    chunk = 96
    for i in range(chunk, len(equity_r), chunk):
        returns_daily.append(equity_r[i] - equity_r[i - chunk])
    sharpe_like = _sharpe(returns_daily)

    summary = {
        "trades": len(trades),
        "win_rate_%": round(win_rate, 2),
        "avg_win_R": round(avg_win, 2),
        "avg_loss_R": round(avg_loss, 2),
        "expectancy_R": round(expectancy, 3),
        "max_drawdown_%": round(100 * max_dd, 2),
        "sharpe_like": round(sharpe_like, 2),
        "scratches": len(scratches),
    }
    return summary, trades, equity_r

if __name__ == "__main__":
    ex = get_exchange()
    ex.load_markets()
    universe = top_usdt_perps(ex, UNIVERSE_SIZE)
    print("Universe:", universe[:12])

    summary, trades, equity_r = run_backtest(
        ex,
        symbols=universe[:12],           # limit for speed; adjust as you like
        timeframe=TIMEFRAME,
        htf_timeframe=HTF_TIMEFRAME,
        lookback_bars=1200,
        start_days_ago=90,
        end_days_ago=0,
        use_trailing=True,
        use_flip_exit=True
    )

    print("\n=== Backtest Summary (last ~90 days) ===")
    for k, v in summary.items():
        print(f"{k}: {v}")

    pd.DataFrame(trades).to_csv("backtest_trades.csv", index=False)
    print("Saved trades to backtest_trades.csv")
