# 多空對稱修正驗證

需求測試：101 項通過（requested_fixes 35、profit_room 18、retracement_twenty 6、symmetric_rules 42）。

本次修改前：27 failed, 196 passed in 3.83s

修改後指定回歸與需求測試：33 failed, 232 passed in 4.47s

既有失敗 27 項。新增舊規則衝突：

- FAILED tests/test_channel_position_path.py::test_adverse_bodies_hold_at_or_outside_favorable_rail[0.0-False-True-LONG]
- FAILED tests/test_channel_position_path.py::test_adverse_bodies_hold_at_or_outside_favorable_rail[0.2-False-True-LONG]
- FAILED tests/test_channel_position_path.py::test_continuation_needs_current_price_outside[SHORT]
- FAILED tests/test_channel_swing.py::test_favorable_waterfall_live_turn_holds_while_ma3_outside[LONG]
- FAILED tests/test_channel_swing_execution.py::test_low_volume_outer_rechase_keeps_existing_position[asyncio]
- FAILED tests/test_channel_swing_execution.py::test_outer_rechase_holds_position_without_confirmed_exit_or_reverse[asyncio-SHORT]

新增衝突涉及即時十字 K 開空、長反色 K 仍持有與空單中軌仍持有；新成對測試已確認本次授權行為。

獲利保護舊測試檔 test_channel_profit_protection.py:88 的既有縮排錯誤未改寫，本次另以 retracement_twenty 驗證回吐保護。

重啟驗證：{"is_running": true, "strategy": "Channel Swing 多空雙收線確認／剩餘淨利空間檢查／未啟動保護中軌退場；最高浮盈回吐20%平倉；階梯式遇即時反色K收緊至10%；預估淨利1USDT啟動（2幣）", "port": 8006, "environment": "binance_testnet"}
