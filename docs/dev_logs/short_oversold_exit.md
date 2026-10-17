# Extreme oversold short exits

### [2026-10-17 21:35:17 UTC+8] - Modification Phase: Add confirmed reversal trim and trough ratchet
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files**: `core/services/exits/short_oversold_exit.py`; `core/services/exits/entry_atr_protection.py`; `core/services/exits/realtime_profit_exit.py`; `core/services/exits/authorized_position_exit.py`; `core/services/strategies/pure_trend_v2.py`; paper and testnet account adapters.
- **Modification Description**: For Channel Swing shorts, arm the extreme-move tracker only after price extends 0.5 ATR beyond the live lower KC band. A confirmed closed-bar lower-wick reversal or bullish engulfing can reduce the position by 50%; after a trough is observed, a 0.4 ATR rebound can close the remainder. Persist state and exchange partial-order intent for restart/reconciliation. A bearish MA5-under-MA15 trend with a falling MA15 holds the short and suppresses these soft exits; hard stops and separately authorized profit locks remain independent.
- **Trigger Reason & Requirement**: Owner requested timely protection against extreme oversold short reversals, a trough-based ATR ratchet, and decoupling take-profit exits from reverse-entry signals, while holding shorts through a confirmed bearish MA trend.
- **Verification & Test Status**: `pytest -q tests/test_short_oversold_exit.py tests/test_channel_exit_true_pivot_poll.py tests/test_true_peak_exit_without_midline.py`: 29 passed. Supplementary legacy testnet partial-close/staged integration tests yielded 21 failures: the partial-close fixture uses disabled `DOGE/USDT`, and staged transport fixtures lack `RestExchange.create_order`. No exit-focused test failed. Python compilation and `git diff --check` passed. Service restart and health verification are pending; no live orders were submitted.

### [2026-10-17 21:39:16 UTC+8] - Deployment Phase: Restart paper-mode 8006
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files & Lines**: `binance-8006.service`; deployment commit `2ce4fdaa`; `/api/status`
- **Modification Description**: Pushed the non-vision changes to `bugfix/vision-structure-veto` and restarted the user-level 8006 service. The local vision-gate changes remain preserved in a stash and were not deployed.
- **Trigger Reason & Requirement**: User explicitly requested deployment, service restart, and commit while deferring vision analysis.
- **Verification & Test Status**: The focused short-oversold and pivot suite passed (29 tests). The adjacent emergency/confirmed-exit selection passed 34 tests and failed 4; the clean parent commit had 33 passed and 5 failed, with the same 4 failures in legacy confirmed-swing assertions, so those 4 are baseline failures. The service is active; `/api/status` returned HTTP 200 with `is_running=true`, `paper_trading=true`, and zero positions both before and after restart. No live orders were submitted. Legacy testnet partial-close/staged integration failures are documented above.

### [2026-10-17 21:41:16 UTC+8] - Verification Phase: Recheck WAIT and entry safety gates
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files & Lines**: `tests/test_wait_entry_firewall.py`; `tests/test_wait_entry_authority.py`; `tests/test_wait_kc2bar_integration.py`; `tests/test_wait_engine_entry.py`; entry-gate regression tests.
- **Modification Description**: Re-ran deployed WAIT lifecycle, KC2BAR coexistence, entry-firewall, numeric trend-gate, and manual-order safety tests with vision changes isolated.
- **Trigger Reason & Requirement**: Verify that deferring the optional vision layer did not remove the required numeric and shared safety gates.
- **Verification & Test Status**: WAIT/firewall/KC2BAR/engine selection: 57 passed. Broader gate/manual selection: 89 passed, 3 failed because legacy fixtures expected later trend/chop veto reasons but lacked the required closed 1m candle and were correctly rejected earlier with `BLOCKED_WILD_SWING_REQUIRES_CLOSED_1M_BAR`; these cases did not authorize an order. `TRADING_GATE` remains blocked for live/production trading pending those legacy assertion updates and separate live-order authorization; the deployed service remains paper mode.
