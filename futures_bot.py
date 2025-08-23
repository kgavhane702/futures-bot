import os, time, math, traceback
from datetime import datetime, UTC
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

from dotenv import load_dotenv
import ccxt

from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, ADXIndicator
from ta.volatility import AverageTrueRange

load_dotenv()

# ==== Exchange / General ====
EXCHANGE_ID        = os.getenv("EXCHANGE", "binanceusdm")
API_KEY            = os.getenv("API_KEY", "")
API_SECRET         = os.getenv("API_SECRET", "")
USE_TESTNET        = os.getenv("USE_TESTNET", "true").lower() == "true"

# ==== Strategy Timeframes ====
TIMEFRAME          = os.getenv("TIMEFRAME", "15m")   # lower timeframe (LTF)
HTF_TIMEFRAME      = os.getenv("HTF_TIMEFRAME", "1h")# higher timeframe (HTF) for confirmation

UNIVERSE_SIZE      = int(os.getenv("UNIVERSE_SIZE", "12"))
MAX_POSITIONS      = int(os.getenv("MAX_POSITIONS", "1"))

# ==== Risk Management ====
ACCOUNT_EQUITY_USDT= float(os.getenv("ACCOUNT_EQUITY_USDT", "100"))
RISK_PER_TRADE     = float(os.getenv("RISK_PER_TRADE", "0.01"))  # 1% of equity
ABS_RISK_USDT      = float(os.getenv("ABS_RISK_USDT", "0"))      # if > 0, overrides RISK_PER_TRADE
LEVERAGE           = int(os.getenv("LEVERAGE", "3"))
MARGIN_MODE        = os.getenv("MARGIN_MODE", "cross")           # cross/isolated

# Notional & Margin guards
MAX_NOTIONAL_FRACTION = float(os.getenv("MAX_NOTIONAL_FRACTION", "0.30"))  # cap of equity*leverage
MIN_NOTIONAL_USDT     = float(os.getenv("MIN_NOTIONAL_USDT", "10"))        # skip too-small orders
MARGIN_BUFFER_FRAC    = float(os.getenv("MARGIN_BUFFER_FRAC", "0.90"))     # 90% buffer of cap

# ==== Signal Settings ====
EMA_FAST           = int(os.getenv("EMA_FAST", "50"))
EMA_SLOW           = int(os.getenv("EMA_SLOW", "200"))
RSI_PERIOD         = int(os.getenv("RSI_PERIOD", "14"))
RSI_LONG_MIN       = float(os.getenv("RSI_LONG_MIN", "52"))
RSI_SHORT_MAX      = float(os.getenv("RSI_SHORT_MAX", "48"))
ADX_PERIOD         = int(os.getenv("ADX_PERIOD", "14"))
MIN_ADX            = float(os.getenv("MIN_ADX", "18"))  # filter chop; raise to be pickier (e.g., 20–25)

# SL/TP & Trailing
ATR_MULT_SL        = float(os.getenv("ATR_MULT_SL", "2.0"))
TP_R_MULT          = float(os.getenv("TP_R_MULT", "2.0"))
BREAKEVEN_AFTER_R  = float(os.getenv("BREAKEVEN_AFTER_R", "1.0"))   # move SL to BE after +1R
TRAIL_AFTER_R      = float(os.getenv("TRAIL_AFTER_R", "1.5"))       # start trailing after +1.5R
TRAIL_ATR_MULT     = float(os.getenv("TRAIL_ATR_MULT", "1.0"))      # trailing stop distance = 1.0 * ATR

# Ops
POLL_SECONDS       = int(os.getenv("POLL_SECONDS", "30"))
TRADES_CSV         = os.getenv("LOG_TRADES_CSV", "trades_futures.csv")
DRY_RUN            = os.getenv("DRY_RUN", "true").lower() == "true"
ALLOW_SHORTS       = os.getenv("ALLOW_SHORTS", "true").lower() == "true"  # selling allowed (futures)

# ==== Timezone / Helpers ====
TIMEZONE_CFG       = os.getenv("TIMEZONE", "indian").strip().lower()

