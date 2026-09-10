# PEPE破軌與MA3小弧度驗證（2026-09-10）

## 實際觀察
- 23:02:47–23:03:00（台北時間）1000PEPE空單已進入外軌送單評估；日誌記錄淨空間約-0.0774%至-0.0896%，低於0.1500%門檻，未成交原因是風控，而非完全沒有產生訊號。
- 23:03:14起多次記錄KC_PROFIT_TARGET_UNAVAILABLE，沒有可用的未突破已確認前低。本輪沒有放寬空間門檻或清除既有候選失效鎖。
- 另有live:1789052520000.0峰谷候選整根被鎖的紀錄。重現原因為盤中快照暫時失效也被寫入已收線候選失效鎖；修正為不新增這類盤中暫時失效鎖，後續新轉向仍須全部重驗。
- 23:03:22出現PEPE SHORT，成交原因為手動開倉，不能算自動開倉驗證。本輪調查沒有代用戶手動下單或清除帳戶。

## 修正
- MA3相對上一根已收線的反向幅度與從峰谷回退幅度均須達固定0.10 ATR；只有微小反向斜率不平倉。版本2保留峰谷與門檻、撤銷待平後按新規則重验，版本3有效待平沿用持久化重試。
- 峰谷暫時失效允許同根新轉向重驗；成功成交仍每根限次，已存在的失效鎖不由程式查詢清除。
- 圖表統一顯示當前CK方向、峰谷與外軌候選、報價與結構淨利空間原因；純讀取，不觀察或消耗峰谷，不改回踩票據。
- 未新增盤整限制；突破、延續、異常重開及持倉其他出口不變。

## 驗證
- 主要現行路徑專項342 passed；新增修正測試33 passed，包含實際圖表API不改交易狀態與PEPE小價格尺度。
- 全部59份進出場相關測試：1367 passed / 382 failed / 40 skipped。隔離HEAD 4f7da5f基準58份：1334 passed / 382 failed / 40 skipped。失敗名稱集合完全相同，新增失敗0；既有失敗未逐項修復，不能宣稱全套通過。
- 原始輸出：/tmp/pepe-all-before.{txt,xml}、/tmp/pepe-all-final.{txt,xml}、/tmp/pepe-current-routes.{txt,xml}。這些為離線邏輯測試，不是實際交易所成交保證。

## 每檔結果

| 測試檔 | 通過 | 失敗 | 跳過 |
|---|---:|---:|---:|
| `tests.test_channel_abnormal_pullback` | 17 | 6 | 0 |
| `tests.test_channel_adverse_entry` | 30 | 0 | 0 |
| `tests.test_channel_aligned_entry` | 91 | 0 | 0 |
| `tests.test_channel_all_entry_room` | 26 | 0 | 0 |
| `tests.test_channel_break_confirmation` | 1 | 0 | 16 |
| `tests.test_channel_breakout_continuation` | 15 | 12 | 0 |
| `tests.test_channel_breakout_only` | 52 | 2 | 0 |
| `tests.test_channel_candle_frequency` | 18 | 0 | 0 |
| `tests.test_channel_chop_entry_guard` | 2 | 8 | 0 |
| `tests.test_channel_ck_reverse` | 33 | 0 | 0 |
| `tests.test_channel_confirmed_rules` | 33 | 4 | 0 |
| `tests.test_channel_current_rules` | 18 | 13 | 0 |
| `tests.test_channel_dual_entry` | 11 | 11 | 0 |
| `tests.test_channel_entry_risk_research` | 4 | 0 | 0 |
| `tests.test_channel_exit_comparison` | 4 | 1 | 0 |
| `tests.test_channel_exit_policy` | 28 | 2 | 0 |
| `tests.test_channel_falling_waves` | 4 | 7 | 0 |
| `tests.test_channel_hard_stop` | 25 | 0 | 0 |
| `tests.test_channel_immediate_exits` | 50 | 0 | 0 |
| `tests.test_channel_impulse_exit` | 92 | 24 | 0 |
| `tests.test_channel_intrabar_execution` | 32 | 0 | 0 |
| `tests.test_channel_late_entry_room` | 42 | 0 | 0 |
| `tests.test_channel_live_body_exit` | 32 | 8 | 0 |
| `tests.test_channel_live_ma3_exit` | 4 | 10 | 0 |
| `tests.test_channel_live_pivot` | 50 | 0 | 0 |
| `tests.test_channel_live_profit_reentry` | 40 | 2 | 0 |
| `tests.test_channel_ma15_direction` | 16 | 4 | 0 |
| `tests.test_channel_ma3_outer_exit` | 0 | 0 | 20 |
| `tests.test_channel_next_live_push` | 24 | 3 | 0 |
| `tests.test_channel_outer_cycle` | 32 | 6 | 4 |
| `tests.test_channel_outside_continuation` | 8 | 6 | 0 |
| `tests.test_channel_pepe_entry_consistency` | 6 | 0 | 0 |
| `tests.test_channel_pepe_execution_recovery` | 33 | 0 | 0 |
| `tests.test_channel_pivot_entry` | 41 | 26 | 0 |
| `tests.test_channel_position_path` | 52 | 50 | 0 |
| `tests.test_channel_post_close_recovery` | 8 | 26 | 0 |
| `tests.test_channel_profit_only_exit` | 8 | 6 | 0 |
| `tests.test_channel_profit_protection` | 26 | 11 | 0 |
| `tests.test_channel_profit_room` | 12 | 6 | 0 |
| `tests.test_channel_protected_only_exit` | 32 | 0 | 0 |
| `tests.test_channel_reentry_body_confirmation` | 14 | 3 | 0 |
| `tests.test_channel_requested_fixes` | 19 | 16 | 0 |
| `tests.test_channel_retracement_twenty` | 4 | 2 | 0 |
| `tests.test_channel_significant_ma3` | 19 | 0 | 0 |
| `tests.test_channel_single_abnormal_removed` | 10 | 0 | 0 |
| `tests.test_channel_stop_preservation` | 6 | 0 | 0 |
| `tests.test_channel_surge_entry` | 19 | 9 | 0 |
| `tests.test_channel_surge_release` | 8 | 1 | 0 |
| `tests.test_channel_sustained_trend` | 36 | 0 | 0 |
| `tests.test_channel_swing` | 44 | 15 | 0 |
| `tests.test_channel_swing_execution` | 3 | 0 | 0 |
| `tests.test_channel_symmetric_rules` | 29 | 13 | 0 |
| `tests.test_channel_three_red_entry` | 11 | 15 | 0 |
| `tests.test_channel_trend_hold` | 25 | 22 | 0 |
| `tests.test_channel_two_closed_entry` | 4 | 22 | 0 |
| `tests.test_channel_upward_exit_release` | 33 | 3 | 0 |
| `tests.test_close_deduplication` | 12 | 0 | 0 |
| `tests.test_direct_break_execution` | 17 | 7 | 0 |
| `tests.test_kc_stoploss` | 2 | 0 | 0 |

