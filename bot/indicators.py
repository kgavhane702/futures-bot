import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, ADXIndicator
from ta.volatility import AverageTrueRange
from .config import EMA_FAST, EMA_SLOW, RSI_PERIOD, ADX_PERIOD

def fetch_ohlcv_df(ex, symbol, tf, limit=400):
    data = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    return df

def add_indicators(df):
    df = df.copy()
    df["ema_fast"] = EMAIndicator(df["close"], window=EMA_FAST).ema_indicator()
    df["ema_slow"] = EMAIndicator(df["close"], window=EMA_SLOW).ema_indicator()
    df["rsi"]      = RSIIndicator(df["close"], window=RSI_PERIOD).rsi()
    df["atr"]      = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
    df["adx"]      = ADXIndicator(df["high"], df["low"], df["close"], window=ADX_PERIOD).adx()
    return df

def valid_row(row):
    return not pd.isna(row[["ema_fast","ema_slow","rsi","atr","adx"]]).any()
