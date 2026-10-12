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
