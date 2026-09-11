# MA15 順勢峰谷開倉驗證

## 已授權行為

一般空手新倉改為最近三根已收線價格峰谷，MA15 三根嚴格順向，右側同向 K 收線與 MA3 嚴格轉向確認。新倉不再要求站到 CK 外軌；最新價破壞峰谷則不開。未收線 K 不參與峰谷或 MA15 確認。

只有新峰谷持倉使用延後中軌出口：先越過中軌，再回到中軌退出。狀態與待平標記寫入持倉及帳戶 metadata；已觸發但未成功平倉，後續即使價格恢復或獲利保護啟動仍重試。舊持倉、外軌反手、獲利外軌重開、資金與槓桿參數沿用。

## 檔案

- core/channel_pivot_entry.py：閉合峰谷確認、中軌退場狀態。
- core/engine.py：一般新倉入口、送單重验、信號代碼、帳戶狀態保存與中軌出口整合。outer_entry_only 僅供保留的反手重試及獲利重開使用。
- core/paper_account.py、core/testnet_account.py：新增峰谷欄位保存白名單；測試網同步保存確認 K 編號以便重啟去重。
- tests/test_channel_pivot_entry.py：56 項多空案例，含真實 PaperAccount、假交易所 TestnetAccount 成交與重載。
- 舊外軌新倉測試保留原斷言，明確指定 outer_entry_only=True，驗證現存重開入口。新增測試另確認預設新倉不能只憑外軌延續進場。

## 驗證結果

- 峰谷新增測試 56 通過。
- 指定 channel_swing、channel_position_path、channel_swing_execution，以及 requested_fixes、profit_protection、ma3_outer_exit、profit_room、retracement_twenty、symmetric_rules 合計 322 通過。
- testnet_account 的進場保護單、reduce-only 手動平倉兩項測試通過。
- 上述合計 380 項通過。
- confirmed_rules 與 stop_preservation：22 通過、20 失敗。失敗節點清單與本次修改前工作區的 20 項相同；不等於 AGENTS 舊提交記錄的 15 項，不宣稱全套通過。未修改這兩份測試。
- git diff --check 通過。

本輪未提交、未推送、未重啟服務。工作區原有其他策略與 UI 修改保留；此驗證僅說明本輪變更，不代表所有未提交內容均已驗證或已部署。
