# 開倉、平倉與鎖利測試 QA 審查

- 審查日期：2026-10-25（Asia/Taipei）
- 專案：`new bn`
- 分支：`feature/kc-1m-continuation-gates`
- 審查 HEAD：`34641af0`
- 測試框架：Pytest（Python 3.12）
- 測試資料隔離：`tests/conftest.py` 將 testnet 帳戶與獲利遙測檔案隔離，並在每個測試阻止寫入生產 testnet 狀態檔。

## 結論

專案已有大量開倉、平倉、止損及鎖利測試，透過關鍵字盤點到 142 個相關測試檔。覆蓋面包括多空對稱、破軌／延續／重入、倉位與錢包限制、精度與名目金額、出場去重、縮量平倉、網路逾時後對帳、淨利扣費後階梯鎖利、重新載入與硬止損優先級。

現有測試**未達完整且可信的綠燈狀態**：

1. 全量收集發現 5,214 個測試可收集，但另有 8 個測試檔在收集階段失敗，因匯入不存在的 helper、舊 API 或已移除函式。
2. 18 個高相關測試檔執行結果為 **357 passed、110 failed、2 skipped**。失敗分散在新舊入場期待、出場策略、測試夾具與舊 API，不能統稱成單一功能故障；它們需要逐檔確認是被新授權策略取代，還是仍代表實際回歸。
3. 另外 5 個執行邊界／會計測試檔初次為 **86 passed、3 failed**。追查後並發案例的測試夾具混入目前禁用幣種與無效訊號，已隔離槽位競態條件；一個 Net ROE ExitGate 優先序案例是實際程式缺陷，已修正。舊 `peak_roe` 階梯期待仍需按現行策略釐清。
4. 此輪修正兩個實際缺陷：缺失或零數量被當成 1 張合約計算淨 ROE；Net ROE 階梯鎖利訊號被一般持倉保護 VETO。新增測試涵蓋無效倉位數量、價格與溢位。

## 執行記錄

### 全量收集

執行：`.venv/bin/pytest --collect-only -q`

結果：**5,214 collected，8 collection errors**。錯誤檔案與原因：

- `tests/test_doji_entry_cleanup.py`、`tests/test_doji_same_color_confirmation.py`、`tests/test_live_third_entry.py`：依賴不存在的 `test_entry_without_wick_filter`。
- `tests/test_gate_strict_bot.py`：從 `gate_strict_bot` 匯入不存在的 `PositionState`。
- `tests/test_kc_band_expansion_gate.py`：匯入不存在的 `kc_band_expansion_reason`。
- `tests/test_kc_pullback_entry_guard.py`：匯入不存在的 `PULLBACK_LONG_CODE`。
- `tests/test_ma_momentum_gate.py`：匯入不存在的 `ma_momentum_reason`。
- `tests/test_trend_continuation_hold.py`：匯入不存在的 `trend_continuation_hold`。

這些檔案雖然有測試意圖，但目前 pytest 根本不會執行它們，不能算有效覆蓋。

### 18 檔交易 Gate／帳戶／止盈止損測試

執行檔案：`test_breakout_continuation_entry_gate.py`、`test_strict_entry_contract.py`、`test_entry_submission_gate.py`、`test_entry_margin_per_slot.py`、`test_order_sizing.py`、`test_order_boundary.py`、`test_core_trading_edge_cases.py`、`test_testnet_account.py`、`test_close_deduplication.py`、`test_single_close_invariant.py`、`test_profit_lock_usdt.py`、`test_ratchet_net_profit_gate.py`、`test_realtime_profit_exit.py`、`test_same_bar_and_profit_lock_holding_protection.py`、`test_top_bottom_reversal_exit.py`、`test_kc_true_extreme_exit.py`、`test_state_machine_and_unified_exit_gate.py`、`test_three_point_pivot_real_trades.py`。

結果：**357 passed、110 failed、2 skipped**。主要失敗型態包括：

- 破軌／延續舊案例預期訊號可通過，但目前策略 Gate 拒絕；有些 firewall 測試仍只接受舊的 `VETO_` 錯誤字串。
- account／close-dedup 測試中多個並發與部分平倉案例失敗，涉及帳戶方法、mock 介面或目前鎖倉語意，需查清後再分級。
- 部分 `KC true extreme`、同棒保護與利潤鎖案例依賴舊出場政策；在確認是否仍屬產品授權前，不應為了讓舊測試通過而放寬或改動出口。

### 執行／會計邊界

執行檔案：`test_order_sizing.py`、`test_entry_margin_per_slot.py`、`test_core_trading_edge_cases.py`、`test_entry_submission_gate.py`、`test_unified_net_roe_profit_lock.py`。

結果：**86 passed、3 failed**。

