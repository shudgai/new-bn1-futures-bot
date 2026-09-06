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

### KC Macro Trend Strategy 不可變規格 (原 Channel Swing)
- **大趨勢判定**：必須比較當下、30根前、60根前的 MA15，確認中軌有階梯式上/下坡。
- **順勢回踩進場 (Pullback)**：大趨勢向下找 MA3 峰頂做空；大趨勢向上找 MA3 谷底做多。
- **逆勢拐點進場 (Inflection)**：當 MA15 呈現 U型底/倒U型頂 (15根前 MA15 出現最低/最高點)，第一時間逆勢開倉。
- **均線排列濾網**：無論上述哪種進場，都必須滿足 `收盤價 < MA3 < MA15` (空) 或 `收盤價 > MA3 > MA15` (多) 才能真正發動，避免 V 型洗盤接刀。
- **死抱與三重防護網 (提早平倉)**：進場後無視一般震盪，直到：
  1. MA15 短線 (15根內) 發生轉折 (MA15_SHORT_TERM_REVERSAL)。
  2. 收盤價強勢貫穿對側外軌 (KC_UPPER/LOWER_BREAKOUT)。
  3. 即時價格突破前 20 根 K 線的最高/最低點結構 (STRUCTURE_BREAK)。
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
