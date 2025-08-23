import os
from datetime import UTC
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


# Ensure environment variables are loaded before any module imports this config
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
LEVERAGE           = int(os.getenv("LEVERAGE", "5"))
MARGIN_MODE        = os.getenv("MARGIN_MODE", "isolated")           # cross/isolated

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
ATR_MULT_SL        = float(os.getenv("ATR_MULT_SL", "2.5"))
TP_R_MULT          = float(os.getenv("TP_R_MULT", "2.0"))
BREAKEVEN_AFTER_R  = float(os.getenv("BREAKEVEN_AFTER_R", "1.0"))   # move SL to BE after +1R
TRAIL_AFTER_R      = float(os.getenv("TRAIL_AFTER_R", "1.5"))       # start trailing after +1.5R
TRAIL_ATR_MULT     = float(os.getenv("TRAIL_ATR_MULT", "1.0"))      # trailing stop distance = 1.0 * ATR

# Ops
POLL_SECONDS       = int(os.getenv("POLL_SECONDS", "30"))
TRADES_CSV         = os.getenv("LOG_TRADES_CSV", "trades_futures.csv")
DRY_RUN            = os.getenv("DRY_RUN", "true").lower() == "true"
ALLOW_SHORTS       = os.getenv("ALLOW_SHORTS", "true").lower() == "true"  # selling allowed (futures)
MONITOR_SECONDS    = int(os.getenv("MONITOR_SECONDS", "10"))
PNL_MONITOR_SECONDS= int(os.getenv("PNL_MONITOR_SECONDS", "2"))
ORPHAN_MONITOR_SECONDS = int(os.getenv("ORPHAN_MONITOR_SECONDS", "2"))
ORPHAN_PROTECT_SECONDS = int(os.getenv("ORPHAN_PROTECT_SECONDS", "45"))
ORPHAN_MIN_AGE_SECONDS = int(os.getenv("ORPHAN_MIN_AGE_SECONDS", "60"))
UNIVERSE_MONITOR_SECONDS = int(os.getenv("UNIVERSE_MONITOR_SECONDS", "2"))
SCAN_WHEN_FLAT_SECONDS = int(os.getenv("SCAN_WHEN_FLAT_SECONDS", "10"))

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