- 通過包含委託量精度與步進、無效價格、最小名目金額、可用餘額與手續費預留、禁止開倉開關、重複開倉、倉位競態、淨 ROE 階梯與會計一致性等案例。
- 失敗：並發兩槽入倉預算預期 `[True, True]`、實際 `[False, False]`；舊 `peak_roe` 70% 保護預期與當前淨 ROE 階梯語意不同；ExitGate 整合的一個鎖利出口未按舊案例預期觸發。

### 本輪新增邊界測試

`.venv/bin/pytest -q tests/test_net_roe_profit_lock.py`：**36 passed**。

新增案例防止零／缺失／NaN／Infinity 數量被當成一張合約、無效報價污染鎖利狀態，以及極大數量造成 Infinity/NaN 傳入鎖利門檻。實作現在遇到無效倉位規模、非有限價格／保證金／槓桿或非有限淨 ROE 時 Fail Closed，不會建立或更新鎖利狀態。

## 業務場景白話說明與邊際覆蓋

- **開倉**：測試確認破軌要符合 K 棒／通道條件、追價距離和趨勢方向；金額不可超過可用保證金與槽位預算；同一訊號不能重複開倉。訂單數量會按交易所最小步進、精度和最低名目金額計算。
- **平倉**：測試涵蓋硬止損、瀑布、KC 反側破軌、已收線真峰谷、盤中毛刺與普通回踩續抱。關閉單會做去重、reduce-only／部分平倉限制，並防止較舊的行情刷新把已平倉部位復活。
- **鎖利**：測試涵蓋手續費／滑價後淨 ROE、階梯切換、底線單調、峰值追蹤、邊界觸發、重啟恢復，以及鎖利和普通續抱規則互相優先級。
- **技術邊際**：零／空／NaN／Infinity 與過期行情已有部分覆蓋；訂單 API timeout／未知成交有 testnet mock 與 staged lifecycle 案例；開倉及平倉競態有併發測試。此輪補上鎖利數量與溢位案例。
- **商業邊際**：餘額與槽位預算、費用與滑價、重複提交、逆勢阻擋、部分平倉、手動平倉與只減倉已有測試。程式目前未見通用角色／權限系統，因此「Permission Deny」只能審查功能開關與 Gate 禁止操作，不能假設有使用者角色授權測試。金融產品沒有退款流程；相應風險由成交對帳、平倉、部分平倉和損益記帳案例涵蓋。

## 尚缺項目與後續建議

1. 修復 8 個不能收集的舊測試：改用共用 fixture／現行 API，或明確標記已淘汰並由現行行為測試取代。
2. 對兩組執行結果中的失敗案例逐一比對 `AGENTS.md` 最新授權，標記為現行回歸或舊政策測試；不要用放寬風控來迎合過時期望。
3. 釐清兩槽並發測試為何兩筆都拒絕；這是功能行為與測試前提的真實不一致，需新增明確的原因斷言，不能只驗證布林結果。
4. 補上真實 HTTP 身份／權限拒絕測試，前提是產品要提供角色或 token 權限；目前只有交易 submission 開關與業務 Gate。
5. 維持本輪新增的淨 ROE 無效數量／數值溢位測試，並將相同 fail-closed 約束延伸到其他會直接使用 qty、margin、leverage 的金額與平倉計算路徑。

### 開倉與入場驗證測試檔案（72 檔）

