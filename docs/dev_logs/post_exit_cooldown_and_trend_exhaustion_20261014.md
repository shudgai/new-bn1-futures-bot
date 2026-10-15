# 開倉平倉後強制冷靜（冷卻期）、有利潤空間開倉與漲勢/跌勢末端一票否決規範（2026-10-14）

## 1. 核心需求與背景
依據使用者最新明確交易鐵律：
> **「開倉平倉後冷靜一下有利潤在開倉，若已在漲勢末端就不要再開倉」**

系統進行三大維度硬核升級：
1. **平倉後冷卻期（冷靜一下）**：
   - 將平倉後冷卻時間由原本過短的 20 秒調整為 **120 秒**（充分冷卻）。
   - 徹底**廢除 / 禁止冷卻期豁免（No Cooldown Bypass）**！冷卻期內任何模式（包含 MODE1 破軌、MODE3 鍘刀）一律 100% 阻斷開倉，絕不連環刷單追價。
2. **獲利空間過濾（有利潤才開倉）**：
   - 新增 `validate_profit_room`：
     - 做多：當前價格距離 KC 上軌剩餘空間至少 20% 通道寬度或 0.35 ATR；若貼軌或超出上軌 > 1.2 ATR 判定無利潤空間，一票否決！
     - 做空：當前價格距離 KC 下軌剩餘空間至少 20% 通道寬度或 0.35 ATR；若貼軌或跌破下軌 > 1.2 ATR 判定無利潤空間，一票否決！
3. **漲勢/跌勢末端一票否決（若已在漲勢末端就不要再開倉）**：
   - 新增 `validate_trend_exhaustion`：
     - 做多（漲勢末端 BULLISH_EXHAUSTION）：
       - 均線乖離過大（> 1.2 ATR，初始突破極限 1.8 ATR）或偏離 MA5 > 1.2%。
       - 頂部高檔滯漲長上影線（避雷針，upper_wick > 0.7*body 且 > 0.35*range）。
       - 連續推進後動能顯著枯竭（Diminishing Thrust）。
       - 高檔 MA5 斜率走平衰竭（ma5_slope <= 0.0001）。
       - 通道上軌停止向上擴張走平。
       - 命中任一項一律 100% 觸發 `VETO_LONG_BULLISH_EXHAUSTION` 阻斷。
     - 做空（跌勢末端 BEARISH_EXHAUSTION）：多空對稱阻斷。

## 2. 影響檔案
- `core/gates/meme_shared_protection_gate.py`：新增 `validate_profit_room` 與 `validate_trend_exhaustion`，並在 `evaluate_entry` 實施全域一票否決。
- `core/services/symbol_runner.py`：冷卻期調整為 120 秒，移除 `BYPASS_COOLDOWN`，並在下單前嚴格複驗獲利空間與趨勢末端。
- `core/gates/entry_gate.py`：在 `evaluate_entry_candidate` 整合末端與獲利空間審查。
- `core/services/entry_contract.py`：在 `evaluate_entry_contract` 中硬檢核末端與獲利空間。
- `tests/test_cooldown_and_trend_exhaustion.py`：新增專屬單元測試，100% 通過。
