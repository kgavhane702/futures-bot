import os
from dotenv import load_dotenv

load_dotenv()

# === Exchange / General ===
EXCHANGE_ID = os.getenv("EXCHANGE", "binanceusdm")
API_KEY     = os.getenv("API_KEY", "")
API_SECRET  = os.getenv("API_SECRET", "")
USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"

# === Strategy Timeframes ===
TIMEFRAME     = os.getenv("TIMEFRAME", "15m")     # lower timeframe (LTF)
HTF_TIMEFRAME = os.getenv("HTF_TIMEFRAME", "1h")  # higher timeframe (HTF) for confirmation

UNIVERSE_SIZE = int(os.getenv("UNIVERSE_SIZE", "12"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "1"))

# === Risk Management ===
ACCOUNT_EQUITY_USDT = float(os.getenv("ACCOUNT_EQUITY_USDT", "100"))
RISK_PER_TRADE      = float(os.getenv("RISK_PER_TRADE", "0.01"))  # fraction of equity
ABS_RISK_USDT       = float(os.getenv("ABS_RISK_USDT", "0"))      # fixed $ risk if > 0
LEVERAGE            = int(os.getenv("LEVERAGE", "3"))
MARGIN_MODE         = os.getenv("MARGIN_MODE", "isolated")        # cross/isolated

# Notional & margin guards
MAX_NOTIONAL_FRACTION = float(os.getenv("MAX_NOTIONAL_FRACTION", "0.30"))  # cap of equity*leverage
MIN_NOTIONAL_USDT     = float(os.getenv("MIN_NOTIONAL_USDT", "10"))        # skip too-small orders
MARGIN_BUFFER_FRAC    = float(os.getenv("MARGIN_BUFFER_FRAC", "0.90"))     # 90% buffer of cap

# === Signal Settings ===
EMA_FAST     = int(os.getenv("EMA_FAST", "50"))
EMA_SLOW     = int(os.getenv("EMA_SLOW", "200"))
RSI_PERIOD   = int(os.getenv("RSI_PERIOD", "14"))
RSI_LONG_MIN = float(os.getenv("RSI_LONG_MIN", "52"))
RSI_SHORT_MAX= float(os.getenv("RSI_SHORT_MAX", "48"))
ADX_PERIOD   = int(os.getenv("ADX_PERIOD", "14"))
MIN_ADX      = float(os.getenv("MIN_ADX", "18"))

# SL/TP & trailing
ATR_MULT_SL       = float(os.getenv("ATR_MULT_SL", "2.0"))
TP_R_MULT         = float(os.getenv("TP_R_MULT", "2.0"))
BREAKEVEN_AFTER_R = float(os.getenv("BREAKEVEN_AFTER_R", "1.0"))
TRAIL_AFTER_R     = float(os.getenv("TRAIL_AFTER_R", "1.5"))
TRAIL_ATR_MULT    = float(os.getenv("TRAIL_ATR_MULT", "1.0"))
MAX_SL_PCT        = float(os.getenv("MAX_SL_PCT", "0"))  # 0 disables; e.g., 0.02 means max 2% distance
WORKING_TYPE      = os.getenv("WORKING_TYPE", "MARK_PRICE")  # MARK_PRICE or CONTRACT_PRICE
PRICE_PROTECT     = os.getenv("PRICE_PROTECT", "false").lower() == "true"
STOP_CAP_BEHAVIOR = os.getenv("STOP_CAP_BEHAVIOR", "clamp").lower()  # 'clamp' or 'skip'
PROTECTION_BUFFER_PCT = float(os.getenv("PROTECTION_BUFFER_PCT", "0.001"))  # 0.1% default
PROTECTION_COOLDOWN_SECS = int(os.getenv("PROTECTION_COOLDOWN_SECS", "10"))
PROTECTION_TOLERANCE_PCT = float(os.getenv("PROTECTION_TOLERANCE_PCT", "0.001"))
ENTRY_PROTECTION_GRACE_SECS = int(os.getenv("ENTRY_PROTECTION_GRACE_SECS", "5"))

