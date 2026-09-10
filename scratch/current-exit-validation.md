# 現行出口續驗紀錄

本輪使用者確認沿用現行出口，不恢復中軌加即時反向實體平倉。本輪未修改策略、測試斷言或重啟服務。

- 目前出口、突破、第三根 K 色取消、同根回調、平倉去重、獲利保護、每根限次及方向相關測試：261 通過、11 失敗。
- 五份指定回歸（channel_swing、channel_position_path、channel_swing_execution、channel_confirmed_rules、channel_stop_preservation）：138 通過、69 失敗。
- 合计 399 通過、80 失敗。失敗測試名稱與 /tmp/pepe-entry-baseline 修改前副本完整一致，未新增失敗。
- 所指的中軌兩組測試：12 通過、26 失敗；包含於上述指定回歸，不重複計數。
- _channel_swing_action 有持倉時返回 KC_POSITION_EXITS_MANAGED，實際出口由持倉流程處理異常 K、MA3 轉彎與獲利保護；舊中軌測試直接要求 helper 回傳 EXIT，與現行規則／分工不符。
- 其他既有失敗包含獲利保護 10% 收緊欄位與舊出口優先序，未為通過測試擅自改動行為。
- 8006 API is_running=true、paper_trading=true；此次查詢可見成交紀錄中未發現 Channel Swing 自動開倉，不能宣稱自動成交驗證完成。

## 原始輸出

- /tmp/current-rules-validation.txt
- /tmp/required-rules-validation.txt
- /tmp/baseline-continued-validation.txt
- /tmp/middle-exit-audit.txt

## 既有失敗清單

- FAILED tests/test_channel_confirmed_rules.py::test_confirmed_signal_fills_once_on_same_scan[True-LONG]
- FAILED tests/test_channel_confirmed_rules.py::test_confirmed_signal_fills_once_on_same_scan[True-SHORT]
- FAILED tests/test_channel_confirmed_rules.py::test_rejected_reverse_retry_expires_with_bar[LONG]
- FAILED tests/test_channel_confirmed_rules.py::test_rejected_reverse_retry_expires_with_bar[SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.39-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.39-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.4-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.4-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.4106594857-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.4106594857-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.5-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.5-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.6-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.6-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.39-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.39-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.4-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.4-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.4106594857-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.4106594857-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.5-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.5-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.6-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.6-SHORT]
- FAILED tests/test_channel_position_path.py::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.0-False-True-LONG]
- FAILED tests/test_channel_position_path.py::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.0-False-True-SHORT]
- FAILED tests/test_channel_position_path.py::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.2-False-True-LONG]
- FAILED tests/test_channel_position_path.py::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.2-False-True-SHORT]
- FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1--0.1-LONG]
- FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1--0.1-SHORT]
- FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.0-LONG]
- FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.0-SHORT]
- FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.1-LONG]
- FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.1-SHORT]
- FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.39-LONG]
- FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.39-SHORT]
- FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.4-LONG]
- FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.4-SHORT]
- FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.41-LONG]
- FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.41-SHORT]
- FAILED tests/test_channel_position_path.py::test_normal_opposite_break_requires_postentry_path[LONG]
- FAILED tests/test_channel_position_path.py::test_normal_opposite_break_requires_postentry_path[SHORT]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.25-False-LONG]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.25-False-SHORT]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.25-True-LONG]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.25-True-SHORT]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.5-False-LONG]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.5-False-SHORT]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.5-True-LONG]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.5-True-SHORT]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.6-False-LONG]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.6-False-SHORT]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.6-True-LONG]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.6-True-SHORT]
- FAILED tests/test_channel_profit_protection.py::test_engine_passes_live_frame_and_preserves_exit_priority[EXIT]
- FAILED tests/test_channel_profit_protection.py::test_engine_passes_live_frame_and_preserves_exit_priority[HOLD]
- FAILED tests/test_channel_profit_protection.py::test_engine_passes_live_frame_and_preserves_exit_priority[REVERSE]
- FAILED tests/test_channel_profit_protection.py::test_first_opposite_tick_already_beyond_ten_percent_exits[LONG]
- FAILED tests/test_channel_profit_protection.py::test_first_opposite_tick_already_beyond_ten_percent_exits[SHORT]
- FAILED tests/test_channel_profit_protection.py::test_general_exit_has_priority_over_profit_close
- FAILED tests/test_channel_profit_protection.py::test_reopened_position_reclassifies_without_previous_ten_percent_lock[CHOPPY]
- FAILED tests/test_channel_profit_protection.py::test_reopened_position_reclassifies_without_previous_ten_percent_lock[SMOOTH]
- FAILED tests/test_channel_profit_protection.py::test_stack_live_opposite_tightens_ten_dollar_peak_to_nine[LONG]
- FAILED tests/test_channel_profit_protection.py::test_stack_live_opposite_tightens_ten_dollar_peak_to_nine[SHORT]
- FAILED tests/test_channel_profit_protection.py::test_tightening_on_opposite_tick_honors_previously_observed_peak
- FAILED tests/test_channel_swing.py::test_aligned_trend_can_enter_without_breakout_body_confirmation
- FAILED tests/test_channel_swing.py::test_favorable_waterfall_live_turn_exits_on_long_adverse_body[LONG]
- FAILED tests/test_channel_swing.py::test_favorable_waterfall_live_turn_exits_on_long_adverse_body[SHORT]
- FAILED tests/test_channel_swing.py::test_flat_scan_can_detect_recent_upper_peak_without_exit_state
- FAILED tests/test_channel_swing.py::test_live_price_above_upper_rail_waits_without_valid_closed_body
- FAILED tests/test_channel_swing.py::test_long_does_not_lock_without_ma_cross
- FAILED tests/test_channel_swing.py::test_outer_reentry_later_clean_continuation_can_enter_after_spike_wait
- FAILED tests/test_channel_swing.py::test_outer_reentry_macro_trend_entry_long_on_upper_kc_structure_break
- FAILED tests/test_channel_swing.py::test_outer_reentry_macro_trend_entry_short_on_lower_kc_structure_break
- FAILED tests/test_channel_swing.py::test_outer_reentry_normal_two_candle_breakout_remains_tradable
- FAILED tests/test_channel_swing.py::test_outer_reentry_spike_breakout_is_not_traded
- FAILED tests/test_channel_swing.py::test_outside_long_signal_waits_after_opposite_closed_body
- FAILED tests/test_channel_swing.py::test_peak_exit_requires_normal_two_bar_reversal_before_short
- FAILED tests/test_channel_swing.py::test_short_does_not_lock_without_ma_cross
- FAILED tests/test_channel_swing.py::test_sudden_ma15_upper_rail_jump_is_not_convergence

