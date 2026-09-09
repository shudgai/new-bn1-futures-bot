# 峰谷開倉與平倉去重發布驗證

- 一般新倉採 MA15 三根順向＋已收線價格峰谷與 MA3 轉向。
- 峰谷倉中軌出口需先越過中軌，再回到中軌；保存跨重啟狀態。
- 平倉鎖防並行送單；策略失敗 30 秒後重試，真正手動平倉保留即時重試。
- 平倉成交後帳戶同步失敗仍回報成功；持倉同步不重建平倉中的舊倉，也丟棄平倉完成前發出的過期回應。
- 移除五個沒有執行引用的舊通道 helper；現行反手與獲利重開保留。
- UI 移除 K 線成交金額，更新策略說明。

## 驗證

主回歸命令涵蓋 close_deduplication、channel_pivot_entry、channel_swing、channel_position_path、channel_swing_execution、channel_requested_fixes、channel_profit_protection、channel_ma3_outer_exit、channel_profit_room、channel_retracement_twenty、channel_symmetric_rules、testnet_account：421 passed, 2 skipped。

confirmed_rules / stop_preservation 另行執行，仍有修改前相同的 20 項失敗，不能宣稱全套通過。這兩份測試未修改。

git diff --check 通過。模擬交易、假交易所測試不對外送單。

發布目標：origin 的 feature/rr-sl-monitoring；服務 binance-8006.service（8006）。部署前 API is_running=true、paper_trading=true。GitHub 推送與重啟結果以本次對話最終回報為準。
