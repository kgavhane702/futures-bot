# Strategy: <strategy_id>

ID: `<strategy_id>`

## Summary
Short description of the edge and when it should trade.

## Inputs & Indicators
- Timeframes: `TIMEFRAME`, `HTF_TIMEFRAME` (or list your own)
- Indicators/Features: list the signals you compute
- Thresholds: list configurable thresholds

## Decision Logic (high level)
1) Outline the gating rules (trend/momentum/filters)
2) How you compute score and confidence
3) What you return as `Decision` (see below)

## Decision outputs
Strategy returns a `Decision`:
- `side`: "long" | "short" | None
- `score`, `confidence` (0..1)
- `entry_price`, `atr`
- `initial_stop`: float (SL)
- `targets`: up to 3 levels (T1/T2/T3)
- `splits`: partial sizes for T1/T2/T3 (e.g., `[0.5,0.3,0.2]`)

## Exits
- How SL is computed (e.g., ATR multiples)
- How T1/T2/T3 levels are derived (e.g., cumulative fractions toward TP3)
- Any special rules per strategy (optional)

## Configuration knobs
- Env vars affecting this strategy (thresholds, timeframes, toggles)
- Global: `TARGET_SPLITS` for both target level distribution and partial sizes

## Enable
- Add to `.env`: `STRATEGIES=<strategy_id>`
- Register in `bot/strategies/registry.py`

## Example (conceptual)
Provide a simple numeric example for entry/SL/targets and partial sizes.

## Notes
- Strategy produces calculations only; engine places orders, manages fills, SL moves, UI/state.
