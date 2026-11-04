# Meme Live-Candle Entry and Structure-Hold Gate

### [2026-11-04 19:24:00 UTC+8] - Modification Phase: Block green-candle shorts and ordinary pullback exits
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/services/entry_methods.py` (`_short_live_candle_veto`, `revalidate_entry_method`); final submission checks in `core/services/entry_firewall.py`, `core/services/math_execution_gate.py`, and `core/services/execution_manager.py`; `core/gates/ultimate_short_exit_gatekeeper.py`; related Meme and exit regression tests.
- **Modification Description**: Revalidate Meme SHORT entries against a fresh, verified 1-minute live candle at final submission and reject unless the current quote remains below its open. Explicitly veto ordinary pullback/retracement exits so positions hold for authorized structure-break exits; existing account-level and emergency controls remain unchanged.
- **Trigger Reason & Requirement**: User reported a SHORT opening during a green candle and requested holding through ordinary retracements until structure breaks.
- **Verification & Test Status**: `tests/test_meme_master_funnel.py` — 748 passed; exit-gate, realtime pullback, and realtime short-gate integration tests — 86 passed. Python compilation and `git diff --check` passed. No exchange orders were submitted by tests. Deployment/restart is pending because this checkout contains additional uncommitted trading-code changes outside this patch.

### [2026-11-04 21:29:00 UTC+8] - Modification Phase: Do not treat wick-only extremes as structure breaks
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/services/exits/kc_true_extreme_exit.py`; `tests/test_kc_true_extreme_exit.py`; `tests/test_ultimate_exit_red_candle_veto.py`.
- **Modification Description**: A long upper-wick rejection may still record/arm an observed extreme, but can no longer independently authorize an exit. Structure-exit checks require the completed candle close to break the relevant KC middle and MA15 levels; a wick through both levels with the close still on the holding side is vetoed for LONG and SHORT.
- **Trigger Reason & Requirement**: User clarified that a wick/shadow alone is not structural damage.
- **Verification & Test Status**: Wick-only extreme tests — 2 passed; LONG/SHORT wick-through-structure tests — 2 passed; complete `test_ultimate_exit_red_candle_veto.py` — 84 passed; completed-structure evaluator tests excluding its realtime wiring case — 9 passed. The full `test_kc_true_extreme_exit.py` remains non-green (15 failed, 12 passed), including legacy pullback/midline expectations; those cases were not changed beyond replacing the direct upper-wick-exit expectation. Compilation and `git diff --check` passed. No service restart, deployment, commit, or exchange order was performed for this follow-up.

### [2026-11-04 21:52:00 UTC+8] - Modification Phase: Authorize only verified live waterfall exits
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/gates/ultimate_short_exit_gatekeeper.py`; `tests/test_meme_waterfall_authority.py`; `tests/test_ultimate_exit_red_candle_veto.py`.
- **Modification Description**: Permit a validated live adverse waterfall to close immediately, including during the cost shield and before five completed bars. Authorization requires a fresh, valid live 1-minute candle, a post-entry candle, an adverse body of at least 1.5 preceding-bar ATR, and the live quote beyond KC middle. Wick-only touches, stale/malformed data, and candles without the required body/structure evidence remain blocked.
- **Trigger Reason & Requirement**: User requested immediate exit when a structural break occurs with a major waterfall, without waiting for the abnormal candle to finish.
- **Verification & Test Status**: `tests/test_meme_waterfall_authority.py` and `tests/test_ultimate_exit_red_candle_veto.py` — 342 passed, including Paper/Testnet close-boundary cases for both sides and all three supported meme symbols. A broader related run had 361 passed and 3 unrelated pre-existing failures in entry direction and circuit-breaker boundary tests. No exchange order, deployment, restart, commit, or push was performed for this follow-up.

### [2026-11-04 22:43:33 UTC+8] - Modification Phase: Require confirmed structural exit strength
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/gates/ultimate_short_exit_gatekeeper.py` (`evaluate`); `tests/test_ultimate_exit_red_candle_veto.py`.
- **Modification Description**: Removed the KC-middle/MA15 special-case exit that bypassed the existing 0.50 ATR buffer and fresh MA5/MA15 cross checks. Ordinary structural exits now use the same confirmed threshold for both sides; inside the cost shield, the existing three-step KC turn confirmation remains mandatory. Verified waterfall and disaster exits are unchanged.
- **Trigger Reason & Requirement**: User reported the 2026-11-04 22:30 Lobster LONG exit as premature.
- **Verification & Test Status**: Exit-gate and structure-wiring tests — 89 passed; structural-hold, waterfall, and master-funnel regressions — 1,294 passed; Python compilation and `git diff --check` passed. No exchange order, deployment, restart, commit, or push was performed.

### [2026-11-04 22:49:56 UTC+8] - Modification Phase: Isolate Kline REST fallback by timeframe
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/engine.py` (`_fetch_wild_swing_rest_klines`); `tests/test_wild_swing_kline_stream.py`.
- **Modification Description**: Keyed REST request cooldowns and no-data log throttling by canonical symbol plus timeframe, preventing a recent request for another timeframe from suppressing a 1m refresh. Empty REST responses with no usable cache now produce a timeframe-specific warning while preserving fail-closed entry behavior.
- **Trigger Reason & Requirement**: User authorized investigation/fix of PEPE Kline availability only, without relaxing entry filters.
- **Verification & Test Status**: Kline stream tests — 26 passed; combined Kline, structural-exit, wiring, and waterfall regression tests — 373 passed; compilation and `git diff --check` passed. The historic missing-candle intervals cannot be attributed conclusively to this shared-cooldown bug; no strategy gate was relaxed. No order, deployment, restart, commit, or push was performed.
