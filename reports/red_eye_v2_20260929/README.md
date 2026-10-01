# 現行V2紅眼測試

受測版本：1367781，分支refactor/clean-trend-v2。結論：本輪現行策略回歸與新增併發壓力186項通過，未重現重複下單、跨幣超額開倉或例外後鎖死。這不是全庫測試通過或實盤獲利證明。

## 現行測試

執行 test_red_eye_v2_pressure、test_continuation_reentry_v2、test_confirmed_swing_exit_v2、test_strict_third_bar_v2、test_v2_execution_boundary、test_pre_entry_pnl_filter、test_order_sizing、test_startup_guard：186 passed in 4.49s，完整輸出 current_tests.log。

新增壓力測試使用實際TradingEngine及隔離PaperAccount：同棒2／50／200並發只成交一次；雙幣競爭單一名額不能超額；注入帳戶例外後提交鎖釋放，另一幣仍能成交；行情證據排除未收線棒。跨幣名額案例以延遲模擬成交隔離資金不足干擾。其餘既有V2測試涵蓋兩根冷卻、成功平倉資格、成交後消耗、亂序紀錄還原、第三根50%邊界、盈虧不足、峰谷唯一常規出口、緊急門檻及待平重試。重啟還原為測試中重建物件，不冒充本輪實際重啟。

## 舊測試差異

原 test_red_eye_entry_pressure.py 加 test_red_eye_stress.py：4 failed, 6 passed in 1.92s，完整輸出 legacy_tests.log。4項壓力失敗均使用舊CLOSED訊號，被BLOCKED_OBSOLETE_ENTRY_SIGNAL拒絕，未到達其預期成交／提交鎖階段。保留原測試不修改；新增現行訊號對應案例補驗相同行為。舊stress資料缺少現行時間戳等契約，通過不能作為現行策略安全證明。

未執行參考staged策略作為生產放行證據。未宣稱舊規範缺失的test_channel_swing.py等測試通過。

## 運行狀態

唯讀檢查服務自2026-09-29 23:03:32 UTC啟動後journal：指定NameError／Traceback／SyntaxError／ERROR／DANGER匹配數0。API is_running=true、paper_trading=true，檢查時無持倉。runtime_summary.json保存摘要。

仍有診斷限制：普通未成交仍統一顯示WAIT_PURE_TREND_V2，無法僅由這個字串區分第三根形態、延續條件或盈虧過濾；本輪未將其解讀為策略故障，也未宣稱已還原截圖時點行情。

py_compile及git diff --check通過。測試沒有發送交易所訂單，沒有變更策略或帳戶狀態，沒有重啟服務。原有兩個未追蹤patch腳本未修改。
