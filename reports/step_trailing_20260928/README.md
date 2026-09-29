# 多單分階段防守

本輪完成必要的啟動、保本與波段追蹤三段；使用者列為可選的 50% 分批止盈未啟用。現有紙上／測試網一般帳戶只有全平接口，未以假減倉或反覆全平代替半倉執行。

- 啟動期：保留既有初始 SL 及已收線 MA15 結構防守，MA3 不作出口。突破倉原初始 1.5 ATR、早鳥倉原峰谷 SL 均保留，沒有放寬持倉既有止損。
- 最高觀察浮盈達 1.2 倍固定進場 ATR：盤中硬防線提高至成交成本價，原有更有利防線不降低。遵照具體實作指示，未增加費用墊；實際成交仍有費用／滑點。
- 多單最高觀察浮盈嚴格超過 1.5 ATR：持久化啟用兩根低點追蹤，替代 MA15 結構出口。2.0 ATR 不自動減倉。
- 每個新確認棒，用它之前兩根已收線且開盤時間不早於進場時間的最低 Low 更新防線；新防線取舊線與候選的較高者，只升不降。確認棒自己的 Low 不參與。
- 只有後續確認棒 Close 嚴格低於防線才全平；碰線、盤中影線不觸發。首次觀察僅建立基準，不追溯觸發。階段啟動不降級，已有有效待平保存並重試。
- 追蹤線與 quote-enforced stop_loss 分開保存；因此最新報價跌破追蹤線但尚未收線時不誤平，成本保本／初始硬止損與帳戶硬止損仍可盤中觸發。空單沿用原 MA15 與保本政策。

## 程式串接

主要變更在 `core/services/exits/dual_track_exit_service.py`，新增 trailing_long_exit 及三個持久化欄位 swing_trailing_armed／swing_trailing_line／swing_trailing_last_bar。symbol_runner 與帳戶更新已共用 DUAL_TRACK_STATE_KEYS，測試網 refresh 也使用此欄位集，沿用既有存檔還原。

已檢查 exit_service.py、profit_protection_service.py：兩者經 entry_atr_protection 或直接委派至同一 DualTrackExitStrategy，最新報價可傳入；不另建第二套互相競爭的防守算法。

## 驗證

```text
.venv/bin/python -m pytest tests/test_step_trailing.py tests/test_peak_trough_cross.py tests/test_long_entry_trend_filter.py tests/test_swing_defense_policy.py tests/test_emergency_ma3_trend_hold.py tests/test_red_eye_entry_pressure.py tests/test_entry_e2e_20260928.py tests/test_order_sizing.py tests/test_startup_guard.py -q
160 passed in 9.63s
.venv/bin/python -m compileall core tests/test_step_trailing.py
成功
```

新增案例驗證 1.2／1.5 ATR 邊界、僅多單啟動、盤中碰線不平、確認棒不納入前兩低、追蹤替代 MA15、防線不下移、已觸發待平還原重試、未收線與進場前低點不參與。既有 MA15 測試改以尚未達追蹤門檻的倉位測試。

已備份紙上帳戶並沿用授權重啟 8006，API is_running=true、paper_trading=true，初次重啟觀察無 ImportError／Traceback／ERROR／DANGER。相關證據及程式差異保存本目錄。未手動清倉或送測試網訂單。

本規則無法保證固定鎖住最高利潤的 80%，也不能保證成本保本後扣費淨損益非負。本輪不以圖示推定 07:35 當時指標值，亦未以小樣本聲稱績效改善。
