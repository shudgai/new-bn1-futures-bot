# Enforce strict Channel Swing entry gates

The deployed pipeline's fast-lane early return previously skipped trend/convergence/space checks for breakout, continuation and reentry triggers. User explicitly requested strict gate enforcement after an audit identified the bypass.

Breakout triggers still require 60 contiguous valid closed candles plus the live candle; quote strictly outside the same-side KC rail; no opposite gap; directional live body >=0.5 previous closed ATR; distance <=3 ATR; strictly directional quote-adjusted MA5; current channel width greater than 25% of the maximum over 20 closed bars; MA5/MA15 and MA5/target-rail distances at least 25% of current width. Same-side outside opens additionally require previous closed price outside its rail, directional closed CK middle, and quote outside MA5. A reliable untouched 60-bar pivot target must provide at least 0.15% net room after bilateral fees and estimated slippage; targets touched in the live candle are rejected. No ATR synthetic target and no currency exemptions.

An execution-band guard now runs during shared decision validation and immediately before account submission: SHORT quotes at or below `kc_lower` fail with `BLOCK_OVERSOLD_OUTSIDE_KC`; LONG quotes at or above `kc_upper` fail with `BLOCK_OVERBOUGHT_OUTSIDE_KC`. The only inside-band automatic entry authority is the new next-bar KC pullback entry: the preceding completed candle must break its side's outer rail, the following completed candle must retrace inside KC and close directionally after rejecting MA5 or KC middle, and the current quote must remain strictly inside KC and on the directional side of the rejected reference. This authority uses the same account, trend, channel, net-room, and 60-bar gates. It does not infer tick order from candle wicks.

## Verification

The focused pullback and real-time exit regression selection passed: 93 tests. A broader five-module compatibility selection reported 155 passed and 210 failed. It includes tests built around older entry behavior (including outside-KC execution) and at least one unrelated source error; failures were not individually classified, so this is not represented as a passing suite.

Code/signal identity changes fail closed. Engine and account firewall both re-evaluate the shared gate, and final account revalidation updates quote/evidence. strict_gate_evidence records policy, OHLC opening, quote, ATR, rails, slope, spacing, historical width and net target/cost result in each new entry snapshot; closed evidence now includes MA5. Manual entries remain explicit manual operations. Existing slot, balance, finality, signal age, abnormal market, fill deduplication and order locks remain active. User's disabled position hard-stop and enabled 5%/1 percentage point net ROE profit protection are unchanged. Existing holdings are not retroactively closed for failing entry gates.

Validation: 205 passed in selected tests for strict live entry gates, engine/account boundary metadata, per-slot margin and recent exit changes. Includes four trigger families in both directions through real shared gates and mocked account submission, individual gate failures across all trigger types, retry/finality metadata, quote retreat, target reuse and invalid/missing data. No real orders submitted by tests. Metadata-only regression explicitly isolates signal qualification; separate integration tests exercise real strict gates. Historical test_strict_entry_contract fixtures expect obsolete six-candle state-machine behavior and are not counted as passing this policy. WAIT candidate remains isolated and not activated.

## Superseding runtime entry and wick behavior

The strict-gate behavior above is historical and is not the current shared pipeline policy. `EntryGatePipeline` is the current signal authorization point; `WAIT_PIPELINE_TRIGGER` is a per-scan diagnostic meaning no current pipeline signal was found, not a global trading lock. The separate Owner WAIT specification remains a distinct authority and is not implemented or activated by this entry-pipeline change.

The current live breakout threshold is a directional body of at least 0.15 ATR and at least 15% of the live candle range, with the quote strictly beyond the matching KC outer rail; maximum rail distance remains 3.5 ATR. It authorizes on the first qualifying live bar without waiting for multi-bar confirmation. Trend continuation may qualify inside KC when MA5 is correctly ordered against MA15, MA15 is strictly sloped in the entry direction, and the live directional body occupies at least 45% of its range. Shared account, order, slot, capital, daily-risk, quote-freshness, execution-quality, and fresh-snapshot checks remain in force.

`AUTHORIZED_BY_PEAK_FLIP_SHORT` is produced only from a verified successful `CLOSE_LONG` receipt for the same symbol, on that close's 1-minute candle or the immediately following candle. Its live bearish body must be at least 0.35 ATR and 60% of the candle range, and price must be below live-adjusted MA5 or the prior completed candle midpoint. The close receipt and candle identity form the stable signal ID; `override_cooldown` only exempts the existing market-crash cooldown, not account or order-safety checks.

For Channel Swing exits, a short upper wick of at least 0.4 ATR suppresses wick/structure reversal exits for that quote. A short lower-wick rejection requires at least 0.5 ATR and more than twice the real body, with meaningful open profit. A confirmed valley reversal additionally requires a strong bullish body (at least 0.35 ATR and 60% of range) above the prior midpoint and MA5/KC middle. Long upper-wick rejection uses the symmetric 0.5 ATR and 2x-body test. The production real-time exit path invokes the peak/valley evaluator; hard stops and the existing net-profit protection remain independent.

## Pipeline execution quote freshness

On 2026-10-11, the candle-range/ATR, candle-move, strong-body, and rapid-drop-cooldown veto was removed from `_abnormal_market_entry_allowed`. A pipeline-authorized breakout or continuation is not rejected merely for large volatility or a large directional candle. The remaining check requires a finite positive execution quote and a per-symbol observed quote timestamp no older than 30 seconds; stale quotes are logged as `BLOCKED_STALE_QUOTE`, and a missing timestamp as `BLOCKED_QUOTE_TIMESTAMP_UNAVAILABLE`. Insufficient margin remains handled by the existing margin gate, and execution order-book/API validation remains independent.

The prior `BLOCKED_ABNORMAL_MARKET_ENTRY` log stored no range, ATR, or move measurements. Therefore, the exact metric that rejected any historical 09:00 entry cannot be reconstructed from that log; the previous code could reject on configured candle range/ATR, range percentage, adverse move percentage, or strong-body ATR conditions. The old guard no longer emits that reason.

Verification: `tests/test_smart_pipeline.py` includes a 3.5-ATR live bearish breakout through pipeline authorization and engine account submission; the quote-freshness guard also covers stale timestamps and invalid prices.

## Mandatory live MA5 direction gate

Every automatic entry, including breakout, continuation, reversal, and re-entry authorities, must pass the same quote-adjusted MA5 direction check at pipeline authorization and shared-contract/pre-flight validation. LONG requires live MA5 strictly above the previous MA5 and quote at or above live MA5; otherwise the reason is `BLOCKED_BY_MA5_WEAK_SLOPE`. SHORT requires live MA5 strictly below the previous MA5 and quote at or below live MA5; otherwise the reason is `BLOCKED_BY_MA5_STRONG_SLOPE`. Invalid MA evidence fails closed. Existing minimum ATR-slope, account, signal, channel, and order-safety gates remain additional checks.

## Channel Swing climax peak/valley protection

The position-bound Channel Swing peak state arms only after the current quote is at or beyond the position-side KC outer rail and current favorable movement is at least 1.5 times the preceding closed candle ATR. The armed state persists and tracks the best quote observed since entry. It authorizes a symmetric LONG/SHORT peak-profit close when retracement from that observed extreme reaches either 25% of peak favorable movement or 0.4 ATR. A wick reversal can also authorize the close only on a completed opposite-color candle that makes a new observed extreme and has the relevant wick at least 60% of the candle range.

While climax tracking is armed, ordinary MA, momentum-stall, and pullback exits are suppressed. Account hard stops, verified waterfall/emergency defenses, and independently confirmed KC-midline structural exits remain active. The separate SHORT lower-rail-break confirmation guard retains its own 35% rebound-or-bullish-reclaim rule.

### [2026-10-16 21:54:27 UTC+8] - Modification Phase: WILD_SWING crossover and breakout gate repair
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files & Lines**: `core/services/strategies/wild_swing_strategy.py` (`evaluate_entry`); `core/gates/macro_candle_structure_gate.py` (`can_open`); `core/vision/chart_guard.py` (`DynamicVisionGatekeeper`); `core/services/symbol_runner.py`; `tests/test_wild_swing_strategy.py`, `tests/test_wild_swing_runner.py`, `tests/test_macro_candle_structure_gate.py`, `tests/test_dynamic_vision_gate.py`, `tests/test_wild_swing_entry_firewall.py`.
- **Modification Description**: Confirmed KC-outer-pivot MA5/MA15 turns now emit CROSSOVER signals without requiring MA15 to already turn; the MA5 slope and candle close remain directional. A confirmed new turn clears the historical Dynamic Vision lock before Macro Gate evaluation and bypasses only that historical extreme-break check. The confirmed pivot crossover wick threshold is symmetric at 50%; BREAKOUT requires an expanding channel, a >60% directional body and rejects a SHORT lower wick above 50%; breakout/crossover MA15 bias is capped at 2.0 ATR.
- **Trigger Reason & Requirement**: User requested that high/low turn crosses and true KC breakouts reach the local Qwen-VL review instead of deadlocking on stale rejection lows, delayed MA15 slope or inappropriate KC-position limits.
- **Verification & Test Status**: Focused suite — 144 passed with 14 pre-existing matplotlib/pyparsing deprecation warnings; `compileall` and `git diff --check` passed. Both chart API requests returned 200 bars, and the restarted paper service is responsive. Historical 21:16–21:20 Gate attribution remains unavailable because the host journal and retained application log did not contain those candidate decisions; no historical cause is inferred.

### [2026-10-16 22:16:44 UTC+8] - Modification Phase: Remove KC outer-pivot prerequisite from WILD_SWING crossovers
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files & Lines**: `core/services/strategies/wild_swing_strategy.py` (`evaluate_entry`); `core/gates/macro_candle_structure_gate.py` (`can_open`); `tests/test_wild_swing_strategy.py`, `tests/test_macro_candle_structure_gate.py`, `tests/test_wild_swing_entry_firewall.py`.
- **Modification Description**: A closed-candle MA5/MA15 cross now emits a `CROSSOVER` candidate without searching for a prior KC outer-band pivot. LONG still requires MA5 crossing up, rising MA5, a bullish candle and close above MA5; SHORT mirrors this. The candidate uses the generic `MA5_MA15_1M_CROSS` confirmation, which is recognized by the entry-pattern, Macro Candle and Dynamic Vision gates. The Macro gate still applies its existing KC-position, extension, candle-quality and validity rules; a passing candidate is sent to local Qwen-VL, and only explicit approval proceeds.
- **Trigger Reason & Requirement**: User requested immediate crossover entry eligibility independent of prior KC outer-band peak/valley formation, with Gate and local vision review still enforced.
- **Verification & Test Status**: Focused strategy, Macro Gate, Dynamic Vision, firewall and runner tests — 105 passed; `compileall` and `git diff --check` passed. Deployment/restart is explicitly authorized and pending.

