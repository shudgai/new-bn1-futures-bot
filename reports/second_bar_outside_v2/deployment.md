# 第二根入口部署紀錄

- 提交：`7d750dd`，分支 `refactor/clean-trend-v2`。
- 訊息：`feat: implement unified 2nd-bar intraday outside KC entry with strict concurrency locks`。
- 已依序提交、執行 `systemctl --user restart binance-8006.service`、檢查最新30行日誌。
- 服務啟動：Tue 2026-09-29 23:33:48 UTC。
- 最後服務狀態：NRestarts=0；ActiveState=active；SubState=running。
- 本輪僅提交至本地分支，未執行推送。

## 即時觀察

從 2026-09-29 23:34:48 UTC 至 2026-09-29 23:37:48 UTC 連續19次API採樣，龍蝦與1000PEPE行情時間及價格持續更新。所有採樣均 is_running=true、paper_trading=true。日誌自此次服務啟動起檢查，未發現 Traceback／ImportError／NameError／SyntaxError／ERROR／DANGER；自動重啟次數0。

PEPE空單已通過第二根形態條件並到達盈虧估算，但被 BLOCKED_EXPECTED_NET_REWARD 攔截；龍蝦空單被 BLOCKED_REWARD_RISK 攔截。共記錄9筆此類日誌事件，不能將日誌事件數當作獨立交易機會數。

觀察期間 SECOND_BAR_OUTSIDE 放行碼、ACCOUNT_SUBMIT、FILLED 均為0，持倉0，累計交易筆數維持6。因此已確認服務及入口評估運行，但尚未以自然行情驗證放行後的送單成交全鏈路；沒有修改風控或人工送單製造驗證結果。既有280項隔離測試與自然行情驗證分開計算。

機器人服務持續運行；本次人工觀察視窗於上述時間結束。原始採樣：deployment_monitor.json；篩選日誌：deployment_events.log（依儲存庫設定不追蹤.log）。此部署紀錄保留於本地，程式提交後未再改動。
