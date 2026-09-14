### [2026-09-14 12:32:07 UTC+8] - Modification Phase: MA15 Rail Pivot Trend Strategy
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`, `core/guards/abnormal_guard.py`, `core/engine.py`
- **Modification Description**: Channel Swing entry now requires MA3/MA15 and closed KC direction to agree. Positions retain the account hard stop and exit only for a waterfall, two adverse closed candles, or a confirmed MA15 V/peak adjacent to its directional KC rail. An abnormal reversal requires a later closed confirmation candle and is marked as a reverse special-K entry after it opens.
- **Trigger Reason & Requirement**: User-authorized 2026-09-14 strategy change: ride MA3/MA15 and KC trends, recognize MA15 rail-adjacent peaks/valleys in either ordering, and delay abnormal reversals until the next confirmed candle.
- **Verification & Test Status**: Passed `tests/test_channel_ma15_rail_pivot.py` (4), `tests/test_channel_ma15_direction.py -k ma3_ma15` (4), and `tests/test_channel_abnormal_direction_release.py -k waits_for_a_closed_confirmation_after_the_exit_bar` (2). `tests/test_channel_waterfall_threshold.py` has 18 pre-existing expectations at 1.5 ATR while the current configured waterfall threshold is 2.5 ATR.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: MA15 Rail Arming Clarification
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`
- **Modification Description**: The 40% MA15-to-rail distance now arms only the rail-pivot observation. Exit requires the MA15 turning point itself to be the closest point and remain within 40%; a prior 40% approach that widens before the true pivot continues holding. Before rail arming, an aligned opposite MA3/MA15/KC trend exits the position.
- **Trigger Reason & Requirement**: User clarification that 40% alone must not close; an unarmed, clear directional reversal must close, and post-exit entries still require a new outer-rail breakout.
- **Verification & Test Status**: Passed `tests/test_channel_ma15_rail_pivot.py` (7), including MA15 rail arming, unarmed clear reversal, and post-exit outer-breakout reentry locking.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Confirmed Opposite Rail Reversal
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`
- **Modification Description**: A held short now closes after a new confirmed upper-rail breakout; a held long mirrors this at the lower rail. The opposite entry is submitted only after the close is confirmed and passes the normal MA3/MA15/KC and account checks. A rejected replacement entry requires a later, new outer-rail breakout.
- **Trigger Reason & Requirement**: User requirement: if a position returns to and successfully breaks the opposite rail, close and reverse; do not reuse a failed breakout.
- **Verification & Test Status**: Passed `tests/test_channel_ma15_rail_pivot.py` (8) and Python compilation for the changed strategy and runner modules.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Aligned Special-K Reentry
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`, `core/engine.py`
- **Modification Description**: An intrabar special K enters only where its breakout direction agrees with MA3, MA15, and closed KC direction. After a position closes, a later closed bar may establish either a new aligned MA3/MA15/KC entry, a new aligned special K, or a new confirmed outer-rail breakout; pre-close and same-bar signals remain blocked.
- **Trigger Reason & Requirement**: User requirement: open on same-direction special K; after the position closes, seek a new breakout or another valid entry point.
- **Verification & Test Status**: Passed focused aligned-entry and post-exit reentry tests (10); runner route compilation pending final validation.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Held Special-K Profit Lock
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`, `tests/test_channel_profit_protection.py`
- **Modification Description**: A live special K matching an existing position's direction upgrades that position to `entry_special_k` before profit protection runs, applying the established special-K ladder: 2U net-profit arm and 1U trailing offset.
- **Trigger Reason & Requirement**: User requested same-direction special K during a held position to use special-K profit locking.
- **Verification & Test Status**: Passed dedicated special-K ladder tests (2); same-direction detection and compilation pending final validation.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Closed Pivot Entry Execution
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/swing_service.py`, `core/services/symbol_runner.py`, `core/engine.py`
- **Modification Description**: Reconnected the existing confirmed price/MA3 pivot signal as the highest-priority flat-position entry. Confirmed pivots now pass the runner and order-time revalidation, remain protected by account and price safety checks, and use the right-side closed confirmation K as their persisted de-duplication identifier.
- **Trigger Reason & Requirement**: User identified that the agreed pivot-entry, trend-hold, and MA15 rail-pivot exit strategy was not reaching the actual order path.
- **Verification & Test Status**: Passed pivot decision, inside-channel snapshot, concurrent order, and persisted confirmation checks (6).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Special-K Room and Reentry Policy
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/engine.py`, `core/services/symbol_runner.py`, `tests/test_channel_profit_protection.py`
- **Modification Description**: Profit-room estimation now runs only for special-K entries; pivot, MA3/MA15, and ordinary outer-breakout entries no longer calculate or block on it. When a held position is promoted by a same-direction special K, its special-K profit lock can close the position and, only after a confirmed close, submit one same-direction special-K reentry through existing account safety checks.
- **Trigger Reason & Requirement**: User requirement to remove profit-room calculation from all non-special-K entries and immediately continue in the same direction after a held special-K profit exit.
- **Verification & Test Status**: Passed dedicated ordinary-position exclusion, special-K ladder, and confirmed same-side reentry tests (6).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Two Closed Bodies Trend Hold
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/swing_service.py`, `core/services/symbol_runner.py`, `core/engine.py`
- **Modification Description**: Two consecutive closed same-colour body candles now form a general entry without profit-room estimation. Ordinary positions no longer exit on an intermediate MA3/MA15/KC reversal or opposite-rail breakout; they hold until the MA15 rail-adjacent confirmed pivot. Account hard-stop, waterfall, and two-adverse-candle protections remain enabled. Special-K profit locking remains separate.
- **Trigger Reason & Requirement**: User requirement: two green/red candles open a position without profit calculation, then hold until the true MA15 peak/valley.
- **Verification & Test Status**: Passed two-body entry (8), MA15 rail-pivot (9), special-K lock/reentry (6), and Python compilation.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Margin-Scaled Profit Lock
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/exits/profit_protection_service.py`, `tests/test_channel_profit_protection.py`
- **Modification Description**: Profit-lock stages now scale to the actual position margin. At 50U, ordinary positions arm at 2U and trail by 1U; special-K positions arm at 1U and trail by 0.5U. Persisted legacy positions without a margin retain their prior fixed configuration.
- **Trigger Reason & Requirement**: User noted that a 50U account should not need a fixed 4U profit before locking.
- **Verification & Test Status**: Passed margin-scaling, ordinary ladder, and special-K ladder tests (6).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Lobster 1H Bearish Priority Short
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`, `core/engine.py`
- **Modification Description**: When `龍蝦/USDT` is flat, its completed 1H SuperTrend cache is bearish, and the live 1m candle is red, the strategy permits a priority short without waiting for a peak or two closed red candles. The scan, fresh snapshot, and final order decision share the same condition.
- **Trigger Reason & Requirement**: User confirmed: open a Lobster short when it becomes bearish.
- **Verification & Test Status**: Passed dedicated Lobster 1H bearish red-candle test and Python compilation.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Confirmed Three-Point Peak/Valley Exit
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `tests/test_channel_ma15_rail_pivot.py`
- **Modification Description**: The MA15 rail exit now requires three confirmations: MA15's three-point turn reaches the directional rail within 40%, price forms a strict three-point high/low at the same point, and the right-side candle closes in the reversal direction. Equal highs/lows and an unconfirmed candle do not exit.
- **Trigger Reason & Requirement**: User required a true three-point peak/valley exit that is not fooled by a false breakout.
- **Verification & Test Status**: Passed MA15 rail-pivot and fakeout regression tests (11).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Trend Reversal Risk Exit
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`, `tests/test_channel_ma15_direction.py`
- **Modification Description**: Held positions now close, without automatically reversing, if the completed 1H SuperTrend turns against the held side or if 1m MA3, MA15, and closed KC direction synchronously reverse. The existing true three-point peak/valley and emergency exits remain active.
- **Trigger Reason & Requirement**: User clarified that positions must not wait until a late peak/valley when the higher timeframe or complete 1m trend has already reversed.
- **Verification & Test Status**: Passed long/short 1m three-indicator reversal tests (2).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Three-Point Pivot-Only Exit
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`, `tests/test_channel_ma15_rail_pivot.py`
- **Modification Description**: Reverted 1H and intermediate 1m trend reversals as holding exits. 1H now remains a new-entry direction filter only. A held long exits at a strict price and MA3 three-point peak plus a closed red right-side confirmation candle; a held short mirrors this at a strict three-point valley plus a closed green confirmation candle. MA15 rail proximity is no longer required.
- **Trigger Reason & Requirement**: User clarified that a continuously declining Lobster position should exit at a genuine three-point peak/valley, while 1H decides direction only at entry.
- **Verification & Test Status**: Passed long/short three-point true-pivot and fakeout tests (4).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Special-K-Only Profit-Room Gate
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/engine.py`, `core/services/entry_diagnostics_service.py`, `core/services/rule_registry.py`, `tests/test_channel_all_entry_room.py`
- **Modification Description**: Removed profit-room calculation and rejection from ordinary Channel Swing entries only. Special-K entries retain profit-room calculation and are blocked when insufficient. Chart diagnostics report a conflicting completed 1H SuperTrend direction before lower-priority entry detail, and show profit-room status only for a special-K candidate.
- **Trigger Reason & Requirement**: User confirmed that peak/valley swing entries create their profit through the subsequent trend and must not be rejected by a precomputed structural target; special-K protection remains unchanged.
- **Verification & Test Status**: Passed 48 ordinary-entry no-room and 2 special-K room-gate regressions.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Direct KC Direction Entry
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/swing_service.py`, `core/services/symbol_runner.py`, `tests/test_channel_ma15_direction.py`
- **Modification Description**: A confirmed closed KC middle direction now permits a direct same-direction entry without waiting for two same-colour candles or an outer-rail break. Price/MA3 pivots remain higher priority; special-K, two-body, and breakout conditions remain valid signal labels. The 1H filter, account safety, stale-quote checks, abnormal-market guard, and one-entry-per-candle constraint remain in the order path.
- **Trigger Reason & Requirement**: User clarified that a confirmed KC direction should be sufficient to open the same direction; two same-colour candles and breakouts must not be mandatory gates.
- **Verification & Test Status**: Passed direct KC long/short entry tests (2).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: KC Direction Slope Gate
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `tests/test_channel_ma15_direction.py`
- **Modification Description**: Direct KC-direction entries now use the existing 5% middle-line displacement relative to channel width gate. A clear rising/falling KC opens in that direction; a near-flat KC does not open despite its microscopic direction.
- **Trigger Reason & Requirement**: User requested that insufficient KC slope must wait for a genuine trend before opening.
- **Verification & Test Status**: Passed direct-direction and flat-channel long/short tests (4).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Closed 1H Direction Cache
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/engine.py`, `tests/test_channel_1h_closed_cache.py`
- **Modification Description**: The 1H EMA, SuperTrend direction, and ADX cache now discard the forming hourly candle before calculation. The 1H entry filter remains fixed throughout the hour and changes only after a completed 1H candle closes.
- **Trigger Reason & Requirement**: User identified that a 1H direction cannot safely change one second after an entry because the current hourly candle is still forming.
- **Verification & Test Status**: Passed a regression where a closed bullish 1H trend is followed by a forming bearish candle; the cache correctly remains bullish.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: KC Direct Entry Diagnostics
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/entry_diagnostics_service.py`
- **Modification Description**: Chart diagnostics no longer describe direct KC entries as requiring an outer-rail breakout. A directional but insufficiently sloped KC now reports the 5% slope wait; a valid slope can proceed directly when 1H agrees.
- **Trigger Reason & Requirement**: SOL and 1000PEPE charts showed a stale outer-entry explanation despite the authorized direct KC-direction entry rule.
- **Verification & Test Status**: Python compilation and existing 1H diagnostic regressions passed.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Pivot Reversal 1H Exception
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/engine.py`, `core/services/entry_diagnostics_service.py`, `tests/test_channel_pivot_entry.py`
- **Modification Description**: Every confirmed three-point peak/valley entry now bypasses the 1H direction gate and displays as `逆1H可開` when the higher timeframe differs. KC-direction, two-body, breakout, and special-K continuation entries retain the 1H same-direction requirement.
- **Trigger Reason & Requirement**: User clarified that a confirmed peak short or valley long captures the following swing and must not be rejected because the slower 1H trend has not yet changed.
- **Verification & Test Status**: Passed long/short confirmed-pivot entries against the opposite 1H cache (2).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Two-Symbol KC Direct Operation
- **Author**: GitHub Copilot
- **Target Files & Lines**: `.env`, `core/services/entry_diagnostics_service.py`, `docs/active_params.md`
- **Modification Description**: Removed `SOL/USDT` from the active symbols and set `MAX_SLOTS=2` for `龙虾/USDT` and `1000PEPE/USDT`. KC-entry diagnostics now state that a valid KC direction and slope signal proceeds to ordering without profit-room calculation, while accurately listing only the remaining account and market safety checks.
- **Trigger Reason & Requirement**: User requested removal of SOL because it duplicates 1000PEPE behavior, a two-slot allocation, and clear explanation of why a valid entry still requires final execution safety checks.
- **Verification & Test Status**: Passed KC direct-entry and flat-slope regressions (4), Python compilation, regenerated active parameter snapshot, and restarted `binance-8006.service` successfully.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: KC Direction Priority Over 1H
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/engine.py`, `core/services/entry_diagnostics_service.py`, `tests/test_channel_ma15_direction.py`
- **Modification Description**: Removed the 1H SuperTrend rejection gate from all Channel Swing entries. A confirmed KC direction with the 5% slope threshold may now submit directly whether the slower 1H trend agrees or not; 1H remains chart context only. Quote freshness, account balance and two-slot limits, one-entry-per-candle, cooldown, and adverse-candle protection remain mandatory.
- **Trigger Reason & Requirement**: User reported that 1H filtering and stale breakout language prevented valid downtrend entries even when KC had already clearly turned bearish.
- **Verification & Test Status**: Passed direct KC entry for both sides with both aligned and opposite 1H cache directions (4).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Lobster Peak Short 1H Override
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/engine.py`, `core/services/entry_diagnostics_service.py`, `services/api.py`, `web/index.html`
- **Modification Description**: A confirmed `龍蝦/USDT` three-point peak short may enter while the completed 1H trend remains bullish. The chart labels this case `逆1H可開` and the live diagnostic explains that the confirmed Lobster peak short is eligible rather than presenting it as a 1H block.
- **Trigger Reason & Requirement**: User required the visible Lobster peak-short signal to open despite the lagging 1H trend because the following downtrend is the profit opportunity.
- **Verification & Test Status**: Passed a dedicated bullish-1H Lobster peak-short execution regression and Python compilation.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Pivot 1H Alignment Markers
- **Author**: GitHub Copilot
- **Target Files & Lines**: `services/api.py`, `web/index.html`
- **Modification Description**: The 1m chart now evaluates confirmed pivots and labels each peak/valley marker with the last completed 1H SuperTrend alignment at that time: `順1H`, `逆1H`, or `1H未取得`.
- **Trigger Reason & Requirement**: User requested visible 1H trend agreement directly at peak/valley chart signals.
- **Verification & Test Status**: Python API compilation and editor diagnostics passed.

### [2026-09-14 17:28:22 UTC+8] - Modification Phase: MA3 Primary Slope Entry with Auxiliary KC Direction
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `core/services/strategies/pivot_strategy.py::pivot_entry`, `core/services/strategies/outer_strategy.py::aligned_entry`, `core/services/entry_diagnostics_service.py::entry_diagnostics`, `core/services/rule_registry.py::active_entry_rule_lines`, `tests/test_channel_ma3_primary_entry.py`, and related pivot/direction/banner tests.
- **Modification Description**: Replaced the general KC-only 5% slope entry with three completed MA3 points and a same-direction right-hand closed candle. Troughs authorize LONG, peaks authorize SHORT. The latest two completed KC midpoints may be aligned or flat; an opposing slope vetoes this pivot route, with relative 1e-12 numerical tolerance. A simultaneous price-high/low pivot and forming MA3 continuation are no longer required. Live price validity, closed OHLC validation, and invalidation beyond the pivot candle extreme remain. Existing pivot signal codes are retained for execution and persisted position compatibility. Diagnostics now explain MA3 confirmation and the auxiliary KC veto.
- **Trigger Reason & Requirement**: User approved MA3 peak/trough as the primary slope reference and KC rise/fall as auxiliary direction. The initial broader replacement of independent entry routes was rejected by automatic approval review; the accepted narrower change preserves special-K, two-body, MA alignment, legacy continuation, account/risk, and exit implementations. No AIDAN specification files or environment parameters were changed.
- **Verification & Test Status**: New regression file: 54 passed, using rolling MA3 from closes and mocked account execution. Related four-file run: 141 passed, 34 failed. All remaining failures were present in the pre-change baseline (80 passed, 41 failed); there are no new failing case IDs. Baseline failures cover legacy MA15, exits, profit-room/reentry expectations, and retired-rule registration. `git diff --check` passed. No database changes or reseeding needed; no exchange orders, service restart, commit, push, or PR performed. Dedicated branch: `bugfix/ma3-pivot-primary`.


### [2026-09-14 17:41:32 UTC+8] - Modification Phase: Two Closed Breakout Bodies and Confirmed Valley Exits
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `outer_strategy.py::aligned_entry`, `three_point_pivot_exit_ready`; `swing_service.py::channel_swing_action`; `symbol_runner.py`; `engine.py::_place_structured_entry`, `_profit_reentry_ready`, `_channel_peak_exit_reentry_blocked`; entry diagnostics, rule registry and API strategy description; `tests/test_channel_breakout_valley_fix.py`, `tests/test_channel_ma15_rail_pivot.py`, `tests/fixtures/pepe_lobster_20260914_1721.json`.
- **Trigger Reason & Requirement**: User reported 1000PEPE short closed at 17:21:03 (+0.9173 USDT) without a genuine valley, and Lobster long opened at 17:20:06 then stopped at 17:21:47 (-1.9725 USDT). User now requires two closed body candles for ordinary breakouts; a confirmed MA3 peak/trough retains one right-hand closed confirmation candle.
- **Evidence**: Paper-account records identify `KC_THREE_POINT_PIVOT_EXIT` for the PEPE close and `KC_DIRECTION_LONG` for the Lobster entry, followed by `ATR_STOP`. Local chart data shows PEPE's closed KC middle still falling at its small three-point price/MA3 bounce; both Lobster pre-entry closes were inside the upper rail and MA3 had no fresh trough. The running service had started at 17:11:26 UTC+8 and had not loaded the previous turn's code.
- **Modification Description**: Shared ordinary entry requires two consecutive same-direction closed bodies, each at least 20% of candle range; the first body crosses the directional KC rail and both closes plus the current quote stay outside. Closed MA3 and KC must advance in the entry direction. Confirmed MA3 pivots use one same-direction closed right candle and non-opposing KC. Old special-K, symbol-specific and same-side ticket paths cannot waive the two-body rule. Final order validation enforces the shared decision. Existing ordinary rejection-wick/0.25 ATR guard remains. The price/MA3 three-point exit now also verifies non-opposing closed KC and an unbroken pivot at the current quote. Account, ATR, emergency and hard-stop implementations are preserved. New post-exit pivots can use one confirmation; old confirmations remain blocked by the exit-bar gate.
- **Verification & Test Status**: 116 passed, one pre-existing retired-rule registry mismatch; new behavior and supplied-case replays passed. Tests cover mirrored entries/exits, forming/weak/opposite candles, missing breakouts, old ticket bypass, post-exit pivot freshness, account/daily/abnormal guards, rejection wicks, independent ATR/hard stops, and actual scanner execution with isolated accounts. Syntax and `git diff --check` passed. Fixture uses closed local chart indicators; supplied fills are live quote proxies and later live candle extrema/close are excluded. No DB reseeding, environment changes, real exchange orders, commits, pushes or PRs. Preparing restart of the existing paper-trading service on port 8006.
- **Runtime Verification**: Restarted `binance-8006.service` at 2026-09-14 17:41:34 UTC+8 (new launcher PID 1579553). Authenticated local status confirms `is_running=true`, `paper_trading=true`, and the updated two-body/pivot policy. Startup banner at 17:41:36 includes the new entry and KC-confirmed pivot-exit rules. Uvicorn reports successful application startup; no post-restart ERROR/Traceback found.


### [2026-09-14 17:57:51 UTC+8] - Modification Phase: CK-Independent Pivot Entries and Closed-Time Chart Signals
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `pivot_strategy.py::confirmed_ma3_pivot/pivot_entry`, `outer_strategy.py::aligned_entry/three_point_pivot_exit_ready`, `swing_service.py::channel_chop_state`, `engine.py::_place_structured_entry`, entry diagnostics/rule registry, `services/pivot_markers.py`, `services/api.py::_load_klines`, `web/index.html`, and related regression tests.
- **Trigger Reason & Requirement**: User confirmed removal of the 1H entry veto and requested removal of the remaining 1m CK opposition veto for MA3 pivots, shared chart/order conditions, no signals or orders in consolidation, and preservation of the previous exit fix.
- **Modification Description**: Confirmed MA3 pivots no longer wait for CK direction. Shared entry evaluation now rejects existing KC compression/low-momentum consolidation, including pivots; final execution cannot exempt old signal flags. Consolidation always excludes the forming candle. Raw pivot geometry is separated from entry policy so the prior exit still requires non-opposing closed CK, joint price/MA3 turns, closed confirmation and intact current pivot. Chart markers use the shared entry decision at the next candle opening quote, timestamped after confirmation, never later candle extrema/close. Hour alignment uses only hours already closed at that signal time. Labels distinguish strategy signals from actual fills; account balance, positions, cooldown and execution guards remain live order checks and cannot be reconstructed from historical candles.
- **Verification & Test Status**: 139 passed, one pre-existing retired-rule registration mismatch (also recorded in the previous phase). New tests cover mirrored CK-opposed entry execution, real compression and low momentum, signal/diagnostic/order agreement, API read-only responses, close timing, no future quote/hour usage, fresh-snapshot rejection and exit availability during entry consolidation. Prior PEPE/Lobster case regressions and independent ATR/hard stops passed. Python AST and inline JavaScript syntax passed; no DB reseeding or environment changes. Working branch: `bugfix/pivot-entry-chart-confirmation`; pre-existing work retained, no commits/push/PR.
- **Runtime Verification**: Restarted existing paper-trading `binance-8006.service` at 2026-09-14 17:58:26 UTC+8 (launcher PID 1588606). Authenticated `/api/status` confirms running paper mode and the new CK-independent pivot policy. Served HTML contains the new signal labels; both active symbols return HTTP 200 chart data and all returned pivot markers match their confirmation-close timestamps.


### [2026-09-14 18:07:23 UTC+8] - Modification Phase: Post-Peak Pivot-or-Breakout Reentry
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `core/engine.py::_channel_peak_exit_reentry_blocked/_channel_post_peak_exit_info/_fresh_channel_entry_snapshot/_profit_reentry_ready/_place_structured_entry_locked`, entry diagnostics, symbol runner, rule registry, API strategy description, `tests/test_post_peak_reentry_policy.py`.
- **Trigger Reason & Requirement**: User requires a new direct peak/trough entry or an outer-rail breakout after a peak/trough close; no middle-line reentry.
- **Modification Description**: Unified post-close gate permits a new closed MA3 pivot confirmation after the exit candle, or a new outer breakout whose breakout candle starts after the exit candle and completes existing two-body confirmation. Middle direction, return inside the channel, opposite direction, elapsed bar count and legacy flags cannot unlock it. The gate now precedes snapshot early returns, applies to reentry tickets, and repeats immediately before account ordering. Old KC_DIRECTION signal labels are rejected. Persisted latest unmatched peak-close fills restore the restriction after restart; a later open prevents stale recovery. Diagnostics explain the wait. Entry/exit geometry, consolidation policy, account checks, hard stops and ATR exits are preserved.
- **Verification & Test Status**: 157 targeted tests passed, including 26 new mirrored freshness, middle rejection, snapshot bypass, restart recovery, diagnostic, actual scanner and close-during-order checks. Previous breakout, pivot, chart and exit regressions passed. Python AST and `git diff --check` passed. No DB or environment changes, no commits/push/PR.
- **Runtime Verification**: Restarted existing `binance-8006.service`; authenticated `/api/status` at 2026-09-14 18:08:03 UTC+8 confirms running paper mode and the new pivot-or-fresh-outer-break post-peak policy.


### [2026-09-14 18:11:55 UTC+8] - Modification Phase: Direction-Only Pivot Labels without Hourly Alignment
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `services/pivot_markers.py::build_pivot_markers`, `services/api.py::_load_klines`, `web/index.html` pivot markers, `tests/test_pivot_entry_chart_confirmation.py`.
- **Trigger Reason & Requirement**: User requested removal of misleading aligned/opposed 1H labels because 1H no longer gates entries, and valley longs should not wait for hourly agreement.
- **Modification Description**: Removed hourly alignment from marker payloads, text and colors; markers now show valley-long or peak-short signals with side-based colors. The chart no longer fetches hourly candles to build markers. Entry timing retains existing closed right-hand confirmation: the first quote after a valid trough/peak confirmation can execute without an extra candle or hourly direction change. Existing post-peak freshness, outer-breakout, consolidation and exit rules remain unchanged. Optional timing clarification was requested; the existing closed-confirmation rule remains the stated assumption unless the user explicitly selects intrabar entry.
- **Verification & Test Status**: 109 targeted tests passed, including six first-confirmed-quote scanner cases across long/short and bullish/bearish/missing 1H cache. API tests prove no hourly request is made; historical marker timing/no-lookahead tests still pass. Python AST, inline JavaScript syntax and `git diff --check` passed. Existing 8006 service verified running in paper mode before reload. No DB/environment changes, commits/push/PR.
- **Runtime Verification**: Reloaded `binance-8006.service`; at 2026-09-14 18:12:27 UTC+8, authenticated status confirms running paper mode, served HTML contains valley-long/peak-short labels without hourly alignment, and both active symbols return chart markers without alignment fields.

### [2026-09-14 18:25:00 UTC+8] - Modification Phase: Hide Lobster Chart Pivot Hints
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `web/index.html::fetchChartData`
- **Trigger Reason & Requirement**: User requested removal of repeated peak-short and valley-long signal hints from the Lobster candlestick chart.
- **Modification Description**: Skip pivot hint rendering for Lobster symbols in simplified/traditional Chinese, including compact and settlement-qualified symbol forms. Preserve actual trade markers, WAIT/BLOCK markers, other symbols and trading behavior. Existing marker signatures update the chart with the filtered marker list.
- **Verification & Test Status**: Inline JavaScript syntax passed. Executed the chart refresh method with mocked market data for five symbol forms; verified pivot visibility, all four actual fill markers, WAIT markers and repeated refresh. `git diff --check -- web/index.html` passed. HTML is read on each page request, so no service restart is required. No browser visual check, commit or push performed.

### [2026-09-14 18:33:14 UTC+8] - Modification Phase: Live Outer Breakout During Consolidation
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py::aligned_entry`, `core/services/swing_service.py::channel_chop_breakout_action`, `core/engine.py` order validation and post-peak gate, entry diagnostics, rule registry, API strategy text, `tests/test_channel_chop_live_breakout.py`, `tests/test_channel_breakout_valley_fix.py`.
- **Trigger Reason & Requirement**: User explicitly confirmed that a current quote crossing the upper/lower KC rail during consolidation must enter long/short immediately, without waiting for candle close. User identified the separate peak example as approximately 17:55 Taiwan time.
- **Modification Description**: Replace the unconditional consolidation veto with the existing range-break route, validated using the original current candle open, latest quote and current rail. The open must be inside or touching the directional rail and the quote strictly outside it. Scan and quote callbacks share the route through fresh snapshots, final order checks and reentry tickets. A new live range break may pass a previous terminal label and the post-peak gate only on a later candle. Returning inside cancels the candidate without locking out a later valid quote. Preserve intra-range pivot blocking, ordinary closed-body confirmation, same-candle limits, abnormal guards, stop cooldown, balance/slots, quote freshness and all held-position exits. Update diagnostics and active policy descriptions.
- **Incident Evidence**: Authenticated local API reports a Lobster `OPEN_SHORT` at 2026-09-14 17:56:03, price 0.13672833, reason `KC_MA15_PEAK_SHORT`. The 17:55 candle is the right-hand closed confirmation following the 17:54 MA3 peak; it becomes available at 17:56. No Lobster `CLOSE_LONG` exists at 17:55 in the returned trade history; the latest such marker is 18:07:03. The screenshot/time association therefore remains uncertain. Runtime logs and chart diagnostics independently confirm unconditional `KC_CHOP_WAIT` before this fix.
- **Verification & Test Status**: 207 related tests passed with the pre-existing retired-rule registry mismatch excluded; 52 dedicated range-break tests then passed, including 16 additional quote-callback, final-price and ticket-path cases. Coverage includes mirrored compression/low-momentum breaks, no closed confirmation, invalid/touch/inside quotes, original open already outside, fresh post-close entry, repeated-entry prevention, account guards and quote freshness. Updated three old fixture expectations specifically superseded by the user's immediate range-break rule. `git diff --check` passed. No DB reseeding, environment changes, commits or pushes.
- **Runtime Verification**: Reloaded existing paper-trading `binance-8006.service`; authenticated status confirms running paper mode and the new live range-break policy. Chart API returned valid candles and diagnostics. Final diagnostic ordering preserves candidate invalidation ahead of entry readiness; 81 range-break and pivot/chart tests passed after that adjustment.
- **Follow-up Diagnosis**: User asked why Lobster still had no position. Runtime logs show a normal breakout snapshot rejected at 18:34:05, followed by a candidate invalidation lock for bar 1789382040000 and repeated refusals despite `KC_UPPER_BREAKOUT_STRICT` at 18:34:19. At 18:35 the current diagnostic was `KC_SECOND_BODY_WAIT`. This was the ordinary closed-breakout route; the newly allowed range-break route did not authorize this already-outside continuation. The exact first snapshot rejection was not logged beyond its generic message. No additional relaxation of ordinary breakout or invalidation policy was made for this diagnostic question.

