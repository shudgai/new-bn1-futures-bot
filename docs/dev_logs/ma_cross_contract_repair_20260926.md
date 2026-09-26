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
