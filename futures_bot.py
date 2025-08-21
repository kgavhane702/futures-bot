
import os, time, math, traceback
from datetime import datetime, UTC
import pandas as pd
import numpy as np

from dotenv import load_dotenv
import ccxt
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange

load_dotenv()

# ==== Config ====
EXCHANGE_ID        = os.getenv("EXCHANGE", "binanceusdm")
API_KEY            = os.getenv("API_KEY", "")
API_SECRET         = os.getenv("API_SECRET", "")
USE_TESTNET        = os.getenv("USE_TESTNET", "true").lower() == "true"

TIMEFRAME          = os.getenv("TIMEFRAME", "15m")
UNIVERSE_SIZE      = int(os.getenv("UNIVERSE_SIZE", "12"))
MAX_POSITIONS      = int(os.getenv("MAX_POSITIONS", "2"))

ACCOUNT_EQUITY_USDT= float(os.getenv("ACCOUNT_EQUITY_USDT", "1000"))
RISK_PER_TRADE     = float(os.getenv("RISK_PER_TRADE", "0.005"))  # 0.5%
LEVERAGE           = int(os.getenv("LEVERAGE", "3"))
MARGIN_MODE        = os.getenv("MARGIN_MODE", "isolated")         # isolated/cross

ATR_MULT_SL        = float(os.getenv("ATR_MULT_SL", "2.0"))
TP_R_MULT          = float(os.getenv("TP_R_MULT", "2.0"))
RSI_PERIOD         = int(os.getenv("RSI_PERIOD", "14"))
EMA_FAST           = int(os.getenv("EMA_FAST", "50"))
EMA_SLOW           = int(os.getenv("EMA_SLOW", "200"))
RSI_LONG_MIN       = float(os.getenv("RSI_LONG_MIN", "52"))
RSI_SHORT_MAX      = float(os.getenv("RSI_SHORT_MAX", "48"))

POLL_SECONDS       = int(os.getenv("POLL_SECONDS", "30"))
TRADES_CSV         = os.getenv("LOG_TRADES_CSV", "trades_futures.csv")
DRY_RUN            = os.getenv("DRY_RUN", "true").lower() == "true"

