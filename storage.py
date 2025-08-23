import os
import pandas as pd

from config import TRADES_CSV


def write_trade(row: dict):
    df = pd.DataFrame([row])
    header = not os.path.exists(TRADES_CSV)
    df.to_csv(TRADES_CSV, mode="a", index=False, header=header)