def _resolve_tz_name(cfg: str) -> str:
    if cfg in ("indian", "ist", "asia/kolkata", "asia/calcutta"):
        return "Asia/Kolkata"
    if cfg in ("utc",):
        return "UTC"
    return cfg or "Asia/Kolkata"

TZ_NAME            = _resolve_tz_name(TIMEZONE_CFG)
TZ                 = UTC if TZ_NAME.upper() == "UTC" else ZoneInfo(TZ_NAME)
def log(*a):
    print(datetime.now(UTC).astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S"), "-", *a, flush=True)

def exchange():
    klass = getattr(ccxt, EXCHANGE_ID)
    ex = klass({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "enableRateLimit": True,
        "options": {
            "defaultType": "future",     # Futures only (no spot)
            "adjustForTimeDifference": True
        }
    })
    try:
        ex.set_sandbox_mode(USE_TESTNET)
        log("Sandbox mode:", USE_TESTNET)
    except Exception:
        pass
    return ex

def top_usdt_perps(ex, n=12):
    ex.load_markets()
    symbols = []
    for s, m in ex.markets.items():
        # USD-M linear perps only (no coin-M, no spot)
        if m.get("swap") and m.get("linear") and m.get("quote") == "USDT":
            symbols.append(s)
    tickers = ex.fetch_tickers(symbols)
    scored = []
    for sym, t in tickers.items():
        qv = t.get("quoteVolume")
        if qv is None:
            qv = float(t.get("info", {}).get("quoteVolume", 0) or 0)
        scored.append((sym, qv))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in scored[:n]]

def fetch_ohlcv_df(ex, symbol, tf, limit=400):
    data = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df

def add_indicators(df):
    df = df.copy()
    df["ema_fast"] = EMAIndicator(df["close"], window=EMA_FAST).ema_indicator()
    df["ema_slow"] = EMAIndicator(df["close"], window=EMA_SLOW).ema_indicator()
    df["rsi"]      = RSIIndicator(df["close"], window=RSI_PERIOD).rsi()
    df["atr"]      = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
    df["adx"]      = ADXIndicator(df["high"], df["low"], df["close"], window=ADX_PERIOD).adx()
    return df

def valid_row(row):
    return not pd.isna(row[["ema_fast","ema_slow","rsi","atr","adx"]]).any()

def trend_and_signal(ltf, htf):
    """
    Multi-timeframe confluence with ADX:
      LONG:  LTF ema_fast>ema_slow & RSI>=RSI_LONG_MIN & ADX>=MIN_ADX
             AND HTF ema_fast>ema_slow & ADX>=MIN_ADX
      SHORT: LTF ema_fast<ema_slow & RSI<=RSI_SHORT_MAX & ADX>=MIN_ADX
             AND HTF ema_fast<ema_slow & ADX>=MIN_ADX
    """
    if len(ltf) < max(EMA_SLOW, RSI_PERIOD) + 2 or len(htf) < max(EMA_SLOW, RSI_PERIOD) + 2:
        return "none", None

    l = ltf.iloc[-2]  # last closed candle
    h = htf.iloc[-2]
    if not (valid_row(l) and valid_row(h)):
        return "none", None

    long_ok  = (l["ema_fast"] > l["ema_slow"]) and (l["rsi"] >= RSI_LONG_MIN)  and (l["adx"] >= MIN_ADX) \
               and (h["ema_fast"] > h["ema_slow"]) and (h["adx"] >= MIN_ADX)

    short_ok = (l["ema_fast"] < l["ema_slow"]) and (l["rsi"] <= RSI_SHORT_MAX) and (l["adx"] >= MIN_ADX) \
               and (h["ema_fast"] < h["ema_slow"]) and (h["adx"] >= MIN_ADX)

    if long_ok:
        return "up", "long"
    if ALLOW_SHORTS and short_ok:
        return "down", "short"
    return "none", None

