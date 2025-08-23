from typing import List
from ..config import ENABLED_STRATEGIES
from .base import Strategy
from .mtf_ema_rsi_adx import MtfEmaRsiAdxStrategy
from .breakout import BreakoutStrategy


def load_strategies() -> List[Strategy]:
    registry = {
        "mtf_ema_rsi_adx": MtfEmaRsiAdxStrategy,
        "breakout": BreakoutStrategy,
    }
    # If STRATEGIES is empty or set to auto/all/*, load all registered strategies
    enabled = [s.lower() for s in (ENABLED_STRATEGIES or [])]
    if not enabled or any(s in ("auto", "all", "*") for s in enabled):
        return [cls() for cls in registry.values()]

    out: List[Strategy] = []
    for sid in enabled:
        cls = registry.get(sid)
        if cls:
            out.append(cls())
    # Fallback: if none matched, load all
    return out or [cls() for cls in registry.values()]


