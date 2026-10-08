# Entry containment regression repair — 2026-10-08

- Author: shudgai999 / Codex. Shell and Git identity verified.
- Branch: entry-containment-v2; repair base: f3365aaf902b49d4d1ff56f85821e5e764ef4d59.
- Request: repair failed red-eye checks on the preserved historical branch.

## Production changes

Define the missing short-resistance diagnostic variable without changing entry decisions. Dispatch explicitly opted-in staged positions to the existing durable staged runtime before legacy exits; retain exclusive ownership even when the runtime is missing. Ordinary Channel Swing positions retain their existing behavior.

## Test contract maintenance

The production adapter now recognizes retired inline legacy hooks, captures native runtime valuation and invokes the existing staged entry guard. The 20 original staged contract cases and reference fixtures remain unchanged. Provenance tests require the real runner and prohibit reference decisions.

Concurrency and account-boundary fixtures now use supported CAP/dragon contracts, fresh finality sampling and the real paper account. Tests assert rejection of retired modes instead of requiring removed MA5, maker and pullback routes to trade. No retired production route was restored. REST fixtures implement the account constructor interface and fail if the old create_order path is used; staged reduction tests use the existing runtime request_reduce API and actual account ledger projection.

## Validation

- Original 12 target modules plus one short-diagnostic regression: 323 passed, zero failed.
- Staged implementation and offline REST integration: 36 passed, zero failed.
- Core contract execution: RED_EYE_REQUIRE_PRODUCTION=1; RED_EYE_FACTORY=my_package.red_eye_adapter:create_strategy.
- Explicit symbol environment: DEFAULT_SYMBOLS=龙虾/USDT,CAP/USDT and SYMBOLS=龙虾/USDT,CAP/USDT.
- No skip/xfail added. No full repository suite claim. Existing datetime deprecation warnings remain.
- Raw logs and JUnit: main workspace reports/entry_containment_red_eye_20261008/.
- No database reseed, real exchange requests, production switch or restart. The 8006 service remains active at detached commit 2684305.

The mandatory referenced AIDAN common/Python files are absent from this historical checkout; they were not claimed as read or modified.