### [2026-10-12 23:43:06 UTC+8] - Modification Phase: Strict MA5 entry and climax peak protection
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files & Lines**: `core/services/ma5_entry_slope.py`; `core/gates/pipeline.py`; `core/services/entry_contract.py`; `core/services/entry_firewall.py`; `core/gates/holding_protection_gate.py`; `core/services/exits/peak_trailing_exit.py`; focused tests.
- **Modification Description**: Enforced quote-adjusted MA5 slope/price alignment and dynamic MA5/MA15 anti-chop checks across entry authorization. Added persistent symmetric KC-climax peak/valley tracking, 1.5 ATR plus outer-rail arming, 25% peak-profit or 0.4 ATR retracement exits, completed 60%-wick reversal exits, and suppression of ordinary soft exits while climax tracking is armed.
- **Trigger Reason & Requirement**: User requested no entries against a weakening/strengthening MA5, adaptive MA-cross chop protection, and holding Channel Swing positions through ordinary pullbacks until a confirmed climax reversal.
- **Verification & Test Status**: Focused selection across peak-profit, confirmed-structure, peak-trailing, live-MA5, and MA-cross tests: 117 passed, 2 deselected because they assert an existing exit-whitelist mismatch. The full selection's 2 failures are those whitelist assertions; no whitelist expansion was made. Python compilation, editor Problems, and `git diff --check` passed. Deployment/restart is pending service-target clarification: port 8006 is occupied by a distinct `binance-8006-paper.service` whose working directory is `/home/shudgai999/project/wait-paper-gate-candidate`; the failed `binance-8006.service` cannot start on that port without affecting the paper process.

## Pipeline authorization TTL grace

On 2026-10-11, a three-second monotonic TTL grace was added for an already-authorized intrabar pipeline decision when immediate candle/MA revalidation flickers. The grace is bound to the authorized entry type, side, stable pending-signal ID, confirmation bar, original quote, and ATR. At final account validation, TTL is measured from the monotonic authorization time, not inferred from the live candle's start timestamp; the candle may roll during the three-second window. If final data retrieval outlasts the TTL, the firewall may reauthorize the exact same signal on the same bar; this runs the normal pipeline checks again and does not renew a signal whose trigger, side, or identity has changed. The symbol quote timestamp must remain at most 30 seconds old and adverse price movement no greater than 0.8 ATR. Engine sizing, available margin, position/slot, daily-risk, execution order-book/API, and account submit-lock checks remain active.

The same evidence is independently checked by the account entry firewall immediately before exposure is opened. A missing/expired grace proof, changed signal identity, invalid price, stale timestamp, or excessive adverse movement fails closed. Rejections identify the failed condition and report elapsed TTL, quote age, adverse slippage in ATR, and current/authorized bar IDs to distinguish timing failures from market-safety failures. Unit/integration coverage verifies grace succeeds through engine `ACCOUNT_SUBMIT` and account-firewall validation across a candle timestamp transition, and fails beyond the TTL, slippage, or quote-age boundaries.

## Bearish MA-midline launch and short lower-break hold

The SHORT midline route now recognizes a forming bearish body before a delayed
death-cross or follow-up consolidation candle. The live body must be at least
0.50 previous-candle ATR and at least 50% of its observed range, open above both
quote-adjusted MA5 and MA15, trade below both averages, and reach the KC middle
or remain no more than 0.10 ATR above it. The shared MA-tangle, KC-squeeze,
25% lower-rail room, close cooldown, account, and final-submit checks remain
mandatory. This route retains the existing midline signal code and uses a
distinct `KC_BEARISH_MA_MIDLINE_LAUNCH` phase for exact revalidation.

A strong bearish body that breaks below KC lower no longer authorizes a SHORT
strategy exit while it is still extending. Short-side strategy exits unlock
only after price rebounds at least 35% of the observed open-to-trough excursion,
or after a completed bullish candle closes back above KC lower. The guard tracks
the most recent qualifying lower-rail break in the available frame and updates
the trough when a newer low appears. Account hard stops and circuit-breaker
exits bypass this hold guard.

### [2026-10-12 13:24:07 UTC+8] - Modification Phase: Strict chop, squeeze, room, and anti-flip gates
- **Author**: Copilot AI Assistant
- **Target Files & Lines**: `core/gates/pipeline.py` (`ma5_ma15_cross_chop_problem`, `strict_kc_entry_problem`, `EntryGatePipeline.authorize`); `core/services/entry_contract.py` (`opposite_side_close_cooldown_problem`, `pre_flight_safety_check`, `evaluate_entry_contract`); `core/services/entry_firewall.py` (`validate_account_entry`); `tests/test_ma5_ma15_cross_chop_gate.py`; `tests/test_strict_chop_squeeze_antiflip.py`
- **Modification Description**: Added non-bypassable entry vetoes for any MA5/MA15 gap below 0.08% within the latest 10 candles, at least two MA5/MA15 crosses within the latest 8 candles, KC bandwidth below 0.6%, less than 25% same-side KC room, and opposite-side entries during the close candle and following two candle identities. Pipeline authorization, shared contract evaluation, pre-flight checks, and final account submission now revalidate the same rules. Exact 0.08%, 0.6%, and 25% boundaries remain allowed with floating-point boundary tolerance.
- **Trigger Reason & Requirement**: CAP/USDT range-bound trading repeatedly chased the KC ceiling/floor and reopened the opposite side immediately after a close. The user explicitly required strict chop/squeeze, directional room, and three-bar anti-flip protection.
- **Verification & Test Status**: 269 focused entry-contract/firewall/chop tests passed with 3 pre-existing deprecation warnings. CAP and fast-path compatibility selection reported 33 passed and 11 failed; those failures exercise superseded fixtures with fewer than 10 candles, historical close timestamps inconsistent with their candle time, or old outside-KC entry expectations now intentionally blocked by the 25% room veto. `tests/test_smart_pipeline.py` likewise contains old short-history and bypass expectations and is not represented as passing. No deployment, restart, or live order was performed.

## Intrabar short-exit and closed-bar confirmation

For Channel Swing SHORT positions, a forming 1m candle cannot authorize ordinary wick, color, MA, climax, or fractal exits. The holding-protection gate admits only an independently verified ratchet lock (peak ROE at least 3.5% and at least 25% giveback) or an intrabar V-reversal whose rise from the live low is at least 1.2 ATR and whose quote crosses above KC middle. Account hard stops remain independently enforced. Fractal valley and short lower-wick exits require a completed candle; valley evidence must be bullish, close above MA5, have a body of at least 0.35 ATR, and have an upper wick below 0.4 ATR. Weak green ticks, bearish closes, dojis, and long upper wicks do not close a short.

`cached_tick_indicators` now records current-candle finality, and the realtime service does not let a SHORT climax detector close from a forming candle. The 3-second pipeline authorization TTL behavior is unchanged; the integration test now advances the clock by two seconds and verifies that revalidation reaches `ACCOUNT_SUBMIT`.

### [2026-10-11 09:32:31 UTC+8] - Modification Phase: Intrabar exit gate and TTL regression
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/gates/holding_protection_gate.py` (`evaluate`, `_short_reversal_body_evidence`, `validate_exit`); `core/exits/peak_valley_exit.py` (`PeakValleyExit.evaluate`); `core/services/exits/realtime_profit_exit.py` (`cached_tick_indicators`, `enforce_realtime_profit_exit`); `tests/test_smart_pipeline.py`; this feature log.
- **Modification Description**: Separated forming-bar SHORT exits from closed-bar valley evidence, restricted live SHORT exits to verified V-reversal/ratchet conditions, preserved hard stops, and covered a two-second entry-revalidation grace through account submission.
- **Trigger Reason & Requirement**: User-requested protection against intrabar false rebounds and weak/upper-wick valley exits, with regression coverage for the existing three-second pipeline authorization grace.
- **Verification & Test Status**: Focused regression run: 53 passed, 2 deselected. `py_compile` and `git diff --check` passed. The deselected cases are the existing net-ROE tier-floor expectation and an outdated direct short-pullback helper expectation; neither was changed by this patch.

### [2026-10-11 09:42 UTC+8] - Realtime breakout threshold and authorization TTL boundary
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/gates/pipeline.py` (`detect_realtime_breakout`, pipeline authorization); `core/intelligence/spatial_brain.py` (shared solidity threshold); `core/engine.py` (intrabar authorization evidence); `core/services/entry_firewall.py` (TTL finality); `tests/test_smart_pipeline.py`; this feature log.
- **Modification Description**: Lowered first-bar realtime breakout solidity/body thresholds to 15%/0.15 ATR while retaining directional breakout, rail, and distance checks. Pipeline-authorized intrabar entries now carry their authorization evidence to the final account firewall; TTL uses monotonic elapsed time rather than treating the live bar's timestamp as its close time. When final snapshot retrieval exceeds TTL, the firewall can only renew it by reauthorizing the unchanged signal through the pipeline on the same bar. Account authorization, quote freshness, adverse-slippage, capital, and order locks remain enforced.
- **Trigger Reason & Requirement**: The 09:36 live breakout was missed by the stricter body gate, and final submission logs showed TTL/finality timestamp rejections after pipeline authorization.
- **Verification & Test Status**: `tests/test_smart_pipeline.py`: 35 passed; `tests/test_ai_intelligence_bench.py`: 6 passed; compile and diff checks passed. A broader 113-test compatibility selection reported 77 passed and 36 failed in strict-entry legacy expectations and dated end-to-end fixtures; it is not represented as fully passing. No real order was submitted by tests.

### Per-symbol Local AI market-regime veto

Each active entry symbol is analyzed independently in a background task using its latest 30 completed 1-minute candles. Only a valid, fresh `TRENDING` response permits entry; `CHOPPY`, missing/stale data, disabled AI, timeout, malformed output, and lookup errors fail closed with `BLOCKED_BY_AI_CHOP_REGIME`. The same veto runs in `EntryGatePipeline`, the shared entry contract, and the account entry firewall, including the pipeline TTL-grace route. Market-regime state is in-memory only and is cleared at engine startup, so restart cannot reuse an old classification. Regime details are exposed by the symbol-rotation status endpoint and each refresh is logged.

