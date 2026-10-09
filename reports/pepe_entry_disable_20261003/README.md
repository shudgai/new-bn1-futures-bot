# 1000PEPE entry disable — review only

Production startup: start.sh sources .env; core/config.py uses load_dotenv and constructs ENTRY_DISABLED_SYMBOLS and DEFAULT_SYMBOLS. Before this task, .env already contained ENTRY_DISABLED_SYMBOLS="1000PEPE/USDT" at line 78 and DEFAULT_SYMBOLS="龙虾/USDT" at line 398. PAPER_TRADING=true, SYMBOL_ROTATION_ENABLED=false. This task did not edit .env. Existing configuration alone did not cover direct/manual account entry or pre-existing entry orders.

Five production files changed: core/config.py, core/engine.py, core/services/entry_firewall.py, core/paper_account.py, core/testnet_account.py. New isolated tests: tests/test_pepe_disable_production_bound.py.

The entry-only denylist always contains PEPE even if the environment omits it. Its matcher handles 1000PEPE/USDT, 1000PEPE/USDT:USDT, 1000PEPEUSDT and case variants. The structured engine submission returns before open_position. The shared account firewall blocks before all manual/cache/context exceptions and also covers the testnet _send_order and exchange create_order wrapper. Testnet limit entry rejects before market/leverage preparation. Both pending-entry loops cancel disabled entry orders instead of letting them add exposure. They use the existing cancel/reconciliation path, preserving fills that already occurred and tracking failed cancellation for retry. No cancellation was executed against a live account during this task.

No symbol filter was added to holdings, account updates, quote exits, SL, hard-stop or profit protection loops. The existing _entry_scan_symbol_snapshot still merges positions into the monitored universe; reducing orders and native reduceOnly stop orders remain allowed. Existing CHANNEL_SWING testnet update_positions delegates valuation to refresh and intentionally skips ticker exits; the quote/scan exit adapter remains active. This pre-existing division of responsibilities was preserved.

The Canonical module exists in this workspace, but current production engine entry is wired through evaluate_entry_contract/evaluate_kc_pending_entry. Tests first prove a real Canonical breakout and a valid production-contract decision on the same frame, then execute the actual engine submission/scan path with a mocked open_position and assert zero calls. They also force a stale engine universe containing PEPE so that the new gate, rather than the already-changed .env alone, is exercised. No Canonical or shared profit/exit logic was edited.

Validation: 59 production-bound disable tests passed; combined with existing doji-pressure and profit-floor tests, 122 passed. Real account/exchange classes and methods are used, with account state loading/saving and external exchange operations isolated. Production fills are never sent. Coverage includes both directions, valid Canonical signals, pyramid, post-exit and continuation reentry, manual/cache/DCA bypass attempts, symbol aliases, direct order boundaries, reducing orders, existing-position valuation/SL/hard-stop/scan exits, pending cancellation/partial fills/cancel failure, and empty denylist environment override.

Lobster before/after parity: the original five files were saved before editing, loaded into an isolated Python process in memory, and the same actual engine-to-paper-account LONG/SHORT pipeline tests passed before (2) and after (2). Shared code and parameters were not relaxed to obtain these outcomes. Full suite was not run. Python compilation and git diff --check passed.

Evidence: production.diff is the exact five-file production patch; tests.diff contains the new test file; exact.diff combines them. git_status_short.txt and git_diff.txt contain the full requested repository commands, including unrelated pre-existing scratch deletions. git_status_before.txt records the pre-task state. Existing untracked tests/test_pepe_entry_disabled.py was not edited or used as proof. Historical trades, logs, replay and forensic files were not deleted by this task.

PEPE_NEW_ENTRY_DISABLED = YES (working-tree code; not deployed)
PEPE_PYRAMID_DISABLED = YES (working-tree code; not deployed)
PEPE_REENTRY_DISABLED = YES (including continuation; not deployed)
PEPE_EXISTING_POSITION_STILL_MANAGED = YES (isolated production-path proof)
LOBSTER_BEHAVIOR_CHANGED = NO
TEST_RESULTS = 122 passed; Lobster before/after 2/2 passed
SAFE_TO_RESTART = NO

No commit, restart, deployment, force-close, account-state mutation or live order action was performed. Review is required; running-process configuration is not inferred from files changed on disk.
