# KC two-bar closed confirmation entry

## Original path and changed functions

The active scanner is process_single_symbol_runner in core/services/symbol_runner.py. It calls evaluate_entry_contract in core/services/entry_contract.py. TradingEngine._fresh_channel_entry_snapshot and _place_structured_entry_locked revalidate at execution; validate_account_entry in core/services/entry_firewall.py revalidates at account submission.

Added core/services/kc_two_bar_entry.py::evaluate_kc_two_bar_entry and registered KC_2BAR_BREAKOUT_LONG / KC_2BAR_BREAKOUT_SHORT. evaluate_entry_contract evaluates the new rule before the existing rule, after common data/account guards. _place_structured_entry_locked and validate_account_entry copy only evidence keys present for the selected rule. Existing entry codes remain valid and independently evaluated. No held-position exit, MA5 exit, stop, take-profit, leverage, sizing or risk threshold was changed or re-enabled.

## Exact rule

Use the latest two consecutive explicitly closed 1-minute candles. LONG requires both Close > Open and each Close > that candle's KC upper. The second closed MA5 must exceed its MA15 and the first closed MA5. The second closed (Close - KC upper) / ATR must be at most 0.5, inclusive with floating-point boundary tolerance. SHORT mirrors every inequality using the lower rail. ATR is the second closed candle's ATR. As specified by the explicit Close > rail conditions, no additional Open-inside-rail requirement is invented.

The new rule does not wait for a forming third candle's body, color, doji ratio or MA. It does not apply the legacy 0.10 ATR distance from third open. It returns the latest executable quote separately from the confirmed close; it never fills retroactively at the close. Closed-only frames can generate the rule, while existing order-boundary settlement verification remains mandatory. That verification includes REST checks and delays, so execution can occur seconds into the next candle. No signal evaluation uses a future candle or reconstructs tick order from historical wicks.

If the second closed distance exceeds 0.5 ATR, this rule does not authorize entry and does not cache permission for a later intrabar pullback. A later evaluation must meet the same closed-candle rule anew. Since the user explicitly retained other entries, the original entry can still qualify independently even if this new rule fails; the new distance rule is not a global veto. Explicit new-code revalidation cannot fall back to a legacy rule.

## Validation

138 tests passed across test_kc_two_bar_entry.py (38), test_doji_same_color_confirmation.py, test_doji_entry_cleanup.py, test_live_third_entry.py, test_entry_without_wick_filter.py and test_entry_latest_close_runtime.py. Coverage includes symmetric strict inequalities, exact 0.5 ATR, missing/invalid MA, no forming-second authorization, live-indicator independence, original-entry fallback, scanner/account path, fresh revalidation rejection, real engine to isolated paper fills and deduplication. All exchange/network behavior in these tests is mocked; no real test orders.

Expanded legacy comparison: test_strict_entry_contract.py and test_abnormal_only_hold.py each ran together against an isolated in-memory reconstruction of the pre-change contract and the new contract. Both runs: 125 passed, 133 failed, identical per-test outcomes (before.json / after.json). These tests include superseded policy assumptions; no assertions were weakened to make them pass. The initial exploratory selection was 221 passed, 124 failed and 12 deselected; it is superseded for baseline comparison by the full paired run. No whole-repository pass is claimed. Historical channel tests and referenced AIDAN specifications remain absent.

Python compilation and git diff --check passed. Existing unrelated uncommitted edits were retained. Complete current source files plus the new test are included in complete_code.zip; engine.py is included in full, with only two snapshot/import lines changed for this feature. Deployment evidence is saved separately.
