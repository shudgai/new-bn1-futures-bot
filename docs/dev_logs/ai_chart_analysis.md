# AI Chart Analysis

### [2026-10-16 14:34:48 UTC+8] - Modification Phase: Port chart AI review to the previous branch
- **Author**: shudgai999 / Copilot SDK
- **Target Files & Lines**: `core/services/symbol_runner.py:15, 80-141` (`_process_wild_swing_symbol`); `core/vision/chart_guard.py:69, 159, 324` (`DynamicVisionGatekeeper`, chart rendering, AI review); `requirements.txt:9-11`; `.env.example:339-346`; `tests/test_vision_chart_guard.py:29-196`; `tests/test_dynamic_vision_gate.py:15-119`.
- **Modification Description**: Added in-memory chart rendering and AI review before WILD_SWING orders, with local Qwen priority, Gemini fallback, configurable outage behavior, and per-symbol rejection cooldown. Removed the dependency on the newer branch's `core.swing` models so the module runs on this branch.
- **Trigger Reason & Requirement**: User requested returning to `refactor/clean-gates-and-peak-exit` and adding the AI chart-image analysis feature.
- **Verification & Test Status**: 41 targeted tests passed; compile and `git diff --check` passed; full dependency resolution dry-run passed. No commit, push, deployment, or service restart. Unfinished Swing work remains preserved in stash `83c90644`.

### [2026-10-16 14:55:00 UTC+8] - Modification Phase: Fail closed on unavailable chart review
- **Author**: shudgai999 / Copilot SDK
- **Target Files & Lines**: `.env.example`; `core/vision/chart_guard.py` (`PROMPT`, `_fail_open`, `evaluate_breakout_vision`); `core/services/symbol_runner.py` (`_process_wild_swing_symbol`); `core/services/strategies/wild_swing_strategy.py` (short wick confirmation); `tests/test_vision_chart_guard.py`; `tests/test_wild_swing_runner.py`; `tests/test_wild_swing_strategy.py`; `docs/dev_logs/ai_chart_analysis.md`.
- **Modification Description**: Added a deterministic Macro Candle VETO before AI review and order submission; short entries with long lower wicks/doji now require the next closed bearish candle to close below the wick candle's low. Made AI outage and disabled-guard outcomes reject entries by default, changed the sample environment to fail closed, tightened the AI prompt against floor shorts, and added explicit per-signal vision approval logs.
- **Trigger Reason & Requirement**: User reported an entry that should have been rejected and asked whether the chart was actually analyzed. Runtime logs showed an unavailable vision service recorded as approved via fallback.
- **Verification & Test Status**: 43 targeted strategy, vision, dynamic-gate, runner, and Macro Gate tests passed; `git diff --check` passed. Local changes only; no commit, push, deployment, or service restart.

### [2026-10-16 15:38:00 UTC+8] - Modification Phase: Enable and deploy WILD_SWING entry gates
- **Author**: shudgai999 / Copilot SDK
- **Target Files & Lines**: Existing WILD_SWING guard changes in `core/services/symbol_runner.py`, `core/services/strategies/wild_swing_strategy.py`, and `core/vision/chart_guard.py`; service `binance-8006.service`.
- **Modification Description**: Deployed the hard Macro Candle Gate, closed-bar short-wick confirmation, and fail-closed chart-review behavior by restarting the 8006 service. No strategy thresholds or gate bypasses were added.
- **Trigger Reason & Requirement**: User explicitly selected WILD_SWING gate activation and authorized deployment/restart.
- **Verification & Test Status**: 43 targeted tests passed. Service restarted successfully; `/api/status` returned `is_running=true`, `paper_trading=true`, and no open positions. Startup logs reported application startup complete. No commit or push.

### [2026-10-16 15:44:00 UTC+8] - Modification Phase: Align Macro Gate with WILD_SWING pullbacks
- **Author**: shudgai999 / Copilot SDK
- **Target Files & Lines**: `core/gates/macro_candle_structure_gate.py`; `core/vision/chart_guard.py` (`PROMPT`); `tests/test_macro_candle_structure_gate.py`; `tests/test_vision_chart_guard.py`.
- **Modification Description**: Set symmetric KC position limits to LONG <= 0.65 and SHORT >= 0.35 so mid-channel pullbacks can reach vision review and order authorization. Retained MA15-bias protection and short bearish lower-wick VETO above 40% of candle range.
- **Trigger Reason & Requirement**: User authorized allowing WILD_SWING pullback continuation candidates in the 0.35-0.65 KC-position zone while preserving the long-lower-wick rejection, and authorized deployment.
- **Verification & Test Status**: 50 targeted strategy, Macro Gate, vision, dynamic-gate, and runner tests passed. Service restarted successfully; `/api/status` returned `is_running=true`, `paper_trading=true`, and no open positions. Startup logs reported application startup complete. No commit or push.