def score_signal(side, lrow):
    # Rank stronger trends first: EMA separation + ADX + RSI distance from 50 (favor momentum)
    ema_gap = abs(lrow["ema_fast"] - lrow["ema_slow"]) / max(1e-9, abs(lrow["ema_slow"]))
    rsi_term = (lrow["rsi"] - 50) if side == "long" else (50 - lrow["rsi"])
    score = float(ema_gap * 1000 + max(0.0, rsi_term) + max(0.0, lrow["adx"] - MIN_ADX))
    return score

def round_qty(ex, symbol, qty):
    try:
        return float(ex.amount_to_precision(symbol, qty))
    except Exception:
        m = ex.market(symbol)
        step = (m.get("limits", {}).get("amount", {}).get("min", 0)) or 0.0001
        return math.floor(qty / step) * step

def equity_from_balance(ex):
    try:
        b = ex.fetch_balance()
        total = b.get("total", {}).get("USDT", None)
        if total is not None:
            return float(total)
    except Exception:
        pass
    return ACCOUNT_EQUITY_USDT

def compute_risk_usdt(equity_usdt):
    if ABS_RISK_USDT and ABS_RISK_USDT > 0:
        return float(ABS_RISK_USDT)
    return max(1.0, equity_usdt * RISK_PER_TRADE)

def size_position(entry, stop, equity_usdt):
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return 0.0
    risk_usdt = compute_risk_usdt(equity_usdt)
    qty_risk = risk_usdt / stop_dist

    notional_cap = equity_usdt * LEVERAGE * MAX_NOTIONAL_FRACTION * MARGIN_BUFFER_FRAC
    qty_cap = notional_cap / entry if entry > 0 else 0.0
    qty = min(qty_risk, qty_cap)
    return max(qty, 0.0)

def set_leverage_and_margin(ex, symbol):
    try:
        if hasattr(ex, "set_leverage"):
            ex.set_leverage(LEVERAGE, symbol=symbol)
            log("set_leverage ok", symbol, LEVERAGE)
    except Exception as e:
        log("set_leverage failed", symbol, str(e))
    try:
        if hasattr(ex, "set_margin_mode"):
            ex.set_margin_mode(MARGIN_MODE, symbol=symbol)
            log("set_margin_mode ok", symbol, MARGIN_MODE)
    except Exception as e:
        log("set_margin_mode failed", symbol, str(e))

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

def place_bracket_orders(ex, symbol, side, qty, entry_price, sl_price, tp_price):
    opposite = "sell" if side == "buy" else "buy"

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
        return {"id": f"dry_{int(time.time())}"}

    entry = ex.create_order(symbol, type="market", side=side, amount=qty)
    log("ENTRY", entry.get("id"), side, qty, symbol)

    params = {"reduceOnly": True}
    try:
        ex.create_order(symbol, type="STOP_MARKET", side=opposite, amount=qty,
                        params={**params, "stopPrice": float(sl_price)})
        log("SL placed", sl_price)
    except Exception as e:
        log("Failed to place SL:", str(e))
    try:
        ex.create_order(symbol, type="TAKE_PROFIT_MARKET", side=opposite, amount=qty,
                        params={**params, "stopPrice": float(tp_price)})
        log("TP placed", tp_price)
    except Exception as e:
        log("Failed to place TP:", str(e))
    return entry

def protective_prices(side, entry, atr, r_mult=TP_R_MULT):
    if side == "buy":
        stop = entry - ATR_MULT_SL * atr
        r = entry - stop
        tp = entry + r_mult * r
    else:
        stop = entry + ATR_MULT_SL * atr
        r = stop - entry
        tp = entry - r_mult * r
    return stop, tp, r