# Ops
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
TRADES_CSV   = os.getenv("LOG_TRADES_CSV", "trades_futures.csv")
DRY_RUN      = os.getenv("DRY_RUN", "true").lower() == "true"
ALLOW_SHORTS = os.getenv("ALLOW_SHORTS", "true").lower() == "true"
HEDGE_MODE   = os.getenv("HEDGE_MODE", "false").lower() == "true"  # assume one-way unless true
ENTRY_SLIPPAGE_MAX_PCT = float(os.getenv("ENTRY_SLIPPAGE_MAX_PCT", "0.01"))  # 1% max slippage guard
ORPHAN_SWEEP_SECONDS = int(os.getenv("ORPHAN_SWEEP_SECONDS", "10"))
ORPHAN_SWEEP_GRACE_SECONDS = int(os.getenv("ORPHAN_SWEEP_GRACE_SECONDS", "60"))
PROTECTION_CHECK_SECONDS = int(os.getenv("PROTECTION_CHECK_SECONDS", "7"))
USE_FLIP_EXIT = os.getenv("USE_FLIP_EXIT", "false").lower() == "true"
FLIP_CONFIRM_BARS = int(os.getenv("FLIP_CONFIRM_BARS", "2"))
USE_TRAILING = os.getenv("USE_TRAILING", "false").lower() == "true"
TOTAL_NOTIONAL_CAP_FRACTION = float(os.getenv("TOTAL_NOTIONAL_CAP_FRACTION", "0.50"))

# Convenience accessor for external tools (e.g., backtests)
def get_config():
    return {
        "EXCHANGE_ID": EXCHANGE_ID,
        "API_KEY": API_KEY,
        "API_SECRET": API_SECRET,
        "USE_TESTNET": USE_TESTNET,
        "TIMEFRAME": TIMEFRAME,
        "HTF_TIMEFRAME": HTF_TIMEFRAME,
        "UNIVERSE_SIZE": UNIVERSE_SIZE,
        "MAX_POSITIONS": MAX_POSITIONS,
        "ACCOUNT_EQUITY_USDT": ACCOUNT_EQUITY_USDT,
        "RISK_PER_TRADE": RISK_PER_TRADE,
        "ABS_RISK_USDT": ABS_RISK_USDT,
        "LEVERAGE": LEVERAGE,
        "MARGIN_MODE": MARGIN_MODE,
        "MAX_NOTIONAL_FRACTION": MAX_NOTIONAL_FRACTION,
        "MIN_NOTIONAL_USDT": MIN_NOTIONAL_USDT,
        "MARGIN_BUFFER_FRAC": MARGIN_BUFFER_FRAC,
        "EMA_FAST": EMA_FAST,
        "EMA_SLOW": EMA_SLOW,
        "RSI_PERIOD": RSI_PERIOD,
        "RSI_LONG_MIN": RSI_LONG_MIN,
        "RSI_SHORT_MAX": RSI_SHORT_MAX,
        "ADX_PERIOD": ADX_PERIOD,
        "MIN_ADX": MIN_ADX,
        "ATR_MULT_SL": ATR_MULT_SL,
        "TP_R_MULT": TP_R_MULT,
        "BREAKEVEN_AFTER_R": BREAKEVEN_AFTER_R,
        "TRAIL_AFTER_R": TRAIL_AFTER_R,
        "TRAIL_ATR_MULT": TRAIL_ATR_MULT,
        "MAX_SL_PCT": MAX_SL_PCT,
        "STOP_CAP_BEHAVIOR": STOP_CAP_BEHAVIOR,
        "WORKING_TYPE": WORKING_TYPE,
        "PRICE_PROTECT": PRICE_PROTECT,
        "POLL_SECONDS": POLL_SECONDS,
        "TRADES_CSV": TRADES_CSV,
        "DRY_RUN": DRY_RUN,
        "ALLOW_SHORTS": ALLOW_SHORTS,
        "HEDGE_MODE": HEDGE_MODE,
        "ENTRY_SLIPPAGE_MAX_PCT": ENTRY_SLIPPAGE_MAX_PCT,
        "ORPHAN_SWEEP_SECONDS": ORPHAN_SWEEP_SECONDS,
        "ORPHAN_SWEEP_GRACE_SECONDS": ORPHAN_SWEEP_GRACE_SECONDS,
        "PROTECTION_CHECK_SECONDS": PROTECTION_CHECK_SECONDS,
        "USE_FLIP_EXIT": USE_FLIP_EXIT,
        "FLIP_CONFIRM_BARS": FLIP_CONFIRM_BARS,
        "USE_TRAILING": USE_TRAILING,
        "TOTAL_NOTIONAL_CAP_FRACTION": TOTAL_NOTIONAL_CAP_FRACTION,
    }

# Web UI
ENABLE_WEB = os.getenv("ENABLE_WEB", "false").lower() == "true"
WEB_HOST   = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT   = int(os.getenv("WEB_PORT", "8080"))