### [2026-10-16 16:02:07 UTC+8] - Modification Phase: Coordinate WILD_SWING entry modes across gates
- **Author**: shudgai999 / Copilot SDK
- **Target Files & Lines**: `core/services/strategies/wild_swing_strategy.py`; `core/gates/macro_candle_structure_gate.py`; `core/services/symbol_runner.py`; `core/vision/chart_guard.py`; `core/engine.py`; `core/services/entry_firewall.py`; related WILD_SWING, Macro Gate, and vision tests.
- **Modification Description**: Added explicit `BREAKOUT`, `CONTINUATION`, `CROSSOVER`, and `REVERSAL` signal modes. Macro Gate applies mode-specific KC-location, channel-expansion, structure-confirmation, and wick checks; vision prompts and runner diagnostics carry the mode. The final account firewall now re-evaluates the closed-bar strategy signal, verifies its mode, reason, and candle identity, and runs the corresponding Macro Gate again against fresh closed-bar data.
- **Trigger Reason & Requirement**: User requested mode-aware coordination between WILD_SWING strategy candidates, Macro Candle Gate, vision review, and the order firewall to prevent valid modes from being assessed by one rigid rule set.
- **Verification & Test Status**: 125 focused strategy, Macro Gate, account-firewall, runner, vision, dynamic-vision, and strict-firewall tests passed. No commit, push, deployment, or service restart.

### [2026-10-16 16:15:42 UTC+8] - Modification Phase: Enforce permitted WILD_SWING entry patterns
- **Author**: shudgai999 / Copilot SDK
- **Target Files & Lines**: `core/gates/entry_pattern_permitted_gate.py`; `core/gates/macro_candle_structure_gate.py`; `core/services/strategies/wild_swing_strategy.py`; `core/services/symbol_runner.py`; `core/services/entry_firewall.py`; `core/engine.py`; related pattern, strategy, Gate, firewall, runner, and vision tests.
- **Modification Description**: Added a shared fail-closed entry-pattern allowlist for BREAKOUT, CONTINUATION, CROSSOVER, and REVERSAL (TOP_BOTTOM alias). Missing, unknown, and conflicting patterns return `REJECT_UNAUTHORIZED_ENTRY_PATTERN`. BREAKOUT requires an expanding channel and confirmed outer-band break; CROSSOVER requires confirmed 1M MA5/MA15 crossing or 5M convergence evidence; REVERSAL requires confirmed outer peak/valley, top-bottom divergence, or a directional exhaustion pinbar; CONTINUATION permits the authorized mid-channel/outer-trend locations. The account firewall repeats the pattern and Macro Gate checks using fresh closed candles. Kept `entry_mode=WILD_SWING` as the strategy-family field; `entry_pattern` carries the entry type end-to-end.
- **Trigger Reason & Requirement**: User explicitly required the Entry Pattern Permitted Gate and prohibited opening positions for any pattern outside the authorized whitelist.
- **Verification & Test Status**: 145 focused entry-pattern, WILD_SWING strategy, Macro Gate, firewall, runner, vision, dynamic-vision, and strict-firewall tests passed. Changed Python modules compiled successfully and `git diff --check` passed. No commit, push, deployment, or service restart.

### Modification Phase: Require explicit local Qwen approval before WILD_SWING orders
- **Target Files**: `core/vision/chart_guard.py`, `core/services/symbol_runner.py`, `core/services/entry_firewall.py`, `.env.example`, and focused tests.
- **Modification Description**: WILD_SWING now submits a chart image only after the mode-aware Macro Gate passes, and requires local Qwen-VL to return explicit JSON `APPROVED`. `REJECTED`, legacy `REAL`/`TRAP`, malformed responses, disabled Qwen, and service failures all fail closed; Gemini, indicator fallback, and `VISION_FAIL_OPEN` cannot authorize this entry path. The account Entry Firewall repeats the fresh Qwen review after revalidating the closed-bar signal and Macro Gate, immediately before order authorization.
- **Trigger Reason & Requirement**: Require Gate approval followed by local AI visual approval before any WILD_SWING order, with clear stage logs.
- **Verification & Test Status**: 111 focused entry-pattern, Macro Gate, WILD_SWING strategy, firewall, runner, vision, and dynamic-gate tests passed in the project virtual environment. Changed modules compile and `git diff --check` passes. No commit, push, deployment, or service restart.

