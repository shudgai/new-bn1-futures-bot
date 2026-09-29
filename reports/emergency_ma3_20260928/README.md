# 緊急修復結果

- 根因：entry_firewall.py引用不存在的assess_market_regime，profit_protection_service.py只有trend_style；不是循環匯入。已補齊使用已收線資料的相容介面，並維持先前授權的強破位／IGNITION風控豁免。
- 強趨勢MA3出口：辨識到順向MA15、KC三軌與沿外軌行情後，保護旗標持久化。多單須收盤嚴格低於MA15或KC中軌，空單對稱，才允許原MA3全平條件執行。僅碰線、影線或MA3小轉彎不足以觸發。初始ATR硬止損及保本止損仍獨立保留。
- 實作預設：回看當前確認棒之前最多六根已收線，尋找兩根MA15及三軌均順向、收盤距順向外軌內側不超過0.5ATR且仍在MA15順向側的組合；保護不因後續兩根小回踩失效。
- 驗證：82項pytest通過；core/services compileall通過。涵蓋全新程序匯入、實際防火牆、多空MA15/中軌確認、碰線、未收線、重啟恢復、獨立硬止損及既有開倉鏈。
- 最後重啟：UTC 2026-09-28 22:56:01，使用者層級binance-8006.service active/running、NRestarts=0，API HTTP200，紙上模式持續運行。重啟後捕獲日誌未再出現ImportError；不宣稱已測過全專案歷史測試。

修改檔案：core/services/entry_firewall.py、core/services/exits/profit_protection_service.py、core/services/exits/dual_track_exit_service.py、tests/test_emergency_ma3_trend_hold.py。

本輪差異：[changes.diff](changes.diff)。測試：[tests.log](tests.log)。崩潰證據：[errors_before.log](errors_before.log)。重啟：[service.txt](service.txt)、[runtime_after.log](runtime_after.log)、[status_after.json](status_after.json)。
