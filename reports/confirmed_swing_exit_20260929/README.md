# 真峰谷唯一常規出口

使用最近左右各一根已收線確認的嚴格峰谷。多單1M收盤嚴格跌破谷底、空單嚴格突破峰頂才全平；碰線、盤中影線、無峰谷、無效行情均不新增常規出口。峰谷及右側確認棒須先於突破棒；最新突破棒開盤時間不得早於持倉進場時間，避免拿進場前已收線訊號追溯平倉。峰谷本身可來自進場前的可用歷史行情。

PureTrendStrategyV2、DualTrackExitStrategy相容介面及帳戶更新共用此規則。移除MA3、MA15、長影線、吞噬與浮盈回吐20%的獨立常規出口。舊策略closed_exit_state待平撤銷；既有硬止損待平保留，新峰谷與緊急待平持久化並重試。

保留初始價格止損、帳戶保證金與價格硬止損、1.5ATR瀑布、1.2ATR反向異常及真實BTC急跌熔斷。緊急ATR取最近已收線值。BTC一般1M方向不再冒充急跌；真實BTC熔斷不再跳過Channel Swing。有效逐報價先執行硬止損，再進入行情與策略處理。

實際機制是WebSocket報價到達即評估，不能保證行情、網路與成交延遲合計0.1秒。未修改入口與盈虧過濾。

驗證：153 passed in 2.71s；包含 confirmed_swing_exit_v2、strict_third_bar_v2、v2_execution_boundary、pre_entry_pnl_filter、order_sizing、startup_guard。py_compile及git diff --check成功。測試涵蓋多空破位、碰線／影線、缺失／無效峰谷、舊待平撤銷、硬止損、熔斷邊界及runner失敗持久化重試。測試未發出交易所訂單。

沿用環境限制：AIDAN所列規範及舊test_channel_swing.py缺失，不宣稱全庫測試通過。
