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
- 2026-09-09 最新確認（取代此前反轉／異常 K 出場規格）：開倉後，不以反色 K、單根大瀑布、兩根異常 K 或其他 K 線形態單獨平倉；不得恢復 `KC_ADVERSE_WATERFALL_EXIT`、`KC_TWO_ADVERSE_ABNORMAL_EXIT` 或單根大 K 即時反手。
- 一般平倉必須同時符合：本次持倉 MA3 曾出持倉側外軌後回到通道內、MA15 到持倉側外軌距離占通道寬度，以即時 K 計算嚴格不足 40%（用戶已撤回已收線比例的額外限制）、價格嚴格過中軌（多單跌破、空單突破）。40% 邊界、價格僅碰中軌、MA3 仍在軌外、空間仍有 50%／60% 都不能單獨觸發一般平倉。
- 保留當前已確認對向突破反手及緊急停損；不得新增獨立 MA3／MA15 交叉、結構失效、固定停利或最高浮盈回吐出場。
- 提早平倉後等待新的 CK 外軌實體突破及下一根同色收線確認，不能沿用舊訊號或峰谷直接重開；平倉失敗仍保留原持倉。
- 反手與新倉使用相同的實體穿軌加下一根同色收盤確認。
- 真突破同輪先平舊倉，確認成功後立即送新倉；失敗保留帳戶安全檢查。
- 手動平／開倉清除舊反手與候選狀態；保存接管標記，掃描持倉時更新 bot_last_managed_at。
- 不再以 20 根結構破位或 MA15 短線反轉單獨平倉；不使用獨立逆勢 U 型進場。
- 修改上述規格須取得用戶明確指示，並通過 `tests/test_channel_swing.py` 及相關執行測試。

---

## 已確認版本與後續變更限制（2026-09-09）

- 交易程式基準為 GitHub `feature/rr-sl-monitoring` 的 `92943ea`。用戶要求維持目前行為，不能以優化、整理、AI 建議或修測試為由改寫交易規則。
- 進場、追單、反手、候選失效鎖、重複下單保護、平倉及風控參數的行為變更，均需用戶明確指示。查詢「為何沒開倉」不能視為授權放寬進場或移除鎖；用戶已確認 1000PEPE 開倉，失效鎖未修改。
- 上方早期進場文字與基準實作存在差異（含即時外軌進場及軌外延續）；不得自行依舊文字改回，也不得將目前實作描述為已完整符合舊規格。需要變更時先列明實際觸發與差異，取得具體指示。
- 涉及交易行為須執行 `tests/test_channel_swing.py`、`tests/test_channel_position_path.py`、`tests/test_channel_swing_execution.py`；基準合計 126 項通過。不得只為通過測試而刪除或放寬既定規則的斷言。
- `tests/test_channel_confirmed_rules.py` 與 `tests/test_channel_stop_preservation.py` 基準合計 27 通過、15 失敗（失敗位於 confirmed_rules）。已用隔離的原提交重現；不能宣稱全套測試通過，也不能因既有失敗而擅自改動進場策略。
- 提交前核對差異；部署後確認 API 運行狀態。既有失敗與新增回歸須分別報告，不可混為一談。

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
