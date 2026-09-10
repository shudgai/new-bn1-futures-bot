# CK 外軌突破唯一入口驗證

本輪最終規則：兩根已收線同色實體各至少全長 20%，第一根實體穿出上下外軌，第二根收在同側軌外；最新價嚴格在同側外軌外。已收線 MA3／MA15／KC 順向且即時 MA3 以報價重算嚴格順向。所有新倉、重開及送單快取共用，沒有順勢、中軌、谷底替代入口。

普通反色與中軌、單根普通長 K、確認反手不單獨平倉。保留 ATR 大瀑布／雙異常、未保護時 MA3 進場後轉彎、20% 保護及帳戶硬停損。MA3 失敗待平保留重試；當根進場需先實際觀察順向。既有淨利 1 USDT 啟動及既存更有利保護線不放寬。

## 測試

- 新增外軌唯一入口測試 60 項通過：多空、軌內／碰軌、即時 MA3 持平／反向、兩根實體、無穿軌、快取與新行情、重開、20% 與 MA3 重試。
- 相關九份執行與安全回歸合計 242 項通過，包括 aligned_entry、protected_only_exit、live_ma3_exit、candle_frequency、close_deduplication、intrabar_execution、swing_execution、stop_preservation。
- 使用實際服務 .venv/bin/python3，加上 AGENTS.md 指定 swing、position_path、confirmed_rules：371 通過、69 失敗。失敗集中於仍要求其他入口、中軌／普通反色出口或確認反手，以及舊訊號代碼／舊資料；不是全套通過。不為符合舊斷言放寬本輪規則。
- py_compile 通過。git diff --check 仍報工作開始前已存在的四處空白（channel_pivot_entry 與 engine），不涉及執行行為。
- 本輪未取得圖片精確時間／原始行情，不能宣稱已逐筆重播圖片成交。

## 指定舊回歸失敗清單

```
FAILED tests/test_channel_swing.py::test_outer_reentry_macro_trend_entry_short_on_lower_kc_structure_break
FAILED tests/test_channel_swing.py::test_outer_reentry_macro_trend_entry_long_on_upper_kc_structure_break
FAILED tests/test_channel_swing.py::test_outer_reentry_normal_two_candle_breakout_remains_tradable
FAILED tests/test_channel_swing.py::test_aligned_trend_can_enter_without_breakout_body_confirmation
FAILED tests/test_channel_swing.py::test_outer_reentry_spike_breakout_is_not_traded
FAILED tests/test_channel_swing.py::test_outside_long_signal_waits_after_opposite_closed_body
FAILED tests/test_channel_swing.py::test_live_price_above_upper_rail_waits_without_valid_closed_body
FAILED tests/test_channel_swing.py::test_outer_reentry_later_clean_continuation_can_enter_after_spike_wait
FAILED tests/test_channel_swing.py::test_favorable_waterfall_live_turn_exits_on_long_adverse_body[LONG]
FAILED tests/test_channel_swing.py::test_favorable_waterfall_live_turn_exits_on_long_adverse_body[SHORT]
FAILED tests/test_channel_swing.py::test_peak_exit_requires_normal_two_bar_reversal_before_short
FAILED tests/test_channel_swing.py::test_flat_scan_can_detect_recent_upper_peak_without_exit_state
FAILED tests/test_channel_swing.py::test_sudden_ma15_upper_rail_jump_is_not_convergence
FAILED tests/test_channel_swing.py::test_long_does_not_lock_without_ma_cross
FAILED tests/test_channel_swing.py::test_short_does_not_lock_without_ma_cross
FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.39-LONG]
FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.39-SHORT]
FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.4-LONG]
FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.4-SHORT]
FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.41-LONG]
FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.41-SHORT]
FAILED tests/test_channel_position_path.py::test_normal_opposite_break_requires_postentry_path[LONG]
FAILED tests/test_channel_position_path.py::test_normal_opposite_break_requires_postentry_path[SHORT]
FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.25-False-LONG]
FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.25-False-SHORT]
FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.25-True-LONG]
FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.25-True-SHORT]
FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.5-False-LONG]
FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.5-False-SHORT]
FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.5-True-LONG]
FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.5-True-SHORT]
FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.6-False-LONG]
FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.6-False-SHORT]
FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.6-True-LONG]
FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.6-True-SHORT]
FAILED tests/test_channel_position_path.py::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.0-False-True-LONG]
FAILED tests/test_channel_position_path.py::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.0-False-True-SHORT]
FAILED tests/test_channel_position_path.py::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.2-False-True-LONG]
FAILED tests/test_channel_position_path.py::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.2-False-True-SHORT]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.39-LONG]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.39-SHORT]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.4-LONG]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.4-SHORT]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.4106594857-LONG]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.4106594857-SHORT]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.5-LONG]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.5-SHORT]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.6-LONG]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.6-SHORT]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.39-LONG]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.39-SHORT]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.4-LONG]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.4-SHORT]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.4106594857-LONG]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.4106594857-SHORT]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.5-LONG]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.5-SHORT]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.6-LONG]
FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.6-SHORT]
FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1--0.1-LONG]
FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1--0.1-SHORT]
FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.0-LONG]
FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.0-SHORT]
FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.1-LONG]
FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.1-SHORT]
FAILED tests/test_channel_confirmed_rules.py::test_confirmed_signal_fills_once_on_same_scan[True-LONG]
FAILED tests/test_channel_confirmed_rules.py::test_confirmed_signal_fills_once_on_same_scan[True-SHORT]
FAILED tests/test_channel_confirmed_rules.py::test_rejected_reverse_retry_expires_with_bar[LONG]
FAILED tests/test_channel_confirmed_rules.py::test_rejected_reverse_retry_expires_with_bar[SHORT]
```

## 部署

已重啟 binance-8006.service，systemd 顯示 active/running；/api/status 回傳 is_running=True，策略說明已更新為僅 CK 上下外軌突破。
