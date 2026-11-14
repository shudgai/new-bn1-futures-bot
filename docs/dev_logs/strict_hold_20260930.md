# Strict swing holding

Historical early closes were caused by the 0.8-ATR spike rule. After reviewing logs, the user explicitly removed that rule. Strategy exits now use strict KC midpoint breach, large-profit 25% drawdown, or confirmed one-minute swing breaks. Retired pending exits and fixed profit tiers are invalidated; account/initial hard stops remain. See reports/strict_hold_20260930/README.md.

## Follow-up: dual-trend hold and reversal retry (2026-11-14)

Automated ordinary exits now require both a post-entry, closed-candle KC middle-line reversal and an opposing higher-timeframe macro trend. Missing, stale, or invalid evidence blocks the ordinary exit. Existing verified emergency exits remain separate.

After a successful close with both reversals confirmed, the opposite side is evaluated through the existing entry and account-safety gates. A veto leaves the account flat and persists a retry ticket; each fresh snapshot revalidates both trends and retries while the reversal remains valid. The ticket is cleared if either trend reverts or the opposite position opens.

Validation: `tests/test_dual_trend_exit_gate.py`, `tests/test_profit_pivot_exit.py`, and the focused pipeline retry case passed (126 tests). The broader combined run reported 24 failures, 221 passes, and 1 skip; the failing legacy exit assertions expect older single-KC, pivot-only, or profit-lock authorization behavior. They were not changed to weaken the dual-trend gate.
