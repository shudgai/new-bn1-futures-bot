# Active MA entry and chandelier contract — 2026-09-26

The earlier 27 failures were from tests/test_ma_cross_atr_contract.py, not the
179-failure historical report. Updated this suite to the active policy instead
of restoring fixed 2 ATR take profit or the retired half-ATR entry body floor.
Entry fixtures now supply six closed candles and a live candle. Exit fixtures
have valid mirrored OHLC and deterministic time. Replaced tick-based fixed-TP/SL
expectations with closed one-minute trailing/KC-body exits and persistent retry.
Three-minute frames must not authorize one-minute exits. Native protective stops
remain verified; fixed TP orders must be absent. Removed a mock of the nonexistent
paper-account notify_email symbol. Paper tests establish a real closed exit
signal before exercising account retries.

Meaningful fixtures exposed actual production defects: opposite-color MA crosses
could pass the signal stage, infinite ATR could be accepted, and missing MA15
raised KeyError. Validate required closed snapshot fields and finite positive
indicators before evaluating entries; require a same-direction body for crosses,
as already required by final order checks. No new entry thresholds or exits.

Validation: 50 tests in the revised suite plus 6 entry-cleanup regressions passed
(56 total). Includes real scan and fresh snapshot dispatch, offline testnet native
stops, paper closing, pending-state persistence, monotone stops under expanding
ATR, and holding beyond the retired 2 ATR target. No skipped/xfail cases added.
Compilation and git diff --check passed. The historical full suite was not rerun
and is not claimed to pass. Required AIDAN common/Python files remain absent.

### [2026-10-12 22:00:08 UTC+8] - Modification Phase: Expanding MA5/MA15 crossover-chop lookback
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files & Lines**: `core/gates/pipeline.py`; `tests/test_ma5_ma15_cross_chop_gate.py`.
- **Modification Description**: Preserved the existing veto for at least 3 MA5/MA15 crosses within the latest 10 closed one-minute candles, and added a second veto for at least 4 crosses within the latest 20 closed candles. The 20-candle rule is evaluated only when 20 valid closed candles are available; cross diagnostics now report both windows.
- **Trigger Reason & Requirement**: User said the 10-candle range was too short, selected 20 candles / 4 crosses, and asked to retain the 10-candle / 3-cross condition.
- **Verification & Test Status**: `tests/test_ma5_ma15_cross_chop_gate.py`: 17 passed; Problems check and `git diff --check` passed. No trade core outside the existing shared entry gate was changed.

### [2026-10-12 22:49:30 UTC+8] - Modification Phase: Dynamic MA5/MA15 chop filter
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files**: `core/gates/pipeline.py`; `core/services/entry_contract.py`; `core/services/entry_firewall.py`; `tests/test_ma5_ma15_cross_chop_gate.py`; `tests/test_high_volatility_gates.py`.
- **Modification Description**: Replaced the fixed 10/20-bar cross counts and 0.08% historical minimum gap with a six-closed-bar rolling cross check, a latest-quote MA gap veto below 0.12%, and a post-cross compression veto when at least one cross occurred within those six closed bars and the current gap remains below 0.15%. Long entries now require both quote-adjusted MA5 and MA15 to rise strictly; short entries require both to fall strictly. Exact 0.12% and 0.15% boundaries are allowed using the shared floating-point tolerance convention. Removed the fast-cross gap exemption so candidate detection, contract authorization, pipeline authorization, and final account validation cannot bypass these filters. Existing KC squeeze checks remain unchanged.
- **Verification & Test Status**: Focused dynamic chop tests and affected narrow-gap regressions: 32 passed. `py_compile` and `git diff --check` passed. A broader run across five older entry test files was not clean (108 passed, 84 failed); that suite includes assertions for now-prohibited flat/opposed MA slopes and stale entry APIs, so it is not reported as passing and needs separate baseline triage. No deployment, restart, or commit performed.

### [2026-10-12 23:24:42 UTC+8] - Modification Phase: Strict live MA5 entry alignment
- **Author**: shudgai999 / Copilot SDK in VS Code
- **Target Files & Lines**: `core/services/ma5_entry_slope.py`; `core/gates/pipeline.py`; `core/services/entry_contract.py`; `tests/test_ma5_entry_slope.py`; `docs/dev_logs/strict_entry_gates.md`.
- **Modification Description**: Added a shared fail-closed check requiring quote-adjusted MA5 to slope strictly upward with price at/above it for LONG, and strictly downward with price at/below it for SHORT. Applied without fast-lane, reversal, breakout, or shadow-entry exceptions at pipeline authorization and shared contract/pre-flight validation. Existing minimum ATR slope and other safety gates remain in force.
- **Trigger Reason & Requirement**: User explicitly requested that MA5 weakening veto every LONG route and MA5 strengthening veto every SHORT route, including when price is still above/below KC middle.
- **Verification & Test Status**: `tests/test_ma5_entry_slope.py` and `tests/test_ma5_ma15_cross_chop_gate.py`: 47 passed. Compilation, Problems, and `git diff --check` passed. An expanded legacy compatibility selection had 99 passed and 5 failures in old SHORT-route / entry-space expectations outside the new MA5 helper; those failures are not claimed as passing. No deployment, restart, commit, or live order performed.
