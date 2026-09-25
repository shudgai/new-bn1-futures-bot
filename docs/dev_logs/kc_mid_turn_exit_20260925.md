# Closed KC midline reversal exit — 2026-09-25

User selected reversal of the midline itself, not price crossing the midline.

Changes are limited to the legacy profit protection service and new regression tests.
The latest two closed KC midlines determine the exit: falling for LONG, rising for
SHORT. Flat, invalid, insufficient, or live-only reversal data does not create an
exit. `kc_middle` is preferred, with `ema_20` as the column fallback.

Removed the fixed 2 ATR take profit. Retained the 1.5 ATR stop, emergency exits,
and 4U->2U / 6U->4U profit steps. Corrected the zero-floor branch so that profits
below 4U cannot arm a step exit. Invalid ATR no longer suppresses independent
midline, monetary hard-stop, or step exits. Identifiable pending fixed-ATR TP
requests are retired without resetting peaks. New exit reasons persist with
pending state for retries after quote recovery or metadata restoration.
Staged opt-in dispatch is unchanged.

Validation:

- `tests/test_kc_mid_turn_exit.py`: 24 passed.
- New tests + `tests/test_staged_testnet_integration.py`: 44 passed.
- New tests + staged implementation + testnet integration: 58 passed, 2 failed.
- Both failures reproduced by loading the HEAD version of only
  `profit_protection_service.py` in memory with the same current dependencies and
  tests. This is a module comparison, not a full historical checkout.
  - `test_legacy_entry_points_are_silent_only_under_new_flag`: expects a returned
    dictionary for an untriggered legacy position without ATR; receives None.
  - `test_production_adapter_calls_staged_dispatcher_without_legacy`: adapter AST
    anchors for old 2.5/3.0 profit branches no longer exist in the HEAD service.
- Existing test assertions and adapter tracing were not changed.
- Required `test_channel_swing.py`, `test_channel_position_path.py`, and
  `test_channel_swing_execution.py` are absent. AIDAN common/Python files specified
  by AGENTS.md are also absent; only `AIDAN/common/high_beta_risk_system.md` exists.
- No exchange orders, deployment, restart, commit, or push were performed.

## Follow-up: remove fixed 1.5 ATR stop

The user subsequently requested removal of the fixed 1.5 ATR stop as well.
Removed its calculation, trigger, and legacy position display field. Only pending
`EXIT_STOP_LOSS:` reasons identifying `1.5 ATR` are retired; other pending reasons
and profit peaks remain intact. KC slope exits, profit steps, emergency/account
hard stops, staged dispatch, and entry rules remain unchanged.

Updated regression: 27 exit tests + 20 offline testnet integration tests = 47 passed.
The previous two staged implementation failures and missing required suites remain
as documented above. The earlier 8006 restart authorization is reused for this fix.
