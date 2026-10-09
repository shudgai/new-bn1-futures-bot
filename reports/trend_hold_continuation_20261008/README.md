# Trend holding, doji reversal and post-close continuation — 2026-10-08

Author: shudgai999 / Codex. Runtime worktree: wait-paper-gate-candidate. Base: 05f907b.

## Authorized sequence

Deploy and restart the previously committed 05f907b first, preserving paper account state. Incomplete pre-existing entry experiments were fully backed up to main workspace reports/deploy_05f907b_20261008/pending and removed from the deployed checkout. The service was active with paper_trading=true, is_running=true and fresh CAP/dragon quotes before this second repair.

## Holding and exits

A favorable live body cannot be closed by an earlier local completed pivot. While completed KC and quote-derived MA5 continue in holding direction and the quote stays beyond MA5, ordinary opposite pressure and the half-body live pivot continue to hold. A full adverse engulfing body through the prior opening remains a strong peak/trough reversal and may exit immediately. Confirmed structure-break exits remain independent. No ATR drawdown ladder or hard stop was added.

A post-entry mature run comprising two completed favorable bodies followed by a completed doji can exit on the next adverse live candle, for ordinary two-bar and live entries alike. A doji is at most ten percent of its candle range, using the existing convention. Fresh entry, absent progression, favorable next candle and invalid profit data cannot authorize this path.

Holding policy v13 revalidates old local-price/pressure retries under the corrected hold rules. Existing proven completed-structure and doji reversal retries migrate and remain persistent. Newly triggered valid exits retain the existing position identity and failed-close retry path.

## Continuation

KC_POST_CLOSE_CONTINUATION_LONG/SHORT uses the latest successful normal strategy close with a supported original entry signal as provenance. Starting from the next candle, a favorable live body, quote outside KC and live MA5, directional MA5, existing MA alignment, nonflat KC and adverse-candle checks can reopen without waiting for an entirely new two-body breakout. Same-bar normal reopen stays blocked. Manual/unknown/unmatched closes cannot grant this authority. Any subsequent successful open consumes that close provenance.

The shared contract, fresh REST sampling and final account firewall all re-evaluate the continuation. Snapshot evidence includes source close ID and entry signal. Sizing, balances, per-candle limits and successful-fill deduplication remain unchanged.

## Evidence

224 related tests passed (zero failures), including normal half-body pullback hold, strong engulfing exit, resumed favorable candle hold, ordinary/live doji reversal, invalid and fresh-entry rejection, pending migration, CAP/dragon long/short continuation, actual paper entry/account firewall and a real OPEN/CLOSE/OPEN ledger sequence. Core tests execute offline using temporary account files, no real exchange orders. Earlier unrelated historical test failures are not claimed as passed.

Raw logs/JUnit: main workspace reports/trend_hold_continuation_20261008/. Final deployment is to the existing 8006 paper service; no switch to entry-containment-v2. Required referenced AIDAN common/Python files remain absent and no imported specification was modified.

Final deployed commit: df75f5f. 8006 paper service restarted and health recorded in deployment_health.json.
