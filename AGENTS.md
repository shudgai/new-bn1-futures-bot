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

### KC Macro Trend Strategy 現行規格（2026-09-07 用戶更新）
- MA15 大方向順勢進場；使用可用歷史判斷，不要求完整 60 根資料。
- 多單：MA3 回踩谷底或確認上軌突破；空單：MA3 反彈峰頂或確認下軌突破。
- 外軌突破：前根收盤在通道內，本根實體由軌內開盤、收盤穿出；影線與未收盤 K 不算。
- 新突破改為即時進場 (Live Breakout)：突破前一根收在通道內，且即時價穿出同側外軌，並確認 MA3/MA15 已形成同向交叉，不再等待 K 線收盤確認，直接於當輪送入結構化市價路由。保留既有異常行情、同向上限、停損冷卻、價格安全與帳戶風控。
- 反手與新倉使用相同的實體穿軌加下一根收盤確認；僅收在軌外、實體由軌外開盤不得反手。確認完成後先平舊倉，確認平倉成功，再送反向候選。
- 真突破同輪先平舊倉，確認成功後立即送新倉；失敗只在原確認根仍有效且價格仍在對側外軌時重送，保留帳戶安全檢查；確認過期或回到通道內取消重送資格。
- 手動平／開倉清除舊反手與候選狀態；保存接管標記，掃描持倉時更新 bot_last_managed_at。
- 多、空單瀑布、單根或連續異常 K 等皆不直接平倉，而是會「提早觸發鎖利防護」（與 MA 交叉鎖利相同），並將止損線移至當下價格。後續皆由移動止損及原有固定止損進行保護（待確認對側外軌實體突破才反手）。
- 多單 MA3 下穿 MA15，SL 設當下價下方滑價緩衝；空單 MA3 上穿 MA15，SL 設當下價上方滑價緩衝。包含正在形成的 K 線交叉，當輪立即鎖損益；SL 只往有利方向移動。
- MA3 恢復原方向且重新站回通道後，解除本次交叉鎖損益 SL（同步交易所），保留原有固定止損。背景程序不得清零固定或交叉 SL、initial_sl 與 initial_risk。
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
