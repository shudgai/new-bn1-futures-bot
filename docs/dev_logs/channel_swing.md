### [2026-09-14 12:32:07 UTC+8] - Modification Phase: MA15 Rail Pivot Trend Strategy
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`, `core/guards/abnormal_guard.py`, `core/engine.py`
- **Modification Description**: Channel Swing entry now requires MA3/MA15 and closed KC direction to agree. Positions retain the account hard stop and exit only for a waterfall, two adverse closed candles, or a confirmed MA15 V/peak adjacent to its directional KC rail. An abnormal reversal requires a later closed confirmation candle and is marked as a reverse special-K entry after it opens.
- **Trigger Reason & Requirement**: User-authorized 2026-09-14 strategy change: ride MA3/MA15 and KC trends, recognize MA15 rail-adjacent peaks/valleys in either ordering, and delay abnormal reversals until the next confirmed candle.
- **Verification & Test Status**: Passed `tests/test_channel_ma15_rail_pivot.py` (4), `tests/test_channel_ma15_direction.py -k ma3_ma15` (4), and `tests/test_channel_abnormal_direction_release.py -k waits_for_a_closed_confirmation_after_the_exit_bar` (2). `tests/test_channel_waterfall_threshold.py` has 18 pre-existing expectations at 1.5 ATR while the current configured waterfall threshold is 2.5 ATR.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: MA15 Rail Arming Clarification
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`
- **Modification Description**: The 40% MA15-to-rail distance now arms only the rail-pivot observation. Exit requires the MA15 turning point itself to be the closest point and remain within 40%; a prior 40% approach that widens before the true pivot continues holding. Before rail arming, an aligned opposite MA3/MA15/KC trend exits the position.
- **Trigger Reason & Requirement**: User clarification that 40% alone must not close; an unarmed, clear directional reversal must close, and post-exit entries still require a new outer-rail breakout.
- **Verification & Test Status**: Passed `tests/test_channel_ma15_rail_pivot.py` (7), including MA15 rail arming, unarmed clear reversal, and post-exit outer-breakout reentry locking.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Confirmed Opposite Rail Reversal
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`
- **Modification Description**: A held short now closes after a new confirmed upper-rail breakout; a held long mirrors this at the lower rail. The opposite entry is submitted only after the close is confirmed and passes the normal MA3/MA15/KC and account checks. A rejected replacement entry requires a later, new outer-rail breakout.
- **Trigger Reason & Requirement**: User requirement: if a position returns to and successfully breaks the opposite rail, close and reverse; do not reuse a failed breakout.
- **Verification & Test Status**: Passed `tests/test_channel_ma15_rail_pivot.py` (8) and Python compilation for the changed strategy and runner modules.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Aligned Special-K Reentry
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`, `core/engine.py`
- **Modification Description**: An intrabar special K enters only where its breakout direction agrees with MA3, MA15, and closed KC direction. After a position closes, a later closed bar may establish either a new aligned MA3/MA15/KC entry, a new aligned special K, or a new confirmed outer-rail breakout; pre-close and same-bar signals remain blocked.
- **Trigger Reason & Requirement**: User requirement: open on same-direction special K; after the position closes, seek a new breakout or another valid entry point.
- **Verification & Test Status**: Passed focused aligned-entry and post-exit reentry tests (10); runner route compilation pending final validation.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Held Special-K Profit Lock
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`, `tests/test_channel_profit_protection.py`
- **Modification Description**: A live special K matching an existing position's direction upgrades that position to `entry_special_k` before profit protection runs, applying the established special-K ladder: 2U net-profit arm and 1U trailing offset.
- **Trigger Reason & Requirement**: User requested same-direction special K during a held position to use special-K profit locking.
- **Verification & Test Status**: Passed dedicated special-K ladder tests (2); same-direction detection and compilation pending final validation.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Closed Pivot Entry Execution
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/swing_service.py`, `core/services/symbol_runner.py`, `core/engine.py`
- **Modification Description**: Reconnected the existing confirmed price/MA3 pivot signal as the highest-priority flat-position entry. Confirmed pivots now pass the runner and order-time revalidation, remain protected by account and price safety checks, and use the right-side closed confirmation K as their persisted de-duplication identifier.
- **Trigger Reason & Requirement**: User identified that the agreed pivot-entry, trend-hold, and MA15 rail-pivot exit strategy was not reaching the actual order path.
- **Verification & Test Status**: Passed pivot decision, inside-channel snapshot, concurrent order, and persisted confirmation checks (6).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Special-K Room and Reentry Policy
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/engine.py`, `core/services/symbol_runner.py`, `tests/test_channel_profit_protection.py`
- **Modification Description**: Profit-room estimation now runs only for special-K entries; pivot, MA3/MA15, and ordinary outer-breakout entries no longer calculate or block on it. When a held position is promoted by a same-direction special K, its special-K profit lock can close the position and, only after a confirmed close, submit one same-direction special-K reentry through existing account safety checks.
- **Trigger Reason & Requirement**: User requirement to remove profit-room calculation from all non-special-K entries and immediately continue in the same direction after a held special-K profit exit.
- **Verification & Test Status**: Passed dedicated ordinary-position exclusion, special-K ladder, and confirmed same-side reentry tests (6).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Two Closed Bodies Trend Hold
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/swing_service.py`, `core/services/symbol_runner.py`, `core/engine.py`
- **Modification Description**: Two consecutive closed same-colour body candles now form a general entry without profit-room estimation. Ordinary positions no longer exit on an intermediate MA3/MA15/KC reversal or opposite-rail breakout; they hold until the MA15 rail-adjacent confirmed pivot. Account hard-stop, waterfall, and two-adverse-candle protections remain enabled. Special-K profit locking remains separate.
- **Trigger Reason & Requirement**: User requirement: two green/red candles open a position without profit calculation, then hold until the true MA15 peak/valley.
- **Verification & Test Status**: Passed two-body entry (8), MA15 rail-pivot (9), special-K lock/reentry (6), and Python compilation.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Margin-Scaled Profit Lock
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/exits/profit_protection_service.py`, `tests/test_channel_profit_protection.py`
- **Modification Description**: Profit-lock stages now scale to the actual position margin. At 50U, ordinary positions arm at 2U and trail by 1U; special-K positions arm at 1U and trail by 0.5U. Persisted legacy positions without a margin retain their prior fixed configuration.
- **Trigger Reason & Requirement**: User noted that a 50U account should not need a fixed 4U profit before locking.
- **Verification & Test Status**: Passed margin-scaling, ordinary ladder, and special-K ladder tests (6).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Lobster 1H Bearish Priority Short
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`, `core/engine.py`
- **Modification Description**: When `龍蝦/USDT` is flat, its completed 1H SuperTrend cache is bearish, and the live 1m candle is red, the strategy permits a priority short without waiting for a peak or two closed red candles. The scan, fresh snapshot, and final order decision share the same condition.
- **Trigger Reason & Requirement**: User confirmed: open a Lobster short when it becomes bearish.
- **Verification & Test Status**: Passed dedicated Lobster 1H bearish red-candle test and Python compilation.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Confirmed Three-Point Peak/Valley Exit
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `tests/test_channel_ma15_rail_pivot.py`
- **Modification Description**: The MA15 rail exit now requires three confirmations: MA15's three-point turn reaches the directional rail within 40%, price forms a strict three-point high/low at the same point, and the right-side candle closes in the reversal direction. Equal highs/lows and an unconfirmed candle do not exit.
- **Trigger Reason & Requirement**: User required a true three-point peak/valley exit that is not fooled by a false breakout.
- **Verification & Test Status**: Passed MA15 rail-pivot and fakeout regression tests (11).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Trend Reversal Risk Exit
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`, `tests/test_channel_ma15_direction.py`
- **Modification Description**: Held positions now close, without automatically reversing, if the completed 1H SuperTrend turns against the held side or if 1m MA3, MA15, and closed KC direction synchronously reverse. The existing true three-point peak/valley and emergency exits remain active.
- **Trigger Reason & Requirement**: User clarified that positions must not wait until a late peak/valley when the higher timeframe or complete 1m trend has already reversed.
- **Verification & Test Status**: Passed long/short 1m three-indicator reversal tests (2).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Three-Point Pivot-Only Exit
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/symbol_runner.py`, `tests/test_channel_ma15_rail_pivot.py`
- **Modification Description**: Reverted 1H and intermediate 1m trend reversals as holding exits. 1H now remains a new-entry direction filter only. A held long exits at a strict price and MA3 three-point peak plus a closed red right-side confirmation candle; a held short mirrors this at a strict three-point valley plus a closed green confirmation candle. MA15 rail proximity is no longer required.
- **Trigger Reason & Requirement**: User clarified that a continuously declining Lobster position should exit at a genuine three-point peak/valley, while 1H decides direction only at entry.
- **Verification & Test Status**: Passed long/short three-point true-pivot and fakeout tests (4).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Special-K-Only Profit-Room Gate
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/engine.py`, `core/services/entry_diagnostics_service.py`, `core/services/rule_registry.py`, `tests/test_channel_all_entry_room.py`
- **Modification Description**: Removed profit-room calculation and rejection from ordinary Channel Swing entries only. Special-K entries retain profit-room calculation and are blocked when insufficient. Chart diagnostics report a conflicting completed 1H SuperTrend direction before lower-priority entry detail, and show profit-room status only for a special-K candidate.
- **Trigger Reason & Requirement**: User confirmed that peak/valley swing entries create their profit through the subsequent trend and must not be rejected by a precomputed structural target; special-K protection remains unchanged.
- **Verification & Test Status**: Passed 48 ordinary-entry no-room and 2 special-K room-gate regressions.

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Direct KC Direction Entry
- **Author**: GitHub Copilot
- **Target Files & Lines**: `core/services/strategies/outer_strategy.py`, `core/services/swing_service.py`, `core/services/symbol_runner.py`, `tests/test_channel_ma15_direction.py`
- **Modification Description**: A confirmed closed KC middle direction now permits a direct same-direction entry without waiting for two same-colour candles or an outer-rail break. Price/MA3 pivots remain higher priority; special-K, two-body, and breakout conditions remain valid signal labels. The 1H filter, account safety, stale-quote checks, abnormal-market guard, and one-entry-per-candle constraint remain in the order path.
- **Trigger Reason & Requirement**: User clarified that a confirmed KC direction should be sufficient to open the same direction; two same-colour candles and breakouts must not be mandatory gates.
- **Verification & Test Status**: Passed direct KC long/short entry tests (2).

### [2026-09-14 12:32:07 UTC+8] - Modification Phase: Pivot 1H Alignment Markers
- **Author**: GitHub Copilot
- **Target Files & Lines**: `services/api.py`, `web/index.html`
- **Modification Description**: The 1m chart now evaluates confirmed pivots and labels each peak/valley marker with the last completed 1H SuperTrend alignment at that time: `順1H`, `逆1H`, or `1H未取得`.
- **Trigger Reason & Requirement**: User requested visible 1H trend agreement directly at peak/valley chart signals.
- **Verification & Test Status**: Python API compilation and editor diagnostics passed.