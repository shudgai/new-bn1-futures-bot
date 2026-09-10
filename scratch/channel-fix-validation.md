# 三圖修正驗證

本次需求測試：35 passed。

修改前：34 failed, 194 passed in 5.50s

修改後：40 failed, 201 passed in 5.65s

共同失敗：33

新增失敗（舊預期與新限制差異）：

- FAILED tests/test_channel_confirmed_rules.py::test_closed_trade_cannot_reuse_confirmation_even_after_restart[False]
- FAILED tests/test_channel_confirmed_rules.py::test_closed_trade_cannot_reuse_confirmation_even_after_restart[True]
- FAILED tests/test_channel_confirmed_rules.py::test_confirmed_signal_fills_once_on_same_scan[False-LONG]
- FAILED tests/test_channel_position_path.py::test_adverse_bodies_hold_at_or_outside_favorable_rail[0.0-False-True-SHORT]
- FAILED tests/test_channel_position_path.py::test_adverse_bodies_hold_at_or_outside_favorable_rail[0.2-False-True-SHORT]
- FAILED tests/test_channel_position_path.py::test_continuation_needs_current_price_outside[LONG]
- FAILED tests/test_channel_swing.py::test_favorable_waterfall_live_turn_holds_while_ma3_outside[SHORT]

獲利保護測試在修改前及修改後皆因 tests/test_channel_profit_protection.py:88 的 IndentationError 無法收集。

未重啟或部署服務。
