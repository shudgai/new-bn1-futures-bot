# 紅眼測試報告 — 2026-09-28

結論：已確認舊紙上總電閘崩潰是實際未成交原因；「猴市」為誤導性前端標籤，沒有證據顯示其攔截下單。修復後已有兩筆自然產生的紙上成交。新增並發測試另抓到跨幣持倉上限競態，已修復。本輪67項測試通過。

## 階段1：日誌鑑識

以已保存的第一個錯誤 UTC 22:20:24 為事件中心，查詢 22:05:24–22:35:24（前後各15分鐘）。完整「可取得」journal為 `incident_window.log`，660行，關鍵字結果在 `keyword_hits.log`。限制：舊程序由手動 start.sh 啟動，stdout指向終端，systemd當時已failed；此窗口的journal從22:31:36才開始。已補入上一輪捕獲的記憶體日誌 `recovered_memory_logs.json`，但不能重建缺失的全部30分鐘，更不能聲稱有舊程序完整traceback。

已保存原始證據：

```text
2026-09-28 22:20:24 UTC [MASTER_BREAKER_CRASH] 總電閘驗證過程發生異常: name 'kc_upper_curr' is not defined
...重複至22:20:58...
2026-09-28 22:21:00 UTC 幣種掃描例外: [FATAL_REJECT] 陰線嚴禁開多！Close:0.0041314 <= Open:0.0041363
```

具體位置：上一輪保留的 `../entry_e2e_20260928/before/core/paper_account.py:663` 是 MASTER_BREAKER_CRASH 日誌發出點，664行 return False。556行是參照 `kc_upper_curr` 的重複總電閘計算。磁碟備份已有該變數定義，舊程序卻報錯，因此不可把備份行號當成舊程序未捕獲traceback的確切拋出行。可以確定的是「帳戶總電閘例外→False→沒有成交」，不能從未帶symbol的旧錯誤直接斷言是哪個幣種。

另外已修復的靜態斷點：`../entry_e2e_20260928/before/core/testnet_account.py:168` 使用未定義reason；1826、1830行在_send_order強制0.6ATR。這兩者屬測試網路徑，不冒充本次紙上帳戶收到交易所拒单的證據。

## 階段2：策略、原始行情與重播

`replay.py` 使用本機API保存的500根歷史OHLC，每次只取確認棒及其以前199根內的資料重算實際策略。圖表API未提供volume，重播使用0作佔位；本次MA3/MA15/ATR/KC與三種入口不讀volume。沒有未來棒參與訊號計算。完整每棒指標、前五棒極值、Signal和防火牆結果在 `replay.json`、`replay_values.csv`，原始回應為 `pepe_klines.json`、`lobster_klines.json`。

以下為「事後歷史行情重播」，不是當時逐報價紀錄：

| UTC確認棒 / 幣種 | MA3 / MA15 | Close / KC下軌 / 前五低 | 結果 |
| --- | --- | --- | --- |
| 22:19 PEPE | .0041332 / .0041233667 | .0041364 / .0041226355 / .0041178 | 多頭破五高成立，但IGNITION優先，回傳CLOSED_IGNITION_LONG、防火牆PASS。 |
| 22:20 龍蝦 | .07122 / .0711913333 | .07092 / .0709710206 / .07044 | 雖收盤在下軌外，但MA3不小於MA15，也未跌破前五低；空單WAIT_NEW_A_TO_E_TRIGGER。 |
| 22:33 PEPE | .0041324 / .00413016 | 多頭突破三條件成立 | body/range約0.09，被PIN_BAR_DETECTED攔截，非ATR絕對實體門檻。 |
| 22:35 龍蝦 | .0704633333 / .0708753333 | .07031 / .0706286989 / .07032 | 三條件成立，CLOSED_TREND_BREAKOUT_SHORT、防火牆PASS；現場當時已有22:34空倉，不能解讀為必須再開一筆。 |

關鍵數值的完整OHLC例子：22:35龍蝦 O=.07044 H=.07046 L=.07014 C=.07031，KC上=.0711486989、中=.0708886989、下=.0706286989，ATR=.00026，實體=.00013，只有0.5ATR仍能通過TREND_BREAKOUT。

資料差異：現場成交快照保存龍蝦22:33 closed_price=.07056，後查歷史為.07065；PEPE22:35快照為.0041467，後查歷史為.0041452。缺少原始當時回應，無法判定差異究竟是來源修訂或其他原因；不把重播值冒充事故原始值。本輪補上 `core/services/candle_data.py:84` 的closed-only六棒證據，`symbol_runner.py:73` 寫入日誌，並持久化送單快照；日誌同時帶caller檔案與行號。

所有新倉依is_closed的已收線資料決定，非單純即時Tick破軌。確認棒更新會重驗、舊訊號過期或identity不符就拒絕；未收線尾棒不覆寫已收線判定。測試包含未收線行情改動、同棒50並發只成交一次及快照不含live candle。

