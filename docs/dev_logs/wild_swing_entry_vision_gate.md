### [2026-10-19 10:29:20 UTC+8] - Modification Phase: WILD_SWING 四模式入口與最終視覺 Gate
- **Author**: Codex (workspace identity: shudgai999)
- **Target Files & Lines**: `core/gates/breakout_continuation_entry_gate.py`; `core/gates/trend_riding_strategy.py`; `core/services/strategies/wild_swing_strategy.py`; `core/services/entry_firewall.py`; `core/services/symbol_runner.py`; `core/vision/chart_guard.py`; `.env.example`
- **Modification Description**: 加入即時蓄勢大棒破軌、雙棒收線破軌、軌外延續及 MA5/MA15 交叉四種 WILD_SWING 入口；instant breakout 不檢查 MA5 斜率，但仍服從 KC 趨勢、MA15 方向與其他共用 Gate。修正盤中訊號在送單 Gate 前被改用純收線資料而失效，改由完整最新 frame 與報價重新驗證。帳戶最終 Gate 增加上一根 ATR 的 0.5 倍實體、3 ATR 離軌上限及反側外軌開盤阻斷；Macro Candle Structure Gate 與 Gemini 視覺審查均在最終授權前執行，視覺不可用時維持 fail-closed。Gemini 提示詞採四模式專屬條件，並解析 `allow_trade`、`reason`、`confidence` JSON 欄位。
- **Trigger Reason & Requirement**: 使用者提供四模式 CCXT 範例及 WILD_SWING Gate 規格，要求即時破軌不受 MA5 斜率限制、延續入口有效、所有模式經共用 Gate。
- **Verification & Test Status**: `git diff --check` 通過；6 個修改中的 Python 檔 AST parse 通過。未執行測試套件。

### [2026-10-20 14:41:22 UTC+8] - Modification Phase: 修正 3 分鐘 EMA 交叉輪詢與入場候選漏判
- **Author**: Codex (workspace identity: shudgai999)
- **Target Files & Lines**: `core/services/ema_cross_entry_service.py`; `core/services/symbol_runner.py`; `core/gates/trend_riding_strategy.py`
- **Modification Description**: EMA5/EMA13 交叉改為只根據已收線 3 分鐘 K 棒評估，延遲補開按新收線棒計數；EMA 與 KC 兩棒破軌候選分開評估；趨勢 Gate 從行情 frame 讀取實際週期，以 3 分鐘計算同棒與冷卻判斷；新增每根收線一次的 `ENTRY_SCAN_RESULT`，輸出 EMA/破軌候選與時間週期。
- **Trigger Reason & Requirement**: 使用者回報 Binance 機器人持續運行但未開倉，要求追查與修正 3 分鐘 EMA5/EMA13 及 KC 第二棒入口。
- **Verification & Test Status**: `git diff --check` 及三個修改檔 `py_compile` 通過，未執行測試。已確認 user systemd unit 指向 `/home/shudgai999/project/new bn`，以 paper mode 重啟服務；PID 更新為 3619158，API 回應正常。重啟後 ENTRY_SCAN_RESULT 顯示 CAP 與龍蝦 EMA=NO_CROSS、breakout=NO_CLOSED_2BAR_SIGNAL；fallback MID_LINE_BREAKDOWN 有提交紀錄，但當前 API positions 為空。未產生真實交易。

### [2026-10-20 14:47:00 UTC] - Modification Phase: 修正送單最終驗證週期錯位
- **Author**: Codex (workspace identity: shudgai999)
- **Target Files & Lines**: `core/services/entry_finality.py`; `core/engine.py`; `core/paper_account.py`; `core/testnet_account.py`; `core/services/math_execution_gate.py`; `core/services/entry_firewall.py`
- **Modification Description**: 找到送單 Gate 把 3 分鐘候選拿去和 1 分鐘盤中棒比對，導致 `WILD_SWING_LIVE_CANDLE_IDENTITY_CHANGED`；最終收線快照改依候選 frame 的 1m/3m 週期重抓並驗證時間戳。數學 Gate 與帳戶 Firewall 共用同一份最新且已驗證快照，避免兩次重複 REST 收線確認；快照加入 18 秒上限與明確開始/結果/逾時日誌。宏觀 Gate 的標籤比對改以重新驗證的結構確認訊號決定入口類型，解決 legacy pattern alias 與當前確認訊號衝突。
- **Trigger Reason & Requirement**: 實際服務日誌確認候選送單後因 3m/1m 時間戳身份不一致而拒單；修正後又查出 pattern alias 與結構確認名稱不一致，造成另一個錯誤 veto。
- **Verification & Test Status**: `git diff --check` 與受影響 Python 檔 `py_compile` 通過。8006 在 PAPER_TRADING=true、entry_submission_enabled=true 下重啟。重啟後收線快照驗證約 1.3 秒並確認 3m 時間週期；MA/主趨勢不符的訊號仍由最後數學 Gate 明確拒絕，未以放寬風控方式強行開倉。未執行測試套件，未產生真實交易。
- **補充驗證**：同週期修正後，舊 3m/1m 身分錯誤已消失。現行日誌捕捉到另一個有效阻斷：重新評估訊號為 `MA_CROSSOVER/CROSSOVER`，送單候選卻是 `DIP_SNIPER`，且確認棒時間差一根（signal bar 1792507500000、候選/確認 bar 1792507680000）；因此依既有 fail-closed 規則拒絕這筆不一致訊號。加入 `WILD_SWING_SIGNAL_STALE` 欄位級診斷，保留此阻斷以免將過期/不同策略訊號硬送成倉位。
