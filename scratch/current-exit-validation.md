# 現行出口續驗紀錄

## 2026-09-11 降至0.5U啟動淨利保護

- 扣雙邊費用與預估平倉滑點後0.5U啟動，最高淨利回吐20%；既有峰值、較高鎖利線及待平重試保留。未改動其他出口與入口。
- half_usdt_protection、entry_recovery、fading_exit、hard_stop、waterfall_threshold共175 passed；新增多空0.499不啟動、0.5啟動、回吐界線及重啟待平／保護線保留測試。
- 此為使用者授權的門檻調整，不宣稱已以歷史逐報價驗證可以避免圖中所有虧損。

## 2026-09-11 去除異常拉升等待

- 一般入口、重開、快取送單不再受surge_recovery_entry攔截；舊異常平空票據解除也不再額外檢查surge，其餘匹配成交及有效後續K條件保留。
- 新增多空各送單路徑的歷史拉升案例、即時拉升仍可進場及禁止交易模組匯入舊surge veto的固定規則測試。
- 七份專項237 passed。舊post_close_recovery與surge_release回歸16 passed / 27 failed，隔離修改前亦16 passed / 27 failed，失敗集合相同。證據 /tmp/surge-remove-baseline.txt、/tmp/surge-remove-current.txt。
- 沒有證據顯示程式會自行改寫；此次明確移除仍有效的舊限制，保留行情變化下的動能及帳戶重驗，不宣稱每次軌外都必成交。

## 2026-09-11 移除MA3進場ATR距離限制

- 移除MA3距同側外軌至少0.10ATR的進場限制，穿軌及延續只需嚴格軌外；保留CK動能增強、價格及MA3順向與其他風控。
- 更新原距離測試為微小軌外距離可入場，快取送單仍必須新快照重驗；移除僅測已刪除距離helper的無效資料案例，既有異常資料風控沿用。
- 七份相關專項229 passed；所有持倉出口不變。

## 2026-09-11 動能恢復及MA3離軌入口

- 龍蝦07:23:39平空原因為CK_FADING_MA3_TURN_EXIT，淨損1.6158U；與PEPE原因相同，不代表已核實兩次進場行情相同，缺少完整進場快照，不使用圖片像素推算。
- 發現入口僅拒絕連續衰退，等速及尚未回升也可能放行；改為最新已收線順向位移嚴格大於前一次，並新增MA3距外軌至少0.10已收線ATR。出口不變。
- 新增44項動能／距離／快取實際送單測試，發現並修正快取沿用舊frame缺口，送單重新取得快照。
- 原4份入口回歸基準133 passed；收緊後初跑101 passed / 32 failed，失敗為等速CK的舊放行預期。有效共用樣本改為最近CK位移增強，等速明確改為拒絕，未放寬風控。
- 最終entry_recovery、fading_exit、ma3_continuation、ma3_cross_entry、ck_momentum、hard_stop、waterfall_threshold共235 passed。git diff --check通過。

## 2026-09-11 CK衰退出口新增通道狹窄

- 入口既有CK衰退攔截不變；出口新增最新已收線相對軌寬≤前20根中位數75%，再與CK衰退及MA3顯著反轉共同判斷。固定ATR倍數不適合判斷，因KC軌寬本身為ATR固定倍數。
- 已觸發待平仍重試；寬通道反轉不追溯觸發。既有其他出口不變；不宣稱可避免07:17歷史平倉，缺少當時完整快照。
- fading_exit、ma3_continuation、hard_stop共108 passed，含新增12項狹窄邊界、無效資料、多空及保護狀態測試。
- waterfall_threshold及profit_protection回歸：52 passed / 18 failed；隔離HEAD基準同為52 passed / 18 failed，失敗集合一致，新增0。證據 /tmp/ck-narrow-baseline.txt、/tmp/ck-narrow-regression.txt。

## 2026-09-10 單根瀑布放寬至1.5 ATR

- 使用者要求1 ATR設寬；本輪採1.5 ATR，多空及盤中／已收線補判一致。獨立CHANNEL_WATERFALL_BODY_ATR，保持原始開盤與上一根已收線ATR，雙異常各0.5 ATR及開倉異常攔截不變。已觸發待平仍重試，其他出口不變。
- 新增33項邊界與執行測試，含1／1.15／1.499不觸發、1.5觸發、多空、當根進場、掃描／逐報價、失敗持久化重試、影線與即時ATR隔離、雙異常與開倉攔截不變；連同風控及延續專項共134 passed。
- 指定三份交易回歸加immediate_exits、single_abnormal_removed、fading_exit、hard_stop：修改前237 passed / 67 failed；最終245 passed / 67 failed，失敗集合完全相同。只更新瀑布相關舊1／1.1 ATR測試數據至1.5 ATR並補低於門檻的拒絕案例，不修改其他歷史斷言，非全套通過。
- 龍蝦06:45:19的原開盤0.038023、反推報價約0.037849、歷史ATR約0.0001508，其約1.15 ATR實體不會單獨觸發新版瀑布；不推論其後不會由其他出口平倉。
- 證據：/tmp/waterfall-wider-before.txt、/tmp/waterfall-wider-focused.txt、/tmp/waterfall-wider-final.txt。

## 2026-09-10 MA3穿軌未成交延續與正常平倉無回調

- 使用者要求穿軌未成交可延續，並明確選擇「只取消正常平倉回調；同根限次及異常回踩保留」。因此正常平倉最快下一根起符合條件即評估，沒有新增同根豁免或無條件反手。
- 共用入口改為KC與即時MA3順向、即時MA3及報價嚴格在同側外軌外，不必前根MA3在軌內或再次穿越。一般新倉、掃描、逐報價、快取與正常／異常重開送單共用，保留CK衰退、反向異常、行情時效、候選失效鎖與帳戶風控。
- 正常獲利票據不等回調，CK衰退MA3平倉票據匹配成功成交後下一根可用軌外延續解除；瀑布／雙異常同向回踩原樣保留。所有出口、1U／20%鎖利及資金參數不變。
- 9份專項224 passed，包含本次新增16項，驗證多空錯過穿越、一般／快取／重開實際送單、正常平倉下一根延續、平倉同根禁開、異常回踩及衰退平倉匹配成交。
- 指定5份回歸加breakout_only：181 passed / 86 failed；隔離c26f85f基準186 passed / 81 failed。新增5項均為confirmed_rules舊實體拒絕預期（十字／反色各多空，跳空空單），因本輪允许MA3已在軌外延續而不再拒絕；保留歷史斷言，不為通過而恢復舊實體限制。合計405 passed / 86 failed，非全套通過。
- 證據：/tmp/ma3-continue-final.txt、/tmp/ma3-continue-regression.txt、/tmp/ma3-continue-baseline.txt。

