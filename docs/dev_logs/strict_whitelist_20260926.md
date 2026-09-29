# Strict entry whitelist — 2026-09-26

User authorization: replace automatic entries with A–E only; exact B/E refinement; restart service.

- B: opposite prior body <=0.6 previous ATR; current body >=1.0 current ATR; strict engulfing close, inclusive opening; strictly directional MA3.
- E: three closes outside their own rails; current body >=0.8 current ATR; strictly directional MA3; middle distance <=2.2 current ATR. Removed historic extreme, wick and prior-close dependencies.
- A requires the immediately preceding opposing candle. D requires actual prior-body rail crossing and same-color confirmation.
- Removed same-pass close-to-reverse entry and obsolete engine reverse authorization import.
- Binance create_order calls converge on _send_order. Non-reduce-only orders re-fetch candles and verify exact whitelist code, confirmation identity, freshness, color and the 1.2 ATR inside-rail veto. Paper entries use the same firewall. Missing context/provider/data fails closed, including manual_entry flags.
- The physical 1.2 ATR gate uses the latest completed candle ATR; B/E pattern thresholds use their explicitly specified ATRs.
- Existing caller candle_data.entry_candles import did not exist, and broad exception handling silently bypassed its guard; removed that guard.
- Protective algo orders and staged transport remain reduce-only. Closed history before the latest close cannot authorize reentry.

Validation: 52 passed (46 new whitelist/firewall tests plus 6 startup tests). Existing three-file regression run: 42 failed, 60 passed; raw output in reports/strict_whitelist_20260926/existing_regressions.txt. This is not a full-suite pass. Required historical channel_swing, channel_position_path and channel_swing_execution files are absent. AIDAN specification files were also absent in this checkout and the searched project tree.

Pre-existing working-tree edits were preserved. No credentials or position state were rewritten. Deployment status is recorded separately after restart.

Deployment: user-level binance-8006.service restarted successfully; API HTTP 200, is_running=true. The prior unmanaged start.sh instance was stopped. See deployment.json for service state.
