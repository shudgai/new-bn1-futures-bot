# AGENTS.md — AI 助理規則（Binance Futures Bot 2.0）

## ⚠️ 禁止修改的核心邏輯

以下函式是**手動精調過的交易策略核心**，AI 在沒有用戶明確指示的情況下，**絕對不能修改**：

### `core/strategy.py`
- `evaluate_signal()` — 進場訊號判斷邏輯（MA7 Pivot 谷底/峰頂）
- `confirm_pullback_entry()` — 回調確認邏輯
- `check_simple_ma7_exit()` — MA7 出場條件
- `detect_simple_ma7_signal()` — MA7 訊號偵測

### `core/config.py`
- 所有 `INITIAL_BALANCE`、`LEVERAGE`、`TRADE_AMOUNT_USDT` 等參數
- `SIGNAL_LEVERAGE_CAPS` — 槓桿上限矩陣

### `core/engine.py`
- 主迴圈邏輯（`run_cycle()`）
- 止損止利觸發邏輯
- `_channel_swing_action()` — Channel Swing 唯一進場／反手判定入口

### KC Macro Trend Strategy 現行規格（2026-09-09 用戶明確授權更新）
- MA15 大方向順勢進場；使用可用歷史判斷，不要求完整 60 根資料。
- 開多：嚴格等待第一根K線實體穿出上軌，且第二根K線收盤確認為綠K（真突破），才會開倉。
- 開空：嚴格等待第一根K線實體穿出下軌，且第二根K線收盤確認為紅K（真突破），才會開倉。
- 對向 CK 外軌實體突破加下一根同色收線確認，優先執行平倉與反手。
- 用戶明確授權恢復條件式反轉平倉：先有正向大瀑布（實體達 2 倍既有長K門檻），或至少連續兩根正向長K，再緊接反向長K／大瀑布／兩根反向長K，立即平倉；可採即時價格確認實體，不要求等待即時K收盤。既有長K門檻為 ATR × RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR，預設 0.5 ATR。單根或數根小K不累加成正向大走勢。
- 最近三根已收線的 MA15 到持倉側 CK 外軌距離連續擴大（空單下軌、多單上軌）時，優先續抱，抑制反轉與趨勢提早平倉；不覆蓋對向外軌真突破反手。保留既有 MA3 連續在軌外且 MA15 距外軌至少半通道的反轉續抱條件。
- 保留現有已收線通道壓縮加 MA3 回軌、兩根結構失效出場；不恢復固定停利或單純 MA 交叉平倉。
- 提早平倉後等待新的 CK 外軌實體突破及下一根同色收線確認，不能沿用舊訊號或峰谷直接重開；平倉失敗仍保留原持倉。
- 反手與新倉使用相同的實體穿軌加下一根同色收盤確認。
- 真突破同輪先平舊倉，確認成功後立即送新倉；失敗保留帳戶安全檢查。
- 手動平／開倉清除舊反手與候選狀態；保存接管標記，掃描持倉時更新 bot_last_managed_at。
- 不再以 20 根結構破位或 MA15 短線反轉單獨平倉；不使用獨立逆勢 U 型進場。
- 修改上述規格須取得用戶明確指示，並通過 `tests/test_channel_swing.py` 及相關執行測試。

---

## ✅ 可以修改的部分

| 檔案 | 可修改內容 |
|------|-----------|
| `web/index.html` | 介面 UI、顯示欄位、按鈕 |
| `services/api.py` | 新增 API endpoint |
| `core/paper_account.py` | 帳戶功能、重置邏輯 |
| `core/symbol_rotation.py` | 幣種輪替邏輯 |
| `.env` | 環境設定（API key、port 等） |

---

## �� 工作流程規則

1. **修改前先讀檔案** — 任何修改前都要先 view_file 確認目前內容
2. **不執行 `rewrite.py`** — 此腳本已停用（改名為 `.bak`），禁止執行
3. **不做 `git reset --hard`** — 除非用戶明確要求
4. **不覆蓋整個檔案** — 使用局部修改，不要替換整個檔案
5. **修改後要告知用戶** — 說明改了哪一行、改了什麼，方便追蹤

---

## 🗂️ 專案結構簡介

```
new bn/
├── core/
│   ├── strategy.py      ← 交易策略核心（禁止亂改）
│   ├── engine.py        ← 主引擎（禁止亂改）
│   ├── config.py        ← 所有設定參數（禁止亂改）
│   ├── paper_account.py ← 模擬帳戶
│   └── testnet_account.py ← Binance Testnet 帳戶
├── services/
│   └── api.py           ← FastAPI 後端
├── web/
│   └── index.html       ← 前端介面
└── data/
    └── paper_account.json ← 帳戶持久化（bot 自動維護）
```