### [2026-10-11 17:30:07 UTC+8] - Flat KC/MA entry-line veto
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `core/gates/pipeline.py` (`chop_lockout_problem`, `authorize`); `tests/test_smart_pipeline.py`; `tests/test_ai_market_regime.py`.
- **Modification Description**: Require each of KC upper/middle/lower, MA5, and MA15 to move by more than 0.10 of the latest completed ATR over the latest completed 1-minute interval. Any flatter line blocks entry with a line-specific reason. Removed breakout, flip, and retest bypasses for the shared chop gate; explosive moves no longer bypass unknown direction.
- **Trigger Reason & Requirement**: User requested no entry whenever any KC rail or MA5/MA15 is visually flat with low movement.
- **Verification & Test Status**: Focused regression selection: 8 passed. It covers each of the five lines, pipeline rejection for a breakout with a flat KC rail, and no explosive-breakout exception for unknown KC direction. No live orders submitted.

### [2026-10-11 19:10:57 UTC+8] - Breakout flat-line exception and pullback exit tightening
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `core/gates/pipeline.py` (`chop_lockout_problem`, `authorize`); `core/services/entry_contract.py` (`pre_flight_safety_check`, shared contract); `core/services/entry_firewall.py`; `core/exits/peak_valley_exit.py`; `core/gates/holding_protection_gate.py`; `core/services/exits/realtime_profit_exit.py`; corresponding entry/exit tests.
- **Modification Description**: Allow verified immediate/confirmed outer-rail breakout signals through the flat-line threshold at pipeline, shared-contract, and account-firewall checks; all other entry types still fail if any of five lines moves no more than 0.10 ATR. Ordinary same-color/weak intrabar retracements no longer authorize peak exits; the opposing live body must be at least 0.35 ATR and break structure. Waterfall exits now require an adverse body of at least 1.5 ATR through KC middle, replacing the prior 1.2 ATR whole-range test and 0.5% body test. Removed standalone immediate-opposite, non-engulfing top-dump, and KC-middle-break exit authorization; verified top-engulfing flip, real peak/valley, ratchet profit lock, and hard/waterfall exits remain.
- **Follow-up**: Closed-bar reversals now require the adverse body to break both MA5 and the previous candle midpoint; bodies below 0.75 ATR additionally require volume at least 1.2 times the preceding five-bar mean. Legacy fractal and exhaustion validators share this evidence so an ordinary MA5 retest cannot bypass the pullback guard.
- **Trigger Reason & Requirement**: User clarified that live/ordinary breakouts are exempt from flat-line blocking, while other entries are blocked; user also requested investigating pullback closes and limiting ordinary closes to verified peak/valley reversals.
- **Verification & Test Status**: Focused selection: 22 passed before adding the final sub-1.5 ATR realtime regression cases. `reports/second_bar_outside_v2/deployment_events.log` contains entry-gate events only, so it cannot establish the exact reason for any particular live close. No live orders submitted.

### Symmetric MA fast-cross and momentum-burst entries

- `KC_GOLDEN_CROSS_FAST_LONG` and `KC_DEATH_CROSS_FAST_SHORT` now share one evidence model. A fresh MA5/MA15 cross requires a directional live body of at least 0.40 previous-bar ATR, directional MA5 movement, price on the matching side of KC middle, and at least 25% channel room before the matching outer rail.
- A qualified fresh fast cross may ignore only the sub-0.08% MA gap caused by that cross. Two or more MA5/MA15 crossings in the eight-bar window still fail closed. KC squeeze, directional room, close cooldown, account, and order-safety checks remain mandatory.
- `KC_MOMENTUM_BURST_LONG` and `KC_MOMENTUM_BURST_SHORT` are mirrored intrabar authorities. The directional body must cross both MA5 and MA15, reach at least 0.50 previous-bar ATR and 50% of the observed candle range, and have a directional wick no longer than the body. MA5 must turn in the entry direction, and price must reach or approach KC middle within 0.10 ATR.
- Directional KC room remains 25% for ordinary momentum. When the body reaches at least 0.70 ATR, the minimum room dynamically falls to 15%. Only phases explicitly classified as `strategy_type=BREAKOUT` may bypass the in-channel room check; fast-cross and momentum-burst entries do not use that exemption.
- Pipeline, shared contract, final account firewall, engine, paper account, and testnet account use the same signal codes and revalidation rules. This source change does not deploy or restart the service.

### [2026-10-12 16:31:14 UTC+8] - Isolate high-volatility gate regression tests
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files & Lines**: `tests/test_high_volatility_gates.py`; `tests/test_smart_pipeline.py` (move the focused cooldown regression out); `core/gates/pipeline.py` and `core/services/entry_contract.py` (verified, unchanged).
- **Modification Description**: Added a dedicated test module covering the inclusive 50% Momentum Burst body with a 1.0x directional wick, dynamic 15% KC room at 0.70 ATR versus the standard 25% room below that threshold, and same-side 60-second profit / 180-second stop-loss cooldown behavior. Moved the earlier focused cooldown test out of the mixed pipeline suite.
- **Trigger Reason & Requirement**: User requested an isolated, minimal regression patch for the three high-volatility entry rules.
- **Verification & Test Status**: `pytest tests/test_high_volatility_gates.py -q` — 10 passed. Core gate logic was already in place and required no changes; no trading orders were submitted.

### [2026-10-12 10:02:52 UTC+8] - Modification Phase: Fresh-cross gap and squeeze expansion
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files & Lines**: `core/gates/pipeline.py` (`ma5_ma15_cross_chop_problem`, `_recent_bullish_cross_gap_exemption`, `strict_kc_entry_problem`, `_has_directional_squeeze_expansion`); `core/services/entry_contract.py`; `core/services/entry_firewall.py`; `core/services/strategies/unified_entry_strategy.py`; `tests/test_high_volatility_gates.py`; `tests/test_strict_chop_squeeze_antiflip.py`.
- **Modification Description**: A fresh rising LONG MA5/MA15 cross now bypasses only the narrow-gap veto during its two-bar window; cross-chop checks remain active. A KC squeeze may be bypassed only by a directionally aligned MA5 and a shaped live body that is at least 0.7 ATR, or a qualifying 0.5 ATR body that genuinely crosses the same-side outer rail from an opening inside the channel. Ordinary/weak bodies, invalid evidence, and gap openings outside the rail remain blocked. The existing directional room and account/order gates remain in force.
- **Trigger Reason & Requirement**: User requested a bounded exception for early bullish crosses and squeeze-compression momentum expansion while retaining core trend and anti-reversal protections.
- **Verification & Test Status**: `pytest tests/test_high_volatility_gates.py tests/test_strict_chop_squeeze_antiflip.py tests/test_symmetric_fast_cross_and_momentum_burst.py tests/test_ma5_ma15_cross_chop_gate.py -q` — 53 passed. The directional-room test fixture was corrected to use 1.0 ATR so its 25% boundary case remains below the pre-existing 0.7 ATR high-momentum threshold. No live orders were submitted; no deployment or service restart was performed.

### [2026-10-17 21:06:42 UTC+8] - Modification Phase: Enforce market-structure entry veto
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files & Lines**: `core/services/entry_firewall.py` (`market_structure_gate_problem`, `ma5_ma15_entry_gate_problem`, `final_trend_alignment_problem`); `core/engine.py` (both direct entry submit paths); `core/paper_account.py`; `core/testnet_account.py`; `core/services/manual_order_service.py`; focused entry-gate tests.
- **Modification Description**: Added a fail-closed 15-closed-candle structure veto for flat MA5/MA15/KC rails, MA compression, repeated crossings, post-cross chop, and regular MA5 oscillation. LONG/SHORT must also satisfy strict MA5/MA15 order, regression direction, and one-bar MA5 slope. Automatic, WAIT, KC2BAR, reversal, engine-direct, and manual account openings share the hard check; manual orders retain their non-strategy account pathway but require valid MA evidence. Veto logs include slope and MA-gap measurements.
- **Trigger Reason & Requirement**: Owner requested universal, non-bypassable entry gating and explicit veto diagnostics before submission.
- **Verification & Test Status**: Focused `pytest -q tests/test_final_trend_alignment_firewall.py tests/test_wait_entry_firewall.py tests/test_wait_kc2bar_integration.py tests/test_wait_engine_entry.py tests/test_manual_trading.py`: 71 passed. An earlier adjacent exit regression selection reported 75 passed, 11 failed, 2 skipped; those failures are outside the entry-gate changes and must be reviewed before deploying the whole dirty worktree. Python compilation and `git diff --check` passed. No live orders submitted. `binance-8006.service` is not installed in this environment, so deployment/restart could not be performed.

### [2026-11-02] - Repair contradictory reentry location and strategy arbitration

- `POST_CLOSE_KC_REENTRY` retains its new closed-bar, directional MA3, and same-side outside-KC quote requirements. Like the existing two-bar breakout authority, it no longer inherits the contradictory opposite-side 35%/65% pullback-location restriction. The 1.2 ATR MA15-bias veto, directional exhaustion-wick veto, shared structural gate, signal identity, and final execution revalidation remain active. Pivot and crossover authorities retain their location restrictions.
- The execution pipeline may evaluate independent existing entries only when every rejected closed-method candidate failed its strategy-specific KC location check. Bias, exhaustion, invalid data, hourly limits, claimed bars, persistence failures, successful entries, and final submission vetoes remain terminal. Rejection evidence is logged; it is returned unchanged when no alternative qualifies.
- A fallback pivot submission attempt ends this newly reachable route even when unsuccessful. It cannot fall through to a second submission attempt with uncertain order status. No account safety checks or trading parameters are changed.
- Regression coverage includes mirrored LOBSTER/CAP/PEPE reentry through shared math, central, and account execution validators; quote retreat and stale identity; retained bias/wick/data vetoes; independent fallback success/failure; hourly limits and persisted-bar deduplication. Tests run from an isolated source copy without runtime account files. Required historical `test_channel_swing.py`, `test_channel_position_path.py`, and `test_channel_swing_execution.py` are absent in this checkout and cannot be reported as passing.
- Validation: 138 focused tests passed, including the entry-method and KC two-bar suites, swing-pivot execution, disabled submission, final firewall rejection/exchange-boundary cases, and slot/available-margin calculations. The isolated pre-change entry baseline was 73 passed. The full suite was not run.
- Scope: source patch only. Existing unrelated dirty changes are preserved. No service restart, deployment, commit, exchange order, or WAIT implementation/activation is authorized by this fix.

### [2026-11-02] - Explain manual-entry rejection and unsupported account reset

