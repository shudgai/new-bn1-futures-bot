# 開倉前預估盈虧過濾

依使用者指定公式新增 PureTrendStrategyV2.verify_profitable_expectation：成本為進場價格乘 0.003，預估淨獲利為 1.5 ATR 減成本。多單估算防守點 max(上軌 - 0.2 ATR, MA15)，空單 min(下軌 + 0.2 ATR, MA15)；風險為 max(方向性止損距離, 成本) + 成本。淨獲利 <= 0 或 Reward/Risk < 1.2 拒絕，1.2 邊界允許。無效方向、價格、ATR、MA15 或通道資料拒絕。

收線第三根、延續及盤中第三根入口共用；盤中 ATR 取上一根已收線值，收線入口取該已收線棒。evaluate_v2_frame 另以送單最新報價、最新通道與最近已收線 ATR 重驗，因此掃描、引擎、帳戶與交易所包裝均繼承攔截。

結構防守點僅供估算，未變更現行實際止損及持倉出口。1.5 ATR 為使用者指定的假設目標，未估計到達機率，不能據此宣稱正交易期望值或保證盈利。

驗證：90 passed in 2.26s。測試為 test_pre_entry_pnl_filter、test_v2_execution_boundary、test_order_sizing、test_startup_guard；另通過 py_compile 與 git diff --check。涵蓋正負淨空間、1.2 邊界、多空、低價幣比例、失效資料、盤中 ATR 來源及送單前惡化拒單。既有成交測試的通道改為滿足新風益比之有效案例，另有成本與距離不足的拒單案例，未停用過濾以換取測試通過。

沿用上一輪已確認的環境限制：AIDAN 指定規範與舊 test_channel_swing.py 缺失，未宣稱完整舊回歸通過。
