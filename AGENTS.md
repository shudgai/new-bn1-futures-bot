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

### Channel Swing 不可變規格
- 多單：倒數第二根已收盤綠 K 的最低點觸及 KC 下軌，且下一根突破其高點。
- 空單：倒數第二根已收盤紅 K 的最高點觸及 KC 上軌，且下一根跌破其低點。
- 下一根若先突破候選 K 的反方向極值，訊號取消；候選只限緊接的一根 K。
- 即時外軌突破例外：空手時現價大於等於 KC 上軌立即開多，現價小於等於 KC 下軌立即開空；不等收盤、不看 K 色，也不套用 `0.5%` KC 寬度門檻。CHOP_WAIT、已有持倉／掛單與下單安全檢查仍保留。
- 除上述即時外軌突破例外外，禁止加入外側區收盤比例、未收盤 K 顏色、碰軌直接開倉或 KC 撕裂復原旁路。
- Channel Swing 持倉禁止中途停損、加碼及未確認的提前反手。
- KC 外軌趨勢追單必須多空鏡像：上軌上升追多、下軌下降追空；門檻、確認、取消與過熱回測規則的修改必須同步涵蓋 LONG／SHORT 並各自測試。
- 修改上述規格必須取得用戶明確指示，並通過 `tests/test_channel_swing.py`。

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
