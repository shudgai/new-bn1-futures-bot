# 現行出口續驗紀錄

## 2026-09-10 取消同根價格回調等待並發布

- 龍蝦先前 21:47 至 21:48 與 PEPE 保存日誌均出現 KC_INTRABAR_PULLBACK_WAIT；另有 MA_ALIGNMENT_WAIT 與 SUSTAINED_TREND_WAIT，不能將所有未成交都歸因於回調。
- 新倉、突破、延續、快取及重開取消同根先回調再轉向要求；保留報價時效、方向、反向異常、結構淨利空間、每根限次及異常平倉票據的專用回踩規則。
- 送單測試 24 組驗證第一個有效報價即可成交，涵蓋多空、突破／延續、即時反色、新倉／快取／正常重開，使用真实結構目標檢查；另 8 組報價時效檢查。這是離線帳戶替身送單驗證，不代表實際服務已自動成交。
- 十份專項測試 244 passed / 6 failed；增加 8 組報價測試後該檔 32 passed。6 項失敗均位於 test_channel_profit_room.py 的舊中軌出口預期，未為通過而恢復中軌出口。
- 指定五份回歸加獲利保護 164 passed / 80 failed，不能宣稱全套通過。
- 同次發布包含先前未提交的每次進場結構淨利空間檢查與 MA3 真正線方向反轉修正；不要求 MA3 碰軌或穿軌，保留 0.10 ATR 幅度與保護分流。

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

## 2026-09-10 進場當根反向異常修正

- 當根原始開盤價計算反向異常／瀑布，不改用成交價重新累計；只平原倉。
- 執行 channel_swing、channel_position_path、channel_swing_execution、channel_immediate_exits、channel_protected_only_exit、close_deduplication 六份測試。
- 修改前 173 passed / 71 failed；修改後 187 passed / 71 failed，失敗清單完全相同。新增案例涵蓋多空、成交價未再移動、普通 K 不出場與失敗重試。未宣稱全套通過。

## 2026-09-10 送單前反向異常攔截

- 共用 live_adverse_entry_safe：當根原開盤與最新價形成反向異常／瀑布則新倉與重開皆等待；最終送單另重驗最新 ticker，快取不能繞過。無效 ATR 禁入，小反色與順向實體不因本檢查禁入。
- 新增 tests/test_channel_adverse_entry.py 共 30 項通過，含多空、門檻邊界、行情無效、最新價、快取與重開。
- 本輪指定回歸 channel_swing、channel_position_path、channel_swing_execution、channel_outside_continuation、channel_immediate_exits：修改前 157 passed / 71 failed；加上新測試後 187 passed / 71 failed，失敗清單完全相同。

## 2026-09-10 以用戶兩條出場分流核查

- 實作先更新 protection，再依 armed 分流：已啟動不執行異常／瀑布／MA3 helper，並清除相關待平旗標；未啟動依既有異常／瀑布與進場後 MA3 轉彎判斷出場。
- 專項驗證 scratch/test_exit_policy_audit.py 加既有回吐與異常失敗重試案例：36 passed。涵蓋多空、未啟動、同次報價剛啟動、已啟動、達回吐線及舊待平旗標。
- 細節：保護仍取淨利 1 USDT 底線、保留峰值 80% 與既有更有利保護價；因此不是每次都必須回吐滿 20%。MA3 同根進場須先實際觀察順向再反向。
- 8006 API is_running=true / paper_trading=true，服務目錄吻合。API 策略說明仍是舊版「異常／MA3不單獨平倉」，與未啟動分流不一致。此次只核查並記錄，未修改交易邏輯或重啟。

## 2026-09-10 移除單根反向異常 K 出口

- 刪除 LIVE_ADVERSE_ABNORMAL 觸發與重試，清除持倉及 metadata 的該待平標記。保留瀑布、雙異常、MA3、獲利保護及送單前異常攔截。
- 逐報價、待平清理與保護專項 64 passed。指定回歸修改前 161 passed / 65 failed，修改後含新測試 171 passed / 65 failed，無新增失敗。

## 2026-09-10 單向走勢篩選