### [2026-09-14 18:41:07 UTC+8] - Modification Phase: Retain Completed Breakout Confirmation
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py::confirmed_outer_breakout_bars/confirmed_outer_breakout_ready`, `core/engine.py` candidate retry and post-peak gate, entry diagnostics, rule registry, API strategy text, `tests/test_channel_confirmed_breakout_retention.py`.
- **Trigger Reason & Requirement**: User supplied the 18:35 Lobster chart and challenged repeated requests for a second candle after two confirmed breakout candles already existed.
- **Modification Description**: Locate the actual breakout and first subsequent effective confirmation in the continuous same-color outside-rail run instead of forcing them into the last two closed array positions. Keep eligibility as the run advances; opposite/doji/invalid/inside closes break the sequence. Latest quote must remain outside. Post-peak reentry compares the original breakout timestamp to the exit, so continuation cannot relabel an old breakout as new. Qualified ordinary and range-break candidates retry with fresh snapshots after transient failures and ignore obsolete candidate-invalid locks; filled-candle limits, safety checks and pivot invalidation remain in place.
- **Incident Evidence**: Local chart data confirms the 18:32 original breakout and 18:33 closed confirmation. At 18:35 the previous implementation incorrectly demanded another crossing because it treated 18:33 as the breakout root. The supplied live price 0.141731 also fails the independent rejection-wick rule; after the fix its reason is `KC_BREAKOUT_REJECTION_WAIT`, not `KC_SECOND_BODY_WAIT`. The initial 18:34:05 snapshot cancellation remains unspecified by its generic runtime log.
- **Verification & Test Status**: 246 related tests passed, one known pre-existing retired-rule registry mismatch excluded. Includes 23 confirmation-retention cases with mirrored advancing bars, invalidation/reset boundaries, actual root freshness after closes, stale-lock recovery, and the reported Lobster candle data. `git diff --check` passed. All testing used isolated accounts; no DB reseeding, environment changes, commits or pushes.
- **Runtime Verification**: Restarted `binance-8006.service` and confirmed running paper mode with the confirmation-retention policy in authenticated status. Lobster chart returned 100 rows; live diagnostics now recognize the completed breakout and report the independent `KC_BREAKOUT_REJECTION_WAIT` guard rather than waiting for a second candle. No manual orders submitted.

### [2026-09-14 19:04:01 UTC+8] - Modification Phase: Immediate Trend Reentry and Closed Peak Breakout
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `outer_strategy.py` continuation and peak-break helpers; `engine.py` contextual entry, fill guards, snapshots and profit tickets; `symbol_runner.py`; `entry_diagnostics_service.py`; `rule_registry.py`; `services/api.py`; focused entry tests.
- **Trigger Reason & Requirement**: The user authorized same-candle reentry without profit cooldown while the original trend continues. A missed peak short may enter on one closed lower-rail breakout body; a subsequent long reversal requires two closed breakout bodies. The user explicitly confirmed waiting for the short candle to close.
- **Modification Description**: Recover completed normal closes from fills or confirmed tickets. Require same-side closed MA3 and CK plus a valid directional live quote, rejecting an intervening MA3 reversal. Allow that continuation through same-minute, used-confirmation and profit-cooldown gates; consume each close only after a successful order. Preserve risk, abnormal pullback and adverse-quote checks. Detect the latest MA3 peak within twenty closed bars and require uninterrupted descent plus a bearish lower-rail crossing body of at least 20% of candle range. Prevent pivot and live range paths from bypassing post-peak reversal confirmation. Share the rule registry with status output. Correct five pre-existing entries that did not meet the registry's fixed-return definition, without changing their implementations.
- **Verification & Test Status**: 249 focused tests passed across continuation, peak exits, closed breakouts, chop entries, confirmation retention and the rule registry. Coverage includes concurrent requests, pending/failed closes, stop exits, direction changes, quote reversal during order checks, account risk and matching runtime descriptions. `git diff --check` passed. No database reseed or manual trade was needed.
- **Runtime Verification**: Restarted `binance-8006.service`; systemd reported active/running. Authenticated `/api/status` returned `is_running=true`, `paper_trading=true`, `port=8006`, and all three new rule descriptions. Changes remain in the working tree on `bugfix/trend-reentry-closed-peak-break`; existing unrelated edits were preserved.
