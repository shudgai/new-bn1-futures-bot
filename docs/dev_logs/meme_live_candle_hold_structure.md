# Meme Live-Candle Entry and Structure-Hold Gate

### [2026-11-04 19:24:00 UTC+8] - Modification Phase: Block green-candle shorts and ordinary pullback exits
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/services/entry_methods.py` (`_short_live_candle_veto`, `revalidate_entry_method`); final submission checks in `core/services/entry_firewall.py`, `core/services/math_execution_gate.py`, and `core/services/execution_manager.py`; `core/gates/ultimate_short_exit_gatekeeper.py`; related Meme and exit regression tests.
- **Modification Description**: Revalidate Meme SHORT entries against a fresh, verified 1-minute live candle at final submission and reject unless the current quote remains below its open. Explicitly veto ordinary pullback/retracement exits so positions hold for authorized structure-break exits; existing account-level and emergency controls remain unchanged.
- **Trigger Reason & Requirement**: User reported a SHORT opening during a green candle and requested holding through ordinary retracements until structure breaks.
- **Verification & Test Status**: `tests/test_meme_master_funnel.py` — 748 passed; exit-gate, realtime pullback, and realtime short-gate integration tests — 86 passed. Python compilation and `git diff --check` passed. No exchange orders were submitted by tests. Deployment/restart is pending because this checkout contains additional uncommitted trading-code changes outside this patch.
