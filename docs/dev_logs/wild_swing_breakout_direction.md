# WILD_SWING Breakout Direction Gate

### [2026-10-17 07:49:44 UTC+8] - Modification Phase: Breakout Gate Integration and Countertrend Crossover Veto
- **Author**: shudgai999 + AI assistant
- **Target Files & Lines**: `core/gates/breakout_continuation_entry_gate.py`; `core/services/strategies/wild_swing_strategy.py`; `core/gates/macro_candle_structure_gate.py`; entry-gate exports; related tests
- **Modification Description**: Added closed-candle consolidation-breakout and outside-continuation candidate detection to WILD_SWING, retaining the existing Macro Gate, Qwen approval, and final account firewall. One-minute MA5/MA15 crossover candidates now require KC middle direction to agree with their side and cannot short at the upper KC rail or go long at the lower rail; the Macro Gate independently rechecks these conditions.
- **Trigger Reason & Requirement**: User requested integration of the supplied breakout/continuation logic and investigation of a SHORT opened during an upward KC move.
- **Verification & Test Status**: 98 targeted tests passed across the new gate, WILD_SWING strategy, Macro Gate, and account firewall. A broader run of four legacy hard-stop/exit test files reported 38 failed, 91 passed, and 2 skipped; several assertions expect per-position stops or exit behavior that conflicts with the separately authorized stop-policy changes and require review. Paper short position was manually closed at the user's request. Paper service was restarted after the code changes and API/symbol status verified.
