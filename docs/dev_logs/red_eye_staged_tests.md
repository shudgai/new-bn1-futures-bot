
### [2026-09-25 08:32:43 UTC+8] - Modification Phase: Red-eye contract suite
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: tests/red_eye_support.py; tests/test_red_eye_staged_contract.py; tests/RED_EYE.md
- **Modification Description**: Added eight scenario functions (20 parameterized cases), fault-injectable exchange, serializable position, A/B reference policy, and production factory guard.
- **Trigger Reason & Requirement**: User requested five fault scenarios plus three explicit R/ATR parameter tests.
- **Verification & Test Status**: Initial isolated reference run: 20 passed. No database used. Production integration and deployment safety are not established by the reference run.
- **Final Verification**: 20 passed in 0.12s; negative check confirmed production-required mode fails without an adapter.

### [2026-09-25 08:42:21 UTC+8] - Modification Phase: Production bridge and red baseline
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: my_package/red_eye_adapter.py; my_package/__init__.py; tests/test_red_eye_production_adapter.py; tests/RED_EYE.md; reports/red_eye_production/
- **Modification Description**: Added real runner/entry bridge, read-only AST-anchored inline legacy tracing, explicit missing-capability failures, and native valuation checks. No core source changes.
- **Trigger Reason & Requirement**: User requested a production adapter without reimplementing strategy logic.
- **Verification & Test Status**: Reference 20 passed; adapter fidelity 6 passed; production contract 20 failed (17 legacy path, 2 missing STOP transport, 1 entry reason mismatch). No live clients or database reseed used.
- **Scope Limitation**: Account API is a test double; actual account/CCXT transport and distributed atomicity remain unverified. Auto-review rejected production helper extraction; completed a test-only read-only-tracing alternative.

### [2026-09-25 09:02:24 UTC+8] - Modification Phase: Authorized production staged risk implementation
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: core/services/exits/staged_risk_service.py; staged_state_store.py; profit_protection_service.py; core/services/symbol_runner.py; core/services/strategies/unified_entry_strategy.py; core/paper_account.py; core/testnet_account.py; my_package/red_eye_adapter.py; tests/test_staged_risk_implementation.py; tools/run_red_eye_legacy_baseline.py; MASTER_BLUEPRINT.md
- **Modification Description**: Opt-in staged ownership; monotonic R/ATR decisions; durable reduce-only intents; explicit UNKNOWN recovery; stop replacement and fill accounting guards; test transport injection. Original tests remain byte-identical.
- **Trigger Reason & Requirement**: User explicitly authorized production changes on feature/staged-risk-implementation to satisfy the existing 20-case contract.
- **Verification & Test Status**: Production contract 20 passed; supplemental 16 passed; selected legacy account regressions 2 passed; unchanged legacy adapter baseline 6 passed. Existing startup-stop expectation 98 vs 99 reproduced on fe8b72bc. No live deployment, database reseed, commit or push.
- **Rollout Boundary**: Real exchange connector/accounting and bootstrap installation still require integration verification; flags remain off unless explicitly installed.
