# Strategy: mtf_ema_rsi_adx

ID: `mtf_ema_rsi_adx`

## Summary
Multi-timeframe confluence using EMA trend, RSI momentum, and ADX strength:
- Long: LTF and HTF EMA-fast > EMA-slow; LTF RSI ≥ threshold; ADX ≥ threshold on both
- Short: symmetric conditions; shorts can be disabled via `ALLOW_SHORTS=false`

Returns a `Decision` with side, score, confidence, entry, ATR, initial SL, and up to 3 TP targets.

## Inputs & Indicators
- Timeframes: `TIMEFRAME` (LTF), `HTF_TIMEFRAME` (HTF)
- Indicators: EMA(EMA_FAST/EMA_SLOW), RSI(RSI_PERIOD), ADX(ADX_PERIOD), ATR(14)
- Thresholds: `RSI_LONG_MIN`, `RSI_SHORT_MAX`, `MIN_ADX`

## Decision Logic (high level)
1) Compute indicators on last closed candle (index -2) for LTF and HTF
2) Gate by EMA/RSI/ADX rules; if no signal → return `side=None`
3) Score: weighted EMA gap + ADX excess + RSI distance from 50 (higher is better)
4) Confidence (0..1): normalized blend of EMA gap, ADX excess, and RSI distance
5) Entry: last closed LTF close; ATR from LTF

## Exits
- Initial SL: from risk `protective_prices()` (ATR-based R multiple)
- Main target TP3: from risk `protective_prices()`
- T1/T2 levels: cumulative fractions of distance to TP3 using `TARGET_SPLITS`
  - Example `TARGET_SPLITS=0.5,0.3,0.2` → cumulative = 0.5, 0.8, 1.0 → T1/T2/T3
- Partial sizes: same `TARGET_SPLITS` [p1,p2,p3]; engine rounds each part to exchange precision

Engine behavior (generic, not strategy-specific):
- Places ENTRY (market), initial SL (STOP_MARKET reduce-only), and up to 3 TP legs (TAKE_PROFIT_MARKET reduce-only)
- If size too small for all legs (exchange min amount), legs may be merged; final TP is preserved
- SL moves on fills: after TP1 → breakeven; after TP2 → to TP1

## Configuration knobs
- Env vars (see `.env.example`):
  - Timeframes/Universe: `TIMEFRAME`, `HTF_TIMEFRAME`, `UNIVERSE_SIZE`
  - Risk: `LEVERAGE`, `ACCOUNT_EQUITY_USDT`, `RISK_PER_TRADE`, `ABS_RISK_USDT`, `MARGIN_MODE`
  - Signal thresholds: `EMA_FAST`, `EMA_SLOW`, `RSI_PERIOD`, `RSI_LONG_MIN`, `RSI_SHORT_MAX`, `ADX_PERIOD`, `MIN_ADX`
  - Targets/partials: `TARGET_SPLITS` (e.g., `0.5,0.3,0.2`)
  - Shorts toggle: `ALLOW_SHORTS`

## Enable
- Add to `.env`: `STRATEGIES=mtf_ema_rsi_adx`

## Example (conceptual)
- Entry 100, SL 95 → `protective_prices` gives TP3 110 (2R)
- `TARGET_SPLITS=0.5,0.3,0.2` → cumulative 0.5, 0.8, 1.0
  - T1 = 105, T2 = 108, T3 = 110
  - Partials: 50%, 30%, 20% (rounded to exchange step)

## Notes
- Strategy computes levels and splits; engine handles placement, rounding, fills, SL moves, and UI/state.
- If size is too small for 3 legs, increase risk/leverage or adjust `TARGET_SPLITS`.
