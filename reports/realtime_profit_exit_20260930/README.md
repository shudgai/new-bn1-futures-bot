# 盤中即時峰值鎖利修正

## 事故證據
紙上成交紀錄：1000PEPE 於 UTC+8 16:47:05 開多，16:59:49 因中軌出口平多，淨損 -0.3513 USDT；不是已核實的16:57成交。附圖為龍蝦，不作PEPE峰值證據。精簡成交與quantity錯誤樣本存 incident.json。沒有完整歷史逐筆行情，不能宣稱已重建最高可得獲利或逐毫秒的漏平時點。

原symbol_runner直接讀quantity，但帳戶提供qty，鎖利計算因此拋KeyError；中軌檢查位於例外之前，仍可平倉。aggTrade獨立回調原只執行十字後反向K出口，未接上峰值鎖利；ticker一般路徑還可能等待掃描鎖與REST。

## 本輪新授權與實作預設
每筆有效aggTrade／ticker更新持倉綁定的peak_price與毛浮盈peak_pnl_usd。正浮盈峰值回吐達25%即盤中市價全平；取消舊10U／3ATR及批次5U／1.5ATR啟動門檻。另自獲利峰值回落達0.8ATR全平，ATR固定為首次取得的有效上一根已收線ATR，不隨後續擴大放寬。25%、0.8ATR及等於即觸發是依本輪範例選定的預設。毛浮盈保護不保證扣費後為正。

軌外反轉採實際觀察獲利報價位於持倉側外軌之外，之後價格反向且穿越即時MA3；多空對稱。MA3用前兩根已收線收盤＋本次報價重算，外軌使用最新已收線KC基準；無新鮮快照不猜MA／外軌，但仍追蹤峰值並執行百分比回吐及已取得固定ATR保護。不以影線回填進場後峰值。

即時鎖利、初始ATR止損不抓K、不等1M收線或掃描鎖。保留帳戶硬止損、十字反轉及已收線Swing結構出口，批次持倉改用相同即時服務與既有結構出口函式；入口、槓桿、資金與冷卻不變。已觸發出口持久化重試，重啟以持倉身分恢復；舊持倉真實已保存峰值保留，無法補回從未記錄的歷史峰值。行情超過5秒、未來／逆序或進場前報價拒絕。帳戶closing_lock防重複下單，測試網使用reduceOnly market。畫面不再把Channel Swing顯示為舊「等待脫離成本區」。

## 驗證與限制
9份相關測試163項通過，含新增21項盤中、多空、MA3、ATR、缺K／掃描鎖占用、重啟／磁碟重載與真實帳戶類別並發去重回歸。tests.log保存結果。紙上測試使用/tmp獨立狀態，測試網使用FakeTestnetExchange；未向交易所發送測試訂單。

額外既有test_confirmed_swing_exit_v2.py有6項失敗，test_close_deduplication.py有14項失敗；原HEAD隔離重現同20個失敗測試，原因為舊Swing樣本／政策及開倉測試缺少現行入口白名單。baseline_tests.log保存原版證據；沒有為通過舊測試放寬入口或修改Swing公式。

AIDAN前置、Python及測試規範檔缺失；指定三份test_channel_swing／position_path／execution亦不存在。不宣稱全庫通過。Python語法及git diff --check通過。即時觸發不等於保證毫秒成交；行情傳輸、程序排程、帳戶撤單與交易所回應仍有延遲，REALTIME_EXIT日誌記錄報價到判定延遲。

## 部署
程式提交ce8038f已推送bugfix/realtime-profit-exit。8006於2026-09-30 09:14:40 UTC（17:14:40 UTC+8）重啟，MainPID=260157，active/running；09:15:00核對/api/status HTTP200、is_running=true、paper_trading=true。查核當下無持倉，因此不宣稱部署後已觀察到自然行情鎖利成交；盤中觸發與送單由隔離回歸驗證。deployment.json保存狀態。