def maybe_update_trailing(ex, symbol, side, qty, entry, atr, last_price):
    """
    After price reaches BE/Trail thresholds, cancel and replace reduceOnly stops.
    This is a coarse per-candle adjust; fine-grained trailing would need websockets.
    """
    if DRY_RUN:
        return
    # Fetch open reduceOnly orders
    try:
        orders = get_open_orders(ex, symbol)
    except Exception:
        orders = []

    # Determine BE/Trail levels
    if side == "buy":
        r = ATR_MULT_SL * atr
        be_trigger = entry + BREAKEVEN_AFTER_R * r
        trail_trigger = entry + TRAIL_AFTER_R * r
        if last_price >= be_trigger:
            # move SL to entry (breakeven) or trail if above trail_trigger
            new_sl = entry if last_price < trail_trigger else (last_price - TRAIL_ATR_MULT * atr)
            cancel_reduce_only_orders(ex, symbol)
            try:
                ex.create_order(symbol, "STOP_MARKET", "sell", qty, params={"reduceOnly": True, "stopPrice": float(new_sl)})
                log("Trailing/BE SL updated", symbol, new_sl)
            except Exception as e:
                log("Failed trailing SL:", str(e))
    else:
        r = ATR_MULT_SL * atr
        be_trigger = entry - BREAKEVEN_AFTER_R * r
        trail_trigger = entry - TRAIL_AFTER_R * r
        if last_price <= be_trigger:
            new_sl = entry if last_price > trail_trigger else (last_price + TRAIL_ATR_MULT * atr)
            cancel_reduce_only_orders(ex, symbol)
            try:
                ex.create_order(symbol, "STOP_MARKET", "buy", qty, params={"reduceOnly": True, "stopPrice": float(new_sl)})
                log("Trailing/BE SL updated", symbol, new_sl)
            except Exception as e:
                log("Failed trailing SL:", str(e))

def write_trade(row):
    df = pd.DataFrame([row])
    header = not os.path.exists(TRADES_CSV)
    df.to_csv(TRADES_CSV, mode="a", index=False, header=header)

def main():
    ex = exchange()
    ex.load_markets()
    last_candle_time = None

    while True:
        try:
            # Heartbeat: wait for a *new* closed LTF candle
            hb = ex.fetch_ohlcv("BTC/USDT", timeframe=TIMEFRAME, limit=3)
            hb_df = pd.DataFrame(hb, columns=["ts","o","h","l","c","v"])
            hb_df["ts"] = pd.to_datetime(hb_df["ts"], unit="ms", utc=True)
            latest_closed_ts = hb_df.iloc[-2]["ts"]
            if last_candle_time == latest_closed_ts:
                log("waiting for next candle...")
                time.sleep(POLL_SECONDS)
                continue
            last_candle_time = latest_closed_ts
            # Display in configured TZ
            log(f"New {TIMEFRAME} close @ {latest_closed_ts.tz_convert(TZ)}")

            # Build universe
            universe = top_usdt_perps(ex, UNIVERSE_SIZE)
            log("Universe:", ", ".join(universe))

            # Existing positions
            open_pos = get_open_positions(ex)
            open_syms = set(open_pos.keys())
            log("Open positions:", open_pos)

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

                stop, tp, r_per_unit = protective_prices("buy" if side_sig=="long" else "sell",
                                                         entry_price, atr, TP_R_MULT)

                qty = size_position(entry_price, stop, equity)
                qty = round_qty(ex, sym, qty)
                if qty <= 0:
                    log("qty after rounding <= 0, skip", sym)
                    continue

                notional = qty * entry_price
                # Upper notional guard (approximate initial margin = notional / leverage)
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
                        "dry_run": DRY_RUN
                    })
                    placed += 1
                except ccxt.BaseError as e:
                    log("Order rejected:", sym, str(e))

            # Manage open positions: breakeven / trailing, and flip exits
            for sym, pos in get_open_positions(ex).items():
                try:
                    ltf = fetch_ohlcv_df(ex, sym, TIMEFRAME, limit=200)
                    ltf = add_indicators(ltf)
                    last = ltf.iloc[-1]  # current forming candle price for trail checks
                    prev = ltf.iloc[-2]  # closed bar for flip checks
                    if not valid_row(prev):
                        continue
                    # Trail / BE (approximation)
                    maybe_update_trailing(ex, sym, "long" if pos["side"]=="long" else "short",
                                          pos["size"], prev["close"], prev["atr"], last["close"])

                    # Flip exit: if signal on LTF now favors the opposite side, close
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