## 2026-09-10 多空MA3穿越KC外軌入口

- 使用者確認多空都改：KC已收線向上時MA3上穿上軌開多，向下時下穿下軌開空，取代兩根同色有效實體確認。以前根已收線MA3／外軌與最新報價重算即時MA3／當根外軌判斷穿越；前者在軌內側或碰軌、後者嚴格軌外，最新價亦在外側。不用當根影線推測穿越先後，前根MA3已在軌外不追入。
- 掃描、逐報價、正常／異常重開、快取及最後送單使用同一aligned_entry。最新報價退回導致MA3未穿軌即拒絕。原CK方向、即時MA3方向、衰退、異常、行情與帳戶風控沿用；候選失效鎖不清除。
- CK衰退MA3平倉後next_breakout票據改等平倉後新K的MA3穿軌，仍匹配成功成交；不再等兩根實體，也不允許平倉當根反手。異常同向回踩及正常重開票據的其他限制保留。
- 1U／20%淨浮盈鎖利、CK衰退加MA3峰谷0.10ATR出口、緊急瀑布／雙異常與帳戶硬止損不變。API、診斷與進場日誌已同步。
- 5份專項140 passed，包含新增MA3穿軌30項，涵蓋多空、十字K、碰軌、已在軌外、報價重驗、正常重開及逐報價實際送單。fading_exit的平倉後等待測試依新授權更新，保留同根拒絕與舊穿軌拒絕。
- 指定5份回歸加breakout_only、ck_momentum：206 passed / 81 failed；隔離0ea4dfe基準216 passed / 71 failed。新增10項失敗皆為舊K實體／影線確認拒絕預期（breakout_only 8、confirmed_rules 2），對應本次明確取消的條件，未修改這些歷史斷言。合計346 passed / 81 failed，非全套通過。
- 證據：/tmp/ma3-cross-focused-final.txt、/tmp/ma3-cross-regression.txt、/tmp/ma3-cross-baseline.txt。

## 2026-09-10 CK衰退加MA3峰谷出口

- 保留1U啟動／最高淨浮盈回吐20%全平；新增CK最近4根已收線中軌的3次順向位移嚴格連續縮小，且進場後觀察MA3順向峰谷再明顯反向才平倉。沿用0.10ATR固定尺度、反向斜率及峰谷回退雙門檻；不要求碰外軌。鎖利已啟動仍適用；硬止損、緊急出口及已觸發浮盈鎖利優先。
- CK無效與未衰退分開判定，不能把資料無效當成衰退。MA3未衰退時的轉彎不留待平，避免之後追溯觸發。新狀態獨立保存於持倉／metadata，紙上及測試網白名單加入；失敗重啟後繼續平倉。
- 成功平倉後next_breakout票據等待兩根全新收線破軌，第一根須晚於實際平倉K；匹配方向、原因與請求時間的成功成交，包含延遲成交及重啟恢復。沒有直接反手，也不限制新破軌方向。掃描與送單共用解除；診斷只讀，舊重開／快取票據不能轉換或繞過。
- 五份專項130 passed，包含本次新增55項。指定五份交易回歸加breakout_only、immediate_exits：244 passed / 73 failed；隔離274226a加CK衰退專項264 passed / 73 failed，失敗名稱完全一致。合計374 passed / 73 failed，新增失敗0；不宣稱全套通過。
- 證據：/tmp/fading-focused-final.txt、/tmp/fading-regression.txt、/tmp/fading-baseline.txt。暫存scratch修補腳本不納入提交。

## 2026-09-10 恢復1U／20%浮盈鎖利及兩根破軌確認

- 最新使用者確認：淨浮盈扣雙邊費用與預估平倉滑點後達1U啟動，最高淨浮盈回吐20%全平。進場滑點已含在實際進場成交價；保留匹配既有倉的已知淨峰值、更有利保護線及已觸發待平重試。
- 入口統一兩根已收線有效同色實體：第一根穿出外軌，第二根確認，送單最新報價仍在同側外軌外。停用盤中峰谷、單純軌外追入、直接反手與CK／MA3獨立出口；保留緊急瀑布／雙異常及帳戶硬止損。其餘入口風控不變。
- 接續工作區已有未提交實作並核查；最後送單使用最新ticker報價重新驗證。舊反手票據不再有同根豁免，正常平倉下一根起重新驗證破軌。
- 六份專項171 passed / 4 failed；四項是舊MA3待平及保護啟動後不走瀑布的預期，隔離HEAD全部可重現。指定五份回歸138 passed / 69 failed，失敗亦皆在隔離HEAD可重現。合計309 passed / 73 failed，新增失敗0，不宣稱全套通過。
- 證據：/tmp/restored-final.txt、/tmp/restored-regression.txt、/tmp/restored-baseline.txt、/tmp/restored-emergency-baseline.txt。未納入scratch暫存修補／稽核腳本。

## 2026-09-10 圖示核查與異常票據方向解除

- 更正前次結論：成交紀錄的 `EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL` 是程式原因，不代表圖上後續大跌就是平倉當下走勢。現行門檻為 2 × 0.50 ATR，即當根反向實體達 1 ATR；本輪未調整出口參數。
- 下表時間為介面 Asia/Taipei（UTC+8）。滑點前報價由保存的8位成交價除以 `(1 - 0.0001)` 估計，有成交價四捨五入誤差；ATR由歷史圖表前根CK寬重建。未保存原逐報價，不能宣稱精確重演觸發瞬間。

| 幣種 | 平多時間 | 當根開盤 | 估計滑點前報價 | 前根ATR／瀑布門檻 | 反向實體估計 |
|---|---|---:|---:|---:|---:|
| PEPE | 09/11 05:14:54 | 0.0033211 | 0.00331680168 | 0.00000351 | 0.00000429832 |
| 龍蝦 | 09/11 05:08:30 | 0.038612 | 0.03844900490 | 0.0001629 | 0.00016299510 |

