# 🏛️ MASTER BLUEPRINT: Futures Bot Trading Engine Architecture

> **Source of Truth Document**  
> 本文件紀錄本幣安期貨自動化交易系統 (Futures Bot) 之全系統模組化架構、OOP 介面層、數據流、核心組件分工與安全守門機制。

---

## 1. 🏗️ 全系統頂層架構 (System Architecture Overview)

```mermaid
graph TD
    API["Binance Futures WebSocket / REST API"] --> Engine["TradingEngine (core/engine.py Orchestrator)"]
    
    subgraph Interfaces Layer (core/interfaces/)
        IEntry["IEntryStrategy (entry_interface.py)"]
        IExit["IExitStrategy (exit_interface.py)"]
        IGuard["IGuardRule (guard_interface.py)"]
    end

    subgraph Strategies & Exits Layer (core/services/)
        OuterStrat["OuterChannelEntryStrategy (services/strategies/outer_strategy.py)"] --> IEntry
        PivotStrat["PivotChannelEntryStrategy (services/strategies/pivot_strategy.py)"] --> IEntry
        DirectReverseStrat["DirectReverseStrategy (services/strategies/direct_reverse_strategy.py)"] --> IEntry
        ProfitProtect["ProfitProtectionExitStrategy (services/exits/profit_protection_service.py)"] --> IExit
        FadingExit["FadingExitStrategy (services/exits/fading_exit_service.py)"] --> IExit
        HardStop["HardStopExitStrategy (services/exits/hard_stop_service.py)"] --> IExit
    end

    subgraph Domain Services Layer (core/services/)
        Engine --> ScanSvc["Scan Service (scan_service.py)"]
        Engine --> RankSvc["Rank Service (rank_service.py)"]
        Engine --> EntrySvc["Entry Service (entry_service.py)"]
        Engine --> SwingSvc["Swing Service (swing_service.py)"]
        Engine --> PivotSvc["Pivot Service (pivot_service.py)"]
        Engine --> PullbackSvc["Pullback Service (pullback_service.py)"]
        Engine --> PulseSvc["Pulse Service (pulse_service.py)"]
        Engine --> LoggerSvc["Logger Service (logger_service.py)"]
        Engine --> SurvSvc["Surveillance Service (surveillance_service.py)"]
        Engine --> SymbolRunner["Symbol Runner (symbol_runner.py)"]
        Engine --> EntryRoomSvc["Entry Room Service (entry_room_service.py)"]
    end
    
    subgraph Risk & Guards Layer (core/guards/)
        Engine --> RiskGuard["Risk Guard (risk_guard.py)"] --> IGuard
        Engine --> AbnormalGuard["Abnormal Guard (abnormal_guard.py)"] --> IGuard
    end

    subgraph Account & Order Layer
        Engine --> Account["Account & Order Manager (core/paper_account.py / testnet_account.py)"]
    end
```

---

## 2. 📁 模組資料夾結構與職責分工 (Folder Structure & Responsibilities)

系統架構依據 SOLID 原則劃分為獨立的領域層級，核心引擎 `core/engine.py` 瘦身為專注於事件調度與生命週期管理的高階 Orchestrator，根目錄過期散亂的 `channel_*.py` 已全數清理乾淨：

```
core/
├── engine.py                  # 瘦身主引擎 (~3,000 行 Orchestrator)
├── config.py                  # 系統設定參數與環境變數
├── indicators.py              # K 線指標計算庫 (EMA, KC, ATR)
├── strategy.py                # 策略封裝與快照管理
├── paper_account.py           # 模擬盤帳戶管理
├── testnet_account.py         # 測試網帳戶管理
├── interfaces/                # 🌟 抽象介面層 (SOLID DIP/OCP Abstraction)
│   ├── entry_interface.py     # IEntryStrategy (進場策略介面)
│   ├── exit_interface.py      # IExitStrategy (出場與浮盈保護介面)
│   └── guard_interface.py     # IGuardRule (風控與異常守門介面)
├── services/                  # 領域服務層 (Business Logic Services)
│   ├── strategies/            # 🌟 進場策略實作 (OOP Strategy Classes)
│   │   ├── outer_strategy.py  # 穿軌與延續進場 (OuterChannelEntryStrategy)
│   │   ├── pivot_strategy.py  # 嚴格轉折突破 (PivotChannelEntryStrategy)
│   │   ├── direct_reverse_strategy.py # 反向直接授權驗證
│   │   └── live_pivot_strategy.py # 即時轉折監控 (LivePivot)
│   ├── exits/                 # 🌟 出場策略實作 (OOP Exit Classes)
│   │   ├── profit_protection_service.py # 浮盈保護 (ProfitProtectionExitStrategy)
│   │   ├── fading_exit_service.py # 動能衰退平倉 (FadingExitStrategy)
│   │   └── hard_stop_service.py   # 移動硬止損保護 (HardStopExitStrategy)
│   ├── entry_room_service.py  # 淨利空間與構造目標價計算
│   ├── scan_service.py        # 掃描與候選名單刷新
│   ├── rank_service.py        # 標的品質評分、利潤空間與能量計算
│   ├── entry_service.py       # 通道外軌突破、連續與趨勢進場動作
│   ├── swing_service.py       # 波段離場、動能衰退與震盪狀態維護
│   ├── pivot_service.py       # 嚴格轉折點、ATR 計算與追蹤止損
│   ├── pullback_service.py    # 回踩確認與落差分類
│   ├── pulse_service.py       # BTC 1m 脉衝監測與影子跟隨
│   ├── logger_service.py      # 進度日誌格式化與等待明細輸出
│   ├── surveillance_service.py # 全市場爆跌監控與價格安全檢查
│   └── symbol_runner.py       # 單幣種併發掃描與出場執行器
├── guards/                    # 風控與異常守門層 (Safety & Risk Guards)
│   ├── risk_guard.py          # 同向開倉數量限制與 K 棒重複開倉鎖定
│   └── abnormal_guard.py      # 瀑布拉砸離場與異常平倉憑證釋放
└── routes/                    # 策略路由層 (Strategy Routes)
    └── legacy_routes.py       # 舊版策略與限價單驗證路由
```

---

## 3. 🧪 測試驗證矩陣 (Test Matrix)

所有架構異動與模組抽離均須於 `refactor/engine-decoupling` 分支通過以下測試套件：
- `pytest tests/test_channel_ma3_cross_entry.py` (MA3 穿軌專項 - 30 個測試點)
- `pytest tests/test_channel_ma3_continuation.py` (MA3 延續專項 - 16 個測試點)
- **總計 46 個 MA3 核心測試點 100% 通過。根目錄 26 個相容檔已清理乾淨，全系統引用直接歸向領域層！**
