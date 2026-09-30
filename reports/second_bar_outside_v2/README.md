# 第二根盤中軌外唯一入口

## 2026-09-30 同向實體與棒數規則更新

現行規則已取代下方歷史版本的「第二根可反色或十字／反向實體50%」：當根須嚴格同向、實體至少全長25%，多單上影或空單下影不得超過實體。初始只准該次破軌第二根；第三／第四根不得重新編號。成功平倉後另走兩根冷卻，以平倉後的新棒對評估延續重開。詳見 ../directional_entry_20260930/README.md。下方304項結果為先前版本紀錄。

## 最新變更：移除開倉前盈虧估算

依使用者最新授權，已刪除 verify_profitable_expectation、入口與送單共用評估中的所有預期淨利／報酬風險比攔截及相關診斷。合法第二根訊號不再因 ATR 尺度、估算費用或舊風險比門檻遭拒。保留形態、有效行情、初始止損、兩根平倉冷卻、資金名額、BTC風控與成交去重。

指定測試：82 passed in 2.46s；完整相關回歸：304 passed in 6.35s。小ATR、大ATR、低價幣尺度及原風險比不足的合法多空案例均驗證放行，並以隔離紙上帳戶驗證送單。第二根形態、緊急出口及常規出口方法AST與本次修改前完全一致。

## 本輪實作

依使用者提供的判斷式，第一根必須明確已收線且同向實體收在 KC 外；不要求第一根開盤在軌內，不以影線穿越代替收盤確認。下一根必須為相鄰一分鐘的未收線棒：多單最新價嚴格高於上軌且 MA3 > MA15；空單嚴格低於下軌且 MA3 < MA15。第二根可反色或十字，反向實體超過第一根實體50%才拒絕，等於50%可通過。沒有新增停留秒數、額外棒數或離軌距離。

純已收線快照不產生此入口，不拿最新報價改寫前根收盤。資料無效、缺少明確收線旗標、不相鄰、碰軌、MA持平／反向均不開。前根已收線 ATR 保留供初始止損使用，無效 ATR 仍不送單。

- pure_trend_v2.py：新增 evaluate_second_bar_outside_entry；evaluate_v2_frame及相容入口統一委派；只有 SECOND_BAR_OUTSIDE_LONG／SHORT 可以自動送單。停用第三根、超級破軌及獨立延續替代入口。
- engine.py：恢復 WebSocket 入口回呼，保留運行狀態、幣種白名單、5秒報價時效、輪替、每日熔斷與BTC方向檢查；同幣掃描忙碌時不排隊舊報價，下個報價重評。WebSocket先處理持倉出口再處理入口。
- 送單重新抓快照；先更新當根報價與高低價再計算指標。共用評估器收到更新報價時同步修正即時MA3／MA15，不修改原始資料。預設訊號自動取得當根時間戳，避免套用上一根收線時間。
- entry_firewall.py：帳戶與交易所邊界只接受新白名單，舊快取碼不再授權訂單；真正手動操作沿用原例外。
- 不在訊號評估時鎖單。沿用同幣提交鎖、跨幣帳戶提交鎖與持久化成交紀錄；同幣同根不分方向只成功開一次，失敗可重試，重啟後從紀錄拒絕重複成交。

保留平倉後兩根冷卻、資金及持倉名額檢查、槓桿與初始止損。兩個持倉出口方法的AST與HEAD完全相同，不修改常規峰谷出口或盤中緊急出口。

## 驗證

```text
.venv/bin/python -m pytest tests/test_second_bar_outside_v2.py tests/test_strict_third_bar_v2.py tests/test_super_breakout_v2.py tests/test_continuation_reentry_v2.py tests/test_v2_execution_boundary.py tests/test_red_eye_v2_pressure.py tests/test_pre_entry_pnl_filter.py tests/test_confirmed_swing_exit_v2.py tests/test_order_sizing.py tests/test_startup_guard.py -q
304 passed in 6.35s
```

覆蓋一根收線確認即可盤中進場、多空50%邊界、只有影線破軌禁入、MA排列、資料有效性、當根回軌後恢復、舊快取封鎖、冷卻重開、送單報價變化、WebSocket入口、同根200並發僅一筆成交、跨幣槽位競爭、失敗釋鎖及成交紀錄去重。交易所接口測試使用mock，沒有外部訂單。

舊third-bar、super-breakout與continuation測試依本次明確取代規則遷移；已收線快照舊成交斷言改驗證拒絕。沒有以放寬新規則使舊入口繼續通過。

語法編譯及git diff --check通過。AIDAN指定前置／Python規範及三份舊Channel Swing回歸檔缺失，因此不宣稱全庫測試通過。

初版已於7d750dd提交並部署；本次移除盈虧閘門依使用者指令於測試通過後提交及重啟。部署後證據另存本目錄，不以隔離測試冒充自然行情成交驗證。原有兩個patch腳本未執行。
