# 龍蝦 07:35–07:39 漏單查核

時間以 UTC+8 表示；主機為 UTC。使用者原始無時區指令查無資料，對應查詢為 2026-09-29 23:35:00 UTC 至 23:39:30 UTC。完整服務日誌存於 journal.log，龍蝦原文存於 lobster.log。

## 結論
07:36:03、07:36:27、07:36:34、07:37:03、07:37:06、07:39:03，龍蝦 SHORT 均記錄 BLOCKED_REWARD_RISK。07:36:03 reward=0.00006285、risk=0.0004263、ratio=0.1474313863，舊門檻為1.2；07:37:03 ratio=0.1572710316。

舊版7d750dd的evaluate_v2_frame先呼叫evaluate_second_bar_outside_entry，成功後才執行verify_profitable_expectation。因此上述拒絕證據證明第二根評估已執行並通過；不是未呼叫，也不是起爆實體閾值拒絕。07:35原始棒open=0.07129、close=0.07109、lower=0.0712086097，實體0.00020，即使與提問的0.00011495相比也較大。

服務07:33:48已重啟第二根版本（Python PID 9686），但仍含盈虧過濾。刪除盈虧過濾的2054ad6於07:42:05提交、07:42:17重啟，07:42:21日誌記錄龍蝦SHORT FILLED；API顯示紙上模式，不是交易所實單。

WAIT_PURE_TREND_V2只是無該方向候選時的通用碼，不代表每輪都因相同因素拒絕。該時段同時存在明確BLOCKED_REWARD_RISK與WAIT紀錄。純收線快照會在第二根評估前直接返回；日誌snapshot僅列已收線棒，不能單看其中is_closed就推定完整輸入。07:38之前一根07:37為陽棒，現行空單規則要求前根陰棒，故不能宣稱四根每一刻均符合入口。

## 本次處理與驗證
現行core程式沒有pre_entry_pnl_filter或verify_profitable_expectation呼叫，也沒有第一根最低實體/ATR起爆門檻；保留同向實體收軌外、第二根盤中軌外與MA排列及反向實體50%限制。無須重複修改已刪除的程式。

相關10份回歸檔重新執行：304 passed in 6.05s；git diff --check通過。AIDAN指定前置/Python/測試規範檔缺失，不宣稱全庫驗證。

依本次指示執行systemctl --user restart binance-8006.service，成功於2026-09-30 00:05:44 UTC（08:05:44 UTC+8）啟動，MainPID=23397、Python PID=23406、active/running。/api/status HTTP200、is_running=true、paper_trading=true。08:06:07另有PEPE自然行情FILLED，僅作服務執行證據，不當作龍蝦歷史補單或交易所成交。
