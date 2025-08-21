# futures-bot-ready

Futures-only crypto bot that scans top USDT perpetuals by volume, opens long/short with ATR-based SL/TP, and rotates if signal flips.  
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
# edit .env to paste your own API_KEY and API_SECRET (TRADE+READ only, withdrawals off)

python futures_bot.py
```

## Docker
```bash
docker compose up --build
```
Provide `.env` in the project root (not committed).

## Security
- Never share keys. Revoke exposed keys immediately.
- Use IP whitelist on the exchange and disable withdrawals.
- Keep leverage low (e.g., 2–3x) and risk small (≤0.5% per trade).
