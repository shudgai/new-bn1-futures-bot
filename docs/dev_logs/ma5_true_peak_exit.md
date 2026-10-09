### [2026-10-09 10:09:12 UTC+8] - Modification Phase: MA5 true peak/trough exit
- **Author**: Codex (workspace identity: shudgai999)
- **Target Files & Lines**: `core/services/exits/peak_trailing_exit.py` (`live_ma5_reversal_exit`, `evaluate_peak_trailing`)
- **Modification Description**: Track the position's live quote-derived MA5 extreme and the latest quote. Authorize the existing full-close/retry path on the first observed adverse MA5 movement confirmed by an adverse price tick; retain snapshot freshness checks and waterfall/hard-stop priority.
- **Trigger Reason & Requirement**: User reported MA5 peak/trough closes were too late and profits were being given back, and asked for a fix.
- **Verification & Test Status**: Tests were not run in this change.

### [2026-10-09 10:17:09 UTC+8] - Modification Phase: Remove the remaining Trend Hold veto for MA5 pivots
- **Author**: Codex (workspace identity: shudgai999)
- **Target Files & Lines**: `core/services/exits/realtime_profit_exit.py` (Channel Swing Trend Hold authorization and trigger detail)
- **Modification Description**: Allow `MA5_TRUE_PEAK_REVERSAL` through the final Trend Hold gate so the first confirmed MA5 reversal can close immediately; preserve hard-stop precedence and include the MA5 trigger in close records.
- **Trigger Reason & Requirement**: User reported closes remained late and supplied a chart; source trace found that the new MA5 pivot reason was omitted from the final Trend Hold exemption list.
- **Verification & Test Status**: Source path inspected. Tests were not run in this change.

### [2026-10-09 10:24 UTC+8] - Verification Phase: Regression coverage and persistence
- **Author**: Codex (workspace identity: shudgai999)
- **Target Files & Lines**: `tests/test_ma5_turn_exit.py`; `core/services/exits/realtime_profit_exit.py` (MA5 state persistence change detection)
- **Modification Description**: Add long/short observed-extreme-to-reversal tests, Trend Hold bypass and retry assertions; save updated MA5 quote/pivot state when it changes so restarts retain the observed extreme.
- **Verification & Test Status**: `tests/test_ma5_turn_exit.py`: 28 passed. `py_compile` and staged `git diff --check` passed. Adjacent run: 34 passed, 4 failed, 1 skipped; all four failures are in pre-existing modified entry reversal tests (`tests/test_channel_peak_reversal_exit.py`), outside this MA5 change.

### [2026-10-09 10:23 UTC+8] - Deployment Phase: 8006
- **Author**: Codex (workspace identity: shudgai999)
- **Target Files & Lines**: user systemd drop-in `91-current-branch.conf`; 8006 service runtime
- **Modification Description**: Point `binance-8006.service` at the requested `fix/kc-outer-pivot-entry-exit` working tree while retaining paper-trading mode. Removed the synthetic `TEST` paper position after backing up its state file; it had timestamp `1234567890`, no matching trade, and caused exchange-symbol errors.
- **Verification & Test Status**: 8006 active; `/api/status` returned `is_running=true`, `paper_trading=true`, and monitored `龙虾/USDT`. The service process imports from the requested working tree.
