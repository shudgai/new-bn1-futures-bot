# 現行生效參數（.env 實際值，不含機密）

> 由 `tools/dump_active_params.py` 從 `core/config.py` 讀出的執行期實際值（2026-09-12 15:53）。調整 `.env` 後請重跑並一起 commit。

| 參數 | 值 | 說明 |
|---|---|---|
| `PAPER_TRADING` | True | 模擬帳戶（純本地） |
| `INITIAL_BALANCE` | 150.0 | 初始餘額 USDT |
| `LEVERAGE` | 5 | 槓桿 |
| `SYMBOL_ROTATION_ENABLED` | False | 幣種輪替 |
| `KELTNER_ATR_MULTIPLIER` | 1.0 | KC 軌道 = EMA20 ± ATR×此值 |
| `CHANNEL_LIVE_BREAKOUT_BODY_ATR` | 1.7 | 特例K門檻（即時長K） |
| `CHANNEL_LONG_BODY_ENTRY_ATR` | 1.7 | 特例K門檻（已收線長實體） |
| `CHANNEL_SPECIAL_K_REQUIRES_CONFIRMATION` | True | 特例K是否需兩根同色實體確認 |
| `CHANNEL_BREAKOUT_STOP_ENTRY_ENABLED` | True | 破軌預掛觸價單 |
| `CHANNEL_BREAKOUT_STOP_BODY_ATR` | 0.0 | 預掛觸發價用實體（0=直接用外軌價） |
| `CHANNEL_BREAKOUT_STOP_MAX_DISTANCE_ATR` | 1.0 | 預掛：現價距觸發價上限 |
| `CHANNEL_BREAKOUT_STOP_MAX_AGE_SEC` | 75.0 | 預掛：最長存活秒數 |
| `CHANNEL_MIN_ATR_PCT` | 0.5 | 最低 ATR% 門檻 |
| `CHANNEL_MIN_DIRECTION_EFFICIENCY` | 0.45 | 方向效率門檻 |
| `CHANNEL_ENTRY_MAX_BODY_ATR` | 0.8 | 當根實體過熱上限 |
| `CHANNEL_ENTRY_MAX_PREV_BODY_ATR` | 1.0 | 前一根大K不追上限 |
| `CHANNEL_TAIL_MAX_TREND_BARS` | 0 | 末端禁開（0=停用） |
| `CHANNEL_ATR_STOP_MULT` | 1.5 | ATR 停損倍數 |
| `CHANNEL_ATR_TARGET_MULT` | 3.0 | ATR 目標倍數 |
| `CHANNEL_WATERFALL_BODY_ATR` | 2.5 | 單根瀑布門檻 |
| `CHANNEL_SWING_PROFIT_LADDER_ARM_NET_USDT` | 4.0 | 階梯鎖利啟動（一般單） |
| `CHANNEL_SWING_PROFIT_LADDER_LOCK_OFFSET_USDT` | 2.0 | 階梯鎖利回吐（一般單） |
| `CHANNEL_SWING_PROFIT_LADDER_ARM_SPECIAL_K_USDT` | 2.0 | 階梯鎖利啟動（特例K） |
| `CHANNEL_SWING_PROFIT_LADDER_LOCK_OFFSET_SPECIAL_K_USDT` | 1.0 | 階梯鎖利回吐（特例K） |
| `CHANNEL_STOP_LOSS_COOLDOWN_SEC` | 120.0 | 停損後冷卻秒數 |
| `CHANNEL_PROFIT_REENTRY_COOLDOWN_SEC` | 120.0 | 獲利重開冷卻秒數 |
| `NET_PROFIT_GUARANTEE_BUFFER` | 0.0015 | 進場淨利空間門檻 |

## 交易幣種

- 龙虾/USDT、LAB/USDT（固定兩檔，幣種輪替停用）

## 入口（現行）

1. 一般入口：破軌根（同色、收在持倉側外軌外）＋同色實體確認根（實體 ≥ 全長 20%），中間可夾同色弱實體順延；另需方向效率 ≥ 0.45、ATR% ≥ 0.5%、無反向異常、每根限次與帳戶風控。
2. 破軌預掛觸價單：價格尚未破軌、現價距觸發價 ≤ 1.0 ATR、量能 ≥ 1.5× 近20根均量時，先在觸發價（＝外軌價位，`CHANNEL_BREAKOUT_STOP_BODY_ATR=0.0`）掛 STOP_MARKET；換根、CK 中軌轉向、價格退回 > 1.5 ATR、逾時 75 秒、停損冷卻或已有持倉就撤單。成交後若「開盤到成交價」實體 ≥ 1.7 ATR 才標記特例K。
3. 特例K（即時長K／已收線長實體，1.7 ATR）不再有特權：一律照一般入口的完整關卡。

## 出口（現行）

- ATR 停損 1.5／目標 3.0；階梯鎖利：一般單 4U 啟動回吐 2U、特例K 2U 啟動回吐 1U。
- CK 狹窄衰退＋MA3 峰谷反向 0.10 ATR、單根瀑布 2.5 ATR、雙已收線反向異常、帳戶硬止損（保證金 10%）。
