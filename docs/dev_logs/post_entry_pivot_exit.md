# Post-entry pivot exit correction

## Problem
SUI and lobster accepted a pivot on the entry candle even when its start preceded the actual fill. They also searched older candles and replayed that pivot after newer candles or restart. Recent paper closes included three-point-pivot exits around the existing 120-second minimum hold boundary. This establishes a stale-signal mechanism, not proof that every early close was erroneous.

## Change
All symbols now require the pivot candle to begin strictly after the actual position opening timestamp, and its confirmation candle to be the latest closed candle. Entry-candle extrema cannot establish their ordering relative to the fill from OHLC alone. Removed the symbol-specific historical replay exception. Existing minimum-hold modification, account hard stops, waterfall exits, direction correction, and retries of already authorized pending closes remain unchanged.

## Verification
83 passed: test_kc_outer_pivot_strategy.py, test_channel_exit_grace.py, test_realtime_profit_exit.py. Includes long/short SUI/lobster rejection before and after confirmation, later candles, restart-like recovery, and minimum-hold expiry. Previous tests expecting the retired entry-candle exception were updated to reflect the requested behavior.

WAIT candidate remains isolated and disabled. No manual trading orders were placed by this repair. Live market behavior requires subsequent fills for observation.

### [2026-10-17 13:59:43 UTC+8] - Modification Phase: Remove KC midline veto from confirmed pivot exits
- **Author**: Copilot (workspace identity: shudgai999)
- **Target Files & Lines**: `core/services/exits/strict_true_pivot.py` (`confirmed_true_pivot_exit`); `core/services/exits/realtime_profit_exit.py` (three-point-pivot exit path); `tests/test_true_peak_exit_without_midline.py`; `tests/test_wild_swing_strategy.py` (confirmed-pivot fixture)
- **Modification Description**: Confirm post-entry peaks/troughs from three contiguous completed candles. Exit on a strict local extreme plus either reversal body confirmation or MA5 reversal; remove the remaining KC-middleline veto from the legacy real-time pivot branch. Return the actual close result and log failed closes as retry-pending.
- **Trigger Reason & Requirement**: User requested an immediate close on a confirmed true peak without waiting for price to cross the KC middleline.
- **Verification & Test Status**: Focused pivot-exit suite: 35 passed, 4 unrelated entry assertions deselected; Python compilation and `git diff --check` passed. Broader mixed exit/entry regression: 20 failed, 85 passed, 2 deselected; failures include existing entry-route and legacy exit expectations, so deployment remains blocked pending triage.

### [2026-10-17 14:09:00 UTC+8] - Modification Phase: Persist confirmed pivot close retries
- **Author**: Copilot (workspace identity: shudgai999)
- **Target Files & Lines**: `core/services/exits/strict_true_pivot.py` (identity-bound pending-ticket helpers); `core/services/exits/realtime_profit_exit.py` (`enforce_realtime_profit_exit`); `core/services/strategies/wild_swing_strategy.py` (`evaluate_exit`); `core/services/symbol_runner.py` (`_process_wild_swing_symbol`); `tests/test_true_peak_exit_without_midline.py`; `tests/test_wild_swing_runner.py`
- **Modification Description**: Persist a validated, position-identity-bound true-pivot close ticket before submitting the close. The production Wild Swing runner restores the ticket from position metadata and retries it before evaluating new candle structure; the realtime helper shares the same ticket validation. Account hard-stop evaluation remains earlier in the realtime helper.
- **Trigger Reason & Requirement**: Continue validating true-pivot exits and prevent an already-confirmed pivot close from being lost after a failed close and a new candle or restart.
- **Verification & Test Status**: Focused helper/strategy/runner selection: 14 passed. Python compilation and `git diff --check` passed. The broader selected exit/runner/strategy regression reported 34 failed and 113 passed; failures include legacy exit expectations and entry/runner cases outside this change and are not proven baseline failures. WAIT/runtime gates remain blocked, so deployment remains blocked.

### [2026-10-18 09:51:21 UTC+8] - Modification Phase: Confirm true trough one candle earlier
- **Author**: Copilot (workspace identity: shudgai999)
- **Target Files & Lines**: `core/services/exits/strict_true_pivot.py` (`analyze_market_structure`, `confirmed_true_pivot_exit`); `core/services/exits/kc_true_extreme_exit.py` (confirmed-pivot close ticket); `tests/test_true_peak_exit_without_midline.py`; `tests/test_authorized_position_exit.py`
- **Modification Description**: Change confirmed peaks/troughs from a five-closed-candle window to a three-candle local pivot. The pivot candle is checked against one completed candle on each side, then passed through the existing KC exit ticket and authorization path so the automatic-symbol close can be submitted and retried safely.
- **Trigger Reason & Requirement**: User reported a LOBSTER short exiting at 09:39 instead of near the 09:37 true trough and requested an earlier confirmed-pivot exit.
- **Verification & Test Status**: Focused exit regression: 40 passed across `test_true_peak_exit_without_midline.py`, `test_authorized_position_exit.py`, `test_kc_true_extreme_exit.py`, and `test_channel_exit_true_pivot_poll.py`; `git diff --check` passed. This change is in the working tree only; no deployment, restart, or commit performed. A three-point pivot still requires the right-side candle to close and cannot guarantee a fill at the exact low.

