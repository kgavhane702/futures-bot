import threading
import time
from datetime import datetime
from typing import Optional

from ..config import (
    SCALP1M_ENABLED,
    SCALP1M_UNIVERSE_SIZE,
    SCALP1M_REFRESH_SECONDS,
    SCALP1M_MAX_POSITIONS,
    SCALP1M_BLACKLIST_HOURS,
)
from ..utils import log as base_log
from ..state import STATE
from ..market_data import top_usdt_perps, fetch_ohlcv_df
from ..strategies.scalp_1m_trail.strategy import Scalp1mTrailStrategy
from ..strategies.registry import _file_cfg
from ..risk import equity_from_balance, size_position, round_qty
from ..orders import get_open_orders


def slog(*a):
    base_log("scalp_1m_trail ->", *a)


class Scalp1mWorker:
    def __init__(self, ex):
        self.ex = ex
        self.strategy = Scalp1mTrailStrategy(_file_cfg("scalp_1m_trail"))
        self.blacklist_until = {}  # symbol -> epoch seconds
        self.entries = {}  # symbol -> {time, entry}
        self._placing = False  # guard against concurrent placements per loop

    def _is_enabled(self) -> bool:
        return SCALP1M_ENABLED

    def _universe(self):
        try:
            return top_usdt_perps(self.ex, SCALP1M_UNIVERSE_SIZE)
        except Exception:
            return []

    def _active_scalp_count(self) -> int:
        try:
            poss = self.ex.fetch_positions()
        except Exception:
            poss = []
        n = 0
        for p in poss:
            try:
                amt = p.get("contracts") or p.get("positionAmt") or p.get("contractsAmount")
                sz = float(amt or 0)
                if abs(sz) > 0 and p.get("symbol") in self.entries:
                    n += 1
            except Exception:
                pass
        return n

    def _symbol_has_any_position(self, sym: str) -> bool:
        try:
            poss = self.ex.fetch_positions()
        except Exception:
            poss = []
        for p in poss:
            try:
                if p.get("symbol") == sym:
                    amt = p.get("contracts") or p.get("positionAmt") or p.get("contractsAmount")
                    if abs(float(amt or 0)) > 0:
                        return True
            except Exception:
                continue
        return False

    def _place_entry(self, sym: str):
        # Fetch 1m data
        try:
            d = {"1m": fetch_ohlcv_df(self.ex, sym, "1m", limit= max(300, int(self.strategy.cfg.get("LOOKBACK", 300))))}
        except Exception:
            return
        dec = self.strategy.decide(sym, d)
        if not dec or dec.side not in ("long", "short") or dec.initial_stop is None or dec.entry_price is None:
            return
        # Risk sizing with available margin cap
        equity = equity_from_balance(self.ex)
        stop = float(dec.initial_stop)
        entry = float(dec.entry_price)
        qty = size_position(entry, stop, equity)
        # Cap by available margin * LEVERAGE * SCALP1M_MARGIN_FRACTION
        try:
            from ..config import LEVERAGE, SCALP1M_MARGIN_FRACTION
        except Exception:
            LEVERAGE, SCALP1M_MARGIN_FRACTION = 5, 0.05
        try:
            # Attempt to read available balance (free collateral)
            bal = self.ex.fetch_balance()
            avail = float((bal.get("USDT") or {}).get("free") or bal.get("free", 0) or 0)
        except Exception:
            avail = equity
        raw_cap = max(0.0, avail) * float(LEVERAGE) * float(SCALP1M_MARGIN_FRACTION)
        max_by_avail = max(0.0, avail) * float(LEVERAGE)
        # Use at least $10 notional if 5% is below $10, but never exceed available*leverage
        max_notional = min(max_by_avail, max(10.0, raw_cap))
        notional = qty * entry
        if notional > max_notional and entry > 0:
            qty = round_qty(self.ex, sym, max(0.0, max_notional / entry))
        elif notional < 10.0 and entry > 0 and max_by_avail >= 10.0:
            # Bump up to minimum $10 notional if capacity allows
            qty = round_qty(self.ex, sym, 10.0 / entry)
        qty = round_qty(self.ex, sym, qty)
        if qty <= 0:
            return
        # Notional guard: reuse exchange precision to estimate
        notional = qty * entry
        from ..config import MIN_NOTIONAL_USDT
        if notional < MIN_NOTIONAL_USDT:
            return
        # Set leverage 5x and isolated margin for this symbol
        try:
            if hasattr(self.ex, "set_leverage"):
                self.ex.set_leverage(5, symbol=sym)
        except Exception:
            pass
        try:
            if hasattr(self.ex, "set_margin_mode"):
                self.ex.set_margin_mode("isolated", symbol=sym)
        except Exception:
            pass
        # Entry
        side = "buy" if dec.side == "long" else "sell"
        try:
            entry_order = self.ex.create_order(sym, type="market", side=side, amount=qty)
            slog("ENTRY", sym, dec.side, qty, entry)
        except Exception as e:
            slog("entry fail", sym, str(e))
            return
        # Place initial closePosition SL
        try:
            sl_side = "sell" if dec.side == "long" else "buy"
            cid = f"scalp1m-sl-{int(time.time()*1000)}"
            self.ex.create_order(sym, "STOP_MARKET", sl_side, None, params={
                # reduceOnly is redundant with closePosition on Binance and causes -1106
                "closePosition": True,
                "stopPrice": float(stop),
                "workingType": "MARK_PRICE",
                "timeInForce": "GTE_GTC",
                "newClientOrderId": cid,
            })
            slog("SL placed", sym, float(stop))
            self.entries[sym] = {"time": time.time(), "entry": float(entry)}
            try:
                STATE.set_strategy_meta(sym, {
                    "strategy": "scalp_1m_trail",
                    "entry": float(entry),
                    "qty": float(qty),
                    "initial_stop": float(stop),
                    "client_tag": cid,
                })
                STATE.mark_entry(sym)
            except Exception:
                pass
        except Exception as e:
            slog("sl place fail", sym, str(e))

    def _unrealized_pnl_pct(self, sym: str, entry: float) -> Optional[float]:
        try:
            t = self.ex.fetch_ticker(sym)
            last = t.get("last") or t.get("close") or (t.get("info", {}) or {}).get("lastPrice")
            last = float(last) if last is not None else None
            if last is None or entry <= 0:
                return None
            return (last - entry) / entry * 100.0
        except Exception:
            return None

    def _trail_for_symbol(self, sym: str):
        meta = self.entries.get(sym)
        if not meta:
            return
        entry = float(meta.get("entry", 0.0) or 0.0)
        if entry <= 0:
            return
        pnl = self._unrealized_pnl_pct(sym, entry)
        now = time.time()
        # Use the strategy's cfg (loaded from file + env overrides)
        ttl = int(self.strategy.cfg.get("TTL_SECONDS", 600))
        if pnl is None:
            return
        # Time stop if no profit within TTL
        min_pct = float(self.strategy.cfg.get("TTL_MIN_PROFIT_PCT", 1.0))
        if (now - float(meta.get("time", now))) >= ttl and pnl < min_pct:
            try:
                # Market close reduceOnly
                poss = self.ex.fetch_positions()
                for p in poss:
                    if p.get("symbol") == sym:
                        amt = p.get("contracts") or p.get("positionAmt") or p.get("contractsAmount")
                        sz = abs(float(amt or 0))
                        if sz > 0:
                            side = p.get("side")
                            opp = "sell" if side == "long" else "buy"
                            self.ex.create_order(sym, "market", opp, sz, params={"reduceOnly": True})
                            slog("TTL close", sym)
                            self.blacklist_until[sym] = now + SCALP1M_BLACKLIST_HOURS * 3600.0
                            self.entries.pop(sym, None)
                            return
            except Exception:
                pass
        # Laddered SL trailing
        levels = self.strategy.cfg.get("TRAIL_LEVELS", [])
        # Find highest level crossed
        target_sl_pct = None
        for lvl in levels:
            try:
                if pnl >= float(lvl.get("pnl_pct", 0.0)):
                    target_sl_pct = float(lvl.get("sl_pct", 0.0))
            except Exception:
                continue
        if target_sl_pct is None:
            return
        # Compute target SL price from entry
        try:
            poss = self.ex.fetch_positions()
        except Exception:
            poss = []
        side = None
        sz = 0.0
        for p in poss:
            if p.get("symbol") == sym:
                amt = p.get("contracts") or p.get("positionAmt") or p.get("contractsAmount")
                sz = abs(float(amt or 0))
                side = p.get("side")
                break
        if sz <= 0 or side not in ("long", "short"):
            # Position closed externally -> blacklist and cleanup
            self.blacklist_until[sym] = time.time() + SCALP1M_BLACKLIST_HOURS * 3600.0
            self.entries.pop(sym, None)
            return
        if side == "long":
            new_sl = entry * (1.0 + target_sl_pct / 100.0)
        else:
            new_sl = entry * (1.0 - target_sl_pct / 100.0)
        # Cancel prior SLs, then place new closePosition SL
        try:
            orders = self.ex.fetch_open_orders(sym)
        except Exception:
            orders = []
        # Cancel only our own prior SLs: match clientOrderId tag when present
        our_tag_prefix = "scalp1m-sl-"
        for o in orders:
            try:
                if not (o.get("reduceOnly") and "STOP" in (o.get("type", "").upper())):
                    continue
                # try to read client id from standardized field or raw info
                coid = o.get("clientOrderId") or (o.get("info", {}) or {}).get("clientOrderId") or ""
                if str(coid).startswith(our_tag_prefix):
                    self.ex.cancel_order(o.get("id"), sym)
            except Exception:
                pass
        try:
            opp = "sell" if side == "long" else "buy"
            cid2 = f"scalp1m-sl-{int(time.time()*1000)}"
            self.ex.create_order(sym, "STOP_MARKET", opp, None, params={
                "closePosition": True,
                "stopPrice": float(new_sl),
                "workingType": "MARK_PRICE",
                "timeInForce": "GTE_GTC",
                "newClientOrderId": cid2,
            })
            slog("trail SL", sym, round(new_sl, 8), f"pnl={round(pnl,3)}%")
        except Exception as e:
            slog("trail SL fail", sym, str(e))

    def loop(self):
        while True:
            try:
                if not self._is_enabled():
                    time.sleep(2)
                    continue
                # Universe
                universe = self._universe()
                # Capacity: at most 1 scalp position
                from ..config import SCALP1M_MAX_POSITIONS
                if (self._active_scalp_count() < SCALP1M_MAX_POSITIONS) and not self._placing:
                    # Try to find an entry over the universe (first hit wins)
                    for sym in universe:
                        # Skip blacklisted
                        until = float(self.blacklist_until.get(sym, 0.0) or 0.0)
                        if until and time.time() < until:
                            continue
                        # Skip if any position already exists on this symbol (do not interfere)
                        if self._symbol_has_any_position(sym):
                            continue
                        # place
                        self._placing = True
                        self._place_entry(sym)
                        self._placing = False
                        if self._active_scalp_count() >= SCALP1M_MAX_POSITIONS:
                            break
                else:
                    # Ensure flag resets if capacity is filled
                    self._placing = False
                # Trail SL for active scalp entries
                for sym in list(self.entries.keys()):
                    self._trail_for_symbol(sym)
                time.sleep(max(1, int(SCALP1M_REFRESH_SECONDS)))
            except Exception as e:
                slog("worker error", str(e))
                time.sleep(max(1, int(SCALP1M_REFRESH_SECONDS)))


def start(ex) -> threading.Thread:
    w = Scalp1mWorker(ex)
    t = threading.Thread(target=w.loop, daemon=True)
    t.start()
    return t