- Status exposes `reset_account_supported` from the account's callable reset capability. The UI disables unsupported reset operations and guards the handler; older status responses enable reset only when `paper_trading` is explicitly true. Demo/live exchange balances and positions are not locally reset. The endpoint retains HTTP 501 for unsupported accounts.
- Manual orders preflight the existing execution validator to return its actual rejection reason. The original frame, finality, direction requirements, and account-side final revalidation remain unchanged. An unfinished candle can still be rejected with `VETO_INTRABAR_NOT_CLOSED`; this patch does not authorize an intrabar bypass or guarantee a fill. A final account rejection no longer misleadingly asserts that a position already exists.
- UI mutation requests report HTTP errors, network failures, and a 20-second timeout. Busy flags are released, no automatic retry is added, and uncertain outcomes explicitly require checking positions/logs before another submission.
- Validation: 26 focused tests passed in an isolated source copy, covering UI handlers using Node's VM with mocked fetch only, capability/status responses, reset rejection without mutation, manual gate diagnostics, retained finality/direction vetoes, and account firewall/exchange boundaries. Vue template compilation, Python compilation, and whitespace checks passed. The two existing `tests/test_manual_trading.py` tests failed identically with both this patch and the original UI/API/manual-service files restored in a separate baseline copy; they are not reported as passing.
- No account reset, deployment, restart, or WAIT activation was performed by this patch. During earlier browser diagnosis, a Demo manual-order request escaped the intended mock and was rejected by the central gate; no position was opened. Subsequent validation used isolated copies and mocked requests, not the running trading UI.

### [2026-11-02] - Owner-approved PEPE/Lobster entry-limit relaxation

- Scope: the Owner approved all four proposed changes for 1000PEPE and Lobster, mirrored LONG/SHORT and including their existing aliases. CAP and other symbols retain their existing limits. These changes supersede the earlier retained 1.2 ATR bias/one-body wick statements only for the approved symbols and entry classes.
- `core/gates/meme_entry_policy.py` defines the shared symbol policy. MA15 distance ceilings are removed from the entry-method, macro-structure, and math entry checks for these two symbols; finite positive MA15/ATR data and existing direction checks remain required.
- Breakout/continuation terminal wicks are rejected only when the directional wick strictly exceeds both 1.5 times the candle body and 65% of its range. Macro, entry-method, central, math, and wild-strategy wick-wait checks use the same test. Crossover/reversal wick requirements, including long-pin reversal confirmation, remain unchanged.
- Approved breakout/continuation distance limits are 3.5 ATR from the same-side KC rail and 3.5 ATR from MA5. Entry-method final quote checks and the late-chase guard reject an overextended signal close even if the latest quote retreats. Existing outer-continuation freshness is extended from 8 to 12 completed outside bars; 13 is rejected. This does not create an entry authority, remove closed-bar confirmation, or authorize immediate post-close reentry.
- Symbol identity is threaded through wild scanning, closed-outer revalidation, trend riding, the firewall, engine call sites, and final math validation. No CAP/global constant is increased to emulate a symbol-specific exception.
- Validation: 430 focused tests passed from an isolated source copy without runtime account files. Coverage includes both symbols/directions, CAP non-regression, alias isolation, large/invalid MA15, wick boundaries and extreme rejection, 3.5 ATR exact/over-limit quote boundaries, 8/9/12/13-bar continuation freshness, actual unmocked continuation scan/firewall agreement, stale signal/finality/quote-retreat vetoes, disabled submission, account firewall/exchange boundaries, and available-margin limits. Python compilation and whitespace checks passed.
- An expanded central/wild-strategy run reported 318 passed and 16 failed before additional boundary cases were added. The same 16 failures were reproduced with the original central/macro/breakout/trend/firewall/wild-strategy/engine sources in a separate isolated copy (39 passed, 16 failed for those two suites). They are not new passes or a full-suite success. The previously documented missing historical Channel Swing test files remain unavailable.
- Source patch only: no commit, deployment, restart, reset, order submission, leverage/capital change, or exit-policy change. Existing unrelated dirty edits, including math exit changes, are preserved. The paper account's configured initial 150 USDT balance is unchanged.
- WAIT is outside this authorization. Its direction/symbol/live/ATR/bridge/deduplication/consumption/persistence/restart/order-safety/arbitration gates are NOT_TESTED; doji PASS remains specification/static-trace only. `TRADING_GATE = BLOCK`; this review label does not stop or change the running service.

### [2026-11-02] - Owner-authorized GitHub publication and paper-service restart

- The Owner subsequently authorized committing, GitHub publication, deployment, and restart. The staged release includes this session's contradictory-entry repair, UI/manual-order diagnostics, reset capability, and the four approved PEPE/Lobster entry-limit changes. It excludes unrelated existing exit edits, account files, databases, telemetry, and `.env`.
- Exact staged source was exported with `git archive` and validated independently of the dirty worktree: 448 focused tests passed, with four existing FastAPI lifecycle deprecation warnings. This selection combines the entry-limit, identity/finality, UI/status, account-boundary, and margin regressions without counting duplicate selectors twice.
- Restart uses the existing working directory and preserves its pre-existing local exit changes; runtime is not claimed to be a pristine checkout of the published commit. Paper mode and the configured 150 USDT initial balance remain local environment settings, not GitHub secrets or account-state artifacts.
- Operational authorization does not implement or validate WAIT. All previously reported WAIT implementation/runtime gates remain NOT_TESTED, with specification/static-trace-only doji PASS and `TRADING_GATE = BLOCK`. Readiness checks establish service/API availability, not a confirmed entry fill or production trading certification.

### [2026-11-02] - Restore independent chart market-data refresh

- Runtime evidence: PEPE chart responses repeatedly ended at 11:36 UTC while the public exchange already supplied newer candles. A different request limit still returned the same 150-row snapshot with `cache.source=live`. Lobster responses were current. This proves stale chart input, not a browser-only rendering problem; the exact runtime trading-cache starvation sequence was not instrumented.
- The chart loader now reads public OHLCV through the existing market-data helper rather than the trading engine's per-symbol REST cooldown/cache. Chart single-flight and five-second caching remain; the chart fetch is bounded to six seconds and one attempt, within the browser's eight-second deadline. Trading data providers, entry rules, exits, account state, and the 150 USDT setting are unchanged.
- Reject stale upstream tail timestamps before saving a fresh chart payload. Failed refreshes log the failure and explicitly label retained chart data as stale. Tail `is_live` follows actual candle finality, not merely last-row position. The UI warns on stale data or failed refresh even when old candles remain visible.
- Validation: 48 isolated focused tests passed, including rollover, PEPE/Lobster public-fetch routing, stale-upstream rejection, explicit stale fallback, single-flight, timeout cleanup, candle finality, offline JavaScript chart-error handling, existing stream behavior, and manual UI controls. Four existing FastAPI lifecycle deprecation warnings remain. An isolated real public-data request returned 200 PEPE candles ending at the current 11:44 UTC minute.
- The Owner explicitly selected deployment, service restart, and GitHub publication after validation. Only the API, chart UI, new chart tests, and this log belong to this patch. Existing unrelated dirty exit/account edits remain excluded from the commit; the service still runs the working directory rather than a pristine published tree. No reset or manual order is performed.
- WAIT remains outside this chart patch. Direction/symbol/live/ATR/bridge/deduplication/consumption/persistence/restart/order-safety/arbitration gates are NOT_TESTED. Doji PASS is specification/static-trace only; `TRADING_GATE = BLOCK` remains a readiness-review label, not a service-stop command or claim that a trade was filled.

### [2026-11-02] - Unify active entry qualification on MA5/MA15

- The Owner requested MA5/MA15 consistency after confirming that the chart already displayed MA5/MA15 while several entry gates still used MA3. The active execution pipeline, closed-entry authorities, pivot/reentry qualification, KC-midline crossing, direction lock, consolidation filter, strict whitelist, shared math gate, and paper/testnet entry boundaries now use MA5. KC, candle, quote, order, capital, and risk conditions remain additional requirements; a crossover is an evaluation opportunity, not an unconditional order or guaranteed fill.
- The new crossover authority is `MA5_MA15_CROSS`. Literal persisted `MA3_MA15_CROSS` authorities fail revalidation. Legacy module paths and import aliases remain compatible but delegate to MA5 evaluation. The persisted `strict_ma3_ma15_last_trade_bar` key deliberately retains its original name so migration/restart cannot erase an existing candle claim.
- Missing calculated MA5 requires five closes, not three. Authorized live central validation adjusts MA5 by the quote delta divided by five and MA15 by the delta divided by fifteen. Invalid/nonpositive fast-line data fails closed. Entry-only chop calculations run in the flat-position phase; the existing exit funnel retains its MA3 crossing-count input. Existing MA3 exit calculations, indicator/model compatibility fields, and unrelated legacy engine paths are not represented as globally removed.
- Shared trend-riding, high-position defense, and master-funnel exit function bodies were compared structurally against HEAD and are unchanged. Existing unrelated dirty exit/account edits remain intact; validation uses the current worktree copy and does not claim those pre-existing edits belong to this patch.
- Validation used isolated source copies without runtime account files: 543 focused tests passed. Expanded compatibility checks added 99 passed and 43 failed; all 43 failure identities were independently reproduced against archived HEAD. Combined result: 642 passed, 43 pre-existing failures, and four existing FastAPI lifecycle deprecation warnings. This is not a full-suite pass. Python compilation and whitespace checks passed.
- Regression coverage includes LONG/SHORT for PEPE, Lobster, and CAP; MA3 absent/invalid/opposed to MA5; crossover and obsolete-authority rejection; five-period warmup/live-quote calculation; pivot and post-close reentry; central/math/final-execution agreement; persisted candle claims and hourly limits; account submission and margin boundaries; and existing chart/reset/manual-control behavior. Test fixture migrations preserve the original numeric thresholds; independently valid alternative entry paths are excluded when testing a specific rejection.
- Source changes only: no commit, GitHub push, service restart, reset, manual order, or WAIT implementation/activation was performed. The service has not been restarted onto this patch; no runtime source-identity or entry-fill claim is inferred from source tests. The three historical Channel Swing test files previously documented as absent remain unavailable.

Mandatory WAIT review (not implemented or activated by this patch):

