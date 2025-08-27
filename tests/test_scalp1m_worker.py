import os
import sys
import time
import math

_root = os.path.dirname(os.path.dirname(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

from bot.workers.scalp1m_worker import Scalp1mWorker


class MockExchange:
    def __init__(self):
        self.leverage_set = []
        self.margin_mode_set = []
        self.orders = []
        self._positions = []
        self.current_price = 100.0
        self._precision = {"amount": 6, "price": 2}

    def load_markets(self):
        self.markets = {"BTC/USDT:USDT": {"swap": True, "linear": True, "quote": "USDT"}}
        return {}

    def set_leverage(self, lev, symbol=None):
        self.leverage_set.append((symbol, lev))

    def set_margin_mode(self, mode, symbol=None):
        self.margin_mode_set.append((symbol, mode))

    def fetch_ohlcv(self, symbol, timeframe="1m", limit=300):
        # simple increasing series
        now_ms = int(time.time() * 1000)
        data = []
        base = self.current_price
        for i in range(limit):
            ts = now_ms - (limit - i) * 60_000
            o = base * (1 + (i - limit) * 0.0002)
            c = o * 1.0005
            h = max(o, c) * 1.0005
            l = min(o, c) * 0.9995
            v = 10 + i * 0.1
            data.append([ts, float(o), float(h), float(l), float(c), float(v)])
        return data

    def fetch_positions(self):
        return self._positions

    def fetch_open_orders(self, symbol):
        return [o for o in self.orders if o.get("symbol") == symbol and not o.get("closed")]

    def create_order(self, symbol, type="market", side="buy", amount=None, price=None, params=None):
        params = params or {}
        order = {
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": float(amount or 0),
            "price": float(price or 0) if price else None,
            "params": dict(params),
            "id": f"mock-{len(self.orders)+1}",
        }
        self.orders.append(order)
        if type == "market" and not params.get("reduceOnly"):
            side_dir = 1 if side == "buy" else -1
            self._positions = [{
                "symbol": symbol,
                "contracts": (amount or 0) * side_dir,
                "side": "long" if side == "buy" else "short",
            }]
        return order

    def cancel_order(self, id, symbol):
        for o in self.orders:
            if o.get("id") == id and o.get("symbol") == symbol:
                o["closed"] = True
                return True
        return False

    def fetch_ticker(self, symbol):
        return {"last": float(self.current_price), "symbol": symbol}

    def amount_to_precision(self, symbol, amount):
        return f"{float(amount):.{self._precision['amount']}f}"

    def price_to_precision(self, symbol, price):
        return f"{float(price):.{self._precision['price']}f}"

    def market(self, symbol):
        return {
            "symbol": symbol,
            "precision": dict(self._precision),
            "limits": {
                "amount": {"min": 0.000001, "max": 1e9},
                "price": {"min": 0.01, "max": 1e9},
            },
        }


def test_entry_sets_leverage_and_initial_sl():
    ex = MockExchange()
    ex.load_markets()
    w = Scalp1mWorker(ex)
    sym = "BTC/USDT:USDT"
    w._place_entry(sym)

    # leverage 5x isolated applied
    assert (sym, 5) in ex.leverage_set
    assert (sym, "isolated") in ex.margin_mode_set

    # orders: market entry and a closePosition SL
    assert any(o["type"] == "market" and not o["params"].get("reduceOnly") for o in ex.orders)
    assert any(
        o["type"] == "STOP_MARKET" and o["params"].get("closePosition") is True for o in ex.orders
    )


def test_trailing_replaces_sl_when_profit_grows():
    ex = MockExchange()
    ex.load_markets()
    w = Scalp1mWorker(ex)
    sym = "BTC/USDT:USDT"
    w._place_entry(sym)
    # increase price to trigger trailing
    ex.current_price *= 1.02
    w._trail_for_symbol(sym)

    # There should be at least two STOP_MARKET closePosition orders: initial and trailed
    stops = [o for o in ex.orders if o["type"] == "STOP_MARKET" and o["params"].get("closePosition")]
    assert len(stops) >= 2
    # last stopPrice should be higher than the first for long
    assert float(stops[-1]["params"]["stopPrice"]) > float(stops[0]["params"]["stopPrice"])


def test_ttl_close_blacklists_on_no_profit():
    ex = MockExchange()
    ex.load_markets()
    w = Scalp1mWorker(ex)
    sym = "BTC/USDT:USDT"
    w._place_entry(sym)
    # simulate time passing and no profit
    # decrease price slightly to ensure pnl <= 0
    ex.current_price *= 0.99
    # shorten TTL via cfg: monkey-patch strategy cfg for quick test
    w.strategy.cfg["TTL_SECONDS"] = 0
    w._trail_for_symbol(sym)
    # position should be closed and blacklisted
    assert sym in w.blacklist_until
    # entries should be cleaned up
    assert sym not in w.entries


