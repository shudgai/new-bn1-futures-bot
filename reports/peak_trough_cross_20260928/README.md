# PEAK_TROUGH_CROSS 早鳥入口

已新增多空對稱的 `CLOSED_PEAK_TROUGH_CROSS_LONG/SHORT`，與既有 IGNITION／TREND_BREAKOUT／TREND_CRAWLING 並存。

## 量化定義

- N 固定 8，使用交叉確認棒之前的 8 根已收線 K；總共需要 9 根有效且時間連續的 1 分鐘 K。
- 多單：前 8 根至少一根 Low <= 該根 KC 下軌；前根 MA3 <= MA15、確認根 MA3 > MA15；確認根為陽線且實體／全長 >= 40%；收盤 <= KC 上軌。
- 空單：前 8 根至少一根 High >= 該根 KC 上軌；前根 MA3 >= MA15、確認根 MA3 < MA15；確認根為陰線且實體／全長 >= 40%；收盤 >= KC 下軌。
- 價格等於外軌允許此模式，均線確認根僅相等不算交叉。交叉棒自身的碰軌不算前置峰谷，8 根之前的碰軌不算；沒有未收線或未來資料參與。
- 多單初始 SL 固定在前 8 根最低 Low，空單固定在前 8 根最高 High；止損必須在進場價格的風險側。成交滑價不平移峰谷價，也不覆寫為 1.5 ATR。

依本次授權，此模式是先前「多單必須收在上軌外」及突破專用通道／CHOPPY 檢查的明確例外。新模式先以完整的碰軌＋收線交叉＋實體條件驗證；既有突破模式仍接受原做多趨勢濾網。已越過持倉側外軌則回到既有突破入口評估，不強制保證成交。

## 完整鏈路

`unified_entry_strategy.py` 新增 `peak_trough_cross` 與訊號白名單；`entry_firewall.py` 對此模式重新執行相同策略，保留訊號新鮮度、確認棒 identity、最新行情與兩根平倉冷卻檢查。引擎保留帳戶提交鎖、持倉限額、資金、報價、每日熔斷、既有幣種白名單及同棒去重。

`paper_account.py` 與 `testnet_account.py` 從帳戶入口重驗結果取得結構 SL；`entry_atr_protection.py`／`exit_service.py` 支援指定初始 SL。測試網成交落帳以顯式參數傳遞峰谷價；若成交價已跨過此 SL，走緊急平倉流程而不捏造另一條止損。

MA15 結構防守、固定進場 ATR 的 1.2 ATR 成本保本與原帳戶硬止損沿用。帳戶硬止損仍可能早於峰谷 SL 觸發。成本保本不代表扣費及滑點後必然淨利為零。

## 驗證

```text
.venv/bin/python -m pytest tests/test_peak_trough_cross.py tests/test_long_entry_trend_filter.py tests/test_swing_defense_policy.py tests/test_emergency_ma3_trend_hold.py tests/test_red_eye_entry_pressure.py tests/test_entry_e2e_20260928.py tests/test_order_sizing.py tests/test_startup_guard.py -q
152 passed in 9.63s
.venv/bin/python -m compileall core tests/test_peak_trough_cross.py
exit=0
```

新增 38 項案例，涵蓋多空訊號、無碰軌／過期碰軌、偽交叉、相等、反色、影線、資料無效、時間缺口、未收線、碰軌邊界、40% 邊界、兩根冷卻、最新行情失效、實際掃描到紙上成交與同棒去重、峰谷 SL 在紙上及 mock 測試網的保留、成本保本與止損已被穿越時拒絕建倉。未向真實交易所送測試單。

已備份紙上帳戶並沿用授權重啟 8006。MainPID=3655619、active/running、NRestarts=0；API is_running=true、paper_trading=true。重啟後初次觀察 71 行 journal 無 ImportError、Traceback、ERROR、DANGER。

測試、編譯、服務與 API 證據在本目錄。`before/` 保存本輪接手版本，`changes.diff` 僅記錄本輪 7 個程式檔的變動；未將工作區其他既有改動一併提交。根規範沿用本會話快取，引用的 AIDAN 檔案在本機缺失。