## 階段3：防火牆、猴市與帳戶限制

- IGNITION本身仍要求>=0.55ATR及影線條件（策略202/237行）；「豁免」是免於下游額外0.6/0.8/1.2ATR重複門檻，並非取消其自身定義。
- TREND_BREAKOUT沒有最低實體ATR門檻；仍保留同向K實體、MA排列、明確反向CK禁止及實體占全長至少40%（策略288–291行）。不能把三個基礎條件True當作所有風控必然放行。
- `entry_firewall.py:34/45`尚有「軌內小實體」檢查，但合法IGNITION/TREND_BREAKOUT必須收於軌外，不會命中；進入軌內本來就不再符合這兩種訊號。
- 猴市來源：本輪前 `web/index.html:583` 用kc_width_atr<3。策略通道=(EMA±ATR×倍數)，寬度/ATR=2×倍數；當前約2，固定低於3。這是顯示算法，不是入口的CHOPPY veto。前端已改直接顯示通道寬度，不再宣稱猴市；不另加缺乏授權的新市場分類策略。
- 保留：MAX_SLOTS=2、持倉/掛單排他、每日虧損、可用保證金含手續費、半錢包配置、每根成交去重。90秒已收線訊號時效在entry_firewall.py:86–88，兩棒平倉冷卻在91–93。未發現此鏈路另有獨立總名義曝險上限。
- 新發現：只用每symbol鎖，跨幣可同時看見空槽。`pressure_before.log`重現MAX_SLOTS=1卻兩筆均True（mock帳戶隔離資金檢查）。修復 `core/engine.py:1780` 帳戶提交鎖，1785行重新檢查持倉上限，並重算可用資金/最新價/每日熔斷。不得把此額外缺陷當作之前未成交的原因。

## 階段4：執行、非同步與精度

實際鏈路：main_loop → asyncio.gather → 每幣symbol lock → symbol_runner → await _execute_confirmed_channel_break → 每幣entry lock → 最新快照/防火牆 → 帳戶提交鎖 → await account.open_position。沒有市價下單worker/queue，因此原UI「等待市價執行」不證明有堵塞佇列。

真實成交日誌（均為紙上，不是Binance成交）：

```text
22:34:01 龙虾/USDT SHORT ACCOUNT_SUBMIT code=CLOSED_IGNITION_SHORT margin=74.98440616536995 leverage=5
22:34:02 龙虾/USDT SHORT FILLED
22:36:01 1000PEPE/USDT LONG ACCOUNT_SUBMIT code=CLOSED_IGNITION_LONG margin=74.61041910220104 leverage=5
22:36:01 1000PEPE/USDT LONG FILLED
```

`fills.json`保存實際成交記錄；龍蝦價.07062294、PEPE價.00414481，與確認棒時間分開。`runtime_chain.log`保存服務鏈。帳戶失敗/例外後鎖釋放測試通過，其他幣不會被遺留鎖永久阻塞；沒有證據顯示現場發生queue堵塞。

測試網使用真實帳戶包裝器+mock exchange驗證market調用、原始API錯誤傳播與內部context剝除。`order_sizing.py` Decimal/CCXT amount_to_precision、LOT_SIZE/MARKET_LOT_SIZE、min/maxQty、stepSize、minNotional已測。市價單送price=None，不把顯示價依tickSize四捨五入當成必要市價單價格參數。紙上小數qty不代表送往交易所。未發送實際測試網驗證單，沒有證據聲稱某筆Binance實際訂單已被接受。

## 修復、驗證、重啟

本輪具體修改：`core/engine.py`提交鎖及證據快照、`core/services/candle_data.py`來源行號及六棒證據、`core/services/symbol_runner.py`訊號快照、`web/index.html`猴市誤標，以及 `tests/test_red_eye_entry_pressure.py`。本輪diff：`changes.diff`。上一輪根因修復diff：`../entry_e2e_20260928/changes.diff`，兩者分開避免混入用戶其他變更。

```text
pytest tests/test_red_eye_entry_pressure.py tests/test_entry_e2e_20260928.py tests/test_order_sizing.py tests/test_startup_guard.py
67 passed
```

Python compileall通過，本輪新增diff行無尾隨空白；沒有宣稱歷史全套測試通過。測試完全隔離帳戶讀取/儲存，未新增上一輪的持久化疏漏。

最新修復已於UTC 2026-09-28 22:42:30載入：systemctl --user binance-8006.service active/running、MainPID=3631658、NRestarts=0，API HTTP200、is_running=true、paper_trading=true，重啟後無新增ERROR/DANGER。`status_after.json`有一筆PEPE多倉，服務繼續管理；未手動清倉或重設帳戶。完整證據見service_status.txt/status_after.json，重啟前紙上帳戶備份paper_before_restart.json。
