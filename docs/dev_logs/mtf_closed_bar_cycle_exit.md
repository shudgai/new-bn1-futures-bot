# MTF closed-bar entry and cycle-exit safeguards

## 2026-11-16

- **Scope**: MTF KC state-machine entry finality, same-minute close/re-entry protection, cycle-exit confirmation, and emergency-exit authorization.
- **Changes**: Require a directionally aligned completed 1m candle and matching live-candle direction at final MTF entry validation. Prevent same-1m-bar re-entry after a close. Persist ordinary MTF exits until a completed adverse 1m candle confirms the close; keep account-level hard stops, market circuit breakers, and verified waterfall exits available. Block unrelated mid-cycle strategy exits.
- **Bug fix**: Preserve raw candle and position timestamps in pending-exit state, then normalize them consistently when comparing. This prevents synthetic millisecond timestamps from being interpreted as seconds again and re-arming the exit instead of recognizing the confirming bar.
- **Focused verification**: `pytest -q tests/test_mtf_kc_state_machine.py tests/test_math_execution_gate.py` — 54 passed. Python compilation and `git diff --check` passed.
- **Broader verification**: An additional entry/firewall and KC breakout selection completed with 213 passed and 30 failed. Failures are in `tests/test_strict_entry_contract.py` and `tests/test_kc_bb_squeeze_breakout.py`; these assertions were not changed and are not represented as passing.
- **Runtime verification**: Restarted the user-level `binance-8006.service`. `/api/status` reports the service running in paper mode for 龙虾/USDT and 1000PEPE/USDT; the existing 龙虾 SHORT and 1000PEPE LONG paper positions remained present. No live orders were submitted.
