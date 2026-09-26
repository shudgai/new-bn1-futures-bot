# Momentum signal execution verification

Added four side-matched ENTER_FIRST_BREAKOUT / ENTER_CONTINUATION codes to the shared execution and account allowlist; retained both MA_CROSS codes. Added SIGNAL_ACCEPTED execution diagnostics and corrected momentum confirmation labels. Converted NumPy candle identifiers to native scalars so confirmed fills and entry ATR metadata serialize to JSON.

Validation: 27 new offline execution tests passed. The tests exercise the real engine, fresh snapshot, final entry validation and BinanceTestnetAccount with FakeTestnetExchange. All four signal variants create market orders, positions, persisted entry snapshots and 1.5 ATR STOP_MARKET protection. Daily halt, duplicate candidate, stale snapshot and wrong side remain blocked. Existing ATR suite: 40 passed, 4 failed; the same four failures were observed before this change in the preceding diagnostic run. The three legacy channel regression files named by AGENTS.md are absent.

Protection is fixed entry ATR (1.5 ATR stop, 2 ATR target), not a newly implemented trailing ATR policy. No discretionary manual order was sent. Service restarted 2026-09-25 22:04:21 UTC. See deploy_raw.log for runtime evidence, allowlist.diff for this task only, and git_diff_full.patch for the full requested git diff including earlier workspace changes.

Runtime result: at 22:05:00–22:05:01 UTC, ENTER_FIRST_BREAKOUT_LONG (1000PEPE/USDT) and ENTER_FIRST_BREAKOUT_SHORT (龙虾/USDT) logged SIGNAL_ACCEPTED then OPENED. Both fills and ATR levels persisted in paper_account.json; no exchange order IDs. This deployment is paper trading, not a verified Binance fill. Continuation signals verified offline only. Account mode was not changed.