- 舊異常票據保留平倉前方向，存在時接管一般入口；舊解除僅涵蓋平空後有效綠K。這是確認存在的阻擋路徑，但當時票據已不在，日誌只保留空槽等待，不能證明整段未開倉都只有此原因。
- 新增純讀判斷：空手、異常 `outer_cycle` closed票據、原因／方向／請求時間匹配成功成交，且已過實際成交當根；CK確認相反方向、即時MA3同向、無CK衰退及反向異常時解除舊方向票據。掃描、逐報價與送單前共用；診斷預覽同一結果但不修改狀態。
- 回到一般入口後仍驗證外軌／盤中峰谷、行情、帳戶與每根限次。沒有新增同根反手例外；原直接反手、固定階梯與緊急／硬止損不變。
- 因果回放僅用已收線資料及下一根開盤，當根H/L/C全部重置為開盤並重新計算指標：PEPE首個可解除分鐘開盤為05:15、首個符合外軌空訊號為05:17；龍蝦分別為05:11與05:12。這些是策略條件回放，不含歷史即時報價時效／帳戶風控或真實成交保證，也不能以分鐘資料確認最早盤中峰谷。
- 證據：`scratch/abnormal-direction-evidence.json`。重現：`PYTHONPATH=. python3 scratch/replay-abnormal-direction.py`。回放票據以已知成交時間合成，僅驗證新條件，不宣稱恢復當時票據。
- 新增方向解除測試78項通過，含多空、匹配成交、重啟、延遲成交當根不解除、資料／方向不符、掃描／報價／快取／送單、資金／限次／執行風控及診斷只讀。加直接反手、階梯、硬止損、CK衰退、每根限次與反向異常專項共214項通過。
- 擴充相關回歸308通過、13失敗；13項均在隔離HEAD基準可重現（回踩8、舊綠K入口3、舊空間2）。两項舊快取測試更新為「新資料既無有效綠K也無確認CK方向」，保留不得沿用舊快取解除的拒絕行為。
- AGENTS指定五份回歸129通過、78失敗，失敗名稱與隔離HEAD基準完全一致；無新增失敗，不宣稱全套通過。輸出：`/tmp/abnormal-release-focused.txt`、`/tmp/abnormal-release-final-tests.txt`、`/tmp/abnormal-required-current.txt`、`/tmp/abnormal-required-baseline.txt`。

## 2026-09-10 CK 方向與同根反手

- 用戶確認 CK 反向先平錯向倉並同根反手，不等待 MA3／MA15。一般新倉與正常重開移除 MA 排列及斜率門檻；CK 取最近兩根已收線中軌嚴格升降及同側外軌不逆向，未收線不決定方向。
- 原反手函式在有持倉時直接返回；本次接通獨立 CK 反手流程，先執行硬止損檢查，再平掉 CK 反向倉。已啟動獲利保護也適用 CK 反手。
- 成功平倉紀錄與票據匹配才豁免當根禁開；反手新倉不等再破軌或兩根反色，仍重驗方向、報價、反向異常、淨利空間、日熔斷及帳戶風控。平倉失敗保留待平；成功後新倉不合格則空手，票據過期不得沿用。
- 票據持久化於紙上及測試網帳戶；重啟可恢復已成交平倉的重開，已消耗票據不可重用。
- 新增 CK 反手專項 33 項，涵蓋多空、通道內反手、已啟動保護、逐報價、失敗、同根去重、重啟及風控。與方向、持續走勢、反向異常、破軌四份測試合計 242 passed / 2 failed；2 項仍為舊無幅度 MA3 出口預期。
- 前輪核心專項加 CK 反手（持久化測試加入前）164 passed。指定五份回歸加獲利保護 164 passed / 80 failed；新增等待原因斷言已由 MA_ALIGNMENT_WAIT 同步為 CK_DIRECTION_WAIT，未放寬拒絕行為。非全套通過。
- 這些是離線測試，不代表已驗證圖示歷史逐報價或實際反手獲利；CK 僅描述當前已確認方向。

## 2026-09-10 取消同根價格回調等待並發布

- 龍蝦先前 21:47 至 21:48 與 PEPE 保存日誌均出現 KC_INTRABAR_PULLBACK_WAIT；另有 MA_ALIGNMENT_WAIT 與 SUSTAINED_TREND_WAIT，不能將所有未成交都歸因於回調。
- 新倉、突破、延續、快取及重開取消同根先回調再轉向要求；保留報價時效、方向、反向異常、結構淨利空間、每根限次及異常平倉票據的專用回踩規則。
- 送單測試 24 組驗證第一個有效報價即可成交，涵蓋多空、突破／延續、即時反色、新倉／快取／正常重開，使用真实結構目標檢查；另 8 組報價時效檢查。這是離線帳戶替身送單驗證，不代表實際服務已自動成交。
- 十份專項測試 244 passed / 6 failed；增加 8 組報價測試後該檔 32 passed。6 項失敗均位於 test_channel_profit_room.py 的舊中軌出口預期，未為通過而恢復中軌出口。
- 指定五份回歸加獲利保護 164 passed / 80 failed，不能宣稱全套通過。
- 同次發布包含先前未提交的每次進場結構淨利空間檢查與 MA3 真正線方向反轉修正；不要求 MA3 碰軌或穿軌，保留 0.10 ATR 幅度與保護分流。

本輪使用者確認沿用現行出口，不恢復中軌加即時反向實體平倉。本輪未修改策略、測試斷言或重啟服務。

- 目前出口、突破、第三根 K 色取消、同根回調、平倉去重、獲利保護、每根限次及方向相關測試：261 通過、11 失敗。
- 五份指定回歸（channel_swing、channel_position_path、channel_swing_execution、channel_confirmed_rules、channel_stop_preservation）：138 通過、69 失敗。
- 合计 399 通過、80 失敗。失敗測試名稱與 /tmp/pepe-entry-baseline 修改前副本完整一致，未新增失敗。
- 所指的中軌兩組測試：12 通過、26 失敗；包含於上述指定回歸，不重複計數。
- _channel_swing_action 有持倉時返回 KC_POSITION_EXITS_MANAGED，實際出口由持倉流程處理異常 K、MA3 轉彎與獲利保護；舊中軌測試直接要求 helper 回傳 EXIT，與現行規則／分工不符。
- 其他既有失敗包含獲利保護 10% 收緊欄位與舊出口優先序，未為通過測試擅自改動行為。
- 8006 API is_running=true、paper_trading=true；此次查詢可見成交紀錄中未發現 Channel Swing 自動開倉，不能宣稱自動成交驗證完成。

