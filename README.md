# futures-bot

Futures-only crypto bot that scans top USDT perpetuals by volume, evaluates pluggable strategies, and places entries with strategy-driven SL/TP.  
**No guarantees. Futures are risky. Start with `USE_TESTNET=true` + `DRY_RUN=true`.**

## Quick Start
```bash
python -V  # >= 3.10
python -m venv .venv
# Windows:  . .venv/Scripts/Activate.ps1
# macOS/Linux:  source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
# edit .env to paste your own API_KEY and API_SECRET (TRADE+READ only)

python runner.py
```

## Docker
```bash
docker compose up --build
```
Provide `.env` in the project root (not committed).

## Architecture (high level)
- Runner: `runner.py` orchestrates the loop and UI.
- Core package: `bot/` (exchange client, market data, orders, risk, state, workers, UI).
- Strategy system: `bot/strategies/` provides a clean interface to plug in many strategies.

### Strategy framework
- Base types: `bot/strategies/base.py`
  - `Strategy.decide(symbol, data) -> Decision`
  - `Decision` fields (key ones):
    - `side` (long/short), `score`, `confidence`
    - `entry_price`, `atr`
    - `initial_stop` (SL), `targets` (up to 3), `splits` (partial sizes)
- Registry: `bot/strategies/registry.py` loads strategies based on `STRATEGIES` env.
- Example strategy: `bot/strategies/mtf_ema_rsi_adx.py` (EMA/RSI/ADX MTF with confidence). It computes:
  - Main target (TP3) from risk model, then positions T1/T2 as cumulative fractions of the distance to TP3.
  - Partial quantities from `TARGET_SPLITS`.

### Orders and multi-target exits
- Orders: `bot/orders.py`
  - Single TP: `place_bracket_orders`
  - Multi-TP: `place_multi_target_orders` (entry, initial SL, up to 3 reduce-only TPs)
  - Quantities are rounded to exchange precision; if size is too small, legs may be merged by necessity.
- Workers: `bot/workers/monitor_worker.py`
  - Monitors positions/prices, cancels orphan reduce-only orders, and adjusts SL according to stage (TP1→breakeven, TP2→TP1).

### State and UI
- State: `bot/state.py` holds in-memory snapshot for UI (prices, positions, pnl, logs, strategy meta).
- UI: `bot/ui/` (FastAPI + Jinja + vanilla JS)
  - Positions table shows `strategy`, `confidence`, and TP stage.

## Configuration (env)
Key vars (see `.env.example`):
- Exchange/API: `EXCHANGE`, `API_KEY`, `API_SECRET`, `USE_TESTNET`
- Universe/timeframes: `UNIVERSE_SIZE`, `TIMEFRAME`, `HTF_TIMEFRAME`
- Risk: `ACCOUNT_EQUITY_USDT`, `RISK_PER_TRADE`, `ABS_RISK_USDT`, `LEVERAGE`, `MARGIN_MODE`
- Strategy control:
  - `STRATEGIES=mtf_ema_rsi_adx` (comma-separated IDs)
  - `TARGET_SPLITS=0.5,0.3,0.2` (both level distribution toward TP3 and partial sizes)
- Ops: `DRY_RUN`, `POLL_SECONDS`, `MONITOR_SECONDS`, etc.

## Trades CSV schema
Written by `bot/storage.py` to `TRADES_CSV` (default `trades_futures.csv`). Columns:
```
time, symbol, side, strategy, confidence, qty, entry,
initial_stop, stop, take_profit, targets, splits, atr,
equity_snapshot, dry_run
```
Notes:
- `targets`/`splits` are JSON strings.
- Missing values are blank.

## Add a new strategy
1) Create `bot/strategies/my_strategy.py` implementing `Strategy` and returning a `Decision` with your SL/TP plan.  
2) Register it in `bot/strategies/registry.py`.  
3) Enable via `.env`: `STRATEGIES=my_strategy`.

## Security
- Never share keys. Revoke exposed keys immediately.
- Use IP whitelist on the exchange and disable withdrawals.
- Keep leverage low (e.g., 2–3x) and risk small (≤0.5% per trade).
