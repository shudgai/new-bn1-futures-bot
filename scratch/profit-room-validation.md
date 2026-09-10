# 漲勢末端與中軌退場驗證

修改位置：core/engine.py 的 _place_structured_entry、_channel_long_profit_room、_process_single_symbol_locked。

規則：已收線 ATR 延伸上限及最近已確認上方波峰估算剩餘空間，扣雙邊費用與滑點，達既有成本緩衝才買；已收線動能連續衰退不買。未啟動獲利保護多單到達或跌破即時中軌市價平倉，失敗保留並重試。

本次新增測試 18 項通過。指定回歸及前次需求測試在本次修改前 23 failed, 176 passed in 3.91s；加入本次前 16 項測試後 27 failed, 188 passed in 4.00s。另兩項真實虧損持倉測試隨新增測試獨立通過。

本次新增的舊測試衝突（原要求中軌下仍持有）：

- FAILED tests/test_channel_position_path.py::test_cross_only_never_trades_with_legacy_mode_disabled
- FAILED tests/test_channel_position_path.py::test_waterfall_scan_keeps_position[False]
- FAILED tests/test_channel_position_path.py::test_waterfall_scan_keeps_position[True]
- FAILED tests/test_channel_swing_execution.py::test_outer_rechase_holds_position_without_confirmed_exit_or_reverse[asyncio-LONG]

沿用前次發現：tests/test_channel_profit_protection.py:88 存在縮排錯誤，未修改。未部署或重啟服務。