```text
WAIT_LONG_GATE = NOT_TESTED
WAIT_SHORT_GATE = NOT_TESTED
LOBSTER_WAIT_GATE = NOT_TESTED
CAP_WAIT_GATE = NOT_TESTED
LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_ATR_GATE = NOT_TESTED
WAIT_BRIDGE_GATE = NOT_TESTED
WAIT_DEDUPE_GATE = NOT_TESTED
WAIT_CONSUMPTION_GATE = NOT_TESTED
WAIT_PERSISTENCE_GATE = NOT_TESTED
WAIT_RESTART_GATE = NOT_TESTED
WAIT_ORDER_SAFETY_GATE = NOT_TESTED
WAIT_ARBITRATION_GATE = NOT_TESTED
DOJI_CLASSIFICATION_GATE = PASS
TRADING_GATE = BLOCK
WAIT_CODE_PATCH_AUTHORIZED = NO
DEPLOY_AUTHORIZED = NO
RESTART_AUTHORIZED = NO
LIVE_ORDER_AUTHORIZED = NO
```

Doji PASS refers only to the existing approved specification/static helper trace, not runtime verification. `TRADING_GATE = BLOCK` is the readiness-review outcome, not a claim that the existing service was stopped.

### [2026-11-02] - MA5 release authorization

- The Owner subsequently explicitly requested restart, commit, and GitHub publication of the MA5/MA15 unification patch. This supersedes the operational authorization flags above only for this MA5 release; WAIT implementation and live-order authority are not added.
- Stage only the MA5 entry changes, directly related tests, and this log. Existing unrelated exit changes in mixed files are excluded from the commit and remain in the working directory. Restarting the existing service preserves those local changes; the runtime must not be described as a pristine checkout of the published commit.
- Validate the exact staged tree independently before publication, then restart the existing paper service and verify HTTP readiness and chart freshness. Do not reset the account or submit manual test orders.
- `DEPLOY_AUTHORIZED = YES` and `RESTART_AUTHORIZED = YES` for this release. All WAIT runtime gates remain NOT_TESTED, specification/static-trace doji remains PASS, and the independent WAIT readiness result remains `TRADING_GATE = BLOCK`.

### [2026-11-02] - MA5 release result and missed-breakout evidence

- Published commit `590c11ba2fe70285cdd20c22efe063f2ed8c4f43` to `bugfix/test-failure-triage`. The exact staged tree passed 543 focused tests before publication. Restart completed at 12:12:28 UTC; the API reported paper mode, an active bot, and 150 USDT balance/available balance. PEPE and Lobster chart endpoints each returned 200 candles with current 12:12 UTC live tails.
- Historical journal evidence: Lobster reached `EXECUTION_ENTER` at 11:56 and 11:57 UTC, and PEPE at 12:08 UTC, but execution raised `evaluate_direction_master_lock() got an unexpected keyword argument 'ma5_prev'`. Updating files in the active working directory while the old process retained loaded helpers exposed incompatible interfaces. The MA5 update caused this mismatch; it must not be described as a valid risk rejection. The restart loaded consistent interfaces; no recurrence was observed during the post-restart check. No historical signal was manually backfilled.
- PEPE at 12:06 UTC also had a legacy MA3 crossover rejected by the KC-relative-location rule. Subsequent breakout candidates recorded body/range and MA5-distance vetoes before the 12:08 submission attempt. These are distinct from the execution exception. A visible breakout alone does not establish that all signal/finality/account gates passed.
- The runtime retains the separately pre-existing dirty exit changes; it is not a pristine checkout of the published commit. No account reset or manual order was performed.

### [2026-11-02] - Owner-selected KC-reversal-only holding policy

- After reviewing the missed entries, the Owner explicitly selected: hold while completed KC-middle direction remains unchanged; ordinary strategy exit only on confirmed completed KC-middle reversal. Do not exit solely for MA pullbacks, local pivots, staged profit floors, or profit giveback. Existing independent emergency/account protection and explicit manual close remain unchanged.
- The public `ExitGate` now suppresses profit-lock results and evaluates the existing authoritative closed-KC reversal. The execution manager holds even when its legacy Scheme 1 profit calculation requests a close. Shared account-close authorization rejects old profit-lock/pivot pending reasons, including genuinely armed locks; final execution revalidates the current authoritative candle frame. Profit-state records are not deleted or rewritten into new close authority.
- A LONG closes on a strict downward change in the latest completed KC middle; a SHORT closes on a strict upward change. Flat, still-aligned, live-only, stale, future, gapped, invalid OHLC, missing-finality, and pre-entry evidence do not authorize a new ordinary close. Submit at the current quote, not the historical middle or a hypothetical peak; a failed close leaves the position for re-evaluation/retry. Do not auto-reverse.
- Validation: 540 focused tests passed in an isolated current-worktree source copy, combining the new holding policy with the MA5 entry, reentry, submission, and margin regressions. This includes all three symbols in both directions, truly armed profit locks, actual paper/testnet close boundaries with fake exchanges, confirmed reversal, close failure/retry, manual close, verified hard-stop preservation, and rejection of a profit floor relabeled as an emergency. Python compilation passed.
- A separate legacy compatibility selection reported 180 passed, 24 failed, one skipped, and five deselected obsolete cases. These failures have not been independently classified against a pre-policy dirty-tree baseline and are not represented as a passing suite or as newly fixed. The focused result above is not a full-suite success.
- This new holding-policy patch has not been committed, pushed, or restarted into the service. The prior MA5 release authorization/result must not be confused with deployment of this later policy. Existing unrelated dirty changes remain preserved.

### [2026-11-02] - Holding-policy publication authorization

- The Owner subsequently selected `publish_restart`, explicitly authorizing commit, GitHub publication, and restart of the KC-reversal-only policy and its necessary previously uncommitted KC-wave dependencies. Exclude unrelated red-eye/tiered-profit/trend-lock test edits, telemetry, account files, databases, and environment settings.
- The final close-boundary diagnostics now name confirmed KC reversal rather than implying profit-lock authority remains valid. Validate the exact staged tree before publication. WAIT remains untouched and not activated; its previously recorded NOT_TESTED runtime gates and `TRADING_GATE = BLOCK` remain unchanged.
- The subsequent KC entry-direction patch below is a separate source change and does not inherit publication/restart authorization.

### [2026-11-02] - Completed KC direction for entries and chart labels

- The Owner requested correction after reviewing the difference between bull/bear regime filtering and KC direction. Entry direction now compares the latest two explicitly completed KC middle values: increasing permits LONG evaluation only, decreasing permits SHORT evaluation only, and flat/unavailable data blocks new entries. This is a direction filter, not an unconditional order trigger.
- The shared completed-candle helper rejects unknown finality, nonpositive/nonfinite middle values, duplicate/reversed timestamps, and inconsistent candle spacing. DataPipeline reports this one-bar closed slope and KC trend without overriding them from EMA60, price location, or higher-timeframe regimes. Its separate HTF macro field remains intact for existing emergency-exit evidence; no emergency authority is removed.
- The execution pipeline no longer fetches 15-minute candles to authorize entry direction, consults old cached bull/bear state to retain direction, or changes direction from MA3/current-price location. Legacy downstream regime predicates share the completed KC direction. Entry metadata, the entry gate, consolidation blacklist, master funnel, and final math gate no longer impose an independent opposing macro-regime veto. Long-lookback KC/MA15 trend vetoes are removed from the fallback entry direction check; existing MA5/MA15, candle, breakout, squeeze/chop, distance, sizing, capital, claims, and account checks remain.
- The final math gate enforces completed KC direction even for override-tagged entry classes. Live candles cannot reverse that direction. EntryGate now requires completed candle evidence rather than accepting a naked caller-supplied direction/slope.
- The chart API uses the same completed KC comparison and returns `kc_direction` (`UP`, `DOWN`, `FLAT`, `UNKNOWN`); its legacy `macro_trend` response is a compatibility projection. Chart requests no longer write execution-manager trend state. Synthetic chart direction-veto diagnostics use completed KC, not the live midline. The UI displays KC up/down/flat/unavailable and ignores MA3, live-price direction, and old backend macro labels.
- Validation: 666 tests passed in an isolated worktree source copy, covering all three symbols in both directions, conflicting HTF/cached labels, live-candle independence, invalid/flat evidence, final submission, account boundaries, chart API/UI, and existing KC exits. Python compilation and whitespace checks passed. The old bare-slope breakout fixture now supplies two explicitly completed KC values; the flat-market test explicitly verifies rejection before method evaluation. No price/body/distance thresholds were relaxed to make tests pass.
- After synchronizing the additional synthetic chart-veto diagnostic, its affected direction/chart selection was revalidated: 63 passed. Test isolation explicitly creates/removes the optional engine diagnostic cache without touching runtime state.
- Expanded legacy compatibility initially reported 294 passed / 17 failed. Archived published HEAD independently reported 295 passed / 16 failed with the same 16 failure identities; the additional failure was the bare-slope fixture updated above and is included in the passing focused selection. The remaining 16 existing failures are not a full-suite success. The historical Channel Swing/position-path/execution test files remain absent.
- No commit, push, deployment, restart, account reset, manual order, or historical-signal backfill is authorized or performed by this source patch. The running service has not been restarted onto it; source tests do not establish runtime adoption or any successful live entry. Existing unrelated dirty files are preserved.

Mandatory WAIT review for this patch:

```text
WAIT_LONG_GATE = NOT_TESTED
WAIT_SHORT_GATE = NOT_TESTED
LOBSTER_WAIT_GATE = NOT_TESTED
CAP_WAIT_GATE = NOT_TESTED
LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_ATR_GATE = NOT_TESTED
WAIT_BRIDGE_GATE = NOT_TESTED
WAIT_DEDUPE_GATE = NOT_TESTED
WAIT_CONSUMPTION_GATE = NOT_TESTED
WAIT_PERSISTENCE_GATE = NOT_TESTED
WAIT_RESTART_GATE = NOT_TESTED
WAIT_ORDER_SAFETY_GATE = NOT_TESTED
WAIT_ARBITRATION_GATE = NOT_TESTED
DOJI_CLASSIFICATION_GATE = PASS
TRADING_GATE = BLOCK
WAIT_CODE_PATCH_AUTHORIZED = NO
DEPLOY_AUTHORIZED = NO
RESTART_AUTHORIZED = NO
LIVE_ORDER_AUTHORIZED = NO
```

Doji PASS is the existing specification/static-helper trace only. WAIT remains unimplemented/unverified in this patch; the readiness BLOCK does not mean the existing service was stopped.

### [2026-11-02] - Repairing the reported compatibility failures