### [2026-10-18 10:03:56 UTC+8] - Modification Phase: Hold short positions during falling KC trend
- **Author**: Copilot (workspace identity: shudgai999)
- **Target Files & Lines**: `core/services/exits/kc_true_extreme_exit.py` (`short_kc_bearish_trend_active`, true-extreme and midline evaluation); `core/services/exits/short_oversold_exit.py` (`evaluate_short_oversold_exit`); `core/services/exits/realtime_profit_exit.py` (short oversold-exit integration); `tests/test_kc_true_extreme_exit.py`; `tests/test_true_peak_exit_without_midline.py`; `tests/test_short_oversold_exit.py`
- **Modification Description**: For SHORT positions, suppress confirmed true-valley, reversal, oversold-rebound, and KC-midline strategy exits while the latest two completed KC middle values strictly fall. Cancel an already-persisted ordinary KC exit ticket under that same verified trend. LONG exits are unchanged; account hard-stop and independent safety/profit-lock exits remain outside this gate.
- **Trigger Reason & Requirement**: User instructed that a short should continue running rather than close while the trend is bearish, and confirmed KC middle-line decline as the trend definition.
- **Verification & Test Status**: Focused combined run: 61 passed across KC true-extreme, true-pivot, exit-authorization, pivot-poll, and short-oversold tests, including account-level paper/testnet partial-close authorization. `git diff --check` and Python compilation passed. Working-tree change only; no deployment, restart, or commit performed.


### [2026-10-26 07:48:38 UTC+8] - Modification Phase: Block intermediate position exits and revalidate post-entry pivots
- **Author**: shudgai999 (workspace operator) / Codex
- **Target Files & Lines**: `core/gates/central_entry_gate.py`; `core/gates/holding_protection_gate.py`; `core/gates/top_bottom_reversal_exit.py`; `core/gates/unified_pipeline_gates.py`; `core/paper_account.py`; `core/services/exits/authorized_position_exit.py`; `core/services/math_execution_gate.py`; `core/testnet_account.py`
- **Modification Description**: Revalidate entry direction and structure at the account boundary; reject unsupported automated exits; require verified profit-lock evidence and a fresh, post-entry three-point pivot confirmed by the latest closed candle. Remove standalone buying/selling-pressure authorization and unconditional meme-exit math approval.
- **Trigger Reason & Requirement**: User found that positions closed before the intended final peak/valley while the broader direction remained unchanged, and explicitly requested deployment, restart, commit, and GitHub push.
- **Verification & Test Status**: `py_compile` for all modified Python modules and `git diff --check` passed. No tests were run. Deployment and Git operations are pending environment permission/service-bus access.


### [2026-10-26 07:49:33 UTC+8] - Deployment Phase: Push and restart 8006
- **Author**: shudgai999 (workspace operator) / Codex
- **Target Files & Lines**: `binance-8006.service`; feature branch `feature/kc-1m-continuation-gates`; commit `6c3e362c`; `/api/status`
- **Modification Description**: Pushed the exit/entry gate repair to GitHub and restarted the user-level 8006 service from the current workspace.
- **Trigger Reason & Requirement**: User explicitly requested deployment, restart, commit, and GitHub push.
- **Verification & Test Status**: Service is `active`; `/api/status` returned `is_running=true`, `paper_trading=true`, `environment=binance_testnet`; service working directory is `/home/shudgai999/project/new bn`; local and remote feature branch both point to `6c3e362c`. Syntax checks passed; no automated tests were run.


### [2026-10-26 07:53:16 UTC+8] - Test Update Phase: Pressure signals are not standalone exit authority
- **Author**: shudgai999 (workspace operator) / Codex
- **Target Files & Lines**: `tests/test_confirmed_pressure_exit.py`
- **Modification Description**: Replace assertions that allowed confirmed buying/selling pressure to close a position. Keep pressure classification as diagnostic candidate evidence, and assert both the holding gate and final account authorization reject pressure without a verified true pivot or profit lock.
- **Trigger Reason & Requirement**: User requested updating tests that still asserted the retired early-pressure exit policy.
- **Verification & Test Status**: `pytest -q tests/test_confirmed_pressure_exit.py` — 15 passed.


### [2026-10-26 08:01:25 UTC+8] - Modification Phase: Make the chart KC direction badge responsive
- **Author**: shudgai999 (workspace operator) / Codex
- **Target Files & Lines**: `web/index.html` (`getChartCkTrend`)
- **Modification Description**: Resolve chart direction from the latest forming or just-closed candle first: price beyond an outer rail sets that side; a directional body holding beyond the KC middle sets the matching side. Fall back to the latest three completed KC midlines instead of a five-bar slope. This changes the chart badge only; entry authorization remains unchanged.
- **Trigger Reason & Requirement**: User reported that the chart showed KC direction unclear/down while price and candles had already advanced upward to the upper rail.
- **Verification & Test Status**: Extracted the updated method and `node --check` passed; `git diff --check` passed. No automated test suite run. The root route serves `web/index.html` with no-cache headers, so refreshing the dashboard loads the change without restarting the bot.


### [2026-10-26 08:09:22 UTC+8] - Modification Phase: Correct chart indicator badge
- **Author**: shudgai999 (workspace operator) / Codex
- **Target Files & Lines**: `web/index.html` (chart modal header badge)
- **Modification Description**: Replace the inaccurate EMA15/40 and live holding-policy claim with the indicators actually drawn: 1-minute candles, MA5, MA15, and KC. Clarify in the tooltip that holding protection is enforced separately by strategy gates.
- **Trigger Reason & Requirement**: User asked whether the chart header description needed correction.
- **Verification & Test Status**: `git diff --check` passed. The dashboard serves the HTML file with no-cache headers; refresh the chart to load the label without restarting the service.
