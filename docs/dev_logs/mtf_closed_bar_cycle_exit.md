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

## 2026-11-16 — Require macro and midline reversal; allow immediate reverse entry

- **Change**: Removed the 1m-only MTF exit path. An ordinary MTF exit now requires both the live 15m macro state and the last completed 15m KC midline direction to reverse against the position; neutral, flat, mismatched, or invalid direction holds the position and cancels stale pending exit confirmation. Account emergencies and verified waterfalls remain immediate.
- **Reverse entry**: After a confirmed cycle-reversal close succeeds, evaluate the opposite-side MTF entry in the same pipeline pass without a post-close cooldown. A persisted, one-shot ticket binds the opposite side, reversal reason, actual close timestamp, and current 1m bar. The entry firewall, final entry contract, position-flat check, market validation, and account/risk gates remain mandatory; failed persistence or stale/mismatched tickets fail closed.
- **Regression coverage**: Added LONG/SHORT direction-mismatch holds and an immediate same-bar reverse-entry execution test. Removed obsolete 1m-only reversal tests.

## 2026-11-17 — Block MTF entries in consolidation

- **Change**: MTF entries now run the existing closed-candle consolidation blacklist in strict mode before authorization. It blocks range/chop conditions such as flat KC midlines, repeated MA/KC crossings, low-trend Donchian ranges, and invalid or insufficient history. This applies to normal and immediate reverse entries.
- **Cooldown**: The blacklist is called with `cooldown_bars=0`; same-bar reverse eligibility remains governed by the persisted close ticket and all other entry/risk gates.
- **Regression coverage**: Added an MTF entry test asserting that the consolidation veto blocks a short candidate without introducing a cooldown.

## 2026-11-17 — Allow colorless immediate MTF reversals

- **Change**: A validated same-bar reversal from SHORT to LONG or LONG to SHORT no longer requires the previous completed 1m candle or current live candle to match the entry direction. The rule is limited to the persisted MTF reversal ticket; ordinary entries keep their candle-color requirements.
- **Safety**: HTF/LTF direction, MA/KC alignment, strict consolidation filtering, quote validity, account checks, and final same-bar ticket validation remain required. Stale deferred candidates are not reused for this reversal.
- **Regression coverage**: Added colorless evaluator and final-gate checks for both reversal directions, plus entry-method flag validation.

## 2026-11-17 — Add 15m midline-hold MTF entries

- **Trigger**: Reuse the existing 15m MTF slope classification. A directional candidate is created when two consecutive closed 1m closes and the latest quote are strictly above the current 15m KC middle for LONG, or strictly below for SHORT. This continuation signal does not require either candle to match the entry color.
- **Preserved gates**: Existing MTF MA/KC alignment and consolidation filters remain. Final revalidation applies the Macro Candle Structure veto, including the 0.35/0.65 KC-position limits, 1.2 ATR MA15-bias cap, and wick-exhaustion checks.
- **Regression coverage**: Added mirrored LONG/SHORT tests for the two-close condition, strict midline boundaries, colorless qualification, and macro-gate vetoes.
