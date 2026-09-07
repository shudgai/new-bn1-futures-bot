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
- 新倉須通過量能、利潤空間、價格安全及帳戶風控，沿用市價／結構化訂單路由。
- 空單遇綠 K 實體上破上軌平空開多；多單遇紅 K 實體下破下軌平多開空。先確認平倉成功，再送反向候選。
- 反向開倉失敗且價格仍在對側外軌，後續直接重送並保留開倉風控；回到通道內取消重送資格。
- 多單瀑布、連續異常 K、固定止損及帳戶風控仍可直接平倉。空單單根異常 K／單次 ticker 急漲不直接平倉，MA3 上穿 MA15 時先鎖損益；後續上軌實體真突破才觸發原本的平空／反手流程。
- 空單 MA3 上穿 MA15，SL 設當下價上方滑價緩衝；多單 MA3 下穿 MA15，SL 設當下價下方滑價緩衝。SL 只往有利方向移動。
- MA3 恢復原方向且重新站回通道後，解除本次交叉鎖損益 SL（同步交易所），保留原有固定止損。空單以 MA3 與已收盤價格皆回到 MA15 下方為準，不另要求跌破 KC 中軌。
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