## 原始輸出

- /tmp/current-rules-validation.txt
- /tmp/required-rules-validation.txt
- /tmp/baseline-continued-validation.txt
- /tmp/middle-exit-audit.txt

## 既有失敗清單

- FAILED tests/test_channel_confirmed_rules.py::test_confirmed_signal_fills_once_on_same_scan[True-LONG]
- FAILED tests/test_channel_confirmed_rules.py::test_confirmed_signal_fills_once_on_same_scan[True-SHORT]
- FAILED tests/test_channel_confirmed_rules.py::test_rejected_reverse_retry_expires_with_bar[LONG]
- FAILED tests/test_channel_confirmed_rules.py::test_rejected_reverse_retry_expires_with_bar[SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.39-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.39-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.4-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.4-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.4106594857-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.4106594857-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.5-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.5-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.6-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.25-0.6-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.39-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.39-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.4-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.4-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.4106594857-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.4106594857-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.5-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.5-SHORT]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.6-LONG]
- FAILED tests/test_channel_position_path.py::test_exit_independent_of_live_and_closed_space[0.5-0.6-SHORT]
- FAILED tests/test_channel_position_path.py::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.0-False-True-LONG]
- FAILED tests/test_channel_position_path.py::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.0-False-True-SHORT]
- FAILED tests/test_channel_position_path.py::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.2-False-True-LONG]
- FAILED tests/test_channel_position_path.py::test_live_long_adverse_body_exits_at_or_outside_favorable_rail[0.2-False-True-SHORT]
- FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1--0.1-LONG]
- FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1--0.1-SHORT]
- FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.0-LONG]
- FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.0-SHORT]
- FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.1-LONG]
- FAILED tests/test_channel_position_path.py::test_middle_exit_uses_live_body_independently_of_closed_candle[-0.1-0.1-SHORT]
- FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.39-LONG]
- FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.39-SHORT]
- FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.4-LONG]
- FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.4-SHORT]
- FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.41-LONG]
- FAILED tests/test_channel_position_path.py::test_middle_strict_cross_and_space[True-0.41-SHORT]
- FAILED tests/test_channel_position_path.py::test_normal_opposite_break_requires_postentry_path[LONG]
- FAILED tests/test_channel_position_path.py::test_normal_opposite_break_requires_postentry_path[SHORT]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.25-False-LONG]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.25-False-SHORT]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.25-True-LONG]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.25-True-SHORT]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.5-False-LONG]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.5-False-SHORT]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.5-True-LONG]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.5-True-SHORT]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.6-False-LONG]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.6-False-SHORT]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.6-True-LONG]
- FAILED tests/test_channel_position_path.py::test_raw_middle_signal_independent_of_ma3_and_space[0.6-True-SHORT]
- FAILED tests/test_channel_profit_protection.py::test_engine_passes_live_frame_and_preserves_exit_priority[EXIT]
- FAILED tests/test_channel_profit_protection.py::test_engine_passes_live_frame_and_preserves_exit_priority[HOLD]
- FAILED tests/test_channel_profit_protection.py::test_engine_passes_live_frame_and_preserves_exit_priority[REVERSE]
- FAILED tests/test_channel_profit_protection.py::test_first_opposite_tick_already_beyond_ten_percent_exits[LONG]
- FAILED tests/test_channel_profit_protection.py::test_first_opposite_tick_already_beyond_ten_percent_exits[SHORT]
- FAILED tests/test_channel_profit_protection.py::test_general_exit_has_priority_over_profit_close
- FAILED tests/test_channel_profit_protection.py::test_reopened_position_reclassifies_without_previous_ten_percent_lock[CHOPPY]
- FAILED tests/test_channel_profit_protection.py::test_reopened_position_reclassifies_without_previous_ten_percent_lock[SMOOTH]
- FAILED tests/test_channel_profit_protection.py::test_stack_live_opposite_tightens_ten_dollar_peak_to_nine[LONG]
- FAILED tests/test_channel_profit_protection.py::test_stack_live_opposite_tightens_ten_dollar_peak_to_nine[SHORT]
- FAILED tests/test_channel_profit_protection.py::test_tightening_on_opposite_tick_honors_previously_observed_peak
- FAILED tests/test_channel_swing.py::test_aligned_trend_can_enter_without_breakout_body_confirmation
- FAILED tests/test_channel_swing.py::test_favorable_waterfall_live_turn_exits_on_long_adverse_body[LONG]
- FAILED tests/test_channel_swing.py::test_favorable_waterfall_live_turn_exits_on_long_adverse_body[SHORT]
- FAILED tests/test_channel_swing.py::test_flat_scan_can_detect_recent_upper_peak_without_exit_state
- FAILED tests/test_channel_swing.py::test_live_price_above_upper_rail_waits_without_valid_closed_body
- FAILED tests/test_channel_swing.py::test_long_does_not_lock_without_ma_cross
- FAILED tests/test_channel_swing.py::test_outer_reentry_later_clean_continuation_can_enter_after_spike_wait
- FAILED tests/test_channel_swing.py::test_outer_reentry_macro_trend_entry_long_on_upper_kc_structure_break
- FAILED tests/test_channel_swing.py::test_outer_reentry_macro_trend_entry_short_on_lower_kc_structure_break
- FAILED tests/test_channel_swing.py::test_outer_reentry_normal_two_candle_breakout_remains_tradable
- FAILED tests/test_channel_swing.py::test_outer_reentry_spike_breakout_is_not_traded
- FAILED tests/test_channel_swing.py::test_outside_long_signal_waits_after_opposite_closed_body
- FAILED tests/test_channel_swing.py::test_peak_exit_requires_normal_two_bar_reversal_before_short
- FAILED tests/test_channel_swing.py::test_short_does_not_lock_without_ma_cross
- FAILED tests/test_channel_swing.py::test_sudden_ma15_upper_rail_jump_is_not_convergence

