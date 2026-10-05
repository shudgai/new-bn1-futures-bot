# Lobster and CAP two-slot gates

Base: 05103c3c74897c788e01daccc8e3a4e5b5ccf1da.
Branch: feature/lobster-cap-two-slots-gates.

## Scope

Default fixed symbols: 龙虾/USDT and CAP/USDT. Two slots; automatic rotation disabled. Structured entry margin is capped at half the wallet and available balance including the opening fee. Both existing sizing call sites use the same helper. Desktop and mobile show up to two chart panes. Deployment must merge the settings into the target environment; config defaults do not override existing environment values.

## Entry

Live body breakout requires the original opening price inside both KC rails (inclusive), a quote strictly outside the requested rail, and directional body >= 0.5 times previous closed ATR. It is evaluated without waiting for candle close or CK reversal. Invalid data and gaps fail closed for this entry. Existing pending-confirmation entries and their protections remain.

Continuation and profit reentry require confirmed CK direction, directional live MA5 slope, and quote strictly beyond both the side's KC rail and live MA5. Existing adverse candle, distance, MA, doji and account protections remain. Fix an undefined confirmation-edge variable that silently rejected valid continuations. Reentry readiness additionally checks this continuation contract. Same-candle post-close reversal is rejected; successful close matching and abnormal-close pullback rules remain.

## Exit

Preserve initial 1.5 ATR hard stop, waterfall and mature reversal logic, and existing account hard stops. Preserve dynamic peak drawdown limits: 0.60 ATR for peaks [0.5,1), 0.50 for [1,2), 0.40 for [2,3), 0.35 for >=3. Drawdown protection remains active if current net profit crosses zero. Remove the obsolete alternative MA-turn/parabolic branch; the single-point MA turn has no close authority.

## Deployment gate

Run tools/deployment_preflight.py with an explicit evidence JSON. Missing evidence, dirty worktree, commit mismatch, any unverified checkpoint or missing deployment authorization blocks. This tool only evaluates supplied evidence; it does not independently obtain or authenticate exchange exposure or runtime identity and never deploys. Evidence must be freshly verified by the operator for the exact commit before authorization.

The old candidate and its 124/124 last accepted report are separate. Missing Phase files are not recreated and that old result is not inherited.

## Validation

26 new regression cases passed: long/short breakout, gap, rail touch, body and invalid ATR; shared contract; continuation retreat/MA5 rejection; same-candle reversal block; half-wallet fee cap; all evidence/authorization denials; all four dynamic drawdown tiers and initial stop. Four existing waterfall/initial-stop regression cases passed. Python syntax and git whitespace checks passed. Tests use an isolated paper-only environment. No exchange requests or orders were issued. CAP futures availability, authoritative exposure, complete Phase inventory and deployment preflight remain unverified. No deployment, production restart or live-order authorization.

## Extended entry integration verification

56 focused Gate tests passed. Added long/short CAP and Lobster runner-to-engine-to-real-PaperAccount fills for breakout and continuation, 50% margin cap, persistent trade duplicate blocking, fresh account-provider retreat/invalid ATR/missing finality/changed-bar rejection, and profit reentry retreat rejection. Exchange, historical market data, account persistence, and execution-quality checks are mocked; no actual network or exchange orders. Actual WebSocket transport and real exchange fills are not verified.

Three historical integration suites: 239 cases, 97 passed and 142 failed. An untouched 05103c3 baseline under the same isolated environment produced the exact same per-case pass/fail outcomes: zero added failures and zero fixed failures. These suites include historical PEPE, wick and entry policies. Identical baseline outcomes do not prove that every failure is obsolete; do not rewrite these tests or declare the entire suite passing without individual review. Full deployment remains blocked.

Remaining continuation vetoes retained from baseline: live candle color, live/closed doji filter, adverse candle protection, MA5 slope at least 0.01 ATR, and maximum 3 ATR outer distance. They are explicit existing constraints rather than runtime environment failures. No new permission to remove them was inferred.

## Follow-up corrections

Fixed emitted KC_2BAR_CONFIRM codes missing from the order whitelist. Pending confirmation now compares against the live KC rail instead of only the prior closed rail. Continuation rejects closed-only tails, non-finite/inverted live rails and non-contiguous live timestamps.

Fixed profit-reentry candidate identity: use the actual candle timestamp for order revalidation, keep the one-time ticket token separately in persisted entry_snapshot, and deduplicate against that token. Normal matching profit closes use the current continuation gate instead of an additional obsolete UnifiedEntryStrategy gate.

The runner delegates outstanding reentry tickets to their protected path. The final account firewall independently requires a matching successful close, closed ticket, correct token, side and later candle; abnormal close additionally requires its dedicated pullback. Inside-channel quotes can persist the abnormal pullback observation without granting entry. No unmatched ticket grants exposure.

Final validation: 75 focused tests plus four existing waterfall/hard-stop tests passed (79 total). Tests cover both symbols and directions, normal matched-close reentry, abnormal pullback/reclaim through the real paper account, unmatched-ticket rejection, two-bar code whitelist/live-rail checks, and invalid continuation data. Three historical suites still produce 97 passed/142 failed, with zero added regressions relative to untouched baseline. Those historical failures remain unresolved; full suite is not PASS. Test clocks are controlled to preserve, rather than bypass, the existing 3-second candle finality requirement.