- The Owner requested fixes for the 16 failures reported above. The three original failing modules now pass within a combined 21-module regression selection: **748 passed, 0 failed**, with four existing FastAPI lifecycle deprecation warnings. This is a scoped regression result, not a claim that the entire repository passes.
- Corrected fixtures to provide explicit completed-candle evidence, valid current KC/MA5/MA15 paths, and isolated paper-account/audit state. Tests no longer depend on shared persisted account files or expect superseded EMA40/profit-lock exits. Updated the extended final-math tests to require completed KC evidence while allowing the absence of an independent macro label. No entry threshold, pattern whitelist, sizing, leverage, or account risk parameter was relaxed.
- Fixed an uninitialized `strong_peak_reversal` in the legacy LONG evaluator using the matching complete reversal predicate rather than a constant fallback. The public ordinary-exit policy still requires confirmed completed KC midline reversal; this fix does not reactivate ordinary pivot or profit-lock exits.
- Preserved confirmed absolute-safety reasons through the execution pipeline and common account close path. Legacy ordinary-exit logic can no longer overwrite them with a nonauthorized trend-line reason. The final mathematical/account close gate still validates market evidence: a fabricated emergency label with an inside-channel quote is rejected. Regressions cover hard stop, opposite-band breach, and waterfall, LONG/SHORT independently for 1000PEPE, lobster, and CAP.
- Completed paper order-audit stages 3 and 4 around simulated execution and persisted position creation. Both LONG and SHORT success tests verify all five stages, one signal identity, positive filled quantity, and persisted positions. Simulated confirmations explicitly use `PAPER_FILLED` and a `PAPER_` order identity; these are not exchange fills.
- Extended close-deduplication coverage exposed a coupled retry bug: the confirmed-KC reason was missing from an old strategy-reason prefix list, allowing a strategy call using `is_manual=True` to bypass the existing failed-close cooldown. Only the exact actual manual-close reason now qualifies for that manual exemption. LONG/SHORT tests verify one failed submission during cooldown, successful genuine manual override, and rejection of obsolete middle/profit-lock exits before any order or retry cooldown is created. Existing hard-stop priority remains unchanged.
- Macro-aligned opposite-band tests explicitly supply the desired HTF context; their 1-minute fixture is not proof of the historical runtime macro classification. A separate test verifies that the 2-percent account hard stop takes precedence when it is also reached.
- Python compilation and `git diff --check` passed. Tests ran in an isolated source copy without runtime credentials or account data. No commit, push, deployment, restart, account reset, or live order was performed; source validation does not establish adoption by the running service. Unrelated dirty files remain untouched.

Mandatory WAIT review for this repair:

```text
WAIT_LONG_GATE = NOT_TESTED
WAIT_SHORT_GATE = NOT_TESTED
LOBSTER_WAIT_GATE = NOT_TESTED
CAP_WAIT_GATE = NOT_TESTED
LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_ATR_GATE = NOT_TESTED
WAIT_BRIDGE_GATE = NOT_TESTED
WAIT_DEDUPE_GATE = NOT_TESTED
WAIT_CONSUMPTION_GATE = NOT_TESTED
WAIT_PERSISTENCE_GATE = NOT_TESTED
WAIT_RESTART_GATE = NOT_TESTED
WAIT_ORDER_SAFETY_GATE = NOT_TESTED
WAIT_ARBITRATION_GATE = NOT_TESTED
DOJI_CLASSIFICATION_GATE = PASS
TRADING_GATE = BLOCK
WAIT_CODE_PATCH_AUTHORIZED = NO
DEPLOY_AUTHORIZED = NO
RESTART_AUTHORIZED = NO
LIVE_ORDER_AUTHORIZED = NO
```

Doji PASS remains specification/static-helper evidence only, not runtime verification. WAIT implementation and operation are outside this repair's scope and remain unverified. Readiness BLOCK does not indicate that the existing service was stopped.

### [2026-11-02] - KC direction and failure-repair publication authorization

- The Owner explicitly requested deployment, restart, commit, and GitHub publication of the completed KC-direction changes and failure repairs. This grants publication and restart authority for those changes only; it does not authorize WAIT changes or activation, account reset, manual entries, or changes to capital/leverage.
- Include only the reviewed implementation, directly related regression tests, chart direction display, and this log. Exclude unrelated red-eye/tiered-profit/trend-lock edits, telemetry, account files, databases, and environment settings. Validate the exact staged source snapshot before committing and pushing, then restart the existing `binance-8006.service`.
- Verify the replacement service process, HTTP readiness, and startup journal without calling status/price endpoints that mutate account state. The preceding WAIT review remains unchanged: all WAIT runtime gates are NOT_TESTED, Doji PASS is specification/static evidence only, and `TRADING_GATE = BLOCK`. The deployment authorization is not proof of WAIT production readiness.

### [2026-11-02] - New fills without ordinary price/ATR hard stops

- The Owner selected `keep_disaster_only` and `new_only`: remove ordinary position price/ATR loss stops for future fills only, preserve existing positions and independent emergency/account protection. This source change has not been committed, pushed, deployed, or restarted; the preceding publication authorization covered the prior patch, not this new policy.
- New paper position creation and confirmed exchange-fill finalization stamp `position_loss_policy=NO_PRICE_ATR_STOP_V1` into both position and persistent metadata. The shared context allowlists preserve it through refresh, partial-close state, and restart. Loading or refreshing an old position never stamps the policy; missing/unknown policy retains existing behavior. A paper top-up does not rewrite the existing position policy.
- ExitGate skips its ordinary 3-ATR/2-percent loss trigger only for stamped positions. The final execution math gate rejects stale numeric hard-stop requests for those positions, including initial-ATR, margin-loss, and legacy circuit-breaker labels. This closes the downstream bypass without changing the existing threshold values or global hard-stop flag.
- Confirmed closed KC reversal remains the ordinary exit authority. Manual close, independently verified waterfalls/opposite-band emergencies, two-adverse-candle protection, liquidation/data-outage emergencies, and the separate Live Mainnet account circuit breaker remain unchanged. The account-level 15-percent equity latch uses its existing independent reduce-only flatten path; it is not disabled by a position policy. That account equity circuit breaker is Live Mainnet only, not a new paper/testnet circuit breaker.
- Validation in an isolated source copy: **872 passed, 2 skipped**, four existing FastAPI lifecycle deprecation warnings. Both skips are existing removed-early-profit-guard tests. Coverage includes LONG/SHORT numeric-stop suppression versus unchanged legacy holdings on all three symbols, real paper/testnet final-close rejection, new-fill metadata persistence across refresh/restart, unchanged KC/manual exits, three-symbol emergency pipeline closes, and actual account-circuit-breaker flatten calls. Python compilation and whitespace checks passed. No runtime credentials or account state were copied into the test directory.
- During the follow-up read-only entry investigation, runtime journal and persisted paper trade/position agreed that lobster opened LONG at 21:14:00 Taipei via `KC_TWO_BAR_BREAKOUT`, approximately 0.01818182 at 5x. The earlier MA5/MA15 cross candidate had been rejected by relative KC location. This existing fill predates deployment of this new policy and must not be retroactively modified. The chart returned a trade marker while `current_position` was null; persistent account state, not that null field, established the holding.

Mandatory review for this position-policy patch:

```text
WAIT_LONG_GATE = NOT_TESTED
WAIT_SHORT_GATE = NOT_TESTED
LOBSTER_WAIT_GATE = NOT_TESTED
CAP_WAIT_GATE = NOT_TESTED
LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_ATR_GATE = NOT_TESTED
WAIT_BRIDGE_GATE = NOT_TESTED
WAIT_DEDUPE_GATE = NOT_TESTED
WAIT_CONSUMPTION_GATE = NOT_TESTED
WAIT_PERSISTENCE_GATE = NOT_TESTED
WAIT_RESTART_GATE = NOT_TESTED
WAIT_ORDER_SAFETY_GATE = NOT_TESTED
WAIT_ARBITRATION_GATE = NOT_TESTED
DOJI_CLASSIFICATION_GATE = PASS
TRADING_GATE = BLOCK
WAIT_CODE_PATCH_AUTHORIZED = NO
DEPLOY_AUTHORIZED = NO
RESTART_AUTHORIZED = NO
LIVE_ORDER_AUTHORIZED = NO
```

Doji PASS is existing specification/static-helper evidence only. WAIT remains untouched and unverified; readiness BLOCK does not mean that the running service was stopped.

### [2026-11-02] - Existing and new positions hold until a profitable confirmed pivot

- The Owner selected `profit_pivot`, `existing_and_new`, and `keep_disaster`. This supersedes the preceding new-fill-only loss policy and the ordinary KC-midline-reversal exit. Ordinary pullbacks, a plain KC reversal, MA noise, legacy profit-lock labels, and per-position numeric price/ATR loss limits cannot authorize a close. Entry qualifications, sizing, leverage, account state, and completed trade history are unchanged.
- Ordinary LONG exits require a strict three-completed-bar high pivot, a bearish confirmation closing below the pivot body midpoint and MA5; SHORT mirrors with a strict low pivot and bullish confirmation above both. The entire pattern must be held after entry. Profit qualification is peak favorable displacement at least 0.5 ATR or peak ROE at least 5 percent. Confirmation-bar closed ATR is the implementation reference; ROE uses fractions, so 0.05 means 5 percent. This is retrospective confirmation, not a promise of an absolute top/bottom or profitable market fill.
- Scan, real-time quote, and account-poll paths share `profitable_true_pivot_exit`; paper and exchange full/partial close boundaries enforce the same authority. Legacy trend holding and profitability flags cannot swallow a verified full-close pivot. Verified manual and independent emergency exits remain available, including waterfalls, opposite KC breach, two closed adverse abnormal candles, and the separately implemented Live Mainnet account circuit breaker. These emergency exceptions may close before a profitable pivot; they are not ordinary numeric stops.
- A versioned, position-identity-matched pending intent is persisted before order submission and survives rejection/restart without requiring a current frame. The dedicated key is isolated from legacy KC-pivot cleanup. Corrupt evidence is rejected with a log; failed strict exchange-state persistence prevents submission. New evidence requires explicit candle finality, valid OHLC/ATR, contiguous bars, and a fresh snapshot; unobserved historical peaks before entry do not qualify.
- After the Owner asked why same-bar losses continued, read-only runtime evidence showed the old process still started at 20:44:07 Taipei. Lobster opened at 21:14:00 and closed at 21:14:47 with `CIRCUIT_BREAKER_HARD_SL`, net -3.6421 USDT. Later PEPE closes at 22:30:38 (waterfall, -2.195 USDT) and 22:38:01 (old KC reversal, -2.3988 USDT) were different authorities, not additional proof of numeric hard stops.
- The Owner then explicitly authorized: "完成最後核對後部署重啟，保留已授權災難風控". This grants deployment/restart for this exit-policy patch only, not account reset, manual entry, GitHub publication, or WAIT activation. Startup now logs the loaded position-exit policy and numeric-stop status; the status description reflects this policy.
- Validation in the isolated source copy: **1032 passed, 2 skipped**, four existing FastAPI lifecycle deprecation warnings, across 27 modules. The skips remain the removed-early-profit-guard cases. Coverage includes three-symbol LONG/SHORT scan, quote and poll behavior; exact profit thresholds; invalid/stale data; corrupt tickets; rejection/restart retries; failed-persistence no-submit; legacy and new numeric-stop rejection; manual overrides; and preserved emergency/circuit-breaker behavior. Historical required files `tests/test_channel_swing.py`, `tests/test_channel_position_path.py`, and `tests/test_channel_swing_execution.py` do not exist on this checkout: their attempted selection ran no tests and is not reported as passing. This is not a full-repository test claim.

