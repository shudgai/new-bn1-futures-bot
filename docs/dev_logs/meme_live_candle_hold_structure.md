# Meme Live-Candle Entry and Structure-Hold Gate

### [2026-11-04 19:24:00 UTC+8] - Modification Phase: Block green-candle shorts and ordinary pullback exits
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/services/entry_methods.py` (`_short_live_candle_veto`, `revalidate_entry_method`); final submission checks in `core/services/entry_firewall.py`, `core/services/math_execution_gate.py`, and `core/services/execution_manager.py`; `core/gates/ultimate_short_exit_gatekeeper.py`; related Meme and exit regression tests.
- **Modification Description**: Revalidate Meme SHORT entries against a fresh, verified 1-minute live candle at final submission and reject unless the current quote remains below its open. Explicitly veto ordinary pullback/retracement exits so positions hold for authorized structure-break exits; existing account-level and emergency controls remain unchanged.
- **Trigger Reason & Requirement**: User reported a SHORT opening during a green candle and requested holding through ordinary retracements until structure breaks.
- **Verification & Test Status**: `tests/test_meme_master_funnel.py` — 748 passed; exit-gate, realtime pullback, and realtime short-gate integration tests — 86 passed. Python compilation and `git diff --check` passed. No exchange orders were submitted by tests. Deployment/restart is pending because this checkout contains additional uncommitted trading-code changes outside this patch.

### [2026-11-04 21:29:00 UTC+8] - Modification Phase: Do not treat wick-only extremes as structure breaks
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/services/exits/kc_true_extreme_exit.py`; `tests/test_kc_true_extreme_exit.py`; `tests/test_ultimate_exit_red_candle_veto.py`.
- **Modification Description**: A long upper-wick rejection may still record/arm an observed extreme, but can no longer independently authorize an exit. Structure-exit checks require the completed candle close to break the relevant KC middle and MA15 levels; a wick through both levels with the close still on the holding side is vetoed for LONG and SHORT.
- **Trigger Reason & Requirement**: User clarified that a wick/shadow alone is not structural damage.
- **Verification & Test Status**: Wick-only extreme tests — 2 passed; LONG/SHORT wick-through-structure tests — 2 passed; complete `test_ultimate_exit_red_candle_veto.py` — 84 passed; completed-structure evaluator tests excluding its realtime wiring case — 9 passed. The full `test_kc_true_extreme_exit.py` remains non-green (15 failed, 12 passed), including legacy pullback/midline expectations; those cases were not changed beyond replacing the direct upper-wick-exit expectation. Compilation and `git diff --check` passed. No service restart, deployment, commit, or exchange order was performed for this follow-up.
