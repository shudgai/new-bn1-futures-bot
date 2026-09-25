# 階段性止損紅眼測試

**正式核心契約：20 passed。分支：`feature/staged-risk-implementation`。**
原有 20 個契約案例、參考模型及 6 個歷史適配器測試檔均未修改。
這次修改正式 `core` 與適配器映射；新策略不會自動啟用，沒有部署、推送或重啟實盤服務。

## 執行正式核心契約

```bash
RED_EYE_REQUIRE_PRODUCTION=1 RED_EYE_FACTORY=my_package.red_eye_adapter:create_strategy \
  .venv/bin/python -m pytest -q -s tests/test_red_eye_staged_contract.py
```

結果 **20 passed**。完整 JUnit：[staged_contract.xml](../reports/red_eye_production/staged_contract.xml)。
歷史紅燈輸出保留在 [pytest_failures.txt](../reports/red_eye_production/pytest_failures.txt)，不是目前結果。
最新摘要：[summary.json](../reports/red_eye_production/summary.json)。

## 正式修正

- 持倉／metadata 的 `use_staged_risk_engine is True` 才分流；字串 `"false"` 不會誤啟用。
  正式主循環直接呼叫新 runtime，帳戶更新不再執行該持倉的旧策略出口。
  `protection()` 與 `ProfitProtectionExitStrategy` 也有防護，舊代码仍保留供旗標關閉使用。
- Stage 使用 max 升級；多單止損取 max、空單取 min。極值僅讀取進場後報價。
- 部分止盈送出與成交分開記錄；持久化 client ID 後才送單，不以每次重試的時間戳建立新 ID。
- `update_stop_loss()` 撤舊止損確認後才送新單；拒單、回報 ID 不符、送單／撤單未知，均進入 RECONCILE。
- 報價不解除 RECONCILE；明確對帳或帳戶刷新才查實際持倉與委託狀態。
  Flat 對帳不送恢復訂單，會處理仍存活的已知 TP／STOP。
- CHOPPY_EXIT 要滿足 5 個不同 bar ID 沒有順向新極值及至少 25% 回吐。
  先確認 TP／STOP 終態，再按實際剩餘數量送 reduce-only 全平。
- 新旗標下入口以 MA7 為基準，順向乖離 >2.5 ATR 回傳 `ENTRY_BLOCKED: Deviation > 2.5 ATR`，多空對稱。
  旗標關閉保留原入口規則與文字。

## 程式分工與接線

- `core/services/exits/staged_risk_service.py`：正式狀態機、委託意圖、止損更新、對帳、入口 guard、runtime 安裝。
- `core/services/exits/staged_state_store.py`：atomic replace、檔案／目錄 fsync、同主機獨占持倉租約。
- `core/services/symbol_runner.py`：新舊策略分流。
- `core/paper_account.py`／`core/testnet_account.py`：帳戶刷新先對帳新 runtime，跳過該持倉的舊出口。
- `my_package/red_eye_adapter.py`：只映射測試 Position、行情、估值、傳輸與狀態；Stage、止損與對帳均委派 core。

測試透過 production valuation callable 注入指定 net_pnl；沒有改寫 core 決策。
未注入時使用正式扣費估值。部分成交改變數量後，若 transport 沒提供已實現淨利，
正式估值會回報 `STAGED_REALIZED_PNL_REQUIRED`，不把減倉造成的浮盈縮小當成回吐。
原生止損同步仍可維持現有保護線。帳戶 transport 應回傳該筆交易累計的 `realized_net_pnl`。

舊分支監測仍使用 AST 定位與唯讀執行追蹤；正式核心進入舊分支就會通知 spy 並失敗。
新策略通過不是因為 adapter 關閉監測或使用 ReferenceStagedStrategy。

## Runtime 與真正交易所的邊界

`install_staged_runtime(account, symbol, transport, risk_params, store)` 是明確啟用入口：

- 必須提供 StagedTransport 實作與持續持有租約的 durable store；缺 runtime 不回退到舊策略。
- 程式啟動時必須在交易／帳戶處理前恢復 runtime。重啟不信任快取委託狀態，先對帳。
- Store 的 position ID 必須與帳戶 metadata 匹配；不能把已結束交易的檔案沿用到新倉。
- 正式安裝預設 `sync_native_stops=True`，將止損計算結果交给 update_stop_loss。
  原契約的 Mock 場景分開測試意圖計算與 submit_stop，使用不自動撮合／同步止損的模式。
  補充測試另驗證正式安裝後自動提交 STOP，且檢查送出的 stop_price。
- 目前的 `_InjectedTransport` 是同步 MockExchange 的映射，不是真實 Binance／CCXT 連接器。
  不可把這個 Mock 映射用於上線。既有 TestnetAccount 原生條件單尚須對接正確的
  client ID 查詢、取消終態、成交與費用帳本，再跑實際帳戶傳輸整合測試。

撤舊／建新是可恢復的序列，不是交易所提供的原子交易；撤單成功到新保護生效之間仍有時間窗。
檔案租約只涵蓋同主機／相同鎖路徑，不是多主機分布式鎖。沒有宣稱所有程序斷電或交易所亂序已驗證。

## 補充測試與舊版基線

```bash
# 正式補充故障與接線（16 passed）
.venv/bin/python -m pytest -q tests/test_staged_risk_implementation.py

# 未修改的舊適配器行為斷言，明確選用 legacy factory（6 passed）
.venv/bin/python tools/run_red_eye_legacy_baseline.py

# 原參考模型（20 passed；執行前移除 RED_EYE_FACTORY／RED_EYE_REQUIRE_PRODUCTION）
.venv/bin/python -m pytest -q tests/test_red_eye_staged_contract.py
```

補充案例包含磁碟寫入失敗、撤單逾時、拒單、錯誤 ACK、已確認 OPEN 恢復、
正常部分成交對帳、20 個並發 tick、任務取消、Flat 孤兒掛單處理、獨占租約與 JSON 重啟。
兩個舊帳戶回歸（reduce-only 手動平倉、部分平倉）亦通過。

`test_red_eye_production_adapter.py` 的 6 個既有斷言描述的是舊版行為；
現在不能用新 factory 執行它們並解讀為新策略回歸。上面的 baseline 工具明確選用舊 factory，
未更改測試檔或斷言。新 factory 的可信度改由補充測試直接追蹤正式主循環驗證。

另記錄既有失敗：`test_initialize_restores_exchange_stop_for_position_opened_in_local_mode`
預期 triggerPrice=98.0、實際99.0；以 fe8b72bc 原始 TestnetAccount 也重現，未修改原規則或斷言。

## 參數與證據範圍

A／B 皆測試，尚未替正式部署選定參數：

- A：Stage 1 <1R；Stage 2 為 1R 至 <3R；Stage 3 >=3R。Stage 2/3 為 1 ATR，1R 送 50% TP。
- B：Stage 1 <1.5R；Stage 2 為 1.5R 至 <3R；Stage 3 >=3R。Stage 2 為 1.5 ATR、Stage 3 為 1 ATR，1.5R 送 50% TP。

場景 2 明確模擬止損成交回報尚未到達，Mock 不自動撮合。通過的是該故障前提下的狀態與委託協調。
20 passed 證明指定單元契約成立，不是實盤上線核准；真實連接器、重啟掛載順序與交易帳本整合仍須完成。
