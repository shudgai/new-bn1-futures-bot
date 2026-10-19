### [2026-10-19 10:29:20 UTC+8] - Modification Phase: WILD_SWING 四模式入口與最終視覺 Gate
- **Author**: Codex (workspace identity: shudgai999)
- **Target Files & Lines**: `core/gates/breakout_continuation_entry_gate.py`; `core/gates/trend_riding_strategy.py`; `core/services/strategies/wild_swing_strategy.py`; `core/services/entry_firewall.py`; `core/services/symbol_runner.py`; `core/vision/chart_guard.py`; `.env.example`
- **Modification Description**: 加入即時蓄勢大棒破軌、雙棒收線破軌、軌外延續及 MA5/MA15 交叉四種 WILD_SWING 入口；instant breakout 不檢查 MA5 斜率，但仍服從 KC 趨勢與其他共用 Gate。修正盤中訊號在送單 Gate 前被改用純收線資料而失效，改由完整最新 frame 與報價重新驗證。帳戶最終 Gate 增加上一根 ATR 的 0.5 倍實體、3 ATR 離軌上限及反側外軌開盤阻斷；Macro Candle Structure Gate 與 Gemini 視覺審查均在最終授權前執行，視覺不可用時維持 fail-closed。
- **Trigger Reason & Requirement**: 使用者提供四模式 CCXT 範例及 WILD_SWING Gate 規格，要求即時破軌不受 MA5 斜率限制、延續入口有效、所有模式經共用 Gate。
- **Verification & Test Status**: `git diff --check` 通過；6 個修改中的 Python 檔 AST parse 通過。未執行測試套件。
