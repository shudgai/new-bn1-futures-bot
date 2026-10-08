### [2026-10-09 07:15:00 UTC+8] - Modification Phase: Restore ATR profit ladder
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/services/exits/atr_step_profit.py`, `core/services/exits/trend_pivot_exit.py`, `tests/test_atr_step_profit.py`
- **Modification Description**: Restored the fixed-entry-ATR profit ladder with 1 ATR steps for 龙虾/USDT and 2 ATR steps for CAP/USDT; retained half-step floors, fee/slippage break-even protection, persisted state, and close retry behavior.
- **Trigger Reason & Requirement**: User explicitly requested ATR locking changed back to 1 ATR for 龙虾 and 2 ATR for CAP.
- **Verification & Test Status**: `tests/test_atr_step_profit.py`, `tests/test_trend_pivot_owner_policy.py`, `tests/test_live_ma5_owner_exit.py`, `tests/test_short_bear_hold.py`, and `tests/test_fast_close_reentry.py` passed: 293 tests; six existing `utcfromtimestamp()` deprecation warnings.
