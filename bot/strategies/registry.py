from typing import List, Dict, Any
import json
from pathlib import Path
import os
from ..config import ENABLED_STRATEGIES
from .base import Strategy
from .mtf_ema_rsi_adx import MtfEmaRsiAdxStrategy
from .breakout import BreakoutStrategy
from .scalping import Scalping5mStrategy
from .mtf_5m_high_conf import Mtf5mHighConfStrategy
from .scalp_1m_trail.strategy import Scalp1mTrailStrategy


def _env_for_strategy(prefix: str) -> Dict[str, Any]:
    # Read env vars with STRAT_<ID>_ prefix into a dict without the prefix
    cfg: Dict[str, Any] = {}
    plen = len(prefix)
    for k, v in os.environ.items():
        if k.startswith(prefix):
            key = k[plen:]
            # Try to cast numbers/bools when possible
            lv = v.strip()
            if lv.lower() in ("true", "false"):
                cfg[key] = (lv.lower() == "true")
            else:
                try:
                    if "." in lv:
                        cfg[key] = float(lv)
                    else:
                        cfg[key] = int(lv)
                except Exception:
                    cfg[key] = lv
    return cfg


def _file_cfg(strategy_id: str) -> Dict[str, Any]:
    """Load default config from strategies/config/<id>.json, if present."""
    try:
        p = Path(__file__).parent / "config" / f"{strategy_id}.json"
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return {}


def available_strategy_ids() -> List[str]:
    return sorted(["mtf_ema_rsi_adx", "breakout", "scalping", "mtf_5m_high_conf", "scalp_1m_trail"])


def load_strategies() -> List[Strategy]:
    registry = {
        "mtf_ema_rsi_adx": MtfEmaRsiAdxStrategy,
        "breakout": BreakoutStrategy,
        "scalping": Scalping5mStrategy,
        "mtf_5m_high_conf": Mtf5mHighConfStrategy,
        "scalp_1m_trail": Scalp1mTrailStrategy,
    }
    enabled = [s.lower() for s in (ENABLED_STRATEGIES or [])]
    if not enabled or any(s in ("auto", "all", "*") for s in enabled):
        # Load all, each with its own env-config
        out: List[Strategy] = []
        for sid, cls in registry.items():
            prefix = f"STRAT_{sid.upper()}_"
            cfg = {**_file_cfg(sid), **_env_for_strategy(prefix)}
            out.append(cls(cfg))
        return out

    out: List[Strategy] = []
    for sid in enabled:
        cls = registry.get(sid)
        if cls:
            prefix = f"STRAT_{sid.upper()}_"
            cfg = {**_file_cfg(sid), **_env_for_strategy(prefix)}
            out.append(cls(cfg))
    return out or [cls({**_file_cfg(sid), **_env_for_strategy(f"STRAT_{sid.upper()}_")}) for sid, cls in registry.items()]