## 2026-09-10 反向長 K 事前風險研究續驗

- 完成兩幣舊資料重跑，以及各 2,191 根近期資料的固定模型驗證；區分盤中反向觸及與收盤反向長實體。
- 收盤長實體：舊驗證 748 個候選，異常率 24.5% → 21.9%，錯過正報酬代理 39/136；近期 174 個候選，異常率 23.6% → 22.0%，錯過代理 6/35。近期 PEPE 多空保留後異常率均上升，未支持跨幣部署。
- 研究資料因果性與統計測試 4 通過；交易策略、服務與出口未修改，沒有新增攔截或重啟。未重跑上述既有交易測試，不能將研究檢查稱為全套回歸。
- 詳細報告：`reports/channel_entry_risk_current/ADVERSE_BODY_REVIEW.md`。只有分鐘開盤候選，不能估計實際同根回調成交的獲利機會損失。

## 突破未成交後允許延續

已依用戶修正加入兩根軌外同色已收線 K 延續入口，詳細規則與驗證見 `scratch/outside-continuation-validation.md`。出口未更動；新增針對性測試 92 通過，指定回歸仍為原有 69 個失敗，無新增失敗。

## 延續入口上線後邏輯測試（2026-09-10）

本輪依用戶要求執行 13 份離線邏輯測試，共 416 項：336 通過、80 失敗；失敗名稱與本文件原有 80 項清單完全一致，沒有新增失敗。未修改策略、測試斷言或重啟服務。

| 測試檔 | 通過 | 失敗 |
|---|---:|---:|
| `test_channel_outside_continuation.py` | 14 | 0 |
| `test_channel_breakout_only.py` | 54 | 0 |
| `test_channel_intrabar_execution.py` | 24 | 0 |
| `test_channel_immediate_exits.py` | 36 | 0 |
| `test_channel_live_ma3_exit.py` | 14 | 0 |
| `test_channel_profit_protection.py` | 26 | 11 |
| `test_channel_candle_frequency.py` | 18 | 0 |
| `test_close_deduplication.py` | 12 | 0 |
| `test_channel_swing.py` | 44 | 15 |
| `test_channel_position_path.py` | 52 | 50 |
| `test_channel_swing_execution.py` | 3 | 0 |
| `test_channel_confirmed_rules.py` | 33 | 4 |
| `test_channel_stop_preservation.py` | 6 | 0 |

既有失敗分布：獲利保護 11、Channel Swing 15、持倉路徑 50、confirmed_rules 4。這些測試仍未通過，不能宣稱全套正常；本次僅確認未新增失敗。

原始輸出：`/tmp/logic-validation-current.txt`；JUnit：`/tmp/logic-validation-current.xml`。

## 2026-09-10 進場當根反向異常修正

- 當根原始開盤價計算反向異常／瀑布，不改用成交價重新累計；只平原倉。
- 執行 channel_swing、channel_position_path、channel_swing_execution、channel_immediate_exits、channel_protected_only_exit、close_deduplication 六份測試。
- 修改前 173 passed / 71 failed；修改後 187 passed / 71 failed，失敗清單完全相同。新增案例涵蓋多空、成交價未再移動、普通 K 不出場與失敗重試。未宣稱全套通過。

## 2026-09-10 送單前反向異常攔截

- 共用 live_adverse_entry_safe：當根原開盤與最新價形成反向異常／瀑布則新倉與重開皆等待；最終送單另重驗最新 ticker，快取不能繞過。無效 ATR 禁入，小反色與順向實體不因本檢查禁入。
- 新增 tests/test_channel_adverse_entry.py 共 30 項通過，含多空、門檻邊界、行情無效、最新價、快取與重開。
- 本輪指定回歸 channel_swing、channel_position_path、channel_swing_execution、channel_outside_continuation、channel_immediate_exits：修改前 157 passed / 71 failed；加上新測試後 187 passed / 71 failed，失敗清單完全相同。

## 2026-09-10 以用戶兩條出場分流核查

- 實作先更新 protection，再依 armed 分流：已啟動不執行異常／瀑布／MA3 helper，並清除相關待平旗標；未啟動依既有異常／瀑布與進場後 MA3 轉彎判斷出場。
- 專項驗證 scratch/test_exit_policy_audit.py 加既有回吐與異常失敗重試案例：36 passed。涵蓋多空、未啟動、同次報價剛啟動、已啟動、達回吐線及舊待平旗標。
- 細節：保護仍取淨利 1 USDT 底線、保留峰值 80% 與既有更有利保護價；因此不是每次都必須回吐滿 20%。MA3 同根進場須先實際觀察順向再反向。
- 8006 API is_running=true / paper_trading=true，服務目錄吻合。API 策略說明仍是舊版「異常／MA3不單獨平倉」，與未啟動分流不一致。此次只核查並記錄，未修改交易邏輯或重啟。

## 2026-09-10 移除單根反向異常 K 出口

- 刪除 LIVE_ADVERSE_ABNORMAL 觸發與重試，清除持倉及 metadata 的該待平標記。保留瀑布、雙異常、MA3、獲利保護及送單前異常攔截。
- 逐報價、待平清理與保護專項 64 passed。指定回歸修改前 161 passed / 65 failed，修改後含新測試 171 passed / 65 failed，無新增失敗。

## 2026-09-10 單向走勢篩選

- 已加入最近 6 根已收線的 CK 持續順向、高低點推進、方向效率、中軌穿越與實體重疊篩選；具體預設见 AGENTS.md 最新節。掃描、新倉、重開及快取送單共用，持倉出口不變。
- tests/test_channel_sustained_trend.py：26 passed，涵蓋多空有效趨勢、波浪／重疊／回調／資料無效拒絕、即時 K 隔離與快取及重開攔截。
- 指定回歸含 channel_swing、channel_position_path、channel_swing_execution、single_abnormal_removed、immediate_exits：修改前 159 passed / 65 failed；加新測試後 185 passed / 65 failed，失敗清單相同。
- 同步 API 策略文字為現行入口與保護分流。未使用圖示後續走勢擬合，無歷史績效改善或保證獲利結論。

## 2026-09-10 GitHub 發布驗證與破軌雙入口