Mandatory WAIT review (unrelated to this exit-policy deployment):

```text
WAIT_LONG_GATE = NOT_TESTED
WAIT_SHORT_GATE = NOT_TESTED
LOBSTER_WAIT_GATE = NOT_TESTED
CAP_WAIT_GATE = NOT_TESTED
LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_ATR_GATE = NOT_TESTED
WAIT_BRIDGE_GATE = NOT_TESTED
WAIT_DEDUPE_GATE = NOT_TESTED
WAIT_CONSUMPTION_GATE = NOT_TESTED
WAIT_PERSISTENCE_GATE = NOT_TESTED
WAIT_RESTART_GATE = NOT_TESTED
WAIT_ORDER_SAFETY_GATE = NOT_TESTED
WAIT_ARBITRATION_GATE = NOT_TESTED
DOJI_CLASSIFICATION_GATE = PASS
TRADING_GATE = BLOCK
WAIT_CODE_PATCH_AUTHORIZED = NO
WAIT_DEPLOY_AUTHORIZED = NO
WAIT_RESTART_AUTHORIZED = NO
WAIT_LIVE_ORDER_AUTHORIZED = NO
```

Doji PASS remains specification/static-helper evidence only. WAIT is untouched and not production-verified; its readiness BLOCK is not a statement that this existing service has been stopped.

Deployment verification: the final 27-module selection again reported **1032 passed, 2 skipped, 4 warnings** after adding the startup policy marker. All 11 changed production files were byte-identical to the tested isolated snapshot and compiled successfully. The authorized `binance-8006.service` restart completed at **2026-11-02 22:59:31 Taipei** (14:59:31 UTC); launcher PID 438868, Uvicorn PID 438878. At 22:59:33 the new process logged `POSITION_EXIT_POLICY policy=PROFIT_CONFIRMED_TRUE_PIVOT_ONLY_V1 scope=EXISTING_AND_NEW price_atr_stops=DISABLED ordinary_kc_reversal=DISABLED disaster_exits=ENABLED`, followed by application startup completion. The homepage returned HTTP 200. No account reset, manual order, commit, or GitHub push was performed.

### [2026-11-02] - Independent outside-KC confirmed pivot entry

- The Owner requested outside-KC peak/valley reversal entries and confirmed the independent-direction choice: an upper-band-external peak may open SHORT without waiting for KC to turn down; a lower-band-external valley may open LONG without waiting for KC to turn up. Other entries retain their existing KC-direction requirements. The Owner selected three completed bars with a strict central extreme and a directional confirmation closing beyond the pivot body midpoint and MA5.
- Added the separate `KC_OUTER_PIVOT_REVERSAL` authority rather than relaxing legacy three-bar, breakout, cross, or reentry authorities. The pivot extreme must be strictly outside its own candle's KC band; a touch is insufficient. MA5 must move in the entry direction. Confirmation must remain intact at the latest quote, beyond both midpoint and MA5. All three source bars must be adjacent, completed, valid, and fresh; live third bars, gaps, stale evidence, and destroyed pivots produce no authority.
- Independent reversal evaluation precedes the normal KC-slope gate, including flat KC. Persisted actual close times prevent reusing patterns formed before/during the previous position after restart. Event identity and existing bar claims/hourly limits remain active. The shared execution manager still submits through paper/testnet account boundaries, sizing, pending-order protection, and final revalidation; no direct reverse or position deletion was added.
- Central and final math direction exceptions require successful exact-method revalidation, not a caller flag or PIVOT label. The market blacklist suppresses only KC-flat/direction qualifications for this verified authority; MA flatness, chop, volume, range, and other existing filters remain. Existing Macro Candle Structure location, wick, and extension checks remain. Testnet's repeated KC-slope check and strategy firewall are wired to the same authority; the firewall re-fetches market data and checks actual close identity and account state before submission.
- The new position inherits the already-approved exit policy: ordinary price/ATR loss stops and plain KC reversals remain disabled. The entry pivot cannot authorize an exit because all three exit-pattern candles must be fully held after entry. Once profit reaches 0.5 confirmation ATR or peak ROE 5 percent, a later completed peak closes LONG or a later completed valley closes SHORT. Manual and disaster exceptions remain enabled, so this is not an unconditional hold guarantee or a promise of exact extrema.
- Isolated final validation: **1118 passed, 2 skipped, 4 existing warnings**, across 28 modules, including **86 outside-KC tests**. Coverage includes each symbol/direction, opposite/flat KC, a forming next candle, invalid patterns, exact event revalidation, pre-close/restart signal rejection, fresh firewall/account-state rejection, full scan-to-paper execution, real exchange adapter with a fake exchange (not live orders), and entry -> hold through numeric loss -> later profitable pivot close without automatic reverse. Existing entry-direction and emergency/account safety tests remain green. The three historical required Channel Swing files remain absent as documented above.
- At this source-validation point the new entry patch has not been restarted, committed, or pushed. The preceding 22:59 deployment covered the exit-policy patch only; new operational authorization is requested separately.

Mandatory WAIT review for this entry patch:

```text
WAIT_LONG_GATE = NOT_TESTED
WAIT_SHORT_GATE = NOT_TESTED
LOBSTER_WAIT_GATE = NOT_TESTED
CAP_WAIT_GATE = NOT_TESTED
LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_ATR_GATE = NOT_TESTED
WAIT_BRIDGE_GATE = NOT_TESTED
WAIT_DEDUPE_GATE = NOT_TESTED
WAIT_CONSUMPTION_GATE = NOT_TESTED
WAIT_PERSISTENCE_GATE = NOT_TESTED
WAIT_RESTART_GATE = NOT_TESTED
WAIT_ORDER_SAFETY_GATE = NOT_TESTED
WAIT_ARBITRATION_GATE = NOT_TESTED
DOJI_CLASSIFICATION_GATE = PASS
TRADING_GATE = BLOCK
WAIT_CODE_PATCH_AUTHORIZED = NO
WAIT_DEPLOY_AUTHORIZED = NO
WAIT_RESTART_AUTHORIZED = NO
WAIT_LIVE_ORDER_AUTHORIZED = NO
```

Doji PASS remains specification/static evidence only, not runtime proof. WAIT has not been changed or tested; its BLOCK is separate from the running service's operating state.

The Owner subsequently selected "現在部署重啟" for this independent reversal patch. After source/snapshot identity, syntax, and whitespace checks, the existing service restarted at **2026-11-02 23:36:09 Taipei** (15:36:09 UTC), launcher PID 566657 and Uvicorn PID 566666. The new process logged both the all-position profitable-pivot exit policy with numeric stops disabled and `ENTRY_REVERSAL_POLICY authority=KC_OUTER_PIVOT_REVERSAL confirmation=THREE_CLOSED_BARS kc_direction=INDEPENDENT account_safety=REQUIRED`, then completed startup. The homepage returned HTTP 200. This is loaded-code/readiness evidence, not proof of a naturally occurring live reversal fill. No account reset, manual order, commit, GitHub push, or WAIT activation was performed.

### [2026-11-02] - Closed breakout wiring and current positive-net pivot exits

- Read-only evidence for the latest persisted LOBSTER close: OPEN LONG at 23:22:01 Taipei, CLOSE LONG at 23:31:00, reason `EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT`, net PnL -0.441 USDT. This is not a same-candle numerical hard stop and predates the 23:36 restart. The journal confirms the active process still has all-position price/ATR stops disabled. Historical favorable movement exceeding 0.5 ATR previously authorized this pivot even when current net profit was negative.
- The Owner explicitly selected a new additional ordinary-exit condition: current estimated net profit after fees and slippage must be strictly positive; otherwise hold. Disaster and manual exceptions remain. All active producers (scan, realtime quote, account polling), final authorization, and saved-pivot retries now recheck this condition. The shared cost calculator is reused at the estimated slipped fill with closing fees charged on that fill; funding remains included. Historical profit and a persisted pending ticket cannot bypass the current-net check. Actual fills can still lose money after a gap or unexpected slippage.
- PEPE at 23:35:01 generated a valid `KC_TWO_BAR_BREAKOUT` SHORT candidate from completed 23:33/23:34 bars, but CentralEntryGate rejected the next forming 23:35 candle as `VETO_INTRABAR_NOT_CLOSED`. Central now precisely revalidates the method on authoritative completed candles and current quote before selecting the completed frame for its existing checks. This does not allow an unclosed K2, a quote back inside KC, a forged event, or weaker KC direction qualification.
- Full exchange-adapter testing exposed another wiring defect: valid breakouts were rejected by the legacy `WILD_SWING_SOURCE_INVALID` whitelist. The existing fresh-method firewall now handles `KC_TWO_BAR_BREAKOUT` as well as independent outer pivots, with provider refresh, exact pattern/event/quote revalidation, position/closing/pending checks, and actual-last-close protection. The paper boundary stamps its actual submitted quote, matching the testnet boundary. No raw label bypass, manual order, signal-lock removal, position reset, or WAIT change was introduced.
- Added **106 focused cases** across both directions and all three supported symbols: paper and fake-exchange adapter success with a forming third candle, unclosed K2/retreated-quote rejection, fresh firewall/account-state rejection, losing and fee-only pivots blocked on all four exit surfaces, saved-ticket rechecks, positive-net recovery, zero-net strictness, and fees on the slipped fill. Positive-net short-close fixtures in existing tests were corrected to actually be profitable; no production rule was weakened to satisfy old assertions.
- Final selected validation: **1006 passed, 2 skipped, 4 existing warnings**, 23 modules, using an isolated copy without `.env` or production data. An additional six legacy-policy modules have **96 failed, 61 passed, 1 skipped**. All 96 failure identities were reproduced with the new central/net behavior withdrawn in the isolated copy and remain identical after the final patch. These pre-existing policy conflicts were not rewritten; this is not a full-suite pass. The historical three required Channel Swing files remain absent.
- This source-validation stage has not restarted the service or committed/pushed changes. New deployment authorization is requested separately.

