# Entry decision metadata

### [2026-10-10 00:20:00 UTC+8] - Modification Phase: Restore pipeline submission
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: core/engine.py `_place_structured_entry_locked`; core/services/entry_contract.py `evaluate_entry_contract`; tests/test_entry_decision_metadata.py
- **Modification Description**: Read optional exit-bar metadata safely in diagnostics. Include the actual latest completed close and confirmation timestamp in pipeline decisions consumed by execution and the account firewall. No signal qualifications or account safety gates changed.
- **Trigger Reason & Requirement**: User reported inability to open positions. User-service journal repeatedly showed `KeyError: 'exit_bar_id'` after successful fresh decision validation; close_price and pair_confirmation_bar_id were also absent from this producer.
- **Verification & Test Status**: Eight isolated LONG/SHORT cases across breakout, MA cross, continuation and reentry exercise real decision production, engine submission, fresh account firewall validation and duplicate rejection; three slot-margin tests pass. Total 11 passed. Required historical test_channel_swing.py, test_channel_position_path.py and test_channel_swing_execution.py are absent in this checkout; not reported as passing. No database reseed applies to this metadata-only change.
- **Runtime Evidence**: Applied only these deltas to active checkout, preserving existing dirty exit changes. Restarted user-level binance-8006.service. New PID 2780960 logged ENTRY_SEQUENCE, ACCOUNT_SUBMIT and FILLED for 龙虾/USDT SHORT at 2026-10-10 00:19:42 UTC+8. Status API returned is_running=true and the corresponding position. No manual order submitted.
- **WAIT Scope**: Permanent WAIT state machine implementation and activation are outside this bug fix; WAIT_DUAL_TRACK_TRIGGER is an existing diagnostic, not proof of the specified WAIT implementation. No WAIT qualification or authority changed.

```text
WAIT_LONG_GATE = NOT_TESTED
WAIT_SHORT_GATE = NOT_TESTED
LOBSTER_WAIT_GATE = NOT_TESTED
CAP_WAIT_GATE = NOT_TESTED
LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_LIVE_TRIGGER_GATE = NOT_TESTED
WAIT_ATR_GATE = NOT_TESTED
WAIT_BRIDGE_GATE = NOT_TESTED
WAIT_DEDUPE_GATE = NOT_TESTED
WAIT_CONSUMPTION_GATE = NOT_TESTED
WAIT_PERSISTENCE_GATE = NOT_TESTED
WAIT_RESTART_GATE = NOT_TESTED
WAIT_ORDER_SAFETY_GATE = NOT_TESTED
WAIT_ARBITRATION_GATE = NOT_TESTED
DOJI_CLASSIFICATION_GATE = PASS (existing specification/static trace only)
TRADING_GATE = BLOCK (permanent integrated WAIT readiness; not a runtime stop command)
```