## 2026-09-10 反向長 K 事前風險研究續驗

- 完成兩幣舊資料重跑，以及各 2,191 根近期資料的固定模型驗證；區分盤中反向觸及與收盤反向長實體。
- 收盤長實體：舊驗證 748 個候選，異常率 24.5% → 21.9%，錯過正報酬代理 39/136；近期 174 個候選，異常率 23.6% → 22.0%，錯過代理 6/35。近期 PEPE 多空保留後異常率均上升，未支持跨幣部署。
- 研究資料因果性與統計測試 4 通過；交易策略、服務與出口未修改，沒有新增攔截或重啟。未重跑上述既有交易測試，不能將研究檢查稱為全套回歸。
- 詳細報告：`reports/channel_entry_risk_current/ADVERSE_BODY_REVIEW.md`。只有分鐘開盤候選，不能估計實際同根回調成交的獲利機會損失。

## 突破未成交後允許延續

已依用戶修正加入兩根軌外同色已收線 K 延續入口，詳細規則與驗證見 `scratch/outside-continuation-validation.md`。出口未更動；新增針對性測試 92 通過，指定回歸仍為原有 69 個失敗，無新增失敗。

## 延續入口上線後邏輯測試（2026-09-10）

本輪依用戶要求執行 13 份離線邏輯測試，共 416 項：336 通過、80 失敗；失敗名稱與本文件原有 80 項清單完全一致，沒有新增失敗。未修改策略、測試斷言或重啟服務。

| 測試檔 | 通過 | 失敗 |
|---|---:|---:|
| `test_channel_outside_continuation.py` | 14 | 0 |
| `test_channel_breakout_only.py` | 54 | 0 |
| `test_channel_intrabar_execution.py` | 24 | 0 |
| `test_channel_immediate_exits.py` | 36 | 0 |
| `test_channel_live_ma3_exit.py` | 14 | 0 |
| `test_channel_profit_protection.py` | 26 | 11 |
| `test_channel_candle_frequency.py` | 18 | 0 |
| `test_close_deduplication.py` | 12 | 0 |
| `test_channel_swing.py` | 44 | 15 |
| `test_channel_position_path.py` | 52 | 50 |
| `test_channel_swing_execution.py` | 3 | 0 |
| `test_channel_confirmed_rules.py` | 33 | 4 |
| `test_channel_stop_preservation.py` | 6 | 0 |

既有失敗分布：獲利保護 11、Channel Swing 15、持倉路徑 50、confirmed_rules 4。這些測試仍未通過，不能宣稱全套正常；本次僅確認未新增失敗。

原始輸出：`/tmp/logic-validation-current.txt`；JUnit：`/tmp/logic-validation-current.xml`。
