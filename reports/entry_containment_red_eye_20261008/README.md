# entry-containment-v2 紅眼測試

版本：f3365aaf902b49d4d1ff56f85821e5e764ef4d59。僅測試，原始碼未修改，8006 未切換。

- 紅眼 36 項：5 通過、31 失敗。使用 my_package.red_eye_adapter:create_strategy 正式轉接器，沒有用 reference 結果冒充正式通過。
- 26 項因 Legacy source changed 轉接器錨點不符，未進入測試策略；1 項舊緊急瀑布 API 已停用與測試預期不同；4 項舊並發測試未通過（包含缺失 submit lock 屬性）。
- 補充執行測試：明確設定 DEFAULT_SYMBOLS=龍蝦/CAP（實際 symbol 為龙虾/USDT,CAP/USDT），228 通過、58 失敗，286 項總計。
- CAP/龍蝦專用 Gate 測試：181/181 通過。共用訊號一致性13/13、單次平倉2/2、訂單邊界4/4、訂單額度24/24通過。
- 其餘58失敗：舊 v2 邊界22、舊專用送單路由12、舊 structured 路由24。包含 core/services/strategies/pure_trend_v2.py:344 引用未定義 held_resistance_short（僅該處引用）；專用 MA5 送單函式缺失與已退休訊號被拒絕。不能把所有失敗都視為現在 CAP/龍蝦主流程缺陷，也不能宣稱所有交易路徑已通過。

原始 junit 與 logs 在同目錄。initial execution.log 受外部 DEFAULT_SYMBOLS 僅龍蝦設定影響（210過76失敗），以 execution_two_symbols.log 為正式雙幣補充結果。

## 失敗項目修正（2026-10-08）

- 修正空單拒絕診斷引用未定義變數。
- 恢復明確啟用 staged risk 的逐幣分流，不落回舊出口；一般持倉不自動啟用。
- 正式紅眼轉接器對接目前核心、原生估值與真正 staged dispatcher。
- 舊 PEPE／已退休策略路由測試改為目前 CAP 合法入口及退休入口拒單驗證；未恢復過期入口。
- 補齊離線 REST 替身的 create_order 介面（誤走舊送單路徑直接失敗），減倉透過既有 runtime.request_reduce 並核對帳戶帳本。
- 原先 12 個模組加 1 項空單回歸：323 通過；補充 staged implementation／離線 REST integration：36 通過。合計 359 通過、0 失敗，不代表整個儲存庫全套已通過。
- 20 項核心契約與 reference fixture 未修改；沒有 skip、xfail 或 reference 取代正式核心。
- 8006 未切換、未重啟；運行版本仍是 2684305。離線測試未連線交易所或送出真實訂單。

修正後原始輸出：entry_containment_fixed.log/xml、entry_staged_integration_fixed.log/xml。歷史失敗紀錄保留。

修正提交：`8be7707`（entry-containment-v2）；原基準 `f3365aa` 仍為祖先。