# ==== Helpers ====
def log(*a):
    print(datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"), "-", *a, flush=True)

def exchange():
    klass = getattr(ccxt, EXCHANGE_ID)
    ex = klass({
        "apiKey": API_KEY,
        "secret": API_SECRET,
        "enableRateLimit": True,
        "options": {
            "defaultType": "future",
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
        if m.get("swap") and m.get("linear") and m.get("quote") == "USDT":
            symbols.append(s)
    tickers = ex.fetch_tickers(symbols)
    scored = []
    for sym, t in tickers.items():
        qv = t.get("quoteVolume", None)
        if qv is None:
            info = t.get("info", {})
            qv = float(info.get("quoteVolume", 0) or 0)
        scored.append((sym, qv))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in scored[:n]]

def fetch_ohlcv_df(ex, symbol, limit=400):
    data = ex.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=limit)
    df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df

def add_indicators(df):
    df = df.copy()
    df["ema_fast"] = EMAIndicator(df["close"], window=EMA_FAST).ema_indicator()
    df["ema_slow"] = EMAIndicator(df["close"], window=EMA_SLOW).ema_indicator()
    df["rsi"]      = RSIIndicator(df["close"], window=RSI_PERIOD).rsi()
    atr            = AverageTrueRange(df["high"], df["low"], df["close"], window=14)
    df["atr"]      = atr.average_true_range()
    return df

def trend_and_signal(df):
    if len(df) < max(EMA_SLOW, RSI_PERIOD) + 2:
        return "none", None
    last_closed = df.iloc[-2]
    
    # SAFER: handles missing or bad values
    if last_closed[["ema_fast", "ema_slow", "rsi", "atr"]].isna().any():
        return "none", None

    tr = "up" if last_closed["ema_fast"] > last_closed["ema_slow"] else "down"
    side = None
    if tr == "up" and last_closed["rsi"] >= RSI_LONG_MIN:
        side = "long"
    elif tr == "down" and last_closed["rsi"] <= RSI_SHORT_MAX:
        side = "short"
    return tr, side

def round_qty(ex, symbol, qty):
    market = ex.market(symbol)
    try:
        return float(ex.amount_to_precision(symbol, qty))
    except Exception:
        step = (market.get("limits", {}).get("amount", {}).get("min", 0)) or 0.0001
        return math.floor(qty / step) * step

def usd_position_size(entry, stop, equity_usdt, risk_fraction):
    risk_usdt = max(5.0, equity_usdt * risk_fraction)
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return 0.0
    qty = risk_usdt / stop_dist
    return qty

def set_leverage_and_margin(ex, symbol):
    try:
        if hasattr(ex, "set_leverage"):
            ex.set_leverage(LEVERAGE, symbol=symbol)
    except Exception as e:
        log("set_leverage failed", symbol, str(e))
    try:
        if hasattr(ex, "set_margin_mode"):
            ex.set_margin_mode(MARGIN_MODE, symbol=symbol)
    except Exception as e:
        log("set_margin_mode failed", symbol, str(e))

def place_bracket_orders(ex, symbol, side, qty, entry_price, sl_price, tp_price):
    opposite = "sell" if side == "buy" else "buy"

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

def write_trade(row):
    df = pd.DataFrame([row])
    header = not os.path.exists(TRADES_CSV)
    df.to_csv(TRADES_CSV, mode="a", index=False, header=header)

def get_open_positions(ex):
    try:
        poss = ex.fetch_positions()
        open_map = {}
        for p in poss:
            sym = p.get("symbol")
            amt = p.get("contracts") or p.get("positionAmt") or p.get("contractsAmount")
            sz = float(amt or 0)
            side = None
            if sz > 0: side = "long"
            if sz < 0: side = "short"
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
        orders = get_open_orders(ex, symbol)
        for o in orders:
            if o.get("reduceOnly"):
                try:
                    ex.cancel_order(o["id"], symbol)
                except Exception:
                    pass
    except Exception:
        pass

def equity_from_balance(ex):
    try:
        b = ex.fetch_balance()
        total = b.get("total", {}).get("USDT", None)
        if total is not None:
            return float(total)
    except Exception:
        pass
    return ACCOUNT_EQUITY_USDT

def main():
    ex = exchange()
    ex.load_markets()

    last_candle_time = None

    while True:
        try:
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

            universe = top_usdt_perps(ex, UNIVERSE_SIZE)
            log("Universe:", ", ".join(universe))

            open_pos = get_open_positions(ex)
            open_syms = set(open_pos.keys())
            log("Open positions:", open_pos)

            candidates = []
            for sym in universe:
                try:
                    df = fetch_ohlcv_df(ex, sym, limit=400)
                    df = add_indicators(df)
                    tr, side = trend_and_signal(df)
                    prev = df.iloc[-2]
                    if side is None:
                        continue
                    ema_gap = abs(prev["ema_fast"] - prev["ema_slow"]) / max(1e-9, abs(prev["ema_slow"]))
                    rsi_centered = 100 - abs((60 - prev["rsi"]) if side == "long" else (prev["rsi"] - 40))
                    score = float(ema_gap * 1000 + rsi_centered)
                    candidates.append((sym, side, float(prev["close"]), float(prev["atr"]), score))
                except Exception as e:
                    log("scan fail", sym, str(e))
                    continue

            candidates.sort(key=lambda x: x[4], reverse=True)
            log("Top signals:", candidates[:5])

            equity = equity_from_balance(ex)
            placed = 0
            for sym, side, entry_price, atr, _ in candidates:
                if sym in open_syms:
                    continue
                if placed >= max(0, MAX_POSITIONS - len(open_syms)):
                    break
                if atr <= 0:
                    continue

                stop = entry_price - ATR_MULT_SL * atr if side == "long" else entry_price + ATR_MULT_SL * atr
                r_per_unit = abs(entry_price - stop)
                tp = entry_price + TP_R_MULT * r_per_unit if side == "long" else entry_price - TP_R_MULT * r_per_unit

                qty = usd_position_size(entry_price, stop, equity, RISK_PER_TRADE)
                if qty <= 0:
                    continue
                qty = round_qty(ex, sym, qty)
                if qty <= 0:
                    continue

                set_leverage_and_margin(ex, sym)
                cancel_reduce_only_orders(ex, sym)

                side_ex = "buy" if side == "long" else "sell"
                place_bracket_orders(ex, sym, side_ex, qty, entry_price, stop, tp)

                write_trade({
                    "time": datetime.utcnow().isoformat(),
                    "symbol": sym,
                    "side": side,
                    "qty": qty,
                    "entry": entry_price,
                    "stop": stop,
                    "take_profit": tp,
                    "atr": atr,
                    "equity_snapshot": equity,
                    "dry_run": DRY_RUN
                })
                placed += 1

            for sym in list(open_syms):
                try:
                    df = fetch_ohlcv_df(ex, sym, limit=300)
                    df = add_indicators(df)
                    tr, side_now = trend_and_signal(df)
                    pos_side = open_pos[sym]["side"]
                    if side_now is None:
                        continue
                    flip = (pos_side == "long" and side_now == "short") or (pos_side == "short" and side_now == "long")
                    if flip:
                        log("Flip detected on", sym, "closing position…")
                        if not DRY_RUN:
                            amt = open_pos[sym]["size"]
                            try:
                                ex.create_order(sym, "market", "sell" if pos_side=="long" else "buy",
                                                amt, params={"reduceOnly": True})
                            except Exception as e:
                                log("close fail", sym, str(e))
                        else:
                            log("[DRY_RUN] close", sym, "pos_side=", pos_side)
                except Exception as e:
                    log("rotation check fail", sym, str(e))

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            log("Stopping…")
            break
        except ccxt.RateLimitExceeded as e:
            log("Rate limit; sleeping 10s")
            time.sleep(10)
        except Exception as e:
            log("Loop error:", str(e))
            traceback.print_exc()
            time.sleep(5)

if __name__ == "__main__":
    main()