### [2026-10-16 17:08:33 UTC+8] - Modification Phase: Require outer pivot and MA cross for WILD_SWING
- **Author**: shudgai999 / Copilot SDK
- **Target Files & Lines**: `core/services/strategies/wild_swing_strategy.py`; `core/gates/macro_candle_structure_gate.py`; `core/services/symbol_runner.py`; `core/services/entry_firewall.py`; related WILD_SWING, Macro Gate, runner, firewall, and vision tests.
- **Modification Description**: Initial entries now require a confirmed KC-outer three-point pivot followed by a closed-bar MA5/MA15 cross, aligned MA5 slope, and directional close. Profit-lock re-entry is rebuilt from the latest matching WILD_SWING close record and requires same-side outer-band location, aligned MA5/KC-middle direction, and no low-volume three-bar stall. Strategy exits are limited to the ratchet profit lock or a confirmed post-entry true peak/valley with lower pivot volume and a close through the intervening structure level. Macro Gate and the final account Firewall revalidate the new evidence; account-level safety exits remain unchanged.
- **Trigger Reason & Requirement**: Require a completed outer-pivot/crossover setup for initial entries, preserve strong trends after profit-lock closes only when continuation remains healthy, and avoid ordinary trend-loss exits.
- **Verification & Test Status**: 111 focused WILD_SWING strategy, Macro Gate, runner, Firewall, close execution, chart vision, and dynamic-vision tests passed. Python compilation and `git diff --check` passed. Runtime logs for 2026-10-16 show the 16:46 closed bar (bar ID `1792140360000`) passed Macro Gate as CONTINUATION but local Qwen rejected it for unclear trend/momentum; the decision was logged at 16:47. The prior 16:44 bar was evaluated at 16:45 and its LONG crossover was vetoed at `KC_Pos 0.68`. Changes are local only; no commit, push, deployment, or service restart.

### [2026-10-17 12:55:13 CST] - Modification Phase: Defer Directional-Wick Entries and Hold Through Pullbacks
- **Author**: shudgai999 / Copilot SDK
- **Target Files**: `core/services/strategies/wild_swing_strategy.py`; `core/services/strategies/defensive_trend_strategy.py`; `core/engine.py`; `core/services/entry_firewall.py`; Paper/Testnet account entry-context allowlists; focused WILD_SWING tests.
- **Modification Description**: A directional wick larger than its candle body now waits for the immediately following closed 1-minute candle to confirm in the same direction with a strict aligned MA5 slope. Its source-bar identity is carried through submission and revalidated by the account Firewall. WILD_SWING now suppresses ordinary MA-cross, MA-slope, MA15, and KC trend/band pullback exits; shared defensive-exit helpers retain their prior defaults for other callers. Profit-lock/breakeven, numeric hard stops, and the existing entry-bar blow-off exception remain active. This change does not add or verify a true peak/valley exit.
- **Trigger Reason & Requirement**: The Owner requested delaying long-shadow entries, rejecting LONG confirmation when MA5 slopes down (SHORT mirrored), and not closing positions on ordinary pullbacks.
- **Verification & Test Status**: 81 focused WILD_SWING strategy, account Firewall, runner, and shared defensive-exit tests passed, including both-side numeric hard stops and SHORT profit-lock behavior; changed Python modules compiled and `git diff --check` passed. The runner test fixture now supplies the WAIT authority interface required by the shared runner. Changes are local only; no commit, push, deployment, or service restart.

### [2026-10-17 13:05:00 CST] - Modification Phase: Veto Opposite-Color Live Breakouts and Deduplicate Same-Bar Reentry
- **Author**: shudgai999 / Copilot SDK
- **Target Files**: `core/gates/breakout_continuation_entry_gate.py`; `core/services/strategies/wild_swing_strategy.py`; related breakout, WILD_SWING, and WAIT tests.
- **Modification Description**: Intrabar breakout and outside-continuation entries now require the live quote to be directionally beyond that candle's open, so a green live candle cannot open SHORT below the lower KC rail and a red candle cannot open LONG above the upper rail, even if MA5 slope still points the other way. WILD_SWING exit decisions now bind to the forming candle ID when one is present, and exit bookkeeping never moves its processed/exit bar backward. This prevents a close during a live candle from making that same candle eligible for another entry. Added a CAP SMALL-RED setup + SMALL-GREEN bridge + LIVE-BIG-GREEN LONG regression.
- **Trigger Reason & Requirement**: The Owner reported an apparent SHORT on a rising live candle and repeated CAP entries, and explicitly requested correction.
- **Verification & Test Status**: 159 focused breakout, WILD_SWING strategy/Firewall/Runner, shared defensive-exit, and WAIT authority/integration tests passed. Deployment target check found `binance-8006.service` inactive with no unit fragment installed in this environment; no service was started or restarted. Source changes are ready for commit/push; deployment remains unverified.

### [2026-10-17 13:19:23 CST] - Modification Phase: Hold WILD_SWING Through Pullbacks Until Profit Lock or True Pivot
- **Author**: shudgai999 / Copilot SDK
- **Target Files**: `core/services/strategies/wild_swing_strategy.py`; WILD_SWING strategy and runner tests.
- **Modification Description**: WILD_SWING now authorizes ordinary exits only for trailing profit lock, breakeven protection, numeric hard stop, or a strict three-closed-candle true peak/valley confirmed after the entry bar. Ordinary MA/KC trend reversals, pullbacks, and an unconfirmed entry-bar wick no longer close the position. The shared defensive-exit helpers remain unchanged for other strategies.
- **Trigger Reason & Requirement**: The Owner requested that LONG pullbacks during a continuing bullish move and SHORT pullbacks during a continuing bearish move must not close positions; exit only on profit lock or a confirmed true peak/valley.
- **Verification & Test Status**: 59 focused WILD_SWING strategy, runner, and close-execution tests passed. Changed Python modules compiled and `git diff --check` passed. Changes are local only; no commit, push, deployment, or service restart.
