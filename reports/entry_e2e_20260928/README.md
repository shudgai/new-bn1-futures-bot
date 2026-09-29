# 破軌未成交端到端檢查 — 2026-09-28

已修復現場可證實的帳戶總電閘崩潰、測試網下單額外 ATR 攔截及畫面原因誤標。完整本輪差異見 [changes.diff](changes.diff)；差異基準為開始本輪時的工作區，保留原有未提交變更。

## 現場證據與根因

1. `runtime_errors_before.log` 記錄 UTC 22:20:24–22:20:58 重複出現：
   ```text
   [MASTER_BREAKER_CRASH] 總電閘驗證過程發生異常: name 'kc_upper_curr' is not defined
   ```
   運行模式為 `paper_trading=true`。斷點位於 PaperAccount.open_position 的重複 MASTER_BREAKER，例外被捕捉後回傳 False，無成交。開始檢查時磁碟原始碼已定義該變數，但舊程序仍報未定義：運行版本與磁碟不一致。沒有舊程序完整 traceback，不能指定舊版精確執行行號；對應區段保存在 `before/core/paper_account.py`。新版刪除此重複策略實作，由 `validate_account_entry` 統一重驗，不是繞過帳戶風控。
2. `before/core/testnet_account.py:168` 在原始 create_order 包裝器引用未定義的 `reason`。另 `_send_order` 無條件要求實體 >=0.6 ATR，會擋住合法的 0.55 ATR IGNITION 及小實體 TREND_BREAKOUT。這是程式檢查及隔離測試確認的測試網缺陷，不能冒稱為當時紙上帳戶的 Binance 拒單。
3. `services/api.py` 原本只讀 CLOSED_SIGNAL，未讀取最終拒絕結果，任何 CLOSED_* 都顯示「信號已觸發 (等待市價執行或遭風控攔截)」。另 `"CK" in reason` 會命中 `BLOCKED`，把非 CK 原因誤標。現改讀取同確認棒的 EXECUTION 結果，CK 採明確前綴判斷。
4. TREND_BREAKOUT 原在 TREND_CRAWLING 的 else 分支，可能被慢趨勢分類搶先選中後遭額外限制。現優先順序為 IGNITION → TREND_BREAKOUT → TREND_CRAWLING，突破要求至少五根完整歷史棒。
5. 重新驗證曾裁切行情至舊候選棒，可能保留過期訊號；現在使用最新完整快照，確認棒改變即拒絕舊候選。入口 CK 改由相同的最近兩根已收線計算，避免有無 live tail 改變判斷。

## 四環節檢查結果

| 環節 | 結果與處理 |
| --- | --- |
| 日誌 | 已保存運行 API 的錯誤及前後狀態；EXECUTION 記錄 symbol、side、confirmation bar、拒絕原因。交易所邊界新增 ORDER_SUBMIT / ORDER_ACK / ORDER_REJECT，數量驗證失敗記錄 ORDER_SIZE_REJECT。 |
| ATR / 市場狀態 | IGNITION 原已是 0.55 ATR，修正含等號浮點邊界；TREND_BREAKOUT 無最低 ATR 實體門檻。移除交易所額外 0.6 ATR 重複門檻。兩種強訊號豁免低波動、全通道擴張及外軌斜率限制，保留明確反向 CK、同向實體及既有影線比例限制。未發現目前入口依 CHOPPY 標籤一票否決；用 CHOPPY context 回歸驗證放行。 |
| 冷卻 / 資金 | 保留兩根 K 棒冷卻、MAX_SLOTS、每日虧損、待成交/持倉排他、每確認棒成交去重及半錢包預算。現場持倉 0、MAX_SLOTS=2、可用約149.97 U，無證據指向滿倉或餘額不足。冷卻依確認棒時間比較實際平倉時間，仍可能因棒內成交時點等待超過120秒，未擅自取消。未發現此執行路徑另有獨立總名義曝險攔截。 |
| 訊號 | 空單保持 MA3<MA15、收盤跌破前五根最低價、收在 KC 下軌外，多單對稱。只評估已收線棒；未收線 tick 不覆寫已收線決策。即時價格破軌不代表已收線條件成立。未改既有 IGNITION/TREND_BREAKOUT 的 MA3斜率例外及其他持倉出口。 |
| 下單 | 掃描→引擎→防火牆→紙上帳戶實際成交已通過隔離測試；真正的測試網 create_order 包裝器使用模擬 exchange 驗證市價調用及拒單傳播，內部 context 不送到交易所。原有 Decimal 數量、LOT_SIZE/MARKET_LOT_SIZE、minQty/maxQty、stepSize、MIN_NOTIONAL 與 CCXT 精度測試通過；未找到本次精度拒單證據。未發送 Binance 實際驗證訂單。 |

## 修改檔案

- `core/services/strategies/unified_entry_strategy.py`：強破位排序、五棒歷史、強訊號通道豁免、0.55 浮點邊界、已收線 CK 一致性及方向不符診斷。
- `core/services/entry_firewall.py`：強訊號外軌斜率豁免、最新棒重驗、具體原因及門檻文案。
- `core/paper_account.py`：移除有崩潰紀錄且與共享驗證重複的 MASTER_BREAKER。
- `core/testnet_account.py`：統一低層驗證、移除未定義 reason/額外0.6ATR、內部 context 清理、提交/回應/錯誤/數量日誌；不再以送出 reduce-only 請求冒充已成功平倉時間。
- `core/engine.py`：最新快照重驗、執行階段與各拒絕原因、捕捉帳戶例外，只有成功成交才標記去重。
- `services/api.py`：同棒執行結果優先、CK 誤判修正、訊號成立與成功成交區分。
- `tests/test_entry_e2e_20260928.py`：新增回歸測試。

## 驗證及服務

```text
.venv/bin/python -m pytest -q tests/test_entry_e2e_20260928.py tests/test_order_sizing.py tests/test_startup_guard.py
62 passed
```

本輪修改 Python compileall 及限定修改檔案的 git diff --check 通過。整個工作區仍有既有變更的空白格式問題；不宣稱全專案歷史測試通過。

原服務是使用者層級 `systemctl --user`，原本因8006被手動 start.sh 占用而 failed。已優雅關閉該啟動器並交回服務管理。最後重啟 UTC 22:32:40，22:33:25 查核 active/running、NRestarts=0、API HTTP200、is_running=true、paper_trading=true，行情正常更新。重啟後觀察窗口無新增 ERROR/DANGER；目前沒有新實際成交，因此不宣稱現場已成交。參閱 `service_after.txt`、`service_after.log`、`runtime_after.json`。

## 測試隔離疏漏

第一輪測試網包裝器測試只 mock `_load_state`，漏 mock `save_state`，測試日誌寫入未使用的 `data/testnet_account.json`。已將純測試產物移到 `isolated_testnet_fixture_state.json`，後續測試隔離兩者。未事先備份該檔，不能確認或恢復可能存在的原內容。現行紙上帳戶未被測試重設；重啟前另備份 `paper_state_before_restart.json`。此限制已向使用者說明。

規範限制：使用者指定的 AIDAN/*.md 不存在，替代位置搜尋亦無結果；未修改任何 AIDAN 規範，沒有宣稱讀取成功。未提交或推送工作區大量既有變更。
