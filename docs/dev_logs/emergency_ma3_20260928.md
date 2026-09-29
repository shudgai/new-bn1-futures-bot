# Missing regime import and strong-trend MA3 hold — 2026-09-28

The entry firewall referenced assess_market_regime, which was absent from profit_protection_service. Only trend_style existed; no circular import was found. Added a closed-candle adapter that preserves the latest completed row when calling the legacy live-tail classifier. Restored the previously authorized IGNITION/TREND_BREAKOUT exceptions for CHOPPY and the redundant 2.2 ATR distance veto.

Strong-trend MA3 exits now require an adverse completed close beyond MA15 or KC middle. A trend is recognized from an aligned pair within the preceding six completed bars: MA15 and all KC rails slope favorably, closes remain beyond MA15 and within half an ATR of the favorable outer rail or farther outside. Six bars and half an ATR are implementation defaults. The protection flag persists in position metadata and remains active through pullbacks and restarts. This lookback also recovers context for existing positions after two small pullback candles.

The new confirmation is an additional requirement for discretionary MA3 full exits, not an unconditional new exit. Touching the level or a live wick is insufficient. Shorts mirror longs. Existing initial ATR stops and breakeven checks remain independent; they can still close before a discretionary trend confirmation. No existing positions or completed trades were reset.

Validation: 82 targeted tests passed, including fresh-process imports, actual firewall calls, small pullbacks, both confirmation levels, live-candle exclusion, restart context, hard-stop precedence in protected trends, entry E2E/concurrency, sizing and startup. Python compileall core services passed; new diff lines have no trailing whitespace. No claim is made about the entire historical suite.

Final user systemd service restart: 2026-09-28 22:56:01 UTC; active/running, NRestarts=0, API HTTP200, is_running=true, paper_trading=true. No ImportError observed after restart. Baseline-relative changes.diff, logs, hashes and status evidence are under reports/emergency_ma3_20260928/.