- `tests/test_anti_fomo_cross_entry.py`
- `tests/test_breakout_continuation_entry_gate.py`
- `tests/test_breakout_only_entry.py`
- `tests/test_breakout_pullback_entry.py`
- `tests/test_cap_entry_safety.py`
- `tests/test_central_entry_gate.py`
- `tests/test_central_entry_gate_e2e.py`
- `tests/test_closed_breakout_entry.py`
- `tests/test_conditional_ma_cross_entry.py`
- `tests/test_continuation_entry.py`
- `tests/test_continuation_failed_exit.py`
- `tests/test_continuation_reentry_v2.py`
- `tests/test_directional_entry_v2.py`
- `tests/test_doji_entry_cleanup.py`
- `tests/test_dual_entry_strategies.py`
- `tests/test_dual_track_exit_and_reentry.py`
- `tests/test_ema_cross_entry_service.py`
- `tests/test_entry_cleanup_regression.py`
- `tests/test_entry_decision_metadata.py`
- `tests/test_entry_e2e_20260928.py`
- `tests/test_entry_exit_position_edges.py`
- `tests/test_entry_gate_consistency.py`
- `tests/test_entry_latest_close_runtime.py`
- `tests/test_entry_margin_per_slot.py`
- `tests/test_entry_momentum_exhaustion.py`
- `tests/test_entry_pattern_permitted_gate.py`
- `tests/test_entry_prefilter_gate.py`
- `tests/test_entry_signal_calibration.py`
- `tests/test_entry_submission_gate.py`
- `tests/test_entry_timing_audit.py`
- `tests/test_entry_trend_alignment_gate.py`
- `tests/test_entry_v2_architecture.py`
- `tests/test_entry_veto_shadow.py`
- `tests/test_high_position_chase_and_defense.py`
- `tests/test_intrabar_midline_entry.py`
- `tests/test_kc_entry_immutability.py`
- `tests/test_kc_mid_defense_and_breakthrough_entry.py`
- `tests/test_kc_midline_entry_exit_gates.py`
- `tests/test_kc_pending_entry.py`
- `tests/test_kc_pullback_entry_guard.py`
- `tests/test_kc_two_bar_entry.py`
- `tests/test_late_chase_entry_guard.py`
- `tests/test_live_third_entry.py`
- `tests/test_long_entry_trend_filter.py`
- `tests/test_ma5_entry_slope.py`
- `tests/test_outer_turn_entry.py`
- `tests/test_peak_valley_stale_entry_prevention.py`
- `tests/test_pending_entry_trend_guard.py`
- `tests/test_pepe_entry_disabled.py`
- `tests/test_post_close_continuation.py`
- `tests/test_post_close_reentry_opportunity.py`
- `tests/test_pre_entry_pnl_filter.py`
- `tests/test_pre_entry_space_shadow.py`
- `tests/test_red_eye_entry_pressure.py`
- `tests/test_same_direction_reentry_cooldown.py`
- `tests/test_second_third_entry.py`
- `tests/test_short_entry_symmetry.py`
- `tests/test_short_false_breakout_exit.py`
- `tests/test_single_entry_funnel_gate.py`
- `tests/test_strict_entry_and_immunity_gate.py`
- `tests/test_strict_entry_contract.py`
- `tests/test_strict_entry_firewall.py`
- `tests/test_strict_entry_gates_live.py`
- `tests/test_strict_pivot_entry.py`
- `tests/test_target_canonical_entry.py`
- `tests/test_three_pure_entry_modes_and_meme_gate.py`
- `tests/test_trend_ride_and_entry_exit_timing.py`
- `tests/test_unblock_continuation_and_chart_intercept.py`
- `tests/test_wait_engine_entry.py`
- `tests/test_wait_entry_authority.py`
- `tests/test_wait_entry_firewall.py`
- `tests/test_wild_swing_entry_firewall.py`

### 平倉、止損、持倉保護測試檔案（61 檔）

- `tests/test_anti_fakeout_exit_guard.py`
- `tests/test_authorized_position_exit.py`
- `tests/test_channel_exit_grace.py`
- `tests/test_channel_exit_true_pivot_poll.py`
- `tests/test_channel_initial_atr_stop_disabled.py`
- `tests/test_channel_peak_profit_exit.py`
- `tests/test_channel_peak_reversal_exit.py`
- `tests/test_channel_position_hard_stop_disabled.py`
- `tests/test_close_deduplication.py`
- `tests/test_closed_bear_priority.py`
- `tests/test_closed_breakout_entry.py`
- `tests/test_confirmed_structure_exit.py`
- `tests/test_confirmed_swing_exit_v2.py`
- `tests/test_continuation_failed_exit.py`
- `tests/test_doji_following_pressure_exit.py`
- `tests/test_doji_reversal_exit.py`
- `tests/test_dual_track_exit_and_reentry.py`
- `tests/test_entry_exit_position_edges.py`
- `tests/test_entry_latest_close_runtime.py`
- `tests/test_exhaustion_exit.py`
- `tests/test_final_closed_policy.py`
- `tests/test_fixed_lock_peak_abnormal.py`
- `tests/test_hybrid_exit_and_holding_protection.py`
- `tests/test_intrabar_spike_rebound_exit.py`
- `tests/test_intraday_instant_exit.py`
- `tests/test_kc_mid_turn_exit.py`
- `tests/test_kc_midline_entry_exit_gates.py`
- `tests/test_kc_stoploss.py`
- `tests/test_kc_true_extreme_exit.py`
- `tests/test_lobster_exit_misclose_and_hold_audit.py`
- `tests/test_lobster_ma_exit_churn.py`
- `tests/test_lobster_only_and_same_bar_exit.py`
- `tests/test_ma5_turn_exit.py`
- `tests/test_mature_reversal_exit.py`
- `tests/test_mid_band_ma3_exit.py`
- `tests/test_phase4_native_stop_outage_lifecycle.py`
- `tests/test_phase5_exit_refactor.py`
- `tests/test_pivot_without_profit_lock.py`
- `tests/test_post_close_channel_impulse.py`
- `tests/test_post_close_continuation.py`
- `tests/test_post_close_reentry_opportunity.py`
- `tests/test_post_exit_formation.py`
- `tests/test_post_exit_pullback_gate.py`
- `tests/test_profit_exit_telemetry.py`
- `tests/test_profit_exit_telemetry_rotation.py`
- `tests/test_realtime_profit_exit.py`
- `tests/test_realtime_short_pullback_exit.py`
- `tests/test_same_bar_and_profit_lock_holding_protection.py`
- `tests/test_short_exit_and_direction_valve.py`
- `tests/test_short_exit_first_bar_ma5.py`
- `tests/test_short_false_breakout_exit.py`
- `tests/test_short_lower_break_exit_guard.py`
- `tests/test_short_oversold_exit.py`
- `tests/test_single_close_invariant.py`
- `tests/test_spike_reversal_and_breakeven_lock.py`
- `tests/test_state_machine_and_unified_exit_gate.py`
- `tests/test_strict_pivot_entry.py`
- `tests/test_top_bottom_reversal_exit.py`
- `tests/test_trend_ride_and_entry_exit_timing.py`
- `tests/test_true_peak_exit_without_midline.py`
- `tests/test_wild_swing_close_execution.py`