Mandatory WAIT review (unchanged, unrelated to this repair):

```text
WAIT_LONG_GATE = NOT_TESTED
WAIT_SHORT_GATE = NOT_TESTED
LOBSTER_WAIT_GATE = NOT_TESTED
CAP_WAIT_GATE = NOT_TESTED
LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_ATR_GATE = NOT_TESTED
WAIT_BRIDGE_GATE = NOT_TESTED
WAIT_DEDUPE_GATE = NOT_TESTED
WAIT_CONSUMPTION_GATE = NOT_TESTED
WAIT_PERSISTENCE_GATE = NOT_TESTED
WAIT_RESTART_GATE = NOT_TESTED
WAIT_ORDER_SAFETY_GATE = NOT_TESTED
WAIT_ARBITRATION_GATE = NOT_TESTED
DOJI_CLASSIFICATION_GATE = PASS
TRADING_GATE = BLOCK
WAIT_CODE_PATCH_AUTHORIZED = NO
WAIT_DEPLOY_AUTHORIZED = NO
WAIT_RESTART_AUTHORIZED = NO
WAIT_LIVE_ORDER_AUTHORIZED = NO
```

Doji PASS is specification/static evidence only. WAIT readiness BLOCK is not an assertion that the existing service has stopped.

The Owner subsequently explicitly requested deployment, restart, commit, and GitHub push for these completed repairs. The release includes the previously loaded profitable-pivot/outer-KC changes and the newly tested closed-breakout/positive-current-net changes. Account databases, runtime telemetry, and unrelated dirty changes are excluded. This operational approval does not authorize WAIT implementation or activation. Commit and remote verification precede the service restart; loaded-policy and HTTP readiness evidence will be checked against the new process.

### [2026-11-03] - Ordinary entry-candle hold with verified disaster exception

- The Owner first selected a total strategy exit block during the entry candle, then explicitly superseded that selection: verified disasters may close during the entry candle; all other exits must wait for a true peak/valley. The final patch implements the later instruction, not the intermediate total-disaster block.
- At the initial read-only check, the 46 persisted paper trades contained no same-UTC-minute open/close pairs. The latest LOBSTER example was 05:51:01 LONG open -> 06:03:01 confirmed true peak close. Nonpivot examples included PEPE 05:44:14 extreme waterfall and 04:47:01 two closed adverse abnormal candles. These remain separately authorized disaster exits; they are not numerical position hard stops. The Owner's cited PEPE 05:11/06:26 examples were not matched to exact fills in that snapshot; no candle-marker interpretation was presented as verified execution history.
- Added a shared ordinary-entry-candle hold based on authoritative persisted `open_timestamp` and current UTC minute, supporting seconds and milliseconds. Signal times, stale snapshot candle identities, and metadata cannot override the actual position fill time. Missing, invalid, or future fill times explicitly log and fail closed for ordinary exits. Manual closes remain independent.
- Both full/partial paper and testnet final boundaries enforce the hold, including strategy callers using `is_manual=True` with nonmanual reasons. The central scan gate preserves existing hold diagnostics and suppresses ordinary exits on the entry candle. Verified emergency math still runs before this ordinary block, so disaster labels alone never bypass authority. Numeric price/ATR stops remain disabled. Ordinary exits after the entry candle still require the existing fully post-entry closed three-bar pivot, prior profit expansion, and current positive estimated net profit.
- Added **97 tests** covering three symbols/both sides, seconds/milliseconds, actual-versus-signal identity, restart persistence, invalid/future times, the exact next UTC-minute boundary, paper/testnet full and partial zero-submission holds, fake emergency labels, and real account-adapter waterfall closure on the entry candle. Final isolated validation: **1103 passed, 2 skipped, 4 existing warnings**, 24 modules. No production data or `.env` was copied into tests. The previously documented unrelated legacy-policy failures and absent historical Channel Swing test files remain outside this selected passing suite.
- No entry logic, account capital, leverage, WAIT implementation, position reset, or manual order changed. This patch has not been deployed/restarted or committed/pushed at this validation stage; operational authorization is separate.

Mandatory WAIT review:

```text
WAIT_LONG_GATE = NOT_TESTED
WAIT_SHORT_GATE = NOT_TESTED
LOBSTER_WAIT_GATE = NOT_TESTED
CAP_WAIT_GATE = NOT_TESTED
LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_ATR_GATE = NOT_TESTED
WAIT_BRIDGE_GATE = NOT_TESTED
WAIT_DEDUPE_GATE = NOT_TESTED
WAIT_CONSUMPTION_GATE = NOT_TESTED
WAIT_PERSISTENCE_GATE = NOT_TESTED
WAIT_RESTART_GATE = NOT_TESTED
WAIT_ORDER_SAFETY_GATE = NOT_TESTED
WAIT_ARBITRATION_GATE = NOT_TESTED
DOJI_CLASSIFICATION_GATE = PASS
TRADING_GATE = BLOCK
WAIT_CODE_PATCH_AUTHORIZED = NO
WAIT_DEPLOY_AUTHORIZED = NO
WAIT_RESTART_AUTHORIZED = NO
WAIT_LIVE_ORDER_AUTHORIZED = NO
```

Doji PASS is specification/static evidence only. WAIT readiness BLOCK is separate from the existing service's operating state.

The Owner subsequently selected deployment/restart plus commit/GitHub push for this ordinary-entry-candle hold patch. The final authorized policy keeps math-verified disaster exits available on the entry candle. Runtime/account data and unrelated dirty changes are excluded from the release; WAIT activation remains unauthorized.

### [2026-11-03] - Meme MA5 structural holding with a 0.50 ATR buffer

- The Owner confirmed the complete priority resolution: keep the profitable true pivot as a necessary ordinary-exit authority; add structural holding conditions; aligned-KC buffer protection overrides the two-middle-close exception; MA5 need only be on the reverse side of MA15 on the confirmation bar (not cross for the first time); retain positive estimated net profit; preserve all previously verified disaster exits even during the entry candle/four-bar protection period. Scope is existing and new LOBSTER, CAP, and 1000PEPE positions, including shared symbol aliases.
- Added the shared `evaluate_position_exit` qualifier in `strict_true_pivot.py` and wired it through `profitable_true_pivot_exit`, which already serves scan, realtime monitoring, account polling, final paper/testnet authorization, and persisted retries. Qualification alone grants no pivot/order authority. Authoritative account symbol is used at final boundaries, not a caller's claimed position symbol. Other symbols retain their prior qualifications. The older similarly named MasterGateFunnel method is not treated as final account authority.
- Ordinary exits require at least four fully held, completed, contiguous 1m candles after actual entry. A partially held entry candle does not count. Fresh authoritative completed OHLC, KC middle, ATR, MA5, and MA15 are required. Missing/invalid/stale data fails closed with explicit diagnostics.
- LONG holds when the last completed KC middle rises and close remains strictly above middle minus 0.50 ATR; SHORT mirrors. These emit exactly `[VETO_HOLD_LONG_050_ATR_BUFFER]` / `[VETO_HOLD_SHORT_050_ATR_BUFFER]`. This hold takes precedence even when both latest closes are beyond middle or an old confirmed pivot is pending.
- Outside that veto, the confirmation must be opposite-colored and MA5 must be strictly on the reverse side of MA15. Then either close strictly breaks the 0.50 ATR buffer / both latest closes strictly break their own middles, or favorable movement reaches at least 2.5 ATR and retreat strictly exceeds 1.5 ATR. The trailing branch is an alternative structural qualification, not independent close authority: it still requires the same MA5/MA15, opposite candle, four-bar hold, true pivot, and positive-net requirements. Boundary equality is not a strict structure break/retreat.
- ATR scales use the latest completed confirmation candle, never the live candle. Favorable extrema use only fully held completed candles plus existing position-owned observed `highest_price` / `lowest_price` when present; no pre-entry/unobserved historical quote reconstruction is added. Saved pivot retries must recheck fresh current structure, so a missing frame or restored healthy buffer cannot bypass holding. Invalid pending evidence explicitly rejects instead of creating a success-shaped retry.
- Updated pivot fixtures to include actually broken/reversed structures and corrected a full-entry fixture's stale provider to return the future close snapshot. Missing-frame retry tests now assert holding under the new authorization. Existing emergency and independent-entry semantics were not weakened.
- Isolated final validation: **1389 passed, 2 skipped, 4 existing warnings**, across 25 modules, including **286 new structural-holding cases**. Coverage includes all three symbols/two sides through paper and fake-exchange adapter full-close boundaries, pending/current signals, buffer-first behavior, MA equality, same-direction candles, four-bar boundary, invalid/unclosed data, alias scope, exact ATR thresholds, observed extrema, and trailing without true pivot rejection. This is a selected suite, not a full-repository pass; the previously documented legacy-policy failures and absent historical Channel Swing files remain.
- At this validation point no commit, push, deployment, restart, account reset, manual order, WAIT activation, or entry-rule change was performed for this new MA5/0.50 ATR patch.

Mandatory WAIT review:

```text
WAIT_LONG_GATE = NOT_TESTED
WAIT_SHORT_GATE = NOT_TESTED
LOBSTER_WAIT_GATE = NOT_TESTED
CAP_WAIT_GATE = NOT_TESTED
LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_ATR_GATE = NOT_TESTED
WAIT_BRIDGE_GATE = NOT_TESTED
WAIT_DEDUPE_GATE = NOT_TESTED
WAIT_CONSUMPTION_GATE = NOT_TESTED
WAIT_PERSISTENCE_GATE = NOT_TESTED
WAIT_RESTART_GATE = NOT_TESTED
WAIT_ORDER_SAFETY_GATE = NOT_TESTED
WAIT_ARBITRATION_GATE = NOT_TESTED
DOJI_CLASSIFICATION_GATE = PASS
TRADING_GATE = BLOCK
WAIT_CODE_PATCH_AUTHORIZED = NO
WAIT_DEPLOY_AUTHORIZED = NO
WAIT_RESTART_AUTHORIZED = NO
WAIT_LIVE_ORDER_AUTHORIZED = NO
```

Doji PASS is specification/static evidence only; WAIT readiness BLOCK does not mean the existing service was stopped.

The Owner subsequently selected deployment/restart plus commit/GitHub push for this MA5/0.50 ATR patch. Release scope excludes account/runtime files and unrelated dirty changes. Commit and remote verification precede restart; loaded-policy and HTTP checks will confirm the newly started process. No WAIT implementation or activation is authorized.
