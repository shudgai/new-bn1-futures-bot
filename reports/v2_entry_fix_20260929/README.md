# V2 送單流程修復

修復未定義 final 造成送單中斷，改用最新快照重新評估所得 decision，統一 type、entry_atr、close_price、confirmation_bar_id。ATR 使用最新已收線值，原 1.5 ATR 初始止損計算保留。

移除引擎最新已收線同色、重複雙同色及舊 CK 趨勢攔截。掃描、引擎重驗及帳戶防火牆共用 evaluate_v2_frame；V2 訊號使用專用白名單，包含 TRACK_RIDING、INTRA 相容名稱、THREE_BAR_BREAKOUT 及 CONTINUATION 的多空型別。帳戶邊界重新取得行情並驗證實際策略，不只相信型別字串。原舊訊號的防火牆路徑未放寬。

修正盤中訊號時效從當根開盤時間起算，避免尚未收線訊號被誤判為未來。未知訊號、方向不符、ATR 無效、回到通道內、快照換根、過期、重複成交、資金不足、名額限制及每日虧損仍拒絕。

驗證：

```text
.venv/bin/python -m pytest tests/test_v2_execution_boundary.py tests/test_order_sizing.py tests/test_startup_guard.py -q
56 passed in 2.13s
python3 -m py_compile core/engine.py
成功
```

端到端包含多空盤中／收線第三根反色，經 runner、engine、實際 PaperAccount 成交與去重；交易所接口使用 AsyncMock，未送出測試網訂單。

規範列出的 tests/test_channel_swing.py 不存在，指定三檔回歸命令無法收集測試；不宣稱全套測試通過。AIDAN 所列 common 規範及 Python 規範檔亦未提供於目前工作樹。原有兩個未追蹤 patch_engine_execute 腳本未修改或提交。
