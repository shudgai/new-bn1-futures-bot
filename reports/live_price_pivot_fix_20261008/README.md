# Live price pivot repair — 2026-10-08

Author: shudgai999 / Codex; shell and Git identity verified.
Runtime worktree: wait-paper-gate-candidate; base commit: 2684305.

## Problem and change

Ordinary Channel Swing positions waited for the right-hand pivot candle to close. The live pressure path applied only to KC_LIVE_BODY_BREAKOUT, delaying a short exit during the first strong rebound after a mature decline.

Add EXIT_LIVE_PRICE_PIVOT_REVERSAL before the closed pivot fallback for every Channel Swing entry phase. Two completed post-entry favorable bodies, directional closes and a new favorable price extreme establish the run. A current adverse body of at least half the prior completed body, reversing beyond the prior close, confirms the live price pivot. The 0.5 body ratio is an implementation default, not an ATR profit ladder. A live wick sweep is permitted when the current body independently confirms reversal. Ordinary noise, favorable continuation, pre-entry context, gaps, fallback and invalid prices cannot authorize this exit.

The rule uses available completed candles and the current quote, without reconstructing future ticks or predicting the absolute low. It does not require a MA5/KC turn, rail touch, a right-hand completed candle or a fixed ATR drawdown. Existing closed pivot and structure-break exits remain. Pending decisions use the existing position-bound policy and close retry path; audit metadata retains live pivot evidence. No hard stop was added.

## Validation and boundary

149 relevant cases passed across test_live_price_pivot_exit, test_pivot_structure_only_exits, test_live_profit_peak_reversal and test_confirmed_pivot_exit. New coverage includes both sides, CAP/dragon real-time dispatcher, all three entry phases, actual cached indicator construction, small rebounds, new extrema, invalid data, wick sweeps and failed-close retry.

Expanded legacy checks contain 17 existing failures: four hard-stop/waterfall assertions and 13 older realtime state/stop expectations. All failure names were reproduced using the original HEAD modules loaded in an independent process. They were not skipped, rewritten or reported as passing. Raw output is in new bn/reports/live_price_pivot_fix_20261008/.

Existing unrelated uncommitted entry changes were retained and excluded from this commit. No service restart or branch switch was performed. The referenced mandatory AIDAN common/Python specification files are absent; no imported standard was modified.