- 已加入最近 6 根已收線的 CK 持續順向、高低點推進、方向效率、中軌穿越與實體重疊篩選；具體預設见 AGENTS.md 最新節。掃描、新倉、重開及快取送單共用，持倉出口不變。
- tests/test_channel_sustained_trend.py：26 passed，涵蓋多空有效趨勢、波浪／重疊／回調／資料無效拒絕、即時 K 隔離與快取及重開攔截。
- 指定回歸含 channel_swing、channel_position_path、channel_swing_execution、single_abnormal_removed、immediate_exits：修改前 159 passed / 65 failed；加新測試後 185 passed / 65 failed，失敗清單相同。
- 同步 API 策略文字為現行入口與保護分流。未使用圖示後續走勢擬合，無歷史績效改善或保證獲利結論。

## 2026-09-10 GitHub 發布驗證與破軌雙入口

- 最後授權：有效上下軌突破可略過新增六根走勢篩選；單向延續仍受篩選。共用其他進場風控，出口不變。
- 現行專項 sustained_trend、adverse_entry、single_abnormal_removed、immediate_exits、exit_policy、close_deduplication：168 passed。
- 指定五份舊回歸 swing、position_path、swing_execution、confirmed_rules、stop_preservation：138 passed / 69 failed；失敗清單與隔離 HEAD 基準完全相同。未宣稱全套通過。
- 首輪兩項重疊測試因資料同時符合新增破軌例外而失敗，已改用非破軌重疊案例且專項重跑通過。異常進場測試更新成符合單向條件的行情，未放寬策略。
- 提交包含現行程式、API說明、授權與驗證、正式測試；scratch 修補腳本與歷史暫存 audit 不納入。


## 2026-09-10 移除殘留 MA3 出口與恢復 Channel Swing 硬止損

- 使用者授權修正、邏輯測試、直接重啟及推送 GitHub。移除未啟動保護時 MA3 轉彎平倉分支並清除持倉／metadata 舊待平欄位；保留瀑布、雙異常、獲利回吐與進場規則。
- 新增共用 channel_hard_stop：逐報價持倉路徑與紙上／測試網 update_positions 在提前略過策略出口之前，檢查既有保證金 10% 與價格逆向 2% 門檻，先達先平。75 USDT、5 倍約對應毛虧損 7.5 USDT；費用及跳價可能使最終淨損更大。屬本地市價平倉，未新增交易所掛單。
- 用實際數量計算毛損，保護已啟動也不豁免；保存待平狀態，metadata 重啟恢復後行情回復仍重試，沿用帳戶平倉鎖／既有失敗重試規則。硬止損不建立獲利重開票據。
- 專項 181 passed（hard_stop、immediate_exits、single_abnormal_removed、close_deduplication、candle_frequency、sustained_trend、adverse_entry）。涵蓋多空、兩個門檻、同根報價、兩種帳戶、MA3 待平清除、無效報價與待平恢復。
- 指定五份回歸：修改前與後均 138 passed / 69 failed，失敗名稱完全相同；獲利保護 26 passed / 11 failed，11 項皆在本文件已有失敗清單。總計 345 passed / 80 failed，不能宣稱全套通過。
- 原始測試結果：/tmp/exit-fix-before.txt、/tmp/exit-fix-after.txt、/tmp/exit-fix-targeted.txt、/tmp/exit-fix-profit.txt。

- 部署核查：2026-09-10 12:57:28 UTC 重啟 binance-8006.service；/api/status 回報 is_running=true、paper_trading=true、port=8006，策略文字已包含「MA3轉彎不平倉」。


## 恢復明顯 MA3 峰谷反轉（2026-09-10）
- 使用者要求恢復明顯峰谷反向平倉、小幅抖動不平。實作預設反向幅度 0.10 ATR：首次進場後有效觀察固定上一根已收線 ATR，MA3 用兩根已收線收盤及最新價計算；先觀察順向新極值，從極值反向達門檻才平。首次報價不追溯進場前峰谷。
- 僅未啟動保護持倉適用；硬止損優先，瀑布／雙異常與已啟動獲利回吐不變。峰谷及待平狀態寫入持倉與metadata，重啟恢復；舊無幅度 MA3 旗標仍清除。
- 專項196 passed；指定五份回歸加獲利保護164 passed / 80 failed，失敗名稱與上輪完全相同，無新增失敗。總計360 passed / 80 failed，非全套通過。
- 輸出：/tmp/ma3-restore-targeted.txt、/tmp/ma3-restore-regression.txt。

- 部署驗證：8006重啟後 is_running=true、paper_trading=true，API策略說明已更新為MA3峰谷反向0.10 ATR。
