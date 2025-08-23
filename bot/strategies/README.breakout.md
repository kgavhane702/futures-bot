# Strategy: breakout

ID: `breakout`

## Summary
Breakout system that enters on clean breaks of recent swing highs/lows, with volume and momentum confirmation, and enforces RR ≥ 1:2.

## Entry
- Long: close above recent swing high (last ~50 bars), HTF trend up (EMA fast>slow), LTF momentum up (EMA fast>slow, RSI ≥ threshold), ADX ≥ MIN_ADX on HTF.
- Short: symmetric below swing low with HTF down, LTF momentum down, ADX filter.
- Volume: last closed volume > 1.5× 30-bar average.

## SL
- Just beyond the opposite structural swing (low for long, high for short) with 0.5×ATR buffer.

## Targets & management
- Targets at 1R / 2R / 3R; partial sizes from `TARGET_SPLITS` (e.g., 50/30/20).
- After T1: SL → breakeven.
- After T2: SL → T1 or trail per engine defaults.

## Risk
- Same engine risk: percent or absolute risk per trade; precision rounding and min amount respected.

## Config
- Enable via `.env`: `STRATEGIES=breakout`
- Uses global `TARGET_SPLITS`, and signal thresholds from `config.py` (EMA/RSI/ADX).

## Notes
- Strategy only computes levels; engine places orders and handles fills.
