from typing import List
from ..config import ENABLED_STRATEGIES
from .base import Strategy
from .mtf_ema_rsi_adx import MtfEmaRsiAdxStrategy


def load_strategies() -> List[Strategy]:
    registry = {
        "mtf_ema_rsi_adx": MtfEmaRsiAdxStrategy,
    }
    out: List[Strategy] = []
    for sid in ENABLED_STRATEGIES:
        cls = registry.get(sid)
        if cls:
            out.append(cls())
    return out or [MtfEmaRsiAdxStrategy()]


