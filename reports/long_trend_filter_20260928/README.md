# 做多趨勢濾網與龍蝦成交鑑識

已將做多入口改為共用兩層門檻；空單與持倉出口不變。

1. 最新已收線收盤必須嚴格高於同根 KC 上軌，碰軌或軌內一律拒絕。
2. 以下三者至少其一成立：MA15 最近三根已收線連續兩次嚴格上升；最近兩根收盤各自嚴格高於同根 KC 中軌；KC 三軌最近三根均連續兩次嚴格上升。

使用者已明確確認 MA15 採連續兩次上升，不採單次拐頭。第二層適用全部自動做多模式，包括 IGNITION、TREND_BREAKOUT、TREND_CRAWLING，沒有強訊號豁免；原有明確反向 CK 攔截等其他風控仍保留，因此三項擇一通過不表示保證成交。這是已收線確認，未額外新增送單即時價必須在上軌外的規則。

## 原始成交證據

`lobster_trade.json` 保存紙上成交 id=1790637602059，介面時間 09/29 07:20:02（UTC 09/28 23:20:02），原因為 `Closed1M CLOSED_IGNITION_LONG`，不是 PULLBACK 或獨立 Oversold Bounce 模式。

送單保存的確認棒：收盤 0.07020、上軌 0.07012815275；當時確實已收線站上上軌。最近三根 MA15 為 0.069772 → 0.0697626667 → 0.0697853333，只上升一次。前根收盤 0.06980 低於中軌 0.06985422146，沒有兩根站上中軌；KC 三軌亦未連續向上。因此新規則明確回傳 `BLOCKED_LONG_REVERSAL_UNCONFIRMED`，掃描與送單防火牆皆拒絕此歷史快照。

舊邏輯只以最近一次中軌及持倉側外軌變化判斷 CK，IGNITION 另豁免 MA3 斜率及額外通道擴張要求，未檢查上述持續反轉證據。截圖的「尚未連續同向」及「破上軌還差」不等於歷史成交確認棒狀態，不能據此宣稱當時軌內送單。

## 程式與驗證

- `core/services/strategies/unified_entry_strategy.py`：新增 `long_entry_trend_problem`，在挑選任何 LONG 模式前檢查。
- `core/services/entry_firewall.py`：在 IGNITION 豁免前調用相同門檻；帳戶取得最新行情後再次執行，可拒絕失效快取。
- `tests/test_long_entry_trend_filter.py`：11 項新案例涵蓋歷史成交、三條反轉確認、強訊號不可豁免、碰軌／軌內、未收線、帳戶最新行情重驗及空單保留。

```text
.venv/bin/python -m pytest tests/test_long_entry_trend_filter.py tests/test_swing_defense_policy.py tests/test_emergency_ma3_trend_hold.py tests/test_red_eye_entry_pressure.py tests/test_entry_e2e_20260928.py tests/test_order_sizing.py tests/test_startup_guard.py -q
114 passed in 8.33s
.venv/bin/python -m compileall core tests/test_long_entry_trend_filter.py
exit=0
```

沿用服務重啟授權，備份紙上帳戶後載入新版；active/running，MainPID=3651679、NRestarts=0。API is_running=true、paper_trading=true；重啟後初次觀察 46 行 journal 無 ImportError、Traceback、ERROR、DANGER。未手動送單或清倉。

原始證據、測試、部署健康狀態及本輪差異均在本目錄。`changes.diff` 僅比較本輪接手前後兩個程式檔，不混入先前未提交改動。根規範沿用本會話快取，其引用 AIDAN 檔案仍未取得。