- 最後授權：有效上下軌突破可略過新增六根走勢篩選；單向延續仍受篩選。共用其他進場風控，出口不變。
- 現行專項 sustained_trend、adverse_entry、single_abnormal_removed、immediate_exits、exit_policy、close_deduplication：168 passed。
- 指定五份舊回歸 swing、position_path、swing_execution、confirmed_rules、stop_preservation：138 passed / 69 failed；失敗清單與隔離 HEAD 基準完全相同。未宣稱全套通過。
- 首輪兩項重疊測試因資料同時符合新增破軌例外而失敗，已改用非破軌重疊案例且專項重跑通過。異常進場測試更新成符合單向條件的行情，未放寬策略。
- 提交包含現行程式、API說明、授權與驗證、正式測試；scratch 修補腳本與歷史暫存 audit 不納入。


## 2026-09-10 移除殘留 MA3 出口與恢復 Channel Swing 硬止損

- 使用者授權修正、邏輯測試、直接重啟及推送 GitHub。移除未啟動保護時 MA3 轉彎平倉分支並清除持倉／metadata 舊待平欄位；保留瀑布、雙異常、獲利回吐與進場規則。
- 新增共用 channel_hard_stop：逐報價持倉路徑與紙上／測試網 update_positions 在提前略過策略出口之前，檢查既有保證金 10% 與價格逆向 2% 門檻，先達先平。75 USDT、5 倍約對應毛虧損 7.5 USDT；費用及跳價可能使最終淨損更大。屬本地市價平倉，未新增交易所掛單。
- 用實際數量計算毛損，保護已啟動也不豁免；保存待平狀態，metadata 重啟恢復後行情回復仍重試，沿用帳戶平倉鎖／既有失敗重試規則。硬止損不建立獲利重開票據。
- 專項 181 passed（hard_stop、immediate_exits、single_abnormal_removed、close_deduplication、candle_frequency、sustained_trend、adverse_entry）。涵蓋多空、兩個門檻、同根報價、兩種帳戶、MA3 待平清除、無效報價與待平恢復。
- 指定五份回歸：修改前與後均 138 passed / 69 failed，失敗名稱完全相同；獲利保護 26 passed / 11 failed，11 項皆在本文件已有失敗清單。總計 345 passed / 80 failed，不能宣稱全套通過。
- 原始測試結果：/tmp/exit-fix-before.txt、/tmp/exit-fix-after.txt、/tmp/exit-fix-targeted.txt、/tmp/exit-fix-profit.txt。

- 部署核查：2026-09-10 12:57:28 UTC 重啟 binance-8006.service；/api/status 回報 is_running=true、paper_trading=true、port=8006，策略文字已包含「MA3轉彎不平倉」。


## 恢復明顯 MA3 峰谷反轉（2026-09-10）
- 使用者要求恢復明顯峰谷反向平倉、小幅抖動不平。實作預設反向幅度 0.10 ATR：首次進場後有效觀察固定上一根已收線 ATR，MA3 用兩根已收線收盤及最新價計算；先觀察順向新極值，從極值反向達門檻才平。首次報價不追溯進場前峰谷。
- 僅未啟動保護持倉適用；硬止損優先，瀑布／雙異常與已啟動獲利回吐不變。峰谷及待平狀態寫入持倉與metadata，重啟恢復；舊無幅度 MA3 旗標仍清除。
- 專項196 passed；指定五份回歸加獲利保護164 passed / 80 failed，失敗名稱與上輪完全相同，無新增失敗。總計360 passed / 80 failed，非全套通過。
- 輸出：/tmp/ma3-restore-targeted.txt、/tmp/ma3-restore-regression.txt。

- 部署驗證：8006重啟後 is_running=true、paper_trading=true，API策略說明已更新為MA3峰谷反向0.10 ATR。


## 2026-09-10 盤中峰谷轉向入口完成

- 新增逐報價峰谷入口：CK向上、同根報價先跌後升評估多單；CK向下、先升後跌評估空單。第一個嚴格轉向即有效，無額外K線、實體、MA或ATR反彈幅度要求；不使用當根影線推測報價先後。既有上下軌突破及受篩選延續保留。
- WebSocket與掃描共用；正常平倉後下一根可重新觀察峰谷，異常票據仍須原回踩。新倉、快取、重開及最後送單重驗方向、報價、淨利空間、異常及帳戶風控；同幣鎖、每根限次沿用。換根、方向失效、持倉、重啟或報價中斷重新觀察；反向報價撤銷舊轉向。
- 持倉出口、資金與槓桿未改。API策略與正常重開文字同步。
- 新增峰谷測試50項全過；涵蓋多空、當根第一個轉向、掃描與WebSocket送單、正常／異常重開、報價順序、缺失／過期／跨分鐘、去重、失敗重試、最後報價變更、空間與帳戶攔截。
- 最終18份測試：501 passed / 82 failed。隔離HEAD 50406c0基準六份164 passed / 80 failed、11份現行專項287 passed / 2 failed；最後失敗名稱與兩份基準聯集完全相同，新增失敗0。兩項專項既有失敗為exit_policy unarmed-ma3 LONG／SHORT；未改舊測試或出口以迎合斷言，不能宣稱全套通過。
- 原始輸出與JUnit：/tmp/live-pivot-before.{txt,xml}、/tmp/live-pivot-targeted-before.{txt,xml}、/tmp/live-pivot-final.{txt,xml}。離線測試不代表實際市場已自動成交。


## 2026-09-10 PEPE破軌與MA3小弧度修正

- PEPE實際外軌訊號已進送單；23:02–23:03台北時間因淨空間約-0.08%低於0.15%、之後無有效前低而禁止新倉，未放寬門檻。
- 修正峰谷盤中快照暫時失效被鎖整根的問題；保留既有失效鎖、每根限次與全部風控。
- MA3實際反向斜率與峰谷回退均須達固定0.10 ATR；小弧度不平，其他出口不變。圖表診斷純讀，不推進或清除交易狀態。
- 主要現行路徑342 passed；全59檔1367 passed / 382 failed / 40 skipped。失敗集合與隔離4f7da5f基準完全相同，新增失敗0，非全套通過。
- 完整每檔結果、失敗清單與證據見 `scratch/pepe-entry-and-ma3-validation.md`。


