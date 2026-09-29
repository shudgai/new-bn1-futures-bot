# MA15 波段防守與匯入修復驗證

本輪開始時，工作區已存在 `assess_market_regime` 相容介面與 MA15 結構防守草稿。沒有重現磁碟版本的 ImportError；以獨立 Python 程序匯入 entry_firewall、profit_protection_service 與 engine，並測試防火牆真實呼叫路徑，確認介面可用。

現行策略以 MA15 為防守線：已收線收盤跌破 MA15，或已收線 MA3 由 MA15 上方／相等轉到下方時平多；單獨 MA3 斜率轉負或價格跌破 MA3 不平倉。沿用工作區既有的多空對稱實作。未收線 MA15／MA3 不產生結構出場，既有帳戶硬止損仍生效。

保本以進場 ATR 固定尺度，實際觀察有利價格位移達 1.2 ATR 後，將止損提高至成交成本價；保留原有更有利止損。此成本價不含手續費及滑點，不能宣稱成交後淨損益必定非負。不從進場前影線重建浮盈峰值。

本輪補齊的缺口：

- `entry_atr_protection.py` 與 `exit_service.py` 原先丟棄最新價，現已傳入策略。
- 缺少 K 線或仍處於進場確認棒時，報價仍可啟動及觸發保本；結構判斷才等待有效已收線資料。
- 帳戶更新路徑保存及還原完整保本狀態，包含 stop_loss、峰值、啟動旗標與新政策待平原因。
- 新政策已觸發平倉保存待平狀態，失敗後重試；舊政策待平不直接授權新平倉。

驗證：

```text
.venv/bin/python -m pytest tests/test_swing_defense_policy.py tests/test_emergency_ma3_trend_hold.py tests/test_red_eye_entry_pressure.py tests/test_entry_e2e_20260928.py tests/test_order_sizing.py tests/test_startup_guard.py -q
103 passed
.venv/bin/python -m compileall .
成功
```

測試包含入口實際防火牆、獨立程序匯入、MA15 跌破、真正交叉、碰線、未收線隔離、同進場棒即時保本、無行情框架報價路徑、持久化還原、平倉失敗重試及無效價格。舊 MA3 趨勢保持測試已更新為新規則，沒有宣稱歷史全套測試通過。

已備份紙上帳戶並重啟 `binance-8006.service`。重啟後 active/running，MainPID=3646723，NRestarts=0；API 成功回應，is_running=true、paper_trading=true。初次重啟後觀察窗口 102 行 journal 中無 ImportError、Traceback、ERROR 或 DANGER。這是有限觀察窗口，並非未來運行保證；未手動下單、清倉或重設帳戶。

證據：completion_tests.log、completion_compileall.log、completion_service_status.txt、completion_status.json、completion_runtime.log、restart_time.txt。completion_before/ 為本輪接手時三個修正檔案備份；原 before/ 屬先前工作。工作區包含大量既有未提交變更，本輪未將其一併提交。

規範限制：已讀取根 AGENTS.md 並核對 whoami/git 身分；其引用的 AIDAN/ 檔案在本機不存在，未臆造或修改規範。


## 再次核驗

依使用者再次要求重新檢查，既有修復仍在，無需重複修改策略。相關 pytest 再次 103 passed，完整 compileall 結束碼 0。於 2026-09-28T23:23:18Z 再次備份帳戶並重啟；MainPID=3649435，active/running、NRestarts=0。API is_running=true、paper_trading=true；重啟後本次觀察窗口 55 行 journal 無 ImportError、Traceback、ERROR 或 DANGER。證據保存於本目錄 recheck_* 檔案。
