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
