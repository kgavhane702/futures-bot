import time
from datetime import datetime, timedelta
import os, sys

# Ensure project root on sys.path for imports
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
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
        self._precision = {
            "amount": 6,
            "price": 2,
        }

    def load_markets(self):
        return {}

    def set_leverage(self, lev, symbol=None):
        self.leverage_set.append((symbol, lev))

    def set_margin_mode(self, mode, symbol=None):
        self.margin_mode_set.append((symbol, mode))

    def fetch_ohlcv(self, symbol, timeframe="1m", limit=300):
        # Create an ascending series to trigger long bias
        now = datetime.utcnow()
        data = []
        base = self.current_price
        for i in range(limit):
            ts = int((now - timedelta(minutes=(limit - i))).timestamp() * 1000)
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
        # If market entry, open a position simulation
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

    # Precision helpers used by risk.round_qty
    def amount_to_precision(self, symbol, amount):
        fmt = "{:.%df}" % self._precision["amount"]
        return fmt.format(float(amount))

    def price_to_precision(self, symbol, price):
        fmt = "{:.%df}" % self._precision["price"]
        return fmt.format(float(price))

    def market(self, symbol):
        return {
            "symbol": symbol,
            "precision": dict(self._precision),
            "limits": {
                "amount": {"min": 0.000001, "max": 1e9},
                "price": {"min": 0.01, "max": 1e9},
            },
        }


def main():
    ex = MockExchange()
    w = Scalp1mWorker(ex)

    sym = "BTC/USDT:USDT"
    print("-- placing entry --")
    w._place_entry(sym)
    print("leverage calls:", ex.leverage_set)
    print("margin mode calls:", ex.margin_mode_set)
    print("orders after entry:", [
        (o["type"], o["side"], o["params"].get("closePosition")) for o in ex.orders
    ])

    # Move price up to trigger trailing
    ex.current_price *= 1.02  # +2%
    print("-- trailing check --")
    w._trail_for_symbol(sym)
    print("orders after trail:", [
        (o["type"], o["side"], o["params"].get("closePosition"), o["params"].get("stopPrice")) for o in ex.orders
    ])


if __name__ == "__main__":
    main()