## 固定2U階梯與CK只平倉（2026-09-10）
- 用戶確認淨利4U鎖2U、6U鎖4U，以此類推，扣雙邊費用與滑點後回落才平。取消MA3出口及百分比回吐；緊急瀑布／雙異常在階梯前後有效，硬止損保留，CK只平倉不立即反手。
- 舊百分比狀態從首次新版淨利觀察建立階梯；MA3待平清除。新階梯待平持久化重試；舊CK票據不再授權新倉。
- 新策略、single_abnormal_removed、close_deduplication、hard_stop共63 passed，涵蓋多空、成本、階梯、恢復、緊急出口及平倉失敗重試。
- 規定三份交易回歸加ck_reverse及profit_protection：隔離b671f83基準158 passed / 76 failed；修改後140 passed / 94 failed。新增18項均為舊1U／20%鎖利或立即反手預期。不宣稱全套通過。證據 /tmp/fixed-baseline.txt、/tmp/fixed-regression.txt、/tmp/fixed-focused.txt。


## 移除獲利空間（2026-09-10）
- 已按用戶要求移除Channel Swing送單與診斷的結構淨空間攔截，兼容helper標示停用，清除快取舊目標。其他入口安全條件與固定階梯出口不變。
- all_entry_room更新成多空／快取／重開及遠近／缺少目標均可通過空間關卡；加fixed_steps、adverse_entry、candle_frequency及close_deduplication共102 passed。
- 規定三份交易回歸：隔離2b6ecf3及修改後均99 passed / 65 failed。結果見/tmp/remove-room-before.txt、/tmp/remove-room-after.txt、/tmp/remove-room-focused.txt；非全套通過。


## CK動能衰退只停新倉（2026-09-10）
- 最新4根已收線中軌的3次順向位移連續縮小時暫停該方向進場；每根重新評估，動能恢復即重新走既有風控。此為實作預設，不保證預知頂底。
- 峰谷、外軌、新倉／重開與送單前重驗共用；只讀診斷顯示KC_MOMENTUM_FADE_WAIT。持倉不因衰退平倉，固定階梯與原出口保留。
- 6份專項122 passed，包含多空、恢復、即時K隔離、峰谷觀察、新倉快取重開與持倉不受影響。
- 規定三份回歸，隔離f9a1470與修改後皆99 passed / 65 failed，失敗集合相同。證據/tmp/ck-fade-focused.txt、/tmp/ck-fade-before.txt、/tmp/ck-fade-after.txt；非全套通過。


## 成功平倉直接反向（2026-09-10）
- 按用戶接受的建議，固定階梯／CK成功平倉後同根評估反向新倉；直接反手豁免CK、MA3及一般入口／衰退條件，仍重驗報價、反向異常與帳戶風控。緊急／硬止損與手動不反向。
- direct_reverse票據須匹配成功成交、方向、原因、請求時間；僅平倉當根有效，一次成功反手即不可重用。失敗可於當根重試，重啟恢復需成交佐證。
- 反手倉等待CK首次同向後才重新啟用CK反向出口，旗標加入兩種帳戶持久化白名單。階梯鎖利與緊急出口始終有效。
- 最終9份專項184 passed，含逆CK／MA、逐報價階梯平倉反手、CK平倉反手、失敗、過期、重啟、成交防偽／防重複及CK等待恢復。
- 指定三份交易回歸加fixed_steps：隔離0069b36基準115 passed / 65 failed；修改後115 passed / 65 failed，失敗集合相同。兩項舊只平倉測試按新授權更新成「無新報價時保留反手票據、不送單」，不放寬風控。
- 證據/tmp/direct-focused.txt、/tmp/direct-before.txt、/tmp/direct-after.txt；非全套通過。

## 2026-09-11 1000PEPE 阻擋診斷與末端守門

- 線上狀態核查：1000PEPE/USDT 當時已有一筆手動接管 LONG，持倉數達 `MAX_SLOTS=2`，可用餘額為 `0.0`；因此目前首先被已有持倉、槽位及資金風控擋住，不是單一 PEPE 訊號原因。
- 歷史事件另見 `KC_WAIT_NET_PROFIT_GIVEBACK`、異常瀑布平倉後的回踩票據，以及 CK 方向／動能、即時 MA3 順向與外軌條件等待。近期平倉記錄包含瀑布 -0.98U 與保護線 -0.19U，不能把「有保護狀態」解讀成必然獲利。
- 將既有 `_channel_mature_outer_trend_is_weak` 接入共用 `_channel_swing_action`：CK／外軌走勢已成熟且能量不足時回傳 `KC_TREND_END_WAIT`，不送新倉；待下一輪 CK 方向與動能重新明朗後再評估。
- API 圖表診斷即使已有持倉也會回傳阻擋結果，顯示 `KC_POSITION_HELD`，不再以 `null` 掩蓋實際原因。
- 一般策略出口維持淨利保護；CK 衰退末端出口可先平倉並等待新 CK，硬止損、瀑布及雙異常安全出口不取消。末端守門測試及 CK／0.5U 出口專項共 `74 passed`。

## 2026-09-11 重新破軌票據修正

- 找到實際缺口：CK 末端平倉後的 `next_breakout` 票據在 `_try_profit_reentry_locked` 被直接返回，導致成功平倉後重新破軌永遠不送單。
- 修正為成功平倉、換到新 K、CK 方向與動能明朗、MA3／價格重新位於同側外軌外時重新評估；不再錯誤導向 live pivot 入口。
- 掃描訊號與最後送單快照都重新套用 `KC_TREND_END_WAIT`，避免舊快取在趨勢末端追入。
- 一般買入沿用淨利 0.5U 啟動、峰值回吐20%平倉；CK末端最後平倉、硬止損與瀑布安全出口保留。
- 重新破軌、末端守門、0.5U保護與送單流程共 `77 passed`。

## 2026-09-11 CK未明先平倉

- 有效已收線 CK 若無法確認多空，或方向已與持倉相反，持倉先平倉，原因分別為 `KC_CK_DIRECTION_UNCLEAR_EXIT`／`KC_CK_DIRECTION_REVERSED_EXIT`；空手入口仍拒絕 CK 未明，不再買入。
- 指標資料缺失、NaN 或通道結構無效時不猜測、不強制平倉；硬止損與瀑布／雙異常出口優先。
- CK 未明平倉後建立正常重開票據，只有換 K 且 CK 重新明朗、MA3 與價格符合外軌入口才可再評估；一般平倉仍使用淨利保護回吐20%，CK 末端最後平倉為例外。
- 受影響出口與方向專項 `74 passed`，新增 CK 未明／反向／無效資料邊界 `4 passed`。

