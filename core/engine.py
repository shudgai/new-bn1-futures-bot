import asyncio
import math
import re
import time
import ccxt.async_support as ccxt
import ccxt.pro as ccxtpro
import pandas as pd
import weakref
from typing import Dict, List
from core.config import (
    DEFAULT_SYMBOLS, MAX_SLOTS, MAX_SAME_SIDE_POSITIONS, TRADE_AMOUNT_USDT, get_effective_slot_count, TREND_FILTER_EMA_PERIOD,
    CONTINUOUS_SINGLE_SLOT_MARGIN_FRACTION,
    PULLBACK_TIMEOUT_MINUTES, ENTRY_LIMIT_TIMEOUT_SEC,
    PULLBACK_TARGET_MAX_DRIFT_ATR, PULLBACK_RECLAIM_MIN_ATR,
    PULLBACK_RETRY_COOLDOWN_SEC, get_pullback_target_depth,
    SYMBOL_ROTATION_INTERVAL_SEC, SYMBOL_ROTATION_ENABLED,
    UNHEALTHY_SYMBOL_CHECK_INTERVAL_SEC,
    BINANCE_API_KEY, BINANCE_SECRET, get_position_multiplier, MIN_TRADE_USDT,
    MIN_SCORE_THRESHOLD, USE_TESTNET,
    ADX_QUALITY_MIN, ADX_DECLINE_LOOKBACK_BARS_1H, TEST_BUDGET_CAP_USDT,
    HISTORY_RECENCY_DECAY, ENTRY_FRESHNESS_SCORE_MAX, MIN_FRESHNESS_SCORE,
    ENTRY_DISABLED_SYMBOLS, MIN_SL_DISTANCE_PCT, MIN_NET_REWARD_RISK, ENABLE_TREND_FOLLOW_EXIT, ENABLE_STRONG_TRIGGER_AUTO_CLOSE,
    STRUCTURED_NET_RR_FILTER_ENABLED, STRUCTURED_MIN_NET_REWARD_RISK, STRUCTURED_NET_RR_HARD_FLOOR,
    MA5_EXIT_MIN_HOLD_SEC, MA5_EXIT_MIN_ADVERSE_PCT, MA5_EXIT_MIN_ADVERSE_ATR_MULT, MA5_EXIT_TIMEFRAME,
    SL_ONLY_AFTER_PEAK_PCT,
    ENABLE_TRAILING_SL, TRAILING_SL_ATR_MULT, USE_NATIVE_TRAILING_STOP, DISABLE_STOP_LOSS,
    TAKER_FEE_RATE, SLIPPAGE_PCT, MAX_TRADE_RISK_USDT, PAPER_TRADING, SOFT_WARNING_PERSIST_SEC, ENABLE_SOFT_WARNING_TIGHTEN,
    CONTRARIAN_POSITION_SIZE_MULTIPLIER, MAINSTREAM_SYMBOLS, MA5_EARLY_CONFIRM_SCANS,
    MA5_REVERSAL_MIN_ATR_MULT, MA5_FAST_MIN_ATR_MULT, MA5_FAST_MAX_ATR_MULT,
    MA5_FAST_MIN_VOLUME_RATIO,
    RAPID_PIVOT_IMMEDIATE_REVERSE_ENABLED, RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR,
    CONTINUOUS_TREND_ONLY, CONTINUOUS_PIVOT_ONLY, DISABLE_CONTINUOUS_TREND_ENTRIES, PIVOT_LONG_ONLY, PIVOT_EARLY_ENTRY_MAX_REBOUND_ATR, PIVOT_MIN_KC_WIDTH_PCT, MA3_MARKET_ENTRY_MAX_DISTANCE_ATR,
    TREND_ENTRY_MIN_KC_MIDDLE_DISTANCE_ATR, CONTINUOUS_ENTRY_OUTER_ZONE_RATIO, CONTINUOUS_OUTER_RAIL_EXIT_ONLY,
    ABNORMAL_MARKET_GUARD_ENABLED, ABNORMAL_MARKET_MAX_CANDLE_RANGE_ATR,
    ABNORMAL_MARKET_MAX_CANDLE_RANGE_PCT, ABNORMAL_MARKET_ADVERSE_MOVE_PCT,
    CHANNEL_SWING_MIN_OUTER_DEPTH_RATIO, CHANNEL_SWING_MIN_REENTRY_RATIO,
    CHANNEL_SWING_TURN_LOOKBACK_BARS,
    BTC_1M_PULSE_FILTER_ENABLED, BTC_1M_PULSE_LOOKBACK_BARS,
    BTC_1M_PULSE_MIN_ATR,
    MA5_BOTTOM_MIN_HOLD_SEC,
    EXECUTION_PRICE_MAX_DEVIATION_PCT,
    STRUCTURED_ENTRY_ENABLED, STRUCTURED_SUPPORT_ORDER_TIMEOUT_SEC,
    BREAKOUT_HARD_STOP_ATR_MULT, BREAKOUT_CANDLE_STOP_BUFFER_ATR,
    BREAKOUT_TRAILING_ATR_MULT, BREAKOUT_RR1_TARGET, BREAKOUT_RR2_TARGET,
    BREAKOUT_RR_CLOSE_FRACTION, STRUCTURED_EXIT_INTERVAL_SEC, ENABLE_BREAKOUT_PARTIAL_TAKE_PROFIT,
    BREAKOUT_KC_FAIL_CONFIRM_BARS, STOP_LOSS_MULTIPLIER,
    BREAKOUT_PULLBACK_ATR_MULT, BREAKOUT_PULLBACK_TIMEOUT_SEC,
    CONTINUOUS_REENTRY_COOLDOWN_SEC, MA5_STOP_LOSS_COOLDOWN_SEC,
    EXHAUSTION_SNIPER_STOP_LOSS_PCT, EXHAUSTION_SNIPER_GRACE_SEC,
    KELTNER_MIN_VOLUME_RATIO,
)
from core.strategy import (
    SuperTrendKeltnerStrategy, build_sl_tp_for_side, compute_sl_tp_distance,
    compute_pullback_target, compute_net_reward_risk,
    has_volume_divergence, check_exhaustion_entry_filters,
)


def cap_margin_to_trade_risk(
    amount_usdt: float, leverage: int, entry_price: float, sl_price: float,
) -> tuple[float, float]:
    """依 SL、雙邊 taker fee 與單邊滑價縮小保證金，回傳(金額, 預估虧損)。"""
    amount = max(0.0, float(amount_usdt))
    lev = max(1, int(leverage or 1))
    entry = float(entry_price or 0.0)
    if amount <= 0 or entry <= 0:
        return amount, 0.0
    stop_pct = abs(entry - float(sl_price or entry)) / entry
    loss_pct_on_notional = stop_pct + 2 * max(TAKER_FEE_RATE, 0.0) + max(SLIPPAGE_PCT, 0.0)
    if loss_pct_on_notional <= 0:
        return amount, 0.0
    projected_loss = amount * lev * loss_pct_on_notional
    if MAX_TRADE_RISK_USDT > 0 and projected_loss > MAX_TRADE_RISK_USDT:
        amount *= MAX_TRADE_RISK_USDT / projected_loss
        projected_loss = MAX_TRADE_RISK_USDT
    return amount, projected_loss
from core.testnet_account import BinanceTestnetAccount
from core.paper_account import PaperAccount
from core.symbol_rotation import SymbolRotation
from core.indicators import drop_unclosed_candle, compute_position_trigger

class TradingEngine:
    def __init__(self):
        # 真實市場公開行情永遠連線（訊號偵測用，讀取公開資料不受
        # PAPER_TRADING 影響）；執行帳戶依 PAPER_TRADING 決定是否真的
        # 連上 Binance Testnet 下單，還是完全本地模擬（不受測試網伺服器
        # 穩不穩定影響）。
        self.exchange = ccxt.binanceusdm({"enableRateLimit": True})
        self.ws_exchange = ccxtpro.binanceusdm({"enableRateLimit": False})
        self.execution_exchange = ccxt.binanceusdm({
            "apiKey": BINANCE_API_KEY,
            "secret": BINANCE_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        self.execution_exchange.set_sandbox_mode(USE_TESTNET)
        # Ensure exchanges are closed if TradingEngine is garbage-collected
        def _close_exchanges(e1, e2, e3=None):
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                try:
                    # schedule coroutine on the running loop without creating
                    # the coroutine object here (avoids un-awaited coroutine)
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(e1.close()))
                    loop.call_soon_threadsafe(lambda: asyncio.create_task(e2.close()))
                    if e3:
                        loop.call_soon_threadsafe(lambda: asyncio.create_task(e3.close()))
                except Exception:
                    pass
            else:
                try:
                    asyncio.run(e1.close())
                except Exception:
                    pass
                try:
                    asyncio.run(e2.close())
                except Exception:
                    pass
                try:
                    asyncio.run(e3.close())
                except Exception:
                    pass

        weakref.finalize(self, _close_exchanges, self.exchange, self.execution_exchange, self.ws_exchange)
        self.strategy = SuperTrendKeltnerStrategy()
        self.account = PaperAccount() if PAPER_TRADING else BinanceTestnetAccount(self.execution_exchange)
        self.symbol_rotation = SymbolRotation(self.account)
        self.is_running = False
        self.task: asyncio.Task = None
        self.rotation_task: asyncio.Task = None
        self.analysis_task: asyncio.Task = None
        self.trend_cache_task: asyncio.Task = None
        self.analysis_event = asyncio.Event()
        self.rotation_event = asyncio.Event()
        self.account.on_trade_closed = self.request_trade_analysis
        self.tickers: Dict[str, float] = {}
        self.ticker_volumes: Dict[str, float] = {}  # 24小時成交量 (USDT)
        # 非紙上模式可下單合約集合；None代表紙上模式不需執行市場交集。
        self.execution_symbols = None
        self.last_ticker_success_ts: float = time.time()
        self._last_stale_ticker_log: float = 0.0
        self.ema_50_1h_cache: Dict[str, float] = {}
        # 大週期（1h）本身動能是不是也在衰退，用同一批 update_1h_trend_
        # cache() 已經抓到的1h K線算 ADX，判斷「不只是5分K的小趨勢要提防，
        # 連大方向本身都已經在做頭/做底」——5分K的新鮮度/ADX檢查看不到
        # 這一層。
        self.adx_1h_declining_cache: Dict[str, bool] = {}
        # 個幣 1h SuperTrend 方向快取（1=多頭 / -1=空頭）
        self.st_direction_1h_cache: Dict[str, int] = {}
        # BTC 大盤方向：1h SuperTrend 方向 + 翻轉後已過幾根 1h K棒
        self.btc_1h_st_direction: int = 0      # 0=未知, 1=多頭, -1=空頭
        self.btc_1h_st_flip_age: int = 999     # 翻轉後已過幾根 1h K棒（999=尚未初始化）
        self.last_1h_cache_time: float = 0.0
        # 兩階段回踩：突破先進候選池，觸價後等待 1m 收盤反轉確認，才送
        # 短效 Post-Only 限價單。候選與交易所掛單分開追蹤。
        self.pending_pullback_candidates: Dict[str, dict] = {}
        self._pullback_retry_after: Dict[str, float] = {}
        # 候選逾時後鎖住同方向舊 KC 突破，直到價格先回到通道內重置。
        self._expired_pullback_sides: Dict[str, str] = {}
        # 盤中投影MA5必須連續多輪成立；任何一輪失效即清零。回撤底部
        # Maker預掛不是盤中投影，不需等待轉彎確認。
        self._ma5_early_confirmations: Dict[tuple, dict] = {}
        # 峰谷前提前平倉狀態；保存方向與離場 MA5，供假轉彎續開判斷。
        self._continuous_alignment_wait: Dict[str, dict] = {}
        # 同一根 K、同一方向只允許成交一次，避免止盈後重複吃同一訊號。
        self._continuous_last_entry_bar: Dict[str, tuple] = {}
        # Channel Swing 每根已收盤 K 最多只能觸發一次反手，避免新倉
        # 在下一輪掃描重複使用同一根確認 K 再次反向成交。
        self._channel_swing_last_reverse_bar: Dict[str, object] = {}
        # Channel Swing 平倉後，同一根 live K 不得再次用任何入口重開。
        # 下一根已收盤 K 出現後自動解鎖；即時同 K 反手規格不受影響。
        self._channel_swing_last_exit_bar: Dict[str, object] = {}
        # 盤整鎖：均線與 KC 中軌反覆交叉時，外軌 V 只可作為持倉離場
        # 確認，不得開新倉或平倉後立即反手。需三根已收盤 K 明確同向才解鎖。
        self._channel_chop_locked: Dict[str, bool] = {}
        # K 線圖使用的 CHOP_WAIT 狀態切換紀錄；只保存近期事件。
        self._channel_chop_events: Dict[str, list[dict]] = {}
        # K 線圖與日誌使用的 Channel Swing 等待／取消／阻擋狀態。
        self._channel_signal_events: Dict[str, list[dict]] = {}
        # BTC 領先影子監控：只記錄 BTC 脈衝後各幣同向首個 KC 外軌事件，
        # 不參與下單，用來驗證是否真的存在可利用的秒級領先。
        self._btc_lead_shadow_active: dict = {}
        self._btc_lead_shadow_events: list[dict] = []
        # KC 外軌趨勢追單採多空對稱：先確認既有趨勢品質，再由下一根
        # 突破候選極值；過熱時等待回踩／回抽外軌後重新突破。
        self._channel_outer_trend_wait: Dict[str, dict] = {}
        # ADX + MA3/MA15 距離的雙門檻狀態；預設 RANGE，需連續 3 根確認才進 TREND。
        self._continuous_wave_regime: Dict[str, str] = {}
        # 在短週期 TREND 之上，以個幣自己的 1h 趨勢確認牛／熊市；
        # RANGE 保留給猴市的峰谷交易，BULL/BEAR 則只做個幣同向順勢單。
        self._continuous_market_mode: Dict[str, str] = {}
        # OUTER_RUN 多單：第一根紅 K 收回上軌內先平多；第二根
        # 紅 K 收盤後才確認反手，於第三根 K 開始市價開空。
        self._kc_reversal_wait: Dict[str, dict] = {}
        # Live-pivot reversals may be evaluated every 3 seconds; allow at most
        # one reversal in the same 1m candle and require two scans for MA3.
        self._live_pivot_reversal_bar: Dict[str, int] = {}
        self._fast_pivot_confirmations: Dict[str, dict] = {}
        self.pivot_prealerts: Dict[str, dict] = {}
        self._pivot_pullback_wait: Dict[str, dict] = {}
        self.last_signal_progress_log_at: float = 0.0
        # KC 通道撕裂停損後的冷卻記錄（symbol -> 停損 timestamp）
        self._kc_rip_after: Dict[str, float] = {}
        # 持倉手動平倉參考指標（跌破/站上關鍵均線、跌破前低/站上前高）：
        # 純粹給使用者按「平倉」前參考用，不是自動出場條件，不影響
        # 止損/止利/24h時間過濾等既有的自動平倉邏輯。
        self.position_triggers: Dict[str, dict] = {}
        # 持倉持續處於「✗」（ma_ok=false）但還沒升級成「⛔」（strong）的
        # 起始時間；持續超過 SOFT_WARNING_PERSIST_SEC 會收緊一次止損（見
        # _position_trigger_loop）。ma_ok恢復True時清空，讓下次重新計時。
        self._soft_warning_since: Dict[str, float] = {}
        self.trigger_task: asyncio.Task = None
        self.fixed_stop_task: asyncio.Task = None
        self.trend_follow_task: asyncio.Task = None
        self.trailing_sl_task: asyncio.Task = None
        self.ticker_task: asyncio.Task = None
        # 歷史係數降分 log 節流：同一個 symbol 在績效數據沒變的情況下，
        # 每輪主迴圈都會重算出同樣的係數/分數，導致同一則訊息每 5~10 秒
        # 就重複印一次（實測 ZEC/USDT 這樣連續洗了好幾分鐘）。只記錄狀態
        # 有變化時才印，同樣的狀態只顯示一次。
        self._history_coeff_logged: Dict[str, tuple] = {}
        # 診斷與影子比較每分鐘落盤一次，避免每 5 秒主迴圈造成過度寫檔。
        self._last_diagnostic_stats_save_at: float = 0.0
        self._last_empty_pivot_rescan_at: float = 0.0
        # KC失敗連續計數器：記錄每個持倉已連續將實體收在EMA20不利側的已收盤K棒數
        # 需達到 BREAKOUT_KC_FAIL_CONFIRM_BARS 根才實際關倉，防止單根回踩誤觸
        self._kc_fail_count: Dict[str, int] = {}


    def _ma5_timing_ready(self, symbol: str, signal: dict, now: float) -> tuple:
        """已收盤轉彎直接放行；盤中投影須連續多輪成立，失效即清零。"""
        required = max(2, MA5_EARLY_CONFIRM_SCANS)
        side = signal.get("side")
        key = (symbol, side)
        for stale_key in [item for item in self._ma5_early_confirmations if item[0] == symbol and item != key]:
            self._ma5_early_confirmations.pop(stale_key, None)
        if not signal.get("detected"):
            self._ma5_early_confirmations.pop(key, None)
            return False, 0, required
        if not signal.get("early_projection"):
            self._ma5_early_confirmations.pop(key, None)
            return True, required, required
        state = self._ma5_early_confirmations.get(key, {})
        count = int(state.get("count", 0)) + 1
        self._ma5_early_confirmations[key] = {"count": count, "last_seen": now}
        if count >= required:
            self._ma5_early_confirmations.pop(key, None)
            return True, count, required
        return False, count, required

    @staticmethod
    def _format_signal_progress(
        symbol: str,
        signal: dict,
        current_direction: str,
    ) -> str:
        """將策略結果壓縮成適合系統日誌的一行進度。"""
        eligible = signal.get("eligible")
        score = signal.get("score")
        if score is None:
            # 嘗試從 reason 字串中解析分數（格式：Score(85)）
            match = re.search(r"Score\((\d+)\)", signal.get("reason", ""))
            if match:
                score = int(match.group(1))
            else:
                # 嘗試 btc_adjusted_score / raw_score 作為備用
                score = signal.get("btc_adjusted_score") or signal.get("raw_score")
        direction_text = {"LONG": "多單", "SHORT": "空單"}.get(
            current_direction, "雙向"
        )
        action = signal.get("action", "HOLD")
        reason = signal.get("reason", "")
        score_components = signal.get("score_components") or {}
        component_text = ""
        if score_components:
            component_text = (
                f"KC{score_components.get('kc', 0)}/量{score_components.get('volume', 0)}/"
                f"RSI{score_components.get('rsi', 0)}/新鮮{score_components.get('freshness', 0)}/"
                f"品質{score_components.get('quality', 0)}"
            )
        diagnostics = signal.get("diagnostics") or {}
        raw_score = signal.get("raw_score")
        btc_score = signal.get("btc_adjusted_score", score)
        history_score = signal.get("history_adjusted_score")
        if eligible is False:
            score_text = "資格未通過"
        elif history_score is not None and history_score != btc_score:
            score_text = f"原{raw_score}→BTC{btc_score}→歷史{history_score}分"
        elif raw_score is not None and btc_score != raw_score:
            score_text = f"原{raw_score}→BTC{btc_score}分"
        else:
            score_text = f"{int(score or 0)}分"
        if signal.get("history_blocked"):
            stage = (
                f"歷史績效降分後取消（{component_text}）"
                if component_text else "歷史績效降分後取消"
            )
        elif action in ("BUY", "SELL"):
            stage = "符合立即開倉"
        elif action == "WAIT_PULLBACK":
            stage = signal.get(
                "confirmation_reason", "等待回調至KC區後二次確認"
            )
        elif "BTC_1h_ST_JustFlipped" in reason:
            stage = "BTC剛翻轉緩衝期過濾"
        elif "BTC_Regime" in reason:
            stage = "BTC大盤方向不符"
        elif "Symbol_1h_ST" in reason:
            stage = (
                f"個幣1h趨勢不符（5m={diagnostics.get('st_direction_5m')}/"
                f"1h={diagnostics.get('st_direction_1h')}）"
            )
        elif "1h_EMA50" in reason:
            stage = (
                f"1h EMA50方向不符（價格{diagnostics.get('price', 0):.6g}/"
                f"EMA50={diagnostics.get('ema_50_1h', 0):.6g}）"
            )
        elif "ADX_Too_Low" in reason:
            stage = f"ADX過低{diagnostics.get('adx', 0):.1f}<10過濾"
        elif "Score_Low" in reason:
            stage = f"分數不足（{component_text}）" if component_text else "分數不足"
        elif "Entry_Quality_Too_Low" in reason:
            quality_match = re.search(r"Entry_Quality_Too_Low\(([\d.]+<[\d.]+)\)", reason)
            stage = (
                f"進場品質不足{quality_match.group(1)}"
                if quality_match else "進場品質不足"
            )
        elif "Freshness_Too_Stale" in reason or "SuperTrend_Stale" in reason:
            stale = re.search(r"(?:Freshness_Too_Stale|SuperTrend_Stale)\((\d+)(?:bars)?\)", reason)
            stage = f"訊號新鮮度不足{stale.group(1)}根" if stale else "訊號新鮮度不足"
        elif "ADX_Declining_Exhaustion" in reason:
            adx_match = re.search(r"ADX_Declining_Exhaustion\(([\d.]+)<([\d.]+)\)", reason)
            stage = (
                f"ADX {adx_match.group(1)}←{adx_match.group(2)}且低於{ADX_QUALITY_MIN:g}，動能衰退過濾"
                if adx_match else "ADX低於品質底線且動能衰退過濾"
            )
        elif "Pullback_Range_Too_Narrow" in reason:
            stage = "KC至EMA20回踩空間不足0.10 ATR"
        elif "Price_Overextended" in reason:
            overext_match = re.search(r"Price_Overextended\(([\d.]+x_ATR)\)", reason)
            stage = f"價格乖離過大{overext_match.group(1)}" if overext_match else "價格乖離過大"
        elif "1h_Trend_Declining" in reason:
            stage = "大週期動能衰退過濾"
        elif "Mandatory_Fail: KC_Breakout_Unconfirmed" in reason:
            stage = "待KC突破"
        elif "EMA20" in reason or "1h_Trend" in reason:
            stage = "趨勢方向不符"
        elif "ATR_Too_High" in reason:
            atr_match = re.search(r"ATR_Too_High\(([\d.]+%)\)", reason)
            stage = f"波動過大{atr_match.group(1)}過濾" if atr_match else "波動過大過濾"
        elif "ATR_Too_Low" in reason:
            atr_match = re.search(r"ATR_Too_Low\(([\d.]+%)", reason)
            stage = f"波動過低{atr_match.group(1)}過濾" if atr_match else "波動過低過濾"
        elif "Volume" in reason:
            stage = f"量能不足（現量/均量={diagnostics.get('volume_ratio', 0):.2f}）"
        elif "RSI_Overbought" in reason:
            stage = f"RSI過熱{diagnostics.get('rsi', 0):.1f}>68"
        elif "RSI_Oversold" in reason:
            stage = f"RSI過冷{diagnostics.get('rsi', 0):.1f}<32"
        elif "RSI" in reason:
            stage = f"RSI方向不足（RSI={diagnostics.get('rsi', 0):.1f}）"
        else:
            stage = "條件未完成"
        coin = symbol.replace("/USDT", "")
        return f"{coin} {direction_text} {score_text},{stage}"

    @staticmethod
    def _format_ma5_wait_detail(df: pd.DataFrame, side: str) -> str:
        """把「等待MA5」拆成可行動的階段、四點、量能與RSI資訊。"""
        if df is None or df.empty or "ma5" not in df.columns:
            return "等待MA5拐頭轉彎"
        ma5 = df["ma5"].dropna()
        if len(ma5) < 4:
            return f"等待MA5有效資料（{len(ma5)}/4根）"

        values = [float(value) for value in ma5.iloc[-4:]]
        prev3, prev2, prev, curr = values
        row = df.iloc[-1]
        atr = max(float(row.get("atr") or 0.0), 1e-12)
        volume = float(row.get("volume") or 0.0)
        volume_ma = float(row.get("vol_ma_20") or 0.0)
        volume_ratio = volume / volume_ma if volume_ma > 0 else 0.0
        rsi = float(row.get("rsi") or 0.0)

        if side == "LONG":
            regular_shape = prev2 < prev3 and prev > prev2 and curr > prev
            first_turn = prev < prev2 and curr > prev
            still_retracing = curr <= prev
            no_pullback = prev3 <= prev2 <= prev <= curr
            turn_distance = curr - prev
            retrace_label = "回撤中，等待向上轉彎"
        else:
            regular_shape = prev2 > prev3 and prev < prev2 and curr < prev
            first_turn = prev > prev2 and curr < prev
            still_retracing = curr >= prev
            no_pullback = prev3 >= prev2 >= prev >= curr
            turn_distance = prev - curr
            retrace_label = "反彈中，等待向下轉彎"

        if regular_shape:
            regular_turn_atr = abs(curr - prev2) / atr
            stage = (
                f"兩根已轉向但拐幅{regular_turn_atr:.2f}ATR"
                f"<{MA5_REVERSAL_MIN_ATR_MULT:.2f}ATR"
            )
        elif first_turn:
            fast_turn_atr = max(0.0, turn_distance) / atr
            if fast_turn_atr < MA5_FAST_MIN_ATR_MULT:
                stage = (
                    f"已轉向第1根，但拐幅{fast_turn_atr:.2f}ATR"
                    f"<{MA5_FAST_MIN_ATR_MULT:.2f}ATR"
                )
            elif fast_turn_atr > MA5_FAST_MAX_ATR_MULT:
                stage = (
                    f"已轉向第1根，但拐幅{fast_turn_atr:.2f}ATR"
                    f">{MA5_FAST_MAX_ATR_MULT:.2f}ATR，等待第2根"
                )
            else:
                stage = (
                    f"已轉向第1根，但量能{volume_ratio:.2f}x"
                    f"<{MA5_FAST_MIN_VOLUME_RATIO:.2f}x，等待第2根"
                )
        elif still_retracing:
            stage = retrace_label
        elif no_pullback:
            stage = "尚未回撤，MA5仍沿原方向"
        else:
            stage = "峰谷型態尚未完成"

        ma_text = "→".join(f"{value:.6g}" for value in values)
        return (
            f"{stage}｜MA5 {ma_text}｜量{volume_ratio:.2f}x/"
            f"快線{MA5_FAST_MIN_VOLUME_RATIO:.2f}x｜RSI {rsi:.1f}"
        )

    @staticmethod
    def _ma5_exit_ready(
        position: dict, trigger: dict, mark_price: float, now: float
    ) -> tuple[bool, str]:
        """MA5單獨反轉的時間與幅度閘門；交易所SL及結構失守不走這裡。"""
        entry_price = float(position.get("entry_price") or 0.0)
        raw_opened_at = position.get("open_timestamp")
        opened_at = float(raw_opened_at if raw_opened_at is not None else now)
        age_sec = max(0.0, now - opened_at)
        if age_sec < MA5_EXIT_MIN_HOLD_SEC:
            return False, f"持倉{age_sec / 60:.1f}分<{MA5_EXIT_MIN_HOLD_SEC / 60:.0f}分"
        if entry_price <= 0:
            return False, "缺少進場價"

        adverse_pct = (
            (entry_price - mark_price) / entry_price
            if position.get("side") == "LONG"
            else (mark_price - entry_price) / entry_price
        )
        atr_pct = max(0.0, float(trigger.get("atr") or 0.0) / entry_price)
        required_pct = max(
            MA5_EXIT_MIN_ADVERSE_PCT,
            atr_pct * MA5_EXIT_MIN_ADVERSE_ATR_MULT,
        )
        if adverse_pct < required_pct:
            return (
                False,
                f"逆向{max(adverse_pct, 0.0):.2%}<門檻{required_pct:.2%}",
            )
        return True, f"持倉{age_sec / 60:.1f}分，逆向{adverse_pct:.2%}"
    @staticmethod
    def _bottom_entry_grace(position: dict, now: float) -> tuple[bool, float]:
        entry_mode = position.get("entry_mode")
        if entry_mode not in ("MA5_BOTTOM_LIMIT", "MA3_PIVOT", "EXHAUSTION_SNIPER", "PIVOT_TURN"):
            return False, 0.0
        opened_at = float(position.get("open_timestamp") or now)
        age_sec = max(0.0, now - opened_at)
        if entry_mode in ("EXHAUSTION_SNIPER", "PIVOT_TURN"):
            return age_sec < EXHAUSTION_SNIPER_GRACE_SEC, age_sec
        if entry_mode == "MA3_PIVOT":
            return age_sec < 180, age_sec
        return age_sec < MA5_BOTTOM_MIN_HOLD_SEC, age_sec



    @staticmethod
    def _trend_follow_breach(df: pd.DataFrame, side: str) -> dict:
        """Use two closed 15m bars to detect an EMA20 plus ATR-buffer breach."""
        if df.empty or len(df) < 20:
            return {"breached": False}
        work = df.copy()
        high_low = work["high"] - work["low"]
        high_cp = (work["high"] - work["close"].shift()).abs()
        low_cp = (work["low"] - work["close"].shift()).abs()
        work["tr"] = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
        work["atr"] = work["tr"].rolling(window=14).mean()
        work["ema_20"] = work["close"].ewm(span=20, adjust=False).mean()
        last_bar, prev_bar = work.iloc[-1], work.iloc[-2]
        close1, close2 = float(last_bar["close"]), float(prev_bar["close"])
        ema1, ema2 = float(last_bar["ema_20"]), float(prev_bar["ema_20"])
        atr1 = float(last_bar["atr"]) if not pd.isna(last_bar["atr"]) else close1 * 0.015
        atr2 = float(prev_bar["atr"]) if not pd.isna(prev_bar["atr"]) else close2 * 0.015
        buffer1 = max(close1 * 0.003, 0.5 * atr1)
        buffer2 = max(close2 * 0.003, 0.5 * atr2)
        breached = (
            close1 < ema1 - buffer1 and close2 < ema2 - buffer2
            if side == "LONG"
            else close1 > ema1 + buffer1 and close2 > ema2 + buffer2
        )
        return {
            "breached": bool(breached),
            "close1": close1, "close2": close2,
            "ema1": ema1, "ema2": ema2,
            "buffer1": buffer1, "buffer2": buffer2,
        }

    @staticmethod
    def _history_adjusted_score(raw_score: int, performance: dict) -> tuple[int, float]:
        # 歷史績效降分功能已關閉，一律不降分
        return int(raw_score), 1.0

    def _symbol_recent_performance(self, symbol: str, side: str) -> dict:
        """取這個幣種+方向最近 10 筆已平倉交易的勝率與平均損益，用來過濾歷史表現差的訊號。"""
        recent = [
            t for t in self.account.trades
            if t.get("symbol") == symbol
            and t.get("side") == side
            and str(t.get("action", "")).startswith("CLOSE")
        ][:10]
        if not recent:
            return {"trades": 0, "avg_pnl": 0.0, "win_rate": 1.0}
        pnls = [float(t.get("pnl") or 0.0) for t in recent]
        weights = [HISTORY_RECENCY_DECAY ** index for index in range(len(pnls))]
        total_weight = sum(weights) or 1.0
        return {
            "trades": len(pnls),
            "avg_pnl": sum(pnl * weight for pnl, weight in zip(pnls, weights)) / total_weight,
            "win_rate": sum((pnl > 0) * weight for pnl, weight in zip(pnls, weights)) / total_weight,
            "recency_decay": HISTORY_RECENCY_DECAY,
        }

    @staticmethod
    def _entry_filter_outcome(signal: dict) -> str:
        reason = str(signal.get("reason", ""))
        if signal.get("history_blocked"):
            return "history_score_block"
        if signal.get("action") == "WAIT_PULLBACK":
            return "pullback_candidate_ready"
        mapping = (
            ("BTC_1h_ST_JustFlipped", "btc_flip_block"),
            ("Symbol_1h_ST", "symbol_1h_mismatch"),
            ("1h_EMA50", "ema50_1h_mismatch"),
            ("ADX_Too_Low", "adx_too_low"),
            ("ATR_Too_High", "atr_too_high"),
            ("ATR_Too_Low", "atr_too_low"),
            ("RSI_Overbought", "rsi_overbought"),
            ("RSI_Oversold", "rsi_oversold"),
            ("KC_Breakout_Unconfirmed", "kc_unconfirmed"),
            ("Entry_Quality_Too_Low", "quality_too_low"),
            ("Freshness_Too_Stale", "freshness_too_stale"),
            ("ADX_Declining_Exhaustion", "adx_declining_below_floor"),
            ("Pullback_Range_Too_Narrow", "pullback_room_narrow"),
            ("Price_Overextended", "price_overextended"),
            ("1h_Trend_Declining", "trend_1h_declining"),
            ("Score_Low", "score_low"),
        )
        for marker, outcome in mapping:
            if marker in reason:
                return outcome
        return "other_hold"

    def _record_entry_filter(
        self, symbol: str, signal: dict, direction: str, outcome: str = None
    ) -> None:
        stats = getattr(self.account, "entry_filter_stats", None)
        if not isinstance(stats, dict):
            stats = {"evaluations": 0, "outcomes": {}, "components": {}, "adjustments": {}}
            self.account.entry_filter_stats = stats
        stats["evaluations"] = int(stats.get("evaluations", 0)) + 1
        outcomes = stats.setdefault("outcomes", {})
        outcome = outcome or self._entry_filter_outcome(signal)
        outcomes[outcome] = int(outcomes.get(outcome, 0)) + 1

        components = signal.get("score_components") or {}
        for name in ("kc", "volume", "rsi", "freshness", "quality"):
            if name not in components:
                continue
            threshold = (
                5 if name == "quality"
                else round(MIN_FRESHNESS_SCORE / 30 * ENTRY_FRESHNESS_SCORE_MAX)
                if name == "freshness"
                else 1
            )
            result = "pass" if float(components.get(name) or 0) >= threshold else "fail"
            bucket = stats.setdefault("components", {}).setdefault(name, {"pass": 0, "fail": 0})
            bucket[result] = int(bucket.get(result, 0)) + 1

        adjustments = stats.setdefault("adjustments", {})
        if int(signal.get("btc_score_penalty") or 0) > 0:
            adjustments["btc_penalty"] = int(adjustments.get("btc_penalty", 0)) + 1
        if float(signal.get("history_score_multiplier") or 1.0) < 0.99:
            adjustments["history_penalty"] = int(adjustments.get("history_penalty", 0)) + 1

        last = getattr(self.account, "entry_filter_last", None)
        if not isinstance(last, dict):
            last = {}
            self.account.entry_filter_last = last
        last[symbol] = {
            "timestamp": time.time(),
            "direction": direction,
            "outcome": outcome,
            "action": signal.get("action", "HOLD"),
            "score": signal.get("score"),
            "readiness_score": signal.get("readiness_score"),
            "readiness_components": dict(signal.get("readiness_components") or {}),
            "wait_estimate": signal.get("wait_estimate"),
            "raw_score": signal.get("raw_score"),
            "btc_adjusted_score": signal.get("btc_adjusted_score"),
            "history_adjusted_score": signal.get("history_adjusted_score"),
            "score_components": dict(components),
            "diagnostics": dict(signal.get("diagnostics") or {}),
            "reason": str(signal.get("reason", "")),
        }

    @staticmethod
    def _shadow_ready(signal: dict) -> bool:
        return (
            signal.get("action") == "WAIT_PULLBACK"
            and not signal.get("history_blocked")
            and int(signal.get("score") or 0) >= MIN_SCORE_THRESHOLD
        )

    def _record_shadow_parameter_comparison(
        self, symbol: str, df: pd.DataFrame, baseline: dict, direction: str
    ) -> None:
        current_adx = float(df.iloc[-1].get("adx") or 0.0)
        profiles = {
            "volume_adx25_06": {
                "label": "ADX>=25時量能0.8→0.6",
                "condition_met": current_adx >= 25.0,
                "overrides": {"volume_min_ratio": 0.6},
            },
            "atr_adx20_012": {
                "label": "ADX>=20時ATR下限0.15%→0.12%",
                "condition_met": current_adx >= 20.0,
                "overrides": {"atr_min_pct": 0.0012},
            },
            "rsi_70_30": {
                "label": "RSI極限68/32→70/30",
                "condition_met": True,
                "overrides": {"rsi_long_max": 70.0, "rsi_short_min": 30.0},
            },
            "btc_penalty_8": {
                "label": "BTC背離扣分12→8",
                "condition_met": True,
                "overrides": {"btc_score_penalty": 8},
            },
        }
        stats = getattr(self.account, "shadow_parameter_stats", None)
        if not isinstance(stats, dict):
            stats = {"evaluations": 0, "baseline_ready": 0, "profiles": {}}
            self.account.shadow_parameter_stats = stats
        stats["evaluations"] = int(stats.get("evaluations", 0)) + 1
        baseline_ready = self._shadow_ready(baseline)
        if baseline_ready:
            stats["baseline_ready"] = int(stats.get("baseline_ready", 0)) + 1

        last = getattr(self.account, "shadow_parameter_last", None)
        if not isinstance(last, dict):
            last = {}
            self.account.shadow_parameter_last = last

        common_kwargs = {
            "ema_50_1h": self.ema_50_1h_cache.get(symbol),
            "trend_1h_declining": self.adx_1h_declining_cache.get(symbol, False),
            "st_direction_1h": self.st_direction_1h_cache.get(symbol),
            "btc_st_direction_1h": self.btc_1h_st_direction,
            "btc_st_flip_age": self.btc_1h_st_flip_age,
            "symbol": symbol,
            "indicators_precomputed": True,
        }
        for name, profile in profiles.items():
            if profile["condition_met"]:
                shadow = self.strategy.evaluate_signal(
                    df, parameter_overrides=profile["overrides"], **common_kwargs
                )
                if shadow.get("action") == "WAIT_PULLBACK":
                    perf = self._symbol_recent_performance(symbol, shadow["side"])
                    adjusted_score, history_mult = self._history_adjusted_score(
                        int(shadow.get("score") or 0), perf
                    )
                    shadow["history_score_multiplier"] = history_mult
                    shadow["history_adjusted_score"] = adjusted_score
                    shadow["score"] = adjusted_score
                    if adjusted_score < MIN_SCORE_THRESHOLD:
                        shadow["history_blocked"] = True
            else:
                shadow = dict(baseline)

            shadow_ready = self._shadow_ready(shadow)
            profile_stats = stats.setdefault("profiles", {}).setdefault(name, {
                "label": profile["label"], "evaluations": 0, "condition_met": 0,
                "ready": 0, "incremental_ready": 0, "outcomes": {},
                "score_gain_total": 0.0, "score_gain_samples": 0,
            })
            profile_stats["evaluations"] = int(profile_stats.get("evaluations", 0)) + 1
            if profile["condition_met"]:
                profile_stats["condition_met"] = int(profile_stats.get("condition_met", 0)) + 1
            if shadow_ready:
                profile_stats["ready"] = int(profile_stats.get("ready", 0)) + 1
            if shadow_ready and not baseline_ready:
                profile_stats["incremental_ready"] = int(profile_stats.get("incremental_ready", 0)) + 1
            outcome = self._entry_filter_outcome(shadow)
            outcomes = profile_stats.setdefault("outcomes", {})
            outcomes[outcome] = int(outcomes.get(outcome, 0)) + 1
            baseline_score = baseline.get("score")
            shadow_score = shadow.get("score")
            if baseline_score is not None and shadow_score is not None:
                profile_stats["score_gain_total"] = float(profile_stats.get("score_gain_total", 0.0)) + (
                    float(shadow_score) - float(baseline_score)
                )
                profile_stats["score_gain_samples"] = int(profile_stats.get("score_gain_samples", 0)) + 1
            last.setdefault(name, {})[symbol] = {
                "timestamp": time.time(),
                "label": profile["label"],
                "condition_met": bool(profile["condition_met"]),
                "direction": direction,
                "baseline_ready": baseline_ready,
                "baseline_score": baseline_score,
                "baseline_outcome": self._entry_filter_outcome(baseline),
                "shadow_ready": shadow_ready,
                "shadow_score": shadow_score,
                "shadow_outcome": outcome,
                "incremental_ready": shadow_ready and not baseline_ready,
                "reason": str(shadow.get("reason", "")),
            }

    def _log_signal_progress(
        self, entries: List[str], now_time: float, symbols_snapshot: List[str]
    ) -> None:
        import core.config as config
        if getattr(config, "ENABLE_SYMBOL_ROTATION", True) and self.symbol_rotation.last_rotation_at <= 0:
            return
        if symbols_snapshot != list(DEFAULT_SYMBOLS):
            return
        if not entries or now_time - self.last_signal_progress_log_at < 60:
            return
        self.account.log(f"📊 [{len(symbols_snapshot)}幣訊號進度]\n" + "\n".join(f"• {entry}" for entry in entries), "INFO")
        self.last_signal_progress_log_at = now_time

    async def _validate_mainstream_symbols(self):
        """啟動時核對 MAINSTREAM_SYMBOLS 是否都是幣安合約市場真實存在、
        有效的永續合約——之前 ICP/USDT 明明不在名單該有的幣種裡卻混進來，
        導致下單時才炸 BadSymbol。self.exchange 不論什麼模式都是連接
        真實主網，用它的市場資料當作真相來源，只警示不中斷啟動（單一
        幣種異常不該影響其他幣種正常交易）。"""
        try:
            await self.exchange.load_markets()
        except Exception as exc:
            self.account.log(f"⚠️ [幣種名單核對] 無法載入市場資料，略過本次核對：{exc}", "WARNING")
            return
        invalid = []
        for sym in sorted(MAINSTREAM_SYMBOLS):
            try:
                market = self.exchange.market(sym)
                if not market.get("active", True) or not market.get("swap"):
                    invalid.append(f"{sym}（已下架或非永續合約）")
            except Exception:
                invalid.append(f"{sym}（市場不存在）")
        if invalid:
            self.account.log(
                f"🚨 [幣種名單核對] MAINSTREAM_SYMBOLS 內有 {len(invalid)} 個幣種異常，"
                f"請檢查並從名單移除：{', '.join(invalid)}",
                "DANGER",
            )
        else:
            self.account.log(
                f"✅ [幣種名單核對] MAINSTREAM_SYMBOLS 共 {len(MAINSTREAM_SYMBOLS)} 個幣種"
                f"皆為真實有效的幣安永續合約"
            )

    async def _load_execution_symbols(self) -> None:
        if PAPER_TRADING:
            self.execution_symbols = None
            return
        try:
            markets = await self.execution_exchange.load_markets()
            self.execution_symbols = {
                market["symbol"].replace(":USDT", "")
                for market in markets.values()
                if market.get("active") and market.get("swap")
                and market.get("quote") == "USDT"
            }
            mode = "Testnet" if USE_TESTNET else "實盤"
            self.account.log(
                f"✅ [{mode}執行市場] 可下單USDT永續 {len(self.execution_symbols)} 幣，"
                "全市場候選將取主網與執行市場交集"
            )
        except Exception as exc:
            self.execution_symbols = set()
            self.account.log(
                f"🛑 無法載入執行交易所合約，為避免錯誤下單已停用新倉：{exc}",
                "DANGER",
            )

    async def _execution_price_is_safe(self, symbol: str, side: str) -> bool:
        """確認執行合約存在，且主網與執行市場最佳價偏差不超標。

        ✅ 修正：改用「方向性偏差」取代「絕對偏差」。
        - 做多(LONG)時：執行 ask ≤ 主網 ask → 對我們有利（買得更便宜），直接放行。
          只有執行 ask 比主網 ask「貴」超過門檻，才代表會多付錢，才需要拒絕。
        - 做空(SHORT)時：執行 bid ≥ 主網 bid → 對我們有利（賣得更高），直接放行。
          只有執行 bid 比主網 bid「低」超過門檻，才代表會少收錢，才需要拒絕。
        原本用 abs() 同等對待有利/不利方向，導致底部進場時執行市場稍低被誤拒。
        """
        if PAPER_TRADING:
            return True
        if self.execution_symbols is None or symbol not in self.execution_symbols:
            self.account.log(f"🛑 {symbol} 不在執行交易所可下單合約交集，拒絕下單", "WARNING")
            return False
        try:
            main_book, execution_book = await asyncio.gather(
                self.exchange.fetch_order_book(symbol, limit=5),
                self.execution_exchange.fetch_order_book(symbol, limit=5),
            )
            book_side = "asks" if str(side).upper() == "LONG" else "bids"
            main_rows = main_book.get(book_side) or []
            execution_rows = execution_book.get(book_side) or []
            if not main_rows or not execution_rows:
                raise ValueError(f"{book_side}深度為空")
            main_price = float(main_rows[0][0])
            execution_price = float(execution_rows[0][0])

            # 方向性偏差：只計算「對我們不利」的方向
            # LONG (asks)：執行比主網貴 → 不利；SHORT (bids)：執行比主網便宜 → 不利
            if str(side).upper() == "LONG":
                adverse_deviation = (execution_price - main_price) / max(main_price, 1e-12)
            else:
                adverse_deviation = (main_price - execution_price) / max(main_price, 1e-12)

            abs_deviation = abs(execution_price - main_price) / max(main_price, 1e-12)

            if adverse_deviation > EXECUTION_PRICE_MAX_DEVIATION_PCT:
                # 執行市場對我們不利，且超過門檻 → 拒絕
                self.account.log(
                    f"🛑 {symbol} 最佳價偏差（執行市場不利偏差） {adverse_deviation:.2%}>"
                    f"{EXECUTION_PRICE_MAX_DEVIATION_PCT:.2%}，拒絕下單"
                    f"（主網={main_price:.8g}，執行={execution_price:.8g}，方向={side}）",
                    "WARNING",
                )
                return False

            if adverse_deviation < 0 and abs_deviation > EXECUTION_PRICE_MAX_DEVIATION_PCT:
                # 執行市場對我們有利（如底部 ask 更低），即使絕對偏差超標也放行
                self.account.log(
                    f"✅ {symbol} 執行市場有利偏差 {abs_deviation:.2%}（{side} 更優），放行下單"
                    f"（主網={main_price:.8g}，執行={execution_price:.8g}）",
                    "INFO",
                )

            return True
        except Exception as exc:
            self.account.log(f"🛑 {symbol} 執行市場價差驗證失敗，拒絕下單：{exc}", "WARNING")
            return False

    async def start(self):
        if self.is_running:
            return
        await self.account.initialize()
        # 策略切換後撤掉尚未成交的舊 MA5/舊回踩進場單，避免重啟後偷渡成交。
        for symbol, pending in list(self.account.pending_limit_orders.items()):
            mode = (pending.get("entry_context") or {}).get("entry_mode")
            if mode != "SUPPORT_PULLBACK":
                await self.account.cancel_pending_limit(symbol, "已切換為無 MA5 結構進場")
        await self._load_execution_symbols()
        await self._validate_mainstream_symbols()
        self.is_running = True
        if PAPER_TRADING:
            self.account.log(f"▶️ 8006 機器人啟動【紙上模擬模式 PAPER TRADING】不連接真實交易所，純本地模擬（{len(DEFAULT_SYMBOLS)}幣雙向交易）")
        elif USE_TESTNET:
            self.account.log(f"▶️ 8006 機器人啟動【Binance Testnet 測試網模式】無MA5三模式結構進場 / {len(DEFAULT_SYMBOLS)}幣雙向交易")
        else:
            self.account.log(
                "🔴🔴🔴 【正式實盤模式已啟用】（USE_TESTNET=false）：本次啟動將用真實資金下單！",
                "DANGER",
            )
        self.task = asyncio.create_task(self._main_loop())
        # 幣種輪替（含 AI 呼叫，最壞情況耗時數十秒）獨立成背景任務，
        # 避免跟主迴圈共用同一個 await 鏈，卡住停損停利檢查。
        import core.config as config
        if getattr(config, "ENABLE_SYMBOL_ROTATION", True):
            self.rotation_task = asyncio.create_task(self._rotation_loop())
        else:
            self.rotation_task = None
            self.account.log(f"⏸️ [自動幣種輪替] 已停用，鎖定預設 {len(config.DEFAULT_SYMBOLS)} 個幣種交易", "INFO")
        # 歷史分析是第三條完全獨立的工作，不等待主交易或幣種輪替。
        self.analysis_task = asyncio.create_task(self._analysis_loop())
        # 持倉平倉參考指標同樣獨立成背景任務，抓K線失敗/變慢不影響主迴圈。
        self.trigger_task = asyncio.create_task(self._position_trigger_loop())
        self.fixed_stop_task = asyncio.create_task(self._fixed_stop_loss_loop())
        # KC失敗、ATR追蹤、RR分批與1h翻向出場背景任務
        self.trend_follow_task = asyncio.create_task(self._run_structured_exits())
        # 舊移動停損任務保留但預設停用，避免與結構ATR追蹤衝突
        self.trailing_sl_task = asyncio.create_task(self._run_trailing_sl_loop())
        # 獨立的即時價格更新任務
        self.ticker_task = asyncio.create_task(self._ticker_loop())
        # 啟動時檢查既有歷史；摘要未變時會由 digest 快取直接略過。
        self.request_trade_analysis()

    async def stop(self, close_exchanges: bool = False):
        was_running = self.is_running
        self.is_running = False
        task_names = (
            "task", "rotation_task", "analysis_task", "trend_cache_task",
            "trigger_task", "fixed_stop_task", "trend_follow_task",
            "trailing_sl_task", "ticker_task",
        )
        tasks = []
        for name in task_names:
            task = getattr(self, name, None)
            if task:
                task.cancel()
                tasks.append(task)
                setattr(self, name, None)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # UI 的停止只是暫停交易任務，不能關閉 ccxt；否則按下再次啟動後，
        # fetch_klines 會一直得到 "instance was closed by the user"。
        if close_exchanges:
            await self.exchange.close()
            await self.ws_exchange.close()
            await self.execution_exchange.close()
        if was_running:
            self.account.log("⏹️ 量化交易機器人已停止")

    def request_trade_analysis(self) -> None:
        """平倉事件只設旗標，絕不在平倉／風控路徑等待 AI。"""
        self.analysis_event.set()

    async def _analysis_loop(self):
        """事件式歷史分析；連續平倉會合併，失敗才按節流時間重試。"""
        retry_delay = None
        while self.is_running:
            try:
                if retry_delay is None:
                    await self.analysis_event.wait()
                else:
                    try:
                        await asyncio.wait_for(
                            self.analysis_event.wait(),
                            timeout=retry_delay,
                        )
                    except asyncio.TimeoutError:
                        pass
                self.analysis_event.clear()

                analysis_updated = await self.symbol_rotation.trade_analysis.analyze_if_changed(
                    self.account.trades
                )
                analysis_status = self.symbol_rotation.trade_analysis.status()
                if analysis_updated:
                    count = analysis_status.get("trade_count", 0)
                    self.account.log(f"🧠 [AI 歷史分析] 已記錄並分析 {count} 筆平倉交易", "INFO")

                retry_delay = (
                    self.symbol_rotation.trade_analysis.retry_after_sec
                    if analysis_status.get("status") == "fallback"
                    else None
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                retry_delay = self.symbol_rotation.trade_analysis.retry_after_sec
                self.account.log(
                    f"⚠️ [AI 歷史分析] 暫時失敗，交易與輪替不受影響："
                    f"{type(exc).__name__}: {exc}",
                    "WARNING",
                )

    async def _rotation_loop(self):
        """獨立於主交易迴圈之外定時執行幣種輪替，避免 AI 呼叫延遲停損停利判斷。"""
        last_unhealthy_check_at = 0.0
        while self.is_running:
            try:
                if not SYMBOL_ROTATION_ENABLED:
                    await asyncio.sleep(60)
                    continue
                now_time = time.time()
                rotation_interval_sec = (
                    60.0 if not self.account.positions
                    else float(SYMBOL_ROTATION_INTERVAL_SEC)
                )
                replacement_pending = bool(
                    getattr(self.symbol_rotation, "next_rotation_exclusions", set())
                )
                if (
                    replacement_pending
                    or now_time - self.symbol_rotation.last_rotation_at >= rotation_interval_sec
                ):
                    changes = await self.symbol_rotation.rotate(self.exchange, self.execution_symbols)
                    if changes:
                        change_text = "、".join(
                            f"{item['out']}→{item['in']}" if item.get("in")
                            else f"{item['out']}→移除"
                            for item in changes
                        )
                        self.account.log(f"🔄 [幣種輪替] {change_text}；{self.symbol_rotation.last_reason}", "INFO")
                    else:
                        self.account.log(f"✅ [幣種輪替] 目前 {len(DEFAULT_SYMBOLS)} 幣仍為合格組合；{self.symbol_rotation.last_reason}", "INFO")
                    last_unhealthy_check_at = now_time
                elif now_time - last_unhealthy_check_at >= UNHEALTHY_SYMBOL_CHECK_INTERVAL_SEC:
                    last_unhealthy_check_at = now_time
                    purge_changes = await self.symbol_rotation.purge_unhealthy(self.exchange)
                    if purge_changes:
                        change_text = "、".join(
                            f"{item['out']}→{item['in']}（{item['reason']}）" if item.get("in")
                            else f"{item['out']}→移除（{item['reason']}）"
                            for item in purge_changes
                        )
                        self.account.log(f"🚨 [不健康幣種淘汰] {change_text}", "WARNING")
                try:
                    await asyncio.wait_for(self.rotation_event.wait(), timeout=30.0)
                    self.rotation_event.clear()
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                # 輪替與 AI 都是輔助層，失敗時不能中斷持倉管理與主策略。
                self.symbol_rotation.last_rotation_at = time.time()
                self.symbol_rotation.last_reason = f"輪替失敗，保留原牌面：{type(exc).__name__}"
                self.account.log(f"⚠️ [幣種輪替] 暫時失敗，保留原牌面並繼續交易：{type(exc).__name__}: {exc}", "WARNING")
                await asyncio.sleep(30)


    async def _fixed_stop_loss_loop(self):
        """Runs every 10 seconds to check for a hard 0.5% stop loss using mark price"""
        while self.is_running:
            try:
                for symbol, position in list(self.account.positions.items()):
                    live_price = float(self.tickers.get(symbol) or position.get("mark_price") or position["entry_price"])
                    entry_price = float(position["entry_price"])
                    side = position["side"]
                    entry_mode = str(position.get("entry_mode") or position.get("reason") or "")

                    if entry_price > 0 and live_price > 0:
                        if side == "LONG":
                            pnl_pct = (live_price - entry_price) / entry_price
                        else:
                            pnl_pct = (entry_price - live_price) / entry_price

                        # Channel Swing 完全交由主循環的「對面 KC 外軌轉色」管理。
                        # 不執行同側 KC 破軌、固定停損、結構破壞或移動停利。
                        if entry_mode.upper() == "CHANNEL_SWING":
                            if any(float(position.get(key) or 0.0) != 0.0 for key in (
                                "sl", "initial_sl", "initial_risk",
                            )):
                                position["sl"] = 0.0
                                position["initial_sl"] = 0.0
                                position["initial_risk"] = 0.0
                                meta = self.account.position_meta.setdefault(symbol, {})
                                meta["sl"] = 0.0
                                meta["initial_sl"] = 0.0
                                meta["initial_risk"] = 0.0
                                save_state = getattr(self.account, "save_state", None)
                                if callable(save_state):
                                    save_state()
                            highest_pnl = float(position.get("peak_pnl_pct") or pnl_pct)
                            if pnl_pct > highest_pnl:
                                position["peak_pnl_pct"] = pnl_pct
                            continue

                        import core.config as config

                        # === KC 破軌緊急停損 (全模式適用) ===
                        # 只要價格向逆方向貫穿 KC 軌道，強制停損。
                        # 這是防止帳戶在單邊暴走行情中死扛的最後防線。
                        trigger = self.position_triggers.get(symbol, {})
                        kc_upper = float(trigger.get("kc_upper") or 0)
                        kc_lower = float(trigger.get("kc_lower") or 0)
                        if kc_upper > 0 and kc_lower > 0:
                            is_kc_ripped = self._adverse_kc_outer_breached(
                                side, live_price, kc_upper, kc_lower,
                            )
                            if is_kc_ripped:
                                self.account.log(
                                    f"🆘 [KC 破軌停損] {symbol} {side} 跌破/漲破軌道！"
                                    f"現價={live_price:.6g} 已超出 KC 範圍，緊急停損！",
                                    "WARNING"
                                )
                                self._kc_rip_after[symbol] = time.time()
                                await self.account.close_position(
                                    symbol, live_price, f"KC 破軌緊急停損 (KC {kc_lower:.6g}~{kc_upper:.6g})"
                                )
                                continue
                        # ================================================

                        # --- 大爆走 (Mega Trend) 模式檢查 ---
                        # 若處於強烈單邊趨勢 (ADX >= 30)，關閉移動停利與固定止損，完全交給 MA5 轉向平倉
                        trigger = self.position_triggers.get(symbol, {})
                        adx_val = trigger.get("adx", 0.0)
                        is_mega_trend = (adx_val >= 30.0)

                        # Trailing Stop Logic
                        highest_pnl = position.get("peak_pnl_pct", pnl_pct)
                        if pnl_pct > highest_pnl:
                            position["peak_pnl_pct"] = pnl_pct
                            highest_pnl = pnl_pct

                        is_structure_exit_mode = self._is_continuous_wave_position(
                            position, self.account.position_meta.get(symbol, {}),
                        )
                        if is_structure_exit_mode:
                            if (
                                not CONTINUOUS_OUTER_RAIL_EXIT_ONLY
                                and pnl_pct <= -config.FIXED_STOP_LOSS_PCT
                            ):
                                await self.account.close_position(
                                    symbol, live_price,
                                    f"固定止損 ({config.FIXED_STOP_LOSS_PCT*100:.1f}%)",
                                )
                            # 外軌專用模式會忽略固定百分比停損；只保留上方的
                            # 不利方向 KC 外軌破軌與手動平倉。
                            continue
                        if not is_mega_trend:
                            if (
                                not is_structure_exit_mode
                                and config.ENABLE_TRAILING_STOP
                            ) and highest_pnl >= config.TRAILING_STOP_ACTIVATION_PCT:
                                if pnl_pct <= highest_pnl * config.TRAILING_STOP_RETAIN_PCT:
                                    self.account.log(f"🚨 [移動停利觸發] {symbol} 從最高 +{highest_pnl*100:.2f}% 回落至 +{pnl_pct*100:.2f}%，執行自動平倉", "SUCCESS")
                                    await self.account.close_position(symbol, live_price, f"移動停利 (最高 +{highest_pnl*100:.2f}%)")
                                    continue # Skip fixed stop loss check since it's closed

                            # Fixed Stop Loss Logic (只對非 Channel Swing 開倉有效，Channel Swing 已有上面的KC撕裂防線)
                            if "CHANNEL_SWING" not in entry_mode and pnl_pct <= -config.FIXED_STOP_LOSS_PCT:
                                await self.account.close_position(symbol, live_price, f"固定止損 ({config.FIXED_STOP_LOSS_PCT*100:.1f}%)")
                        else:
                            # 即使在大爆走模式，如果帳面嚴重虧損仍需終極防線，避免ADX騙線
                            if pnl_pct <= -config.FIXED_STOP_LOSS_PCT * 2.0:
                                await self.account.close_position(symbol, live_price, f"終極防線止損 ({config.FIXED_STOP_LOSS_PCT*200:.1f}%)")

                import asyncio
                await asyncio.sleep(10)
            except Exception as e:
                self.account.log(f"⚠️ Fixed SL loop error: {str(e)}", "WARNING")
                import asyncio
                await asyncio.sleep(10)

    async def _position_trigger_loop(self):
        """持倉手動平倉參考指標：用 EMA20（策略本身 Keltner 通道用的同一條
        基準線）跟近 20 根 5 分K的前低/前高，判斷「跌破均線」「跌破前低」
        （多單）或「站上均線」「站上前高」（空單）。純粹是給使用者按網頁
        「平倉」按鈕前參考用的視覺提示，不會觸發任何自動平倉，獨立成
        背景任務、抓K線失敗或變慢也不影響主迴圈的止損止利判斷。"""
        while self.is_running:
            try:
                for symbol, position in list(self.account.positions.items()):
                    # 使用正確的 K 棒週期做平倉判斷：
                    # CONTINUOUS_REVERSE 模式進場的部位用 5m（與進場相同），
                    # 其他路徑仍用 MA5_EXIT_TIMEFRAME（預設 1m）。
                    pos_reason = str(position.get("reason") or "")
                    pos_entry_mode = str(position.get("entry_mode") or "")
                    is_cr_position = bool(
                        pos_entry_mode in ("MA3_MA15_MARKET", "STRONG_LONG_BURST")
                        or any(k in pos_reason for k in (
                            "TROUGH_TURN", "PEAK_TURN", "RANGE_SWING_REVERSE",
                            "KC_MIDDLE_PEAK_REVERSE", "KC_MIDDLE_TROUGH_REVERSE",
                            "CROSS_UP", "CROSS_DOWN", "TREND_LONG", "TREND_SHORT",
                        ))
                    )
                    from core.config import CONTINUOUS_REVERSE_TIMEFRAME
                    exit_tf = CONTINUOUS_REVERSE_TIMEFRAME if is_cr_position else MA5_EXIT_TIMEFRAME
                    # keep_live=True: 用最新未收盤的 tick 資料即時判斷，只要 MA5 反向彎了就立刻走，不需等該分 K 收盤
                    df = await self.fetch_klines(symbol, timeframe=exit_tf, limit=30, keep_live=True)
                    # 外軌延伸與峰谷確認只能使用已收盤 K；先建立，避免前段
                    # 外軌鎖定判斷在後段初始化前取用。
                    df_closed = drop_unclosed_candle(df, exit_tf)
                    if CONTINUOUS_PIVOT_ONLY and is_cr_position:
                        df = df_closed
                        if df.empty:
                            continue
                    trigger = compute_position_trigger(df, position.get("side"))
                    trigger["updated_at"] = time.time()
                    # 有利潤時價格仍延續原方向但量能萎縮 -> 主力收手動能耗盡的
                    # 反轉警訊。純顯示用（UI 用愛心圖示提示），不觸發任何平倉。
                    # 警訊方向跟持倉方向相反：多單看「量縮頂背離」(want_dir=-1)，
                    # 空單看「量縮底背離」(want_dir=1)。
                    is_profitable = float(position.get("unrealized_pnl") or 0.0) > 0
                    warn_dir = -1 if position.get("side") == "LONG" else 1
                    trigger["volume_divergence_alert"] = bool(
                        is_profitable and not df.empty and has_volume_divergence(df, warn_dir)
                    )
                    self.position_triggers[symbol] = trigger
                    position_meta = self.account.position_meta.get(symbol, {})
                    kc_outer_lock = {"blocked": False, "armed": False, "released": False}
                    if is_cr_position:
                        from core.indicators import evaluate_kc_outer_run_lock
                        kc_outer_lock = evaluate_kc_outer_run_lock(
                            df_closed, position.get("side"),
                            armed=bool(position_meta.get("kc_outer_run_armed")),
                            outer_run_active=bool(position_meta.get("outer_run_active")),
                        )
                        position_meta["kc_outer_run_armed"] = kc_outer_lock["armed"]
                        position["kc_outer_run_armed"] = kc_outer_lock["armed"]
                        position_meta["outer_run_active"] = kc_outer_lock["outer_run_active"]
                        position["outer_run_active"] = kc_outer_lock["outer_run_active"]
                        trigger["kc_outer_run_armed"] = kc_outer_lock["armed"]
                        trigger["outer_run_active"] = kc_outer_lock["outer_run_active"]
                        trigger["returned_inside_outer"] = kc_outer_lock["returned_inside_outer"]
                        trigger["kc_outer_reversal_blocked"] = kc_outer_lock["blocked"]
                        trigger["kc_middle_reached"] = kc_outer_lock["reached_middle"]
                    # Tier 1（本地保本，仍是靜態單）不算真正接管，5m強防線要繼續生效；
                    # 只有 Tier 2+（交易所原生毫秒級追蹤）才屏蔽，理由同15m趨勢止損。
                    has_native_trailing = position_meta.get("native_trailing_tier", 0) >= 2
                    # 已經靠移動停利鎖到保本以上的部位，交給移動停利自己顧，
                    # 5m防線只負責「還沒鎖利」的部位快速停損，避免鎖利後的
                    # 正常拉回被誤判成反轉提早出場。
                    is_profit_locked = bool(position_meta.get("is_breakeven_moved")) or has_native_trailing
                    # MA5單獨反轉需通過「持倉時間＋逆向ATR幅度」閘門，避免
                    # 4~8分鐘內的正常震盪造成純手續費磨損；EMA20緩衝帶與
                    # 前低/前高同時失守屬結構破壞，仍立即退出。
                    entry_p = float(position.get("entry_price") or 0.0)
                    mark_p = float(df['close'].iloc[-1]) if not df.empty else (self.tickers.get(symbol) or entry_p)
                    structural_strong = bool(
                        trigger.get("ema_breach_confirmed")
                        and trigger.get("structure_broken")
                    )
                    ma5_exit_ready, ma5_exit_ready_gate = self._ma5_exit_ready(
                        position, trigger, mark_p, time.time()
                    )
                    trigger["ma5_exit_ready"] = ma5_exit_ready
                    trigger["ma5_exit_gate"] = ma5_exit_ready_gate
                    from core.indicators import detect_ma3_ma15_cross_and_turn
                    # Exhaustion Sniper 的前三分鐘只保留帳戶層硬停損；所有
                    # 技術型出場（包含急速反向 K）都延後到保護期結束。
                    entry_grace, entry_grace_age = self._bottom_entry_grace(position, time.time())
                    trigger["bottom_entry_grace"] = entry_grace
                    trigger["bottom_entry_age_sec"] = entry_grace_age
                    # A sharp adverse live candle is an emergency exit: it must have a
                    # meaningful body and cross MA3, so ordinary pullbacks are ignored.
                    rapid_adverse_exit = False
                    if (
                        RAPID_PIVOT_IMMEDIATE_REVERSE_ENABLED
                        and not df.empty
                        and len(df) >= 3
                    ):
                        adverse_live = df.iloc[-1]
                        adverse_open = float(adverse_live.get("open", adverse_live["close"]))
                        adverse_close = float(adverse_live["close"])
                        adverse_ma3 = float(
                            df["ma3"].iloc[-1]
                            if "ma3" in df.columns
                            else df["close"].rolling(3).mean().iloc[-1]
                        )
                        adverse_atr = max(
                            float(trigger.get("atr") or 0.0), adverse_close * 1e-12
                        )
                        adverse_body = abs(adverse_close - adverse_open)
                        rapid_adverse_exit = bool(
                            adverse_body >= adverse_atr * RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR
                            and (
                                (position.get("side") == "LONG"
                                 and adverse_close < adverse_open
                                 and adverse_close < adverse_ma3)
                                or (position.get("side") == "SHORT"
                                    and adverse_close > adverse_open
                                    and adverse_close > adverse_ma3)
                            )
                        )
                    trigger["rapid_adverse_exit"] = rapid_adverse_exit
                    if rapid_adverse_exit and not entry_grace and not is_cr_position:
                        curr_p = self.tickers.get(symbol) or adverse_close
                        close_reason = (
                            f"{exit_tf} adverse rapid reversal: body >= "
                            f"{RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR:.2f} ATR and crossed MA3"
                        )
                        self.account.log(
                            f"[Rapid adverse exit] {symbol} {close_reason}", "WARNING"
                        )
                        await self.account.close_position(
                            symbol, curr_p, close_reason, is_manual=True
                        )
                        self._soft_warning_since.pop(symbol, None)
                        continue
                    exit_frame = self.strategy.compute_indicators(df_closed.copy())
                    exit_frame["ma15"] = exit_frame["close"].rolling(15).mean()
                    structure_exit_frame = exit_frame
                    if "timestamp" in exit_frame.columns:
                        opened_ms = float(position.get("open_timestamp") or 0.0) * 1000.0
                        structure_exit_frame = exit_frame[
                            exit_frame["timestamp"].astype(float) >= opened_ms
                        ]
                    structure_failure_exit = bool(
                        is_cr_position
                        and self._two_bar_structure_failure_exit(
                            structure_exit_frame, position.get("side"),
                        )
                    )
                    trigger["two_bar_structure_failure_exit"] = structure_failure_exit
                    if structure_failure_exit and not CONTINUOUS_OUTER_RAIL_EXIT_ONLY:
                        curr_p = float(
                            self.tickers.get(symbol)
                            or exit_frame["close"].iloc[-1]
                        )
                        close_reason = (
                            f"{exit_tf} 空方結構失效：MA3與KC連續上升，兩根收於中軌及MA15上方；先平空換幣"
                            if position.get("side") == "SHORT" else
                            f"{exit_tf} 多方結構失效：MA3與KC連續下降，兩根收於中軌及MA15下方；先平多換幣"
                        )
                        self.account.log(f"🛡️ {symbol} {close_reason}", "WARNING")
                        await self.account.close_position(
                            symbol, curr_p, close_reason, is_manual=True,
                        )
                        self._soft_warning_since.pop(symbol, None)
                        continue
                    exit_signal_info = detect_ma3_ma15_cross_and_turn(
                        exit_frame, allow_live_pivot=False
                    )
                    confirmed_outer_exit = self._confirmed_outer_reversal(
                        position.get("side"), exit_signal_info, exit_frame,
                    )
                    trigger["confirmed_outer_exit"] = confirmed_outer_exit
                    wave_regime = str(
                        position.get("wave_regime")
                        or position_meta.get("wave_regime")
                        or self._continuous_wave_regime.get(symbol, "TREND")
                    ).upper()
                    # 連續波段只由不利方向外軌破軌或硬停損離場；舊版峰谷後
                    # 1U 回吐旗標可能由持久化資料帶回，必須明確清除。
                    if is_cr_position:
                        for protect_key in (
                            "outer_run_pivot_protect_armed", "kc_pivot_protect_armed",
                        ):
                            position_meta[protect_key] = False
                            position[protect_key] = False
                            trigger[protect_key] = False
                    # 未收線訊號只保留在 UI 診斷，不得比已收盤的正式
                    # 外軌峰／谷更早平掉既有持倉。
                    live_rail_signal_info = detect_ma3_ma15_cross_and_turn(
                        df, allow_live_pivot=True
                    )
                    # 連續持倉不得用未收線的大實體提前平倉。
                    # 只接受 confirmed_outer_exit 的已收盤外軌峰／谷。
                    live_next_rail_flatten = False
                    trigger["live_next_rail_flatten"] = live_next_rail_flatten
                    trigger["live_next_rail_entry_type"] = live_rail_signal_info.get("entry_type", "")
                    from core.indicators import (
                        allow_two_bar_protective_exit,
                        detect_two_bar_opposite_trend,
                    )
                    opposite_trend_info = detect_two_bar_opposite_trend(
                        self.strategy.compute_indicators(df_closed.copy()),
                        position.get("side"),
                    )
                    two_bar_opposite_trend_exit = bool(
                        is_cr_position and opposite_trend_info.get("exit")
                        and allow_two_bar_protective_exit(exit_signal_info)
                    )
                    trigger["two_bar_opposite_trend_exit"] = two_bar_opposite_trend_exit
                    trigger["two_bar_opposite_trend_reason"] = opposite_trend_info.get("reason", "")
                    trigger["two_bar_opposite_trend_filter"] = exit_signal_info.get("entry_type", "")
                    false_breakout_hold = str(
                        exit_signal_info.get("reason", "")
                    ).startswith("假突破過濾")
                    trigger["false_breakout_hold"] = false_breakout_hold
                    if is_cr_position:
                        # 這個訊息是「等下一個相反峰谷」的CR專屬語意，非CR
                        # 部位維持上面 _ma5_exit_ready 算出來的持倉時間/幅度
                        # 閘門說明，不要被這裡蓋掉。
                        trigger["ma5_exit_gate"] = (
                            "連續波段續抱：只等不利方向 KC 外軌破軌或硬停損"
                        )
                    market_mode = str(
                        position.get("market_mode")
                        or position_meta.get("market_mode")
                        or "TREND"
                    ).upper()
                    macro_trend_mode = market_mode in ("BULL", "BEAR")
                    trend_exhaustion_exit = False
                    trend_exit_info = {}
                    if is_cr_position and wave_regime == "TREND" and len(df_closed) >= 20:
                        from core.indicators import detect_strong_trend_exhaustion
                        trend_df = self.strategy.compute_indicators(df_closed)
                        trend_df["ma15"] = trend_df["close"].rolling(15).mean()
                        if "timestamp" in trend_df.columns:
                            opened_ms = float(position.get("open_timestamp") or 0.0) * 1000.0
                            after_entry = trend_df[trend_df["timestamp"].astype(float) >= opened_ms]
                            if len(after_entry) >= 3:
                                trend_df = after_entry
                        trend_exit_info = detect_strong_trend_exhaustion(
                            trend_df, position.get("side"),
                            previous_extreme=position_meta.get("trend_extreme_price"),
                            previous_ma3_extreme=position_meta.get("trend_ma3_extreme"),
                            retrace_atr=0.50 if macro_trend_mode else 0.15,
                        )
                        position_meta["trend_extreme_price"] = trend_exit_info.get("extreme_price")
                        position_meta["trend_ma3_extreme"] = trend_exit_info.get("ma3_extreme")
                        position["trend_extreme_price"] = trend_exit_info.get("extreme_price")
                        trend_exhaustion_exit = bool(
                            trend_exit_info.get("exit")
                            and not kc_outer_lock.get("blocked")
                        )
                        # 原持倉的 MA3/MA15 方向仍成立，而且此刻平倉連雙邊
                        # 手續費與平倉滑價都賺不回時，不因「強趨勢結束」來回磨損。
                        latest_ma3 = float(trend_df["ma3"].iloc[-1])
                        latest_ma15 = float(trend_df["ma15"].iloc[-1])
                        original_direction_intact = bool(
                            (position.get("side") == "LONG" and latest_ma3 >= latest_ma15)
                            or (position.get("side") == "SHORT" and latest_ma3 <= latest_ma15)
                        )
                        exit_price = float(
                            self.tickers.get(symbol)
                            or df_closed["close"].iloc[-1]
                        )
                        entry_price = float(position.get("entry_price") or exit_price)
                        qty = float(position.get("qty") or 0.0)
                        exec_exit_price = (
                            exit_price * (1.0 - SLIPPAGE_PCT)
                            if position.get("side") == "LONG"
                            else exit_price * (1.0 + SLIPPAGE_PCT)
                        )
                        raw_exit_pnl = (
                            (exec_exit_price - entry_price) * qty
                            if position.get("side") == "LONG"
                            else (entry_price - exec_exit_price) * qty
                        )
                        estimated_exit_fees = (
                            entry_price + exec_exit_price
                        ) * qty * TAKER_FEE_RATE
                        estimated_net_exit_pnl = raw_exit_pnl - estimated_exit_fees
                        trend_exhaustion_fee_hold = bool(
                            trend_exhaustion_exit
                            and original_direction_intact
                            and raw_exit_pnl <= estimated_exit_fees
                        )
                        if trend_exhaustion_fee_hold:
                            trend_exhaustion_exit = False
                        trigger["trend_exhaustion_fee_hold"] = trend_exhaustion_fee_hold
                        trigger["estimated_net_exit_pnl"] = estimated_net_exit_pnl
                        trigger["original_direction_intact"] = original_direction_intact
                    trigger["trend_exhaustion_exit"] = trend_exhaustion_exit
                    trigger["trend_retrace_atr"] = trend_exit_info.get("retrace_atr", 0.0)
                    trigger["trend_extreme_price"] = trend_exit_info.get("extreme_price")

                    # KC 內連續持倉在正式同方向峰／谷形成前，除硬止損外不採用
                    # 盤中轉折、兩根反向 K 或強趨勢衰退等技術型提前平倉。
                    if (
                        is_cr_position
                        and wave_regime in ("RANGE", "TREND")
                        and not kc_outer_lock.get("outer_run_active")
                        and not confirmed_outer_exit
                    ):
                        live_next_rail_flatten = False
                        two_bar_opposite_trend_exit = False
                        trend_exhaustion_exit = False
                        trigger["live_next_rail_flatten"] = False
                        trigger["two_bar_opposite_trend_exit"] = False
                        trigger["trend_exhaustion_exit"] = False

                    # 立即轉向（fast_pivot）：紅K/綠K已回到 KC 中軌，無須等待外軌 blocked 狀態
                    # 直接視為「中軌反轉」平仓，不論外軌是否已解鎖。
                    outer_run_active = bool(kc_outer_lock.get("outer_run_active"))
                    # 外軌延伸期間無條件持有；只有第一根相反顏色 K 已收盤回到
                    # 同側外軌內，才解除死抱並平倉。影線或未收 K 一律不算。
                    # 已收盤回軌的反手交給主交易循環同一輪「平舊＋開新」，
                    # 此快速持倉迴圈不可先把舊倉平掉，否則新倉會被落下。
                    outer_run_return_pending = bool(
                        kc_outer_lock.get("returned_inside_outer")
                    )
                    if outer_run_return_pending:
                        position_meta["outer_run_return_pending"] = True
                    trigger["outer_run_return_pending"] = outer_run_return_pending
                    outer_run_return_exit = False
                    opposite_candle_exit = False
                    trigger["opposite_candle_exit"] = opposite_candle_exit
                    immediate_reversal = bool(
                        not CONTINUOUS_PIVOT_ONLY
                        and not outer_run_active
                        and confirmed_outer_exit
                        and wave_regime == "RANGE"
                        and exit_signal_info.get("fast_pivot")
                        and (
                            (position.get("side") == "LONG"
                             and exit_signal_info.get("entry_type") == "PEAK_TURN")
                            or (position.get("side") == "SHORT"
                                and exit_signal_info.get("entry_type") == "TROUGH_TURN")
                        )
                    )
                    range_swing_reverse_side = self._range_swing_reverse_side(
                        position.get("side"), exit_signal_info.get("entry_type"),
                        wave_regime,
                        outer_run_active or outer_run_return_exit or opposite_candle_exit,
                    )
                    opposite_pivot = bool(
                        range_swing_reverse_side and confirmed_outer_exit
                    )
                    outer_rail_flatten = bool(immediate_reversal or opposite_pivot)
                    if CONTINUOUS_PIVOT_ONLY and PIVOT_LONG_ONLY:
                        pivot_only_upper_exit = bool(
                            position.get("side") == "LONG" and confirmed_outer_exit
                        )
                        opposite_pivot = pivot_only_upper_exit
                        outer_rail_flatten = pivot_only_upper_exit
                        trend_exhaustion_exit = False
                    if CONTINUOUS_PIVOT_ONLY and not PIVOT_LONG_ONLY:
                        opposite_pivot = False
                        outer_rail_flatten = False
                        trend_exhaustion_exit = False
                    trigger["outer_rail_flatten"] = outer_rail_flatten
                    trigger["immediate_reversal"] = immediate_reversal
                    pre_pivot_wait = (
                        position.get("side") == "LONG"
                        and exit_signal_info.get("entry_type") == "WAIT_PRE_PIVOT"
                        and float(exit_signal_info.get("ma3_slope", 0.0)) < 0
                    ) or (
                        position.get("side") == "SHORT"
                        and exit_signal_info.get("entry_type") == "WAIT_PRE_PIVOT"
                        and float(exit_signal_info.get("ma3_slope", 0.0)) > 0
                    )
                    trigger["pre_pivot_wait"] = pre_pivot_wait
                    # Fast path: MA3 has less smoothing than MA5, so detect the
                    # first meaningful live reversal at 0.02 ATR, including a flat step at the peak or trough.
                    ma3_live = (
                        df["ma3"] if "ma3" in df.columns
                        else df["close"].rolling(3).mean()
                    ).dropna()
                    fast_ma3_pivot = False
                    first_reversal_pivot = False
                    rapid_impulse_pivot = False
                    rapid_reverse_side = None
                    if is_cr_position and len(ma3_live) >= 4:
                        ma3_prev3 = float(ma3_live.iloc[-4])
                        ma3_prev2 = float(ma3_live.iloc[-3])
                        ma3_prev = float(ma3_live.iloc[-2])
                        ma3_curr = float(ma3_live.iloc[-1])
                        ma3_current_slope = ma3_curr - ma3_prev
                        fast_threshold = max(float(trigger.get("atr") or 0.0) * 0.02, float(df["close"].iloc[-1]) * 0.00035, 1e-12)
                        ma3_turn = bool(
                            (position.get("side") == "LONG"
                             and ma3_prev >= ma3_prev2 >= ma3_prev3
                             and ma3_prev - ma3_prev3 >= fast_threshold
                             and ma3_current_slope <= -fast_threshold)
                            or (position.get("side") == "SHORT"
                                and ma3_prev <= ma3_prev2 <= ma3_prev3
                                and ma3_prev3 - ma3_prev >= fast_threshold
                                and ma3_current_slope >= fast_threshold)
                        )
                        # The first opposite live candle after a local extreme confirms
                        # the turn sooner than waiting for the MA5 curve to bend.
                        live_candle = df.iloc[-1]
                        live_open = float(live_candle.get("open", live_candle["close"]))
                        live_close = float(live_candle["close"])
                        pivot_candle = df.iloc[-2]
                        earlier_high = float(df["high"].iloc[-6:-2].max())
                        earlier_low = float(df["low"].iloc[-6:-2].min())
                        pivot_high = float(pivot_candle["high"])
                        pivot_low = float(pivot_candle["low"])
                        local_peak_at_previous_bar = pivot_high >= earlier_high
                        local_trough_at_previous_bar = pivot_low <= earlier_low
                        age_sec = max(0.0, time.time() - float(position.get("open_timestamp") or time.time()))
                        first_reversal_pivot = bool(
                            age_sec > 180 and
                            ((position.get("side") == "LONG"
                              and local_peak_at_previous_bar
                              and ma3_current_slope <= -fast_threshold
                              and live_close < live_open
                              and pivot_high - live_close >= fast_threshold)
                            or (position.get("side") == "SHORT"
                                and local_trough_at_previous_bar
                                and ma3_current_slope >= fast_threshold
                                and live_close > live_open
                                and live_close - pivot_low >= fast_threshold))
                        )
                        # 大實體反向 K 已跨過 MA3 時，視為急漲／急跌突破：
                        # 不等第二次掃描，平舊倉後同輪立即反手。一般小轉折仍需
                        # 兩次掃描確認，避免未收線 K 的假突破。
                        live_atr = max(float(trigger.get("atr") or 0.0), live_close * 1e-12)
                        body = abs(live_close - live_open)
                        breaks_ma3 = (
                            (position.get("side") == "LONG" and live_close < ma3_curr)
                            or (position.get("side") == "SHORT" and live_close > ma3_curr)
                        )
                        rapid_impulse_pivot = bool(
                            RAPID_PIVOT_IMMEDIATE_REVERSE_ENABLED
                            and first_reversal_pivot
                            and body >= live_atr * RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR
                            and breaks_ma3
                        )
                        if rapid_impulse_pivot:
                            rapid_reverse_side = "SHORT" if position.get("side") == "LONG" else "LONG"
                        fast_ma3_pivot = ma3_turn or first_reversal_pivot
                    live_bar_id = (
                        int(float(df["timestamp"].iloc[-1]))
                        if "timestamp" in df.columns else int(df.index[-1])
                    )
                    fast_ma3_confirmed = False
                    if fast_ma3_pivot:
                        confirmation = self._fast_pivot_confirmations.get(symbol, {})
                        if (
                            confirmation.get("bar_id") == live_bar_id
                            and confirmation.get("side") == position.get("side")
                        ):
                            confirmation["hits"] = int(confirmation.get("hits", 0)) + 1
                        else:
                            confirmation = {"bar_id": live_bar_id, "side": position.get("side"), "hits": 1}
                        self._fast_pivot_confirmations[symbol] = confirmation
                        fast_ma3_confirmed = rapid_impulse_pivot or int(confirmation["hits"]) >= 2
                    else:
                        self._fast_pivot_confirmations.pop(symbol, None)
                    trigger["fast_ma3_pivot"] = fast_ma3_pivot
                    trigger["fast_ma3_confirmed"] = fast_ma3_confirmed
                    trigger["rapid_impulse_pivot"] = rapid_impulse_pivot
                    same_bar_reversal = self._live_pivot_reversal_bar.get(symbol) == live_bar_id
                    # 一般盤中轉折只作診斷；未收線 K 可能重繪，不再先平倉造成
                    # 「平空後沒有買多」的空窗。只有大實體突破 MA3 的急速反轉
                    # 才即時平倉並沿用下方既有邏輯同輪反手。
                    live_pivot_exit = bool(
                        not CONTINUOUS_PIVOT_ONLY
                        and wave_regime != "TREND"
                        and not kc_outer_lock.get("blocked")
                        and not same_bar_reversal
                        and rapid_impulse_pivot
                    )
                    # WAIT_PRE_PIVOT 代表斜率仍不足，必須維持原倉等待已收盤峰谷。
                    pre_turn_exit = False
                    if is_cr_position:
                        # 中軌回踩、正式獲利側峰谷、MA轉向與回吐都不平倉。
                        # 不利方向 KC 外軌破軌由固定停損迴圈處理。
                        live_next_rail_flatten = False
                        trend_exhaustion_exit = False
                        two_bar_opposite_trend_exit = False
                        live_pivot_exit = False
                        opposite_pivot = False
                        outer_rail_flatten = False
                        outer_run_return_exit = False
                        opposite_candle_exit = False
                        trigger["live_next_rail_flatten"] = False
                        trigger["trend_exhaustion_exit"] = False
                        trigger["two_bar_opposite_trend_exit"] = False
                        trigger["outer_rail_flatten"] = False
                        trigger["outer_run_return_pending"] = False
                    should_auto_close = bool(
                        (not false_breakout_hold or live_next_rail_flatten or live_pivot_exit or trend_exhaustion_exit or two_bar_opposite_trend_exit or outer_rail_flatten or outer_run_return_exit or opposite_candle_exit)
                        and (
                            opposite_pivot or live_next_rail_flatten or trend_exhaustion_exit or two_bar_opposite_trend_exit or pre_turn_exit or live_pivot_exit or outer_run_return_exit or opposite_candle_exit
                            if is_cr_position
                            else (
                                (trigger.get("ma5_reversed") and ma5_exit_ready)
                                or trigger.get("is_panic_reversal")
                                or pre_turn_exit
                            )
                        )
                    )
                    pivot_exit_ready = (
                        opposite_pivot or live_next_rail_flatten or trend_exhaustion_exit or two_bar_opposite_trend_exit or pre_turn_exit or live_pivot_exit or outer_run_return_exit or opposite_candle_exit
                        if is_cr_position else bool(trigger.get("strong") or pre_turn_exit)
                    )
                    bottom_grace, bottom_age = self._bottom_entry_grace(
                        position, time.time()
                    )
                    trigger["bottom_entry_grace"] = bottom_grace
                    trigger["bottom_entry_age_sec"] = bottom_age
                    if bottom_grace:
                        self._soft_warning_since.pop(symbol, None)
                    if (
                        (ENABLE_STRONG_TRIGGER_AUTO_CLOSE or pivot_exit_ready)
                        and pivot_exit_ready
                        and (not bottom_grace or live_next_rail_flatten or outer_rail_flatten or opposite_candle_exit or two_bar_opposite_trend_exit)
                        and should_auto_close
                    ):
                        if live_next_rail_flatten:
                            close_reason = (
                                f"{exit_tf} 峰頂後大紅K盤中跌入下一軌，先平多；收盤確認再開空"
                                if position.get("side") == "LONG"
                                else f"{exit_tf} 谷底後大綠K盤中升入下一軌，先平空；收盤確認再開多"
                            )
                        elif outer_run_return_exit:
                            close_reason = (
                                f"{exit_tf} OUTER_RUN紅K收回上軌內，提前平多"
                                if position.get("side") == "LONG"
                                else f"{exit_tf} OUTER_RUN綠K收回下軌內，提前平空"
                            )
                        elif opposite_candle_exit:
                            close_reason = (
                                f"{exit_tf} 峰頂形成前第一根收盤紅K，提前平多"
                                if position.get("side") == "LONG"
                                else f"{exit_tf} 谷底形成前第一根收盤綠K，提前平空"
                            )
                        elif outer_rail_flatten:
                            close_reason = (
                                f"{exit_tf} RANGE峰頂確認，平多並立即開空"
                                if position.get("side") == "LONG"
                                else f"{exit_tf} RANGE谷底確認，平空並立即開多"
                            )
                        elif two_bar_opposite_trend_exit:
                            close_reason = (
                                f"{exit_tf}連續兩根明確反向趨勢，保護性平倉；等待完整峰谷再反手"
                            )
                        elif trigger.get("pre_peak_exit"):
                            close_reason = f"{exit_tf}強紅K跌破MA3，保護性平多"
                        elif trigger.get("pre_trough_exit"):
                            close_reason = f"{exit_tf}強綠K站上MA3，保護性平空"
                        elif trend_exhaustion_exit:
                            close_reason = (
                                f"{exit_tf}強趨勢結束，最高/最低 {trend_exit_info.get('extreme_price') or 0:.8g}，"
                                f"MA3回撤 {trend_exit_info.get('retrace_atr') or 0:.2f}ATR，兩根收盤確認"
                            )
                        elif live_pivot_exit:
                            close_reason = f"{exit_tf}局部峰頂/谷底第一根反向K，優先平倉"
                        elif trigger.get("is_panic_reversal"):
                            close_reason = f"{MA5_EXIT_TIMEFRAME}爆量K線反轉，緊急平倉"
                        else:
                            close_reason = (
                                f"{MA5_EXIT_TIMEFRAME}收線均線與結構防線同時失守"
                                if structural_strong
                                else f"{MA5_EXIT_TIMEFRAME} MA5轉彎反轉平倉"
                            )
                        self.account.log(
                            f"🚨 [出場防線偵測] {symbol} {close_reason}",
                            "DANGER"
                        )
                        curr_p = self.tickers.get(symbol) or (df['close'].iloc[-1] if not df.empty else position["entry_price"])
                        from core.config import FIXED_STOP_LOSS_PCT
                        adverse_pct = (
                            max(0.0, (float(position.get("entry_price") or curr_p) - curr_p) / float(position.get("entry_price") or curr_p))
                            if position.get("side") == "LONG"
                            else max(0.0, (curr_p - float(position.get("entry_price") or curr_p)) / float(position.get("entry_price") or curr_p))
                        )
                        hard_loss_reached = adverse_pct >= FIXED_STOP_LOSS_PCT
                        if (
                            not is_profit_locked
                            and not hard_loss_reached
                            and not trend_exhaustion_exit
                            and not two_bar_opposite_trend_exit
                            and not live_next_rail_flatten
                            and not outer_rail_flatten
                            and not outer_run_return_exit
                            and not opposite_candle_exit
                        ):
                            self.account.log(
                                f"⏸️ [未鎖利技術出場略過] {symbol} {close_reason}；"
                                f"目前逆向 {adverse_pct:.2%} < 硬停損 {FIXED_STOP_LOSS_PCT:.2%}",
                                "INFO",
                            )
                        else:
                            closed = await self.account.close_position(
                                symbol, curr_p, close_reason,
                                is_manual=(pre_turn_exit or live_next_rail_flatten or live_pivot_exit or two_bar_opposite_trend_exit or outer_rail_flatten or outer_run_return_exit or opposite_candle_exit),
                            )
                            if closed and outer_rail_flatten and not CONTINUOUS_PIVOT_ONLY:
                                target_side = range_swing_reverse_side or (
                                    "SHORT" if position.get("side") == "LONG" else "LONG"
                                )
                                available = self.account.get_available_balance()
                                if TEST_BUDGET_CAP_USDT > 0:
                                    available = min(available, TEST_BUDGET_CAP_USDT)
                                daily_blocked = False
                                daily_check = getattr(self.account, "daily_loss_limit_hit", None)
                                if callable(daily_check):
                                    daily_blocked = bool(daily_check()[0])
                                if available >= MIN_TRADE_USDT and not daily_blocked:
                                    reverse_df = self.strategy.compute_indicators(df_closed.copy())
                                    opened = await self._place_continuous_market_entry(
                                        symbol=symbol, side=target_side, df=reverse_df,
                                        live_price=curr_p, entry_type="RANGE_SWING_REVERSE",
                                        reason=(
                                            "RANGE峰頂平多並開空"
                                            if target_side == "SHORT" else "RANGE谷底平空並開多"
                                        ),
                                        score=100, timeframe=exit_tf,
                                        wave_regime="RANGE", market_mode=market_mode,
                                    )
                                    if opened:
                                        self._continuous_last_entry_bar[symbol] = (
                                            target_side, live_bar_id
                                        )
                                else:
                                    self.account.log(
                                        f"{symbol} RANGE 波段已平倉，但風控或餘額不允許反手 {target_side}",
                                        "WARNING",
                                    )
                            if closed and live_pivot_exit and not outer_rail_flatten:
                                self._live_pivot_reversal_bar[symbol] = live_bar_id
                                if rapid_impulse_pivot and rapid_reverse_side and not CONTINUOUS_TREND_ONLY and not CONTINUOUS_PIVOT_ONLY:
                                    available = self.account.get_available_balance()
                                    if TEST_BUDGET_CAP_USDT > 0:
                                        available = min(available, TEST_BUDGET_CAP_USDT)
                                    if available >= MIN_TRADE_USDT:
                                        self.account.log(
                                            f"⚡ {symbol} 1m急速反轉（實體≥{RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR:.2f}ATR且突破MA3），"
                                            f"平{position.get('side')}後立即反手{rapid_reverse_side}",
                                            "WARNING",
                                        )
                                        opened = await self._place_continuous_market_entry(
                                            symbol=symbol, side=rapid_reverse_side, df=df,
                                            live_price=curr_p,
                                            entry_type="RAPID_PIVOT_REVERSE",
                                            reason="1m 大實體突破 MA3，立即反手",
                                            score=100, timeframe=exit_tf,
                                        )
                                        if opened:
                                            self._continuous_last_entry_bar[symbol] = (
                                                rapid_reverse_side, live_bar_id
                                            )
                                    else:
                                        self.account.log(
                                            f"{symbol} 急速反轉已平倉，但可用餘額不足，未反手",
                                            "WARNING",
                                        )
                                else:
                                    self.account.log(
                                        f"{symbol} pivot closed; wait for confirmed reversal or trend resumption",
                                        "WARNING",
                                    )
                            if closed and pre_turn_exit:
                                exit_ma5 = (
                                    float(df["ma5"].iloc[-1])
                                    if "ma5" in df.columns
                                    else float(df["close"].rolling(5).mean().iloc[-1])
                                )
                                self._continuous_alignment_wait[symbol] = {
                                    "side": position.get("side"),
                                    "exit_ma5": exit_ma5,
                                    "atr": float(trigger.get("atr") or 0.0),
                                    "exited_at": time.time(),
                                }
                                self.account.log(
                                    f"⏳ {symbol} 已提前平倉，等待 MA5 改變方向再反手",
                                    "INFO",
                                )
                        self._soft_warning_since.pop(symbol, None)
                    else:
                        from core.config import ENABLE_SOFT_WARNING_TIGHTEN
                        if (
                            not is_profit_locked
                            and not bottom_grace
                            and ENABLE_SOFT_WARNING_TIGHTEN
                        ):
                            # 軟性警訊收緊止損：持續處於「✗」（ma_ok=false）但還沒
                            # 升級成「⛔」超過 SOFT_WARNING_PERSIST_SEC，把止損往
                            # 進場價方向收緊到「目前止損與進場價的中點」（只會變緊
                            # 不會變鬆），降低風險但不直接平倉，介於「完全不管」跟
                            # 「5m防線直接關倉」之間。
                            if trigger.get("ma_ok") is False:
                                since = self._soft_warning_since.setdefault(symbol, time.time())
                            already_tightened = bool(position_meta.get("soft_warning_tightened"))
                            if ENABLE_SOFT_WARNING_TIGHTEN and time.time() - since >= SOFT_WARNING_PERSIST_SEC and not already_tightened:
                                side = position.get("side")
                                current_sl = float(position.get("sl") or 0.0)
                                entry_p2 = float(position.get("entry_price") or 0.0)
                                if current_sl > 0 and entry_p2 > 0:
                                    new_sl = (current_sl + entry_p2) / 2
                                    improved = (
                                        (side == "LONG" and new_sl > current_sl)
                                        or (side == "SHORT" and new_sl < current_sl)
                                    )
                                    if improved:
                                        if DISABLE_STOP_LOSS:
                                            self.account.log(
                                                f"⏸️ [自動停損已停用] 跳過收緊止損 {symbol}",
                                                "INFO",
                                            )
                                        else:
                                            if await self.account.trail_stop_loss(
                                                symbol, new_sl, mark_profit_locked=False
                                            ):
                                                position_meta["soft_warning_tightened"] = True
                                                self.account.log(
                                                    f"⚠️ [軟性警訊收緊止損] {symbol} 持續{SOFT_WARNING_PERSIST_SEC:.0f}秒"
                                                    f"未解除✗警訊，止損從{current_sl:.6g}收緊到{new_sl:.6g}",
                                                    "WARNING",
                                                )
                        else:
                            # ma_ok恢復True，清空計時與旗標，允許下次重新觸發
                            self._soft_warning_since.pop(symbol, None)
                            if position_meta.get("soft_warning_tightened"):
                                position_meta["soft_warning_tightened"] = False
                for symbol in set(self.position_triggers) - set(self.account.positions):
                    self.position_triggers.pop(symbol, None)
                    self._soft_warning_since.pop(symbol, None)
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.account.log(f"⚠️ [平倉參考指標] 暫時失敗：{type(exc).__name__}: {exc}", "WARNING")
                await asyncio.sleep(3)

    async def _process_single_exit(self, symbol, position):
        try:
            meta = self.account.position_meta.setdefault(symbol, {})
            # 連續峰谷／MA3-MA15 波段持倉的技術型出場只能由主交易循環使用
            # 「已收盤、方向相反、MA3 位於對應 KC 外軌外」的正式峰谷確認。
            # 這裡舊有的即時 MA3 彎頭逃命路徑會把 KC 內回踩誤當平倉，造成
            # 圖表上的谷底平多／峰頂平空，因此不得再接管這類持倉。
            if self._is_continuous_wave_position(position, meta):
                return
            managed_modes = {
                'BREAKOUT', 'SUPPORT_PULLBACK', 'MOMENTUM_CROSS',
                'MA5_REVERSAL', 'MA5_BOTTOM_LIMIT', 'CURRENT_MAKER', 'PULLBACK',
            }
            entry_mode = position.get("entry_mode") or meta.get("entry_mode")
            if entry_mode not in managed_modes:
                # 針對順勢/峰谷模式，加入無條件逃命機制：若MA3在外軌外發生轉折，立即平倉保住利潤
                from core.config import CONTINUOUS_REVERSE_TIMEFRAME
                df_escape = await self.fetch_klines(symbol, timeframe=CONTINUOUS_REVERSE_TIMEFRAME, limit=10)
                if df_escape is not None and len(df_escape) >= 3:
                    df_escape = self.strategy.compute_indicators(df_escape)
                    side_esc = position["side"]
                    ma3_now = float(df_escape["ma3"].iloc[-1])
                    ma3_prev = float(df_escape["ma3"].iloc[-2])
                    kc_upper = float(df_escape["kc_upper"].iloc[-2])
                    kc_lower = float(df_escape["kc_lower"].iloc[-2])

                    if side_esc == "LONG" and ma3_now < ma3_prev and (ma3_prev > kc_upper or ma3_now > kc_upper):
                        self.account.log(f"⚠️ {symbol} 上軌外峰頂無條件逃命平多！", "WARNING")
                        await self.account.close_position(
                            symbol=symbol, current_price=float(df_escape["close"].iloc[-1]),
                            close_reason="上軌外峰頂逃命平倉", is_manual=True
                        )
                    elif side_esc == "SHORT" and ma3_now > ma3_prev and (ma3_prev < kc_lower or ma3_now < kc_lower):
                        self.account.log(f"⚠️ {symbol} 下軌外谷底無條件逃命平空！", "WARNING")
                        await self.account.close_position(
                            symbol=symbol, current_price=float(df_escape["close"].iloc[-1]),
                            close_reason="下軌外谷底逃命平倉", is_manual=True
                        )
                return
            side = position["side"]
            direction = 1 if side == "LONG" else -1
            current_price = float(
                self.tickers.get(symbol) or position.get("mark_price") or position["entry_price"]
            )

            symbol_1h = self.st_direction_1h_cache.get(symbol)
            btc_1h = int(self.btc_1h_st_direction or 0)

            # 提前載入 5m K線與指標計算，供後續 DCA 評分與 exits 共同使用
            df = await self.fetch_klines(symbol, timeframe="3m", limit=100)
            if df.empty or len(df) < 65:
                return
            computed = self.strategy.compute_indicators(df)
            bar = computed.iloc[-1]
            atr = float(bar["atr"]) if not pd.isna(bar["atr"]) else 0.0
            if atr <= 0:
                atr = float(meta.get("atr") or position.get("atr") or 0.0)
            if atr <= 0:
                return

            # ====== 【DCA 分批加倉限價單掛載與均價 SL/TP 重算】 ======
            from core.config import ENABLE_DCA_LIMIT, DCA_STAGE_DEPTHS, MIN_OPEN_SIGNAL_SCORE

            # 取得目前持倉所處的 DCA 階段，若沒有則從 meta 讀取
            dca_stage = position.get("dca_stage") or meta.get("dca_stage")

            # 如果有新加倉單成交，qty 增加時更新 stage
            last_qty = meta.get("dca_last_qty", 0.0)
            if last_qty > 0.0 and abs(position["qty"] - last_qty) > 1e-8:
                # 數量增加，代表上一階 DCA 限價單已成交！
                old_stage = dca_stage or 1
                dca_stage = old_stage + 1
                meta["dca_stage"] = dca_stage
                meta["dca_last_qty"] = position["qty"]

                # 成交後，重新對齊均價（最新進場價）來重算 SL
                dca_atr = float(position.get("atr") or meta.get("atr") or 0.0)
                if dca_atr <= 0:
                    dca_atr = atr
                if dca_atr > 0:
                    from core.config import BREAKOUT_HARD_STOP_ATR_MULT, MIN_SL_DISTANCE_PCT
                    if side == "LONG":
                        new_sl = position["entry_price"] - BREAKOUT_HARD_STOP_ATR_MULT * dca_atr
                        new_sl = min(new_sl, position["entry_price"] * (1.0 - MIN_SL_DISTANCE_PCT))
                    else:
                        new_sl = position["entry_price"] + BREAKOUT_HARD_STOP_ATR_MULT * dca_atr
                        new_sl = max(new_sl, position["entry_price"] * (1.0 + MIN_SL_DISTANCE_PCT))

                    # 強制將新止損對齊到交易所與本地
                    meta["sl"] = new_sl
                    if DISABLE_STOP_LOSS:
                        self.account.log(f"⏸️ [自動停損已停用] 跳過 DCA 重設止損 {symbol} -> {new_sl}", "INFO")
                    else:
                        await self.account.trail_stop_loss(symbol, new_sl, mark_profit_locked=False)
                    self.account.log(f"🔄 [DCA 均價對齊] {symbol} 加倉成交 (階 {dca_stage})，新均價 {position['entry_price']:.8g}，止損重設為 {new_sl:.8g}", "SUCCESS")
            else:
                meta["dca_last_qty"] = position["qty"]

            # 1h 級別趨勢防接飛刀 (防瀑布) 過濾：若大趨勢已翻向，停止加倉並撤回掛單
            waterfall_crash = (side == "LONG" and symbol_1h == -1) or (side == "SHORT" and symbol_1h == 1)
            if waterfall_crash:
                if symbol in self.account.pending_limit_orders:
                    po = self.account.pending_limit_orders[symbol]
                    if "dca_stage" in po.get("entry_context", {}):
                        await self.account.cancel_pending_limit(symbol, f"大週期 1h 趨勢反向({symbol_1h})，DCA撤單避險")
            else:
                # DCA 掛單超時管理：掛單時間過長自動撤回，釋放資金佔用
                if symbol in self.account.pending_limit_orders:
                    po = self.account.pending_limit_orders[symbol]
                    if "dca_stage" in po.get("entry_context", {}):
                        from core.config import DCA_LIMIT_TIMEOUT_SEC
                        placed_at = po.get("placed_at") or time.time()
                        if time.time() - placed_at >= DCA_LIMIT_TIMEOUT_SEC:
                            await self.account.cancel_pending_limit(symbol, "DCA 加倉限價單超時撤單")

            if ENABLE_DCA_LIMIT and dca_stage in (1, 2) and not waterfall_crash:
                # 如果此幣種目前沒有在掛限價加倉單，則進行最新評分檢測
                if symbol not in self.account.pending_limit_orders:
                    score_res = self.strategy.evaluate_structured_entry(
                        computed,
                        ema_50_1h=self.ema_50_1h_cache.get(symbol),
                        st_direction_1h=symbol_1h,
                        btc_st_direction_1h=btc_1h,
                        symbol=symbol,
                        indicators_precomputed=True,
                        is_dca_check=True,
                    )
                    current_score = score_res.get("score") or 0

                    # 分數有維持（符合最低開倉門檻），才允許分批掛出下一階
                    if current_score >= MIN_OPEN_SIGNAL_SCORE:
                        dca_base_price = float(position.get("dca_base_price") or meta.get("dca_base_price") or position["entry_price"])
                        dca_original_amount = float(position.get("dca_original_amount") or meta.get("dca_original_amount") or (position["entry_price"] * position["qty"]))

                        next_stage = int(dca_stage) + 1
                        if next_stage - 2 < len(DCA_STAGE_DEPTHS):
                            depth_pct = DCA_STAGE_DEPTHS[next_stage - 2]
                            next_price = dca_base_price * (1 - depth_pct) if side == "LONG" else dca_base_price * (1 + depth_pct)
                            next_amount = dca_original_amount / 3.0

                            dca_context = {
                                "dca_stage": next_stage,
                                "dca_base_price": dca_base_price,
                                "dca_original_amount": dca_original_amount,
                                "entry_mode": entry_mode,
                                "initial_sl": float(position.get("sl") or meta.get("sl") or 0.0),
                            }
                            # 使用與目前持倉相同的槓桿與 SL
                            success = await self.account.place_limit_entry(
                                symbol=symbol,
                                side=side,
                                target_price=next_price,
                                amount_usdt=next_amount,
                                sl=float(position.get("sl") or meta.get("sl") or 0.0),
                                tp=float(position.get("tp") or meta.get("tp") or 0.0),
                                reason=f"DCA 加倉 (階 {next_stage})",
                                atr=atr,
                                leverage=int(position.get("leverage") or 1),
                                post_only=True,
                                entry_context=dca_context
                            )
                            if success:
                                self.account.log(f"📥 [DCA 自動加倉掛單] {symbol} {side} 成功掛出第 {next_stage} 階加倉委託 @ {next_price:.8g}，當前評分維持 {current_score} 分", "INFO")
                    else:
                        if getattr(self, "_last_dca_skip_log_at", {}).get(symbol, 0) < time.time() - 300:
                            self._last_dca_skip_log_at = getattr(self, "_last_dca_skip_log_at", {})
                            self._last_dca_skip_log_at[symbol] = time.time()
                            self.account.log(f"⚠️ [DCA 評分不足] {symbol} 最新分數 {current_score} 低於 {MIN_OPEN_SIGNAL_SCORE} 分，暫不掛載下一階加倉單", "WARNING")

            # 根據使用者偏好：已停用 1h SuperTrend 翻向強制全平防線，給持倉死等利潤彈回或吃滿硬止損的空間。
            pass



            # --- K 線反轉逃頂機制 ---
            # 獲利狀態下，若出現明顯的反轉 K 線 (多單遇到流星線，空單遇到錘頭線)，提早獲利了結。
            from core.indicators import analyze_candle_pattern
            candle_pattern = analyze_candle_pattern(bar)

            unrealized_pnl_pct = (current_price - position["entry_price"]) / position["entry_price"] if side == "LONG" else (position["entry_price"] - current_price) / position["entry_price"]

            # --- 外軌峰谷平倉 (Max Profit Peak/Valley Exit) ---
            # 只要有利潤，且摸到對側 KC 軌道，等待 MA3 形成反向極值 (峰頂/谷底) 才平倉，拿滿最大利潤！
            if unrealized_pnl_pct > 0.0 and not bool(
                position.get("outer_run_active") or meta.get("outer_run_active")
            ):
                # 使用 1m 級別來判斷精準的軌道與 MA3 彎頭
                df_1m = await self.fetch_klines(symbol, timeframe="1m", limit=20, keep_live=True)
                if not df_1m.empty and len(df_1m) >= 5:
                    df_1m = self.strategy.compute_indicators(df_1m)
                    curr_close = float(df_1m['close'].iloc[-1])
                    kc_upper = float(df_1m['kc_upper'].iloc[-1]) if 'kc_upper' in df_1m.columns else curr_close
                    kc_lower = float(df_1m['kc_lower'].iloc[-1]) if 'kc_lower' in df_1m.columns else curr_close

                    is_touching = (side == "LONG" and current_price >= kc_upper) or (side == "SHORT" and current_price <= kc_lower)

                    if is_touching or meta.get("band_touched"):
                        meta["band_touched"] = True

                        # 已經摸到對面軌道！現在只等 MA3 彎頭 (check_simple_ma5_exit)
                        from core.strategy import check_simple_ma5_exit
                        exit_signal = check_simple_ma5_exit(df_1m, position)

                        if exit_signal.get("close"):
                            self.account.log(f"🎯 [外軌峰谷平倉] {symbol} {side} 單於軌道外出現 {exit_signal.get('reason')}，鎖定最大利潤 {unrealized_pnl_pct:.2%}，平倉！", "SUCCESS")
                            await self.account.close_position(symbol, current_price, f"外軌峰谷 ({exit_signal.get('reason')})")

                            # 觸發極速反手
                            target_side = "SHORT" if side == "LONG" else "LONG"
                            self._kc_reversal_wait[symbol] = {
                                "from_side": side,
                                "target_side": target_side,
                                "pivot_type": "BAND_PEAK_VALLEY",
                                "created_at": time.time(),
                                "middle_reached": True,
                            }
                            return
            # ----------------------------------------
            # (已停用) 提早逃頂邏輯：既然我們已經有了「觸軌極速反手」，就直接抱到上/下軌，
            # 不再因為中途出現流星線或錘頭線而提早下車，直接拿最大的利潤！

            if entry_mode == "BREAKOUT":
                # 修正2：kc_failed 改為需要連續 BREAKOUT_KC_FAIL_CONFIRM_BARS 根
                # 已收盤5m K棒實體（開盤+收盤）都在EMA20不利側才觸發關倉。
                # 突破後第一根正常回踩K棒不會觸發，需要連續失守才認定趨勢翻轉。
                # 第二道防線保留：影線觸及反向KC外軌仍立即觸發（極端反轉訊號）。
                past_bars = computed.iloc[-BREAKOUT_KC_FAIL_CONFIRM_BARS - 1:-1]
                if side == "LONG":
                    # 每根K棒的開盤和收盤都在EMA20以下才算失守1根
                    bar_failed_count = int(
                        ((past_bars['open'] < past_bars['ema_20']) & (past_bars['close'] < past_bars['ema_20'])).sum()
                    )
                    shadow_breach = float(bar["low"]) <= float(bar["kc_lower"])
                    kc_failed = bar_failed_count >= BREAKOUT_KC_FAIL_CONFIRM_BARS or shadow_breach
                else:
                    bar_failed_count = int(
                        ((past_bars['open'] > past_bars['ema_20']) & (past_bars['close'] > past_bars['ema_20'])).sum()
                    )
                    shadow_breach = float(bar["high"]) >= float(bar["kc_upper"])
                    kc_failed = bar_failed_count >= BREAKOUT_KC_FAIL_CONFIRM_BARS or shadow_breach

                if kc_failed:
                    fail_reason = (
                        f"影線觸及反向KC外軌" if shadow_breach
                        else f"連續{bar_failed_count}根5m實體收在EMA20不利側"
                    )
                    self.account.log(
                        f"🟡 [KC失敗關倉] {symbol} {side} {fail_reason}",
                        "WARNING"
                    )
                    if DISABLE_STOP_LOSS:
                        self.account.log(f"⏸️ [自動停損已停用] 跳過 KC 失敗自動平倉 {symbol} ({fail_reason})", "INFO")
                    else:
                        await self.account.close_position(
                            symbol, current_price, f"KC失敗({fail_reason})"
                        )
                    return


            entry_price = float(position["entry_price"])
            initial_risk = float(
                position.get("initial_risk") or meta.get("initial_risk")
                or abs(entry_price - float(position.get("sl") or meta.get("sl") or entry_price))
            )
            if initial_risk <= 0:
                return
            favorable_price = current_price
            peak_key = "structured_peak_price"
            previous_peak = float(meta.get(peak_key) or entry_price)
            peak = max(previous_peak, favorable_price) if side == "LONG" else min(previous_peak, favorable_price)
            meta[peak_key] = peak

            current_sl = float(position.get("sl") or meta.get("sl") or 0.0)
            # 只有 BREAKOUT 突破策略使用 BREAKOUT_TRAILING_ATR_MULT 移動止損
            if entry_mode == "BREAKOUT":
                trail_sl = (
                    peak - BREAKOUT_TRAILING_ATR_MULT * atr
                    if side == "LONG" else peak + BREAKOUT_TRAILING_ATR_MULT * atr
                )
                cost_pct = 2 * TAKER_FEE_RATE + SLIPPAGE_PCT
                if side == "LONG" and trail_sl > entry_price:
                    trail_sl = max(trail_sl, entry_price * (1.0 + cost_pct))
                elif side == "SHORT" and trail_sl < entry_price:
                    trail_sl = min(trail_sl, entry_price * (1.0 - cost_pct))
                improves = False
                if side == "LONG":
                    if trail_sl >= entry_price:
                        improves = (trail_sl > current_sl)
                else:
                    if trail_sl <= entry_price:
                        improves = (current_sl <= 0 or trail_sl < current_sl)
                if improves:
                    locked = trail_sl >= entry_price if side == "LONG" else trail_sl <= entry_price
                    if DISABLE_STOP_LOSS:
                        self.account.log(f"⏸️ [自動停損已停用] 跳過移動止損 {symbol} -> {trail_sl}", "INFO")
                    else:
                        await self.account.trail_stop_loss(
                            symbol, trail_sl, mark_profit_locked=locked
                        )

            favorable_move = (
                current_price - entry_price if side == "LONG"
                else entry_price - current_price
            )
            rr = favorable_move / initial_risk
            if ENABLE_BREAKOUT_PARTIAL_TAKE_PROFIT and rr >= BREAKOUT_RR1_TARGET and not meta.get("rr_1_5_done"):
                if await self.account.partial_close_position(
                    symbol, current_price, f"達 {BREAKOUT_RR1_TARGET:.1f}R，分批止盈",
                    fraction=BREAKOUT_RR_CLOSE_FRACTION,
                ):
                    meta = self.account.position_meta.setdefault(symbol, meta)
                    meta["rr_1_5_done"] = True
                    self.account.save_state()
                    return
            if ENABLE_BREAKOUT_PARTIAL_TAKE_PROFIT and rr >= BREAKOUT_RR2_TARGET and not meta.get("rr_2_5_done"):
                if await self.account.partial_close_position(
                    symbol, current_price, f"達 {BREAKOUT_RR2_TARGET:.1f}R，分批止盈",
                    fraction=BREAKOUT_RR_CLOSE_FRACTION,
                ):
                    meta = self.account.position_meta.setdefault(symbol, meta)
                    meta["rr_2_5_done"] = True
                    self.account.save_state()
        except Exception as e:
            self.account.log(f'⚠️ [{symbol}] 出場監控例外: {e}', 'WARNING')

    async def _run_structured_exits(self):
        """Manage structured positions with KC failure, ATR trail, RR scales, and 1h flips."""
        managed_modes = {
            "BREAKOUT", "SUPPORT_PULLBACK", "MOMENTUM_CROSS",
            "MA5_REVERSAL", "MA5_BOTTOM_LIMIT", "CURRENT_MAKER", "PULLBACK",
        }
        while self.is_running:
            try:
                tasks = [
                    self._process_single_exit(symbol, position)
                    for symbol, position in list(self.account.positions.items())
                ]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(STRUCTURED_EXIT_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.account.log(
                    f"⚠️ [結構出場管理] 暫時失敗：{type(exc).__name__}: {exc}", "WARNING"
                )
                await asyncio.sleep(STRUCTURED_EXIT_INTERVAL_SEC)

    async def _run_trend_follow_exits(self):
        """背景任務：大週期 (15m) EMA20 收線確認趨勢移動止損與分批止盈。"""
        while self.is_running:
            try:
                if CONTINUOUS_PIVOT_ONLY:
                    await asyncio.sleep(30)
                    continue
                if ENABLE_TREND_FOLLOW_EXIT or any(
                    str(pos.get("market_mode") or self.account.position_meta.get(sym, {}).get("market_mode") or "").upper()
                    in ("BULL", "BEAR")
                    for sym, pos in self.account.positions.items()
                ):
                    for symbol, position in list(self.account.positions.items()):
                        position_meta = self.account.position_meta.get(symbol, {})
                        if self._is_continuous_wave_position(position, position_meta):
                            continue
                        macro_trend_mode = str(
                            position.get("market_mode") or position_meta.get("market_mode") or ""
                        ).upper() in ("BULL", "BEAR")
                        if not ENABLE_TREND_FOLLOW_EXIT and not macro_trend_mode:
                            continue

                        # 0a. 若已由 Binance 原生毫秒級 Trailing Stop 接管（Tier 2+），
                        # 屏蔽微觀趨勢平倉，放手博取大波段。Tier 1 只是本地移到保本價
                        # （仍是靜態單，不是交易所主動追蹤），不算「已接管」，這裡不能
                        # 跳過，否則 Tier 1 到 Tier 2 之間的空窗期會完全沒有 15m 趨勢
                        # 止損防護。
                        if position_meta.get("native_trailing_tier", 0) >= 2:
                            continue

                        # 0b. 1H 大週期趨勢過濾器：大級別方向與持倉不一致時，跳過 15m EMA20 止損以防橫盤雙巴
                        st_dir_1h = self.st_direction_1h_cache.get(symbol)
                        if st_dir_1h is not None:
                            side = position["side"]
                            is_aligned = (side == "LONG" and st_dir_1h == 1) or (side == "SHORT" and st_dir_1h == -1)
                            if not is_aligned:
                                continue



                        # 底點預掛在轉彎前承接，前30分鐘的15m EMA逆向通常仍是
                        # 原回撤的一部分；讓固定交易所SL控風險，不用軟退出砍掉。
                        bottom_grace, _bottom_age = self._bottom_entry_grace(
                            position, time.time()
                        )
                        if bottom_grace:
                            continue

                        # 2. 檢查大週期 (15m) EMA20 收線與 ATR 緩衝帶跌破/突破 (連續兩根 K 棒收線確認)
                        df = await self.fetch_klines(symbol, timeframe="15m", limit=50)
                        if df.empty or len(df) < 20:
                            continue

                        # 計算 15m ATR 肯特納帶狀緩衝
                        high_low = df['high'] - df['low']
                        high_cp = (df['high'] - df['close'].shift()).abs()
                        low_cp = (df['low'] - df['close'].shift()).abs()
                        df['tr'] = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
                        df['atr'] = df['tr'].rolling(window=14).mean()
                        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()

                        # 取最後兩根已收盤的 K 棒
                        last_bar = df.iloc[-1]
                        prev_bar = df.iloc[-2]

                        close_p1 = float(last_bar['close'])
                        ema20_val1 = float(last_bar['ema_20'])
                        atr_val1 = float(last_bar['atr']) if not pd.isna(last_bar['atr']) else close_p1 * 0.015
                        buffer1 = max(close_p1 * 0.003, 0.5 * atr_val1)

                        close_p2 = float(prev_bar['close'])
                        ema20_val2 = float(prev_bar['ema_20'])
                        atr_val2 = float(prev_bar['atr']) if not pd.isna(prev_bar['atr']) else close_p2 * 0.015
                        buffer2 = max(close_p2 * 0.003, 0.5 * atr_val2)

                        side = position["side"]
                        curr_p = self.tickers.get(symbol) or close_p1

                        if side == "LONG":
                            if close_p1 < (ema20_val1 - buffer1) and close_p2 < (ema20_val2 - buffer2):
                                self.account.log(
                                    f"📉 [EMA20趨勢止損] {symbol} 連續兩根 15m 收線跌破 EMA20 緩衝帶 (收盤={close_p2:.6g}/{close_p1:.6g}, 均線={ema20_val2:.6g}/{ema20_val1:.6g}, 緩衝={buffer2:.6g}/{buffer1:.6g})，執行平倉",
                                    "WARNING"
                                )
                                if DISABLE_STOP_LOSS:
                                    self.account.log(f"⏸️ [自動停損已停用] 跳過 15m EMA20 自動平倉 {symbol}", "INFO")
                                else:
                                    await self.account.close_position(symbol, curr_p, "15m連續兩根收線實體跌破EMA20緩衝")
                        elif side == "SHORT":
                            if close_p1 > (ema20_val1 + buffer1) and close_p2 > (ema20_val2 + buffer2):
                                self.account.log(
                                    f"📈 [EMA20趨勢止損] {symbol} 連續兩根 15m 收線突破 EMA20 緩衝帶 (收盤={close_p2:.6g}/{close_p1:.6g}, 均線={ema20_val2:.6g}/{ema20_val1:.6g}, 緩衝={buffer2:.6g}/{buffer1:.6g})，執行平倉",
                                    "WARNING"
                                )
                                if DISABLE_STOP_LOSS:
                                    self.account.log(f"⏸️ [自動停損已停用] 跳過 15m EMA20 自動平倉 {symbol}", "INFO")
                                else:
                                    await self.account.close_position(symbol, curr_p, "15m連續兩根收線實體突破EMA20緩衝")

                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.account.log(f"⚠️ [大週期趨勢止損] 偵測失敗：{type(exc).__name__}: {exc}", "WARNING")
                await asyncio.sleep(30)

    async def _run_trailing_sl_loop(self):
        """背景任務（雙模式）：

        USE_NATIVE_TRAILING_STOP = True（預設，實盤）：
          此 loop 退化為「孤兒保護監控」。
          Trailing Stop 由 Binance 伺服器端毫秒級即時追蹤（TRAILING_STOP_MARKET），
          機器人不再主動撤單重掛，此 loop 只做安全網：
          若偵測到有持倉但既無本地 SL 紀錄、又沒有 native_trailing_tier（代表
          初始保護單可能未建立），記錄警告讓 _create_orphan_protection 接手。

        USE_NATIVE_TRAILING_STOP = False（Testnet / fallback）：
          維持原本每 60 秒 ATR 倍數輪詢移動止損邏輯，適合 Testnet 環境。
          trail_dist = TRAILING_SL_ATR_MULT × ATR（只往有利方向移動）
        """
        while self.is_running:
            try:
                if USE_NATIVE_TRAILING_STOP:
                    # 原生模式：僅監控異常孤兒情況（有倉無任何保護）
                    if ENABLE_TRAILING_SL:
                        for symbol, position in list(self.account.positions.items()):
                            meta = self.account.position_meta.get(symbol, {})
                            has_local_sl = meta.get("sl", 0.0) > 0
                            has_native_trailing = meta.get("native_trailing_tier", 0) > 0
                            if not has_local_sl and not has_native_trailing:
                                self.account.log(
                                    f"⚠️ [孤兒監控] {symbol} 有持倉但無任何保護單（SL=0, native_tier=0），"
                                    f"等待 _create_orphan_protection 補建",
                                    "WARNING",
                                )
                else:
                    # Fallback 模式：每 60 秒 ATR 倍數輪詢移動止損
                    if ENABLE_TRAILING_SL:
                        for symbol, position in list(self.account.positions.items()):
                            meta = self.account.position_meta.get(symbol, {})
                            curr_p = self.tickers.get(symbol)
                            if not curr_p:
                                continue
                            current_sl = meta.get("sl", 0.0)
                            if not current_sl:
                                continue
                            # The fixed profit-lock ladder is the only mechanism allowed
                            # to tighten a stop before a position has earned its first
                            # protection step.  A fallback ATR trail must never turn a
                            # normal early pullback into a premature stop-out.
                            peak_pnl = float(
                                meta.get("highest_pnl_pct")
                                or position.get("peak_pnl_pct")
                                or 0.0
                            )
                            if peak_pnl + 1e-12 < SL_ONLY_AFTER_PEAK_PCT:
                                continue
                            if not bool(
                                position.get("outer_run_active")
                                or meta.get("outer_run_active")
                            ):
                                continue
                            atr_value = self._resolve_trailing_atr(
                                symbol, position, meta, curr_p,
                            )
                            is_range_mode = (meta.get("market_mode") == "RANGE" or position.get("market_mode") == "RANGE")
                            multiplier = TRAILING_SL_ATR_MULT * (0.5 if is_range_mode else 1.0)
                            trail_dist = multiplier * atr_value
                            side = position["side"]
                            if side == "LONG":
                                new_sl = curr_p - trail_dist
                                if new_sl > current_sl:
                                    if DISABLE_STOP_LOSS:
                                        self.account.log(f"⏸️ [自動停損已停用] 跳過移動止損 (loop) {symbol} -> {new_sl}", "INFO")
                                    else:
                                        await self.account.trail_stop_loss(symbol, new_sl)
                            elif side == "SHORT":
                                new_sl = curr_p + trail_dist
                                if new_sl < current_sl:
                                    if DISABLE_STOP_LOSS:
                                        self.account.log(f"⏸️ [自動停損已停用] 跳過移動止損 (loop) {symbol} -> {new_sl}", "INFO")
                                    else:
                                        await self.account.trail_stop_loss(symbol, new_sl)
                await asyncio.sleep(60)  # 每 1 分鐘執行一次
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.account.log(f"⚠️ [移動止損Loop] 偵測失敗：{type(exc).__name__}: {exc}", "WARNING")
                await asyncio.sleep(60)




    async def fetch_klines(self, symbol: str, timeframe: str = "3m", limit: int = 100, keep_live: bool = False) -> pd.DataFrame:
        try:
            ohlcv = await asyncio.wait_for(
                self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit),
                timeout=12.0,
            )
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            # 丟棄還沒收盤的最後一根 K 棒，只在這個共用入口做一次，
            # evaluate_signal/confirm_pullback_entry 等下游邏輯用 df.iloc[-1]
            # 時就天然拿到「最後一根已收盤」的資料，不用逐處修改。
            if keep_live:
                return df
            return drop_unclosed_candle(df, timeframe)
        except Exception as e:
            print(f"fetch_klines ERROR: {e}")
            return pd.DataFrame()

    async def update_market_prices(self):
        try:
            # 牌面外的舊持倉仍必須取得報價，才能正常執行停損／停利；
            # 只有 DEFAULT_SYMBOLS 會進入下方的新開倉掃描。
            monitored_symbols = list(dict.fromkeys([
                *DEFAULT_SYMBOLS,
                *self.account.positions.keys(),
            ]))
            tickers = await self.exchange.fetch_tickers(monitored_symbols)
            for sym, t in tickers.items():
                if 'last' in t and t['last'] is not None:
                    price = float(t['last'])
                    # 統一存成 "SYMBOL/USDT" 格式（去掉 :USDT 後綴）
                    clean_sym = sym.replace(':USDT', '') if sym.endswith(':USDT') else sym
                    self.tickers[clean_sym] = price
                    self.tickers[sym] = price  # 同時保留原格式作為備援
                if 'quoteVolume' in t and t['quoteVolume'] is not None:
                    clean_sym = sym.replace(':USDT', '') if sym.endswith(':USDT') else sym
                    self.ticker_volumes[clean_sym] = float(t['quoteVolume'])
                    self.ticker_volumes[sym] = float(t['quoteVolume'])
            self.last_ticker_success_ts = time.time()
        except Exception as e:
            # 原本這裡整個吞掉例外，抓價失敗時 self.tickers 會停在上一次的
            # 舊報價，止損/止利判斷、訊號評分全部悄悄用過期價格繼續跑，
            # 不會有任何紀錄。改成量測「已經幾秒沒更新」並每 30 秒記一次
            # WARNING，讓抓價持續失敗這件事至少看得到，不是無聲無息。
            now = time.time()
            stale_sec = now - self.last_ticker_success_ts
            if now - self._last_stale_ticker_log >= 30:
                self._last_stale_ticker_log = now
                self.account.log(
                    f"⚠️ 抓取即時報價失敗（{type(e).__name__}: {e}），"
                    f"報價已 {stale_sec:.0f} 秒未更新",
                    "WARNING",
                )

    async def _ticker_loop(self):
        """使用 WebSocket (ccxt.pro) 接收真正的毫秒級即時報價串流"""
        while self.is_running:
            try:
                monitored_symbols = list(dict.fromkeys([
                    *DEFAULT_SYMBOLS,
                    *self.account.positions.keys(),
                ]))

                # 若無監控幣種，短暫等待
                if not monitored_symbols:
                    await asyncio.sleep(1)
                    continue

                # watch_tickers 會一直等待直到有新的 WebSocket 封包抵達
                tickers = await self.ws_exchange.watch_tickers(monitored_symbols)

                for sym, t in tickers.items():
                    if 'last' in t and t['last'] is not None:
                        price = float(t['last'])
                        clean_sym = sym.replace(':USDT', '') if sym.endswith(':USDT') else sym
                        self.tickers[clean_sym] = price
                        self.tickers[sym] = price
                    if 'quoteVolume' in t and t['quoteVolume'] is not None:
                        clean_sym = sym.replace(':USDT', '') if sym.endswith(':USDT') else sym
                        self.ticker_volumes[clean_sym] = float(t['quoteVolume'])
                        self.ticker_volumes[sym] = float(t['quoteVolume'])

                self.last_ticker_success_ts = time.time()

            except asyncio.CancelledError:
                break
            except Exception as e:
                # 遇到錯誤時退回使用 REST polling (update_market_prices) 做為備援
                # 並等待一下再重新嘗試連線 WS
                self.account.log(f"⚠️ [WebSocket Ticker Loop] 錯誤: {e}，暫時退回 REST 抓取...", "WARNING")
                try:
                    await self.update_market_prices()
                except Exception as rest_e:
                    self.account.log(f"⚠️ [REST Fallback] 錯誤: {rest_e}", "WARNING")
                await asyncio.sleep(2)

    async def update_1h_trend_cache(self):
        """10 分鐘才抓取一次 1h 大週期數據，避免頻繁調用 API Rate Limit"""
        now = time.time()
        if now - self.last_1h_cache_time < 600 and self.ema_50_1h_cache:
            return

        monitored_symbols = list(dict.fromkeys([
            *DEFAULT_SYMBOLS,
            *self.account.positions.keys(),
        ]))

        # 確保 BTC/USDT 一定在監控列表裡（守門員需要它）
        btc_symbol = "BTC/USDT"
        all_symbols = list(dict.fromkeys([btc_symbol, *monitored_symbols]))

        for symbol in all_symbols:
            df_1h = await self.fetch_klines(symbol, timeframe="1h", limit=150)
            if not df_1h.empty and len(df_1h) >= 30:
                ema_val = df_1h['close'].ewm(span=min(len(df_1h), TREND_FILTER_EMA_PERIOD), adjust=False).mean().iloc[-1]
                self.ema_50_1h_cache[symbol] = float(ema_val)
                # 計算 1h 指標（SuperTrend + ADX）
                computed_1h = self.strategy.compute_indicators(df_1h)

                # 個幣 1h SuperTrend 方向快取
                st_dir_1h = int(computed_1h['st_direction'].iloc[-1])
                self.st_direction_1h_cache[symbol] = st_dir_1h

                # BTC 守門員：記錄方向 + 翻轉後幾根 K棒
                if symbol == btc_symbol:
                    from core.indicators import bars_since_supertrend_flip
                    self.btc_1h_st_direction = st_dir_1h
                    self.btc_1h_st_flip_age = int(bars_since_supertrend_flip(computed_1h['st_direction']))

                # ADX 衰退快取
                adx_1h = computed_1h['adx'].iloc[-1]
                lookback_idx = len(computed_1h) - 1 - ADX_DECLINE_LOOKBACK_BARS_1H
                adx_1h_prior = computed_1h['adx'].iloc[lookback_idx] if lookback_idx >= 0 else float('nan')
                if not pd.isna(adx_1h) and not pd.isna(adx_1h_prior):
                    self.adx_1h_declining_cache[symbol] = bool(
                        adx_1h < ADX_QUALITY_MIN and adx_1h < adx_1h_prior
                    )
            await asyncio.sleep(0.1)
        self.last_1h_cache_time = now

    @staticmethod
    def _quality_bonus(reason: str) -> int:
        match = re.search(r"Quality\+(\d+)", reason or "")
        return int(match.group(1)) if match else 0

    @staticmethod
    def _format_pullback_order_log(symbol: str, candidate: dict, target: float) -> str:
        return (
            f"📝 [回踩掛單] {symbol} {candidate['side']} "
            f"原始{candidate['score']}分 → 回踩確認"
            f"{candidate['pullback_confirmation_score']}分，掛單 @ {target:.8g}"
        )

    @staticmethod
    def _pullback_reversal_confirmed(candidate: dict, candles_1m: pd.DataFrame) -> bool:
        """觸價後必須等包含觸價時刻的 1m K 棒真正收盤，且收回目標外側。"""
        if candles_1m is None or candles_1m.empty or len(candles_1m) < 8:
            return False
        candle = candles_1m.iloc[-1]
        close_time_ms = float(candle["timestamp"]) + 60_000
        if close_time_ms <= float(candidate.get("touched_at", 0.0)) * 1000:
            return False

        # 1m MA5 拐頭向上/向下確認
        closes = candles_1m["close"].astype(float)
        ma5 = closes.rolling(window=5).mean()
        if pd.isna(ma5.iloc[-1]) or pd.isna(ma5.iloc[-2]):
            return False
        ma5_curr = ma5.iloc[-1]
        ma5_prev = ma5.iloc[-2]

        target = float(candidate["target_price"])
        atr = max(float(candidate.get("atr") or 0.0), target * 1e-6)
        reclaim = atr * PULLBACK_RECLAIM_MIN_ATR
        open_price = float(candle["open"])
        close_price = float(candle["close"])
        if candidate["side"] == "LONG":
            return bool(
                close_price > open_price
                and close_price >= target + reclaim
                and ma5_curr > ma5_prev
            )
        return bool(
            close_price < open_price
            and close_price <= target - reclaim
            and ma5_curr < ma5_prev
        )

    def _fresh_pullback_target(self, df: pd.DataFrame, side: str, score: int) -> tuple[float, float]:
        computed = self.strategy.compute_indicators(df)
        curr = computed.iloc[-1]
        atr = float(curr["atr"])
        ema_20 = float(curr["ema_20"])
        kc_edge = float(curr["kc_upper"] if side == "LONG" else curr["kc_lower"])
        target, _distance, room_ok = compute_pullback_target(
            kc_edge, ema_20, atr, side, score
        )
        if not room_ok:
            return None, atr
        return target, atr

    def _record_pullback_outcome(self, key: str) -> None:
        stats = getattr(self.account, "pullback_outcome_stats", None)
        if stats is None:
            stats = {}
            self.account.pullback_outcome_stats = stats
        stats[key] = int(stats.get(key, 0)) + 1

    @staticmethod
    def _classify_pullback_drop(reason: str, candidate: dict) -> str:
        if "等待回踩/反轉確認逾時" in reason:
            return "reversal_timeout" if candidate.get("touched_at") else "touch_timeout"
        if "回調總分不足" in reason:
            return "score_low"
        if "品質不足" in reason or "Quality_Too_Low" in reason:
            return "quality_low"
        if "目標漂移" in reason:
            return "target_drift"
        if "錯側" in reason:
            return "reversal_wrong_side"
        if "槽位" in reason:
            return "slot_full"
        if "保證金" in reason:
            return "insufficient_balance"
        if "熔斷" in reason:
            return "daily_halt"
        if "停止新倉" in reason or "移出牌面" in reason:
            return "symbol_disabled"
        if "條件已變差" in reason:
            return "condition_changed"
        return "other_cancel"

    def _drop_pullback_candidate(
        self, symbol: str, reason: str, now: float, cooldown: bool = True
    ) -> None:
        candidate = self.pending_pullback_candidates.pop(symbol, None)
        if not candidate:
            return
        if cooldown:
            self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
        self._record_pullback_outcome(self._classify_pullback_drop(reason, candidate))
        self.account.log(f"↩️ [回踩候選取消] {symbol}：{reason}", "INFO")

    def _expired_pullback_still_active(
        self, symbol: str, side: str, price: float, kc_upper: float, kc_lower: float
    ) -> bool:
        expired_side = self._expired_pullback_sides.get(symbol)
        if expired_side is None:
            return False
        if expired_side != side:
            self._expired_pullback_sides.pop(symbol, None)
            return False
        still_outside_kc = price >= kc_upper if side == "LONG" else price <= kc_lower
        if still_outside_kc:
            return True
        self._expired_pullback_sides.pop(symbol, None)
        return False

    def _admit_pullback_candidates(
        self, signals: list, available_balance: float, now: float
    ) -> None:
        """把舊候選與本輪新訊號一起排序，只保留分數/品質最佳的可用槽位。"""
        pool = {
            symbol: info
            for symbol, info in self.pending_pullback_candidates.items()
            if symbol not in self.account.positions
            and symbol not in self.account.pending_limit_orders
            and symbol in DEFAULT_SYMBOLS
            and symbol not in ENTRY_DISABLED_SYMBOLS
            and now - info["created_at"] <= PULLBACK_TIMEOUT_MINUTES * 60
        }
        for score, symbol, sig, _price, real_atr in signals:
            if symbol in pool or self._pullback_retry_after.get(symbol, 0.0) > now:
                continue
            # ✅ 修正：結合「收盤確認」與「限價回踩」
            # 即使分數達到 90 分以上，也強制使用 PULLBACK 模式。
            # 這會迫使系統必須等 5m/1m 收盤反轉確認，並以限價單掛在回踩點，避免市價追高吃針。
            entry_mode = "PULLBACK"
            pullback_depth = get_pullback_target_depth(score)
            target_price = float(_price if entry_mode == "CURRENT_MAKER" else sig["target_zone"])
            pullback_distance_atr = float(sig.get("pullback_distance_atr") or 0.0)
            if entry_mode != "CURRENT_MAKER" and sig.get("ema_20") is not None:
                ema_20 = float(sig["ema_20"])
                kc_edge = float(sig["kc_upper"] if sig["side"] == "LONG" else sig["kc_lower"])
                computed_target, computed_distance, room_ok = compute_pullback_target(
                    kc_edge, ema_20, float(sig.get("atr") or real_atr), sig["side"], score
                )
                if not room_ok:
                    continue
                target_price = computed_target
                pullback_distance_atr = computed_distance / max(float(sig.get("atr") or real_atr), 1e-12)
            wallet_balance_fn = getattr(self.account, "get_wallet_balance", None)
            if wallet_balance_fn is None:
                wallet_balance_fn = self.account.get_available_balance
            dynamic_trade_amount = (
                wallet_balance_fn() / max(MAX_SLOTS, 1)
                if MAX_SLOTS > 0
                else TRADE_AMOUNT_USDT
            )
            amount = min(dynamic_trade_amount, TRADE_AMOUNT_USDT)
            btc_allocation_factor = float(sig.get("btc_allocation_factor", 1.0) or 1.0)
            pool[symbol] = {
                "symbol": symbol,
                "side": sig["side"],
                "score": score,
                "quality": self._quality_bonus(sig.get("reason", "")),
                "target_price": target_price,
                "pullback_depth": pullback_depth,
                "entry_mode": entry_mode,
                "pullback_distance_atr": pullback_distance_atr,
                "atr": float(sig.get("atr") or real_atr),
                "reason": sig.get("reason", ""),
                "amount_usdt": amount * btc_allocation_factor,
                "base_amount_usdt": amount,
                "btc_regime_mode": sig.get("btc_regime_mode", "UNKNOWN"),
                "btc_direction_1h": sig.get("btc_direction_1h", 0),
                "btc_score_penalty": sig.get("btc_score_penalty", 0),
                "btc_allocation_factor": btc_allocation_factor,
                "btc_pre_penalty_score": sig.get("btc_pre_penalty_score", score),
                "raw_signal_score": sig.get("raw_score", sig.get("btc_pre_penalty_score", score)),
                "btc_adjusted_score": sig.get("btc_adjusted_score", score),
                "history_adjusted_score": sig.get("history_adjusted_score", score),
                "history_score_multiplier": sig.get("history_score_multiplier", 1.0),
                "pullback_confirmation_score": None,
                "volume_fade_logged": False,
                "adverse_volume_logged": False,
                "leverage": self.symbol_rotation.get_dynamic_leverage(symbol, score),
                "created_at": now,
                "touched_at": None,
            }

        capacity = (
            max(0, MAX_SLOTS - len(self.account.positions) - len(self.account.pending_limit_orders))
            if MAX_SLOTS > 0 else len(pool)
        )
        ranked = sorted(
            pool.values(),
            key=lambda item: (item["score"], item["quality"], -item["created_at"]),
            reverse=True,
        )
        selected = {}
        budget_used = 0.0
        for item in ranked:
            if len(selected) >= capacity:
                break
            if budget_used + item["amount_usdt"] > available_balance:
                continue
            selected[item["symbol"]] = item
            budget_used += item["amount_usdt"]

        old_symbols = set(self.pending_pullback_candidates)
        self.pending_pullback_candidates = selected
        for symbol in old_symbols - set(selected):
            self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
        for symbol in set(selected) - old_symbols:
            item = selected[symbol]
            self._record_pullback_outcome("candidate_created")
            if item["entry_mode"] == "CURRENT_MAKER":
                self.account.log(
                    f"⚡ [現價Maker候選] {symbol} {item['side']} {item['score']}分/品質{item['quality']} "
                    f"現價限價 @ {item['target_price']:.8g}（BTC={item['btc_regime_mode']}，倉位×{item['btc_allocation_factor']:.2f}）",
                    "INFO",
                )
                continue
            self.account.log(
                f"🎯 [回踩候選] {symbol} {item['side']} {item['score']}分/品質{item['quality']} "
                f"等待{item['pullback_depth']:.0%}回踩（實際{item['pullback_distance_atr']:.2f} ATR）觸價 @ {item['target_price']:.8g}"
                f"（BTC={item['btc_regime_mode']}，倉位×{item['btc_allocation_factor']:.2f}）",
                "INFO",
            )

    async def _place_current_maker_candidate(
        self, symbol: str, candidate: dict, live_price: float, now: float
    ) -> bool:
        """90+ 試行模式：以最新價送 Post-Only，不等待回踩或 1m 反轉。"""
        committed = len(self.account.positions) + len(self.account.pending_limit_orders)
        if MAX_SLOTS > 0 and committed >= MAX_SLOTS:
            self._drop_pullback_candidate(symbol, "可用持倉槽位已滿", now, cooldown=False)
            return False
        if self.account.get_available_balance() < candidate["amount_usdt"]:
            self._drop_pullback_candidate(symbol, "可用保證金不足", now, cooldown=False)
            return False

        atr = max(float(candidate.get("atr") or 0.0), live_price * 1e-6)
        sl_distance, tp_distance = compute_sl_tp_distance(live_price, atr)
        sl, tp = build_sl_tp_for_side(live_price, candidate["side"], sl_distance, tp_distance)
        original_amount = candidate["amount_usdt"]
        candidate["amount_usdt"], projected_risk = cap_margin_to_trade_risk(
            original_amount, candidate["leverage"], live_price, sl,
        )
        if candidate["amount_usdt"] < original_amount - 0.01:
            self.account.log(
                f"🛡️ [單筆風險上限] {symbol} 保證金 {original_amount:.2f}→{candidate['amount_usdt']:.2f}U，"
                f"預估最大淨虧損≤{projected_risk:.2f}U", "INFO",
            )
        if not await self._execution_price_is_safe(symbol, candidate["side"]):
            self._drop_pullback_candidate(symbol, "執行市場合約或價差驗證未通過", now)
            return False
        placed = await self.account.place_limit_entry(
            symbol=symbol, side=candidate["side"], target_price=live_price,
            amount_usdt=candidate["amount_usdt"], sl=sl, tp=tp,
            reason=f"CurrentPrice_PostOnly_90Plus | {candidate['reason']}", atr=atr,
            leverage=candidate["leverage"], signal_score=candidate["score"],
            post_only=True, entry_context={
                "entry_mode": "CURRENT_MAKER",
                "btc_regime_at_entry": candidate["btc_regime_mode"],
                "btc_direction_1h_at_entry": candidate["btc_direction_1h"],
                "btc_score_penalty": candidate["btc_score_penalty"],
                "btc_allocation_factor": candidate["btc_allocation_factor"],
                "btc_pre_penalty_score": candidate["btc_pre_penalty_score"],
                "raw_signal_score": candidate["raw_signal_score"],
                "btc_adjusted_score": candidate["btc_adjusted_score"],
                "history_adjusted_score": candidate["history_adjusted_score"],
                "history_score_multiplier": candidate["history_score_multiplier"],
                "pullback_confirmation_score": None,
            },
        )
        if placed:
            self._record_pullback_outcome("current_maker_placed")
            self.account.log(
                f"⚡ [90+現價Maker掛單] {symbol} {candidate['side']} "
                f"{candidate['score']}分 @ {live_price:.8g}",
                "INFO",
            )
            self.pending_pullback_candidates.pop(symbol, None)
            return True
        self._drop_pullback_candidate(symbol, "現價 Post-Only 掛單失敗", now)
        return False

    def _entry_direction_allowed(self, symbol: str, side: str, planned_price: float, log_on_fail: bool = True) -> bool:
        """Final safeguard: ensure requested entry direction aligns with higher-timeframe filters.

        Returns True if allowed, False if should be blocked.
        """
        # BTC regime guard
        try:
            from core.config import BTC_REGIME_FILTER_ENABLED, BTC_REGIME_ALLOW_CONTRARY, SYMBOL_1H_ST_FILTER_ENABLED, ENABLE_1H_EMA50_FILTER, STRUCTURED_1H_EMA50_TOLERANCE_PCT
        except Exception:
            BTC_REGIME_FILTER_ENABLED = False
            BTC_REGIME_ALLOW_CONTRARY = False
            SYMBOL_1H_ST_FILTER_ENABLED = False
            ENABLE_1H_EMA50_FILTER = False
            STRUCTURED_1H_EMA50_TOLERANCE_PCT = 0.0

        want_dir = 1 if side == "LONG" else -1

        # BTC regime check (global large-cap direction guard)
        if BTC_REGIME_FILTER_ENABLED:
            btc_dir = int(getattr(self, "btc_1h_st_direction", 0) or 0)
            if btc_dir != 0 and btc_dir != want_dir and not BTC_REGIME_ALLOW_CONTRARY:
                if log_on_fail and hasattr(self, "account"):
                    self.account.log(f"🛑 {symbol} 拒絕開倉：BTC 大盤 1h 方向不符 (BTC={btc_dir})，禁止逆勢開倉", "WARNING")
                return False

        # Per-symbol 1h SuperTrend guard
        if SYMBOL_1H_ST_FILTER_ENABLED:
            sym_cache = getattr(self, "st_direction_1h_cache", {}) or {}
            sym_dir = int(sym_cache.get(symbol) or 0)
            if sym_dir == 0:
                if log_on_fail and hasattr(self, "account"):
                    self.account.log(
                        f"⏸️ {symbol} 暫不開倉：個幣 1h SuperTrend 尚未載入，等待趨勢確認",
                        "INFO",
                    )
                return False
            if sym_dir != 0 and sym_dir != want_dir:
                if log_on_fail and hasattr(self, "account"):
                    self.account.log(f"🛑 {symbol} 拒絕開倉：個幣 1h SuperTrend 方向不符 (1h={sym_dir})，跳過本次進場", "WARNING")
                return False

        # 1h EMA50 filter: require planned price to be on the same side of EMA50
        if ENABLE_1H_EMA50_FILTER:
            ema_cache = getattr(self, "ema_50_1h_cache", {}) or {}
            ema50 = ema_cache.get(symbol)
            if ema50 is not None and ema50 > 0:
                tol = 1.0 - STRUCTURED_1H_EMA50_TOLERANCE_PCT if want_dir == 1 else 1.0 + STRUCTURED_1H_EMA50_TOLERANCE_PCT
                if want_dir == 1:
                    # LONG requires price not significantly below EMA50
                    if planned_price < ema50 * (1.0 - STRUCTURED_1H_EMA50_TOLERANCE_PCT):
                        if log_on_fail and hasattr(self, "account"):
                            self.account.log(f"🛑 {symbol} 拒絕開多單：價格低於1h EMA50 ({ema50:.6g})，方向不符", "WARNING")
                        return False
                else:
                    # SHORT requires price not significantly above EMA50
                    if planned_price > ema50 * (1.0 + STRUCTURED_1H_EMA50_TOLERANCE_PCT):
                        if log_on_fail and hasattr(self, "account"):
                            self.account.log(f"🛑 {symbol} 拒絕開空單：價格高於1h EMA50 ({ema50:.6g})，方向不符", "WARNING")
                        return False

        return True

    def _ma3_ma15_entry_allowed(
        self, symbol: str, side: str, df: pd.DataFrame, log_on_fail: bool = True,
        entry_type: str = "",
    ) -> bool:
        """一般趨勢單須與 MA3/MA15 同向；已確認峰谷允許在交叉前反手。"""
        if df is None or df.empty:
            if log_on_fail:
                self.account.log(f"🛑 {symbol} 拒絕開倉：MA3/MA15 資料不足", "WARNING")
            return False
        close = pd.to_numeric(df["close"], errors="coerce")
        ma3 = pd.to_numeric(df["ma3"], errors="coerce") if "ma3" in df.columns else close.rolling(3).mean()
        ma15 = pd.to_numeric(df["ma15"], errors="coerce") if "ma15" in df.columns else close.rolling(15).mean()
        ma3_now = float(ma3.iloc[-1])
        ma15_now = float(ma15.iloc[-1])
        requested = str(side or "").upper()
        if str(entry_type or "").upper() in ("TROUGH_TURN", "PEAK_TURN"):
            return True
        allowed = (
            (requested == "LONG" and ma3_now > ma15_now)
            or (requested == "SHORT" and ma3_now < ma15_now)
        )
        if not allowed and log_on_fail:
            trend = "MA3>MA15 偏多" if ma3_now > ma15_now else "MA3<MA15 偏空" if ma3_now < ma15_now else "MA3=MA15 無方向"
            self.account.log(
                f"🛑 {symbol} 拒絕開{('多' if requested == 'LONG' else '空')}：{trend}"
                f"（MA3={ma3_now:.8g}, MA15={ma15_now:.8g}）",
                "WARNING",
            )
        return allowed

    def _continuous_market_mode_for(
        self, symbol: str, wave_regime: str, price: float,
    ) -> str:
        """只依個幣自身趨勢，將短週期型態升級為猴市／牛市／熊市。"""
        if str(wave_regime).upper() != "TREND":
            return "RANGE"

        symbol_st = int(self.st_direction_1h_cache.get(symbol) or 0)
        ema50 = float(self.ema_50_1h_cache.get(symbol) or 0.0)
        if symbol_st == 1 and ema50 > 0 and price >= ema50:
            return "BULL"
        if symbol_st == -1 and ema50 > 0 and price <= ema50:
            return "BEAR"
        return "TREND"

    def _same_side_entry_allowed(self, symbol: str, side: str) -> bool:
        """Prevent correlated entries from filling every slot in one direction."""
        if MAX_SAME_SIDE_POSITIONS <= 0:
            return True
        requested_side = str(side or "").upper()
        committed = list(self.account.positions.items()) + list(self.account.pending_limit_orders.items())
        same_side_count = sum(
            1 for _symbol, order in committed
            if str((order or {}).get("side") or "").upper() == requested_side
        )
        if same_side_count >= MAX_SAME_SIDE_POSITIONS:
            self.account.log(
                f"🛑 {symbol} 拒絕開倉：{requested_side} 已有 {same_side_count} 筆持倉／掛單，"
                f"同向上限為 {MAX_SAME_SIDE_POSITIONS}",
                "WARNING",
            )
            return False
        return True

    def _ma5_stop_cooldown_remaining(self, symbol: str, side: str, now: float) -> float:
        """Return remaining cooldown after the latest MA2/MA5 same-side hard stop."""
        if MA5_STOP_LOSS_COOLDOWN_SEC <= 0:
            return 0.0
        for trade in reversed(self.account.trades):
            if trade.get("symbol") != symbol or str(trade.get("side") or "").upper() != str(side or "").upper():
                continue
            if not str(trade.get("action") or "").startswith("CLOSE"):
                continue
            reason = str(trade.get("reason") or "")
            if "Stop-Loss" not in reason and not ("止損" in reason and "移動" not in reason):
                return 0.0
            closed_at = float(trade.get("timestamp") or 0.0)
            return max(0.0, MA5_STOP_LOSS_COOLDOWN_SEC - max(0.0, now - closed_at))
        return 0.0

    def _ma2_confirmation_allowed(self, symbol: str, side: str, signal: dict) -> bool:
        """Require the red-circle style short-term breakout before MA2 entry."""
        import core.config as runtime_config
        if not runtime_config.MA2_CONFIRMATION_ENTRY_ENABLED:
            return True
        close = float(signal.get("confirmation_close") or 0.0)
        open_price = float(signal.get("confirmation_open") or 0.0)
        ma2 = float(signal.get("confirmation_ma2") or 0.0)
        ma5 = float(signal.get("confirmation_ma5") or 0.0)
        recent_high = float(signal.get("confirmation_recent_high") or 0.0)
        recent_low = float(signal.get("confirmation_recent_low") or 0.0)
        if str(side).upper() == "LONG":
            confirmed = close > open_price and close > ma2 and close > ma5 and close > recent_high
        else:
            confirmed = close < open_price and close < ma2 and close < ma5 and close < recent_low
        if not confirmed:
            self.account.log(f"[MA2 confirmation] {symbol} {side} waiting for confirmed breakout", "INFO")
            return False
        return True

    @staticmethod
    def _structured_stop_cooldown_blocks(
        entry_mode: str, remaining: float,
    ) -> bool:
        """Channel Swing uses its own confirmation and ignores legacy stop cooldowns."""
        return bool(
            remaining > 0
            and str(entry_mode or "").upper() not in (
                "EXHAUSTION_SNIPER", "PIVOT_TURN", "CHANNEL_SWING",
            )
        )

    async def _place_structured_entry(
        self, symbol: str, signal: dict, live_price: float
    ) -> bool:
        """Place one of the three non-MA5 entries with an exchange hard stop."""
        committed = len(self.account.positions) + len(self.account.pending_limit_orders)
        if MAX_SLOTS > 0 and committed >= MAX_SLOTS:
            return False
        score = int(signal.get("score") or 0)
        side = signal["side"]
        entry_mode = signal["entry_mode"]
        signal_volume_ratio = signal.get("volume_ratio")
        if (
            signal_volume_ratio is not None
            and float(signal_volume_ratio) < KELTNER_MIN_VOLUME_RATIO
        ):
            self.account.log(
                f"🛑 {symbol} {side} 當下量能 {float(signal_volume_ratio):.2f}x "
                f"低於 {KELTNER_MIN_VOLUME_RATIO:.2f}x，取消結構化開倉",
                "WARNING",
            )
            return False
        if not self._same_side_entry_allowed(symbol, side):
            return False
        stop_cooldown_fn = getattr(
            self.symbol_rotation, "get_stop_cooldown_remaining", lambda *_args: 0.0
        )
        stop_cooldown_remaining = float(stop_cooldown_fn(symbol, side) or 0.0)
        if self._structured_stop_cooldown_blocks(
            entry_mode, stop_cooldown_remaining,
        ):
            self.account.log(
                f"🛑 {symbol} {side} 近期同方向連續停損，冷卻尚餘 "
                f"{stop_cooldown_remaining / 3600.0:.1f} 小時，拒絕結構化進場",
                "WARNING",
            )
            return False
        if entry_mode == "MA3_PIVOT" and not self._ma2_confirmation_allowed(symbol, side, signal):
            return False
        is_limit = signal.get("action") == "ENTER_LIMIT"
        planned_price = float(signal.get("target_price") if is_limit else live_price)
        atr = max(float(signal.get("atr") or 0.0), planned_price * 1e-6)
        if not self._abnormal_market_entry_allowed(
            symbol, side, planned_price, atr,
            float(signal.get("signal_candle_open") or planned_price),
            float(signal.get("signal_candle_high") or planned_price),
            float(signal.get("signal_candle_low") or planned_price),
            float(signal.get("signal_candle_close") or planned_price),
        ):
            return False
        # 最後一道方向守門：避免在高週期趨勢不符時開錯方向 (MA5_CROSS_PIVOT 策略除外)
        if entry_mode not in ("MA5_CROSS_PIVOT", "EXHAUSTION_SNIPER", "PIVOT_TURN", "CHANNEL_SWING"):
            if not self._entry_direction_allowed(symbol, side, planned_price):
                return False
        candle_low = float(signal.get("signal_candle_low") or planned_price)
        candle_high = float(signal.get("signal_candle_high") or planned_price)

        # BREAKOUT 限價掛單：止損以「訊號K棒低/高點」為基準（結構失效點），
        # 而非以限價進場點往下/上算 ATR。這樣進場在 EMA20 附近（限價），
        # 止損在突破K棒低點以下，兩者距離 = 突破K棒振幅的一大半，
        # 遠比舊版「進場@突破高點 - 1ATR」給更寬的止損空間，賠率大幅改善。
        # 非 BREAKOUT 的 SUPPORT_PULLBACK 等仍用原本邏輯。
        if entry_mode in ("EXHAUSTION_SNIPER", "PIVOT_TURN"):
            sl = planned_price * (
                1.0 - EXHAUSTION_SNIPER_STOP_LOSS_PCT
                if side == "LONG"
                else 1.0 + EXHAUSTION_SNIPER_STOP_LOSS_PCT
            )
        elif entry_mode == "BREAKOUT" and is_limit:
            if side == "LONG":
                sl = candle_low - BREAKOUT_CANDLE_STOP_BUFFER_ATR * atr
                sl = min(sl, planned_price * (1.0 - MIN_SL_DISTANCE_PCT))
            else:
                sl = candle_high + BREAKOUT_CANDLE_STOP_BUFFER_ATR * atr
                sl = max(sl, planned_price * (1.0 + MIN_SL_DISTANCE_PCT))
        elif side == "LONG":
            sl = min(
                planned_price - BREAKOUT_HARD_STOP_ATR_MULT * atr,
                candle_low - BREAKOUT_CANDLE_STOP_BUFFER_ATR * atr,
            )
            # PAXG、FARTCOIN 這類絕對價格波動小或報價精度粗的品種，ATR/K棒
            # 算出來的止損可能窄到只剩幾個最小報價單位，一有正常雜訊就被
            # 掃到。比照舊版 MA5 邏輯套用 MIN_SL_DISTANCE_PCT 下限。
            sl = min(sl, planned_price * (1.0 - MIN_SL_DISTANCE_PCT))
        else:
            sl = max(
                planned_price + BREAKOUT_HARD_STOP_ATR_MULT * atr,
                candle_high + BREAKOUT_CANDLE_STOP_BUFFER_ATR * atr,
            )
            sl = max(sl, planned_price * (1.0 + MIN_SL_DISTANCE_PCT))
        initial_risk = abs(planned_price - sl)
        # Ensure stop-loss is on the correct side and respects minimum distance.
        min_dist = (
            planned_price * EXHAUSTION_SNIPER_STOP_LOSS_PCT
            if entry_mode in ("EXHAUSTION_SNIPER", "PIVOT_TURN")
            else max(planned_price * MIN_SL_DISTANCE_PCT, atr * STOP_LOSS_MULTIPLIER)
        )
        if side == "LONG":
            if sl >= planned_price - 1e-12:
                sl = planned_price - min_dist
        else:
            if sl <= planned_price + 1e-12:
                sl = planned_price + min_dist
        initial_risk = abs(planned_price - sl)
        if initial_risk <= 0:
            return False
        channel_swing_no_stop = entry_mode == "CHANNEL_SWING"
        if channel_swing_no_stop:
            sl = 0.0
            initial_risk = 0.0
        structured_net_rr = None
        profit_profile = signal.get("profit_profile")
        if not profit_profile:
            profit_profile = "TREND_EXTENSION" if entry_mode != "SUPPORT_PULLBACK" else "BOUNCE"

        if profit_profile == "BOUNCE":
            reward_pct = float(signal.get("bounce_target_pct") or 0.0)
            if reward_pct <= 0 and entry_mode == "SUPPORT_PULLBACK":
                self.account.log(
                    f"🛑 {symbol} 反彈單未計算到獲利空間 (bounce_target_pct=0)，拒絕掛單",
                    "WARNING",
                )
                return False
            if reward_pct > 0:
                structured_net_rr, _, _ = compute_net_reward_risk(
                    planned_price, sl, reward_pct,
                )
                required_net_rr = (
                    STRUCTURED_MIN_NET_REWARD_RISK
                    if STRUCTURED_NET_RR_FILTER_ENABLED
                    else STRUCTURED_NET_RR_HARD_FLOOR
                )
                if structured_net_rr + 1e-12 < required_net_rr:
                    self.account.log(
                        f"🛑 {symbol} 結構反彈單淨風報比 {structured_net_rr:.2f}:1 低於 "
                        f"{required_net_rr:.2f}:1（已含雙邊費用與出場滑價），拒絕掛單",
                        "WARNING",
                    )
                    return False
        leverage = self.symbol_rotation.get_dynamic_leverage(symbol, score)
        if channel_swing_no_stop:
            available_bal = max(0.0, float(self.account.get_available_balance()))
            fee_safe_available = available_bal / (
                1.0 + leverage * max(TAKER_FEE_RATE, 0.0)
            )
            amount = min(self._continuous_entry_amount(), fee_safe_available)
        else:
            wallet_balance_fn = getattr(self.account, "get_wallet_balance", None)
            if wallet_balance_fn is None:
                wallet_balance_fn = self.account.get_available_balance
            amount = min(
                wallet_balance_fn() / max(MAX_SLOTS, 1)
                if MAX_SLOTS > 0 else TRADE_AMOUNT_USDT,
                TRADE_AMOUNT_USDT,
            )
        amount, projected_risk = cap_margin_to_trade_risk(
            amount, leverage, planned_price,
            planned_price if channel_swing_no_stop else sl,
        )
        if amount < MIN_TRADE_USDT:
            self.account.log(f"🛑 {symbol} 風控縮減後金額 {amount:.2f}U 低於最小交易門檻 {MIN_TRADE_USDT}U，放棄掛單", "WARNING")
            return False
        available_bal = self.account.get_available_balance()
        if available_bal < amount:
            if available_bal >= MIN_TRADE_USDT:
                self.account.log(f"⚠️ {symbol} 可用餘額 {available_bal:.2f}U 不足 {amount:.2f}U，改以剩餘餘額掛單", "WARNING")
                amount = available_bal
            else:
                self.account.log(f"🛑 {symbol} 可用餘額 {available_bal:.2f}U 不足 {amount:.2f}U 且低於最小門檻，放棄掛單", "WARNING")
                return False
        if not await self._execution_price_is_safe(symbol, side):
            return False
        entry_context = {
            "entry_mode": entry_mode,
            "initial_sl": sl, "initial_risk": initial_risk,
            "signal_candle_low": candle_low, "signal_candle_high": candle_high,
            "channel_turn_low": signal.get("channel_turn_low"),
            "channel_turn_high": signal.get("channel_turn_high"),
            "btc_regime_at_entry": signal.get("btc_regime_mode", "ALIGNED"),
            "btc_direction_1h_at_entry": self.btc_1h_st_direction,
            "btc_score_penalty": int(signal.get("btc_score_penalty") or 0),
            "profit_profile": profit_profile,
            "profit_room_pct": float(signal.get("profit_room_pct") or 0.0),
            "bounce_capture_ratio": float(signal.get("bounce_capture_ratio") or 0.0),
            "bounce_target_pct": float(signal.get("bounce_target_pct") or 0.0),
            "structured_net_rr": (
                round(structured_net_rr, 4) if structured_net_rr is not None else None
            ),
            "high_readiness_low_room": bool(signal.get("high_readiness_low_room")),
            "wave_regime": signal.get("wave_regime"),
            "market_mode": signal.get("market_mode"),
        }
        kwargs = dict(
            symbol=symbol, side=side, amount_usdt=amount, sl=sl, tp=0.0,
            reason=signal["reason"], atr=atr, leverage=leverage,
            signal_score=score, entry_context=entry_context,
        )
        if is_limit:
            placed = await self.account.place_limit_entry(
                target_price=planned_price, post_only=True, **kwargs
            )
        else:
            placed = await self.account.open_position(
                price=planned_price, **kwargs
            )
        if placed:
            order_type = "支撐限價" if is_limit else "市價"
            protection_text = (
                "無中途停損，僅對面KC外軌轉向平倉"
                if channel_swing_no_stop
                else f"硬停損 {sl:.8g}｜風險 {initial_risk:.8g}"
            )
            self.account.log(
                f"📝 [結構掛單] {symbol} {side} {entry_mode} {order_type} @ "
                f"{planned_price:.8g}｜{protection_text}",
                "SUCCESS",
            )
        return bool(placed)

    async def _place_ma5_reversal_entry(
        self, symbol: str, side: str, ma5_sig: dict, live_price: float, now: float
    ) -> bool:
        """MA5回撤時預掛底部Maker；已拐頭的舊路徑則仍以對手價成交。"""
        committed = len(self.account.positions) + len(self.account.pending_limit_orders)
        if MAX_SLOTS > 0 and committed >= MAX_SLOTS:
            return False
        score = int(ma5_sig.get("score") or 0)
        if not self._same_side_entry_allowed(symbol, side):
            return False
        ma5_stop_cooldown = self._ma5_stop_cooldown_remaining(symbol, side, now)
        if not self._ma2_confirmation_allowed(symbol, side, ma5_sig):
            return False
        if ma5_stop_cooldown > 0:
            self.account.log(
                f"🛑 {symbol} {side} MA2 硬停損後冷卻尚餘 "
                f"{ma5_stop_cooldown / 60.0:.0f} 分鐘，拒絕重開",
                "WARNING",
            )
            return False
        if score < MIN_SCORE_THRESHOLD:
            self.account.log(
                f"🛑 {symbol} MA5訊號 {score}分低於 {MIN_SCORE_THRESHOLD} 分，拒絕開倉",
                "INFO",
            )
            return False

        # [已關閉] 5分鐘週期進場前過濾 — 因策略改為純1分鐘MA5方向，高時間周期過濾
        # 會導致MA5已轉向但因5m/15m還在舊方向而錯失即時反手進場機會，故停用。
        # trigger_df = await self.fetch_klines(symbol, timeframe=MA5_EXIT_TIMEFRAME, limit=30)
        # pre_entry_trigger = compute_position_trigger(trigger_df, side)
        # if pre_entry_trigger.get("strong") or (
        #     not is_bottom_order and pre_entry_trigger.get("ma_ok") is False
        # ):
        #     return False
        # 上面雖然關閉了，但下面判斷「是否用對手價成交」仍要用到這個旗標，
        # 不能整段一起註解掉，否則會是 NameError。
        is_bottom_order = bool(ma5_sig.get("pullback_bottom_order"))

        # [已關閉] 15分鐘EMA20趨勢過濾 — 同上，純1分鐘策略不依賴15m趨勢方向。
        # trend_df = await self.fetch_klines(symbol, timeframe="15m", limit=50)
        # trend_breach = self._trend_follow_breach(trend_df, side)
        # if trend_breach["breached"]:
        #     return False

        wallet_balance_fn = getattr(self.account, "get_wallet_balance", None)
        if wallet_balance_fn is None:
            wallet_balance_fn = self.account.get_available_balance
        dynamic_trade_amount = (
            wallet_balance_fn() / max(MAX_SLOTS, 1)
            if MAX_SLOTS > 0
            else TRADE_AMOUNT_USDT
        )
        amount_usdt = min(dynamic_trade_amount, TRADE_AMOUNT_USDT)
        if self.account.get_available_balance() < amount_usdt:
            return False

        if not await self._execution_price_is_safe(symbol, side):
            return False

        # 回撤底單使用策略估出的KC底部價並保持Maker；只有已轉彎路徑才取
        # 對手價立即成交。
        target_price = float(ma5_sig.get("target_price") or live_price)
        if not is_bottom_order and hasattr(self.exchange, "fetch_order_book"):
            try:
                book = await self.exchange.fetch_order_book(symbol, limit=3)
                if side == "LONG" and book.get("asks") and len(book["asks"]) > 0:
                    target_price = float(book["asks"][0][0])
                elif side == "SHORT" and book.get("bids") and len(book["bids"]) > 0:
                    target_price = float(book["bids"][0][0])
            except Exception:
                pass

        if not self._entry_direction_allowed(symbol, side, target_price):
            return False

        atr = max(float(ma5_sig.get("atr") or 0.0), target_price * 1e-6)
        sl_distance, tp_distance = compute_sl_tp_distance(target_price, atr)

        # 結構性止損與風險界限保護
        structural_sl = ma5_sig.get("structural_sl")
        if ma5_sig.get("entry_mode") == "MA5_CROSS_PIVOT":
            # MA2 拐點沒有固定 TP，獲利交由 0.2% 階梯鎖利；仍必須建立有效 ATR 止損。
            sl = target_price - sl_distance if side == "LONG" else target_price + sl_distance
            tp = 0.0
        elif structural_sl is not None:
            if side == "LONG":
                # 限制止損距離：最小不能低於 MIN_SL_DISTANCE_PCT，最大不能超過 2.0 * ATR
                min_sl = target_price - (target_price * MIN_SL_DISTANCE_PCT)
                max_sl = max(target_price - (2.0 * atr), target_price * (1.0 - config.FIXED_STOP_LOSS_PCT))
                sl = min(min_sl, max(max_sl, structural_sl))
                sl_dist = target_price - sl
                # 確保 TP 滿足最少盈虧比 (MIN_NET_REWARD_RISK)
                tp_dist_needed = sl_dist * MIN_NET_REWARD_RISK
                # 不讓停損大於停利：TP 距離至少要 >= SL 距離
                tp_dist_final = (
                    target_price * config.FIXED_TAKE_PROFIT_PCT
                    if config.FIXED_TAKE_PROFIT_PCT > 0
                    else max(tp_distance, tp_dist_needed, sl_dist)
                )
                tp = target_price + tp_dist_final
            else:
                min_sl = target_price + (target_price * MIN_SL_DISTANCE_PCT)
                max_sl = min(target_price + (2.0 * atr), target_price * (1.0 + config.FIXED_STOP_LOSS_PCT))
                sl = max(min_sl, min(max_sl, structural_sl))
                sl_dist = sl - target_price
                tp_dist_needed = sl_dist * MIN_NET_REWARD_RISK
                # 不讓停損大於停利：TP 距離至少要 >= SL 距離
                tp_dist_final = (
                    target_price * config.FIXED_TAKE_PROFIT_PCT
                    if config.FIXED_TAKE_PROFIT_PCT > 0
                    else max(tp_distance, tp_dist_needed, sl_dist)
                )
                tp = target_price - tp_dist_final
        else:
            sl, tp = build_sl_tp_for_side(target_price, side, sl_distance, tp_distance)

        leverage = self.symbol_rotation.get_dynamic_leverage(
            symbol, score, adx=ma5_sig.get("adx")
        )
        original_amount = amount_usdt
        amount_usdt, projected_risk = cap_margin_to_trade_risk(
            amount_usdt, leverage, target_price, sl,
        )
        if amount_usdt < original_amount - 0.01:
            self.account.log(
                f"🛡️ [單筆風險上限] {symbol} 保證金 {original_amount:.2f}→{amount_usdt:.2f}U，"
                f"預估最大淨虧損≤{projected_risk:.2f}U", "INFO",
            )

        placed = await self.account.place_limit_entry(
            symbol=symbol, side=side, target_price=target_price,
            amount_usdt=amount_usdt, sl=sl, tp=tp,
            reason=ma5_sig.get("reason", "MA5_Reversal_Entry"),
            atr=atr,
            leverage=leverage,
            signal_score=score,
            post_only=is_bottom_order,
            entry_context={
                "entry_mode": (
                    "MA5_CROSS_PIVOT" if ma5_sig.get("entry_mode") == "MA5_CROSS_PIVOT"
                    else "MA5_BOTTOM_LIMIT" if is_bottom_order else "MA5_REVERSAL"
                ),
                "btc_regime_at_entry": ma5_sig.get("btc_regime_mode", "UNKNOWN"),
                "ma5_curr": ma5_sig.get("ma5_curr"),
                "ma5_prev": ma5_sig.get("ma5_prev"),
                "ma5_prev2": ma5_sig.get("ma5_prev2"),
            },
        )
        if placed:
            self._record_pullback_outcome(
                "ma5_bottom_limit_placed" if is_bottom_order else "ma5_reversal_placed"
            )
            if is_bottom_order:
                direction_note = "回撤中預估底點" if side == "LONG" else "反彈中預估頂點"
                self.account.log(
                    f"🎯 [MA5回撤底點掛單] {symbol} {side} {score}分 @ {target_price:.8g}"
                    f"（{direction_note}，不等MA5轉彎）",
                    "INFO",
                )
                return True
            direction_note = "MA5谷底轉彎向上" if side == "LONG" else "MA5峰頂轉彎向下"
            timing_note = (
                "盤中投影連續確認，" if ma5_sig.get("early_projection")
                else "爆量微拐幅收線確認，" if ma5_sig.get("fast_entry")
                else ""
            )
            self.account.log(
                f"⚡ [MA5拐頭進場] {symbol} {side} {score}分 @ {target_price:.8g}（{direction_note}，{timing_note}對手價直接成交）",
                "INFO",
            )
            return True
        return False


    async def _monitor_pullback_candidates(self, now: float) -> None:
        daily_halt, _ = self.account.daily_loss_limit_hit()
        if daily_halt:
            for symbol in list(self.pending_pullback_candidates):
                self._drop_pullback_candidate(symbol, "每日虧損熔斷，停止新倉", now)
            return

        for symbol, candidate in list(self.pending_pullback_candidates.items()):
            if symbol in ENTRY_DISABLED_SYMBOLS or symbol not in DEFAULT_SYMBOLS:
                self._drop_pullback_candidate(symbol, "幣種已停止新倉或移出牌面", now)
                continue
            if symbol in self.account.positions or symbol in self.account.pending_limit_orders:
                self.pending_pullback_candidates.pop(symbol, None)
                continue
            if now - candidate["created_at"] > PULLBACK_TIMEOUT_MINUTES * 60:
                self._expired_pullback_sides[symbol] = candidate["side"]
                self._drop_pullback_candidate(
                    symbol, "等待回踩/反轉確認逾時，本波不再掛單，等待KC重置", now
                )
                continue

            live_price = self.tickers.get(symbol)
            if not live_price:
                continue
            if candidate.get("entry_mode") == "CURRENT_MAKER":
                await self._place_current_maker_candidate(symbol, candidate, live_price, now)
                continue

            target = candidate["target_price"]
            touched = live_price <= target if candidate["side"] == "LONG" else live_price >= target
            if candidate.get("touched_at") is None:
                if not touched:
                    continue
                candidate["touched_at"] = now
                self._record_pullback_outcome("target_touched")
                self.account.log(
                    f"👀 [回踩觸價] {symbol} {candidate['side']} @ {live_price:.8g}，"
                    "等待 1m 收盤反轉確認",
                    "INFO",
                )

            confirm_df = await self.fetch_klines(symbol, timeframe="3m", limit=100)
            if confirm_df.empty or len(confirm_df) < 50:
                continue
            confirm = self.strategy.confirm_pullback_entry(
                confirm_df, candidate["side"], ema_1h=self.ema_50_1h_cache.get(symbol),
                trend_1h_declining=self.adx_1h_declining_cache.get(symbol, False),
                btc_st_direction_1h=getattr(self, "btc_1h_st_direction", 0),
                btc_st_flip_age=getattr(self, "btc_1h_st_flip_age", 999), symbol=symbol,
            )
            if confirm.get("adverse_volume_spike") and not candidate.get("adverse_volume_logged"):
                candidate["adverse_volume_logged"] = True
                self._record_pullback_outcome("adverse_volume_observed")
                self.account.log(
                    f"⚠️ [逆向爆量觀察] {symbol} {candidate['side']} 回踩K量能 "
                    f"{confirm.get('adverse_volume_ratio', 0):.2f}倍均量，目前僅記錄、不擋單",
                    "WARNING",
                )
            if confirm.get("volume_faded") and not candidate.get("volume_fade_logged"):
                candidate["volume_fade_logged"] = True
                self.account.log(
                    f"📉 [回踩縮量] {symbol} 總量 "
                    f"{confirm.get('recent_volume_avg', 0):.0f}<"
                    f"{confirm.get('min_sustain_volume', 0):.0f}，"
                    f"量能+0，繼續評分（回踩確認{confirm.get('pullback_score', '-')}分）",
                    "INFO",
                )
            if confirm["status"] == "WAIT_REVERSAL":
                continue
            if confirm["status"] != "PASS":
                self._drop_pullback_candidate(symbol, f"條件已變差：{confirm['reason']}", now)
                continue

            final_btc_factor = float(confirm.get("btc_allocation_factor", 1.0) or 1.0)
            candidate["amount_usdt"] = min(
                candidate["amount_usdt"], candidate["base_amount_usdt"] * final_btc_factor
            )
            candidate["btc_regime_mode"] = confirm.get("btc_regime_mode", "UNKNOWN")
            candidate["btc_direction_1h"] = confirm.get("btc_direction_1h", 0)
            candidate["btc_score_penalty"] = confirm.get("btc_score_penalty", 0)
            candidate["btc_allocation_factor"] = final_btc_factor
            candidate["pullback_confirmation_score"] = confirm.get("pullback_score")

            fresh_target, fresh_atr = self._fresh_pullback_target(confirm_df, candidate["side"], candidate["score"])
            if fresh_target is None:
                self._drop_pullback_candidate(symbol, "最新KC至EMA20回踩空間不足0.10 ATR", now)
                continue
            drift = abs(fresh_target - target)
            if drift > max(candidate["atr"], fresh_atr) * PULLBACK_TARGET_MAX_DRIFT_ATR:
                self._drop_pullback_candidate(symbol, f"目標漂移 {drift / max(fresh_atr, 1e-12):.2f} ATR", now)
                continue

            candles_1m = await self.fetch_klines(symbol, timeframe="1m", limit=20)
            candidate_for_check = dict(candidate, target_price=fresh_target, atr=fresh_atr)

            from core.config import ENABLE_TRAILING_ENTRY, TRAILING_REVERSAL_ATR_MULT, TRAILING_ENTRY_TYPE

            reversal_confirmed = False
            use_post_only = True
            live_price = self.tickers.get(symbol, live_price)

            if ENABLE_TRAILING_ENTRY:
                if "local_extreme" not in candidate:
                    candidate["local_extreme"] = live_price
                else:
                    if candidate["side"] == "LONG":
                        candidate["local_extreme"] = min(candidate["local_extreme"], live_price)
                    else:
                        candidate["local_extreme"] = max(candidate["local_extreme"], live_price)

                bounce_distance = abs(live_price - candidate["local_extreme"])
                if bounce_distance >= TRAILING_REVERSAL_ATR_MULT * fresh_atr:
                    reversal_confirmed = True
                    use_post_only = (TRAILING_ENTRY_TYPE != "MARKET")
                    self.account.log(f"📈 [Trailing Entry] {symbol} {candidate['side']} 從極值 {candidate['local_extreme']:.8g} 反彈 {bounce_distance/max(fresh_atr, 1e-12):.2f} ATR，確認進場", "INFO")
            else:
                reversal_confirmed = self._pullback_reversal_confirmed(candidate_for_check, candles_1m)

            if not reversal_confirmed:
                continue

            reclaimed = live_price >= fresh_target if candidate["side"] == "LONG" else live_price <= fresh_target
            if not reclaimed and not ENABLE_TRAILING_ENTRY:
                self._drop_pullback_candidate(symbol, "反轉確認後又跌回/漲回目標錯側", now)
                continue
            committed = len(self.account.positions) + len(self.account.pending_limit_orders)
            if MAX_SLOTS > 0 and committed >= MAX_SLOTS:
                self._drop_pullback_candidate(symbol, "可用持倉槽位已滿", now, cooldown=False)
                continue
            if self.account.get_available_balance() < candidate["amount_usdt"]:
                self._drop_pullback_candidate(symbol, "可用保證金不足", now, cooldown=False)
                continue

            # 如果是 Trailing Entry Market，以 live_price 作為進場基準，止損基準設為 local_extreme 會更安全
            entry_price_ref = live_price if (ENABLE_TRAILING_ENTRY and not use_post_only) else fresh_target
            sl_distance, tp_distance = compute_sl_tp_distance(entry_price_ref, fresh_atr)
            sl, tp = build_sl_tp_for_side(entry_price_ref, candidate["side"], sl_distance, tp_distance)
            original_amount = candidate["amount_usdt"]
            candidate["amount_usdt"], projected_risk = cap_margin_to_trade_risk(
                original_amount, candidate["leverage"], entry_price_ref, sl,
            )
            if candidate["amount_usdt"] < original_amount - 0.01:
                self.account.log(
                    f"🛡️ [單筆風險上限] {symbol} 保證金 {original_amount:.2f}→{candidate['amount_usdt']:.2f}U，"
                    f"預估最大淨虧損≤{projected_risk:.2f}U", "INFO",
                )
            if not await self._execution_price_is_safe(symbol, candidate["side"]):
                self._drop_pullback_candidate(symbol, "執行市場合約或價差驗證未通過", now)
                continue
            placed = await self.account.place_limit_entry(
                symbol=symbol, side=candidate["side"], target_price=entry_price_ref,
                amount_usdt=candidate["amount_usdt"], sl=sl, tp=tp,
                reason=f"Trailing_Entry_Market | {candidate['reason']}" if ENABLE_TRAILING_ENTRY else f"Pullback_Confirmed_Limit | {candidate['reason']}", atr=fresh_atr,
                leverage=candidate["leverage"], signal_score=candidate["score"],
                post_only=use_post_only, entry_context={
                    "btc_regime_at_entry": candidate["btc_regime_mode"],
                    "btc_direction_1h_at_entry": candidate["btc_direction_1h"],
                    "btc_score_penalty": candidate["btc_score_penalty"],
                    "btc_allocation_factor": candidate["btc_allocation_factor"],
                    "btc_pre_penalty_score": candidate["btc_pre_penalty_score"],
                    "raw_signal_score": candidate["raw_signal_score"],
                    "btc_adjusted_score": candidate["btc_adjusted_score"],
                    "history_adjusted_score": candidate["history_adjusted_score"],
                    "history_score_multiplier": candidate["history_score_multiplier"],
                    "pullback_confirmation_score": candidate["pullback_confirmation_score"],
                },
            )
            if placed:
                self._record_pullback_outcome("maker_placed")
                self.account.log(
                    self._format_pullback_order_log(symbol, candidate, fresh_target),
                    "INFO",
                )
                self.pending_pullback_candidates.pop(symbol, None)

    async def _validate_pending_limit_orders(self, now: float) -> None:
        """先驗證再查成交；最大限度縮小失效訊號被舊掛單接住的時間窗。"""
        daily_halt, _ = self.account.daily_loss_limit_hit()
        if daily_halt:
            for symbol in list(self.account.pending_limit_orders):
                await self.account.cancel_pending_limit(symbol, "每日虧損熔斷，停止新倉")
                self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
            await self.account.check_pending_limit_orders()
            return

        for symbol, info in list(self.account.pending_limit_orders.items()):
            if symbol in ENTRY_DISABLED_SYMBOLS or symbol not in DEFAULT_SYMBOLS:
                await self.account.cancel_pending_limit(symbol, "幣種已停止新倉或移出牌面")
                continue
            entry_mode = (info.get("entry_context") or {}).get("entry_mode")
            order_timeout = (
                STRUCTURED_SUPPORT_ORDER_TIMEOUT_SEC
                if entry_mode == "SUPPORT_PULLBACK"
                else BREAKOUT_PULLBACK_TIMEOUT_SEC
                if entry_mode == "BREAKOUT"
                else ENTRY_LIMIT_TIMEOUT_SEC
            )
            if now - info["placed_at"] > order_timeout:
                self._record_pullback_outcome("maker_timeout")
                await self.account.cancel_pending_limit(
                    symbol, f"限價掛單 {order_timeout:.0f} 秒未成交"
                )
                # MA5拐頭/回撤底單逾時未成交不設冷卻：下一輪(5秒後)
                # 會用最新K線與KC重新估價，型態若仍成立便再掛；
                # 若價格已經走遠、型態不再成立，策略本身自然回HOLD，不需要額外
                # 冷卻機制硬擋——避免因為冷卻而錯過還在成立的真實訊號，也不會
                # 變成無腦追價，追不追完全看MA5型態當下是否仍然成立。
                if entry_mode not in ("MA5_REVERSAL", "MA5_BOTTOM_LIMIT"):
                    self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
                continue
            if entry_mode == "MA3_MA15_LIMIT":
                from core.config import CONTINUOUS_REVERSE_TIMEFRAME
                from core.indicators import detect_ma3_ma15_cross_and_turn
                fresh_df = await self.fetch_klines(
                    symbol, timeframe=CONTINUOUS_REVERSE_TIMEFRAME,
                    limit=100, keep_live=True,
                )
                if not fresh_df.empty:
                    fresh_df = drop_unclosed_candle(
                        fresh_df, CONTINUOUS_REVERSE_TIMEFRAME
                    )
                if fresh_df.empty or len(fresh_df) < 15:
                    continue
                fresh_signal = detect_ma3_ma15_cross_and_turn(fresh_df)
                fresh_side = fresh_signal.get("signal")
                if fresh_side != info.get("side"):
                    await self.account.cancel_pending_limit(
                        symbol,
                        f"MA3/MA15 方向失效：原{info.get('side')}→現{fresh_side or '等待'}",
                    )
                    self._pullback_retry_after[symbol] = now
                continue

            if entry_mode in ("SUPPORT_PULLBACK", "BREAKOUT"):
                confirm_df = await self.fetch_klines(symbol, timeframe="3m", limit=100)
                if not confirm_df.empty and len(confirm_df) >= 50:
                    confirm_df = self.strategy.compute_indicators(confirm_df)
                    from core.config import MIN_OPEN_SIGNAL_SCORE
                    ema_50_1h = self.ema_50_1h_cache.get(symbol)
                    st_direction_1h = self.st_direction_1h_cache.get(symbol)
                    structured_sig = self.strategy.evaluate_structured_entry(
                        confirm_df,
                        ema_50_1h=ema_50_1h,
                        st_direction_1h=st_direction_1h,
                        btc_st_direction_1h=self.btc_1h_st_direction,
                        symbol=symbol,
                        indicators_precomputed=True,
                    )
                    action = structured_sig.get("action", "HOLD")
                    score = int(structured_sig.get("score") or 0)
                    if action not in ("ENTER_MARKET", "ENTER_LIMIT") or score < MIN_OPEN_SIGNAL_SCORE:
                        self._record_pullback_outcome("maker_condition_changed")
                        await self.account.cancel_pending_limit(
                            symbol,
                    f"條件已變差或分數不足：模式={action}, 分數={score} < {MIN_OPEN_SIGNAL_SCORE}"
                        )
                        self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
                        continue
                    fresh_side = str(structured_sig.get("side") or "")
                    fresh_mode = str(structured_sig.get("entry_mode") or "")
                    if fresh_side != str(info.get("side") or ""):
                        self._record_pullback_outcome("maker_direction_changed")
                        await self.account.cancel_pending_limit(
                            symbol,
                            f"方向已翻轉：原{info.get('side')}→現{fresh_side}，撤銷舊掛單",
                        )
                        # 當輪主掃描即可依新方向重新建立，不讓舊方向冷卻擋住反手。
                        self._pullback_retry_after[symbol] = now
                        continue
                    if action != "ENTER_LIMIT" or fresh_mode != entry_mode:
                        self._record_pullback_outcome("maker_mode_changed")
                        await self.account.cancel_pending_limit(
                            symbol,
                            f"入口模式已改變：原{entry_mode}→現{fresh_mode or action}，撤銷舊掛單",
                        )
                        self._pullback_retry_after[symbol] = now
                        continue
                    fresh_target = structured_sig.get("target_price")
                    fresh_atr = max(
                        float(structured_sig.get("atr") or info.get("atr") or 0.0),
                        1e-12,
                    )
                    if fresh_target is None:
                        self._record_pullback_outcome("maker_target_missing")
                        await self.account.cancel_pending_limit(
                            symbol, "最新結構訊號缺少掛單價，撤銷舊掛單"
                        )
                        self._pullback_retry_after[symbol] = now
                        continue
                    drift_atr = abs(
                        float(fresh_target) - float(info["target_price"])
                    ) / fresh_atr
                    if drift_atr > PULLBACK_TARGET_MAX_DRIFT_ATR:
                        self._record_pullback_outcome("maker_target_drift")
                        await self.account.cancel_pending_limit(
                            symbol,
                            f"結構掛單目標已漂移 {drift_atr:.2f} ATR："
                            f"{float(info['target_price']):.8g}→{float(fresh_target):.8g}",
                        )
                        self._pullback_retry_after[symbol] = now
                        continue

            if entry_mode in ("CURRENT_MAKER", "MA5_REVERSAL", "MA5_BOTTOM_LIMIT", "SUPPORT_PULLBACK", "BREAKOUT"):
                # 90+現價單、MA5拐頭單與回撤底單只短暫存活15秒；底單逾時後
                # 由下一輪重算KC底價，不在舊單上套用回踩二次確認與目標漂移。
                # BREAKOUT 回踩限價掛單不需要 confirm_pullback_entry，
                # 由限價單成交自然確認回踩；每日熔斷、幣種停用及掛單逾時仍在上方保留。
                continue

            confirm_df = await self.fetch_klines(symbol, timeframe="3m", limit=100)
            if confirm_df.empty or len(confirm_df) < 50:
                continue
            confirm = self.strategy.confirm_pullback_entry(
                confirm_df, info["side"], ema_1h=self.ema_50_1h_cache.get(symbol),
                trend_1h_declining=self.adx_1h_declining_cache.get(symbol, False),
                btc_st_direction_1h=getattr(self, "btc_1h_st_direction", 0),
                btc_st_flip_age=getattr(self, "btc_1h_st_flip_age", 999), symbol=symbol,
            )
            if confirm["status"] == "WAIT_REVERSAL":
                pass
            elif confirm["status"] != "PASS":
                self._record_pullback_outcome("maker_condition_changed")
                await self.account.cancel_pending_limit(symbol, f"條件已變差：{confirm['reason']}")
                self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
                continue
            current_factor = float(confirm.get("btc_allocation_factor", 1.0) or 1.0)
            placed_factor = float((info.get("entry_context") or {}).get("btc_allocation_factor", 1.0) or 1.0)
            if current_factor < placed_factor:
                self._record_pullback_outcome("maker_btc_changed")
                await self.account.cancel_pending_limit(
                    symbol, "BTC方向轉為背離，撤銷原倉位並按50%重新建立候選"
                )
                self._pullback_retry_after[symbol] = now
                continue
            fresh_target, fresh_atr = self._fresh_pullback_target(confirm_df, info["side"], int(info.get("signal_score") or MIN_SCORE_THRESHOLD))
            if fresh_target is None:
                self._record_pullback_outcome("maker_pullback_room_narrow")
                await self.account.cancel_pending_limit(symbol, "最新KC至EMA20回踩空間不足0.10 ATR")
                self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
                continue
            drift_atr = abs(fresh_target - info["target_price"]) / max(fresh_atr, 1e-12)
            if drift_atr > PULLBACK_TARGET_MAX_DRIFT_ATR:
                self._record_pullback_outcome("maker_target_drift")
                await self.account.cancel_pending_limit(
                    symbol, f"掛單目標已漂移 {drift_atr:.2f} ATR"
                )
                self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC

        pending_before_fill_check = set(self.account.pending_limit_orders)
        await self.account.check_pending_limit_orders()
        for symbol in pending_before_fill_check - set(self.account.pending_limit_orders):
            if symbol in self.account.positions:
                self._record_pullback_outcome("maker_filled")
            else:
                self._record_pullback_outcome("maker_reclaim_failed")
                self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
            self.account.save_state()

    @staticmethod
    def _validate_strict_pivot_entry(
        frame: pd.DataFrame, side: str,
    ) -> tuple[bool, str, int]:
        """Validate the 5887ffa pivot with strict line-distance and second-K rules."""
        if frame is None or len(frame) < 3:
            return False, "峰谷資料不足", -2
        required = {
            "open", "close", "high", "low", "ma3", "ma15",
            "kc_upper", "kc_lower", "atr",
        }
        if not required.issubset(frame.columns):
            return False, "峰谷確認欄位不足", -2

        side = str(side or "").upper()
        if side not in ("LONG", "SHORT"):
            return False, "峰谷方向無效", -2

        # 峰谷必須是 MA3 本身越過 KC 外軌，不接受只有價格影線碰軌。
        # 最後兩根保留給第一、第二確認 K，因此只檢查它們之前的峰谷。
        pivot_offset = None
        for offset in (-3, -4, -5):
            if len(frame) < abs(offset):
                continue
            row = frame.iloc[offset]
            ma3_value = float(row["ma3"])
            crossed_outer = (
                ma3_value < float(row["kc_lower"])
                if side == "LONG"
                else ma3_value > float(row["kc_upper"])
            )
            if crossed_outer:
                pivot_offset = offset
                break
        if pivot_offset is None:
            target = "下軌外" if side == "LONG" else "上軌外"
            return (
                False,
                f"MA3尚未越過KC{target}，即使價格有峰谷也不轉向、不開倉",
                -3,
            )

        from core.indicators import evaluate_minimum_kc_wave
        pivot_type = "TROUGH_TURN" if side == "LONG" else "PEAK_TURN"
        full_wave = evaluate_minimum_kc_wave(
            frame, pivot_offset, pivot_type,
        )
        if not full_wave["passed"]:
            return (
                False,
                f"{full_wave['reason']}，不轉向、不開倉",
                pivot_offset,
            )

        # 真正峰谷若仍貼近 MA15 或任一 KC 軌道，視為線邊盤旋。
        pivot = frame.iloc[pivot_offset]
        atr = max(float(pivot["atr"]), abs(float(pivot["close"])) * 1e-12)
        middle = float(
            pivot["kc_middle"]
            if "kc_middle" in frame.columns
            else pivot["ema_20"]
        )
        ma3_at_pivot = float(pivot["ma3"])
        line_distances = {
            "MA15": abs(ma3_at_pivot - float(pivot["ma15"])),
            "KC上軌": abs(ma3_at_pivot - float(pivot["kc_upper"])),
            "KC中軌": abs(ma3_at_pivot - middle),
            "KC下軌": abs(ma3_at_pivot - float(pivot["kc_lower"])),
        }
        nearest_line, nearest_distance = min(
            line_distances.items(), key=lambda item: item[1],
        )
        distance_atr = nearest_distance / atr
        if distance_atr < 0.15:
            return (
                False,
                f"峰谷MA3距{nearest_line}僅{distance_atr:.2f}ATR，視為盤旋，不轉向、不開倉",
                pivot_offset,
            )

        # 倒數第二與最後一根均須同方向，最後一根是第二根確認 K；
        # 第二根必須延續且至少收過峰谷所在區間的下一條軌道。
        pivot_position = len(frame) + pivot_offset
        confirmations = []
        for position in range(pivot_position + 1, len(frame)):
            row = frame.iloc[position]
            row_atr = max(float(row["atr"]), abs(float(row["close"])) * 1e-12)
            body = abs(float(row["close"]) - float(row["open"]))
            candle_range = max(float(row["high"]) - float(row["low"]), 0.0)
            is_doji = body <= max(0.05 * row_atr, 0.10 * candle_range)
            if is_doji:
                continue
            correct_color = (
                float(row["close"]) > float(row["open"])
                if side == "LONG"
                else float(row["close"]) < float(row["open"])
            )
            if not correct_color:
                return False, "opposite non-doji candle invalidated pivot confirmation", pivot_offset
            confirmations.append(row)

        if len(confirmations) < 2:
            return False, "need two non-doji direction candles; volume is not required", pivot_offset

        confirm_1, confirm_2 = confirmations[-2], confirmations[-1]
        first_close = float(confirm_1["close"])
        confirm_close = float(confirm_2["close"])
        ma3_has_turned = (
            float(confirm_2["ma3"]) > ma3_at_pivot
            if side == "LONG"
            else float(confirm_2["ma3"]) < ma3_at_pivot
        )
        if not ma3_has_turned:
            return False, "MA3 has not turned away from the KC extreme", pivot_offset

        ma15_now = float(confirm_2["ma15"])
        ma15_old = float(frame.iloc[-6]["ma15"]) if len(frame) >= 6 else float(frame.iloc[0]["ma15"])
        ma15_slope_atr = (ma15_now - ma15_old) / max(atr, 1e-8)

        # 過濾過激趨勢：做多時禁止均線急跌，做空時禁止均線急漲 (設定 0.25 ATR 為判定門檻)
        if side == "LONG" and ma15_slope_atr < -0.25:
            return False, f"MA15 slope ({ma15_slope_atr:.2f} ATR) too steep downwards", pivot_offset
        if side == "SHORT" and ma15_slope_atr > 0.25:
            return False, f"MA15 slope ({ma15_slope_atr:.2f} ATR) too steep upwards", pivot_offset

        if side == "LONG":
            kc_lower = float(confirm_2["kc_lower"])
            valid_second = (
                confirm_close > first_close
                and confirm_close > kc_lower
            )
        else:
            kc_upper = float(confirm_2["kc_upper"])
            valid_second = (
                confirm_close < first_close
                and confirm_close < kc_upper
            )
        if not valid_second:
            return False, "confirmation candle has not reached the required KC zone", pivot_offset

        return True, "strict pivot and non-doji candle confirmation passed", pivot_offset

    @staticmethod
    def _resolve_entry_atr(cr_info: dict, frame: pd.DataFrame, live_price: float) -> float:
        """Use the real candle ATR; never turn a missing value into a near-zero ATR."""
        candidates = [cr_info.get("atr") if cr_info else None]
        if frame is not None and not frame.empty:
            if "atr" in frame.columns:
                candidates.append(frame["atr"].iloc[-1])
            if {"kc_upper", "kc_lower"}.issubset(frame.columns):
                candidates.append(
                    abs(float(frame["kc_upper"].iloc[-1]) - float(frame["kc_lower"].iloc[-1])) / 2.0
                )
        for candidate in candidates:
            try:
                value = float(candidate)
            except (TypeError, ValueError):
                continue
            if pd.notna(value) and value > 0.0:
                return value
        return max(abs(float(live_price)) * 0.001, 1e-12)

    @staticmethod
    def _pivot_confirmation_body_atr(frame: pd.DataFrame, atr: float) -> float:
        if frame is None or frame.empty or not {"open", "close"}.issubset(frame.columns):
            return 0.0
        body = abs(float(frame["close"].iloc[-1]) - float(frame["open"].iloc[-1]))
        return body / max(float(atr), 1e-12)

    @staticmethod
    def _strong_burst_live_entry_is_valid(
        burst: dict, frame: pd.DataFrame, live_price: float,
    ) -> bool:
        """舊K線曾穿上軌不代表現在還能追多。

        實際下單價必須仍在上軌之上，並和 KC 中軌保持至少設定的 ATR 距離。
        """
        if frame is None or frame.empty or "atr" not in frame.columns:
            return False
        live_price = float(live_price)
        atr = float(frame["atr"].iloc[-1])
        middle = float(burst.get("kc_middle") or 0.0)
        upper = float(burst.get("kc_upper") or 0.0)
        if not all(math.isfinite(value) for value in (live_price, atr, middle, upper)):
            return False
        atr = max(atr, abs(live_price) * 1e-12)
        return bool(
            upper > 0
            and middle > 0
            and live_price >= upper
            and live_price >= middle + atr * TREND_ENTRY_MIN_KC_MIDDLE_DISTANCE_ATR
        )

    def _resolve_trailing_atr(
        self, symbol: str, position: dict, meta: dict, current_price: float,
    ) -> float:
        """取得可用 ATR；拒絕空值、非有限值及接近零的舊資料。"""
        current_price = abs(float(current_price))
        minimum_valid = current_price * 1e-6
        trigger = self.position_triggers.get(symbol, {})
        candidates = (
            trigger.get("atr"), meta.get("atr"), position.get("atr"),
            position.get("initial_risk"), meta.get("initial_risk"),
        )
        for candidate in candidates:
            try:
                value = float(candidate)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > minimum_valid:
                return value
        return max(current_price * 0.015, minimum_valid)

    @staticmethod
    def _opposite_closed_candle_exit(
        position: dict, frame: pd.DataFrame, timeframe: str,
        outer_run_active: bool,
    ) -> bool:
        """停用第一根反向 K 提前平倉；只等正式峰谷確認或 OUTER_RUN 出場。"""
        return False

    @staticmethod
    def _outer_run_second_candle_status(
        frame: pd.DataFrame, pending: dict,
    ) -> tuple[str, str, int | None]:
        """判斷 OUTER_RUN 平倉後的第二根反轉 K。

        WAIT 代表還在第一根 K；CONFIRMED 才可在下一根 K 開始反手；
        INVALIDATED 代表第二根沒有延續，本次倒 V／V 反手作廢。
        """
        required = {"open", "close", "kc_upper", "kc_lower"}
        if (
            frame is None or frame.empty or not isinstance(pending, dict)
            or not required.issubset(frame.columns)
        ):
            return "INVALIDATED", "第二根K資料不完整", None

        try:
            bar_id = (
                int(float(frame["timestamp"].iloc[-1]))
                if "timestamp" in frame.columns
                else int(frame.index[-1])
            )
            first_bar_id = int(pending["first_bar_id"])
            first_close = float(pending["first_close"])
            candle_open = float(frame["open"].iloc[-1])
            candle_close = float(frame["close"].iloc[-1])
            kc_upper = float(frame["kc_upper"].iloc[-1])
            kc_lower = float(frame["kc_lower"].iloc[-1])
        except (KeyError, TypeError, ValueError, OverflowError):
            return "INVALIDATED", "第二根K資料無效", None

        values = (first_close, candle_open, candle_close, kc_upper, kc_lower)
        if not all(math.isfinite(value) for value in values):
            return "INVALIDATED", "第二根K價格無效", bar_id
        if bar_id <= first_bar_id:
            return "WAIT", "等待第二根已收盤K", bar_id

        from_side = str(pending.get("from_side") or "").upper()
        if from_side == "LONG":
            confirmed = bool(
                candle_close < candle_open
                and candle_close < first_close
                and candle_open < kc_upper
                and candle_close < kc_upper
            )
            reason = (
                "第二根紅K繼續收低且留在KC上軌內"
                if confirmed else "第二根未繼續紅K收低，取消開空"
            )
        elif from_side == "SHORT":
            confirmed = bool(
                candle_close > candle_open
                and candle_close > first_close
                and candle_open > kc_lower
                and candle_close > kc_lower
            )
            reason = (
                "第二根綠K繼續收高且留在KC下軌內"
                if confirmed else "第二根未繼續綠K收高，取消開多"
            )
        else:
            return "INVALIDATED", "OUTER_RUN等待方向無效", bar_id
        return ("CONFIRMED" if confirmed else "INVALIDATED"), reason, bar_id

    @staticmethod
    def _detect_btc_1m_pulse(
        frame: pd.DataFrame, live_price: float,
    ) -> str | None:
        """Return a strong BTC 1m impulse direction, otherwise remain neutral."""
        required = {"close", "ma3", "atr"}
        lookback = BTC_1M_PULSE_LOOKBACK_BARS
        if (
            not BTC_1M_PULSE_FILTER_ENABLED
            or frame is None or len(frame) < lookback + 2
            or not required.issubset(frame.columns)
        ):
            return None
        try:
            price = float(live_price)
            ma3_now = float(frame["ma3"].iloc[-1])
            ma3_previous = float(frame["ma3"].iloc[-2])
            baseline = float(frame["close"].iloc[-(lookback + 1)])
            atr = max(float(frame["atr"].iloc[-1]), abs(price) * 1e-12)
        except (TypeError, ValueError, IndexError):
            return None
        if not all(math.isfinite(value) for value in (
            price, ma3_now, ma3_previous, baseline, atr,
        )):
            return None
        move_atr = (price - baseline) / atr
        if (
            move_atr >= BTC_1M_PULSE_MIN_ATR
            and ma3_now > ma3_previous
            and price >= ma3_now
        ):
            return "LONG"
        if (
            move_atr <= -BTC_1M_PULSE_MIN_ATR
            and ma3_now < ma3_previous
            and price <= ma3_now
        ):
            return "SHORT"
        return None

    def _begin_btc_lead_shadow(
        self, direction: str | None, frame: pd.DataFrame,
    ) -> None:
        """Start one shadow observation window for a newly detected BTC pulse."""
        side = str(direction or "").upper()
        if side not in ("LONG", "SHORT") or frame is None or frame.empty:
            self._btc_lead_shadow_active = {}
            return
        try:
            bar_id = int(float(frame.iloc[-1]["timestamp"]))
        except (KeyError, TypeError, ValueError, IndexError):
            return
        active = getattr(self, "_btc_lead_shadow_active", {})
        if active.get("key") != (side, bar_id):
            self._btc_lead_shadow_active = {
                "key": (side, bar_id), "side": side,
                "started_at": time.time(), "bar_id": bar_id,
            }

    def _record_btc_lead_shadow_candidate(
        self, symbol: str, frame: pd.DataFrame, live_price: float,
        chop_locked: bool,
    ) -> None:
        """Record, but never trade, a coin's first aligned outer reaction to BTC."""
        active = getattr(self, "_btc_lead_shadow_active", {})
        if not active or frame is None or frame.empty:
            return
        try:
            row = frame.iloc[-1]
            price = float(live_price)
            upper = float(row["kc_upper"])
            lower = float(row["kc_lower"])
            ma3 = float(row["ma3"])
            ma15 = float(row["ma15"])
            volume_ratio = float(row.get("volume") or 0.0) / max(
                float(row.get("vol_ma_20") or 0.0), 1e-12,
            )
        except (KeyError, TypeError, ValueError, IndexError):
            return
        side = str(active.get("side") or "")
        aligned_outer = (
            side == "LONG" and price >= upper and ma3 > ma15
        ) or (
            side == "SHORT" and price <= lower and ma3 < ma15
        )
        if not aligned_outer or volume_ratio < 1.0:
            return
        events = getattr(self, "_btc_lead_shadow_events", [])
        pulse_key = active.get("key")
        if any(event.get("pulse_key") == pulse_key and event.get("symbol") == symbol for event in events):
            return
        events.append({
            "timestamp": time.time(), "pulse_key": pulse_key,
            "symbol": symbol, "side": side,
            "delay_sec": round(max(0.0, time.time() - float(active["started_at"])), 3),
            "price": price, "kc_upper": upper, "kc_lower": lower,
            "volume_ratio": round(volume_ratio, 3), "chop_locked": bool(chop_locked),
        })
        del events[:-200]
        self._btc_lead_shadow_events = events

    def btc_lead_shadow_status(self) -> dict:
        events = list(getattr(self, "_btc_lead_shadow_events", []))
        eligible = [event for event in events if not event.get("chop_locked")]
        delays = [float(event["delay_sec"]) for event in eligible]
        return {
            "active": dict(getattr(self, "_btc_lead_shadow_active", {})),
            "events": events[-30:], "total_events": len(events),
            "eligible_events": len(eligible),
            "average_delay_sec": round(sum(delays) / len(delays), 3) if delays else None,
        }

    @staticmethod
    def _btc_pulse_blocks_entry(side: str, btc_1m_pulse: str | None) -> bool:
        pulse = str(btc_1m_pulse or "").upper()
        requested = str(side or "").upper()
        return (
            pulse in ("LONG", "SHORT")
            and requested in ("LONG", "SHORT")
            and pulse != requested
        )

    @staticmethod
    def _entry_matches_ranked_direction(
        side: str, ranked_direction: str | None,
    ) -> bool:
        """Only the market-ranked direction may open a position."""
        requested = str(side or "").upper()
        ranked = str(ranked_direction or "").upper()
        return requested in ("LONG", "SHORT") and (ranked == "BOTH" or requested == ranked)

    def _strongest_ranked_symbol(self, side: str) -> tuple[str | None, float]:
        """Return the highest final-score symbol for the requested direction."""
        requested = str(side or "").upper()
        ranked = []
        for metric in self.symbol_rotation.last_metrics:
            symbol = str(metric.get("symbol") or "")
            direction = str(
                metric.get("direction")
                or self.symbol_rotation.direction_map.get(symbol)
                or ""
            ).upper()
            if symbol and direction == requested:
                ranked.append((
                    symbol,
                    float(metric.get("final_score") or metric.get("quant_score") or 0.0),
                ))
        return max(ranked, key=lambda item: item[1]) if ranked else (None, 0.0)

    @staticmethod
    def _directional_trend_quality(
        frame: pd.DataFrame, live_price: float, side: str,
    ) -> float:
        """ATR-normalized current trend quality used for cross-symbol ranking."""
        required = {"close", "ma3", "atr"}
        if frame is None or len(frame) < 2 or not required.issubset(frame.columns):
            return 0.0
        try:
            direction = 1.0 if str(side).upper() == "LONG" else -1.0
            price = float(live_price)
            previous_close = float(frame["close"].iloc[-2])
            ma3_now = float(frame["ma3"].iloc[-1])
            ma3_previous = float(frame["ma3"].iloc[-2])
            atr = max(float(frame["atr"].iloc[-1]), abs(price) * 1e-12)
        except (TypeError, ValueError, IndexError):
            return 0.0
        values = (price, previous_close, ma3_now, ma3_previous, atr)
        if not all(math.isfinite(value) for value in values):
            return 0.0
        ma3_impulse = max(0.0, direction * (ma3_now - ma3_previous) / atr)
        price_impulse = max(0.0, direction * (price - previous_close) / atr)
        return round(ma3_impulse * 2.0 + price_impulse, 6)

    @staticmethod
    def _channel_volume_ratio(frame: pd.DataFrame) -> float:
        """Return the current candle volume relative to its rolling mean."""
        if frame is None or frame.empty or "volume" not in frame.columns:
            return 0.0
        try:
            volume = float(frame["volume"].iloc[-1])
            if "vol_ma_20" in frame.columns:
                volume_ma = float(frame["vol_ma_20"].iloc[-1])
            else:
                volume_ma = float(frame["volume"].iloc[:-1].tail(20).mean())
        except (TypeError, ValueError, IndexError):
            return 0.0
        if not math.isfinite(volume) or not math.isfinite(volume_ma) or volume_ma <= 0:
            return 0.0
        return max(0.0, volume / volume_ma)

    @staticmethod
    def _channel_held_volume_is_declining(frame: pd.DataFrame) -> bool:
        """Confirm low energy with three completed, consecutively weaker bars."""
        if frame is None or len(frame) < 4 or "volume" not in frame.columns:
            return False
        closed = frame.iloc[:-1].tail(3)
        if len(closed) < 3:
            return False
        try:
            volumes = [float(value) for value in closed["volume"]]
            if "vol_ma_20" in closed.columns:
                means = [float(value) for value in closed["vol_ma_20"]]
            else:
                baseline = float(frame["volume"].iloc[:-1].tail(20).mean())
                means = [baseline] * 3
            ratios = [volume / mean if mean > 0 else 0.0 for volume, mean in zip(volumes, means)]
        except (TypeError, ValueError):
            return False
        return (
            all(math.isfinite(value) for value in ratios)
            and ratios[0] > ratios[1] > ratios[2]
            and ratios[2] < KELTNER_MIN_VOLUME_RATIO
        )

    def _candidate_profit_potential(
        self, symbol: str, side: str, atr: float, price: float,
    ) -> float:
        """Estimate directional profit space from daily range and current ATR."""
        stats = getattr(self.symbol_rotation, "volatility_stats", {}).get(symbol, {})
        daily_key = "avg_daily_up_pct" if str(side).upper() == "LONG" else "avg_daily_down_pct"
        daily_space = float(stats.get(daily_key) or 0.0)
        atr_space = float(atr) / float(price) * 100.0 if price else 0.0
        return round(max(daily_space, atr_space), 6)

    @staticmethod
    def _select_strongest_same_side_candidates(
        candidates: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        """Keep only the strongest candidate for each direction in one scan."""
        ranked = sorted(
            candidates,
            key=lambda item: (
                int(item.get("priority") or 0),
                float(
                    item.get("profit_potential")
                    or (
                        float(item.get("atr") or 0.0)
                        / max(float(item.get("live_price") or item.get("price") or 0.0), 1e-12)
                    )
                ),
                float(item.get("trend_quality") or 0.0),
                float(item.get("volume_ratio") or 0.0),
                float(item.get("score") or 0.0),
            ),
            reverse=True,
        )
        selected = []
        skipped = []
        used_sides = set()
        for candidate in ranked:
            side = str(candidate.get("side") or "").upper()
            if side in used_sides:
                skipped.append(candidate)
                continue
            used_sides.add(side)
            selected.append(candidate)
        return selected, skipped

    @staticmethod
    def _candidate_board_refresh_needed(
        opened_any: bool, position_count: int, pending_count: int,
        max_slots: int, seconds_since_refresh: float,
    ) -> bool:
        """Refresh after a fill, or while capacity remains without a new fill."""
        if opened_any:
            return True
        committed = max(0, int(position_count)) + max(0, int(pending_count))
        has_capacity = max_slots <= 0 or committed < max_slots
        return has_capacity and seconds_since_refresh >= 15.0

    @staticmethod
    def _channel_entry_candidate_priority(reason: str | None) -> int:
        """Rank executable Channel Swing entries without changing their rules."""
        reason = str(reason or "")
        if reason in {
            "KC_LIVE_UPPER_BREAK_LONG", "KC_LIVE_LOWER_BREAK_SHORT",
            "KC_UPPER_TREND_CONFIRMED_LONG", "KC_LOWER_TREND_CONFIRMED_SHORT",
            "KC_UPPER_RETEST_BREAK_LONG", "KC_LOWER_RETEST_BREAK_SHORT",
            "CHOP_BREAKOUT_LONG", "CHOP_BREAKOUT_SHORT",
        }:
            return 3
        if reason in {
            "INSTANT_INNER_REENTRY_LONG", "INSTANT_INNER_REENTRY_SHORT",
            "LIVE_INNER_REENTRY_LONG", "LIVE_INNER_REENTRY_SHORT",
        }:
            return 2
        return 1

    @staticmethod
    def _channel_outer_uptrend_entry_action(
        frame: pd.DataFrame, live_price: float, pending: dict | None = None,
    ) -> dict:
        """Use existing trend quality, then require the next candle to break out."""
        required = {
            "open", "high", "low", "close", "ma3", "ma15",
            "kc_upper", "kc_lower",
        }
        if frame is None or len(frame) < 6 or not required.issubset(frame.columns):
            return {"action": "WAIT", "side": None, "reason": "OUTER_UPTREND_DATA_UNAVAILABLE"}
        try:
            live = frame.iloc[-1]
            closed = frame.iloc[:-1].dropna(subset=list(required)).copy()
            if len(closed) < 5:
                return {
                    "action": "WAIT", "side": None,
                    "reason": "OUTER_UPTREND_DATA_UNAVAILABLE",
                }
            latest = closed.iloc[-1]
            price = float(live_price)
            upper = float(live["kc_upper"])
            lower = float(live["kc_lower"])
        except (TypeError, ValueError, IndexError):
            return {"action": "WAIT", "side": None, "reason": "OUTER_UPTREND_DATA_INVALID"}
        if (
            not all(math.isfinite(value) for value in (price, upper, lower))
            or lower >= upper
        ):
            return {"action": "WAIT", "side": None, "reason": "OUTER_UPTREND_DATA_INVALID"}

        if not pending:
            latest_open = float(latest["open"])
            latest_close = float(latest["close"])
            latest_high = max(float(latest["high"]), latest_close)
            latest_upper = float(latest["kc_upper"])
            # 趨勢追多也必須由剛收盤的順勢綠 K 碰到上軌建立候選；
            # 下一根仍要破高才進場，與下軌追空完全鏡像。
            if latest_high < latest_upper or latest_close <= latest_open:
                return {
                    "action": "WAIT", "side": None,
                    "reason": "WAIT_OUTER_UPTREND", "kc_upper": upper,
                    "pending": None,
                }
            quality = closed.tail(6)
            quality_middle = (
                quality["kc_upper"].astype(float)
                + quality["kc_lower"].astype(float)
            ) / 2.0
            quality_close = quality["close"].astype(float)
            quality_ma3 = quality["ma3"].astype(float)
            quality_ma15 = quality["ma15"].astype(float)
            total_path = float(quality_close.diff().abs().sum())
            efficiency = (
                abs(float(quality_close.iloc[-1] - quality_close.iloc[0]))
                / total_path if total_path > 0 else 0.0
            )
            alignment = quality_ma3 - quality_ma15
            trend_clear = bool(
                len(quality) >= 6
                and (quality_close.iloc[-3:] > quality_middle.iloc[-3:]).all()
                and (alignment.iloc[-3:] > 0).all()
                and float(quality_ma15.iloc[-1]) > float(quality_ma15.iloc[-3])
                and float(quality_middle.iloc[-1]) > float(quality_middle.iloc[-3])
                and efficiency >= 0.45
            )
            if not trend_clear:
                return {
                    "action": "WAIT", "side": None,
                    "reason": "WAIT_DYNAMIC_TREND", "kc_upper": upper,
                    "efficiency": efficiency, "pending": None,
                }
            return {
                "action": "WAIT", "side": None,
                "reason": "WAIT_TREND_BREAK", "kc_upper": upper,
                "pending": {
                    "side": "LONG",
                    "candidate_bar_id": str(closed.index[-1]),
                    "candidate_close": latest_close,
                    "candidate_high": float(latest["high"]),
                    "candidate_low": float(latest["low"]),
                    "confirmed": False,
                },
            }

        pending = dict(pending)
        candidate_id = str(pending.get("candidate_bar_id") or "")
        positions = [
            pos for pos, bar_id in enumerate(closed.index)
            if str(bar_id) == candidate_id
        ]
        if not positions:
            return {
                "action": "WAIT", "side": None,
                "reason": "CANCEL_TREND_CONFIRM", "kc_upper": upper,
                "pending": None,
            }
        candidate_pos = positions[-1]
        confirmation = closed.iloc[candidate_pos + 1:]
        if len(confirmation) > 12:
            return {
                "action": "WAIT", "side": None,
                "reason": "CANCEL_TREND_CONFIRM_EXPIRED", "kc_upper": upper,
                "pending": None,
            }

        confirmation_middle = (
            confirmation["kc_upper"].astype(float)
            + confirmation["kc_lower"].astype(float)
        ) / 2.0
        invalidated = bool(
            (
                confirmation["close"].astype(float)
                <= confirmation_middle
            ).any()
            or (
                confirmation["ma3"].astype(float)
                <= confirmation["ma15"].astype(float)
            ).any()
        )
        if invalidated:
            return {
                "action": "WAIT", "side": None,
                "reason": "CANCEL_TREND_CONFIRM", "kc_upper": upper,
                "pending": None,
            }

        if not pending.get("confirmed"):
            if candidate_pos != len(closed) - 1:
                return {
                    "action": "WAIT", "side": None,
                    "reason": "CANCEL_TREND_CONFIRM_EXPIRED", "kc_upper": upper,
                    "pending": None,
                }
            candidate_high = float(pending["candidate_high"])
            candidate_low = float(pending["candidate_low"])
            live_middle = (upper + lower) / 2.0
            if (
                float(live["low"]) < candidate_low
                or price <= live_middle
                or float(live["ma3"]) <= float(live["ma15"])
            ):
                return {
                    "action": "WAIT", "side": None,
                    "reason": "CANCEL_TREND_CONFIRM", "kc_upper": upper,
                    "pending": None,
                }
            if price <= candidate_high:
                return {
                    "action": "WAIT", "side": None,
                    "reason": "WAIT_TREND_BREAK", "kc_upper": upper,
                    "pending": pending,
                }
            pending["confirmed"] = True
            pending["confirmed_bar_id"] = str(live.name)

        width = upper - lower
        upper_distance = price - upper
        if abs(upper_distance) <= width * 0.25:
            return {
                "action": "ENTER", "side": "LONG",
                "reason": "KC_UPPER_TREND_CONFIRMED_LONG",
                "kc_upper": upper, "pending": None,
                "turn_low": None, "turn_high": None,
            }
        if price < upper - width * 0.25:
            return {
                "action": "WAIT", "side": None,
                "reason": "CANCEL_TREND_CONFIRM", "kc_upper": upper,
                "pending": None,
            }

        # 確認後若已過熱，不追價；等一根已收盤綠 K 回踩上軌，再由緊接
        # 的即時 K 突破其高點。若下一根先破低或逾時，回踩候選作廢。
        retest_bar_id = str(pending.get("retest_bar_id") or "")
        if retest_bar_id:
            if retest_bar_id != str(closed.index[-1]):
                return {
                    "action": "WAIT", "side": None,
                    "reason": "CANCEL_TREND_RETEST", "kc_upper": upper,
                    "pending": None,
                }
            retest_high = float(pending["retest_high"])
            retest_low = float(pending["retest_low"])
            live_low = float(live["low"])
            if live_low < retest_low:
                return {
                    "action": "WAIT", "side": None,
                    "reason": "CANCEL_TREND_RETEST", "kc_upper": upper,
                    "pending": None,
                }
            if price > retest_high:
                return {
                    "action": "ENTER", "side": "LONG",
                    "reason": "KC_UPPER_RETEST_BREAK_LONG",
                    "kc_upper": upper, "pending": None,
                    "turn_low": None, "turn_high": None,
                }
            return {
                "action": "WAIT", "side": None,
                "reason": "WAIT_TREND_RETEST_BREAK", "kc_upper": upper,
                "pending": pending,
            }

        latest_open = float(latest["open"])
        latest_close = float(latest["close"])
        latest_low = float(latest["low"])
        latest_upper = float(latest["kc_upper"])
        if (
            latest_close > latest_open
            and latest_low <= latest_upper
            and latest_close >= latest_upper
        ):
            pending["retest_bar_id"] = str(closed.index[-1])
            pending["retest_high"] = float(latest["high"])
            pending["retest_low"] = latest_low
        return {
            "action": "WAIT", "side": None,
            "reason": (
                "WAIT_TREND_RETEST_BREAK"
                if pending.get("retest_bar_id") else "WAIT_TREND_RETEST"
            ),
            "kc_upper": upper, "pending": pending,
        }

    @staticmethod
    def _channel_outer_downtrend_entry_action(
        frame: pd.DataFrame, live_price: float, pending: dict | None = None,
    ) -> dict:
        """Mirror the upper-rail trend entry for lower-rail SHORT setups."""
        required = {
            "open", "high", "low", "close", "ma3", "ma15",
            "kc_upper", "kc_lower",
        }
        if frame is None or len(frame) < 6 or not required.issubset(frame.columns):
            return {"action": "WAIT", "side": None, "reason": "OUTER_DOWNTREND_DATA_UNAVAILABLE"}
        try:
            live = frame.iloc[-1]
            closed = frame.iloc[:-1].dropna(subset=list(required)).copy()
            if len(closed) < 5:
                return {
                    "action": "WAIT", "side": None,
                    "reason": "OUTER_DOWNTREND_DATA_UNAVAILABLE",
                }
            latest = closed.iloc[-1]
            price = float(live_price)
            upper = float(live["kc_upper"])
            lower = float(live["kc_lower"])
            candle_open = float(live["open"])
            candle_high = float(live["high"])
            candle_low = float(live["low"])
        except (TypeError, ValueError, IndexError):
            return {"action": "WAIT", "side": None, "reason": "OUTER_DOWNTREND_DATA_INVALID"}
        if not all(math.isfinite(value) for value in (
            price, upper, lower, candle_open, candle_high, candle_low,
        )) or lower >= upper:
            return {"action": "WAIT", "side": None, "reason": "OUTER_DOWNTREND_DATA_INVALID"}

        if not pending:
            latest_open = float(latest["open"])
            latest_close = float(latest["close"])
            latest_low = min(float(latest["low"]), latest_close)
            latest_lower = float(latest["kc_lower"])
            # 熊市下軌不再要求收盤已經跌到軌外：順勢紅 K 只要觸及
            # 下軌就建立候選，下一根必須續跌破低才開空。這可捕捉剛破
            # 軌的延續段，同時不把 V 型反彈當成空單。
            if latest_low > latest_lower or latest_close >= latest_open:
                return {
                    "action": "WAIT", "side": None,
                    "reason": "WAIT_OUTER_DOWNTREND", "kc_lower": lower,
                    "pending": None,
                }
            quality = closed.tail(6)
            quality_middle = (
                quality["kc_upper"].astype(float)
                + quality["kc_lower"].astype(float)
            ) / 2.0
            quality_close = quality["close"].astype(float)
            quality_ma3 = quality["ma3"].astype(float)
            quality_ma15 = quality["ma15"].astype(float)
            total_path = float(quality_close.diff().abs().sum())
            efficiency = (
                abs(float(quality_close.iloc[-1] - quality_close.iloc[0]))
                / total_path if total_path > 0 else 0.0
            )
            alignment = quality_ma3 - quality_ma15
            trend_clear = bool(
                len(quality) >= 6
                and (quality_close.iloc[-3:] < quality_middle.iloc[-3:]).all()
                and (alignment.iloc[-3:] < 0).all()
                and float(quality_ma15.iloc[-1]) < float(quality_ma15.iloc[-3])
                and float(quality_middle.iloc[-1]) < float(quality_middle.iloc[-3])
                and efficiency >= 0.45
            )
            if not trend_clear:
                return {
                    "action": "WAIT", "side": None,
                    "reason": "WAIT_DYNAMIC_DOWNTREND", "kc_lower": lower,
                    "efficiency": efficiency, "pending": None,
                }
            return {
                "action": "WAIT", "side": None,
                "reason": "WAIT_DOWNTREND_BREAK", "kc_lower": lower,
                "pending": {
                    "side": "SHORT",
                    "candidate_bar_id": str(closed.index[-1]),
                    "candidate_close": latest_close,
                    "candidate_high": float(latest["high"]),
                    "candidate_low": float(latest["low"]),
                    "confirmed": False,
                },
            }

        pending = dict(pending)
        candidate_id = str(pending.get("candidate_bar_id") or "")
        positions = [
            pos for pos, bar_id in enumerate(closed.index)
            if str(bar_id) == candidate_id
        ]
        if not positions:
            return {
                "action": "WAIT", "side": None,
                "reason": "CANCEL_DOWNTREND_CONFIRM", "kc_lower": lower,
                "pending": None,
            }
        candidate_pos = positions[-1]
        confirmation = closed.iloc[candidate_pos + 1:]
        if len(confirmation) > 12:
            return {
                "action": "WAIT", "side": None,
                "reason": "CANCEL_DOWNTREND_CONFIRM_EXPIRED",
                "kc_lower": lower, "pending": None,
            }

        confirmation_middle = (
            confirmation["kc_upper"].astype(float)
            + confirmation["kc_lower"].astype(float)
        ) / 2.0
        invalidated = bool(
            (confirmation["close"].astype(float) >= confirmation_middle).any()
            or (
                confirmation["ma3"].astype(float)
                >= confirmation["ma15"].astype(float)
            ).any()
        )
        if invalidated:
            return {
                "action": "WAIT", "side": None,
                "reason": "CANCEL_DOWNTREND_CONFIRM", "kc_lower": lower,
                "pending": None,
            }

        if not pending.get("confirmed"):
            if candidate_pos != len(closed) - 1:
                return {
                    "action": "WAIT", "side": None,
                    "reason": "CANCEL_DOWNTREND_CONFIRM_EXPIRED",
                    "kc_lower": lower, "pending": None,
                }
            candidate_high = float(pending["candidate_high"])
            candidate_low = float(pending["candidate_low"])
            live_middle = (upper + lower) / 2.0
            if (
                float(live["high"]) > candidate_high
                or price >= live_middle
                or float(live["ma3"]) >= float(live["ma15"])
            ):
                return {
                    "action": "WAIT", "side": None,
                    "reason": "CANCEL_DOWNTREND_CONFIRM",
                    "kc_lower": lower, "pending": None,
                }
            if price >= candidate_low:
                return {
                    "action": "WAIT", "side": None,
                    "reason": "WAIT_DOWNTREND_BREAK",
                    "kc_lower": lower, "pending": pending,
                }
            pending["confirmed"] = True
            pending["confirmed_bar_id"] = str(live.name)

        width = upper - lower
        lower_distance = lower - price
        if abs(lower_distance) <= width * 0.25:
            return {
                "action": "ENTER", "side": "SHORT",
                "reason": "KC_LOWER_TREND_CONFIRMED_SHORT",
                "kc_lower": lower, "pending": None,
                "turn_low": None, "turn_high": None,
            }
        if price > lower + width * 0.25:
            return {
                "action": "WAIT", "side": None,
                "reason": "CANCEL_DOWNTREND_CONFIRM",
                "kc_lower": lower, "pending": None,
            }

        # 過熱時不追空；等待已收盤紅 K 回抽下軌，再由緊接的即時 K
        # 跌破其低點。若下一根先破高或逾時，回抽候選作廢。
        retest_bar_id = str(pending.get("retest_bar_id") or "")
        if retest_bar_id:
            if retest_bar_id != str(closed.index[-1]):
                return {
                    "action": "WAIT", "side": None,
                    "reason": "CANCEL_DOWNTREND_RETEST",
                    "kc_lower": lower, "pending": None,
                }
            retest_high = float(pending["retest_high"])
            retest_low = float(pending["retest_low"])
            if float(live["high"]) > retest_high:
                return {
                    "action": "WAIT", "side": None,
                    "reason": "CANCEL_DOWNTREND_RETEST",
                    "kc_lower": lower, "pending": None,
                }
            if price < retest_low:
                return {
                    "action": "ENTER", "side": "SHORT",
                    "reason": "KC_LOWER_RETEST_BREAK_SHORT",
                    "kc_lower": lower, "pending": None,
                    "turn_low": None, "turn_high": None,
                }
            return {
                "action": "WAIT", "side": None,
                "reason": "WAIT_DOWNTREND_RETEST_BREAK",
                "kc_lower": lower, "pending": pending,
            }

        latest_open = float(latest["open"])
        latest_close = float(latest["close"])
        latest_high = float(latest["high"])
        latest_lower = float(latest["kc_lower"])
        if (
            latest_close < latest_open
            and latest_high >= latest_lower
            and latest_close <= latest_lower
        ):
            pending["retest_bar_id"] = str(closed.index[-1])
            pending["retest_high"] = latest_high
            pending["retest_low"] = float(latest["low"])
        return {
            "action": "WAIT", "side": None,
            "reason": (
                "WAIT_DOWNTREND_RETEST_BREAK"
                if pending.get("retest_bar_id") else "WAIT_DOWNTREND_RETEST"
            ),
            "kc_lower": lower, "pending": pending,
        }

    @staticmethod
    def _channel_outer_trend_entry_action(
        frame: pd.DataFrame, live_price: float, pending: dict | None = None,
    ) -> dict:
        """Dispatch symmetric KC outer-trend entries by candidate direction."""
        if str((pending or {}).get("side") or "").upper() == "SHORT":
            return TradingEngine._channel_outer_downtrend_entry_action(
                frame, live_price, pending,
            )
        if not pending:
            required = {"close", "kc_lower"}
            if (
                frame is not None and len(frame) >= 2
                and required.issubset(frame.columns)
            ):
                try:
                    latest = frame.iloc[-2]
                    if min(float(latest["low"]), float(latest["close"])) <= float(latest["kc_lower"]):
                        return TradingEngine._channel_outer_downtrend_entry_action(
                            frame, live_price,
                        )
                except (TypeError, ValueError, IndexError):
                    pass
        return TradingEngine._channel_outer_uptrend_entry_action(
            frame, live_price, pending,
        )

    @staticmethod
    def _channel_live_inner_reentry_action(
        frame, live_price, current_side
    ) -> dict:
        """回到 KC 內先平倉；半寬且有量能時才反手。"""
        import core.config as config
        if frame is None or len(frame) < 2:
            return {"action": "WAIT"}

        try:
            live = frame.iloc[-1]
            prev = frame.iloc[-2]

            upper = float(live["kc_upper"])
            lower = float(live["kc_lower"])
            width = upper - lower
            if width <= 0: return {"action": "WAIT"}

            price = float(live_price)
            live_open = float(live["open"])
            prev_open = float(prev["open"])
            prev_close = float(prev["close"])
            volume = float(live.get("volume") or 0.0)
            volume_ma = float(live.get("vol_ma_20") or 0.0)
            volume_ratio = volume / volume_ma if volume_ma > 0 else 0.0
            reversal_volume_ok = volume_ratio >= config.KELTNER_MIN_VOLUME_RATIO

            side = str(current_side or "").upper()

            # Short entry logic
            if side in ("", "LONG"):
                # Single candle condition
                single_condition = (
                    live_open >= upper and price < upper
                    and (upper - price) >= 0.5 * width
                )

                # Two candle condition
                two_candle_condition = (
                    prev_close < prev_open and price < live_open
                    and prev_close <= upper and price >= lower
                    and ((prev_open - prev_close) + (live_open - price)) >= 0.5 * width
                )

                if single_condition or two_candle_condition:
                    return {
                        "action": (
                            "ENTER" if not side else "REVERSE"
                            if reversal_volume_ok else "EXIT"
                        ),
                        "side": "SHORT" if (not side or reversal_volume_ok) else None,
                        "reason": (
                            "LIVE_INNER_REENTRY_SHORT" if not side
                            else "INSTANT_INNER_REENTRY_REVERSE_SHORT"
                            if reversal_volume_ok
                            else "INSTANT_INNER_REENTRY_EXIT_LONG_LOW_VOLUME"
                        ),
                    }

            # Long entry logic
            if side in ("", "SHORT"):
                # Single candle condition
                single_condition = (
                    live_open <= lower and price > lower
                    and (price - lower) >= 0.5 * width
                )

                # Two candle condition
                two_candle_condition = (
                    prev_close > prev_open and price > live_open
                    and prev_close >= lower and price <= upper
                    and ((prev_close - prev_open) + (price - live_open)) >= 0.5 * width
                )

                if single_condition or two_candle_condition:
                    return {
                        "action": (
                            "ENTER" if not side else "REVERSE"
                            if reversal_volume_ok else "EXIT"
                        ),
                        "side": "LONG" if (not side or reversal_volume_ok) else None,
                        "reason": (
                            "LIVE_INNER_REENTRY_LONG" if not side
                            else "INSTANT_INNER_REENTRY_REVERSE_LONG"
                            if reversal_volume_ok
                            else "INSTANT_INNER_REENTRY_EXIT_SHORT_LOW_VOLUME"
                        ),
                    }

        except (ValueError, TypeError, KeyError):
            pass

        return {"action": "WAIT"}

    @staticmethod
    def _channel_outer_reentry_exit_action(
        frame: pd.DataFrame, live_price: float, current_side: str | None,
    ) -> dict:
        """Flatten a KC outer-run position when its opposite candle returns inside."""
        required = {"open", "close", "ma3", "kc_upper", "kc_lower"}
        if frame is None or len(frame) < 2 or not required.issubset(frame.columns):
            return {"action": "HOLD", "side": None, "reason": "KC_REENTRY_EXIT_DATA_UNAVAILABLE"}
        try:
            live = frame.iloc[-1]
            previous = frame.iloc[-2]
            price = float(live_price)
            live_open = float(live["open"])
            upper = float(live["kc_upper"])
            lower = float(live["kc_lower"])
            previous_upper = float(previous["kc_upper"])
            previous_lower = float(previous["kc_lower"])
            previous_close = float(previous["close"])
            ma3_now = float(live["ma3"])
            ma3_previous = float(previous["ma3"])
        except (TypeError, ValueError, IndexError, KeyError):
            return {"action": "HOLD", "side": None, "reason": "KC_REENTRY_EXIT_DATA_INVALID"}
        values = (
            price, live_open, upper, lower, previous_upper, previous_lower,
            previous_close, ma3_now, ma3_previous,
        )
        if not all(math.isfinite(value) for value in values) or lower >= upper:
            return {"action": "HOLD", "side": None, "reason": "KC_REENTRY_EXIT_DATA_INVALID"}

        side = str(current_side or "").upper()
        if side == "LONG":
            # 上軌外追多後，只要即時紅 K 回到通道內就先平多。
            red_inside = (
                lower <= price < upper and price < live_open
            )
            came_from_upper_outer = live_open >= upper or previous_close >= previous_upper
            if came_from_upper_outer and red_inside:
                return {
                    "action": "EXIT", "side": None,
                    "reason": "KC_UPPER_RED_REENTRY_EXIT",
                    "kc_upper": upper, "kc_lower": lower,
                }
        elif side == "SHORT":
            # 多空鏡像：下軌外追空後，綠 K 一回到通道內就先平空。
            green_inside = (
                lower < price <= upper and price > live_open
            )
            came_from_lower_outer = live_open <= lower or previous_close <= previous_lower
            if came_from_lower_outer and green_inside:
                return {
                    "action": "EXIT", "side": None,
                    "reason": "KC_LOWER_GREEN_REENTRY_EXIT",
                    "kc_upper": upper, "kc_lower": lower,
                }
        return {"action": "HOLD", "side": None, "reason": "WAIT_KC_REENTRY_EXIT"}


    @staticmethod
    def _channel_live_outer_entry_action(
        frame: pd.DataFrame, live_price: float,
    ) -> dict:
        """Detect a live KC rail touch only for the same-bar rechase exception."""
        required = {"kc_upper", "kc_lower"}
        if frame is None or frame.empty or not required.issubset(frame.columns):
            return {
                "action": "WAIT", "side": None,
                "reason": "KC_LIVE_OUTER_DATA_UNAVAILABLE",
            }
        try:
            live = frame.iloc[-1]
            price = float(live_price)
            upper = float(live["kc_upper"])
            lower = float(live["kc_lower"])
            candle_open = float(live["open"])
            candle_high = max(float(live["high"]), price)
            candle_low = min(float(live["low"]), price)
        except (TypeError, ValueError, IndexError):
            return {
                "action": "WAIT", "side": None,
                "reason": "KC_LIVE_OUTER_DATA_INVALID",
            }
        if not all(math.isfinite(value) for value in (price, upper, lower)) or lower >= upper:
            return {
                "action": "WAIT", "side": None,
                "reason": "KC_LIVE_OUTER_DATA_INVALID",
            }
        if price >= upper:
            return {
                "action": "ENTER", "side": "LONG",
                "reason": "KC_LIVE_UPPER_BREAK_LONG",
                "kc_upper": upper, "kc_lower": lower,
                "turn_low": None, "turn_high": None,
            }
        if price <= lower:
            return {
                "action": "ENTER", "side": "SHORT",
                "reason": "KC_LIVE_LOWER_BREAK_SHORT",
                "kc_upper": upper, "kc_lower": lower,
                "turn_low": None, "turn_high": None,
            }
        return {
            "action": "WAIT", "side": None,
            "reason": "WAIT_LIVE_OUTER_BREAK",
            "kc_upper": upper, "kc_lower": lower,
        }

    @staticmethod
    def _channel_opposite_outer_trend_action(
        frame: pd.DataFrame, live_price: float, current_side: str | None,
    ) -> dict:
        """Reverse a wrong-side position after three directional outer bars."""
        required = {"open", "close", "ma3", "ma15", "kc_upper", "kc_lower"}
        if frame is None or len(frame) < 3 or not required.issubset(frame.columns):
            return {"action": "HOLD", "side": None, "reason": "OUTER_TREND_DATA_UNAVAILABLE"}
        try:
            recent = frame.iloc[-3:].copy()
            opens = recent["open"].astype(float).tolist()
            closes = recent["close"].astype(float).tolist()
            closes[-1] = float(live_price)
            ma3 = recent["ma3"].astype(float).tolist()
            ma15 = recent["ma15"].astype(float).tolist()
            upper = recent["kc_upper"].astype(float).tolist()
            lower = recent["kc_lower"].astype(float).tolist()
        except (TypeError, ValueError, IndexError):
            return {"action": "HOLD", "side": None, "reason": "OUTER_TREND_DATA_INVALID"}
        values = opens + closes + ma3 + ma15 + upper + lower
        if not all(math.isfinite(value) for value in values):
            return {"action": "HOLD", "side": None, "reason": "OUTER_TREND_DATA_INVALID"}

        side = str(current_side or "").upper()
        if side == "SHORT":
            uptrend = bool(
                all(close > open_ for open_, close in zip(opens, closes))
                and closes[0] < closes[1] < closes[2]
                and ma3[-2] >= upper[-2] and ma3[-1] >= upper[-1]
                and ma3[-1] > ma3[-2] and ma3[-1] > ma15[-1]
                and closes[-1] >= upper[-1]
            )
            if uptrend:
                return {
                    "action": "REVERSE", "side": "LONG",
                    "reason": "OPPOSITE_UPPER_OUTER_UPTREND",
                }
        elif side == "LONG":
            downtrend = bool(
                all(close < open_ for open_, close in zip(opens, closes))
                and closes[0] > closes[1] > closes[2]
                and ma3[-2] <= lower[-2] and ma3[-1] <= lower[-1]
                and ma3[-1] < ma3[-2] and ma3[-1] < ma15[-1]
                and closes[-1] <= lower[-1]
            )
            if downtrend:
                return {
                    "action": "REVERSE", "side": "SHORT",
                    "reason": "OPPOSITE_LOWER_OUTER_DOWNTREND",
                }
        return {
            "action": "HOLD", "side": None,
            "reason": "WAIT_OPPOSITE_OUTER_TREND",
        }

    @staticmethod
    def _channel_is_immediate_outer_rechase(reason: str | None) -> bool:
        return str(reason or "") in {
            "SAME_BAR_UPPER_OUTER_RECHASE",
            "SAME_BAR_LOWER_OUTER_RECHASE",
        }

    @staticmethod
    def _channel_same_bar_outer_rechase_action(
        frame: pd.DataFrame, live_price: float, current_side: str | None,
        last_reverse_bar: object,
    ) -> dict:
        """Immediately chase the opposite live rail after a same-bar reversal."""
        if (
            frame is None or len(frame) < 2
            or last_reverse_bar != frame.index[-2]
        ):
            return {
                "action": "HOLD", "side": None,
                "reason": "WAIT_SAME_BAR_OUTER_RECHASE",
            }
        live_outer = TradingEngine._channel_live_outer_entry_action(
            frame, live_price,
        )
        side = str(current_side or "").upper()
        target_side = str(live_outer.get("side") or "").upper()
        if live_outer.get("action") != "ENTER" or target_side == side:
            return {
                "action": "HOLD", "side": None,
                "reason": "WAIT_SAME_BAR_OUTER_RECHASE",
            }
        if side == "SHORT" and target_side == "LONG":
            live_outer.update({
                "action": "REVERSE",
                "reason": "SAME_BAR_UPPER_OUTER_RECHASE",
            })
            return live_outer
        if side == "LONG" and target_side == "SHORT":
            live_outer.update({
                "action": "REVERSE",
                "reason": "SAME_BAR_LOWER_OUTER_RECHASE",
            })
            return live_outer
        return {
            "action": "HOLD", "side": None,
            "reason": "WAIT_SAME_BAR_OUTER_RECHASE",
        }

    def _record_channel_chop_event(
        self, symbol: str, event: str, frame: pd.DataFrame,
        direction: str | None = None, live_bar: bool = False,
    ) -> None:
        """Keep recent lock/unlock transitions for chart markers."""
        try:
            row = frame.iloc[-1 if live_bar else -2]
            timestamp_ms = int(float(row["timestamp"]))
        except (TypeError, ValueError, IndexError, KeyError):
            timestamp_ms = int(time.time() * 1000)
        event_name = str(event or "").upper()
        direction_name = str(direction or "").upper()
        action = (
            "CHOP_LOCK" if event_name == "LOCK"
            else f"CHOP_UNLOCK_{direction_name}"
            if direction_name in ("LONG", "SHORT")
            else "CHOP_UNLOCK"
        )
        events = self._channel_chop_events.setdefault(symbol, [])
        if events and events[-1].get("action") == action and int(events[-1].get("timestamp") or 0) == timestamp_ms:
            return
        events.append({
            "timestamp": timestamp_ms,
            "action": action,
            "reason": event_name,
        })
        del events[:-100]

    def _record_channel_signal_event(
        self, symbol: str, reason: str, frame: pd.DataFrame,
    ) -> None:
        """Record visible Channel Swing state transitions without log spam."""
        reason_name = str(reason or "").strip()
        if not reason_name:
            return
        labels = {
            "KC data unavailable": "KC資料不足",
            "KC data invalid": "KC資料無效",
            "KC channel invalid": "KC通道無效",
            "WAIT_ADJACENT_OUTER_CANDIDATE": "等待緊接的外軌候選K",
            "WAIT_CLOSE_GREEN": "觸下軌，等待綠K收盤",
            "WAIT_CLOSE_RED": "觸上軌，等待紅K收盤",
            "WAIT_BREAK_HIGH": "多方候選成立，等待下一根破高",
            "WAIT_BREAK_LOW": "空方候選成立，等待下一根破低",
            "CANCEL_LONG": "多方候選先破低，已取消",
            "CANCEL_SHORT": "空方候選先破高，已取消",
            "V_TOO_CLOSE_KC": "V線離KC外軌太近",
            "KC_REENTRY_TOO_SHALLOW": "站回KC通道幅度不足",
            "KC_WIDTH_TOO_NARROW": "KC寬度不足設定門檻",
            "CHOP_WAIT_NO_ENTRY": "CHOP_WAIT阻擋一般峰谷進場",
            "CHOP_WAIT_NO_MOMENTUM": "CHOP_WAIT等待有效動能",
            "WAIT_CHOP_MOMENTUM_BREAK": "CHOP動能候選等待下一根確認",
            "CANCEL_CHOP_BREAKOUT": "CHOP動能突破候選已取消",
            "WAIT_DYNAMIC_TREND": "上軌外多方趨勢品質不足",
            "WAIT_DYNAMIC_DOWNTREND": "下軌外空方趨勢品質不足",
            "WAIT_TREND_BREAK": "上軌多方動能等待下一根破高",
            "WAIT_DOWNTREND_BREAK": "下軌空方動能等待下一根破低",
            "WAIT_TREND_RETEST": "多方過熱，等待回踩上軌",
            "WAIT_DOWNTREND_RETEST": "空方過熱，等待回抽下軌",
            "WAIT_TREND_RETEST_BREAK": "上軌回踩成立，等待破高",
            "WAIT_DOWNTREND_RETEST_BREAK": "下軌回抽成立，等待破低",
            "CANCEL_TREND_CONFIRM": "多方趨勢候選確認失敗",
            "CANCEL_TREND_CONFIRM_EXPIRED": "多方趨勢候選逾時",
            "CANCEL_TREND_RETEST": "多方回踩候選已取消",
            "CANCEL_DOWNTREND_CONFIRM": "空方趨勢候選確認失敗",
            "CANCEL_DOWNTREND_CONFIRM_EXPIRED": "空方趨勢候選逾時",
            "CANCEL_DOWNTREND_RETEST": "空方回抽候選已取消",
        }
        label = labels.get(reason_name, reason_name)
        cancel_reason = (
            reason_name.startswith("CANCEL_")
            or reason_name in ("CANCEL_LONG", "CANCEL_SHORT")
        )
        block_reason = (
            reason_name in (
                "KC data unavailable", "KC data invalid", "KC channel invalid",
                "V_TOO_CLOSE_KC", "KC_REENTRY_TOO_SHALLOW",
                "KC_WIDTH_TOO_NARROW", "CHOP_WAIT_NO_ENTRY",
            )
            or reason_name.endswith("_DATA_INVALID")
        )
        action = (
            "CHANNEL_CANCEL" if cancel_reason
            else "CHANNEL_BLOCK" if block_reason
            else "CHANNEL_WAIT"
        )
        try:
            timestamp_ms = int(float(frame.iloc[-1]["timestamp"]))
        except (TypeError, ValueError, IndexError, KeyError):
            timestamp_ms = int(time.time() * 1000)
        events = self._channel_signal_events.setdefault(symbol, [])
        if events and int(events[-1].get("timestamp") or 0) == timestamp_ms:
            if events[-1].get("reason") == reason_name:
                return
            events[-1] = {
                "timestamp": timestamp_ms, "action": action,
                "reason": reason_name, "label": label,
            }
        elif events and events[-1].get("reason") == reason_name:
            return
        else:
            events.append({
                "timestamp": timestamp_ms, "action": action,
                "reason": reason_name, "label": label,
            })
            del events[:-100]
        level = "WARNING" if action != "CHANNEL_WAIT" else "INFO"
        self.account.log(
            f"🧭 [Channel Swing狀態] {symbol} {label} ({reason_name})",
            level,
        )

    @staticmethod
    def _channel_chop_state(frame: pd.DataFrame) -> dict:
        """Detect closed-bar whipsaw and require a three-bar directional unlock."""
        result = {
            "detected": False,
            "clear_direction": None,
            "reason": "CHOP_DATA_UNAVAILABLE",
        }
        required = {"close", "ma3", "ma15", "kc_upper", "kc_lower"}
        if frame is None or len(frame) < 14 or not required.issubset(frame.columns):
            return result

        # 最後一根是即時 K；盤整與解鎖一律只看已收盤 K。
        closed = frame.iloc[:-1].copy()
        closed = closed.dropna(subset=list(required)).tail(12)
        if len(closed) < 12:
            return result

        close = closed["close"].astype(float)
        ma3 = closed["ma3"].astype(float)
        ma15 = closed["ma15"].astype(float)
        upper = closed["kc_upper"].astype(float)
        lower = closed["kc_lower"].astype(float)
        middle = (upper + lower) / 2.0
        width = (upper - lower).abs()
        median_width = max(
            float(width.median()), abs(float(close.iloc[-1])) * 1e-12,
        )

        def _cross_count(values: pd.Series) -> int:
            signs = values.apply(
                lambda value: 1 if value > 0 else -1 if value < 0 else 0
            )
            signs = signs.astype("Float64").mask(signs == 0).ffill().bfill().dropna()
            return int((signs.diff().abs() == 2).sum())

        ma_crosses = _cross_count(ma3 - ma15)
        middle_crosses = _cross_count(close - middle)
        total_path = float(close.diff().abs().sum())
        efficiency = (
            abs(float(close.iloc[-1] - close.iloc[0])) / total_path
            if total_path > 0 else 0.0
        )
        chop_votes = sum((
            ma_crosses >= 3,
            middle_crosses >= 3,
            efficiency <= 0.30,
        ))

        last_three = closed.iloc[-3:]
        last_three_middle = (
            last_three["kc_upper"].astype(float)
            + last_three["kc_lower"].astype(float)
        ) / 2.0
        clear_long = bool(
            (last_three["close"].astype(float) > last_three_middle).all()
            and (
                last_three["ma3"].astype(float)
                > last_three["ma15"].astype(float)
            ).all()
            and float(middle.iloc[-1] - middle.iloc[-4]) >= median_width * 0.08
            and float(ma15.iloc[-1] - ma15.iloc[-4]) >= median_width * 0.08
            and efficiency >= 0.55
        )
        clear_short = bool(
            (last_three["close"].astype(float) < last_three_middle).all()
            and (
                last_three["ma3"].astype(float)
                < last_three["ma15"].astype(float)
            ).all()
            and float(middle.iloc[-1] - middle.iloc[-4]) <= -median_width * 0.08
            and float(ma15.iloc[-1] - ma15.iloc[-4]) <= -median_width * 0.08
            and efficiency >= 0.55
        )
        latest = closed.iloc[-1]
        latest_middle = float(middle.iloc[-1])
        latest_width = float(width.iloc[-1])
        latest_open = float(latest.get("open", latest["close"]))
        latest_close = float(latest["close"])
        latest_body = abs(latest_close - latest_open)
        strong_long = bool(
            latest_open < latest_middle < latest_close
            and latest_body >= latest_width * 0.25
            and float(latest["ma3"]) > float(latest["ma15"])
        )
        strong_short = bool(
            latest_open > latest_middle > latest_close
            and latest_body >= latest_width * 0.25
            and float(latest["ma3"]) < float(latest["ma15"])
        )
        clear_direction = (
            "LONG" if clear_long or strong_long
            else "SHORT" if clear_short or strong_short else None
        )
        detected = bool(chop_votes >= 2 and clear_direction is None)
        return {
            "detected": detected,
            "clear_direction": clear_direction,
            "reason": (
                "CHOP_WAIT" if detected
                else "DIRECTION_CLEAR" if clear_direction else "NO_CHOP"
            ),
            "ma_crosses": ma_crosses,
            "middle_crosses": middle_crosses,
            "efficiency": efficiency,
            "chop_votes": chop_votes,
        }

    @staticmethod
    def _channel_chop_breakout_action(
        frame: pd.DataFrame, live_price: float,
    ) -> dict:
        """Allow a confirmed range breakout to release CHOP_WAIT at its source."""
        required = {"open", "high", "low", "close", "ma3", "ma15", "kc_upper", "kc_lower"}
        if frame is None or len(frame) < 12 or not required.issubset(frame.columns):
            return {"action": "WAIT", "side": None, "reason": "CHOP_BREAKOUT_DATA_UNAVAILABLE"}
        try:
            live = frame.iloc[-1]
            closed = frame.iloc[:-1].dropna(subset=list(required)).copy()
            if len(closed) < 10:
                raise ValueError("not enough closed candles")
            candidate = closed.iloc[-1]
            history = closed.iloc[-9:-1]
            price = float(live_price)
            upper = float(live["kc_upper"])
            lower = float(live["kc_lower"])
            live_middle = (upper + lower) / 2.0
            candidate_open = float(candidate["open"])
            candidate_close = float(candidate["close"])
            candidate_high = float(candidate["high"])
            candidate_low = float(candidate["low"])
            candidate_middle = (float(candidate["kc_upper"]) + float(candidate["kc_lower"])) / 2.0
            previous_middle = (float(closed.iloc[-2]["kc_upper"]) + float(closed.iloc[-2]["kc_lower"])) / 2.0
            median_width = float((history["kc_upper"].astype(float) - history["kc_lower"].astype(float)).abs().median())
        except (TypeError, ValueError, IndexError):
            return {"action": "WAIT", "side": None, "reason": "CHOP_BREAKOUT_DATA_INVALID"}
        values = (price, upper, lower, live_middle, candidate_open, candidate_close, candidate_high, candidate_low, candidate_middle, previous_middle, median_width)
        if not all(math.isfinite(value) for value in values) or lower >= upper:
            return {"action": "WAIT", "side": None, "reason": "CHOP_BREAKOUT_DATA_INVALID"}

        closes = pd.concat([history["close"].astype(float).tail(5), pd.Series([candidate_close])], ignore_index=True)
        total_path = float(closes.diff().abs().sum())
        efficiency = abs(float(closes.iloc[-1] - closes.iloc[0])) / total_path if total_path > 0 else 0.0
        body = abs(candidate_close - candidate_open)
        body_ready = body >= max(median_width * 0.12, abs(candidate_close) * 1e-12)
        prior_high = float(history["high"].astype(float).max())
        prior_low = float(history["low"].astype(float).min())
        candidate_ma3 = float(candidate["ma3"])
        candidate_ma15 = float(candidate["ma15"])
        previous_ma15 = float(closed.iloc[-2]["ma15"])
        long_candidate = bool(
            candidate_close > candidate_open and candidate_close > prior_high
            and candidate_close > candidate_middle and candidate_ma3 > candidate_ma15
            and candidate_ma15 > previous_ma15 and candidate_middle > previous_middle
            and body_ready and efficiency >= 0.45
        )
        short_candidate = bool(
            candidate_close < candidate_open and candidate_close < prior_low
            and candidate_close < candidate_middle and candidate_ma3 < candidate_ma15
            and candidate_ma15 < previous_ma15 and candidate_middle < previous_middle
            and body_ready and efficiency >= 0.45
        )
        side = "LONG" if long_candidate else "SHORT" if short_candidate else None
        if not side:
            return {"action": "WAIT", "side": None, "reason": "CHOP_WAIT_NO_MOMENTUM", "kc_upper": upper, "kc_lower": lower}

        live_ma3 = float(live["ma3"])
        live_ma15 = float(live["ma15"])
        if side == "LONG":
            if float(live["low"]) < candidate_low or live_ma3 <= live_ma15 or price <= live_middle:
                return {"action": "WAIT", "side": None, "reason": "CANCEL_CHOP_BREAKOUT", "kc_upper": upper, "kc_lower": lower}
            confirmed = price > candidate_high
        else:
            if float(live["high"]) > candidate_high or live_ma3 >= live_ma15 or price >= live_middle:
                return {"action": "WAIT", "side": None, "reason": "CANCEL_CHOP_BREAKOUT", "kc_upper": upper, "kc_lower": lower}
            confirmed = price < candidate_low
        if not confirmed:
            return {"action": "WAIT", "side": None, "reason": "WAIT_CHOP_MOMENTUM_BREAK", "candidate_side": side, "kc_upper": upper, "kc_lower": lower}
        return {
            "action": "ENTER", "side": side, "reason": f"CHOP_BREAKOUT_{side}",
            "kc_upper": upper, "kc_lower": lower,
            "turn_low": candidate_low if side == "LONG" else None,
            "turn_high": candidate_high if side == "SHORT" else None,
        }

    @staticmethod
    def _channel_entry_reuses_exit_bar(
        action: str, has_position: bool, closed_bar_id: object,
        last_exit_bar: object,
    ) -> bool:
        """Block a flat-position entry from reusing the K that just exited."""
        return bool(
            action == "ENTER"
            and not has_position
            and closed_bar_id is not None
            and closed_bar_id == last_exit_bar
        )



    @staticmethod
    def _channel_upper_red_short_reversal_allowed(
        frame: pd.DataFrame, live_price: float,
    ) -> bool:
        """Allow reversal only for a green live candle at or above KC upper rail."""
        required = {"open", "kc_upper"}
        if frame is None or frame.empty or not required.issubset(frame.columns):
            return False
        try:
            live = frame.iloc[-1]
            return bool(
                float(live_price) >= float(live["kc_upper"])
                and float(live_price) > float(live["open"])
            )
        except (TypeError, ValueError, IndexError, KeyError):
            return False

    @staticmethod
    def _channel_is_upper_red_peak_short(position: dict) -> bool:
        """Identify a Channel Swing short originated by an upper-rail red peak."""
        try:
            return bool(
                str(position.get("side") or "").upper() == "SHORT"
                and str(position.get("entry_mode") or "").upper() == "CHANNEL_SWING"
                and float(position.get("channel_turn_high") or 0.0) > 0.0
            )
        except (TypeError, ValueError, AttributeError):
            return False

    @staticmethod
    def _channel_three_candle_exit_action(
        frame: pd.DataFrame, current_side: str | None,
    ) -> dict:
        """Flatten after three closed opposite candles continue against a position."""
        required = {"open", "close"}
        if frame is None or len(frame) < 4 or not required.issubset(frame.columns):
            return {"action": "HOLD", "side": None, "reason": "THREE_CANDLE_EXIT_DATA_UNAVAILABLE"}
        side = str(current_side or "").upper()
        if side not in ("LONG", "SHORT"):
            return {"action": "HOLD", "side": None, "reason": "THREE_CANDLE_EXIT_NO_POSITION"}
        try:
            # The last row is live and must never count toward the three-candle exit.
            closed = frame.iloc[-4:-1]
            opens = [float(value) for value in closed["open"]]
            closes = [float(value) for value in closed["close"]]
        except (TypeError, ValueError, IndexError, KeyError):
            return {"action": "HOLD", "side": None, "reason": "THREE_CANDLE_EXIT_DATA_INVALID"}
        if not all(math.isfinite(value) for value in (*opens, *closes)):
            return {"action": "HOLD", "side": None, "reason": "THREE_CANDLE_EXIT_DATA_INVALID"}

        if (
            side == "LONG"
            and all(close < open_ for open_, close in zip(opens, closes))
            and closes[0] > closes[1] > closes[2]
        ):
            return {
                "action": "EXIT", "side": None,
                "reason": "THREE_RED_FALLING_CLOSE_EXIT_LONG",
            }
        if (
            side == "SHORT"
            and all(close > open_ for open_, close in zip(opens, closes))
            and closes[0] < closes[1] < closes[2]
        ):
            return {
                "action": "EXIT", "side": None,
                "reason": "THREE_GREEN_RISING_CLOSE_EXIT_SHORT",
            }
        return {"action": "HOLD", "side": None, "reason": "WAIT_THREE_CANDLE_EXIT"}


    @staticmethod
    def _channel_exit_requests_rotation(reason: str | None) -> bool:
        return str(reason or "") in {
            "KC_UPPER_RED_REENTRY_EXIT",
            "KC_LOWER_GREEN_REENTRY_EXIT",
            "THREE_RED_FALLING_CLOSE_EXIT_LONG",
            "THREE_GREEN_RISING_CLOSE_EXIT_SHORT",
        }


    @staticmethod
    def _channel_chop_gate(
        action: str, target_side: str | None, locked: bool,
        has_position: bool, reason: str | None = None,
    ) -> tuple[str, str | None, str | None]:
        """Block chop exposure except an immediate same-bar outer rechase."""
        if (
            action == "REVERSE"
            and TradingEngine._channel_is_immediate_outer_rechase(reason)
        ):
            return action, target_side, None
        if not locked:
            return action, target_side, None
        if action == "ENTER":
            return "WAIT", None, "CHOP_WAIT_NO_ENTRY"
        if action == "REVERSE" and has_position:
            return "EXIT", None, "CHOP_WAIT_CLOSE_ONLY"
        return action, target_side, None

    @staticmethod
    def _channel_swing_action(
        frame: pd.DataFrame, live_price: float, current_side: str | None = None,
        entry_turn_low: float | None = None,
        entry_turn_high: float | None = None,
        market_mode: str | None = None,
    ) -> dict:
        import core.config as config
        """空手只做 KC 外軌峰谷轉向；持倉則依對側峰谷平倉或反手。"""
        required = {"open", "high", "low", "close", "ma3", "kc_upper", "kc_lower"}
        if frame is None or len(frame) < 20 or not required.issubset(frame.columns):
            return {"action": "WAIT", "reason": "KC data unavailable"}
        row = frame.iloc[-1]
        # -3 是候選已收盤 K，-2 是緊接且已完整收盤的確認 K，-1 只提供
        # 當前價格與 KC 狀態。禁止用尚未收盤、可能變色的即時 K 確認峰谷。
        # 候選只限緊接的第二根 K；若第二根未保持同向顏色，訊號失效。
        signal_pos = len(frame) - 3
        try:
            signal_row = frame.iloc[signal_pos]
            confirmation_row = frame.iloc[-2]

            price = float(live_price)
            upper = float(row["kc_upper"])
            lower = float(row["kc_lower"])
            signal_upper = float(signal_row["kc_upper"])
            signal_lower = float(signal_row["kc_lower"])
            continuation_rows = frame.iloc[-1:]
            live_low = min(
                float(pd.to_numeric(continuation_rows["low"], errors="coerce").min()),
                price,
            )
            live_high = max(
                float(pd.to_numeric(continuation_rows["high"], errors="coerce").max()),
                price,
            )
            live_close = float(row["close"])
            signal_open = float(signal_row["open"])
            signal_close = float(signal_row["close"])
            signal_low = float(signal_row["low"])
            signal_high = float(signal_row["high"])
            confirmation_open = float(confirmation_row["open"])
            confirmation_close = float(confirmation_row["close"])
            confirmation_low = float(confirmation_row["low"])
            confirmation_high = float(confirmation_row["high"])
            confirmation_upper = float(confirmation_row["kc_upper"])
            confirmation_lower = float(confirmation_row["kc_lower"])
            live_ma3 = float(row["ma3"])
            signal_ma3 = float(signal_row["ma3"])
            previous_ma3 = float(frame.iloc[-2]["ma3"])
        except (TypeError, ValueError):
            return {"action": "WAIT", "reason": "KC data invalid"}
        if (
            not all(math.isfinite(value) for value in (
                price, upper, lower, signal_upper, signal_lower,
                live_low, live_high, live_close, signal_open, signal_close,
                signal_low, signal_high, live_ma3, signal_ma3, previous_ma3,
                confirmation_open, confirmation_close,
                confirmation_low, confirmation_high,
                confirmation_upper, confirmation_lower,
            ))
            or lower >= upper or signal_lower >= signal_upper
            or confirmation_lower >= confirmation_upper
            or signal_low > signal_high
        ):
            return {"action": "WAIT", "reason": "KC channel invalid"}

        # 空手時不因即時碰觸或突破 KC 外軌直接追單；新倉只接受外側趨勢
        # 經已收盤 K 與下一根突破確認的順勢交易。

        held_side = str(current_side or "").upper()
        mode = str(market_mode or "RANGE").upper()
        # 猴市在外軌峰谷立即鎖利；牛／熊市只讓順勢倉延伸，直到
        # 反向 K 實際踏回 KC 通道才退出。逆勢倉仍使用外軌峰谷保護。
        if (mode == "BULL" and held_side == "LONG") or (
            mode == "BEAR" and held_side == "SHORT"
        ):
            return TradingEngine._channel_outer_reentry_exit_action(
                frame, price, held_side,
            )
        # 出場極值只能由兩根已收盤 K 證實；即時 K 的 MA3 尚會變動，
        # 因此僅以它的價格確認是否仍在 KC 外軌，不能拿來立即平倉。
        closed_ma3_peak = previous_ma3 < signal_ma3 - 1e-12
        closed_ma3_trough = previous_ma3 > signal_ma3 + 1e-12
        if held_side == "LONG":
            # 獲利側：上軌外，已確認 MA3 峰頂
            if price > upper and closed_ma3_peak:
                return {"action": "EXIT", "side": None, "reason": "KC_UPPER_OUTER_PEAK_EXIT"}
            # 虧損側：下軌外，已確認 MA3 谷底
            if price < lower and closed_ma3_trough:
                return {"action": "EXIT", "side": None, "reason": "KC_LOWER_OUTER_VALLEY_EXIT"}
        elif held_side == "SHORT":
            # 獲利側：下軌外，已確認 MA3 谷底
            if price < lower and closed_ma3_trough:
                return {"action": "EXIT", "side": None, "reason": "KC_LOWER_OUTER_VALLEY_EXIT"}
            # 虧損側：上軌外，已確認 MA3 峰頂
            if price > upper and closed_ma3_peak:
                return {"action": "EXIT", "side": None, "reason": "KC_UPPER_OUTER_PEAK_EXIT"}

        signal_width = signal_upper - signal_lower

        live_width = upper - lower
        live_width_pct = live_width / max((upper + lower) / 2.0, 1e-12)
        raw_trough = bool(signal_low <= signal_lower and signal_close > signal_open)
        raw_peak = bool(signal_high >= signal_upper and signal_close < signal_open)
        confirmation_width = confirmation_upper - confirmation_lower
        closed_trough = raw_trough
        closed_peak = raw_peak
        # 下一根若曾突破候選反方向極值，OHLC 無法還原盤中先後次序，
        # 採保守作法直接取消，不允許後續 K 救回舊候選。
        trough_cancelled = bool(closed_trough and confirmation_low < signal_low)
        peak_cancelled = bool(closed_peak and confirmation_high > signal_high)
        trough_reentry_ready = bool(
            closed_trough
            and confirmation_close + 1e-12 >= (
                confirmation_lower
                + confirmation_width * CHANNEL_SWING_MIN_REENTRY_RATIO
            )
        )
        peak_reentry_ready = bool(
            closed_peak
            and confirmation_close - 1e-12 <= (
                confirmation_upper
                - confirmation_width * CHANNEL_SWING_MIN_REENTRY_RATIO
            )
        )
        live_lower_touch = live_low <= lower
        live_upper_touch = live_high >= upper
        context_row = frame.iloc[-3]
        context_ma3 = float(context_row["ma3"])
        # 空手不直接追逐 KC 外側延伸；只接受上方已確認的外軌峰谷轉向。
        context_middle = (
            float(context_row["kc_upper"]) + float(context_row["kc_lower"])
        ) / 2.0
        live_middle = (upper + lower) / 2.0
        prior_up_context = bool(
            live_ma3 > context_ma3
            and live_middle > context_middle
            and price > live_middle
        )
        prior_down_context = bool(
            live_ma3 < context_ma3
            and live_middle < context_middle
            and price < live_middle
        )
        # 外軌候選只限緊接下一根確認：谷底破候選高、峰頂破候選低。
        trough_turn = bool(
            closed_trough
            and not trough_cancelled
            and confirmation_close > confirmation_open
            and confirmation_high > signal_high
            and trough_reentry_ready
        )
        peak_turn = bool(
            closed_peak
            and not peak_cancelled
            and confirmation_close < confirmation_open
            and confirmation_low < signal_low
            and peak_reentry_ready
        )
        side = str(current_side or "").upper()
        opposite_outer_trend = TradingEngine._channel_opposite_outer_trend_action(
            frame, price, side,
        )
        reason = ""
        if side == "LONG":
            if peak_turn:
                action, target_side, reason = (
                    "REVERSE", "SHORT", "KC_UPPER_PEAK_CONFIRMED"
                )
            elif opposite_outer_trend.get("action") == "REVERSE":
                action, target_side, reason = (
                    "REVERSE", "SHORT", "OPPOSITE_LOWER_OUTER_DOWNTREND"
                )
            else:
                action, target_side, reason = "HOLD", None, (
                    "WAIT_CLOSE_RED_IN_UPPER_MIDDLE"
                    if live_upper_touch else "WAIT_UPPER_RED_REENTRY"
                )
        elif side == "SHORT":
            if trough_turn:
                action, target_side, reason = (
                    "REVERSE", "LONG", "KC_LOWER_TROUGH_CONFIRMED"
                )
            elif opposite_outer_trend.get("action") == "REVERSE":
                action, target_side, reason = (
                    "REVERSE", "LONG", "OPPOSITE_UPPER_OUTER_UPTREND"
                )
            else:
                action, target_side, reason = "HOLD", None, (
                    "WAIT_CLOSE_GREEN_IN_LOWER_MIDDLE"
                    if live_lower_touch else "WAIT_LOWER_GREEN_REENTRY"
                )
        elif trough_turn or peak_turn:
            action, target_side, reason = "WAIT", None, "OUTER_REVERSAL_ENTRY_DISABLED"
        else:
            action, target_side = "WAIT", None
            if trough_turn and prior_down_context:
                reason = "COUNTERTREND_LONG_BLOCKED"
            elif peak_turn and prior_up_context:
                reason = "COUNTERTREND_SHORT_BLOCKED"
            elif raw_trough and not closed_trough:
                reason = "V_TOO_CLOSE_KC"
            elif raw_peak and not closed_peak:
                reason = "V_TOO_CLOSE_KC"
            elif closed_trough and not trough_reentry_ready:
                reason = "KC_REENTRY_TOO_SHALLOW"
            elif closed_peak and not peak_reentry_ready:
                reason = "KC_REENTRY_TOO_SHALLOW"
            elif closed_trough:
                reason = "CANCEL_LONG" if trough_cancelled else "WAIT_BREAK_HIGH"
            elif closed_peak:
                reason = "CANCEL_SHORT" if peak_cancelled else "WAIT_BREAK_LOW"
            elif live_lower_touch:
                reason = "WAIT_CLOSE_GREEN"
            elif live_upper_touch:
                reason = "WAIT_CLOSE_RED"
            else:
                reason = "WAIT_ADJACENT_OUTER_CANDIDATE"
        return {
            "action": action, "side": target_side,
            "kc_upper": upper, "kc_lower": lower, "reason": reason,
            "turn_low": (
                signal_low if trough_turn and action in ("ENTER", "REVERSE")
                else live_low if reason == "FAILED_SHORT_TURN_OUTER_TREND"
                else None
            ),
            "turn_high": (
                signal_high if peak_turn and action in ("ENTER", "REVERSE")
                else live_high if reason == "FAILED_LONG_TURN_OUTER_TREND"
                else None
            ),
        }

    @staticmethod
    def _is_continuous_wave_position(position: dict, meta: dict | None = None) -> bool:
        """是否為應交由連續峰谷主循環管理出場的持倉。"""
        meta = meta or {}
        entry_mode = str(
            position.get("entry_mode") or meta.get("entry_mode") or ""
        ).upper()
        reason = str(position.get("reason") or meta.get("reason") or "").upper()
        return bool(
            entry_mode in ("MA3_MA15_MARKET", "STRONG_LONG_BURST", "CHANNEL_SWING")
            or any(token in reason for token in (
                "TROUGH_TURN", "PEAK_TURN", "RANGE_SWING_REVERSE",
                "KC_MIDDLE_PEAK_REVERSE", "KC_MIDDLE_TROUGH_REVERSE",
                "CROSS_UP", "CROSS_DOWN", "TREND_LONG", "TREND_SHORT",
            ))
        )

    @staticmethod
    def _two_bar_structure_failure_exit(
        frame: pd.DataFrame, position_side: str,
    ) -> bool:
        """兩根已收盤 K 確認 MA3 與 KC 正持續朝持倉不利方向移動。"""
        required = {"close", "ma3", "ma15", "ema_20", "kc_upper", "kc_lower"}
        if frame is None or len(frame) < 3 or not required.issubset(frame.columns):
            return False
        recent = frame.iloc[-3:].dropna(subset=list(required))
        if len(recent) < 3:
            return False
        close = recent["close"].astype(float)
        ma3 = recent["ma3"].astype(float)
        ma15 = recent["ma15"].astype(float)
        middle = recent["ema_20"].astype(float)
        side = str(position_side or "").upper()
        if side == "SHORT":
            upper = recent["kc_upper"].astype(float)
            lines_rising = bool(
                ma3.iloc[0] < ma3.iloc[1] < ma3.iloc[2]
                and middle.iloc[0] < middle.iloc[1] < middle.iloc[2]
                and upper.iloc[0] < upper.iloc[1] < upper.iloc[2]
            )
            closes_confirmed = bool(
                close.iloc[-2] > max(middle.iloc[-2], ma15.iloc[-2])
                and close.iloc[-1] > max(middle.iloc[-1], ma15.iloc[-1])
            )
            return lines_rising and closes_confirmed
        if side == "LONG":
            lower = recent["kc_lower"].astype(float)
            lines_falling = bool(
                ma3.iloc[0] > ma3.iloc[1] > ma3.iloc[2]
                and middle.iloc[0] > middle.iloc[1] > middle.iloc[2]
                and lower.iloc[0] > lower.iloc[1] > lower.iloc[2]
            )
            closes_confirmed = bool(
                close.iloc[-2] < min(middle.iloc[-2], ma15.iloc[-2])
                and close.iloc[-1] < min(middle.iloc[-1], ma15.iloc[-1])
            )
            return lines_falling and closes_confirmed
        return False

    @staticmethod
    def _adverse_kc_outer_breached(
        position_side: str, live_price: float, kc_upper: float, kc_lower: float,
    ) -> bool:
        """價格是否已越過持倉不利方向的 KC 外軌。"""
        side = str(position_side or "").upper()
        return bool(
            (side == "LONG" and kc_lower > 0 and live_price < kc_lower)
            or (side == "SHORT" and kc_upper > 0 and live_price > kc_upper)
        )

    @staticmethod
    def _confirmed_outer_reversal(
        position_side: str, signal_info: dict, frame: pd.DataFrame,
    ) -> bool:
        """只承認方向相反且 MA3 峰／谷確實位於 KC 外軌外的已確認訊號。"""
        if not isinstance(signal_info, dict) or frame is None or frame.empty:
            return False
        side = str(position_side or "").upper()
        expected_signal = "SHORT" if side == "LONG" else "LONG" if side == "SHORT" else ""
        expected_pivot = "PEAK_TURN" if side == "LONG" else "TROUGH_TURN" if side == "SHORT" else ""
        pivot_type = str(
            signal_info.get("pivot_type") or signal_info.get("entry_type") or ""
        ).upper()
        if not (
            expected_signal
            and str(signal_info.get("signal") or "").upper() == expected_signal
            and str(signal_info.get("entry_type") or "").upper() == expected_pivot
            and pivot_type == expected_pivot
            and bool(signal_info.get("pivot_confirmed"))
        ):
            return False

        try:
            pivot_offset = int(signal_info.get("pivot_offset", -2) or -2)
            row = frame.iloc[pivot_offset]
            ma3 = float(row["ma3"])
            upper = float(row["kc_upper"])
            lower = float(row["kc_lower"])
        except (IndexError, KeyError, TypeError, ValueError):
            return False
        if any(pd.isna(value) for value in (ma3, upper, lower)):
            return False
        return bool(
            (side == "LONG" and ma3 >= upper)
            or (side == "SHORT" and ma3 <= lower)
        )

    @staticmethod
    def _range_swing_reverse_side(
        position_side: str, pivot_type: str, wave_regime: str,
        outer_run_active: bool,
    ) -> str | None:
        """RANGE 高點平多開空，低點平空開多；OUTER_RUN 不反手。"""
        if str(wave_regime or "").upper() != "RANGE" or outer_run_active:
            return None
        side = str(position_side or "").upper()
        pivot = str(pivot_type or "").upper()
        if side == "LONG" and pivot == "PEAK_TURN":
            return "SHORT"
        if side == "SHORT" and pivot == "TROUGH_TURN":
            return "LONG"
        return None

    @staticmethod
    def _pivot_pullback_ready(
        side: str, live_price: float, ma3: float, atr: float, extreme_price: float,
    ) -> bool:
        atr = max(float(atr), 1e-12)
        side = str(side or "").upper()
        rebound_atr = (
            (float(live_price) - float(extreme_price)) / atr
            if side == "SHORT"
            else (float(extreme_price) - float(live_price)) / atr
        )
        distance_atr = abs(float(live_price) - float(ma3)) / atr
        return rebound_atr >= 0.10 and distance_atr <= MA3_MARKET_ENTRY_MAX_DISTANCE_ATR

    @staticmethod
    def _detect_strict_pivot_prealert(live_frame: pd.DataFrame) -> str | None:
        """Delay prealert until MA3 really turns, and suppress it near the opposite rail."""
        required = {"open", "close", "ma3", "kc_upper", "kc_lower", "atr"}
        if live_frame is None or len(live_frame) < 3 or not required.issubset(live_frame.columns):
            return None

        latest = live_frame.iloc[-1]
        previous = live_frame.iloc[-2]
        atr = max(float(latest["atr"]), abs(float(latest["close"])) * 1e-12)
        middle = float(
            latest["kc_middle"]
            if "kc_middle" in live_frame.columns
            else latest["ema_20"]
        )
        ma3_now = float(latest["ma3"])
        ma3_prev = float(previous["ma3"])
        turn_distance = abs(ma3_now - ma3_prev)
        origin = live_frame.iloc[-3:-1]
        long_origin_confirmed = any(
            float(row["kc_lower"]) - float(row["ma3"]) >= 0.15 * max(
                float(row["atr"]), abs(float(row["close"])) * 1e-12,
            )
            for _, row in origin.iterrows()
        )
        short_origin_confirmed = any(
            float(row["ma3"]) - float(row["kc_upper"]) >= 0.15 * max(
                float(row["atr"]), abs(float(row["close"])) * 1e-12,
            )
            for _, row in origin.iterrows()
        )
        long_turn = (
            long_origin_confirmed
            and ma3_now > ma3_prev
            and turn_distance >= 0.05 * atr
            and ma3_now < middle
            and float(latest["close"]) > float(latest["open"])
            and float(latest["close"]) > float(previous["close"])
        )
        short_turn = (
            short_origin_confirmed
            and ma3_now < ma3_prev
            and turn_distance >= 0.05 * atr
            and ma3_now > middle
            and float(latest["close"]) < float(latest["open"])
            and float(latest["close"]) < float(previous["close"])
        )
        return "LONG" if long_turn else "SHORT" if short_turn else None

    def _continuous_entry_amount(self) -> float:
        """Allocate configured wallet fraction while preserving a fee/risk buffer."""
        positions = getattr(self.account, "positions", {})
        pending_orders = getattr(self.account, "pending_limit_orders", {})
        committed = len(positions) + len(pending_orders)
        available_fn = getattr(self.account, "get_available_balance", None)
        available = float(available_fn()) if available_fn else TRADE_AMOUNT_USDT
        wallet_fn = getattr(self.account, "get_wallet_balance", None)
        wallet_balance = float(wallet_fn()) if wallet_fn else (
            available + sum(float(pos.get("margin") or 0.0) for pos in positions.values())
        )
        effective_slots = get_effective_slot_count(wallet_balance)
        if effective_slots > 0 and committed >= effective_slots:
            return 0.0
        if committed == 0:
            fraction = (
                CONTINUOUS_SINGLE_SLOT_MARGIN_FRACTION
                if effective_slots == 1 else 0.5
            )
            return max(0.0, min(available, wallet_balance * fraction, TRADE_AMOUNT_USDT))
        # 多槽模式的補位只使用當下可用資金。
        return max(0.0, min(available, TRADE_AMOUNT_USDT))

    @staticmethod
    def _continuous_entry_price_is_safe(
        side: str, frame: pd.DataFrame, live_price: float,
    ) -> tuple[bool, str]:
        """Reject market entries near the directional KC extreme."""
        required = {"kc_upper", "kc_lower"}
        if frame is None or frame.empty or not required.issubset(frame.columns):
            return False, "KC data unavailable"
        middle_column = (
            "kc_middle" if "kc_middle" in frame.columns
            else "ema_20" if "ema_20" in frame.columns
            else None
        )
        if middle_column is None:
            return False, "KC middle data unavailable"
        row = frame.iloc[-1]
        try:
            price = float(live_price)
            middle = float(row[middle_column])
            upper = float(row["kc_upper"])
            lower = float(row["kc_lower"])
        except (TypeError, ValueError):
            return False, "KC data invalid"
        if not all(math.isfinite(value) for value in (price, middle, upper, lower)):
            return False, "KC data invalid"
        if not lower < middle < upper:
            return False, "KC channel invalid"

        side = str(side or "").upper()
        if side == "LONG":
            limit = middle + (upper - middle) * CONTINUOUS_ENTRY_OUTER_ZONE_RATIO
            safe = price < limit
            reason = f"price {price:.8g} is in the upper KC chase zone (limit {limit:.8g})"
        elif side == "SHORT":
            limit = middle - (middle - lower) * CONTINUOUS_ENTRY_OUTER_ZONE_RATIO
            safe = price > limit
            reason = f"price {price:.8g} is in the lower KC chase zone (limit {limit:.8g})"
        else:
            return False, f"invalid entry side {side}"
        return safe, "" if safe else reason

    def _abnormal_market_entry_allowed(
        self, symbol: str, side: str, price: float, atr: float,
        candle_open: float, candle_high: float, candle_low: float,
        candle_close: float,
    ) -> bool:
        """阻止異常拉砸期間的新倉；絕不觸發既有持倉的平倉。"""
        if not ABNORMAL_MARKET_GUARD_ENABLED:
            return True
        values = (price, atr, candle_open, candle_high, candle_low, candle_close)
        if not all(math.isfinite(float(value or 0.0)) for value in values):
            return True
        if price <= 0 or atr <= 0 or candle_high < candle_low or candle_open <= 0:
            return True

        range_pct = (candle_high - candle_low) / price
        range_atr = (candle_high - candle_low) / atr
        signed_move_pct = (candle_close - candle_open) / candle_open
        requested = str(side or "").upper()
        excessive_range = (
            (ABNORMAL_MARKET_MAX_CANDLE_RANGE_ATR > 0
             and range_atr >= ABNORMAL_MARKET_MAX_CANDLE_RANGE_ATR)
            or (ABNORMAL_MARKET_MAX_CANDLE_RANGE_PCT > 0
                and range_pct >= ABNORMAL_MARKET_MAX_CANDLE_RANGE_PCT)
        )
        adverse_impulse = (
            (requested == "LONG" and signed_move_pct <= -ABNORMAL_MARKET_ADVERSE_MOVE_PCT)
            or (requested == "SHORT" and signed_move_pct >= ABNORMAL_MARKET_ADVERSE_MOVE_PCT)
        )
        if not excessive_range and not adverse_impulse:
            return True

        reasons = []
        if excessive_range:
            reasons.append(f"K線振幅 {range_atr:.1f} ATR / {range_pct:.2%}")
        if adverse_impulse:
            reasons.append(f"逆向單根變動 {signed_move_pct:.2%}")
        self.account.log(
            f"🛡️ {symbol} 異常拉砸／流動性風險，暫停新開{requested}："
            + "；".join(reasons),
            "WARNING",
        )
        return False

    async def _place_continuous_market_entry(
        self, symbol: str, side: str, df: pd.DataFrame, live_price: float,
        entry_type: str, reason: str, score: int, timeframe: str,
        wave_regime: str = "TREND",
        market_mode: str = "TREND",
    ) -> bool:
        """MA3/MA15 順勢訊號確認後，以即時市價直接開倉。"""
        from core.config import TRADE_AMOUNT_USDT, get_leverage
        from core.strategy import build_sl_tp_for_side

        if symbol in getattr(self.account, "positions", {}):
            self.account.log(f"⏸️ {symbol} 已有持倉，不重複占用槽位", "DEBUG")
            return False
        entry_volume_ratio = self._channel_volume_ratio(df)
        if entry_volume_ratio < KELTNER_MIN_VOLUME_RATIO:
            self.account.log(
                f"🛑 {symbol} {side} 當下量能 {entry_volume_ratio:.2f}x "
                f"低於 {KELTNER_MIN_VOLUME_RATIO:.2f}x，取消市價開倉並等待換幣",
                "WARNING",
            )
            return False
        latest = df.iloc[-1]
        if not self._abnormal_market_entry_allowed(
            symbol, side, live_price, float(latest.get("atr") or 0.0),
            float(latest.get("open") or live_price),
            float(latest.get("high") or live_price),
            float(latest.get("low") or live_price),
            float(latest.get("close") or live_price),
        ):
            return False
        # 一般行情不使用大週期方向限制；只有盤整時，才依該幣種目前
        # 進場週期的 MA3/MA15 方向避免逆著短線結構開倉。
        if str(wave_regime or "").upper() == "RANGE":
            close = pd.to_numeric(df["close"], errors="coerce")
            ma3 = pd.to_numeric(df["ma3"], errors="coerce") if "ma3" in df.columns else close.rolling(3).mean()
            ma15 = pd.to_numeric(df["ma15"], errors="coerce") if "ma15" in df.columns else close.rolling(15).mean()
            ma3_now = float(ma3.iloc[-1])
            ma15_now = float(ma15.iloc[-1])
            requested = str(side or "").upper()
            aligned = (
                (requested == "LONG" and ma3_now > ma15_now)
                or (requested == "SHORT" and ma3_now < ma15_now)
            )
            if not aligned:
                trend = "向上" if ma3_now > ma15_now else "向下" if ma3_now < ma15_now else "無方向"
                self.account.log(
                    f"🛑 {symbol} 盤整中目前趨勢{trend}（MA3={ma3_now:.8g}, "
                    f"MA15={ma15_now:.8g}），拒絕開{('多' if requested == 'LONG' else '空')}",
                    "WARNING",
                )
                return False
        price_safe, unsafe_reason = self._continuous_entry_price_is_safe(
            side, df, live_price,
        )
        if not price_safe:
            self.account.log(
                f"Entry blocked for {symbol} {side}: {unsafe_reason}; waiting for pullback",
                "INFO",
            )
            return False
        amount_usdt = self._continuous_entry_amount()
        leverage = get_leverage(symbol)
        available_balance = max(0.0, float(self.account.get_available_balance()))
        # 單槽使用全部可用餘額，但預留開倉 taker fee，避免紙帳戶扣成負值。
        fee_safe_amount = available_balance / (1.0 + leverage * max(TAKER_FEE_RATE, 0.0))
        amount_usdt = min(amount_usdt, fee_safe_amount)
        if amount_usdt < MIN_TRADE_USDT:
            self.account.log(
                f"⏸️ {symbol} 交易槽位已滿或可用保證金不足", "INFO"
            )
            return False

        atr_raw = float(df["atr"].iloc[-1]) if "atr" in df.columns else 0.0
        atr = atr_raw if pd.notna(atr_raw) and atr_raw > 0 else live_price * 0.015
        sl_dist, tp_dist = compute_sl_tp_distance(live_price, atr)
        sl, tp = build_sl_tp_for_side(live_price, side, sl_dist, tp_dist)
        if CONTINUOUS_OUTER_RAIL_EXIT_ONLY:
            sl, tp = 0.0, 0.0
        opened = await self.account.open_position(
            symbol=symbol, side=side, price=live_price,
            amount_usdt=amount_usdt, sl=sl, tp=tp,
            reason=f"{entry_type}: {reason}", atr=atr,
            leverage=leverage, signal_score=score,
            entry_context={
                "entry_mode": "MA3_MA15_MARKET", "timeframe": timeframe,
                "wave_regime": str(wave_regime).upper(),
                "market_mode": str(market_mode).upper(),
                "signal_candle_low": float(df["low"].iloc[-1]),
                "signal_candle_high": float(df["high"].iloc[-1]),
            },
        )
        if opened:
            self.account.log(
                f"⚡ {symbol} {side} {str(market_mode).upper()} 訊號以市價開倉 @ {live_price:.8g}",
                "INFO",
            )
        return bool(opened)

    async def _process_single_symbol(self, symbol, now_time, btc_1m_turn, daily_halt):
        signal_progress = []
        detected_candidates = []
        try:
            direction_text = "雙向"
            coin = symbol.replace("/USDT", "")

            # ====== 第一步：檢測強勢多單訊號（綠K衝外軌）===
            # 每5分鐘掃一次1m強勢多單，若檢測到則立即平掉空單並轉向
            try:
                pass # 根據使用者要求已完全關閉非 Channel Swing 的強勢進場
                """
                from core.strategy import detect_strong_green_candle_burst
                df_1m_signal = await self.fetch_klines(symbol, timeframe="1m", limit=30)
                if not df_1m_signal.empty and len(df_1m_signal) >= 2:
                    df_1m_signal = self.strategy.compute_indicators(df_1m_signal)
                    strong_burst = detect_strong_green_candle_burst(df_1m_signal)
                    strong_live_price = float(
                        self.tickers.get(symbol) or df_1m_signal['close'].iloc[-1]
                    )
                    strong_live_entry_valid = self._strong_burst_live_entry_is_valid(
                        strong_burst, df_1m_signal, strong_live_price,
                    )

                    strong_burst_btc_blocked = bool(
                        strong_burst.get("detected")
                        and strong_live_entry_valid
                        and self._btc_pulse_blocks_entry("LONG", btc_1m_turn)
                    )
                    if strong_burst_btc_blocked:
                        signal_progress.append(
                            f"{coin} 強勢多單遭 BTC 1m SHORT 強脈衝阻擋"
                        )
                        return signal_progress, detected_candidates

                    if (
                        strong_burst.get("detected")
                        and strong_live_entry_valid
                        and symbol not in self.account.positions
                        and not DISABLE_CONTINUOUS_TREND_ENTRIES
                        and not CONTINUOUS_PIVOT_ONLY
                    ):
                        # 檢查是否有空單
                        if symbol in self.account.positions:
                            pos = self.account.positions[symbol]
                            if pos.get("side") == "SHORT":
                                live_price = float(self.tickers.get(symbol) or df_1m_signal['close'].iloc[-1])
                                self.account.log(
                                    f"🚨 [強勢多單轉向] {symbol} 檢測到綠K衝外軌！"
                                    f"（現價={live_price:.6g}，上軌={strong_burst.get('kc_upper'):.6g}）"
                                    f"現有空單需立即平倉轉向",
                                    "WARNING"
                                )
                                # 立即平倉空單
                                await self.account.close_position(
                                    symbol=symbol,
                                    current_price=live_price,
                                    close_reason="黑圈位：強勢多單訊號，平倉空單準備轉向"
                                )
                                # 標記此幣種短期已平空單，等待新的多單開倉機會
                                self.account.position_meta.setdefault(symbol, {})["strong_burst_short_closed_at"] = now_time

                        # 如果無持倉或剛平掉空單，標記為候選進場幣種（由主迴圈決定是否開多單）
                        if symbol not in self.account.positions:
                            detected_candidates.append({
                                "symbol": symbol,
                                "side": "LONG",
                                "score": 95,
                                "reason": strong_burst.get("reason") + " | 強勢多單，黑圈轉向",
                                "live_price": strong_live_price,
                                "entry_mode": "STRONG_LONG_BURST",
                                "trend_quality": self._directional_trend_quality(
                                    df_1m_signal, strong_live_price, "LONG",
                                ),
                                "in_outer_rail": strong_burst.get("in_outer_rail", False),
                                "kc_upper": strong_burst.get("kc_upper"),
                                "kc_middle": strong_burst.get("kc_middle"),
                            })
                            signal_progress.append(f"{coin} 強勢多單 95分，黑圈轉向")
                            return signal_progress, detected_candidates
                    elif strong_burst.get("detected") and not strong_live_entry_valid:
                        self.account.log(
                            f"⏸️ {symbol} 強勢多單舊K曾觸上軌，但現價 {strong_live_price:.6g} "
                            f"已回到KC中軌附近，不追多",
                            "INFO",
                        )
                    elif strong_burst.get("detected") and symbol in self.account.positions:
                        self.account.log(
                            f"⏸️ {symbol} 已有波段持倉，STRONG_LONG_BURST 不再強制平倉轉多",
                            "DEBUG",
                        )
                """
            except Exception as e:
                self.account.log(f"⚠️ {symbol} 強勢多單檢測異常: {e}", "WARNING")

            from core.config import ENABLE_CONTINUOUS_REVERSE_MODE

            # ====== 第二步：如果多單在外軌，不要提早出場（延後平倉邏輯）======
            # Channel Swing 要在碰到對面軌道時立即平倉，不能被這個舊外軌延伸收掉。
            if symbol in self.account.positions and not ENABLE_CONTINUOUS_REVERSE_MODE:
                pos = self.account.positions[symbol]
                if pos.get("side") == "LONG":
                    try:
                        df_check_outer = await self.fetch_klines(symbol, timeframe="1m", limit=20)
                        if not df_check_outer.empty:
                            df_check_outer = self.strategy.compute_indicators(df_check_outer)
                            curr_close = float(df_check_outer['close'].iloc[-1])
                            kc_upper = float(df_check_outer['kc_upper'].iloc[-1]) if 'kc_upper' in df_check_outer.columns else curr_close

                            is_in_outer_rail = curr_close > kc_upper
                            if is_in_outer_rail:
                                # 多單在外軌，記錄最高價格用於追蹤
                                pos_meta = self.account.position_meta.setdefault(symbol, {})
                                peak_price = pos_meta.get("outer_rail_peak_price", curr_close)
                                if curr_close > peak_price:
                                    pos_meta["outer_rail_peak_price"] = curr_close
                                    pos_meta["outer_rail_peak_time"] = now_time
                                    self.account.log(
                                        f"📈 {symbol} 多單在外軌延伸中...最新峰值={curr_close:.6g}，上軌={kc_upper:.6g}",
                                        "DEBUG"
                                    )
                                # 暫停此次的平倉檢查，繼續持有
                                # 讓持倉管理迴圈繼續執行，但不觸發提早平倉
                                return signal_progress, detected_candidates
                    except Exception as e:
                        self.account.log(f"⚠️ {symbol} 外軌持仓檢查異常: {e}", "WARNING")

            from core.config import ENABLE_CONTINUOUS_REVERSE_MODE, CONTINUOUS_REVERSE_TIMEFRAME, TRADE_AMOUNT_USDT, get_leverage
            if ENABLE_CONTINUOUS_REVERSE_MODE:
                # Flat and held positions use the same KC outer pivot confirmation.
                channel_df = await self.fetch_klines(
                    symbol, timeframe="1m", limit=200, keep_live=True,
                )
                if not channel_df.empty:
                    channel_df = self.strategy.compute_indicators(channel_df.copy())
                channel_price = float(
                    self.tickers.get(symbol)
                    or (channel_df["close"].iloc[-1] if not channel_df.empty else 0.0)
                )
                existing_pos = self.account.positions.get(symbol)
                ranked_direction = str(
                    self.symbol_rotation.direction_map.get(symbol) or ""
                ).upper()
                chop_info = self._channel_chop_state(channel_df)
                prior_chop_info = (
                    self._channel_chop_state(channel_df.iloc[:-1])
                    if len(channel_df) >= 15 else {}
                )
                recent_chop_detected = bool(prior_chop_info.get("detected"))
                was_chop_locked = bool(self._channel_chop_locked.get(symbol))
                if was_chop_locked:
                    chop_locked = not bool(chop_info.get("clear_direction"))
                else:
                    chop_locked = bool(
                        chop_info.get("detected")
                        and not chop_info.get("clear_direction")
                    )
                if chop_locked:
                    self._channel_chop_locked[symbol] = True
                    # 被盤整鎖擋掉的趨勢候選不得保留到解鎖後補開。
                    self._channel_outer_trend_wait.pop(symbol, None)
                    if not was_chop_locked:
                        self._record_channel_chop_event(
                            symbol, "LOCK", channel_df,
                        )
                        self.account.log(
                            f"⏸️ {symbol} 進入 CHOP_WAIT：MA3/MA15交叉"
                            f"{chop_info.get('ma_crosses', 0)}次、KC中軌交叉"
                            f"{chop_info.get('middle_crosses', 0)}次、"
                            f"方向效率{float(chop_info.get('efficiency') or 0.0):.2f}；"
                            "暫停新開倉與反手",
                            "INFO",
                        )
                else:
                    self._channel_chop_locked.pop(symbol, None)
                    if was_chop_locked:
                        self._record_channel_chop_event(
                            symbol, "UNLOCK", channel_df,
                            direction=chop_info.get("clear_direction"),
                        )
                        self.account.log(
                            f"▶️ {symbol} CHOP_WAIT 解鎖：連續三根已收盤K"
                            f"確認 {chop_info.get('clear_direction')} 方向",
                            "INFO",
                        )
                if not existing_pos:
                    self._record_btc_lead_shadow_candidate(
                        symbol, channel_df, channel_price, chop_locked,
                    )
                channel_action = self._channel_swing_action(
                    channel_df, channel_price,
                    existing_pos.get("side") if existing_pos else None,
                    existing_pos.get("channel_turn_low") if existing_pos else None,
                    existing_pos.get("channel_turn_high") if existing_pos else None,
                    (
                        existing_pos.get("market_mode")
                        or self._continuous_market_mode.get(symbol, "RANGE")
                    ) if existing_pos else None,
                )
                # 盤整突破資格取自目前 K 線狀態，不依賴程序記憶中的鎖定旗標。
                # 服務重啟或首次掃描時，只要當前仍鎖定，或上一根資料仍處於
                # 盤整，就能在緊接的 live K 確認突破。
                chop_breakout_context = bool(
                    chop_locked or recent_chop_detected
                )
                if False:  # Channel Swing 新倉僅保留已確認的 KC 外側趨勢

                    chop_breakout_action = self._channel_chop_breakout_action(
                        channel_df, channel_price,
                    )
                    if chop_breakout_action.get("action") == "ENTER":
                        channel_action = chop_breakout_action
                        chop_locked = False
                        self._channel_chop_locked.pop(symbol, None)
                        self._channel_outer_trend_wait.pop(symbol, None)
                        if not (
                            was_chop_locked
                            and chop_info.get("clear_direction")
                        ):
                            self._record_channel_chop_event(
                                symbol, "UNLOCK", channel_df,
                                direction=chop_breakout_action.get("side"),
                                live_bar=True,
                            )
                        self.account.log(
                            f"▶️ {symbol} CHOP_WAIT 動能突破確認，"
                            f"從當前位置開 {chop_breakout_action.get('side')}",
                            "SUCCESS",
                        )
                    elif (
                        chop_locked
                        and chop_breakout_action.get("reason")
                        in ("WAIT_CHOP_MOMENTUM_BREAK", "CANCEL_CHOP_BREAKOUT")
                    ):
                        channel_action = chop_breakout_action

                if (
                    not chop_locked
                    and not chop_breakout_context
                    and not existing_pos
                    and channel_action.get("action") != "ENTER"
                ):
                    # 使用者確認：第一根即時突破 KC 外軌且量能達標就追。
                    # 後段共用的 volume_ok 與 CHOP_WAIT 閘門仍會阻擋弱量／盤整。
                    outer_chase_action = self._channel_live_outer_entry_action(
                        channel_df, channel_price,
                    )
                    if outer_chase_action.get("action") != "ENTER":
                        outer_chase_action = self._channel_outer_trend_entry_action(
                            channel_df, channel_price,
                            self._channel_outer_trend_wait.get(symbol),
                        )
                    next_wait = outer_chase_action.get("pending")
                    if next_wait:
                        self._channel_outer_trend_wait[symbol] = next_wait
                    else:
                        self._channel_outer_trend_wait.pop(symbol, None)
                    if (
                        outer_chase_action.get("action") == "ENTER"
                        or outer_chase_action.get("reason")
                        not in (
                            "WAIT_OUTER_UPTREND",
                            "WAIT_OUTER_DOWNTREND",
                            "OUTER_UPTREND_DATA_UNAVAILABLE",
                            "OUTER_DOWNTREND_DATA_UNAVAILABLE",
                        )
                    ):
                        channel_action = outer_chase_action
                elif existing_pos:
                    self._channel_outer_trend_wait.pop(symbol, None)
                action = channel_action.get("action")
                target_side = channel_action.get("side")
                # 外軌峰谷、內軌回轉與同 K 反手只可平舊倉，不建立新倉。
                if existing_pos and action == "REVERSE":
                    action = "EXIT"
                    target_side = None
                    channel_action["reason"] = f"{channel_action.get('reason') or 'CHANNEL_REVERSAL'}_EXIT_ONLY"
                # 上軌紅 K 建立的空單在通道內不反覆改開多：只有
                # 即時綠 K 回到上軌外才准許反手。半通道即時平倉
                # 是 EXIT，不受此反手鎖影響。
                if (
                    action == "REVERSE"
                    and target_side == "LONG"
                    and self._channel_is_upper_red_peak_short(existing_pos or {})
                    and not self._channel_upper_red_short_reversal_allowed(
                        channel_df, channel_price,
                    )
                ):
                    action, target_side = "HOLD", None
                    channel_action["reason"] = "UPPER_RED_SHORT_LOCK_WAIT_GREEN_OUTER"
                # 「碰 KC 外軌並成 V」是轉向的必要條件，不是在盤整中
                # 無條件反手的充分條件。盤整鎖期間可平舊倉，但不得開新倉。
                action, target_side, chop_gate_reason = self._channel_chop_gate(
                    action, target_side, chop_locked, bool(existing_pos),
                    channel_action.get("reason"),
                )
                if chop_gate_reason:
                    channel_action["reason"] = chop_gate_reason
                if not existing_pos and action != "ENTER":
                    self._record_channel_signal_event(
                        symbol, channel_action.get("reason"), channel_df,
                    )
                channel_closed_bar_id = (
                    channel_df.index[-2] if len(channel_df) >= 2 else None
                )
                if self._channel_entry_reuses_exit_bar(
                    action, bool(existing_pos), channel_closed_bar_id,
                    getattr(self, "_channel_swing_last_exit_bar", {}).get(symbol),
                ):
                    action = "HOLD"
                    target_side = None
                    channel_action["reason"] = "EXIT_BAR_ALREADY_USED"
                if (
                    action == "REVERSE"
                    and not self._channel_is_immediate_outer_rechase(
                        channel_action.get("reason")
                    )
                    and channel_closed_bar_id is not None
                    and self._channel_swing_last_reverse_bar.get(symbol)
                    == channel_closed_bar_id
                ):
                    action = "HOLD"
                    target_side = None
                    channel_action["reason"] = "REVERSE_ALREADY_USED_THIS_BAR"
                kc_upper = float(channel_action.get("kc_upper") or 0.0)
                kc_lower = float(channel_action.get("kc_lower") or 0.0)

                volume_ratio = self._channel_volume_ratio(channel_df)
                volume_ok = volume_ratio >= KELTNER_MIN_VOLUME_RATIO

                if action in ("ENTER", "REVERSE") and target_side and not volume_ok:
                    if action == "ENTER":
                        signal_progress.append(
                            f"{coin} {target_side} 排名候選但當下量能不足"
                            f"({volume_ratio:.2f}x<{KELTNER_MIN_VOLUME_RATIO:.2f}x)，禁止開倉並換幣"
                        )
                        return signal_progress, detected_candidates
                    action = "EXIT"
                    target_side = None
                    channel_action["reason"] = (
                        f"{channel_action.get('reason') or 'REVERSE'}_LOW_VOLUME"
                    )
                    signal_progress.append(
                        f"{coin} 反手訊號量能不足({volume_ratio:.2f}x)，只平舊倉不開反向倉"
                    )

                if (
                    action == "ENTER"
                    and target_side
                    and SYMBOL_ROTATION_ENABLED
                    and not self._entry_matches_ranked_direction(
                        target_side, ranked_direction,
                    )
                ):
                    signal_progress.append(
                        f"{coin} {target_side} blocked: ranked direction "
                        f"{ranked_direction or 'WAIT'}"
                    )
                    return signal_progress, detected_candidates

                if (
                    action == "ENTER"
                    and target_side
                    and self._btc_pulse_blocks_entry(target_side, btc_1m_turn)
                ):
                    pulse = str(btc_1m_turn).upper()
                    signal_progress.append(
                        f"{coin} {target_side} 訊號遭 BTC 1m {pulse} 強脈衝阻擋；"
                        "既有持倉不變" if existing_pos else
                        f"{coin} {target_side} 訊號遭 BTC 1m {pulse} 強脈衝阻擋"
                    )
                    return signal_progress, detected_candidates

                if action == "EXIT" and existing_pos:
                    exit_reason = str(channel_action.get("reason") or "KC_OUTER_EXIT")
                    closed = await self.account.close_position(
                        symbol, channel_price,
                        f"Channel Swing {exit_reason}", is_manual=True,
                    )
                    if closed:
                        if channel_closed_bar_id is not None:
                            self._channel_swing_last_exit_bar[symbol] = channel_closed_bar_id
                        if self._channel_exit_requests_rotation(exit_reason) or "LOW_VOLUME" in exit_reason:
                            request_replacement = getattr(
                                self.symbol_rotation, "request_replacement", None,
                            )
                            if callable(request_replacement):
                                request_replacement(symbol)
                            rotation_event = getattr(self, "rotation_event", None)
                            if rotation_event is not None:
                                rotation_event.set()
                            self._channel_outer_trend_wait.pop(symbol, None)
                            self.account.log(
                                f"🔄 [Channel Swing] {symbol} 平倉完成；立即重新排名換幣",
                                "SUCCESS",
                            )
                        else:
                            self.account.log(
                            f"⏹️ [Channel Swing] {symbol} 轉向失敗或抵達對側KC外軌，"
                            "先平倉並等待下一個真正峰谷", "SUCCESS",
                        )
                    return signal_progress, detected_candidates

                if action == "REVERSE" and existing_pos:
                    old_side = str(existing_pos.get("side") or "").upper()
                    reverse_reason = str(channel_action.get("reason") or "")
                    if reverse_reason == "SAME_BAR_UPPER_OUTER_RECHASE":
                        close_label = "KC_UPPER_OUTER_RECHASE"
                    elif reverse_reason == "SAME_BAR_LOWER_OUTER_RECHASE":
                        close_label = "KC_LOWER_OUTER_RECHASE"
                    elif reverse_reason == "OPPOSITE_UPPER_OUTER_UPTREND":
                        close_label = "KC_UPPER_OUTER_UPTREND"
                    elif reverse_reason == "OPPOSITE_LOWER_OUTER_DOWNTREND":
                        close_label = "KC_LOWER_OUTER_DOWNTREND"
                    else:
                        close_label = (
                            "KC_OUTER_PEAK"
                            if old_side == "LONG" else "KC_OUTER_TROUGH"
                        )
                    # 單幣模式：確認 KC 外側峰／谷後先立即平掉原倉；只有平倉
                    # 成功才建立同幣反向候選，禁止先掃描或等待其他幣種。
                    closed = await self.account.close_position(
                        symbol, channel_price,
                        f"Channel Swing {close_label} close-first", is_manual=True,
                    )
                    if not closed:
                        return signal_progress, detected_candidates
                    if channel_closed_bar_id is not None:
                        self._channel_swing_last_reverse_bar[symbol] = channel_closed_bar_id
                    switch_reason = (
                        f"KC upper outer rechase close-first then reopen {target_side}"
                        if reverse_reason == "SAME_BAR_UPPER_OUTER_RECHASE"
                        else f"KC lower outer rechase close-first then reopen {target_side}"
                        if reverse_reason == "SAME_BAR_LOWER_OUTER_RECHASE"
                        else f"KC upper outer uptrend close-first then reopen {target_side}"
                        if reverse_reason == "OPPOSITE_UPPER_OUTER_UPTREND"
                        else f"KC lower outer downtrend close-first then reopen {target_side}"
                        if reverse_reason == "OPPOSITE_LOWER_OUTER_DOWNTREND"
                        else f"KC outer peak close-first then reopen {target_side}"
                        if old_side == "LONG"
                        else f"KC outer trough close-first then reopen {target_side}"
                    )
                    self.account.log(
                        f"[Channel Swing] {symbol} {old_side} {close_label}; "
                        f"closed first, then reopen same symbol {target_side}",
                        "SUCCESS",
                    )
                    detected_candidates.append({
                        "detected": True, "side": target_side,
                        "score": 100, "priority": 4,
                        "price": channel_price, "live_price": channel_price,
                        "atr": float(channel_df["atr"].iloc[-1]),
                        "volume_ratio": volume_ratio,
                        "profit_potential": self._candidate_profit_potential(
                            symbol, target_side, float(channel_df["atr"].iloc[-1]),
                            channel_price,
                        ),
                        "entry_mode": "CHANNEL_SWING",
                        "profit_profile": "TREND_EXTENSION",
                        "action": "ENTER_MARKET", "target_price": None,
                        "signal_candle_low": float(channel_df["low"].iloc[-1]),
                        "signal_candle_high": float(channel_df["high"].iloc[-1]),
                        "channel_turn_low": channel_action.get("turn_low"),
                        "channel_turn_high": channel_action.get("turn_high"),
                        "symbol": symbol, "wave_regime": "RANGE",
                        "market_mode": "RANGE",
                        "reason": switch_reason,
                        "trend_quality": self._directional_trend_quality(
                            channel_df, channel_price, target_side,
                        ),
                    })
                    return signal_progress, detected_candidates

                if existing_pos:
                    target = kc_upper if existing_pos.get("side") == "LONG" else kc_lower
                    target_name = "上軌" if existing_pos.get("side") == "LONG" else "下軌"
                    confirm_wait = {
                        "WAIT_CLOSE_GREEN": " | 已越下軌，等待綠K收盤",
                        "WAIT_CLOSE_RED": " | 已越上軌，等待紅K收盤",
                        "WAIT_BREAK_HIGH": " | 下軌綠K已收盤，等待下一根破高",
                        "WAIT_BREAK_LOW": " | 上軌紅K已收盤，等待下一根破低",
                        "CANCEL_LONG": " | 多方候選已先破低取消",
                        "CANCEL_SHORT": " | 空方候選已先破高取消",
                        "V_TOO_CLOSE_KC": " | V線離KC外軌太近，不平倉不轉向",
                        "KC_REENTRY_TOO_SHALLOW": " | 站回KC通道比例不足，不平倉不轉向",
                    }.get(str(channel_action.get("reason") or ""), "")
                    signal_progress.append(
                        f"{coin} {existing_pos.get('side')} 持倉中 | "
                        f"目標{target_name} {target:.8g}{confirm_wait}"
                    )
                    return signal_progress, detected_candidates

                if action == "ENTER" and target_side:
                    detected_candidates.append({
                        "detected": True, "side": target_side, "score": 100,
                        "priority": self._channel_entry_candidate_priority(
                            channel_action.get("reason"),
                        ),
                        "price": channel_price, "live_price": channel_price,
                        "atr": float(channel_df["atr"].iloc[-1]),
                        "volume_ratio": volume_ratio,
                        "profit_potential": self._candidate_profit_potential(
                            symbol, target_side, float(channel_df["atr"].iloc[-1]),
                            channel_price,
                        ),
                        "entry_mode": "CHANNEL_SWING",
                        "profit_profile": "TREND_EXTENSION",
                        "action": "ENTER_MARKET", "target_price": None,
                        "signal_candle_low": float(channel_df["low"].iloc[-1]),
                        "signal_candle_high": float(channel_df["high"].iloc[-1]),
                        "channel_turn_low": channel_action.get("turn_low"),
                        "channel_turn_high": channel_action.get("turn_high"),
                        "symbol": symbol, "wave_regime": "RANGE",
                        "market_mode": "RANGE",
                        "trend_quality": self._directional_trend_quality(
                            channel_df, channel_price, target_side,
                        ),
                        "reason": (
                            f"Channel Swing CHOP momentum breakout {target_side}"
                            if channel_action.get("reason") in ("CHOP_BREAKOUT_LONG", "CHOP_BREAKOUT_SHORT")
                            else f"Channel Swing live KC outer break {target_side}"
                            if channel_action.get("reason") in (
                                "KC_LIVE_UPPER_BREAK_LONG",
                                "KC_LIVE_LOWER_BREAK_SHORT",
                            )
                            else "Channel Swing KC upper trend confirmed LONG"
                            if channel_action.get("reason") in (
                                "KC_UPPER_TREND_CONFIRMED_LONG",
                                "KC_UPPER_RETEST_BREAK_LONG",
                            )
                            else "Channel Swing KC lower trend confirmed SHORT"
                            if channel_action.get("reason") in (
                                "KC_LOWER_TREND_CONFIRMED_SHORT",
                                "KC_LOWER_RETEST_BREAK_SHORT",
                            )
                            else "Channel Swing KC lower outer trough LONG"
                            if target_side == "LONG"
                            else "Channel Swing KC upper outer peak SHORT"
                        ),
                    })
                else:
                    wait_detail = {
                        "STALE_TROUGH_TURN": " | 舊下軌谷底已突破，不補追",
                        "STALE_PEAK_TURN": " | 舊上軌峰頂已跌破，不補追",
                        "CHOP_WAIT_NO_ENTRY": " | CHOP_WAIT 盤整鎖，外軌V也不開倉",
                        "KC_WIDTH_TOO_NARROW": " | KC寬度不足，獲利空間太窄",
                        "WAIT_DYNAMIC_TREND": " | 上軌外但趨勢品質不足，暫不追多",
                        "WAIT_DYNAMIC_DOWNTREND": " | 下軌外但趨勢品質不足，暫不追空",
                        "WAIT_TREND_BREAK": " | 上軌動能成立，等待下一根破高",
                        "WAIT_DOWNTREND_BREAK": " | 下軌動能成立，等待下一根破低",
                        "WAIT_CHOP_MOMENTUM_BREAK": " | 盤整突破候選成立，等待下一根確認",
                        "CANCEL_CHOP_BREAKOUT": " | 盤整突破候選失敗取消",
                        "WAIT_TREND_RETEST": " | 趨勢已確認但價格過熱，等待回踩上軌",
                        "WAIT_DOWNTREND_RETEST": " | 空方趨勢已確認但價格過熱，等待回抽下軌",
                        "WAIT_TREND_RETEST_BREAK": " | 上軌回踩成立，等待下一根破高",
                        "WAIT_DOWNTREND_RETEST_BREAK": " | 下軌回抽成立，等待下一根破低",
                        "CANCEL_TREND_CONFIRM": " | 趨勢確認失敗，候選取消",
                        "CANCEL_TREND_CONFIRM_EXPIRED": " | 趨勢候選逾時取消",
                        "CANCEL_TREND_RETEST": " | 上軌回踩突破失敗，候選取消",
                        "CANCEL_DOWNTREND_CONFIRM": " | 空方趨勢確認失敗，候選取消",
                        "CANCEL_DOWNTREND_CONFIRM_EXPIRED": " | 空方趨勢候選逾時取消",
                        "CANCEL_DOWNTREND_RETEST": " | 下軌回抽突破失敗，候選取消",
                    }.get(str(channel_action.get("reason") or ""), "")
                    signal_progress.append(
                        f"{coin} 通道中央等待 | KC {kc_lower:.8g}~{kc_upper:.8g}"
                        f"{wait_detail}"
                    )
                return signal_progress, detected_candidates

                from core.indicators import classify_wave_regime, detect_ma3_ma15_cross_and_turn
                from core.strategy import build_sl_tp_for_side
                # 連續峰谷模式使用設定的同一週期已收盤K，避免不同週期混用。
                df_cr = await self.fetch_klines(symbol, timeframe=CONTINUOUS_REVERSE_TIMEFRAME, limit=100, keep_live=True)
                df_cr_live = df_cr.copy()
                if df_cr.empty or len(df_cr) < 4:
                    return signal_progress, detected_candidates
                # 正式峰谷、跨軌、平倉與反手一律只使用已收盤 K；即時 K 僅保留作預警。
                df_cr = drop_unclosed_candle(df_cr, CONTINUOUS_REVERSE_TIMEFRAME)
                if len(df_cr) < 15:
                    return signal_progress, detected_candidates
                df_cr_signal = df_cr.copy()
                df_cr_signal = self.strategy.compute_indicators(df_cr_signal)
                df_cr_signal["ma15"] = df_cr_signal["close"].rolling(15).mean()
                previous_wave_regime = self._continuous_wave_regime.get(symbol, "RANGE")
                wave_info = classify_wave_regime(
                    df_cr_signal, previous_regime=previous_wave_regime, confirmation_bars=3,
                )
                wave_regime = wave_info["regime"]
                self._continuous_wave_regime[symbol] = wave_regime
                if wave_regime != previous_wave_regime:
                    self.account.log(
                        f"🔄 {symbol} 波動模式 {previous_wave_regime} → {wave_regime} "
                        f"(ADX={wave_info.get('adx') or 0:.1f}, "
                        f"MA距離={wave_info.get('spread_atr') or 0:.2f} ATR，連續3根確認)",
                        "INFO",
                    )
                if symbol in self.account.positions:
                    self.account.positions[symbol]["wave_regime"] = wave_regime
                    self.account.position_meta.setdefault(symbol, {})["wave_regime"] = wave_regime
                market_mode = self._continuous_market_mode_for(
                    symbol, wave_regime, float(df_cr_signal["close"].iloc[-1]),
                )
                previous_market_mode = self._continuous_market_mode.get(symbol, "RANGE")
                self._continuous_market_mode[symbol] = market_mode
                if market_mode != previous_market_mode:
                    self.account.log(
                        f"🌐 {symbol} 市場模式 {previous_market_mode} → {market_mode} "
                        f"(個幣1h ST={self.st_direction_1h_cache.get(symbol, 0)}, "
                        f"個幣1h EMA50={self.ema_50_1h_cache.get(symbol, 0):.8g})",
                        "INFO",
                    )
                if symbol in self.account.positions:
                    self.account.positions[symbol]["market_mode"] = market_mode
                    self.account.position_meta.setdefault(symbol, {})["market_mode"] = market_mode
                cr_info = detect_ma3_ma15_cross_and_turn(df_cr_signal)
                if CONTINUOUS_PIVOT_ONLY:
                    from core.strategy import detect_simple_ma5_signal
                    # 先用 5887ffa 原始 MA3 峰谷函式確認第一根反向 K，
                    # 再由完整資料的最後一根作第二根 K 嚴格確認。
                    first_confirm_frame = df_cr_signal.iloc[:-1]
                    first_confirm_price = float(
                        first_confirm_frame["close"].iloc[-1]
                    )
                    sig = detect_simple_ma5_signal(
                        first_confirm_frame, live_price=first_confirm_price,
                    )
                    direct_candidates = []
                    for direct_side in ("LONG", "SHORT"):
                        direct_ok, direct_reason, direct_offset = (
                            self._validate_strict_pivot_entry(df_cr_signal, direct_side)
                        )
                        if direct_ok:
                            direct_candidates.append(
                                (direct_side, direct_reason, direct_offset)
                            )
                    if direct_candidates:
                        direct_side, direct_reason, direct_offset = max(
                            direct_candidates, key=lambda item: item[2]
                        )
                        sig = {
                            "detected": True, "side": direct_side,
                            "reason": direct_reason, "direct_strict": True,
                        }
                    if sig.get("detected"):
                        t_side = sig["side"]
                        strict_ok, strict_reason, pivot_offset = (
                            self._validate_strict_pivot_entry(
                                df_cr_signal, t_side,
                            )
                        )
                        if strict_ok:
                            cr_info = {
                                "signal": t_side,
                                "entry_type": "TROUGH_TURN" if t_side == "LONG" else "PEAK_TURN",
                                "reason": (
                                    f"{CONTINUOUS_REVERSE_TIMEFRAME} {sig['reason']}；"
                                    f"{strict_reason}"
                                ),
                                "pivot_offset": pivot_offset,
                                "pivot_confirmed": True,
                                "pivot_score": 100,
                                "atr": float(df_cr_signal["atr"].iloc[-1]),
                            }
                        else:
                            cr_info = {
                                "signal": None,
                                "entry_type": "WAIT_STRICT_PIVOT_CONFIRM",
                                "reason": strict_reason,
                                "pivot_confirmed": False, "pivot_score": 0,
                            }
                    else:
                        cr_info = {
                            "signal": None, "entry_type": "WAIT_KC_OUTER_RAIL",
                            "reason": f"等待{CONTINUOUS_REVERSE_TIMEFRAME} MA3 出現明確峰頂/谷底轉向",
                            "pivot_confirmed": False, "pivot_score": 0,
                        }
                    cr_info["reason"] = str(cr_info.get("reason") or "").replace(
                        "1m MA3", f"{CONTINUOUS_REVERSE_TIMEFRAME} MA3"
                    )
                    live_df = self.strategy.compute_indicators(df_cr_live.copy())
                    prealert_side = self._detect_strict_pivot_prealert(live_df)
                    held_side = self.account.positions.get(symbol, {}).get("side")
                    if prealert_side and held_side != prealert_side:
                        self.pivot_prealerts[symbol] = {
                            "action": "PREALERT_LONG" if prealert_side == "LONG" else "PREALERT_SHORT",
                            "timestamp": int(float(df_cr_live["timestamp"].iloc[-1])),
                            "updated_at": time.time(),
                        }
                    else:
                        self.pivot_prealerts.pop(symbol, None)

                    # 第一根未收盤反向 K 只作預警，不可繞過第二根已收盤 K 的嚴格位置檢查。
                uses_live_pivot = False
                df_cr_entry = df_cr_signal
                cr_signal = cr_info.get("signal")
                cr_entry_type = cr_info.get("entry_type", "")
                is_peak_early = cr_info.get("is_peak_early", False)
                is_trough_early = cr_info.get("is_trough_early", False)

                if CONTINUOUS_PIVOT_ONLY and cr_entry_type in ("TROUGH_TURN", "PEAK_TURN"):
                    pivot_offset = int(cr_info.get("pivot_offset", -2) or -2)
                    pivot_ma3 = float(df_cr_entry["ma3"].iloc[pivot_offset])
                    pivot_upper = float(df_cr_entry["kc_upper"].iloc[pivot_offset])
                    pivot_lower = float(df_cr_entry["kc_lower"].iloc[pivot_offset])
                    pivot_on_outer_rail = (
                        (cr_entry_type == "PEAK_TURN" and pivot_ma3 > pivot_upper)
                        or (cr_entry_type == "TROUGH_TURN" and pivot_ma3 < pivot_lower)
                    )
                    if not pivot_on_outer_rail:
                        signal_progress.append(f"{coin} MA3未越過KC外軌，不預警、不轉向、不開倉")
                        cr_signal = None
                        cr_entry_type = "WAIT_KC_OUTER_RAIL"
                    elif cr_entry_type == "TROUGH_TURN":
                        kc_width_pct = (pivot_upper - pivot_lower) / max((pivot_upper + pivot_lower) / 2.0, 1e-12)
                        if kc_width_pct < PIVOT_MIN_KC_WIDTH_PCT:
                            signal_progress.append(f"{coin} KC range {kc_width_pct:.2%} too narrow; skip entry")
                            cr_signal = None
                            cr_entry_type = "WAIT_KC_WIDTH"

                # 完整波段模式不在途中追 TREND_LONG/TREND_SHORT，避免同一段
                # 反覆補單與多付手續費；只等待谷底／峰頂確認。
                confirmed_pivot_turn = bool(
                    cr_signal in ("LONG", "SHORT")
                    and cr_entry_type in ("TROUGH_TURN", "PEAK_TURN")
                    and cr_info.get("pivot_confirmed")
                )
                if DISABLE_CONTINUOUS_TREND_ENTRIES and cr_entry_type in ("TREND_LONG", "TREND_SHORT"):
                    signal_progress.append(f"{coin} 完整波段模式：略過順勢追單，等待峰谷")
                    cr_signal = None
                # 峰谷專用模式：所有幣一律只接受外軌谷底轉多／峰頂轉空。
                elif CONTINUOUS_PIVOT_ONLY and cr_entry_type not in ("TROUGH_TURN", "PEAK_TURN"):
                    signal_progress.append(
                        f"{coin} {cr_info.get('reason') or '等待下軌谷底轉多／上軌峰頂轉空'}"
                    )
                    cr_signal = None
                # 順勢模式只接受 MA3/MA15 延續訊號；峰谷與交叉訊號可作出場參考，但不可開新倉或反手。
                elif CONTINUOUS_TREND_ONLY and cr_entry_type not in ("TREND_LONG", "TREND_SHORT"):
                    signal_progress.append(f"{coin} 只做順勢：等待 MA3/MA15 同向延續")
                    cr_signal = None
                elif wave_regime == "RANGE" and cr_entry_type in ("TREND_LONG", "TREND_SHORT") and not CONTINUOUS_TREND_ONLY:
                    signal_progress.append(f"{coin} 短波動：等待谷底買多／峰頂開空")
                    cr_signal = None
                # 趨勢行情按牛／熊市限制開倉方向；盤整方向則在實際送單前，
                # 由該幣種當前進場週期的 MA3／MA15 判定。
                elif market_mode == "BULL" and cr_signal != "LONG" and not CONTINUOUS_PIVOT_ONLY and not confirmed_pivot_turn:
                    signal_progress.append(f"{coin} 牛市：只等順勢多單")
                    cr_signal = None
                elif market_mode == "BEAR" and cr_signal != "SHORT" and not CONTINUOUS_PIVOT_ONLY and not confirmed_pivot_turn:
                    signal_progress.append(f"{coin} 熊市：只等順勢空單")
                    cr_signal = None

                wait_state = self._continuous_alignment_wait.get(symbol)
                wait_side = wait_state.get("side") if isinstance(wait_state, dict) else wait_state
                if cr_signal and wait_side:
                    # detect_ma3_ma15_cross_and_turn 只在自己的內部副本上算 ma3，
                    # 從不會把 ma5 欄位寫回 df_cr_entry；比照上面設定
                    # _continuous_alignment_wait 時（約1285行）的既有防呆寫法，
                    # 欄位不存在就臨時算一條 5 期均線，避免 KeyError。
                    ma5_series = (
                        df_cr_entry["ma5"]
                        if "ma5" in df_cr_entry.columns
                        else df_cr_entry["close"].rolling(5).mean()
                    )
                    current_ma5 = float(ma5_series.iloc[-1])
                    previous_ma5 = float(ma5_series.iloc[-2])
                    wait_atr = (
                        max(float(wait_state.get("atr") or cr_info.get("atr") or 0.0), 1e-12)
                        if isinstance(wait_state, dict)
                        else max(float(cr_info.get("atr") or 0.0), 1e-12)
                    )
                    exit_ma5 = (
                        float(wait_state.get("exit_ma5") or current_ma5)
                        if isinstance(wait_state, dict)
                        else current_ma5
                    )
                    resumed = bool(
                        (
                            cr_signal == "LONG"
                            and current_ma5 > exit_ma5 + wait_atr * 0.02
                            and current_ma5 - previous_ma5 >= wait_atr * 0.01
                        )
                        or (
                            cr_signal == "SHORT"
                            and current_ma5 < exit_ma5 - wait_atr * 0.02
                            and previous_ma5 - current_ma5 >= wait_atr * 0.01
                        )
                    )
                    if cr_signal == wait_side and resumed:
                        self._continuous_alignment_wait.pop(symbol, None)
                        self.account.log(
                            f"{symbol} 提前峰谷判斷為假，恢復 {cr_signal} 順勢開倉",
                            "SUCCESS",
                        )
                    elif cr_signal == wait_side:
                        signal_progress.append(f"{coin} 已提前平倉，確認是否為假峰谷")
                        cr_signal = None
                    else:
                        required_pivot = (
                            "PEAK_TURN" if wait_side == "LONG" else "TROUGH_TURN"
                        )
                        if cr_entry_type == required_pivot:
                            self._continuous_alignment_wait.pop(symbol, None)
                            self.account.log(
                                f"{symbol} 已確認 {required_pivot}，解除保護性平倉等待鎖",
                                "SUCCESS",
                            )
                        else:
                            signal_progress.append(
                                f"{coin} 已保護性平倉，等待 {required_pivot} 確認後再掛反向單"
                            )
                            cr_signal = None

                has_pos = symbol in self.account.positions
                curr_side = self.account.positions[symbol]["side"] if has_pos else None

                pending_pivot_pullback = self._pivot_pullback_wait.get(symbol)
                if has_pos and pending_pivot_pullback:
                    self._pivot_pullback_wait.pop(symbol, None)
                    pending_pivot_pullback = None
                elif pending_pivot_pullback:
                    pending_side = str(pending_pivot_pullback.get("side") or "").upper()
                    pending_age = now_time - float(pending_pivot_pullback.get("created_at") or now_time)
                    if pending_age > 180.0 or pending_side not in ("LONG", "SHORT"):
                        self._pivot_pullback_wait.pop(symbol, None)
                        pending_pivot_pullback = None
                    elif cr_signal and cr_signal != pending_side:
                        self._pivot_pullback_wait.pop(symbol, None)
                        pending_pivot_pullback = None
                    else:
                        clean_sym = symbol.replace(":USDT", "") if symbol.endswith(":USDT") else symbol
                        wait_live_price = float(
                            self.tickers.get(clean_sym, self.tickers.get(symbol, df_cr_entry["close"].iloc[-1]))
                        )
                        wait_atr = self._resolve_entry_atr({}, df_cr_entry, wait_live_price)
                        wait_ma3 = float(df_cr_entry["ma3"].iloc[-1])
                        old_extreme = float(pending_pivot_pullback.get("extreme_price") or wait_live_price)
                        pending_pivot_pullback["extreme_price"] = (
                            min(old_extreme, wait_live_price)
                            if pending_side == "SHORT" else max(old_extreme, wait_live_price)
                        )
                        if self._pivot_pullback_ready(
                            pending_side, wait_live_price, wait_ma3, wait_atr,
                            pending_pivot_pullback["extreme_price"],
                        ):
                            cr_signal = pending_side
                            cr_entry_type = str(pending_pivot_pullback.get("entry_type") or (
                                "PEAK_TURN" if pending_side == "SHORT" else "TROUGH_TURN"
                            ))
                            cr_info = {
                                "signal": cr_signal, "entry_type": cr_entry_type,
                                "reason": "long confirmation candle pulled back to MA3",
                                "atr": wait_atr, "deferred_pivot_entry": True,
                                "pivot_confirmed": True, "pivot_score": 100,
                            }
                        else:
                            cr_signal = None
                            cr_entry_type = "WAIT_LONG_CANDLE_PULLBACK"
                            signal_progress.append(
                                f"{coin} long confirmation candle; wait for MA3 pullback"
                            )
                confirmed_outer_reversal = bool(
                    has_pos and self._confirmed_outer_reversal(
                        curr_side, cr_info, df_cr_entry,
                    )
                )
                kc_outer_reversal_blocked = False
                outer_run_active = False
                outer_run_return_exit = False
                opposite_candle_exit = False
                if has_pos:
                    from core.indicators import evaluate_kc_outer_run_lock
                    _position_meta_map = getattr(self.account, "position_meta", {})
                    _position_meta = _position_meta_map.setdefault(symbol, {})
                    _outer_lock = evaluate_kc_outer_run_lock(
                        df_cr_entry, curr_side,
                        armed=bool(_position_meta.get("kc_outer_run_armed")),
                        outer_run_active=bool(_position_meta.get("outer_run_active")),
                    )
                    _position_meta["kc_outer_run_armed"] = _outer_lock["armed"]
                    self.account.positions[symbol]["kc_outer_run_armed"] = _outer_lock["armed"]
                    outer_run_active = bool(_outer_lock["outer_run_active"])
                    for protect_key in (
                        "outer_run_pivot_protect_armed", "kc_pivot_protect_armed",
                    ):
                        _position_meta[protect_key] = False
                        self.account.positions[symbol][protect_key] = False
                    _position_meta["outer_run_return_pending"] = False
                    outer_run_return_detected = bool(
                        _outer_lock.get("returned_inside_outer")
                        or _position_meta.get("outer_run_return_pending", False)
                    )
                    _outer_confirm_bar = df_cr_entry.iloc[-1]
                    _outer_confirm_open = float(_outer_confirm_bar["open"])
                    _outer_confirm_close = float(_outer_confirm_bar["close"])
                    _outer_body_fully_inside = bool(
                        (
                            curr_side == "LONG"
                            and _outer_confirm_close < _outer_confirm_open
                            and _outer_confirm_open < float(_outer_lock["kc_upper"])
                            and _outer_confirm_close < float(_outer_lock["kc_upper"])
                        )
                        or (
                            curr_side == "SHORT"
                            and _outer_confirm_close > _outer_confirm_open
                            and _outer_confirm_open > float(_outer_lock["kc_lower"])
                            and _outer_confirm_close > float(_outer_lock["kc_lower"])
                        )
                    )
                    # 獲利側回到軌內、峰谷轉向與回吐都不平倉。
                    outer_run_return_exit = False
                    _position_meta["outer_run_active"] = outer_run_active
                    self.account.positions[symbol]["outer_run_active"] = outer_run_active
                    kc_outer_reversal_blocked = bool(_outer_lock["blocked"])
                    opposite_candle_exit = False

                entry_bar_id = (
                    int(float(df_cr_entry['timestamp'].iloc[-1]))
                    if 'timestamp' in df_cr_entry.columns
                    else int(df_cr_entry.index[-1])
                )

                first_outer_return = bool(
                    has_pos and outer_run_return_exit
                )
                if first_outer_return:
                    last_close = float(df_cr_entry["close"].iloc[-1])
                    clean_sym = symbol.replace(":USDT", "") if symbol.endswith(":USDT") else symbol
                    live_price = float(
                        self.tickers.get(clean_sym, self.tickers.get(symbol, last_close))
                    )
                    target_side = "SHORT" if curr_side == "LONG" else "LONG"
                    close_reason = (
                        f"{CONTINUOUS_REVERSE_TIMEFRAME} OUTER_RUN第一根紅K收回上軌內，先平多"
                        if curr_side == "LONG"
                        else f"{CONTINUOUS_REVERSE_TIMEFRAME} OUTER_RUN第一根綠K收回下軌內，先平空"
                    )
                    closed = await self.account.close_position(
                        symbol=symbol, current_price=live_price,
                        close_reason=close_reason, is_manual=True,
                    )
                    if closed:
                        self._kc_reversal_wait[symbol] = {
                            "mode": "OUTER_RUN_SECOND_CANDLE",
                            "from_side": curr_side,
                            "target_side": target_side,
                            "pivot_type": (
                                "PEAK_TURN" if curr_side == "LONG" else "TROUGH_TURN"
                            ),
                            "first_bar_id": entry_bar_id,
                            "first_close": last_close,
                            "created_at": now_time,
                        }
                        signal_progress.append(
                            f"{coin} OUTER_RUN第一根反向K已平倉，等待第二根收盤確認"
                        )
                    else:
                        _position_meta["outer_run_return_pending"] = True
                        signal_progress.append(
                            f"{coin} OUTER_RUN第一根反向K已成立，平倉未成交將重試"
                        )
                    return signal_progress, detected_candidates

                if has_pos and opposite_candle_exit:
                    last_close = float(df_cr_entry["close"].iloc[-1])
                    clean_sym = symbol.replace(":USDT", "") if symbol.endswith(":USDT") else symbol
                    live_price = float(
                        self.tickers.get(clean_sym, self.tickers.get(symbol, last_close))
                    )
                    if outer_run_return_exit:
                        close_reason = (
                            f"{CONTINUOUS_REVERSE_TIMEFRAME} OUTER_RUN紅K收回上軌內，提前平多"
                            if curr_side == "LONG"
                            else f"{CONTINUOUS_REVERSE_TIMEFRAME} OUTER_RUN綠K收回下軌內，提前平空"
                        )
                    else:
                        close_reason = (
                            f"{CONTINUOUS_REVERSE_TIMEFRAME} 峰頂形成前第一根收盤紅K，提前平多"
                            if curr_side == "LONG"
                            else f"{CONTINUOUS_REVERSE_TIMEFRAME} 谷底形成前第一根收盤綠K，提前平空"
                        )
                    await self.account.close_position(
                        symbol=symbol, current_price=live_price,
                        close_reason=close_reason, is_manual=True,
                    )
                    signal_progress.append(f"{coin} 反向K已先平倉，等待下一個確認峰谷")
                    return signal_progress, detected_candidates

                # OUTER_RUN 第一根反向 K 已平舊倉；空倉期間鎖住一般進場，
                # 只等第二根同向 K 收盤確認。確認後的市價單即是第三根 K 開始進場。
                pending_kc_reverse = self._kc_reversal_wait.get(symbol)
                if pending_kc_reverse and pending_kc_reverse.get("mode") not in ("OUTER_RUN_SECOND_CANDLE", "PIVOT_SECOND_CANDLE"):
                    # 清除舊版「回到KC中軌再反手」的遺留狀態。
                    self._kc_reversal_wait.pop(symbol, None)
                    pending_kc_reverse = None
                if not has_pos and pending_kc_reverse:
                    pending_age = now_time - float(
                        pending_kc_reverse.get("created_at") or now_time
                    )
                    from_side = pending_kc_reverse.get("from_side")
                    target_side = pending_kc_reverse.get("target_side")
                    if pending_age > 180.0:
                        self._kc_reversal_wait.pop(symbol, None)
                        self.account.log(
                            f"⏸️ {symbol} OUTER_RUN第二根確認逾時，取消反手",
                            "INFO",
                        )
                        return signal_progress, detected_candidates

                    confirm_status, confirm_reason, confirm_bar_id = (
                        self._outer_run_second_candle_status(
                            df_cr_entry, pending_kc_reverse,
                        )
                    )
                    if confirm_status == "WAIT":
                        wait_text = (
                            "已平多，等待第二根紅K收盤確認"
                            if from_side == "LONG"
                            else "已平空，等待第二根綠K收盤確認"
                        )
                        signal_progress.append(f"{coin} {wait_text}")
                        return signal_progress, detected_candidates
                    if confirm_status != "CONFIRMED":
                        self._kc_reversal_wait.pop(symbol, None)
                        self.account.log(
                            f"⏸️ {symbol} {confirm_reason}，等待下一個新的外軌峰谷",
                            "INFO",
                        )
                        return signal_progress, detected_candidates

                    last_close = float(df_cr_entry["close"].iloc[-1])
                    clean_sym = symbol.replace(":USDT", "") if symbol.endswith(":USDT") else symbol
                    live_price = self.tickers.get(clean_sym, self.tickers.get(symbol, last_close))
                    available = self.account.get_available_balance()
                    if TEST_BUDGET_CAP_USDT > 0:
                        available = min(available, TEST_BUDGET_CAP_USDT)
                    if daily_halt or available < MIN_TRADE_USDT:
                        self.account.log(
                            f"{symbol} OUTER_RUN第二根K已確認，但風控或可用餘額不允許開{target_side}",
                            "WARNING",
                        )
                        return signal_progress, detected_candidates

                    entry_type = (
                        "PEAK_TURN"
                        if target_side == "SHORT"
                        else "TROUGH_TURN"
                    )
                    reason = (
                        "OUTER_RUN第二根紅K確認峰頂，第三根K開始開空"
                        if target_side == "SHORT"
                        else "OUTER_RUN第二根綠K確認谷底，第三根K開始開多"
                    )
                    opened = await self._place_continuous_market_entry(
                        symbol=symbol,
                        side=target_side,
                        df=df_cr_entry,
                        live_price=live_price,
                        entry_type=entry_type,
                        reason=reason,
                        score=100,
                        timeframe=CONTINUOUS_REVERSE_TIMEFRAME,
                        wave_regime=wave_regime,
                    )
                    if opened:
                        self._kc_reversal_wait.pop(symbol, None)
                        self._continuous_last_entry_bar[symbol] = (
                            target_side, confirm_bar_id or entry_bar_id,
                        )
                    return signal_progress, detected_candidates

                if has_pos and pending_kc_reverse:
                    # 手動或其他路徑已有新部位時，舊的空倉等待狀態已失效。
                    self._kc_reversal_wait.pop(symbol, None)

                if cr_signal and self._continuous_last_entry_bar.get(symbol) == (cr_signal, entry_bar_id):
                    self.account.log(
                        f"⏸️ {symbol} 同一根K已開過 {cr_signal}，等待下一根避免重複進場",
                        "DEBUG",
                    )
                    cr_signal = None

                if cr_signal:
                    last_close = float(df_cr_entry['close'].iloc[-1])
                    clean_sym = symbol.replace(':USDT', '') if symbol.endswith(':USDT') else symbol
                    live_price = self.tickers.get(clean_sym, self.tickers.get(symbol, last_close))

                    # 空倉依趨勢方向開倉；已有持倉時，反向只接受已確認的
                    # 谷底向上（空轉多）或峰頂向下（多轉空）。交叉訊號不交易。
                    if cr_entry_type not in (
                        "TROUGH_TURN", "PEAK_TURN", "TREND_LONG", "TREND_SHORT"
                    ):
                        self.account.log(
                            f"⏸️ {symbol} {cr_entry_type} 非有效方向訊號，等待趨勢或峰谷確認",
                            "INFO",
                        )
                        return signal_progress, detected_candidates

                    signal_direction_matches = (
                        (cr_entry_type in ("TROUGH_TURN", "TREND_LONG") and cr_signal == "LONG")
                        or (cr_entry_type in ("PEAK_TURN", "TREND_SHORT") and cr_signal == "SHORT")
                    )
                    if not signal_direction_matches:
                        self.account.log(
                            f"⏸️ {symbol} 訊號方向不一致：{cr_entry_type} -> {cr_signal}，略過",
                            "WARNING",
                        )
                        return signal_progress, detected_candidates

                    # 猴市不順勢開倉，除非是剛經歷過移動停利平倉後的順勢重新承接
                    _latest_symbol_trade = next(
                        (trade for trade in self.account.trades if trade.get("symbol") == symbol),
                        {},
                    )
                    _resume_after_profitable_trailing_exit = (
                        _latest_symbol_trade.get("action", "").startswith("CLOSE_")
                        and _latest_symbol_trade.get("side") == cr_signal
                        and float(_latest_symbol_trade.get("pnl") or 0.0) > 0.0
                        and (
                            "Trailing" in str(_latest_symbol_trade.get("reason") or "")
                            or "移動停利" in str(_latest_symbol_trade.get("reason") or "")
                        )
                    )

                    if not has_pos and market_mode == "RANGE" and cr_entry_type in ("TREND_LONG", "TREND_SHORT"):
                        # 計算主導波段 (Dominant Wave)：看前一次碰到外軌是上軌還是下軌
                        # 藉此幫助在沒有持倉時，判斷順勢方向（用戶要求「如果1號沒開，看到2號時要看1號走向」）
                        atr = max(float(cr_info.get('atr') or 0.0), live_price * 1e-12)
                        from core.config import KELTNER_ATR_MULTIPLIER
                        kc_upper_series = df_cr_entry['ema_20'] + atr * KELTNER_ATR_MULTIPLIER
                        kc_lower_series = df_cr_entry['ema_20'] - atr * KELTNER_ATR_MULTIPLIER
                        recent_upper_hits = df_cr_entry['high'] >= kc_upper_series
                        recent_lower_hits = df_cr_entry['low'] <= kc_lower_series
                        last_upper_idx = df_cr_entry[recent_upper_hits].index[-1] if recent_upper_hits.any() else -1
                        last_lower_idx = df_cr_entry[recent_lower_hits].index[-1] if recent_lower_hits.any() else -1

                        if last_upper_idx > last_lower_idx:
                            dominant_wave = "SHORT" # 前一次摸到上軌，目前在下跌波段
                        elif last_lower_idx > last_upper_idx:
                            dominant_wave = "LONG"  # 前一次摸到下軌，目前在上漲波段
                        else:
                            dominant_wave = "NEUTRAL"

                        # 如果是猴市，但目前處於主導波段（前一個外軌觸發為同向），則允許順勢開倉
                        is_aligned_with_dominant_wave = (
                            (cr_entry_type == "TREND_LONG" and dominant_wave == "LONG") or
                            (cr_entry_type == "TREND_SHORT" and dominant_wave == "SHORT")
                        )

                        if not _resume_after_profitable_trailing_exit and not is_aligned_with_dominant_wave:
                            signal_progress.append(f"{coin} 猴市模式，等待谷峰反轉")
                            self.account.log(
                                f"⏸️ {symbol} 猴市(RANGE)模式：不順勢開倉，只等待峰谷反轉 ({cr_entry_type})",
                                "INFO",
                            )
                            return signal_progress, detected_candidates
                        else:
                            if _resume_after_profitable_trailing_exit:
                                self.account.log(f"✅ {symbol} 猴市模式，但剛經歷移動停利，允許順勢再開倉", "INFO")
                            elif is_aligned_with_dominant_wave:
                                self.account.log(f"✅ {symbol} 猴市模式，但符合前波主導趨勢 ({dominant_wave})，允許順勢開倉", "INFO")

                    # MA15 只過濾一般趨勢追單；已確認峰谷必然先出現在 MA15
                    # 交叉之前，若也套用此過濾會永遠錯過真正的峰頂／谷底。
                    if not cr_info.get("outer_run_direct_reverse") and not (CONTINUOUS_PIVOT_ONLY and cr_entry_type == "TROUGH_TURN") and not self._ma3_ma15_entry_allowed(
                        symbol, cr_signal, df_cr_entry, entry_type=cr_entry_type,
                    ):
                        return signal_progress, detected_candidates

                    # 由於使用者要求「完全無視K線顏色，只要紫線轉折就立刻開倉」，
                    # 這裡不再因為是綠K就暫緩做空，或是紅K就暫緩做多。
                    # 完全相信 cr_info 傳來的訊號！

                    # --- 假突破防禦機制 ---
                    if not has_pos and cr_signal:
                        is_true_peak = cr_entry_type == "PEAK_TURN"
                        is_true_trough = cr_entry_type == "TROUGH_TURN"
                        is_true_breakout = is_true_peak or is_true_trough

                        require_true_breakout = False
                        recent_trades = [t for t in self.account.trades if t.get("symbol") == symbol]
                        if recent_trades:
                            # Newest trades record is at index 0; use the just-closed trade.
                            last_trade = recent_trades[0]
                            if last_trade.get("action", "").startswith("CLOSE_") and last_trade.get("side") == cr_signal:
                                pnl = float(last_trade.get("pnl") or 0.0)
                                reason = last_trade.get("reason", "")
                                if pnl <= 0.0 or "停損" in reason or "SL" in reason.upper() or "止損" in reason:
                                    require_true_breakout = True

                        if require_true_breakout and not is_true_breakout:
                            if getattr(self, "_last_false_break_log_at", {}).get(symbol, 0) < time.time() - 60:
                                self._last_false_break_log_at = getattr(self, "_last_false_break_log_at", {})
                                self._last_false_break_log_at[symbol] = time.time()
                                self.account.log(f"🛡️ {symbol} 剛經歷 {cr_signal} 假突破平倉，拒絕弱訊號 ({cr_entry_type})，堅持等待真谷底/峰頂！", "INFO")
                            cr_signal = None

                    if not cr_signal:
                        return signal_progress, detected_candidates

                    if not has_pos:
                        entry_volume_ratio = self._channel_volume_ratio(df_cr_entry)
                        if entry_volume_ratio < KELTNER_MIN_VOLUME_RATIO:
                            signal_progress.append(
                                f"{coin} {cr_signal} 訊號成立但當下量能不足"
                                f"({entry_volume_ratio:.2f}x<{KELTNER_MIN_VOLUME_RATIO:.2f}x)，"
                                "禁止開倉並換幣"
                            )
                            return signal_progress, detected_candidates

                    # 空倉的新市價單不可追在 MA3 順向延伸的尾端：這正是
                    # 12:37 追空、12:40 大綠K追多後立刻被反向掃掉的情形。
                    # 已持倉的急速反手在持倉管理迴圈處理，故不會被這裡擋住。
                    if not has_pos:
                        entry_ma3 = float(df_cr_entry['close'].rolling(3).mean().iloc[-1])
                        entry_atr = self._resolve_entry_atr(cr_info, df_cr_entry, live_price)
                        entry_ema20 = (
                            float(df_cr_entry['ema_20'].iloc[-1])
                            if 'ema_20' in df_cr_entry.columns
                            else float(df_cr_entry['close'].ewm(span=20, adjust=False).mean().iloc[-1])
                        )
                        entry_candle = df_cr_entry.iloc[-1]
                        trend_reached_middle = bool(
                            cr_entry_type == "TREND_LONG"
                            and float(entry_candle['close']) < float(entry_candle['open'])
                            and float(entry_candle['low']) <= entry_ema20
                        ) or bool(
                            cr_entry_type == "TREND_SHORT"
                            and float(entry_candle['close']) > float(entry_candle['open'])
                            and float(entry_candle['high']) >= entry_ema20
                        )
                        trend_far_enough_from_middle = (
                            (
                                cr_entry_type == "TREND_LONG"
                                and live_price >= entry_ema20 + entry_atr * TREND_ENTRY_MIN_KC_MIDDLE_DISTANCE_ATR
                            )
                            or (
                                cr_entry_type == "TREND_SHORT"
                                and live_price <= entry_ema20 - entry_atr * TREND_ENTRY_MIN_KC_MIDDLE_DISTANCE_ATR
                            )
                        )
                        if trend_reached_middle or (
                            cr_entry_type in ("TREND_LONG", "TREND_SHORT")
                            and not trend_far_enough_from_middle
                        ):
                            middle_direction = (
                                "紅K跌到中軌" if cr_entry_type == "TREND_LONG" else "綠K漲到中軌"
                            ) if trend_reached_middle else (
                                f"距KC中軌不足{TREND_ENTRY_MIN_KC_MIDDLE_DISTANCE_ATR:.2f}ATR"
                            )
                            signal_progress.append(
                                f"{coin} {cr_signal} {middle_direction}，等待趨勢重新確認"
                            )
                            self.account.log(
                                f"⏸️ {symbol} {cr_entry_type}：{middle_direction}，暫不順勢開倉",
                                "INFO",
                            )
                            return signal_progress, detected_candidates
                        on_correct_ma3_side = (
                            (cr_signal == "LONG" and live_price >= entry_ma3)
                            or (cr_signal == "SHORT" and live_price <= entry_ma3)
                        )
                        extension_atr = (
                            (live_price - entry_ma3) / entry_atr
                            if cr_signal == "LONG" else (entry_ma3 - live_price) / entry_atr
                        )
                        pivot_turn = cr_entry_type in ("TROUGH_TURN", "PEAK_TURN")
                        # 峰谷訊號已由「收盤跨過所在區間的下一軌」確認，
                        # 大實體確認 K 也直接進場，不再額外等待回踩 MA3。
                        if pivot_turn:
                            on_correct_ma3_side = True
                            extension_atr = min(
                                extension_atr, MA3_MARKET_ENTRY_MAX_DISTANCE_ATR
                            )
                        if (
                            not on_correct_ma3_side
                            or extension_atr > MA3_MARKET_ENTRY_MAX_DISTANCE_ATR
                        ):
                            signal_progress.append(
                                f"{coin} {cr_signal} 等待回踩："
                                f"現價距MA3 {extension_atr:.2f}ATR（上限{MA3_MARKET_ENTRY_MAX_DISTANCE_ATR:.2f}ATR）"
                            )
                            self.account.log(
                                f"⏸️ {symbol} {cr_entry_type} 不追價："
                                f"現價距MA3 {extension_atr:.2f}ATR，等待回踩",
                                "INFO",
                            )
                            return signal_progress, detected_candidates

                    # 止盈後只做時間冷靜：趨勢仍在時可重新承接後半段，
                    # 但不得在同一根 K／數秒內重開；進場仍一律 Maker 預掛。
                    _last_close_ts = self.account.last_closed_at.get(symbol, 0.0)
                    if (
                        not has_pos
                        and _last_close_ts > 0
                        and cr_entry_type in ("TROUGH_TURN", "PEAK_TURN")
                        and "timestamp" in df_cr_entry.columns
                    ):
                        try:
                            _pivot_offset = int(cr_info.get("pivot_offset", -2))
                            _pivot_bar_ts = float(df_cr_entry["timestamp"].iloc[_pivot_offset])
                            if _pivot_bar_ts < 1e11:
                                _pivot_bar_ts *= 1000.0
                            if _pivot_bar_ts <= _last_close_ts * 1000.0:
                                self.account.log(
                                    f"🚫 [{symbol}] 忽略平倉前已形成的舊{cr_entry_type}訊號，等待新峰谷",
                                    "INFO",
                                )
                                return signal_progress, detected_candidates
                        except (IndexError, TypeError, ValueError):
                            pass
                    _cooldown_sec = CONTINUOUS_REENTRY_COOLDOWN_SEC
                    _elapsed = now_time - _last_close_ts
                    _latest_symbol_trade = next(
                        (trade for trade in self.account.trades if trade.get("symbol") == symbol),
                        {},
                    )
                    _resume_after_profitable_trailing_exit = (
                        _latest_symbol_trade.get("action", "").startswith("CLOSE_")
                        and _latest_symbol_trade.get("side") == cr_signal
                        and float(_latest_symbol_trade.get("pnl") or 0.0) > 0.0
                        and (
                            "Trailing" in str(_latest_symbol_trade.get("reason") or "")
                            or "移動停利" in str(_latest_symbol_trade.get("reason") or "")
                        )
                    )
                    _atr = max(float(cr_info.get("atr") or 0.0), live_price * 0.001)
                    _ma3 = float(cr_info.get("ma3_curr") or 0.0)
                    _ma15 = float(cr_info.get("ma15_curr") or 0.0)
                    _ma3_slope = float(cr_info.get("ma3_slope") or 0.0)
                    _strong_trend_continuation = (
                        cr_entry_type in ("TREND_LONG", "TREND_SHORT")
                        and abs(_ma3 - _ma15) >= _atr * 0.35
                        and (
                            (cr_signal == "LONG" and _ma3_slope >= _atr * 0.08)
                            or (cr_signal == "SHORT" and _ma3_slope <= -_atr * 0.08)
                        )
                    )
                    if (
                        not has_pos and _last_close_ts > 0 and _elapsed < _cooldown_sec
                        and not _strong_trend_continuation
                        and not _resume_after_profitable_trailing_exit
                        and cr_entry_type not in ("TROUGH_TURN", "PEAK_TURN")
                    ):
                        self.account.log(
                            f"⏳ [{symbol}] 平倉後冷靜中：{_elapsed:.0f}s / {_cooldown_sec:.0f}s，暫不重開 {cr_signal}",
                            "DEBUG",
                        )
                        return signal_progress, detected_candidates

                    # 守衛 2：ADX 強趨勢反向過濾
                    # ADX > 25 代表目前處於強趨勢（多頭或空頭均適用）。
                    # 若訊號方向與 SuperTrend（最近已收盤）方向相反，代表打算逆勢開倉，
                    # 此時強趨勢中逆勢勝率極低，直接過濾。
                    # 只對「反向開倉」（原本有持倉且即將翻方向）生效；空倉首次開倉不攔截。
                    if not has_pos and cr_signal and cr_entry_type not in ("TROUGH_TURN", "PEAK_TURN"):
                        _adx_now = float(df_cr_entry["adx"].iloc[-1]) if "adx" in df_cr_entry.columns and not pd.isna(df_cr_entry["adx"].iloc[-1]) else 0.0
                        _st_dir_now = int(df_cr_entry["st_direction"].iloc[-1]) if "st_direction" in df_cr_entry.columns else 0
                        _signal_dir = 1 if cr_signal == "LONG" else -1
                        _is_counter_trend = (_st_dir_now != 0 and _signal_dir != _st_dir_now)
                        _adx_strong = _adx_now >= 25.0
                        if _adx_strong and _is_counter_trend and _elapsed < 10.0:
                            self.account.log(
                                f"🚫 [{symbol}] ADX 強趨勢過濾：ADX={_adx_now:.1f} ≥ 25，"
                                f"ST方向={'多' if _st_dir_now == 1 else '空'}，"
                                f"訊號={cr_signal}（逆勢），距平倉 {_elapsed:.0f}s，暫緩開倉",
                                "INFO",
                            )
                            return signal_progress, detected_candidates
                    # ─────────────────────────────────────────────────────────────

                    # 峰頂/谷底提早轉向訊號：如果無持倉則開倉；如果方向相反，強制平倉反轉！
                    if cr_entry_type in ("TROUGH_TURN", "PEAK_TURN"):
                        should_open = False
                        if not has_pos:
                            should_open = bool(
                                cr_info.get("outer_run_direct_reverse")
                                or not (CONTINUOUS_PIVOT_ONLY and PIVOT_LONG_ONLY)
                                or cr_signal == "LONG"
                            )
                            if should_open:
                                self.account.log(
                                    f"✅ {symbol} confirmed {cr_entry_type}; market-enter {cr_signal}",
                                    "INFO",
                                )
                            else:
                                signal_progress.append(f"{coin} 上軌峰頂：只平多，不開空")
                        elif curr_side != cr_signal:
                            self.account.log(
                                f"{symbol} {cr_entry_type} 為持倉內反向波動；"
                                f"只要尚未突破不利方向 KC 外軌就維持 {curr_side} 持倉",
                                "INFO",
                            )
                            return signal_progress, detected_candidates

                        if should_open:
                            post_close_available = self.account.get_available_balance()
                            if TEST_BUDGET_CAP_USDT > 0:
                                post_close_available = min(post_close_available, TEST_BUDGET_CAP_USDT)
                            if daily_halt or post_close_available < MIN_TRADE_USDT:
                                self.account.log(
                                    f"{symbol} 轉彎平倉完成；風控或可用餘額不允許重新開倉",
                                    "WARNING",
                                )
                                return signal_progress, detected_candidates
                            self.account.log(
                                f"{symbol} [{cr_entry_type}] 谷底/頂部訊號：進場 {cr_signal}",
                                "INFO"
                            )
                            opened = await self._place_continuous_market_entry(
                                symbol=symbol, side=cr_signal, df=df_cr_entry,
                                live_price=live_price, entry_type=cr_entry_type,
                                reason=cr_info.get("reason", cr_entry_type),
                                score=100, timeframe=CONTINUOUS_REVERSE_TIMEFRAME,
                                market_mode=market_mode,
                                wave_regime=wave_regime,
                            )
                            if opened:
                                self._pivot_pullback_wait.pop(symbol, None)
                                self._continuous_last_entry_bar[symbol] = (cr_signal, entry_bar_id)

                    # --- MA5 穿越 MA15（金叉/死叉）：反轉/補開訊號 ---
                    # 如果無持倉，直接開倉；如果有持倉且方向相反，強制平倉反轉！
                    elif cr_entry_type in ("CROSS_UP", "CROSS_DOWN"):
                        should_open = False
                        if not has_pos:
                            should_open = True
                        elif curr_side != cr_signal:
                            self.account.log(
                                f"{symbol} {cr_entry_type} 只是 KC 內方向變化；"
                                f"未確認反向外軌峰谷，維持 {curr_side} 持倉",
                                "INFO",
                            )

                        if should_open:
                            self.account.log(
                                f"{symbol} [{cr_entry_type}] MA5穿越MA15：進場 {cr_signal}",
                                "INFO"
                            )
                            atr = cr_info.get("atr", live_price * 0.015)
                            sl_dist, tp_dist = compute_sl_tp_distance(live_price, atr)
                            sl, tp = build_sl_tp_for_side(live_price, cr_signal, sl_dist, tp_dist)
                            opened = await self.account.open_position(
                                symbol=symbol,
                                side=cr_signal,
                                price=live_price,
                                amount_usdt=self.account.get_available_balance(),
                                sl=sl,
                                tp=tp,
                                reason=cr_info.get("reason", cr_entry_type),
                                atr=atr,
                                leverage=get_leverage(symbol),
                                signal_score=85
                            )
                            if opened:
                                self._continuous_last_entry_bar[symbol] = (cr_signal, entry_bar_id)
                        else:
                            self.account.log(
                                f"{symbol} [{cr_entry_type}] MA5穿越訊號，已有 {curr_side} 持倉，不補開",
                                "DEBUG"
                            )

                    # --- MA3/MA15 trend continuation; pivots are evaluated first ---
                    elif cr_entry_type in ("TREND_LONG", "TREND_SHORT"):
                        if has_pos:
                            if curr_side != cr_signal:
                                self.account.log(
                                    f"{symbol} MA3/MA15 方向變化尚未形成反向外軌峰谷；"
                                    f"維持 {curr_side} 持倉",
                                    "INFO",
                                )
                            else:
                                self.account.log(
                                    f"{symbol} [{cr_entry_type}] continuation; already {curr_side}",
                                    "DEBUG",
                                )
                        else:
                            entry_label = (
                                "trailing-profit continuation"
                                if _resume_after_profitable_trailing_exit
                                else "MA3/MA15 trend continuation"
                            )
                            self.account.log(
                                f"{symbol} [{cr_entry_type}] {entry_label}; enter {cr_signal}",
                                "INFO",
                            )
                            opened = await self._place_continuous_market_entry(
                                symbol=symbol, side=cr_signal, df=df_cr_entry,
                                live_price=live_price, entry_type=cr_entry_type,
                                reason=cr_info.get("reason", cr_entry_type),
                                score=int(cr_info.get("pivot_score", 85)),
                                timeframe=CONTINUOUS_REVERSE_TIMEFRAME,
                                market_mode=market_mode,
                                wave_regime=wave_regime,
                            )
                            if opened:
                                self._continuous_last_entry_bar[symbol] = (cr_signal, entry_bar_id)

                if symbol in self.account.positions:
                    position = self.account.positions[symbol]
                    sc = position.get("signal_score") or position.get("raw_signal_score")
                    position_direction = "多單" if position.get("side") == "LONG" else "空單"
                    signal_progress.append(f"{coin} {position_direction} {int(sc) if sc else '--'}分,連續轉向持倉中")
                return signal_progress, detected_candidates

            if symbol in self.account.positions:
                position = self.account.positions[symbol]
                # signal_score 重啟後從交易所同步回來可能是 None
                # 優先用 signal_score，其次 raw_signal_score，都沒有才顯示 --
                raw_sc = position.get("raw_signal_score")
                sc = position.get("signal_score") or raw_sc
                position_score = f"{int(sc)}" if sc is not None else "--"
                position_direction = "多單" if position.get("side") == "LONG" else "空單"
                signal_progress.append(
                    f"{coin} {position_direction} {position_score}分,持倉中"
                )
                return signal_progress, detected_candidates

            if symbol in self.pending_pullback_candidates:
                pending = self.pending_pullback_candidates[symbol]
                stage = (
                    f"等待現價Maker掛單 @ {pending.get('target_price'):.8g}"
                    if pending.get("entry_mode") == "CURRENT_MAKER"
                    else "已觸價，等待1m收盤反轉確認"
                    if pending.get("touched_at")
                    else f"等待{pending.get('pullback_depth', 0):.0%}回踩觸價 @ {pending.get('target_price'):.8g}"
                )
                signal_progress.append(
                    f"{coin} {pending['side']} {pending['score']}分,{stage}"
                )
                return signal_progress, detected_candidates

            # 如果已經掛著限價單，跳過訊號偵測（避免重複掛單）
            if symbol in self.account.pending_limit_orders:
                pending = self.account.pending_limit_orders[symbol]
                entry_context = pending.get("entry_context") or {}
                entry_mode = entry_context.get("entry_mode")
                pullback_score = entry_context.get("pullback_confirmation_score")
                confirmation_reason = (
                    f"回撤中底點掛單等待成交 @ {pending.get('target_price')}"
                    if entry_mode == "MA5_BOTTOM_LIMIT"
                    else
                    f"原始{pending.get('signal_score', 0)}分 → "
                    f"回踩確認{pullback_score}分，掛單等待成交 @ "
                    f"{pending.get('target_price')}"
                    if pullback_score is not None
                    else f"限價單等待成交 @ {pending.get('target_price')}"
                )
                signal_progress.append(self._format_signal_progress(
                    symbol,
                    {
                        "action": "WAIT_PULLBACK",
                        "score": pending.get("signal_score", 0),
                        "confirmation_reason": confirmation_reason,
                    },
                    pending.get("side"),
                ))
                return signal_progress, detected_candidates

            retry_after = self._pullback_retry_after.get(symbol, 0.0)
            if retry_after > now_time:
                signal_progress.append(
                    f"{coin} {direction_text} 資格未通過,回踩失效冷卻{int(retry_after - now_time)}秒"
                )
                self._record_entry_filter(
                    symbol, {"action": "HOLD", "reason": "回踩失效冷卻"},
                    direction_text, "pullback_retry_cooldown",
                )
                return signal_progress, detected_candidates

            if symbol in ENTRY_DISABLED_SYMBOLS:
                signal_progress.append(
                    f"{coin} {direction_text} 資格未通過,暫停新倉"
                )
                self._record_entry_filter(
                    symbol, {"action": "HOLD", "reason": "暫停新倉"},
                    direction_text, "symbol_disabled",
                )
                return signal_progress, detected_candidates

            # 4.1 低流動性過濾
            vol_24h = self.ticker_volumes.get(symbol, 0.0)
            if vol_24h > 0 and vol_24h < 500000.0:
                signal_progress.append(
                    f"{coin} {direction_text} 資格未通過,24h流動性不足"
                )
                self._record_entry_filter(
                    symbol, {"action": "HOLD", "reason": "24h流動性不足",
                             "diagnostics": {"quote_volume_24h": float(vol_24h)}},
                    direction_text, "liquidity_low",
                )
                return signal_progress, detected_candidates

            live_price = float(self.tickers.get(symbol) or 0.0)
            df_1m = await self.fetch_klines(symbol, timeframe="1m", limit=30, keep_live=True)
            if df_1m.empty:
                return signal_progress, detected_candidates

            # 只讀已收盤 1m K，避免未收線 RSI、量能與 MA3 重繪。
            df_1m = drop_unclosed_candle(df_1m, "1m")
            if len(df_1m) < 20:
                return signal_progress, detected_candidates
            df_1m = self.strategy.compute_indicators(df_1m)

            from core.indicators import detect_ma3_ma15_cross_and_turn
            pivot = detect_ma3_ma15_cross_and_turn(df_1m, allow_live_pivot=False)
            entry_type = pivot.get("entry_type")
            if False: # 根據使用者要求已完全關閉非 Channel Swing 的強勢進場 (MA3/MA15 PIVOT_TURN)
                direction = "LONG" if entry_type == "TROUGH_TURN" else "SHORT"

                passed_ema_filter = True
                from core.config import ENABLE_1H_EMA50_FILTER, STRUCTURED_1H_EMA50_TOLERANCE_PCT
                ema_50_1h = self.ema_50_1h_cache.get(symbol)
                if ENABLE_1H_EMA50_FILTER and ema_50_1h is not None and ema_50_1h > 0:
                    ema_lower_bound = ema_50_1h * (1.0 - STRUCTURED_1H_EMA50_TOLERANCE_PCT)
                    ema_upper_bound = ema_50_1h * (1.0 + STRUCTURED_1H_EMA50_TOLERANCE_PCT)
                    if direction == "LONG" and live_price < ema_lower_bound:
                        passed_ema_filter = False
                    elif direction == "SHORT" and live_price > ema_upper_bound:
                        passed_ema_filter = False

                if passed_ema_filter:
                    exhaustion = check_exhaustion_entry_filters(df_1m, direction)
                    if exhaustion.get("passed"):
                        sig = {
                            "detected": True,
                            "side": direction,
                            "score": 100,
                            "price": live_price,
                            "atr": float(pivot.get("atr") or live_price * 0.015),
                            "entry_mode": "PIVOT_TURN",
                            "profit_profile": "TREND_EXTENSION",
                            "action": "ENTER_MARKET",
                            "target_price": None,
                            "signal_candle_low": float(df_1m["low"].iloc[-1]),
                            "signal_candle_high": float(df_1m["high"].iloc[-1]),
                            "symbol": symbol,
                            "live_price": live_price,
                            "extreme_age_bars": exhaustion.get("extreme_age_bars"),
                            "extreme_rsi": exhaustion.get("extreme_rsi"),
                            "extreme_volume_ratio": exhaustion.get("extreme_volume_ratio"),
                            "reason": (
                            f"{entry_type}: {pivot.get('reason', '谷底／峰頂轉彎')}｜"
                            f"{exhaustion.get('reason')}"
                        ),
                        }
                        detected_candidates.append(sig)
                    else:
                        signal_progress.append(
                            f"{coin} {direction} 轉彎成立，但{exhaustion.get('reason')}"
                        )
                else:
                    signal_progress.append(
                        f"{coin} {direction} 未通過 1H EMA50 趨勢過濾"
                    )
            else:
                signal_progress.append(
                    f"{coin} 雙向等待谷底／峰頂轉彎({entry_type or 'WAIT'})"
                )


        except Exception as e:
            self.account.log(f'⚠️ [{symbol}] 處理失敗: {e}', 'WARNING')
        return signal_progress, detected_candidates

    async def _main_loop(self):
        while self.is_running:
            try:
                # 幣種輪替已移到獨立的 _rotation_loop() 背景任務執行，
                # 不再佔用這個迴圈的 await 鏈，停損停利不會被 AI 呼叫延遲。

                # 2. 更新與執行持倉部位
                await self.account.update_positions(self.tickers)
                # 冷卻時間唯一資料來源是 self.account.last_closed_at（見
                # testnet_account.py），不管平倉是這裡的主迴圈觸發，還是
                # /api/prices、/api/status 這些跟主迴圈不同步的網頁輪詢
                # 呼叫觸發，都會準確記錄，不會像原本這裡自己拿前後快照
                # 判斷那樣，漏掉別的呼叫者觸發的平倉。

                # 3. 1h 快取獨立執行；外部 K 線請求變慢時不可阻塞 1m 進場掃描。
                if self.trend_cache_task is None or self.trend_cache_task.done():
                    self.trend_cache_task = asyncio.create_task(self.update_1h_trend_cache())

                # 4. 先等觸價與 1m 反轉確認，再驗證短效掛單，最後才查成交。
                now_time = time.time()
                await self._monitor_pullback_candidates(now_time)
                await self._validate_pending_limit_orders(now_time)



                # 5. 開倉訊號檢查 — 依可用餘額填充預算，用完為止
                # 每日虧損熔斷：觸發時只跳過本段（不開新倉），上面的持倉管理
                # （止損/止利/移動止利/分批止盈）完全不受影響。
                daily_halt, _daily_loss_pct = self.account.daily_loss_limit_hit()
                available_balance = self.account.get_available_balance()
                if TEST_BUDGET_CAP_USDT > 0:
                    available_balance = min(available_balance, TEST_BUDGET_CAP_USDT)
                from core.config import ENABLE_CONTINUOUS_REVERSE_MODE
                # 開機先完成第一輪全市場排名，避免種子幣在真正最強候選出爐前搶走唯一槽位。
                rotation_ready = (
                    not SYMBOL_ROTATION_ENABLED
                    or self.symbol_rotation.last_rotation_at > 0.0
                )
                entry_scan_allowed = (
                    not daily_halt
                    and available_balance >= MIN_TRADE_USDT
                    and rotation_ready
                )
                manage_continuous_position = ENABLE_CONTINUOUS_REVERSE_MODE and bool(self.account.positions)
                if entry_scan_allowed or manage_continuous_position:
                    signal_progress = []
                    detected_candidates = []

                    now_time = time.time()

                    # BTC 1m 只在強脈衝時守新倉方向；中性時不干預個幣峰谷。
                    btc_1m_turn = None
                    if BTC_1M_PULSE_FILTER_ENABLED:
                        try:
                            btc_df_1m = await self.fetch_klines(
                                "BTC/USDT", timeframe="1m", limit=30, keep_live=True,
                            )
                            if not btc_df_1m.empty:
                                btc_df_1m = self.strategy.compute_indicators(btc_df_1m.copy())
                                btc_live = float(
                                    self.tickers.get("BTC/USDT")
                                    or btc_df_1m["close"].iloc[-1]
                                )
                                btc_1m_turn = self._detect_btc_1m_pulse(
                                    btc_df_1m, btc_live,
                                )
                                self._begin_btc_lead_shadow(btc_1m_turn, btc_df_1m)
                        except Exception as e:
                            self.account.log(f"⚠️ 無法取得 BTC 1m 脈衝資料: {e}", "WARNING")

                    # 幣種輪替現在跑在獨立背景任務，可能在這個迴圈 await 期間改動 DEFAULT_SYMBOLS，
                    # 用 list(...) 先拍一份快照，避免邊跑邊被換牌造成跳過或重複掃描。
                    symbols_snapshot = list(dict.fromkeys([*DEFAULT_SYMBOLS, *self.account.positions.keys()]))
                    tasks = [
                        self._process_single_symbol(symbol, now_time, btc_1m_turn, daily_halt)
                        for symbol in symbols_snapshot
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for res in results:
                        if isinstance(res, Exception):
                            self.account.log(f"⚠️ 幣種掃描例外: {res}", "WARNING")
                        else:
                            prog, cands = res
                            signal_progress.extend(prog)
                            detected_candidates.extend(cands)
                    opened_any = False
                    if detected_candidates:
                        detected_candidates, skipped_same_side = (
                            self._select_strongest_same_side_candidates(
                                detected_candidates
                            )
                        )
                        for skipped in skipped_same_side:
                            skipped_coin = skipped["symbol"].replace("/USDT", "")
                            signal_progress.append(
                                f"{skipped_coin} {skipped['side']} 同向候選未入選；"
                                f"預估利潤空間 "
                                f"{float(skipped.get('profit_potential') or 0.0):.2f}%／"
                                f"趨勢品質 {float(skipped.get('trend_quality') or 0.0):.2f}"
                            )
                        for sig in detected_candidates:
                            symbol = sig["symbol"]
                            coin = symbol.replace("/USDT", "")
                            direction_text = "多單" if sig["side"] == "LONG" else "空單"

                            if MAX_SLOTS > 0 and len(self.account.positions) >= MAX_SLOTS:
                                signal_progress.append(f"{coin} {direction_text} 資格未通過,槽位已滿({MAX_SLOTS})")
                                continue

                            opened = await self._place_structured_entry(
                                symbol,
                                sig,
                                sig["live_price"]
                            )
                            opened_any = bool(opened) or opened_any

                    refresh_needed = self._candidate_board_refresh_needed(
                        opened_any,
                        len(self.account.positions),
                        len(self.account.pending_limit_orders),
                        MAX_SLOTS,
                        now_time - self._last_empty_pivot_rescan_at,
                    )
                    if refresh_needed:
                        self._last_empty_pivot_rescan_at = now_time
                        if opened_any:
                            request_rescan = getattr(self.symbol_rotation, "request_rescan", None)
                            if callable(request_rescan):
                                request_rescan(symbols_snapshot)
                            else:
                                self.symbol_rotation.last_rotation_at = 0.0
                            self.rotation_event.set()
                            self.account.log(
                                "🔄 [成交後刷新] 保留新持倉；暫時排除其餘未持倉牌面，"
                                "立即尋找下一個可開倉幣種",
                                "INFO",
                            )
                        else:
                            self.account.log(
                                "👀 [空槽觀察] 目前牌面尚無可開倉；保留現有幣種繼續觀察，"
                                "不因空槽排除整批幣種",
                                "INFO",
                            )

                    self._log_signal_progress(signal_progress, now_time, symbols_snapshot)
                    if now_time - self._last_diagnostic_stats_save_at >= 60.0:
                        self.account.save_state()
                        self._last_diagnostic_stats_save_at = now_time

                # ✅ 修正：紙交易模式下將輪詢間隔縮短為 1 秒，以實現近乎即時的平倉監控
                # 實體模式下保持 5 秒以防 API 頻率超限。
                sleep_sec = 1 if PAPER_TRADING else 5
                await asyncio.sleep(sleep_sec)
            except asyncio.CancelledError:
                break
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                self.account.log(f"🌐 網路連線暫時中斷，正在自動重試... ({type(e).__name__})", "WARNING")
                await asyncio.sleep(5)
            except ccxt.ExchangeError as e:
                self.account.log(f"⚠️ 交易所 API 權限或請求異常: {str(e)}", "WARNING")
                await asyncio.sleep(5)
            except Exception as e:
                self.account.log(f"⚠️ 引擎運作異常: {str(e)}", "WARNING")
                await asyncio.sleep(5)

# Singleton global instance
engine = TradingEngine()
