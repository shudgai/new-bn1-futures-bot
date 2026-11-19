# UI Net ROE staged profit protection

## Latest threshold update

The activation threshold is now 5% net ROE instead of 8%. The 5-point tier interval and 3-percentage-point peak drawdown are unchanged. The stop remains monotonic and net PnL/ROE calculation, reversal protection, persistence, and close safety are unchanged.

Verification after this threshold change: `tests/test_position_profit_protection.py` passed all 21 tests, including the 4.9%, 5%, 6%, 8%, 10%, 15%, and 20% lock-line boundaries.

## Original position protection implementation

The original implementation started the lock at 8% net ROE, used 5-point tier intervals, and continuously raised a monotonic stop to peak net ROE minus 3 percentage points. Net PnL and ROE use the chart's verified fee/slippage estimate, funding cost, and the position's initial margin.

Two consecutive adverse closed 1-minute candles may independently exit a profitable position when the second close progresses against the position and breaks MA5 or the prior short-term structure. A single adverse candle qualifies only at 0.5 ATR or larger and a confirmed structure break. Unclosed candles do not count. The trigger is latched in SQLite state keyed by a deterministic position UID; updates use a revision check and all exits still go through the existing priority close authority.

Ordinary per-position percentage, ATR, and ROE hard stops are disabled. Account-level emergencies, verified waterfall exits, liquidation/data-integrity protections, and the existing Phase 3C close path remain separate.

Verification: the final focused protection, priority-exit, hard-stop, Channel Swing, and pivot run passed 158 tests. A separate legacy close-authority module has two unrelated failures because its `DataPipeline` fixture returns `None`; that module was not changed.

## Earlier 5% staged-lock proposal

An earlier proposal used a 5% start, four-point stages, and a 1.5-point drawdown. It was superseded by the original implementation above. The latest authorization restores only the 5% start while keeping the implemented 5-point interval and 3-point drawdown.

### [2026-11-19 09:15:00 UTC+8] - Modification Phase: P1 exit authorization and normal reentry
- **Author**: AI assistant using Copilot SDK in VS Code
- **Target Files & Lines**: `core/services/exits/authorized_position_exit.py` (`_verified_priority_profit_exit`); `core/services/exits/position_profit_protection.py` (`read_persisted_position_protection`); `core/services/post_profit_lock_gate.py`; `core/paper_account.py`; `services/api.py`; `web/index.html`; focused exit, persistence, and status tests
- **Modification Description**: Permit verified, position-bound P1 net-ROE lock and profitable reversal exits through the final authorization gate; record successful positive-net exits for same-side normal-entry re-evaluation; expose persisted lock state in the status API/UI.
- **Trigger Reason & Requirement**: User reported that a recorded 14.36% peak ROE and 11.36% lock floor did not automatically close, and requested normal same-side reentry after a successful profit exit.
- **Verification & Test Status**: Focused exit/persistence/status tests: 49 passed. `py_compile` and `git diff --check` passed. Broader entry suites still have failures (53 failures across `test_post_close_continuation.py` and `test_closed_breakout_entry.py`); two healthy-pullback cases in `test_authorized_position_exit.py` fail because their snapshot fixture returns `None`. These failures remain unmodified and are not represented as passing.
