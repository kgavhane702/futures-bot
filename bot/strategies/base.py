from dataclasses import dataclass
from typing import Optional, Dict, Any
import pandas as pd


@dataclass
class Decision:
    symbol: str
    strategy_id: str
    side: Optional[str]   # "long" | "short" | None
    score: float
    confidence: float
    entry_price: Optional[float]
    atr: Optional[float]
    stop: Optional[float]
    take_profit: Optional[float]
    meta: Dict[str, Any] = None
    # Multi-target support
    initial_stop: Optional[float] = None
    targets: Optional[list] = None  # up to 3 target prices
    splits: Optional[list] = None   # e.g., [0.5, 0.25, 0.25]


class Strategy:
    id: str = "base"

    def required_timeframes(self) -> Dict[str, int]:
        """Return { timeframe: lookback_bars } to fetch for this strategy."""
        return {}

    def prepare(self, data: Dict[str, Dict[str, pd.DataFrame]]):
        """Optional hook: called once per tick with all pre-fetched data by symbol and timeframe."""
        return

    def decide(self, symbol: str, data: Dict[str, pd.DataFrame]) -> Decision:
        """Return a Decision for the given symbol from prepared data."""
        return Decision(symbol=symbol, strategy_id=self.id, side=None, score=0.0, confidence=0.0,
                        entry_price=None, atr=None, stop=None, take_profit=None, meta={})


