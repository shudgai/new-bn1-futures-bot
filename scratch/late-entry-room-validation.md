# 末端進場空間修正驗證

- 用戶授權：起漲不計算淨利空間，末端才計算並修正估算法。
- 前輪要求保留：兩根已收線同色實體、MA3 轉彎不單獨平倉；其他出口與風控不變。
- 初段及持續強勢：不計算目標價或淨利空間，清除訊號上可能殘留的舊估算。
- 疑似末端實作預設：最近 7 根已收線 K 至少 4 次顺向收盤推進，首尾位移至少 3 ATR，最後 3 次推進嚴格縮小且末次至多首次一半。多空鏡像；此為可驗證代理條件，不能確定真正頂底。
- 末端目標：最近 60 根已收線內，左右一根確認且後續尚未觸及或突破的最近前高／前低；扣雙邊費用及雙邊滑點，淨空間門檻不變。無結構目標則拒絕末端追入，不再捏造「前收±ATR」上限。
- 所有普通、嚴格突破、快取及重開送單共用；既有突破訊號碼不再豁免末端檢查。
- 行情資料無效仍禁入；階段只看已收線 K，末端估算採最新報價。

## 回放

以前輪保存的 PEPE K 線（/tmp/pepe-chart.json）重算 2026-09-10 02:32 UTC：
- 第二根開盤 0.0034484：allowed=true, checked=false, stage=developing。
- 第二根收盤 0.0034531：allowed=true, checked=false, stage=developing。
- 兩個報價都不再因舊淨利空間公式被擋。此項僅驗證空間守門，不代表其他進場條件必然通過或實際已成交。

## 測試

指令使用 .venv/bin/python -m pytest -q，測試檔案：
test_channel_late_entry_room.py、test_channel_two_closed_entry.py、
test_channel_next_live_push.py、test_channel_swing.py、
test_channel_position_path.py、test_channel_swing_execution.py、
test_channel_current_rules.py、test_channel_profit_protection.py、
test_channel_profit_room.py、test_channel_confirmed_rules.py、
test_channel_stop_preservation.py、test_channel_candle_frequency.py、
test_close_deduplication.py、test_channel_symmetric_rules.py、
test_channel_outer_cycle.py。

- 本輪修改前：421 passed, 51 failed, 4 skipped。
- 修改後：463 passed, 51 failed, 4 skipped。
- 新增末端規則 42 項全部通過；51 項失敗清單與本輪基準完全相同。
- 反手測試第 66 根只修正開收盤已改 100 卻留下舊高低價的無效 OHLC；原交易斷言保留。
- 本輪基準 /tmp/late-room-baseline.txt；最終結果 /tmp/late-room-verified.txt。
- git diff --check 通過。

## 部署

已重啟 binance-8006.service；服務 active、API is_running=true、port=8006，新說明已生效，PEPE 圖表 API 回應正常，重啟後近期無 ERROR。