## 基準已存在的失敗

- `tests.test_channel_abnormal_pullback::test_abnormal_exit_creates_wait_only_after_success[False-LONG]`
- `tests.test_channel_abnormal_pullback::test_abnormal_exit_creates_wait_only_after_success[False-SHORT]`
- `tests.test_channel_abnormal_pullback::test_abnormal_exit_creates_wait_only_after_success[True-LONG]`
- `tests.test_channel_abnormal_pullback::test_abnormal_exit_creates_wait_only_after_success[True-SHORT]`
- `tests.test_channel_abnormal_pullback::test_pullback_reentry_revalidates_and_preserves_risk[none-LONG]`
- `tests.test_channel_abnormal_pullback::test_pullback_reentry_revalidates_and_preserves_risk[none-SHORT]`
- `tests.test_channel_breakout_continuation::test_after_close_execution_submits_once_and_snapshot_accepts[LONG]`
- `tests.test_channel_breakout_continuation::test_after_close_execution_submits_once_and_snapshot_accepts[SHORT]`
- `tests.test_channel_breakout_continuation::test_after_opposite_close_uses_new_breakout_bar_and_live_ticker[LONG]`
- `tests.test_channel_breakout_continuation::test_after_opposite_close_uses_new_breakout_bar_and_live_ticker[SHORT]`
- `tests.test_channel_breakout_continuation::test_calm_successor_fills_paper_order_after_large_closed_candle`
- `tests.test_channel_breakout_continuation::test_original_breakout_and_later_continuation_are_eligible[1788965280-LONG]`
- `tests.test_channel_breakout_continuation::test_original_breakout_and_later_continuation_are_eligible[1788965280-SHORT]`
- `tests.test_channel_breakout_continuation::test_original_breakout_and_later_continuation_are_eligible[1788965340-LONG]`
- `tests.test_channel_breakout_continuation::test_original_breakout_and_later_continuation_are_eligible[1788965340-SHORT]`
- `tests.test_channel_breakout_continuation::test_original_breakout_and_later_continuation_are_eligible[1788965400-LONG]`
- `tests.test_channel_breakout_continuation::test_original_breakout_and_later_continuation_are_eligible[1788965400-SHORT]`
- `tests.test_channel_breakout_continuation::test_risk_gate_receives_current_body_and_keeps_rejection`
- `tests.test_channel_breakout_only::test_failed_ma3_close_is_retried_after_quote_recovers[LONG]`
- `tests.test_channel_breakout_only::test_failed_ma3_close_is_retried_after_quote_recovers[SHORT]`
- `tests.test_channel_chop_entry_guard::test_confirmed_break_is_not_blocked_by_chop_state[LONG]`
- `tests.test_channel_chop_entry_guard::test_confirmed_break_is_not_blocked_by_chop_state[SHORT]`
- `tests.test_channel_chop_entry_guard::test_direct_confirmed_break_does_not_use_chop_guard[False-LONG]`
- `tests.test_channel_chop_entry_guard::test_direct_confirmed_break_does_not_use_chop_guard[False-SHORT]`
- `tests.test_channel_chop_entry_guard::test_direct_confirmed_break_does_not_use_chop_guard[True-LONG]`
- `tests.test_channel_chop_entry_guard::test_direct_confirmed_break_does_not_use_chop_guard[True-SHORT]`
- `tests.test_channel_chop_entry_guard::test_unlock_still_requires_live_outer_break[LONG]`
- `tests.test_channel_chop_entry_guard::test_unlock_still_requires_live_outer_break[SHORT]`
- `tests.test_channel_confirmed_rules::test_confirmed_signal_fills_once_on_same_scan[True-LONG]`
- `tests.test_channel_confirmed_rules::test_confirmed_signal_fills_once_on_same_scan[True-SHORT]`
- `tests.test_channel_confirmed_rules::test_rejected_reverse_retry_expires_with_bar[LONG]`
- `tests.test_channel_confirmed_rules::test_rejected_reverse_retry_expires_with_bar[SHORT]`
- `tests.test_channel_current_rules::test_paper_ticker_preserves_channel_stop_but_defers_exit_to_engine[LONG]`
- `tests.test_channel_current_rules::test_paper_ticker_preserves_channel_stop_but_defers_exit_to_engine[SHORT]`
- `tests.test_channel_current_rules::test_reversal_closes_first_and_submits_opposite_order[False-LONG]`
- `tests.test_channel_current_rules::test_reversal_closes_first_and_submits_opposite_order[False-SHORT]`
- `tests.test_channel_current_rules::test_reversal_closes_first_and_submits_opposite_order[True-LONG]`
- `tests.test_channel_current_rules::test_reversal_closes_first_and_submits_opposite_order[True-SHORT]`
- `tests.test_channel_current_rules::test_short_after_lock_unlocks_below_ma15_even_above_kc_middle`
- `tests.test_channel_current_rules::test_short_locked_single_pump_then_true_upper_break_closes_first`
- `tests.test_channel_current_rules::test_short_single_live_pump_holds_without_cross_lock`
- `tests.test_channel_current_rules::test_short_single_pump_without_cross_keeps_position`
- `tests.test_channel_current_rules::test_testnet_channel_short_ticker_spike_does_not_force_close`
- `tests.test_channel_current_rules::test_thirty_bars_can_confirm_outer_break`
- `tests.test_channel_current_rules::test_two_adverse_candles_without_favorable_run_or_structure_failure_hold`
- `tests.test_channel_dual_entry::test_outer_break_confirmation_and_direction[flat_ma15-SHORT]`
- `tests.test_channel_dual_entry::test_outer_break_confirmation_and_direction[invalid_data-SHORT]`
- `tests.test_channel_dual_entry::test_outer_break_confirmation_and_direction[none-LONG]`
- `tests.test_channel_dual_entry::test_outer_break_confirmation_and_direction[none-SHORT]`
- `tests.test_channel_dual_entry::test_outer_break_confirmation_and_direction[opposite_ma15-SHORT]`
- `tests.test_channel_dual_entry::test_outer_break_order_and_profit_reentry_fill_once[False-LONG]`
- `tests.test_channel_dual_entry::test_outer_break_order_and_profit_reentry_fill_once[False-SHORT]`
- `tests.test_channel_dual_entry::test_outer_break_order_and_profit_reentry_fill_once[True-LONG]`
- `tests.test_channel_dual_entry::test_outer_break_order_and_profit_reentry_fill_once[True-SHORT]`
- `tests.test_channel_dual_entry::test_outer_snapshot_rejects_changed_direction[LONG]`
- `tests.test_channel_dual_entry::test_outer_snapshot_rejects_changed_direction[SHORT]`
- `tests.test_channel_exit_comparison::test_signals_match_live_engine_for_entries_and_reversals`
- `tests.test_channel_exit_policy::test_policy[unarmed-ma3-LONG]`
- `tests.test_channel_exit_policy::test_policy[unarmed-ma3-SHORT]`
- `tests.test_channel_falling_waves::test_all_three_conditions_are_required[equal_middle]`
- `tests.test_channel_falling_waves::test_all_three_conditions_are_required[equal_peak]`
- `tests.test_channel_falling_waves::test_all_three_conditions_are_required[peaks]`
- `tests.test_channel_falling_waves::test_all_three_conditions_are_required[slope]`
- `tests.test_channel_falling_waves::test_all_three_conditions_are_required[troughs]`
- `tests.test_channel_falling_waves::test_falling_waves_block_live_long_without_opening_short`
- `tests.test_channel_falling_waves::test_no_automatic_short_and_existing_short_signal_still_allowed`
- `tests.test_channel_impulse_exit::test_favorable_impulse_then_long_pivot_exits[bodies0-closed-LONG]`
- `tests.test_channel_impulse_exit::test_favorable_impulse_then_long_pivot_exits[bodies0-closed-SHORT]`
- `tests.test_channel_impulse_exit::test_favorable_impulse_then_long_pivot_exits[bodies0-live-LONG]`
- `tests.test_channel_impulse_exit::test_favorable_impulse_then_long_pivot_exits[bodies0-live-SHORT]`
- `tests.test_channel_impulse_exit::test_favorable_impulse_then_long_pivot_exits[bodies1-closed-LONG]`
- `tests.test_channel_impulse_exit::test_favorable_impulse_then_long_pivot_exits[bodies1-closed-SHORT]`
- `tests.test_channel_impulse_exit::test_favorable_impulse_then_long_pivot_exits[bodies1-live-LONG]`
- `tests.test_channel_impulse_exit::test_favorable_impulse_then_long_pivot_exits[bodies1-live-SHORT]`
- `tests.test_channel_impulse_exit::test_impulse_execution_only_closes_after_favorable_run[False-favorable-LONG]`
- `tests.test_channel_impulse_exit::test_impulse_execution_only_closes_after_favorable_run[False-favorable-SHORT]`
- `tests.test_channel_impulse_exit::test_impulse_execution_only_closes_after_favorable_run[True-favorable-LONG]`
- `tests.test_channel_impulse_exit::test_impulse_execution_only_closes_after_favorable_run[True-favorable-SHORT]`
- `tests.test_channel_impulse_exit::test_impulse_exit_repeated_scans_never_reopen_old_signal[False-LONG]`
- `tests.test_channel_impulse_exit::test_impulse_exit_repeated_scans_never_reopen_old_signal[False-SHORT]`
- `tests.test_channel_impulse_exit::test_impulse_exit_repeated_scans_never_reopen_old_signal[True-LONG]`
- `tests.test_channel_impulse_exit::test_impulse_exit_repeated_scans_never_reopen_old_signal[True-SHORT]`
- `tests.test_channel_impulse_exit::test_live_pivot_uses_latest_price_when_frame_close_lags[LONG]`
- `tests.test_channel_impulse_exit::test_live_pivot_uses_latest_price_when_frame_close_lags[SHORT]`
- `tests.test_channel_impulse_exit::test_opposite_confirmed_outer_break_reverses_even_with_abnormal_pair[LONG]`
- `tests.test_channel_impulse_exit::test_opposite_confirmed_outer_break_reverses_even_with_abnormal_pair[SHORT]`
- `tests.test_channel_impulse_exit::test_small_candle_run_never_accumulates_into_favorable_impulse[0.6-EXIT-2-LONG]`
- `tests.test_channel_impulse_exit::test_small_candle_run_never_accumulates_into_favorable_impulse[0.6-EXIT-2-SHORT]`
- `tests.test_channel_impulse_exit::test_small_candle_run_never_accumulates_into_favorable_impulse[0.6-EXIT-5-LONG]`
- `tests.test_channel_impulse_exit::test_small_candle_run_never_accumulates_into_favorable_impulse[0.6-EXIT-5-SHORT]`
- `tests.test_channel_live_body_exit::test_live_majority_retains_path_but_ignores_space[space40-LONG]`
- `tests.test_channel_live_body_exit::test_live_majority_retains_path_but_ignores_space[space40-SHORT]`
- `tests.test_channel_live_body_exit::test_live_majority_retains_path_but_ignores_space[space50-LONG]`
- `tests.test_channel_live_body_exit::test_live_majority_retains_path_but_ignores_space[space50-SHORT]`
- `tests.test_channel_live_body_exit::test_strict_majority_color_and_wicks[-0.4-1.0-LONG]`
- `tests.test_channel_live_body_exit::test_strict_majority_color_and_wicks[-0.4-1.0-SHORT]`
- `tests.test_channel_live_body_exit::test_strict_majority_color_and_wicks[-0.4-3.6e-05-LONG]`
- `tests.test_channel_live_body_exit::test_strict_majority_color_and_wicks[-0.4-3.6e-05-SHORT]`
- `tests.test_channel_live_ma3_exit::test_ma3_turn_exits_even_when_protection_arms_or_is_already_armed[False-False-LONG]`
- `tests.test_channel_live_ma3_exit::test_ma3_turn_exits_even_when_protection_arms_or_is_already_armed[False-False-SHORT]`
- `tests.test_channel_live_ma3_exit::test_ma3_turn_exits_even_when_protection_arms_or_is_already_armed[False-True-LONG]`
- `tests.test_channel_live_ma3_exit::test_ma3_turn_exits_even_when_protection_arms_or_is_already_armed[False-True-SHORT]`
- `tests.test_channel_live_ma3_exit::test_ma3_turn_exits_even_when_protection_arms_or_is_already_armed[True-False-LONG]`
- `tests.test_channel_live_ma3_exit::test_ma3_turn_exits_even_when_protection_arms_or_is_already_armed[True-False-SHORT]`
- `tests.test_channel_live_ma3_exit::test_ma3_turn_exits_even_when_protection_arms_or_is_already_armed[True-True-LONG]`
- `tests.test_channel_live_ma3_exit::test_ma3_turn_exits_even_when_protection_arms_or_is_already_armed[True-True-SHORT]`
- `tests.test_channel_live_ma3_exit::test_post_entry_ma3_turn_exits_at_live_price[LONG]`
- `tests.test_channel_live_ma3_exit::test_post_entry_ma3_turn_exits_at_live_price[SHORT]`
- `tests.test_channel_live_profit_reentry::test_middle_touch_and_legacy_pending_never_close[False-SHORT]`
- `tests.test_channel_live_profit_reentry::test_middle_touch_and_legacy_pending_never_close[True-SHORT]`
- `tests.test_channel_ma15_direction::test_three_closed_ma15_controls_entry[1.0-aligned-LONG]`
- `tests.test_channel_ma15_direction::test_three_closed_ma15_controls_entry[1.0-aligned-SHORT]`
- `tests.test_channel_ma15_direction::test_three_closed_ma15_controls_entry[1000.0-aligned-LONG]`
- `tests.test_channel_ma15_direction::test_three_closed_ma15_controls_entry[1000.0-aligned-SHORT]`
- `tests.test_channel_next_live_push::test_entry_during_second_body_keeps_breakout_context_for_tightening`
- `tests.test_channel_next_live_push::test_live_successor_cannot_replace_closed_confirmation[LONG]`
- `tests.test_channel_next_live_push::test_live_successor_cannot_replace_closed_confirmation[SHORT]`
- `tests.test_channel_outer_cycle::test_outer_cycle_conditions[doji-LONG]`
- `tests.test_channel_outer_cycle::test_outer_cycle_conditions[doji-SHORT]`
- `tests.test_channel_outer_cycle::test_outer_cycle_conditions[first_doji-LONG]`
- `tests.test_channel_outer_cycle::test_outer_cycle_conditions[first_doji-SHORT]`
- `tests.test_channel_outer_cycle::test_reentry_fresh_validation_profit_room_and_dedup[color-LONG]`
- `tests.test_channel_outer_cycle::test_reentry_fresh_validation_profit_room_and_dedup[color-SHORT]`
- `tests.test_channel_outside_continuation::test_reentry_continuation_retains_abnormal_pullback_and_new_signal[False-LONG]`
- `tests.test_channel_outside_continuation::test_reentry_continuation_retains_abnormal_pullback_and_new_signal[False-SHORT]`
- `tests.test_channel_outside_continuation::test_reentry_continuation_retains_abnormal_pullback_and_new_signal[True-LONG]`
- `tests.test_channel_outside_continuation::test_reentry_continuation_retains_abnormal_pullback_and_new_signal[True-SHORT]`
- `tests.test_channel_outside_continuation::test_two_outside_bodies_allow_continuation_without_new_cross[LONG]`
- `tests.test_channel_outside_continuation::test_two_outside_bodies_allow_continuation_without_new_cross[SHORT]`
- `tests.test_channel_pivot_entry::test_confirmed_outer_break_can_enter_without_pivot[LONG]`
- `tests.test_channel_pivot_entry::test_confirmed_outer_break_can_enter_without_pivot[SHORT]`
- `tests.test_channel_pivot_entry::test_live_ma15_and_live_pivot_cannot_change_closed_signal[SHORT]`
- `tests.test_channel_pivot_entry::test_pending_middle_close_retries_even_if_profit_later_arms[LONG]`
- `tests.test_channel_pivot_entry::test_pending_middle_close_retries_even_if_profit_later_arms[SHORT]`
- `tests.test_channel_pivot_entry::test_pivot_requires_first_closed_turn_at_order_time[first_doji-LONG]`
- `tests.test_channel_pivot_entry::test_pivot_requires_first_closed_turn_at_order_time[first_doji-SHORT]`
- `tests.test_channel_pivot_entry::test_pivot_requires_first_closed_turn_at_order_time[first_live-LONG]`
- `tests.test_channel_pivot_entry::test_pivot_requires_first_closed_turn_at_order_time[first_live-SHORT]`
- `tests.test_channel_pivot_entry::test_pivot_requires_first_closed_turn_at_order_time[first_opposite-LONG]`
- `tests.test_channel_pivot_entry::test_pivot_requires_first_closed_turn_at_order_time[first_opposite-SHORT]`
- `tests.test_channel_pivot_entry::test_primary_entry_preserves_pivot_signal_code[LONG]`
- `tests.test_channel_pivot_entry::test_primary_entry_preserves_pivot_signal_code[SHORT]`
- `tests.test_channel_pivot_entry::test_process_opens_once_and_persists_pivot_context[LONG]`
- `tests.test_channel_pivot_entry::test_process_opens_once_and_persists_pivot_context[SHORT]`
- `tests.test_channel_pivot_entry::test_profit_reentry_waits_for_pullback_and_reclaim_not_inside_pivot[LONG]`
- `tests.test_channel_pivot_entry::test_profit_reentry_waits_for_pullback_and_reclaim_not_inside_pivot[SHORT]`
- `tests.test_channel_pivot_entry::test_real_middle_exit_waits_for_cross_and_retries[False-LONG]`
- `tests.test_channel_pivot_entry::test_real_middle_exit_waits_for_cross_and_retries[False-SHORT]`
- `tests.test_channel_pivot_entry::test_real_middle_exit_waits_for_cross_and_retries[True-LONG]`
- `tests.test_channel_pivot_entry::test_real_middle_exit_waits_for_cross_and_retries[True-SHORT]`
- `tests.test_channel_pivot_entry::test_real_paper_fill_and_reload_preserve_pivot_state[LONG]`
- `tests.test_channel_pivot_entry::test_real_paper_fill_and_reload_preserve_pivot_state[SHORT]`
- `tests.test_channel_pivot_entry::test_reported_pepe_and_lobster_entry_replay`
- `tests.test_channel_pivot_entry::test_snapshot_accepts_inside_channel_and_rejects_changed_signal[LONG]`
- `tests.test_channel_pivot_entry::test_snapshot_accepts_inside_channel_and_rejects_changed_signal[SHORT]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.25-0.39-LONG]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.25-0.39-SHORT]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.25-0.4-LONG]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.25-0.4-SHORT]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.25-0.4106594857-LONG]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.25-0.4106594857-SHORT]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.25-0.5-LONG]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.25-0.5-SHORT]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.25-0.6-LONG]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.25-0.6-SHORT]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.5-0.39-LONG]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.5-0.39-SHORT]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.5-0.4-LONG]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.5-0.4-SHORT]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.5-0.4106594857-LONG]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.5-0.4106594857-SHORT]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.5-0.5-LONG]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.5-0.5-SHORT]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.5-0.6-LONG]`
- `tests.test_channel_position_path::test_exit_independent_of_live_and_closed_space[0.5-0.6-SHORT]`
- `tests.test_channel_position_path::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.0-False-True-LONG]`
- `tests.test_channel_position_path::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.0-False-True-SHORT]`
- `tests.test_channel_position_path::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.2-False-True-LONG]`
- `tests.test_channel_position_path::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.2-False-True-SHORT]`
- `tests.test_channel_position_path::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1--0.1-LONG]`
- `tests.test_channel_position_path::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1--0.1-SHORT]`
- `tests.test_channel_position_path::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.0-LONG]`
- `tests.test_channel_position_path::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.0-SHORT]`
- `tests.test_channel_position_path::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.1-LONG]`
- `tests.test_channel_position_path::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.1-SHORT]`
- `tests.test_channel_position_path::test_middle_strict_cross_and_space[True-0.39-LONG]`
- `tests.test_channel_position_path::test_middle_strict_cross_and_space[True-0.39-SHORT]`
- `tests.test_channel_position_path::test_middle_strict_cross_and_space[True-0.4-LONG]`
- `tests.test_channel_position_path::test_middle_strict_cross_and_space[True-0.4-SHORT]`
- `tests.test_channel_position_path::test_middle_strict_cross_and_space[True-0.41-LONG]`
- `tests.test_channel_position_path::test_middle_strict_cross_and_space[True-0.41-SHORT]`
- `tests.test_channel_position_path::test_normal_opposite_break_requires_postentry_path[LONG]`
- `tests.test_channel_position_path::test_normal_opposite_break_requires_postentry_path[SHORT]`
- `tests.test_channel_position_path::test_raw_middle_signal_independent_of_ma3_and_space[0.25-False-LONG]`
- `tests.test_channel_position_path::test_raw_middle_signal_independent_of_ma3_and_space[0.25-False-SHORT]`
- `tests.test_channel_position_path::test_raw_middle_signal_independent_of_ma3_and_space[0.25-True-LONG]`
- `tests.test_channel_position_path::test_raw_middle_signal_independent_of_ma3_and_space[0.25-True-SHORT]`
- `tests.test_channel_position_path::test_raw_middle_signal_independent_of_ma3_and_space[0.5-False-LONG]`
- `tests.test_channel_position_path::test_raw_middle_signal_independent_of_ma3_and_space[0.5-False-SHORT]`
- `tests.test_channel_position_path::test_raw_middle_signal_independent_of_ma3_and_space[0.5-True-LONG]`
- `tests.test_channel_position_path::test_raw_middle_signal_independent_of_ma3_and_space[0.5-True-SHORT]`
- `tests.test_channel_position_path::test_raw_middle_signal_independent_of_ma3_and_space[0.6-False-LONG]`
- `tests.test_channel_position_path::test_raw_middle_signal_independent_of_ma3_and_space[0.6-False-SHORT]`
- `tests.test_channel_position_path::test_raw_middle_signal_independent_of_ma3_and_space[0.6-True-LONG]`
- `tests.test_channel_position_path::test_raw_middle_signal_independent_of_ma3_and_space[0.6-True-SHORT]`
- `tests.test_channel_post_close_recovery::test_fresh_order_inside_channel_cancels_previously_valid_reclaim[LONG]`
- `tests.test_channel_post_close_recovery::test_fresh_order_inside_channel_cancels_previously_valid_reclaim[SHORT]`
- `tests.test_channel_post_close_recovery::test_inside_ma3_turn_closes_at_live_price_and_retries[False-False-LONG]`
- `tests.test_channel_post_close_recovery::test_inside_ma3_turn_closes_at_live_price_and_retries[False-False-SHORT]`
- `tests.test_channel_post_close_recovery::test_inside_ma3_turn_closes_at_live_price_and_retries[False-True-LONG]`
- `tests.test_channel_post_close_recovery::test_inside_ma3_turn_closes_at_live_price_and_retries[False-True-SHORT]`
- `tests.test_channel_post_close_recovery::test_inside_ma3_turn_closes_at_live_price_and_retries[True-False-LONG]`
- `tests.test_channel_post_close_recovery::test_inside_ma3_turn_closes_at_live_price_and_retries[True-False-SHORT]`
- `tests.test_channel_post_close_recovery::test_inside_ma3_turn_closes_at_live_price_and_retries[True-True-LONG]`
- `tests.test_channel_post_close_recovery::test_inside_ma3_turn_closes_at_live_price_and_retries[True-True-SHORT]`
- `tests.test_channel_post_close_recovery::test_legacy_outer_helper_cannot_exit_on_pre_entry_turn[LONG]`
- `tests.test_channel_post_close_recovery::test_legacy_outer_helper_cannot_exit_on_pre_entry_turn[SHORT]`
- `tests.test_channel_post_close_recovery::test_missing_ck_data_does_not_delay_valid_ma3_close[LONG]`
- `tests.test_channel_post_close_recovery::test_missing_ck_data_does_not_delay_valid_ma3_close[SHORT]`
- `tests.test_channel_post_close_recovery::test_old_ticket_migration_discards_old_pullback[False-LONG]`
- `tests.test_channel_post_close_recovery::test_old_ticket_migration_discards_old_pullback[False-SHORT]`
- `tests.test_channel_post_close_recovery::test_old_ticket_migration_discards_old_pullback[True-LONG]`
- `tests.test_channel_post_close_recovery::test_old_ticket_migration_discards_old_pullback[True-SHORT]`
- `tests.test_channel_post_close_recovery::test_profit_close_cannot_reopen_until_later_pullback_and_reclaim[True-inside-LONG]`
- `tests.test_channel_post_close_recovery::test_profit_close_cannot_reopen_until_later_pullback_and_reclaim[True-inside-SHORT]`
- `tests.test_channel_post_close_recovery::test_profit_close_cannot_reopen_until_later_pullback_and_reclaim[True-outside-LONG]`
- `tests.test_channel_post_close_recovery::test_profit_close_cannot_reopen_until_later_pullback_and_reclaim[True-outside-SHORT]`
- `tests.test_channel_post_close_recovery::test_profitable_inside_ma3_exit_persists_wait_before_closing[False-LONG]`
- `tests.test_channel_post_close_recovery::test_profitable_inside_ma3_exit_persists_wait_before_closing[False-SHORT]`
- `tests.test_channel_post_close_recovery::test_profitable_inside_ma3_exit_persists_wait_before_closing[True-LONG]`
- `tests.test_channel_post_close_recovery::test_profitable_inside_ma3_exit_persists_wait_before_closing[True-SHORT]`
- `tests.test_channel_profit_only_exit::test_unarmed_holds_and_clears_old_pending_requests[closed_waterfall-LONG]`
- `tests.test_channel_profit_only_exit::test_unarmed_holds_and_clears_old_pending_requests[closed_waterfall-SHORT]`
- `tests.test_channel_profit_only_exit::test_unarmed_holds_and_clears_old_pending_requests[double-LONG]`
- `tests.test_channel_profit_only_exit::test_unarmed_holds_and_clears_old_pending_requests[double-SHORT]`
- `tests.test_channel_profit_only_exit::test_unarmed_holds_and_clears_old_pending_requests[waterfall-LONG]`
- `tests.test_channel_profit_only_exit::test_unarmed_holds_and_clears_old_pending_requests[waterfall-SHORT]`
- `tests.test_channel_profit_protection::test_engine_passes_live_frame_and_preserves_exit_priority[EXIT]`
- `tests.test_channel_profit_protection::test_engine_passes_live_frame_and_preserves_exit_priority[HOLD]`
- `tests.test_channel_profit_protection::test_engine_passes_live_frame_and_preserves_exit_priority[REVERSE]`
- `tests.test_channel_profit_protection::test_first_opposite_tick_already_beyond_ten_percent_exits[LONG]`
- `tests.test_channel_profit_protection::test_first_opposite_tick_already_beyond_ten_percent_exits[SHORT]`
- `tests.test_channel_profit_protection::test_general_exit_has_priority_over_profit_close`
- `tests.test_channel_profit_protection::test_reopened_position_reclassifies_without_previous_ten_percent_lock[CHOPPY]`
- `tests.test_channel_profit_protection::test_reopened_position_reclassifies_without_previous_ten_percent_lock[SMOOTH]`
- `tests.test_channel_profit_protection::test_stack_live_opposite_tightens_ten_dollar_peak_to_nine[LONG]`
- `tests.test_channel_profit_protection::test_stack_live_opposite_tightens_ten_dollar_peak_to_nine[SHORT]`
- `tests.test_channel_profit_protection::test_tightening_on_opposite_tick_honors_previously_observed_peak`
- `tests.test_channel_profit_room::test_losing_position_uses_real_protection_then_middle_exit[False]`
- `tests.test_channel_profit_room::test_losing_position_uses_real_protection_then_middle_exit[True]`
- `tests.test_channel_profit_room::test_unarmed_long_middle_exit[False-100.0-False-True]`
- `tests.test_channel_profit_room::test_unarmed_long_middle_exit[False-99.9-False-True]`
- `tests.test_channel_profit_room::test_unarmed_long_middle_exit[True-100.0-False-True]`
- `tests.test_channel_profit_room::test_unarmed_long_middle_exit[True-99.9-False-True]`
- `tests.test_channel_reentry_body_confirmation::test_actual_profit_protection_gap_can_finish_negative_after_fees`
- `tests.test_channel_reentry_body_confirmation::test_confirmed_body_and_live_successor_survive_restart`
- `tests.test_channel_reentry_body_confirmation::test_valid_reentry_still_checks_latest_price`
- `tests.test_channel_requested_fixes::test_adverse_long_body_exit_by_side[-0.1--4.0-2.0-False-LONG]`
- `tests.test_channel_requested_fixes::test_adverse_long_body_exit_by_side[-0.1--4.0-2.0-False-SHORT]`
- `tests.test_channel_requested_fixes::test_adverse_long_body_exit_by_side[0.0--4.0-2.0-False-LONG]`
- `tests.test_channel_requested_fixes::test_adverse_long_body_exit_by_side[0.0--4.0-2.0-False-SHORT]`
- `tests.test_channel_requested_fixes::test_adverse_long_body_exit_by_side[0.2--0.4-0.2-True-LONG]`
- `tests.test_channel_requested_fixes::test_adverse_long_body_exit_by_side[0.2--0.4-0.2-True-SHORT]`
- `tests.test_channel_requested_fixes::test_adverse_long_body_exit_by_side[0.2--3.0-2.0-True-LONG]`
- `tests.test_channel_requested_fixes::test_adverse_long_body_exit_by_side[0.2--3.0-2.0-True-SHORT]`
- `tests.test_channel_requested_fixes::test_long_pullback_ticket_survives_and_reopens_only_on_reclaim`
- `tests.test_channel_requested_fixes::test_outer_reentry_long_entry_live_body_and_ck_direction[none]`
- `tests.test_channel_requested_fixes::test_outer_reentry_short_entry_needs_two_real_red_bodies[99.0]`
- `tests.test_channel_requested_fixes::test_scan_mirrored_middle_exit_is_not_suppressed[False-LONG]`
- `tests.test_channel_requested_fixes::test_scan_mirrored_middle_exit_is_not_suppressed[False-SHORT]`
- `tests.test_channel_requested_fixes::test_scan_mirrored_middle_exit_is_not_suppressed[True-LONG]`
- `tests.test_channel_requested_fixes::test_scan_mirrored_middle_exit_is_not_suppressed[True-SHORT]`
- `tests.test_channel_requested_fixes::test_short_two_closed_bodies_reopen_without_new_low`
- `tests.test_channel_retracement_twenty::test_twenty_percent_migrates_existing_peak_without_losing_ten[True-LONG-1]`
- `tests.test_channel_retracement_twenty::test_twenty_percent_migrates_existing_peak_without_losing_ten[True-SHORT--1]`
- `tests.test_channel_surge_entry::test_recovery_releases_old_surge_after_effective_green[broken]`
- `tests.test_channel_surge_entry::test_recovery_releases_old_surge_after_effective_green[chase]`
- `tests.test_channel_surge_entry::test_recovery_releases_old_surge_after_effective_green[equal_low]`
- `tests.test_channel_surge_entry::test_recovery_releases_old_surge_after_effective_green[valid]`
- `tests.test_channel_surge_entry::test_recovery_releases_old_surge_after_effective_green[wick_break]`
- `tests.test_channel_surge_entry::test_recovery_scan_and_real_order_revalidate[none]`
- `tests.test_channel_surge_entry::test_reentry_keeps_ticket_and_freshness_guards[normal]`
- `tests.test_channel_surge_entry::test_released_surge_no_longer_caps_price_at_old_confirmation[False]`
- `tests.test_channel_surge_entry::test_released_surge_no_longer_caps_price_at_old_confirmation[True]`
- `tests.test_channel_surge_release::test_release_uses_current_alignment_without_requiring_a_trough`
- `tests.test_channel_swing::test_aligned_trend_can_enter_without_breakout_body_confirmation`
- `tests.test_channel_swing::test_favorable_waterfall_live_turn_exits_on_long_adverse_body[LONG]`
- `tests.test_channel_swing::test_favorable_waterfall_live_turn_exits_on_long_adverse_body[SHORT]`
- `tests.test_channel_swing::test_flat_scan_can_detect_recent_upper_peak_without_exit_state`
- `tests.test_channel_swing::test_live_price_above_upper_rail_waits_without_valid_closed_body`
- `tests.test_channel_swing::test_long_does_not_lock_without_ma_cross`
- `tests.test_channel_swing::test_outer_reentry_later_clean_continuation_can_enter_after_spike_wait`
- `tests.test_channel_swing::test_outer_reentry_macro_trend_entry_long_on_upper_kc_structure_break`
- `tests.test_channel_swing::test_outer_reentry_macro_trend_entry_short_on_lower_kc_structure_break`
- `tests.test_channel_swing::test_outer_reentry_normal_two_candle_breakout_remains_tradable`
- `tests.test_channel_swing::test_outer_reentry_spike_breakout_is_not_traded`
- `tests.test_channel_swing::test_outside_long_signal_waits_after_opposite_closed_body`
- `tests.test_channel_swing::test_peak_exit_requires_normal_two_bar_reversal_before_short`
- `tests.test_channel_swing::test_short_does_not_lock_without_ma_cross`
- `tests.test_channel_swing::test_sudden_ma15_upper_rail_jump_is_not_convergence`
- `tests.test_channel_symmetric_rules::test_adverse_long_body_exits_before_middle[LONG]`
- `tests.test_channel_symmetric_rules::test_adverse_long_body_exits_before_middle[SHORT]`
- `tests.test_channel_symmetric_rules::test_energy_decline_alone_does_not_block_developing_move[LONG]`
- `tests.test_channel_symmetric_rules::test_energy_decline_alone_does_not_block_developing_move[SHORT]`
- `tests.test_channel_symmetric_rules::test_final_order_rechecks_room_for_both[None-LONG]`
- `tests.test_channel_symmetric_rules::test_final_order_rechecks_room_for_both[None-SHORT]`
- `tests.test_channel_symmetric_rules::test_final_order_rechecks_room_for_both[reopen-LONG]`
- `tests.test_channel_symmetric_rules::test_final_order_rechecks_room_for_both[reopen-SHORT]`
- `tests.test_channel_symmetric_rules::test_outer_reentry_two_closed_bodies_and_live_direction[none-LONG]`
- `tests.test_channel_symmetric_rules::test_real_losing_position_middle_and_retry[False-LONG]`
- `tests.test_channel_symmetric_rules::test_real_losing_position_middle_and_retry[False-SHORT]`
- `tests.test_channel_symmetric_rules::test_real_losing_position_middle_and_retry[True-LONG]`
- `tests.test_channel_symmetric_rules::test_real_losing_position_middle_and_retry[True-SHORT]`
- `tests.test_channel_three_red_entry::test_order_gate_cannot_reuse_three_red_after_alignment_changes[False]`
- `tests.test_channel_three_red_entry::test_order_gate_cannot_reuse_three_red_after_alignment_changes[True]`
- `tests.test_channel_three_red_entry::test_reentry_uses_first_of_three_as_breakout_time[True]`
- `tests.test_channel_three_red_entry::test_small_middle_red_allows_entry_and_reentry`
- `tests.test_channel_three_red_entry::test_snapshot_relabels_lost_breakout_and_rechecks_direction`
- `tests.test_channel_three_red_entry::test_three_red_profit_reentry_preserves_other_gates[middle_doji]`
- `tests.test_channel_three_red_entry::test_three_red_profit_reentry_preserves_other_gates[normal]`
- `tests.test_channel_three_red_entry::test_three_red_profit_reentry_preserves_other_gates[recovered]`
- `tests.test_channel_three_red_entry::test_three_red_rejects_incomplete_or_invalid_confirmation[first_small]`
- `tests.test_channel_three_red_entry::test_three_red_rejects_incomplete_or_invalid_confirmation[live_inside]`
- `tests.test_channel_three_red_entry::test_three_red_rejects_incomplete_or_invalid_confirmation[middle_doji]`
- `tests.test_channel_three_red_entry::test_three_red_rejects_incomplete_or_invalid_confirmation[middle_green]`
- `tests.test_channel_three_red_entry::test_three_red_rejects_incomplete_or_invalid_confirmation[no_cross]`
- `tests.test_channel_three_red_entry::test_three_red_rejects_incomplete_or_invalid_confirmation[third_inside]`
- `tests.test_channel_three_red_entry::test_three_red_rejects_incomplete_or_invalid_confirmation[third_small]`
- `tests.test_channel_trend_hold::test_compression_boundary_with_closed_ma3_reentry[0.3-EXIT-LONG]`
- `tests.test_channel_trend_hold::test_compression_boundary_with_closed_ma3_reentry[0.3-EXIT-SHORT]`
- `tests.test_channel_trend_hold::test_compression_boundary_with_closed_ma3_reentry[0.4-EXIT-LONG]`
- `tests.test_channel_trend_hold::test_compression_boundary_with_closed_ma3_reentry[0.4-EXIT-SHORT]`
- `tests.test_channel_trend_hold::test_entry_channel_width_preserves_compression_reference[LONG]`
- `tests.test_channel_trend_hold::test_entry_channel_width_preserves_compression_reference[SHORT]`
- `tests.test_channel_trend_hold::test_exit_then_repeated_scans_do_not_reopen_old_signal[False-LONG]`
- `tests.test_channel_trend_hold::test_exit_then_repeated_scans_do_not_reopen_old_signal[False-SHORT]`
- `tests.test_channel_trend_hold::test_exit_then_repeated_scans_do_not_reopen_old_signal[True-LONG]`
- `tests.test_channel_trend_hold::test_exit_then_repeated_scans_do_not_reopen_old_signal[True-SHORT]`
- `tests.test_channel_trend_hold::test_expanding_gap_prevents_compression_exit[gaps1-EXIT-LONG]`
- `tests.test_channel_trend_hold::test_expanding_gap_prevents_compression_exit[gaps1-EXIT-SHORT]`
- `tests.test_channel_trend_hold::test_expanding_gap_prevents_compression_exit[gaps2-EXIT-LONG]`
- `tests.test_channel_trend_hold::test_expanding_gap_prevents_compression_exit[gaps2-EXIT-SHORT]`
- `tests.test_channel_trend_hold::test_expanding_gap_prevents_compression_exit[gaps3-EXIT-LONG]`
- `tests.test_channel_trend_hold::test_expanding_gap_prevents_compression_exit[gaps3-EXIT-SHORT]`
- `tests.test_channel_trend_hold::test_live_gap_widening_does_not_override_closed_exit[LONG]`
- `tests.test_channel_trend_hold::test_live_gap_widening_does_not_override_closed_exit[SHORT]`
- `tests.test_channel_trend_hold::test_post_exit_gate_is_checked_again_at_order_time[LONG]`
- `tests.test_channel_trend_hold::test_post_exit_gate_is_checked_again_at_order_time[SHORT]`
- `tests.test_channel_trend_hold::test_structure_failure_needs_two_closed_prices_and_adverse_channel[True-LONG]`
- `tests.test_channel_trend_hold::test_structure_failure_needs_two_closed_prices_and_adverse_channel[True-SHORT]`
- `tests.test_channel_two_closed_entry::test_cached_or_fresh_signal_cannot_bypass_invalid_candle[False-LONG]`
- `tests.test_channel_two_closed_entry::test_cached_or_fresh_signal_cannot_bypass_invalid_candle[False-SHORT]`
- `tests.test_channel_two_closed_entry::test_cached_or_fresh_signal_cannot_bypass_invalid_candle[True-LONG]`
- `tests.test_channel_two_closed_entry::test_cached_or_fresh_signal_cannot_bypass_invalid_candle[True-SHORT]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[doji--2-LONG]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[doji--2-SHORT]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[doji--3-LONG]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[doji--3-SHORT]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[nan--2-LONG]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[nan--2-SHORT]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[nan--3-LONG]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[nan--3-SHORT]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[opposite--2-LONG]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[opposite--2-SHORT]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[opposite--3-LONG]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[opposite--3-SHORT]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[small_body--2-LONG]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[small_body--2-SHORT]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[small_body--3-LONG]`
- `tests.test_channel_two_closed_entry::test_closed_bodies_qualify_breakout_but_not_aligned_trend[small_body--3-SHORT]`
- `tests.test_channel_two_closed_entry::test_order_snapshot_falls_back_to_trend_and_rechecks_alignment[LONG]`
- `tests.test_channel_two_closed_entry::test_order_snapshot_falls_back_to_trend_and_rechecks_alignment[SHORT]`
- `tests.test_channel_upward_exit_release::test_release_enters_normal_route_without_bypassing_order_gates[none-cached]`
- `tests.test_channel_upward_exit_release::test_release_enters_normal_route_without_bypassing_order_gates[none-fresh]`
- `tests.test_channel_upward_exit_release::test_release_enters_normal_route_without_bypassing_order_gates[none-scan]`
- `tests.test_direct_break_execution::test_daily_halt_keeps_new_order_blocked`
- `tests.test_direct_break_execution::test_failure_retries_real_open_on_next_scan_without_new_body_break`
- `tests.test_direct_break_execution::test_locked_short_upper_break_closes_even_when_new_long_rejected`
- `tests.test_direct_break_execution::test_outer_entry_fills_without_chop_evaluation[False-LONG]`
- `tests.test_direct_break_execution::test_outer_entry_fills_without_chop_evaluation[False-SHORT]`
- `tests.test_direct_break_execution::test_outer_entry_fills_without_chop_evaluation[True-LONG]`
- `tests.test_direct_break_execution::test_outer_entry_fills_without_chop_evaluation[True-SHORT]`