## 2026-09-11 龍蝦 MA3 穿軌入口修正

- 龍蝦日誌顯示實際阻擋依序包含 `KC_DIRECTION_WAIT`、`KC_MOMENTUM_FADE_WAIT`、`KC_MA3_OUTSIDE_WAIT` 及 `KC_TREND_END_WAIT`；不是單一資金或槽位原因。當時也曾成功開兩次 SHORT，並各以 20% 淨利回吐保護獲利平倉。
- 根因：`ma3_outer_cross_ready()` 已存在但 `aligned_entry()` 沒有呼叫，只檢查 MA3 已在外軌的延續；MA3 剛穿軌時因此回傳 `KC_MA3_OUTSIDE_WAIT`。
- 修正為先接受 MA3 新穿越 `KC_LIVE_OUTER_LONG/SHORT`，若沒有新穿越再接受 `KC_OUTSIDE_LONG/SHORT` 延續。CK 明朗、動能增強、即時 MA3 順向、反向異常、末端守門與最終快照重驗全部保留。
- 「後面又變掉」是掃描訊號與最後送單之間會重新抓 K 線及 ticker；若 CK 動能、MA3 方向、外軌位置、反向異常、末端狀態或每根限次在這段時間改變，送單前會正確拒絕，避免用舊快照追價。
- 龍蝦 MA3 外軌入口專項 `44 passed`；剩餘4項為既有停用獲利空間／CK直接反手舊測試，不是本次穿軌回歸。

## 2026-09-11 多綠K／空紅K進場守門

- 多單只接受當根即時報價高於當根開盤的綠K；空單只接受當根即時報價低於當根開盤的紅K。十字、反色或資料無效不開倉。
- 守門放在 `aligned_entry()` 共用入口，正常新倉、MA3穿軌、外軌延續、平倉後重開、快取及最後送單重驗一致套用。
- CK方向、動能增強、MA3順向、外軌穿越／延續、反向異常、末端守門與每根限次仍保留。
- MA3外軌、重開與保護相關專項 `117 passed`；4項既有失敗為停用獲利空間／CK直接反手舊測試。

## 2026-09-11 修正獲利重開繞過K色

- 龍蝦最近兩次重開原因為 `Channel Swing PROFIT_REENTRY KC_LIVE_OUTER_SHORT`，走 `live_pivot` 重開路徑；原先一般 `aligned_entry()` 的多綠／空紅檢查未涵蓋此路徑。
- 在 Channel Swing 最後快照及最後 ticker 重驗加入共用 K 色守門，因此獲利重開、live pivot、快取與一般送單都必須多單綠K／空單紅K。
- 若送單等待期間 ticker 翻色，直接取消該次送單，下一個有效報價重新評估；不使用舊快照強行成交。
- 相關新倉／重開／保護回歸 `120 passed`；4項既有失敗仍為停用獲利空間／CK直接反手舊測試。

## 2026-09-11 量能恢復後重新評估

- 修正末端量能守門：最近已收線量能恢復至至少 `1.5x` 均量時，立即解除 `KC_TREND_END_WAIT` 的量能衰弱阻擋；不再額外要求趨勢品質乘積達 `2.0`。
- 解除只代表重新評估，不保證成交；CK方向、CK動能增強、MA3順向、外軌、K色、反向異常、每根限次及帳戶風控仍須全部通過。
- 多空對稱，量能再次衰弱時下一輪可再次暫停；不使用未收線量能預測。
- 量能恢復、空單入口及既有出口專項 `121 passed`；4項既有失敗為舊獲利空間／CK直接反手測試。

## 2026-09-11 移除兩根同色K舊入口

- 找到舊規則殘留：`_fresh_channel_entry_snapshot()` 在 `confirmed_reverse` 路徑仍呼叫 `confirmed_outer_breakout_ready()`，導致反向／重開有時要求兩根已收線同色 K。
- 已移除該交易路徑依賴；所有 Channel Swing 新腿、重開、快取與送單重驗統一使用 `aligned_entry_ready()` 的 MA3 穿越／外軌延續入口。
- MA3 穿軌即評估，不再要求兩根同色 K；CK方向、動能、MA3順向、K色、反向異常、末端與帳戶風控仍保留。
- MA3入口、重開、末端與保護專項 `123 passed`；4項既有失敗為舊獲利空間／CK直接反手測試。

## 2026-09-11 末端首根進場後停止追單

- 多空對稱：成熟末端若當根仍為持倉方向 K，允許第一個有效 MA3 外軌入口；多單為綠 K，空單為紅 K。
- 之後若最近兩根已收線 K 都已反持倉方向，回傳 `KC_TREND_END_WAIT`，不再同方向開倉；必須等量能恢復或 CK／動能重新明朗後再評估。
- 規則只使用已收線 K 與當根已形成的開／收方向，不保存易遺失的記憶體鎖；重啟後不會自行退回舊的兩根同色入口。
- 末端首根、兩根反色鎖定、MA3入口與既有出口專項 `125 passed`；4項既有失敗為舊獲利空間／CK直接反手測試。

## 2026-09-11 即時MA3第一轉角立即平倉

- 找到延遲原因：舊出口要同時等待 CK 衰退、通道狹窄、進場後峰谷回退及 `0.10 ATR` 反向幅度，第一個 MA3 轉角不會立即平倉。
- 新增 `MA3_IMMEDIATE_TURN_EXIT`：持倉後先觀察持倉方向 MA3 推進，下一個即時報價造成 MA3 斜率首次反向就市價平倉，不再等待 CK 衰退、通道狹窄或 ATR 幅度。
- 多空對稱；獲利保護已啟動也不延遲此出口。硬止損、瀑布／雙異常優先，平倉失敗沿用原待平重試與持久化票據。
- 即時MA3第一轉角多空核心測試 `2 passed`；舊 MA3 延遲出口測試中的矛盾斷言需依本輪授權更新，不作為新規則失敗。
