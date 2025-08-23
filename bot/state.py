import threading
import time
from typing import Dict, Any, List


class BotState:
    def __init__(self):
        self._lock = threading.Lock()
        self._prices: Dict[str, float] = {}
        self._positions: Dict[str, Dict[str, Any]] = {}
        self._pnl: Dict[str, float] = {}
        self._total_pnl: float = 0.0
        self._logs: List[str] = []
        self._last_exits_ts: Dict[str, float] = {}
        self._last_entry_ts: Dict[str, float] = {}
        self._universe: List[str] = []
        self._threads: Dict[str, Dict[str, Any]] = {}
        # Strategy/exit stage tracking per symbol
        self._exit_stage: Dict[str, int] = {}  # 0 none, 1 after TP1, 2 after TP2, 3 closed
        self._strategy_meta: Dict[str, Dict[str, Any]] = {}

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "prices": dict(self._prices),
                "positions": dict(self._positions),
                "pnl": dict(self._pnl),
                "total_pnl": float(self._total_pnl),
                "logs": list(self._logs[-500:]),
                "last_exits_ts": dict(self._last_exits_ts),
                "last_entry_ts": dict(self._last_entry_ts),
                "universe": list(self._universe),
                "threads": {k: dict(v) for k, v in self._threads.items()},
                "exit_stage": dict(self._exit_stage),
                "strategy_meta": {k: dict(v) for k, v in self._strategy_meta.items()},
            }

    def set_price(self, symbol: str, price: float):
        with self._lock:
            self._prices[symbol] = price

    def set_positions(self, positions: Dict[str, Dict[str, Any]]):
        with self._lock:
            self._positions = dict(positions)

    def set_pnl(self, per_symbol: Dict[str, float]):
        with self._lock:
            self._pnl = dict(per_symbol)
            self._total_pnl = sum(per_symbol.values()) if per_symbol else 0.0

    def append_log(self, line: str):
        with self._lock:
            self._logs.append(line)

    def mark_exits_placed(self, symbol: str):
        with self._lock:
            self._last_exits_ts[symbol] = time.time()
            self._exit_stage.setdefault(symbol, 0)

    def mark_entry(self, symbol: str):
        with self._lock:
            self._last_entry_ts[symbol] = time.time()
            self._exit_stage[symbol] = 0

    def set_exit_stage(self, symbol: str, stage: int):
        with self._lock:
            self._exit_stage[symbol] = max(0, min(3, int(stage)))

    def get_exit_stage(self, symbol: str) -> int:
        with self._lock:
            return int(self._exit_stage.get(symbol, 0))

    def is_exits_protected(self, symbol: str, protect_seconds: int) -> bool:
        with self._lock:
            ts = self._last_exits_ts.get(symbol)
            if ts is None:
                return False
            return (time.time() - ts) <= protect_seconds

    def set_universe(self, universe):
        with self._lock:
            self._universe = list(universe)

    def set_thread_status(self, name: str, info: Dict[str, Any]):
        with self._lock:
            info_copy = dict(info)
            info_copy["ts"] = time.time()
            self._threads[name] = info_copy

    def set_strategy_meta(self, symbol: str, meta: Dict[str, Any]):
        with self._lock:
            self._strategy_meta[symbol] = dict(meta)

    def get_strategy_meta(self, symbol: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._strategy_meta.get(symbol, {}))


STATE = BotState()