### 鎖利與獲利保護測試檔案（22 檔）

- `tests/test_5u_lock_and_pullback_defense.py`
- `tests/test_anti_whipsaw_profit_lock.py`
- `tests/test_channel_peak_profit_exit.py`
- `tests/test_early_profit_guard_integration.py`
- `tests/test_fixed_lock_peak_abnormal.py`
- `tests/test_lock_profit.py`
- `tests/test_model_t_profit_lock.py`
- `tests/test_net_roe_profit_lock.py`
- `tests/test_pivot_without_profit_lock.py`
- `tests/test_price_profit_floor.py`
- `tests/test_profit_exit_telemetry.py`
- `tests/test_profit_exit_telemetry_rotation.py`
- `tests/test_profit_floor.py`
- `tests/test_profit_lock_usdt.py`
- `tests/test_ratchet_net_profit_gate.py`
- `tests/test_realtime_profit_exit.py`
- `tests/test_same_bar_and_profit_lock_holding_protection.py`
- `tests/test_spike_reversal_and_breakeven_lock.py`
- `tests/test_tiered_profit_gate.py`
- `tests/test_two_step_ironclad_locks.py`
- `tests/test_unblock_continuation_and_chart_intercept.py`
- `tests/test_unified_net_roe_profit_lock.py`

### 下單、金額、併發與執行邊界測試檔案（13 檔）

- `tests/test_authorized_position_exit.py`
- `tests/test_channel_position_hard_stop_disabled.py`
- `tests/test_circuit_breaker.py`
- `tests/test_close_deduplication.py`
- `tests/test_entry_exit_position_edges.py`
- `tests/test_entry_submission_gate.py`
- `tests/test_high_position_chase_and_defense.py`
- `tests/test_order_boundary.py`
- `tests/test_order_sizing.py`
- `tests/test_specialized_order_routes.py`
- `tests/test_structured_order_routes.py`
- `tests/test_v2_execution_boundary.py`
- `tests/test_wild_swing_close_execution.py`


## 本輪追加驗證

- `.venv/bin/pytest -q --tb=short tests/test_entry_margin_per_slot.py tests/test_unified_net_roe_profit_lock.py tests/test_net_roe_profit_lock.py`：**80 passed**。
- `.venv/bin/pytest --collect-only -q --tb=short`：**5,214 collected，8 collection errors**，原因仍為上列缺失測試 helper／舊 API。嘗試把三個遺失 fixture 的匯入改接到另一個測試 fixture 後，語意與資料欄位不相容且造成 46 個舊案例失敗，因此已還原該試改；這些測試需要按現行策略重寫，不能用錯誤 fixture 掩蓋。
- `git diff --check`：通過。

## 追加：持倉過早平倉防護

依使用者最新要求，持倉尚未形成進場後確認的真峰／真谷時，普通均線破位、中軌轉折、meme 趨勢衰竭與 ATR 回吐標籤一律不可直接平倉。只有已驗證的淨 ROE 鎖利、已確認峰谷，以及硬止損／緊急風控出口可放行。`ExitGate` 與 `HoldingProtectionExitGate` 均加入此最後授權檢查；原始訊號可留作診斷，但不得直接執行。

- 新增 `tests/test_channel_swing_pivot_or_lock_only.py`，覆蓋普通退出標籤阻擋、真峰確認放行、已驗證鎖利放行、緊急止損保留，以及 `ExitGate.evaluate` 整合檢查。
- 本次聚焦執行：**88 passed**（鎖利、峰谷及新增出場授權測試）；Python 編譯與 `git diff --check` 通過。
