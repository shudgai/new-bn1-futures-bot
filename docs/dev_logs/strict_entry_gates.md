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

The current live breakout threshold is 0.35 ATR, body solidity is at least 60% of the full candle range, and maximum rail distance is 3.5 ATR. Trend continuation may qualify inside KC when MA5 is correctly ordered against MA15, MA15 is strictly sloped in the entry direction, and the live directional body occupies at least 50% of its range. Shared account, order, slot, capital, daily-risk, quote-freshness, execution-quality, and fresh-snapshot checks remain in force.

`AUTHORIZED_BY_PEAK_FLIP_SHORT` is produced only from a verified successful `CLOSE_LONG` receipt for the same symbol, on that close's 1-minute candle or the immediately following candle. Its live bearish body must be at least 0.35 ATR and 60% of the candle range, and price must be below live-adjusted MA5 or the prior completed candle midpoint. The close receipt and candle identity form the stable signal ID; `override_cooldown` only exempts the existing market-crash cooldown, not account or order-safety checks.

For Channel Swing exits, a short upper wick of at least 0.4 ATR suppresses wick/structure reversal exits for that quote. A short lower-wick rejection requires at least 0.5 ATR and more than twice the real body, with meaningful open profit. A confirmed valley reversal additionally requires a strong bullish body (at least 0.35 ATR and 60% of range) above the prior midpoint and MA5/KC middle. Long upper-wick rejection uses the symmetric 0.5 ATR and 2x-body test. The production real-time exit path invokes the peak/valley evaluator; hard stops and the existing net-profit protection remain independent.

## Pipeline execution quote freshness

On 2026-10-11, the candle-range/ATR, candle-move, strong-body, and rapid-drop-cooldown veto was removed from `_abnormal_market_entry_allowed`. A pipeline-authorized breakout or continuation is not rejected merely for large volatility or a large directional candle. The remaining check requires a finite positive execution quote and a per-symbol observed quote timestamp no older than 30 seconds; stale quotes are logged as `BLOCKED_STALE_QUOTE`, and a missing timestamp as `BLOCKED_QUOTE_TIMESTAMP_UNAVAILABLE`. Insufficient margin remains handled by the existing margin gate, and execution order-book/API validation remains independent.

The prior `BLOCKED_ABNORMAL_MARKET_ENTRY` log stored no range, ATR, or move measurements. Therefore, the exact metric that rejected any historical 09:00 entry cannot be reconstructed from that log; the previous code could reject on configured candle range/ATR, range percentage, adverse move percentage, or strong-body ATR conditions. The old guard no longer emits that reason.

Verification: `tests/test_smart_pipeline.py` includes a 3.5-ATR live bearish breakout through pipeline authorization and engine account submission; the quote-freshness guard also covers stale timestamps and invalid prices.

## Pipeline authorization TTL grace

On 2026-10-11, a three-second monotonic TTL grace was added for an already-authorized pipeline decision when immediate candle/MA revalidation flickers. The grace is bound to the authorized entry type, side, stable pending-signal ID, confirmation bar, original quote, and ATR. It applies only while the same live candle remains active, the symbol quote timestamp is at most 30 seconds old, and adverse price movement is no greater than 0.8 ATR. Engine sizing, available margin, position/slot, daily-risk, execution order-book/API, and account submit-lock checks remain active.

The same evidence is independently checked by the account entry firewall immediately before exposure is opened. A missing/expired grace proof, changed signal/candle, invalid price, stale timestamp, or excessive adverse movement fails closed. Unit/integration coverage verifies grace succeeds through engine `ACCOUNT_SUBMIT` and account-firewall validation, and fails beyond the TTL, slippage, or quote-age boundaries.

## Intrabar short-exit and closed-bar confirmation

For Channel Swing SHORT positions, a forming 1m candle cannot authorize ordinary wick, color, MA, climax, or fractal exits. The holding-protection gate admits only an independently verified ratchet lock (peak ROE at least 3.5% and at least 25% giveback) or an intrabar V-reversal whose rise from the live low is at least 1.2 ATR and whose quote crosses above KC middle. Account hard stops remain independently enforced. Fractal valley and short lower-wick exits require a completed candle; valley evidence must be bullish, close above MA5, have a body of at least 0.35 ATR, and have an upper wick below 0.4 ATR. Weak green ticks, bearish closes, dojis, and long upper wicks do not close a short.

`cached_tick_indicators` now records current-candle finality, and the realtime service does not let a SHORT climax detector close from a forming candle. The 3-second pipeline authorization TTL behavior is unchanged; the integration test now advances the clock by two seconds and verifies that revalidation reaches `ACCOUNT_SUBMIT`.

### [2026-10-11 09:32:31 UTC+8] - Modification Phase: Intrabar exit gate and TTL regression
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/gates/holding_protection_gate.py` (`evaluate`, `_short_reversal_body_evidence`, `validate_exit`); `core/exits/peak_valley_exit.py` (`PeakValleyExit.evaluate`); `core/services/exits/realtime_profit_exit.py` (`cached_tick_indicators`, `enforce_realtime_profit_exit`); `tests/test_smart_pipeline.py`; this feature log.
- **Modification Description**: Separated forming-bar SHORT exits from closed-bar valley evidence, restricted live SHORT exits to verified V-reversal/ratchet conditions, preserved hard stops, and covered a two-second entry-revalidation grace through account submission.
- **Trigger Reason & Requirement**: User-requested protection against intrabar false rebounds and weak/upper-wick valley exits, with regression coverage for the existing three-second pipeline authorization grace.
- **Verification & Test Status**: Focused regression run: 53 passed, 2 deselected. `py_compile` and `git diff --check` passed. The deselected cases are the existing net-ROE tier-floor expectation and an outdated direct short-pullback helper expectation; neither was changed by this patch.
