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

### [2026-10-20 23:25 UTC+8] - Modification Phase: 全面打通訊號確認映射、影線延遲與兩段式破軌 Gate
- **Author**: Codex (workspace identity: shudgai999)
- **Target Files & Lines**: `core/services/strategies/wild_swing_strategy.py`; `core/gates/macro_candle_structure_gate.py`; `core/services/entry_firewall.py`; `core/services/key_level_dip_sniper.py`; `core/services/two_stage_breakout_service.py`; `core/services/symbol_runner.py`; `core/engine.py`; `core/testnet_account.py`
- **Modification Description**:
  1. `WildSwingStrategy`: 完整復原影線延遲等待 (`pending_wick_bar_ids`) 與次根 follow-through 驗證 (`_wick_follow_through_candidate`)，修復長影線未能延遲下一根驗證的問題。
  2. `MacroCandleStructureGate`: 補齊兩段式破軌確認模式 (`BREAK_STRONG`, `BREAK_CONFIRM`, `BREAK_CONFIRM_OPP`, `BREAK_STRONG_ADD`, `BREAK_SINGLE`) 與 `EMA_CROSS` 映射至 BREAKOUT/CROSSOVER；允許 `DIP_SNIPER` 與 `CONTINUATION` 互通通過結構審查；修正純數學通過原因標準字串。
  3. `entry_firewall.py`: 僅對非 WildSwing 原生模式進行 fallback 封裝，防止過期破軌訊號被誤合成通過；補齊所有兩段式破軌模式常數映射。
  4. `key_level_dip_sniper.py`: 避免將 `CONTINUATION` 強制覆蓋為 `DIP_SNIPER`，保持中繼延續原語模式一致。
  5. `two_stage_breakout_service.py` & `symbol_runner.py`: 確保破軌兩段式候選標記標準 `entry_pattern="BREAKOUT"`, `structure_confirmed=True`, `channel_expanding=True`，不再被 Macro Gate 阻斷。
  6. `core/testnet_account.py`: 修正 `enforce_hard_stop` 函式內部延遲 import 導致的 `UnboundLocalError`。
- **Verification & Test Status**:
  - `tests/test_wild_swing_entry_firewall.py`: 18 passed (100%)
  - `tests/test_macro_candle_structure_gate.py`: 63 passed (100%)
  - `tests/test_scalping_3m_rules.py` & `tests/test_two_stage_breakout.py`: 14 passed (100%)
  - `tests/test_math_execution_gate.py`: 8 passed (100%)
  - 所有修改 Python 檔案 AST 編譯無語法錯誤。

### [2026-10-20 22:59 UTC] - Modification Phase: 防追高追低，開倉位置精確化
- **Author**: Antigravity (workspace identity: shudgai999)
- **Target Files & Lines**: `core/gates/breakout_continuation_entry_gate.py`（`late_chase_block_reason`、`evaluate_pure_math` 包裝）；`tests/test_late_chase_entry_guard.py`；`AGENTS.md`
- **Modification Description**: 8006 今日 25 筆交易中 CONTINUATION_OUTSIDE_KC 12 筆合計約 -33U、CONFIRMED_2BAR 4 筆全虧，原因是軌外任意根數皆可延續追單。新增：延續開倉僅限破軌後前 2 根軌外收線；延續與兩根確認破軌距外軌 ≤0.5 ATR、距 MA5 ≤1 ATR（取收盤與最新報價較極端者），資料無效 Fail-Closed，日誌 `ENTRY_LATE_CHASE_BLOCK`。
- **Trigger Reason & Requirement**: 使用者回報開倉太晚、漲/跌一大段才進場隨即平倉，選擇「新鮮度＋乖離限制」方案。
- **Verification & Test Status**: 新增 7 個單元測試全過。相關測試以 guard 開/關對照：既有工作樹（含他人未提交修改）原已 41 失敗；啟用 guard 後新增約 14 個失敗，皆為舊夾具價格遠在外軌外（0.8～5.6 ATR）、依新規應被拒絕的案例，尚未改寫。服務未重啟。

### [2026-10-20 23:15 UTC] - Modification Phase: 破軌與 MA 金叉精確開倉修復及全域防追高
- **Author**: Antigravity (workspace identity: shudgai999)
- **Target Files & Lines**: 
  - `core/gates/breakout_continuation_entry_gate.py`: 啟用 `DISABLE_INTRABAR_BREAKOUT = False`；防追高追低限制擴大覆蓋至全破軌模式（`INSTANT_BREAKOUT`、`TwoStageBreakout`、`CONTINUATION`）；`_reference_atr` 支援少於 5 根時之 candle range 備援。
  - `core/gates/ma5_ma15_cross_entry_gate.py`: 修正 MA 金叉年齡 `cross_age in (0, 1, 2)`（允許金叉當根 0 歲進場，原僅允許 1、2 歲）；放寬 KC 中軌走平或向上即允許開多。
  - `core/services/ema_cross_entry_service.py`: 新鮮金叉（`is_fresh_cross`）豁免 `EMA_SPREAD_TOO_SMALL`，避免兩線剛交錯時因間隙極小被誤殺。
  - `core/services/symbol_runner.py`: 在 `_filter_authorities_by_kc_direction` 加入 `BREAK_*` 與 `EMA_CROSS*` 白名單，防止破軌與交叉權威被 KC 中軌斜率誤過濾。
  - `tests/test_breakout_continuation_entry_gate.py`, `tests/test_scalping_3m_rules.py`, `tests/test_late_chase_entry_guard.py`: 測試夾具對齊 0.5 ATR 外軌上限與 1.0 ATR MA5 上限，全部通過。
- **Trigger Reason & Requirement**: 使用者指出「破軌時沒開倉，也沒在 ma 線黃金交叉時開倉」，並指示執行重啟、舊測試修復及全破軌模式防追高。
- **Verification & Test Status**: `test_late_chase_entry_guard.py` (7/7 passed)、`test_scalping_3m_rules.py` (7/7 passed)、`test_two_stage_breakout.py` (7/7 passed)、`test_math_execution_gate.py` (8/8 passed)；`systemctl --user restart binance-8006.service` 成功重啟，PID 617934 正常運行。
