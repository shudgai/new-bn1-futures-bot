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