No deployment, restart, live orders or production file changes. Actual exchange and WebSocket transport remain unverified.

## Historical failure resolution (latest result)

Supersedes the previous 97-pass/142-fail status for these three suites. All original 239 parameterized cases remain collected: 13 entry consistency, 219 strict contract, 7 immutability. They now pass; no skip or xfail was added. Together with 77 focused Gate cases, 316 passed. Four existing waterfall/hard-stop cases also passed separately. This is not a full-repository test result and does not inherit or change the old candidate's 124/124 report.

Actual source correction: the public compatibility state machine IDLE branch now delegates to evaluate_entry_contract and cannot authorize its private legacy trend alternative; held positions return WAIT because this compatibility interface has no independent MA close authority. Its historical implementation remains preserved. Two new cases assert that retained positions cannot gain MA exit permission.

Test migration mapping:
- PEPE-positive-fill fixtures now use CAP, with valid MA5 indicators and mocked exchange time; the production PEPE denylist is unchanged.
- Obsolete pure_trend_v2 entry expectations now exercise the actual shared contract. Existing diagnostic matrices cover invalid ATR, invalid rails/quote, doji and same-candle close, with recovery clearing old diagnostics.
- Closed confirmation body ratio is 20%; live 0.5 ATR is separately verified using the previous closed ATR and live original open. Per-entry helper tests prevent a legal alternative continuation from masking the condition under test.
- Signal phases and dedup assertions now match the actual pipeline stage. Closed-only settlement, stale quotes, changed candle revision, finality, daily loss, slots, balance, exchange failure and obsolete-code rejection remain tested.
- A wick/body ratio >=1 or a T-shaped wick alone is not a veto under the current contract. All existing wick parameter cases remain; genuine doji formation still rejects at the signal, account and mocked exchange boundary. Low-price scale cases include MA5 in rescaling.
- Immutability keeps both 100-retry loops, both cross-symbol cases, cache overflow bounds, invalid-history restart and all six snapshot identity checks. Temporary conditions are re-evaluated; no new permanent terminal lock is invented. The valid snapshot case now requires an actual finality-verified fresh provider, and cannot succeed from cache identity alone.

Automatic review rejected two optional bulk cleanups; neither was executed. All existing immutability test functions were retained and updated individually; unused legacy state-machine tail code remains in place. Production, candidate, deployment authorization and live orders remain untouched.

## Owner-authorized legacy cleanup

Removed only the unreachable private state-machine entry/MA-exit implementation and the unreachable public-method tail that was its sole caller (86 lines). Preserved state fields, public interfaces and all tests.

Retained pure_trend_v2 because realtime_profit_exit depends on its exit adapter; added a compatibility-only module notice and docs/legacy_policy_isolation.json. Repository source search confirms the only remaining production import is in realtime_profit_exit, not the live entry pipeline. This is dependency-based isolation, not a claim that all other historical tests are obsolete. No historical test files were removed or excluded.

After cleanup, the same 316 cases passed without skips. Production and the old candidate are unchanged. No deployment, restart or live orders.

## Start readiness preparation

Added /api/account-exposure with no account refresh/update/order side effects. Cached testnet/live exposure is not marked verified; paper ledger memory is identified explicitly. Tests import the endpoint without running application lifespan. Updated the outdated API strategy explanation. Runtime telemetry remains preserved locally and is ignored as generated data for source integrity.

321 related tests passed before the preflight field rename; required_files replaces phase_files to distinguish the new scoped branch inventory from the separate incomplete historical Phase candidate. Added paper-only environment fragment and startup readiness record. Current production environment is paper, one slot, Lobster only. Public mainnet symbol status verified for CAP and Lobster; demo symbol availability differs. Disk exposure snapshot is empty but current memory is not verified. Deploy/restart/live-order authority remains NO.


## Same-candle reentry authorization

The owner authorized fresh long or short entry after a successful close in the same candle. Entry direction comes from the shared gate, not the old position. Each close fill authorizes at most one reentry, including after restart. Failed closes, remaining positions, invalid quotes, daily risk limits, abnormal-close pullback protection, and final account revalidation remain blocking. The tick and scan exit paths use an entry-only callback without exit recursion. Related regression: 338 passed. Price horizontal dashed guides are hidden; KC middle and crosshair remain visible.


## CAP peak-pullback authority repair

The owner retained immediate same-candle gate reentry. The outer realtime exit executor now honors EXIT_PEAK_PULLBACK_PRESSURE even when trend status is HOLD, WARNING, or UNKNOWN, matching the peak evaluator exemption and authorized ATR tiers. No entry thresholds or pullback tiers were changed. Six new long/short trend-veto regression cases pass; related suite total: 344 passed. Historical peak exits can realize losses under the authorized 0.5 ATR activation / 0.6 ATR drawdown tier and execution costs.


## One ATR protection activation

Owner authorized raising peak-pullback activation from 0.5 to 1.0 ATR. Existing 0.50/0.40/0.35 ATR drawdown tiers and successful-close same-candle gate reentry remain. No new below-threshold profit exit was introduced. Six boundary cases and retained tier tests validate both directions; related regression: 350 passed.
