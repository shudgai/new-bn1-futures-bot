# WAIT strategy testing

### [2026-10-10 UTC+8] - Modification Phase: Offline acceptance baseline
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: reports/wait_strategy_tests/test_wait_contract.py; reports/wait_strategy_tests/results.xml; reports/wait_strategy_verification.md.
- **Modification Description**: Added runnable offline acceptance tests against the current real entry contract, without changing strategy implementation. Recorded explicit failing cases instead of treating missing implementation as an expected pass.
- **Trigger Reason & Requirement**: Owner requested testing before WAIT implementation/activation.
- **Verification & Test Status**: 29 tests executed, 13 passed and 16 failed. Both 龙虾 and CAP, LONG/SHORT, zero/one SMALL bridge, and below/exact/above 0.50 ATR were individually tested. Five basic shared doji-classifier boundary checks passed. All sixteen required positive WAIT candidates were absent. Order handling and restart safety remain untested; detailed gates are recorded in the verification report. No order submitted, production state read/written or service restarted by this harness. Database reseed is not applicable.
