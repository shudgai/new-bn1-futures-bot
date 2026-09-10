# 突破未成交後的軌外延續進場驗證

用戶要求：破軌沒開成，後面要延續開倉。

- 新入口：最近兩根已收線同向實體均收在同側外軌外，每根實體至少全長 20%，多單兩綠、空單兩紅。不要求第一根開盤在軌內；最新報價仍須嚴格在同側外軌外。
- 共用 aligned_entry 更新後，一般掃描、正常／異常重開、快取及送單重驗均可採用延續入口。MA3／MA15／CK、即時 MA3、同根回調、異常等待／回踩、新訊號、每根限次與帳戶風控保留，出口不變。
- 龍蝦保存行情以每根開盤可知資料重播：2026-09-10 18:28、18:29（UTC+8）舊穿軌 helper 均為 False，新 aligned_entry 均為 ENTER / LONG / KC_CONTINUATION_LONG。18:27 仍因兩根實體條件不符等待。此結果只驗證入口，不冒稱實際已成交或已通過所有報價順序風控。
- 針對性測試：test_channel_outside_continuation、test_channel_breakout_only、test_channel_intrabar_execution 合計 92 通過。涵蓋兩根軌外延續、多空、碰軌／軌內拒絕、正常與異常重開、回踩、同根限時、新倉／快取／重開的真實回調觀察順序。
- 指定五份回歸加 breakout_only：修改前 198 通過 / 69 失敗；修改後 192 通過 / 69 失敗，失敗名稱完全一致。六個舊 no_cross 必須拒絕斷言依本次授權撤換，由新增延續成功測試與送單回調測試覆蓋。未改動其他既有失敗斷言。
- 原始輸出：/tmp/two-green-before.txt、/tmp/two-green-after.txt、/tmp/two-green-focused.txt。修改前程式備份：/tmp/two-green-entry-baseline。

## 服務驗證

- 2026-09-10 10:35:40 UTC 重啟 binance-8006.service；API 回報 is_running=true、paper_trading=true、port=8006，strategy 已顯示兩根軌外同色 K 可延續且不要求重新穿軌。
- 10:35:52 UTC 日誌另有來自介面客戶端的 POST /api/reset_account 200；不是本次工具呼叫或啟動程式執行。未自行還原或重置帳戶。
- 本輪未宣稱完成自動成交驗證；行情條件與回調仍須即時成立。
