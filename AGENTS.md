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

### KC Macro Trend Strategy 現行規格（2026-09-08 用戶更新）
- MA15 大方向順勢進場；使用可用歷史判斷，不要求完整 60 根資料。
- 開多：嚴格等待第一根K線實體穿出上軌，且第二根K線收盤確認為綠K（真突破），才會開倉。
- 開空：嚴格等待第一根K線實體穿出下軌，且第二根K線收盤確認為紅K（真突破），才會開倉。
- 持倉與平倉：進場後，中間無論發生任何大紅大綠、均線交叉、暴漲暴跌，皆不提早平倉（已廢除固定停利、提早鎖利防護與 MA 交叉平倉）。必須一直死抱單子，直到撞上「對向的 CK 外側」並且同樣發生「真突破確認」時，才會同時進行「平倉 ＋ 反手開新倉」。
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
