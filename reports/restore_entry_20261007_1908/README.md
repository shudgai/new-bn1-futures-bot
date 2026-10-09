# Entry restoration: 2026-10-07 19:08 Asia/Taipei

Owner selected the 19:08 update, commit `ba1e50fd40786d738e387a47ef6f39696184e9ad` (19:08:57 Taipei), rather than the earlier 18:36 revision.

Restored six entry files exactly to that commit in the actual 8006 runtime directory, `/home/shudgai999/project/wait-paper-gate-candidate`. Applied only the reviewed `restore.patch`, after checking original source hashes and stopping the paper service. No whole-repository rollback occurred. All other 103 candidate core Python files matched the runtime baseline. Exit logic, engine, account adapters, sizing, UI and existing unrelated modifications were preserved.

CAP requires two completed same-color valid breakout candles, beginning inside/touch KC, followed by live evaluation outside both KC and MA5. CAP continuation requires an actually observed original pair with fresh, uncancelled provenance; a KC return, quote interruption or restart revokes old provenance. It does not require ten candles. The ten-outside-candle rule belongs to the lobster independent price-pivot reversal authority. Common directional live MA5, MA5/MA15 alignment, six-completed-candle chop checks, 0.5 ATR outer-rail chase limit, adverse-body checks, close-bar restrictions, fill deduplication and account submission safety are restored. Later WAIT and dual immediate-cross codes do not belong to the restored accepted-code set.

Validation: 408 passed, 19 existing datetime deprecation warnings, using the following selected regression:

```text
tests/test_cap_pair_continuation.py
tests/test_entry_chop_gate.py
tests/test_ma5_outer_pivot_entry.py
tests/test_two_slot_full_margin.py
tests/test_trade_pressure_exit.py
tests/test_ma5_outer_pivot_exit.py
```

Historical entry test fixtures were recovered from ba1e50f for CAP, chop, pivot and slot tests. The production engine/exit code remained current. An initial broader selection produced 309 passes and 40 failures: legacy live-MA and true-breakout tests retained CAP alternate-authority expectations or insufficient chop history, and pivot integration used newer WAIT-based slot fixtures. Those obsolete historical expectation files were restored to the current baseline, not rewritten to weaken entry rules. The version-appropriate selection above passes. This is not a full-suite pass. The three named legacy channel regression files and the AIDAN common/Python specification files were absent from the inspected repository paths; no specification files were modified.

Deployment: user unit `binance-8006-paper.service`, restarted 2026-10-08 14:11:38 Taipei. API returned `is_running=true`, `paper_trading=true`, with no entry gate halts. Existing CAP side, quantity and entry price were preserved. The source version is `entry-gate-20261007-v48-cap-pair-and-proven-continuation`. No testnet/live exchange execution was activated. No GitHub push occurred.

Evidence: `source_verification.json`, `restore.patch`, `before/`, `paper_account_before_restart.json`, `status_after_restart.json`, `deployment.json`. Account snapshots are local recovery artifacts; do not publish them.

The actual saved 18:30–19:30 historical trades carry v41 snapshots; the 19:08 committed source is the Owner-selected restoration target, not a claim that v48 was historically deployed at that exact time.

Rollback: stop the same paper service, verify the restored source hashes, reverse-apply `restore.patch`, then start the service. Preserve current account data; do not overwrite it with the pre-restart snapshot.
