# Entry contract repair — 2026-10-01

The active symbol runner uses StrictStateMachineStrategy, emitting LONG/SHORT_BREAKOUT_FIRST and LONG/SHORT_CONTINUATION. Execution and account validation accepted only SECOND_BAR_OUTSIDE codes. Earlier diagnosis naming CLOSED_TREND_BREAKOUT was incomplete; the active runner was verified before implementation.

Added a shared entry contract used by the idle scanner, engine snapshot, final submission validation and account firewall. Strict signals are recalculated against fresh prices and matched by code, side and confirmation candle. Existing V2 callers retain their evaluator. Unknown signals, invalid data, changed candles, stale account signals, duplicate fills, margin/slot/daily restrictions remain blocked. Held-position exit branches and existing uncommitted strategy/indicator/exit changes were not edited.

Validation: tests/test_strict_entry_contract.py: 32 passed; Python compilation and scoped git diff --check passed. Tests use isolated paper accounts and mocked exchange calls. The first three failures in test_v2_execution_boundary.py and first two failures in test_peak_trailing_policy.py were also reproduced in a temporary pre-repair source copy. These suites were stopped at those failure limits; no full-suite pass is claimed. Required historical channel_swing, channel_position_path and channel_swing_execution test files, and referenced AIDAN Python/common specifications, are absent. Existing unrelated files also have whitespace errors.

Restart: systemctl --user restart binance-8006.service, active/running at 2026-10-01 03:55:52 UTC (11:55:52 UTC+8), MainPID 465358, NRestarts 0. API returned HTTP 200, is_running=true, paper_trading=true. Natural market data generated SHORT_CONTINUATION for 龙虾/USDT at 03:55:57 UTC; ACCOUNT_SUBMIT and FILLED were observed. Paper fill: 0.0385061, 75 USDT margin, 5x. No artificial live order was submitted. Startup observation showed no Traceback/ERROR or obsolete-signal rejection.

Existing behavior differs from the historical peak-trailing report: the current runner has independent state-machine exits and the paper fill logged a TP1 order. This repair does not certify that historical exit policy or modify those existing paths. Deployment evidence is in deployment.json.

## Follow-up: renamed strict signals

At 04:16:54 UTC the working-tree state machine changed its emitted codes to VALID_OUTSIDE_KC_LONG/SHORT, but the contract retained BREAKOUT_FIRST/CONTINUATION codes. The pre-fix runner-to-paper regression reproduced BLOCKED_OBSOLETE_ENTRY_SIGNAL. Strategy emission and the contract now import the same STRICT_ENTRY_CODES definition from the strategy module. Entry predicates, sizing, exit policy and existing unrelated edits are unchanged. Retired strict codes remain rejected.

Validation: 36 tests passed in tests/test_strict_entry_contract.py, including both entry directions, first-break and continuation code consistency, account/engine execution, stale/changed/retreated quotes, risk checks and deduplication. Python compilation passed. Previous missing specification/test files and unrelated suite failures remain as documented above; this is not a full-suite pass.

Restart at 2026-10-01 05:17:07 UTC (13:17:07 UTC+8): MainPID=506486, active/running, NRestarts=0. API became reachable after startup retry and returned is_running=true, paper_trading=true. No positions at the first post-restart snapshot; isolated fills are not claimed as natural-market fills.
