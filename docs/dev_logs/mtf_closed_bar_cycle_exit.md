# MTF closed-bar entry and cycle-exit safeguards

## 2026-11-16

- **Scope**: MTF KC state-machine entry finality, same-minute close/re-entry protection, cycle-exit confirmation, and emergency-exit authorization.
- **Changes**: Require a directionally aligned completed 1m candle and matching live-candle direction at final MTF entry validation. Prevent same-1m-bar re-entry after a close. Persist ordinary MTF exits until a completed adverse 1m candle confirms the close; keep account-level hard stops, market circuit breakers, and verified waterfall exits available. Block unrelated mid-cycle strategy exits.
- **Bug fix**: Preserve raw candle and position timestamps in pending-exit state, then normalize them consistently when comparing. This prevents synthetic millisecond timestamps from being interpreted as seconds again and re-arming the exit instead of recognizing the confirming bar.
- **Focused verification**: `pytest -q tests/test_mtf_kc_state_machine.py tests/test_math_execution_gate.py` — 54 passed. Python compilation and `git diff --check` passed.
- **Broader verification**: An additional entry/firewall and KC breakout selection completed with 213 passed and 30 failed. Failures are in `tests/test_strict_entry_contract.py` and `tests/test_kc_bb_squeeze_breakout.py`; these assertions were not changed and are not represented as passing.
- **Runtime verification**: Restarted the user-level `binance-8006.service`. `/api/status` reports the service running in paper mode for 龙虾/USDT and 1000PEPE/USDT; the existing 龙虾 SHORT and 1000PEPE LONG paper positions remained present. No live orders were submitted.

## 2026-11-16 — Hold through MTF pullbacks

- **Change**: MTF position exits now use the last two completed 15m KC midlines to determine cycle direction. An in-progress 15m pullback or live slope classified as FLAT/DOWNTREND cannot close a LONG while the completed midline remains rising (and vice versa for SHORT). A continuing trend cancels any stale pending cycle-exit confirmation; missing completed direction fails closed and cannot confirm an old cycle exit.
- **Regression coverage**: Added LONG/SHORT tests for live pullbacks against the completed trend and pending-exit cancellation, plus invalid-HTF pending-exit retention. Initialized the post-close reversal flag for the new direction path.
- **Verification**: `pytest -q tests/test_mtf_kc_state_machine.py tests/test_math_execution_gate.py tests/test_short_exit_and_direction_valve.py` — 71 passed. Python compilation and `git diff --check` passed.

## 2026-11-16 — Confirm lower-timeframe midline reversal

- **Problem evidence**: The active 1000PEPE MTF LONG was held because the exit gate considered only the 15m midline, whose last completed values were still rising. At the same snapshot, completed 1m closes were below a falling 1m KC midline. Diagnostic `STRUCTURE_EXIT_PROBE` authorization logs were not actual close orders.
- **Change**: For MTF positions, close after two consecutive post-entry completed 1m candles both finish beyond the KC midline against the position while the 1m midline moves against it. This applies symmetrically to LONG and SHORT, rejects live/incomplete candles and malformed OHLC, and routes the close through the existing MTF exit authority. Account emergency and waterfall exits retain priority.
- **Verification**: Added evaluator and execution-wiring regressions for both directions, insufficient confirmation, pre-entry bars, and 1000PEPE LONG while 15m remains rising. `pytest -q tests/test_mtf_kc_state_machine.py tests/test_math_execution_gate.py tests/test_short_exit_and_direction_valve.py` — 78 passed; Python compilation and `git diff --check` passed.
