import asyncio
import re
import time
import ccxt.async_support as ccxt
import ccxt.pro as ccxtpro
import pandas as pd
import weakref
from typing import Dict, List
from core.config import (
    DEFAULT_SYMBOLS, MAX_SLOTS, MAX_SAME_SIDE_POSITIONS, TRADE_AMOUNT_USDT, TREND_FILTER_EMA_PERIOD,
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
    CONTINUOUS_TREND_ONLY, CONTINUOUS_PIVOT_ONLY, PIVOT_LONG_ONLY, PIVOT_EARLY_ENTRY_MAX_REBOUND_ATR, PIVOT_MIN_KC_WIDTH_PCT, MA3_MARKET_ENTRY_MAX_DISTANCE_ATR,
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
)
from core.strategy import (
    SuperTrendKeltnerStrategy, build_sl_tp_for_side, compute_sl_tp_distance,
    compute_pullback_target, compute_net_reward_risk, detect_ma5_reversal,
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
        # ADX + MA3/MA15 距離的雙門檻狀態；預設 RANGE，需連續 3 根確認才進 TREND。
        self._continuous_wave_regime: Dict[str, str] = {}
        # 在短週期 TREND 之上，再以個幣與 BTC 的 1h 趨勢確認牛／熊市。
        # RANGE 保留給猴市的峰谷交易；BULL/BEAR 則只做同向順勢單。
        self._continuous_market_mode: Dict[str, str] = {}
        # 外軌峰谷先平倉後，等待反向 K 回到 KC 中軌才開反向倉。
        self._kc_reversal_wait: Dict[str, dict] = {}
        # Live-pivot reversals may be evaluated every 3 seconds; allow at most
        # one reversal in the same 1m candle and require two scans for MA3.
        self._live_pivot_reversal_bar: Dict[str, int] = {}
        self._fast_pivot_confirmations: Dict[str, dict] = {}
        self.pivot_prealerts: Dict[str, dict] = {}
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
        self.trend_follow_task: asyncio.Task = None
        self.trailing_sl_task: asyncio.Task = None
        self.market_scan_task: asyncio.Task = None
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

    def _detect_ma5_consolidation(self, df: pd.DataFrame) -> dict:
        """Detect a confirmed low-volatility MA5 chop zone on closed candles."""
        result = {"detected": False, "reason": "insufficient_data"}
        if df is None or len(df) < 20:
            return result

        work = df.copy()
        if "ma5" not in work.columns:
            work["ma5"] = work["close"].rolling(window=5).mean()

        high = work["high"].astype(float)
        low = work["low"].astype(float)
        close = work["close"].astype(float)
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(window=14, min_periods=5).mean()
        atr = float(atr_series.iloc[-1])
        if pd.isna(atr) or atr <= 0:
            atr = max(float(close.iloc[-1]) * 0.015, 1e-12)

        if "adx" in work.columns and not pd.isna(work["adx"].iloc[-1]):
            adx = float(work["adx"].iloc[-1])
        else:
            period = 14
            up_move = high.diff()
            down_move = -low.diff()
            plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
            minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
            tr_smooth = tr.ewm(alpha=1 / period, adjust=False).mean()
            plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / (tr_smooth + 1e-12)
            minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / (tr_smooth + 1e-12)
            dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12)
            adx = float(dx.ewm(alpha=1 / period, adjust=False).mean().iloc[-1])

        recent_ma5 = work["ma5"].dropna().astype(float).iloc[-5:]
        if len(recent_ma5) < 5:
            return result
        steps = recent_ma5.diff().dropna()
        min_turn_step = atr * 0.005
        signs = [1 if step > min_turn_step else -1 if step < -min_turn_step else 0 for step in steps]
        meaningful_signs = [sign for sign in signs if sign]
        direction_changes = sum(
            current != previous
            for previous, current in zip(meaningful_signs, meaningful_signs[1:])
        )
        ma5_range_atr = (float(recent_ma5.max()) - float(recent_ma5.min())) / atr
        last_three_flat = bool((steps.abs().iloc[-3:] <= atr * 0.08).all())
        detected = bool(
            ma5_range_atr <= 0.15
            and direction_changes >= 2
            and last_three_flat
            and adx <= 18.0
        )
        return {
            "detected": detected,
            "reason": "ma5_chop" if detected else "not_consolidating",
            "atr": atr,
            "adx": adx,
            "ma5_range_atr": ma5_range_atr,
            "direction_changes": direction_changes,
            "range_high": float(high.iloc[-5:].max()),
            "range_low": float(low.iloc[-5:].min()),
        }

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
        asyncio.create_task(self._fixed_stop_loss_loop())
        # KC失敗、ATR追蹤、RR分批與1h翻向出場背景任務
        self.trend_follow_task = asyncio.create_task(self._run_structured_exits())
        # 舊移動停損任務保留但預設停用，避免與結構ATR追蹤衝突
        self.trailing_sl_task = asyncio.create_task(self._run_trailing_sl_loop())
        # 無 MA5 模式不啟動全市場 MA5 掃描。
        self.market_scan_task = None
        # 獨立的即時價格更新任務
        self.ticker_task = asyncio.create_task(self._ticker_loop())
        # 啟動時檢查既有歷史；摘要未變時會由 digest 快取直接略過。
        self.request_trade_analysis()

    async def stop(self):
        if not self.is_running:
            return
        self.is_running = False
        if self.task:
            self.task.cancel()
        if self.rotation_task:
            self.rotation_task.cancel()
        if self.analysis_task:
            self.analysis_task.cancel()
        if self.trend_cache_task:
            self.trend_cache_task.cancel()
        if self.trigger_task:
            self.trigger_task.cancel()
        if self.trend_follow_task:
            self.trend_follow_task.cancel()
        if self.trailing_sl_task:
            self.trailing_sl_task.cancel()
        if self.market_scan_task:
            self.market_scan_task.cancel()
        if hasattr(self, 'ticker_task') and self.ticker_task:
            self.ticker_task.cancel()
        await self.exchange.close()
        await self.ws_exchange.close()
        await self.execution_exchange.close()
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
                if now_time - self.symbol_rotation.last_rotation_at >= SYMBOL_ROTATION_INTERVAL_SEC:
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
                await asyncio.sleep(30)  # 30 秒檢查一次是否到了下次輪替/健康檢查時間
            except asyncio.CancelledError:
                break
            except Exception as exc:
                # 輪替與 AI 都是輔助層，失敗時不能中斷持倉管理與主策略。
                self.symbol_rotation.last_rotation_at = time.time()
                self.symbol_rotation.last_reason = f"輪替失敗，保留原牌面：{type(exc).__name__}"
                self.account.log(f"⚠️ [幣種輪替] 暫時失敗，保留原牌面並繼續交易：{type(exc).__name__}: {exc}", "WARNING")
                await asyncio.sleep(30)

    async def _run_market_wide_ma5_scan_loop(self):
        """每 5 分鐘掃一次 DEFAULT_SYMBOLS 之外的合約候選幣，用跟主迴圈
        完全相同的 detect_ma5_reversal 判斷；一旦掃到已經達標（detected=
        True）但還沒被監控的幣，直接加入 DEFAULT_SYMBOLS，讓主迴圈下一輪
        (5秒內) 用正常流程（結構性SL/動態槓桿/KC驗證）接手進場，不用等
        15分鐘一次的幣種輪替（那邊排的是綜合品質分數，不是「現在有沒有
        拐頭」）。跟 bot process 生命週期綁在一起，沒有排程時間上限。"""
        SCAN_INTERVAL_SEC = 300
        while self.is_running:
            try:
                await self.exchange.load_markets()
                tickers = await self.exchange.fetch_tickers()
                candidates = self.symbol_rotation.market_candidates(
                    tickers, self.exchange.markets, self.execution_symbols
                )
                watched = set(DEFAULT_SYMBOLS) | set(self.account.positions.keys())
                to_scan = [s for s in candidates if s not in watched]

                for symbol in to_scan:
                    try:
                        df_1m = await self.fetch_klines(symbol, timeframe="1m", limit=100)
                        if df_1m.empty or len(df_1m) < 50:
                            continue
                        df_1h = await self.fetch_klines(symbol, timeframe="1h", limit=150)
                        if df_1h.empty or len(df_1h) < 30:
                            continue

                        df_1m = self.strategy.compute_indicators(df_1m)
                        computed_1h = self.strategy.compute_indicators(df_1h)
                        ema_50_1h = float(
                            df_1h['close'].ewm(span=min(len(df_1h), TREND_FILTER_EMA_PERIOD), adjust=False)
                            .mean().iloc[-1]
                        )
                        st_direction_1h = int(computed_1h['st_direction'].iloc[-1])
                        ma5 = float(df_1m['close'].rolling(5).mean().iloc[-1])
                        ma15 = float(df_1m['close'].rolling(15).mean().iloc[-1])
                        current_direction = "LONG" if ma5 > ma15 else "SHORT"

                        sig = detect_ma5_reversal(
                            df_1m,
                            side=current_direction,
                            ema_50_1h=ema_50_1h,
                            st_direction_1h=st_direction_1h,
                            btc_st_direction_1h=self.btc_1h_st_direction,
                            btc_st_flip_age=self.btc_1h_st_flip_age,
                            symbol=symbol,
                            indicators_precomputed=True,
                        )
                        if sig["detected"] and symbol not in DEFAULT_SYMBOLS:
                            if getattr(config, "ENABLE_SYMBOL_ROTATION", True):
                                DEFAULT_SYMBOLS.append(symbol)
                                self.account.log(
                                    f"🎯 [全市場掃描] {symbol.replace('/USDT', '')} {sig['side']} "
                                    f"{sig.get('score', 65)}分已符合MA5拐頭條件（原本不在監控名單內），"
                                    f"已加入監控，交由主迴圈接手進場｜{sig['reason']}",
                                    "SUCCESS",
                                )
                            else:
                                self.account.log(
                                    f"🎯 [全市場掃描] {symbol.replace('/USDT', '')} {sig['side']} "
                                    f"{sig.get('score', 65)}分符合進場條件，但因自動幣種輪替已關閉，略過加入監控清單",
                                    "INFO"
                                )
                    except Exception:
                        continue
                    await asyncio.sleep(0.05)

                await asyncio.sleep(SCAN_INTERVAL_SEC)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.account.log(f"⚠️ [全市場掃描] 暫時失敗：{type(exc).__name__}: {exc}", "WARNING")
                await asyncio.sleep(SCAN_INTERVAL_SEC)


    async def _fixed_stop_loss_loop(self):
        """Runs every 10 seconds to check for a hard 0.5% stop loss using mark price"""
        while self.is_running:
            try:
                for symbol, position in list(self.account.positions.items()):
                    if CONTINUOUS_PIVOT_ONLY:
                        continue
                    live_price = float(self.tickers.get(symbol) or position.get("mark_price") or position["entry_price"])
                    entry_price = float(position["entry_price"])
                    side = position["side"]
                    entry_mode = str(position.get("entry_mode") or position.get("reason") or "")
                    
                    if entry_price > 0 and live_price > 0:
                        if side == "LONG":
                            pnl_pct = (live_price - entry_price) / entry_price
                        else:
                            pnl_pct = (entry_price - live_price) / entry_price
                            
                        import core.config as config

                        # === KC 破軌緊急停損 (全模式適用) ===
                        # 只要價格向逆方向貫穿 KC 軌道，強制停損。
                        # 這是防止帳戶在單邊暴走行情中死扛的最後防線。
                        trigger = self.position_triggers.get(symbol, {})
                        kc_upper = float(trigger.get("kc_upper") or 0)
                        kc_lower = float(trigger.get("kc_lower") or 0)
                        if kc_upper > 0 and kc_lower > 0:
                            is_kc_ripped = (
                                (side == "LONG" and live_price < kc_lower)
                                or (side == "SHORT" and live_price > kc_upper)
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
                            
                        is_structure_exit_mode = str(
                            position.get("wave_regime")
                            or self.account.position_meta.get(symbol, {}).get("wave_regime")
                            or ""
                        ).upper() in ("RANGE", "TREND")
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
                    is_cr_position = any(k in pos_reason for k in ("TROUGH_TURN", "PEAK_TURN", "KC_MIDDLE_PEAK_REVERSE", "KC_MIDDLE_TROUGH_REVERSE", "CROSS_UP", "CROSS_DOWN", "TREND_LONG", "TREND_SHORT"))
                    from core.config import CONTINUOUS_REVERSE_TIMEFRAME
                    exit_tf = CONTINUOUS_REVERSE_TIMEFRAME if is_cr_position else MA5_EXIT_TIMEFRAME
                    # keep_live=True: 用最新未收盤的 tick 資料即時判斷，只要 MA5 反向彎了就立刻走，不需等該分 K 收盤
                    df = await self.fetch_klines(symbol, timeframe=exit_tf, limit=30, keep_live=True)
                    if CONTINUOUS_PIVOT_ONLY and is_cr_position:
                        df = drop_unclosed_candle(df, exit_tf)
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
                            df, position.get("side"),
                            armed=bool(position_meta.get("kc_outer_run_armed")),
                        )
                        position_meta["kc_outer_run_armed"] = kc_outer_lock["armed"]
                        position["kc_outer_run_armed"] = kc_outer_lock["armed"]
                        trigger["kc_outer_run_armed"] = kc_outer_lock["armed"]
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
                    # 使用已收盤的資料進行平倉確認
                    df_closed = drop_unclosed_candle(df, exit_tf)
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
                    exit_signal_info = detect_ma3_ma15_cross_and_turn(
                        df_closed, allow_live_pivot=False
                    )
                    false_breakout_hold = str(
                        exit_signal_info.get("reason", "")
                    ).startswith("假突破過濾")
                    trigger["false_breakout_hold"] = false_breakout_hold
                    if is_cr_position:
                        # 這個訊息是「等下一個相反峰谷」的CR專屬語意，非CR
                        # 部位維持上面 _ma5_exit_ready 算出來的持倉時間/幅度
                        # 閘門說明，不要被這裡蓋掉。
                        trigger["ma5_exit_gate"] = (
                            "外軌延伸：峰谷確認先平倉，中軌再反手"
                            if kc_outer_lock.get("blocked")
                            else "假突破：保持原持倉方向"
                            if false_breakout_hold
                            else (
                                "強趨勢：追蹤最高/最低，等待兩根衰退確認"
                                if str(position.get("wave_regime") or position_meta.get("wave_regime") or "").upper() == "TREND"
                                else "等待下一個相反峰谷"
                            )
                        )
                    wave_regime = str(
                        position.get("wave_regime")
                        or position_meta.get("wave_regime")
                        or self._continuous_wave_regime.get(symbol, "TREND")
                    ).upper()
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
                    trigger["trend_exhaustion_exit"] = trend_exhaustion_exit
                    trigger["trend_retrace_atr"] = trend_exit_info.get("retrace_atr", 0.0)
                    trigger["trend_extreme_price"] = trend_exit_info.get("extreme_price")

                    # 立即轉向（fast_pivot）：紅K/綠K已回到 KC 中軌，無須等待外軌 blocked 狀態
                    # 直接視為「中軌反轉」平仓，不論外軌是否已解鎖。
                    immediate_reversal = bool(
                        not CONTINUOUS_PIVOT_ONLY
                        and exit_signal_info.get("fast_pivot")
                        and (
                            (position.get("side") == "LONG"
                             and exit_signal_info.get("entry_type") == "PEAK_TURN")
                            or (position.get("side") == "SHORT"
                                and exit_signal_info.get("entry_type") == "TROUGH_TURN")
                        )
                    )
                    opposite_pivot = (
                        wave_regime == "RANGE"
                        and (
                        (position.get("side") == "LONG"
                         and exit_signal_info.get("entry_type") == "PEAK_TURN")
                        or (position.get("side") == "SHORT"
                            and exit_signal_info.get("entry_type") == "TROUGH_TURN")
                    ))
                    outer_rail_flatten = bool(
                        immediate_reversal or (kc_outer_lock.get("blocked") and opposite_pivot)
                    )
                    if CONTINUOUS_PIVOT_ONLY and PIVOT_LONG_ONLY:
                        pivot_only_upper_exit = False
                        if position.get("side") == "LONG" and len(df_closed) >= 2:
                            pivot_exit_bar = df_closed.iloc[-1]
                            pivot_exit_previous = df_closed.iloc[-2]
                            pivot_only_upper_exit = bool(
                                float(pivot_exit_bar["high"]) >= float(pivot_exit_bar["kc_upper"])
                                and float(pivot_exit_bar["close"]) < float(pivot_exit_bar["open"])
                                and float(pivot_exit_bar["close"]) < float(pivot_exit_previous["close"])
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
                    should_auto_close = bool(
                        (not false_breakout_hold or live_pivot_exit or trend_exhaustion_exit or outer_rail_flatten)
                        and (
                            opposite_pivot or trend_exhaustion_exit or pre_turn_exit or live_pivot_exit
                            if is_cr_position
                            else (
                                (trigger.get("ma5_reversed") and ma5_exit_ready)
                                or trigger.get("is_panic_reversal")
                                or pre_turn_exit
                            )
                        )
                    )
                    pivot_exit_ready = (
                        opposite_pivot or trend_exhaustion_exit or pre_turn_exit or live_pivot_exit
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
                        and (not bottom_grace or outer_rail_flatten)
                        and should_auto_close
                    ):
                        if outer_rail_flatten:
                            close_reason = (
                                f"{exit_tf}外軌峰頂確認，先平多；等待紅K回到KC中軌再開空"
                                if position.get("side") == "LONG"
                                else f"{exit_tf}外軌谷底確認，先平空；等待綠K回到KC中軌再開多"
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
                            and not outer_rail_flatten
                        ):
                            self.account.log(
                                f"⏸️ [未鎖利技術出場略過] {symbol} {close_reason}；"
                                f"目前逆向 {adverse_pct:.2%} < 硬停損 {FIXED_STOP_LOSS_PCT:.2%}",
                                "INFO",
                            )
                        else:
                            closed = await self.account.close_position(
                                symbol, curr_p, close_reason, is_manual=(pre_turn_exit or live_pivot_exit or outer_rail_flatten)
                            )
                            if closed and outer_rail_flatten and not CONTINUOUS_PIVOT_ONLY:
                                target_side = "SHORT" if position.get("side") == "LONG" else "LONG"
                                self._kc_reversal_wait[symbol] = {
                                    "from_side": position.get("side"),
                                    "target_side": target_side,
                                    "pivot_type": exit_signal_info.get("entry_type"),
                                    "created_at": time.time(),
                                    "middle_reached": False,
                                }
                                self.account.log(
                                    f"⏳ {symbol} 外軌峰谷已先平{'多' if position.get('side') == 'LONG' else '空'}，"
                                    f"等待反向K回到KC中軌再開{target_side}",
                                    "INFO",
                                )
                            if closed and live_pivot_exit:
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
            managed_modes = {
                'BREAKOUT', 'SUPPORT_PULLBACK', 'MOMENTUM_CROSS',
                'MA5_REVERSAL', 'MA5_BOTTOM_LIMIT', 'CURRENT_MAKER', 'PULLBACK',
            }
            meta = self.account.position_meta.setdefault(symbol, {})
            entry_mode = position.get("entry_mode") or meta.get("entry_mode")
            if entry_mode not in managed_modes:
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
            if unrealized_pnl_pct > 0.0:
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
                            atr_value = meta.get("atr", curr_p * 0.015)
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
            amount = dynamic_trade_amount
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
        """將短週期波動型態升級為猴市／牛市／熊市交易模式。"""
        if str(wave_regime).upper() != "TREND":
            return "RANGE"

        symbol_st = int(self.st_direction_1h_cache.get(symbol) or 0)
        btc_st = int(self.btc_1h_st_direction or 0)
        ema50 = float(self.ema_50_1h_cache.get(symbol) or 0.0)
        if symbol_st == 1 and btc_st == 1 and ema50 > 0 and price >= ema50:
            return "BULL"
        if symbol_st == -1 and btc_st == -1 and ema50 > 0 and price <= ema50:
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
        if not self._same_side_entry_allowed(symbol, side):
            return False
        stop_cooldown_fn = getattr(
            self.symbol_rotation, "get_stop_cooldown_remaining", lambda *_args: 0.0
        )
        stop_cooldown_remaining = float(stop_cooldown_fn(symbol, side) or 0.0)
        if stop_cooldown_remaining > 0 and entry_mode not in ("EXHAUSTION_SNIPER", "PIVOT_TURN"):
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
        # 最後一道方向守門：避免在高週期趨勢不符時開錯方向 (MA5_CROSS_PIVOT 策略除外)
        if entry_mode not in ("MA5_CROSS_PIVOT", "EXHAUSTION_SNIPER", "PIVOT_TURN"):
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
        wallet_balance_fn = getattr(self.account, "get_wallet_balance", None)
        if wallet_balance_fn is None:
            wallet_balance_fn = self.account.get_available_balance
        dynamic_trade_amount = (
            wallet_balance_fn() / max(MAX_SLOTS, 1)
            if MAX_SLOTS > 0
            else TRADE_AMOUNT_USDT
        )
        amount = dynamic_trade_amount
        leverage = self.symbol_rotation.get_dynamic_leverage(symbol, score)
        amount, projected_risk = cap_margin_to_trade_risk(
            amount, leverage, planned_price, sl,
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
            self.account.log(
                f"📝 [結構掛單] {symbol} {side} {entry_mode} {order_type} @ "
                f"{planned_price:.8g}｜硬停損 {sl:.8g}｜風險 {initial_risk:.8g}",
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
        amount_usdt = dynamic_trade_amount
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
                # detect_ma5_reversal會用最新K線與KC重新估價，型態若仍成立便再掛；
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

    async def _place_continuous_market_entry(
        self, symbol: str, side: str, df: pd.DataFrame, live_price: float,
        entry_type: str, reason: str, score: int, timeframe: str,
        wave_regime: str = "TREND",
        market_mode: str = "TREND",
    ) -> bool:
        """MA3/MA15 順勢訊號確認後，以即時市價直接開倉。"""
        from core.config import TRADE_AMOUNT_USDT, get_leverage
        from core.strategy import build_sl_tp_for_side

        atr_raw = float(df["atr"].iloc[-1]) if "atr" in df.columns else 0.0
        atr = atr_raw if pd.notna(atr_raw) and atr_raw > 0 else live_price * 0.015
        sl_dist, tp_dist = compute_sl_tp_distance(live_price, atr)
        sl, tp = build_sl_tp_for_side(live_price, side, sl_dist, tp_dist)
        opened = await self.account.open_position(
            symbol=symbol, side=side, price=live_price,
            amount_usdt=TRADE_AMOUNT_USDT, sl=sl, tp=tp,
            reason=f"{entry_type}: {reason}", atr=atr,
            leverage=get_leverage(symbol), signal_score=score,
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
                from core.strategy import detect_strong_green_candle_burst
                df_1m_signal = await self.fetch_klines(symbol, timeframe="1m", limit=30)
                if not df_1m_signal.empty and len(df_1m_signal) >= 2:
                    df_1m_signal = self.strategy.compute_indicators(df_1m_signal)
                    strong_burst = detect_strong_green_candle_burst(df_1m_signal)
                    
                    if strong_burst.get("detected") and not CONTINUOUS_PIVOT_ONLY:
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
                                "live_price": float(self.tickers.get(symbol) or df_1m_signal['close'].iloc[-1]),
                                "entry_mode": "STRONG_LONG_BURST",
                                "in_outer_rail": strong_burst.get("in_outer_rail", False),
                                "kc_upper": strong_burst.get("kc_upper"),
                                "kc_middle": strong_burst.get("kc_middle"),
                            })
                            signal_progress.append(f"{coin} 強勢多單 95分，黑圈轉向")
                            return signal_progress, detected_candidates
            except Exception as e:
                self.account.log(f"⚠️ {symbol} 強勢多單檢測異常: {e}", "WARNING")

            # ====== 第二步：如果多單在外軌，不要提早出場（延後平倉邏輯）======
            if symbol in self.account.positions:
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
                from core.indicators import classify_wave_regime, detect_ma3_ma15_cross_and_turn
                from core.strategy import build_sl_tp_for_side
                # 連續峰谷模式使用設定的同一週期已收盤K，避免不同週期混用。
                df_cr = await self.fetch_klines(symbol, timeframe=CONTINUOUS_REVERSE_TIMEFRAME, limit=100, keep_live=True)
                df_cr_live = df_cr.copy()
                if df_cr.empty or len(df_cr) < 4:
                    return signal_progress, detected_candidates
                if CONTINUOUS_PIVOT_ONLY:
                    df_cr = drop_unclosed_candle(df_cr, CONTINUOUS_REVERSE_TIMEFRAME)
                    if len(df_cr) < 15:
                        return signal_progress, detected_candidates
                # 改為直接使用包含當前未收盤 K 線的 df_cr，以達成「碰到中軌/V轉成型瞬間即刻開倉」
                # 的極速要求，不再延遲一根 K 線等待收盤。
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
                        f"BTC1h ST={self.btc_1h_st_direction})",
                        "INFO",
                    )
                if symbol in self.account.positions:
                    self.account.positions[symbol]["market_mode"] = market_mode
                    self.account.position_meta.setdefault(symbol, {})["market_mode"] = market_mode
                cr_info = detect_ma3_ma15_cross_and_turn(df_cr_signal)
                if CONTINUOUS_PIVOT_ONLY:
                    from core.strategy import detect_simple_ma5_signal
                    live_p = float(df_cr_live["close"].iloc[-1])
                    sig = detect_simple_ma5_signal(df_cr_signal, live_price=live_p)
                    if sig.get("detected"):
                        t_side = sig["side"]
                        # 同步檢查最尖端K線是否精準觸軌
                        is_valid_kc = False
                        if t_side == "LONG":
                            is_valid_kc = float(df_cr_signal.iloc[-2]['low']) <= float(df_cr_signal.iloc[-2]['kc_lower']) or float(df_cr_signal.iloc[-3]['low']) <= float(df_cr_signal.iloc[-3]['kc_lower'])
                        else:
                            is_valid_kc = float(df_cr_signal.iloc[-2]['high']) >= float(df_cr_signal.iloc[-2]['kc_upper']) or float(df_cr_signal.iloc[-3]['high']) >= float(df_cr_signal.iloc[-3]['kc_upper'])
                        
                        if is_valid_kc:
                            cr_info = {
                                "signal": t_side, 
                                "entry_type": "TROUGH_TURN" if t_side == "LONG" else "PEAK_TURN",
                                "reason": f"{CONTINUOUS_REVERSE_TIMEFRAME} {sig['reason']} (確認觸及KC邊界)",
                                "pivot_offset": -1, "pivot_confirmed": True, "pivot_score": 100,
                            }
                        else:
                            cr_info = {
                                "signal": None, "entry_type": "WAIT_KC_OUTER_RAIL",
                                "reason": f"等待{CONTINUOUS_REVERSE_TIMEFRAME} 觸碰 KC 外軌極端",
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
                    live_latest = live_df.iloc[-1]
                    live_previous = live_df.iloc[-2]
                    live_lower_turn = (
                        float(live_latest["low"]) < float(live_latest["kc_lower"])
                        and float(live_latest["close"]) > float(live_latest["open"])
                        and float(live_latest["close"]) > float(live_previous["close"])
                    )
                    live_upper_turn = (
                        float(live_latest["high"]) > float(live_latest["kc_upper"])
                        and float(live_latest["close"]) < float(live_latest["open"])
                        and float(live_latest["close"]) < float(live_previous["close"])
                    )
                    prealert_side = "LONG" if live_lower_turn else "SHORT" if live_upper_turn else None
                    held_side = self.account.positions.get(symbol, {}).get("side")
                    if prealert_side and held_side != prealert_side:
                        self.pivot_prealerts[symbol] = {
                            "action": "PREALERT_LONG" if prealert_side == "LONG" else "PREALERT_SHORT",
                            "timestamp": int(float(df_cr_live["timestamp"].iloc[-1])),
                            "updated_at": time.time(),
                        }
                    else:
                        self.pivot_prealerts.pop(symbol, None)

                    lower_outer_touch = float(live_latest["low"]) < float(live_latest["kc_lower"])
                    upper_outer_touch = float(live_latest["high"]) > float(live_latest["kc_upper"])
                    if (lower_outer_touch or upper_outer_touch) and symbol not in self.account.positions:
                        df_early_1m = await self.fetch_klines(symbol, timeframe="1m", limit=5, keep_live=True)
                        if not df_early_1m.empty and len(df_early_1m) >= 2:
                            df_early_1m = self.strategy.compute_indicators(df_early_1m)
                            early_bar = df_early_1m.iloc[-1]
                            early_green = float(early_bar["close"]) > float(early_bar["open"])
                            early_red = float(early_bar["close"]) < float(early_bar["open"])
                            live_atr = max(float(live_latest.get("atr") or 0.0), float(live_latest["close"]) * 1e-12)
                            lower_rebound_atr = (float(early_bar["close"]) - float(live_latest["low"])) / live_atr
                            upper_rebound_atr = (float(live_latest["high"]) - float(early_bar["close"])) / live_atr
                            if lower_outer_touch and early_green and lower_rebound_atr <= PIVOT_EARLY_ENTRY_MAX_REBOUND_ATR:
                                cr_info = {
                                    "signal": "LONG", "entry_type": "TROUGH_TURN",
                                    "reason": "1m first green after lower-rail touch",
                                    "pivot_offset": -1, "pivot_confirmed": True, "pivot_score": 100,
                                    "early_1m_entry": True,
                                }
                            elif upper_outer_touch and early_red and upper_rebound_atr <= PIVOT_EARLY_ENTRY_MAX_REBOUND_ATR:
                                cr_info = {
                                    "signal": "SHORT", "entry_type": "PEAK_TURN",
                                    "reason": "1m first red after upper-rail touch",
                                    "pivot_offset": -1, "pivot_confirmed": True, "pivot_score": 100,
                                    "early_1m_entry": True,
                                }
                            elif (lower_outer_touch and early_green) or (upper_outer_touch and early_red):
                                signal_progress.append(f"{coin} 1m rebound too far; skip chase")
                uses_live_pivot = False
                df_cr_entry = live_df if cr_info.get("early_1m_entry") else df_cr_signal
                cr_signal = cr_info.get("signal")
                cr_entry_type = cr_info.get("entry_type", "")
                is_peak_early = cr_info.get("is_peak_early", False)
                is_trough_early = cr_info.get("is_trough_early", False)

                if CONTINUOUS_PIVOT_ONLY and cr_entry_type in ("TROUGH_TURN", "PEAK_TURN"):
                    pivot_offset = int(cr_info.get("pivot_offset", -2) or -2)
                    pivot_high = float(df_cr_entry["high"].iloc[pivot_offset])
                    pivot_low = float(df_cr_entry["low"].iloc[pivot_offset])
                    pivot_upper = float(df_cr_entry["kc_upper"].iloc[pivot_offset])
                    pivot_lower = float(df_cr_entry["kc_lower"].iloc[pivot_offset])
                    pivot_on_outer_rail = (
                        (cr_entry_type == "PEAK_TURN" and pivot_high >= pivot_upper)
                        or (cr_entry_type == "TROUGH_TURN" and pivot_low <= pivot_lower)
                    )
                    if not pivot_on_outer_rail:
                        signal_progress.append(f"{coin} 中軌峰谷預警，等待KC外軌確認")
                        cr_signal = None
                        cr_entry_type = "WAIT_KC_OUTER_RAIL"
                    elif cr_entry_type == "TROUGH_TURN":
                        kc_width_pct = (pivot_upper - pivot_lower) / max((pivot_upper + pivot_lower) / 2.0, 1e-12)
                        if kc_width_pct < PIVOT_MIN_KC_WIDTH_PCT:
                            signal_progress.append(f"{coin} KC range {kc_width_pct:.2%} too narrow; skip entry")
                            cr_signal = None
                            cr_entry_type = "WAIT_KC_WIDTH"

                # 峰谷專用模式：所有幣一律只接受外軌谷底轉多／峰頂轉空。
                if CONTINUOUS_PIVOT_ONLY and cr_entry_type not in ("TROUGH_TURN", "PEAK_TURN"):
                    signal_progress.append(f"{coin} 等待下軌谷底轉多／上軌峰頂轉空")
                    cr_signal = None
                # 順勢模式只接受 MA3/MA15 延續訊號；峰谷與交叉訊號可作出場參考，但不可開新倉或反手。
                elif CONTINUOUS_TREND_ONLY and cr_entry_type not in ("TREND_LONG", "TREND_SHORT"):
                    signal_progress.append(f"{coin} 只做順勢：等待 MA3/MA15 同向延續")
                    cr_signal = None
                elif wave_regime == "RANGE" and cr_entry_type in ("TREND_LONG", "TREND_SHORT") and not CONTINUOUS_TREND_ONLY:
                    signal_progress.append(f"{coin} 短波動：等待谷底買多／峰頂開空")
                    cr_signal = None
                elif wave_regime == "TREND" and cr_entry_type in ("TROUGH_TURN", "PEAK_TURN") and not CONTINUOUS_PIVOT_ONLY:
                    signal_progress.append(f"{coin} 長波動：忽略逆勢峰谷，等待順勢開倉")
                    cr_signal = None
                elif market_mode == "BULL" and cr_signal != "LONG" and not CONTINUOUS_PIVOT_ONLY:
                    signal_progress.append(f"{coin} 牛市：只等順勢多單")
                    cr_signal = None
                elif market_mode == "BEAR" and cr_signal != "SHORT" and not CONTINUOUS_PIVOT_ONLY:
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
                kc_outer_reversal_blocked = False
                if has_pos:
                    from core.indicators import evaluate_kc_outer_run_lock
                    _position_meta_map = getattr(self.account, "position_meta", {})
                    _position_meta = _position_meta_map.setdefault(symbol, {})
                    _outer_lock = evaluate_kc_outer_run_lock(
                        df_cr, curr_side,
                        armed=bool(_position_meta.get("kc_outer_run_armed")),
                    )
                    _position_meta["kc_outer_run_armed"] = _outer_lock["armed"]
                    self.account.positions[symbol]["kc_outer_run_armed"] = _outer_lock["armed"]
                    kc_outer_reversal_blocked = bool(_outer_lock["blocked"])

                entry_bar_id = (
                    int(float(df_cr_entry['timestamp'].iloc[-1]))
                    if 'timestamp' in df_cr_entry.columns
                    else int(df_cr_entry.index[-1])
                )

                # 外軌峰谷採兩段式反手：峰／谷確認時已先把舊單平掉；空倉期間
                # 禁止所有一般進場（包含在上軌附近重新買多），直到反向 K 線
                # 碰到 KC 中軌。pending 內的 pivot_type 代表前面的倒 V／V 已確認。
                pending_kc_reverse = self._kc_reversal_wait.get(symbol)
                if (CONTINUOUS_TREND_ONLY or CONTINUOUS_PIVOT_ONLY) and pending_kc_reverse:
                    self._kc_reversal_wait.pop(symbol, None)
                    pending_kc_reverse = None
                if not has_pos and pending_kc_reverse:
                    from core.indicators import evaluate_kc_outer_run_lock
                    release_info = evaluate_kc_outer_run_lock(
                        df_cr,
                        pending_kc_reverse.get("from_side"),
                        armed=True,
                    )
                    middle_reached = bool(
                        pending_kc_reverse.get("middle_reached")
                        or release_info.get("released")
                    )
                    pending_kc_reverse["middle_reached"] = middle_reached
                    from_side = pending_kc_reverse.get("from_side")
                    target_side = pending_kc_reverse.get("target_side")
                    if not middle_reached:
                        wait_text = (
                            "峰頂已平多，等待紅K回到KC中軌再開空"
                            if from_side == "LONG"
                            else "谷底已平空，等待綠K回到KC中軌再開多"
                        )
                        signal_progress.append(f"{coin} {wait_text}")
                        return signal_progress, detected_candidates

                    last_close = float(df_cr_entry["close"].iloc[-1])
                    clean_sym = symbol.replace(":USDT", "") if symbol.endswith(":USDT") else symbol
                    live_price = self.tickers.get(clean_sym, self.tickers.get(symbol, last_close))
                    available = self.account.get_available_balance()
                    if TEST_BUDGET_CAP_USDT > 0:
                        available = min(available, TEST_BUDGET_CAP_USDT)
                    if daily_halt or available < MIN_TRADE_USDT:
                        self.account.log(
                            f"{symbol} 已到KC中軌，但風控或可用餘額不允許開{target_side}",
                            "WARNING",
                        )
                        return signal_progress, detected_candidates

                    entry_type = (
                        "KC_MIDDLE_PEAK_REVERSE"
                        if target_side == "SHORT"
                        else "KC_MIDDLE_TROUGH_REVERSE"
                    )
                    reason = (
                        "倒V峰頂已確認，紅K回到KC中軌，立即開空"
                        if target_side == "SHORT"
                        else "V谷底已確認，綠K回到KC中軌，立即開多"
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
                        self._continuous_last_entry_bar[symbol] = (target_side, entry_bar_id)
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
                        and "Trailing" in str(_latest_symbol_trade.get("reason") or "")
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
                    if not (CONTINUOUS_PIVOT_ONLY and cr_entry_type == "TROUGH_TURN") and not self._ma3_ma15_entry_allowed(
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

                    # 空倉的新市價單不可追在 MA3 順向延伸的尾端：這正是
                    # 12:37 追空、12:40 大綠K追多後立刻被反向掃掉的情形。
                    # 已持倉的急速反手在持倉管理迴圈處理，故不會被這裡擋住。
                    if not has_pos:
                        entry_ma3 = float(df_cr_entry['close'].rolling(3).mean().iloc[-1])
                        entry_atr = max(float(cr_info.get('atr') or 0.0), live_price * 1e-12)
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
                        if trend_reached_middle:
                            middle_direction = "紅K跌到中軌" if cr_entry_type == "TREND_LONG" else "綠K漲到中軌"
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
                        and "Trailing" in str(_latest_symbol_trade.get("reason") or "")
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
                            should_open = (not (CONTINUOUS_PIVOT_ONLY and PIVOT_LONG_ONLY) or cr_signal == "LONG")
                            if should_open:
                                self.account.log(
                                    f"✅ {symbol} confirmed {cr_entry_type}; market-enter {cr_signal}",
                                    "INFO",
                                )
                            else:
                                signal_progress.append(f"{coin} 上軌峰頂：只平多，不開空")
                        elif curr_side != cr_signal:
                            if CONTINUOUS_PIVOT_ONLY and PIVOT_LONG_ONLY:
                                await self.account.close_position(
                                    symbol=symbol, current_price=live_price,
                                    close_reason=f"外軌峰谷轉向只平倉 ({cr_entry_type})",
                                    is_manual=True,
                                )
                                return signal_progress, detected_candidates
                            if CONTINUOUS_PIVOT_ONLY:
                                closed = await self.account.close_position(
                                    symbol=symbol, current_price=live_price,
                                    close_reason=f"外軌峰谷直接反手 ({cr_entry_type})",
                                    is_manual=True,
                                )
                                should_open = bool(closed)
                            elif kc_outer_reversal_blocked:
                                self.account.log(
                                    f"{symbol} 已確認外軌 {cr_entry_type}：先平 {curr_side}，"
                                    f"不立即反手，等待反向K回到KC中軌",
                                    "WARNING",
                                )
                                closed = await self.account.close_position(
                                    symbol=symbol,
                                    current_price=live_price,
                                    close_reason=f"外軌峰谷先平倉 ({cr_entry_type})",
                                    is_manual=True,
                                )
                                if closed:
                                    self._kc_reversal_wait[symbol] = {
                                        "from_side": curr_side,
                                        "target_side": cr_signal,
                                        "pivot_type": cr_entry_type,
                                        "created_at": time.time(),
                                        "middle_reached": False,
                                    }
                                return signal_progress, detected_candidates
                            if not CONTINUOUS_PIVOT_ONLY:
                                self.account.log(
                                    f"{symbol} confirmed {cr_entry_type}: close {curr_side}, immediately market-reverse {cr_signal}",
                                    "WARNING",
                                )
                                closed = await self.account.close_position(
                                    symbol=symbol,
                                    current_price=live_price,
                                    close_reason=f"confirmed pivot reversal ({cr_entry_type})",
                                    is_manual=True,
                                )
                                should_open = bool(closed)

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
                                self._continuous_last_entry_bar[symbol] = (cr_signal, entry_bar_id)

                    # --- MA5 穿越 MA15（金叉/死叉）：反轉/補開訊號 ---
                    # 如果無持倉，直接開倉；如果有持倉且方向相反，強制平倉反轉！
                    elif cr_entry_type in ("CROSS_UP", "CROSS_DOWN"):
                        should_open = False
                        if not has_pos:
                            should_open = True
                        elif curr_side != cr_signal:
                            self.account.log(f"🚨 {symbol} 偵測到 {cr_entry_type}，強制平掉舊有 {curr_side} 單並反轉！", "WARNING")
                            closed = await self.account.close_position(
                                symbol=symbol,
                                current_price=live_price,
                                close_reason=f"死叉/金叉反向訊號 ({cr_entry_type})",
                                is_manual=True,
                            )
                            should_open = bool(closed)

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
                                amount_usdt=TRADE_AMOUNT_USDT,
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
                                    f"🚨 {symbol} 偵測到 MA3/MA15 趨勢反轉 ({curr_side} -> {cr_signal})，強制平掉舊有 {curr_side} 單！",
                                    "WARNING",
                                )
                                closed = await self.account.close_position(
                                    symbol=symbol,
                                    current_price=live_price,
                                    close_reason=f"趨勢反轉平倉 ({cr_entry_type})",
                                    is_manual=True,
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
                
            # --- 通道振盪策略 (Channel Swing) ---
            df_1m_live = self.strategy.compute_indicators(df_1m.copy())
            if len(df_1m_live) >= 20:
                live_candle = df_1m_live.iloc[-1]
                kc_upper = float(live_candle.get("kc_upper") or 0)
                kc_lower = float(live_candle.get("kc_lower") or 0)
                
                # === 持倉中：對面軌道到達 → 平倉 + 反手 (已停用，改由 _process_single_exit 峰谷判定) ===
                # 這裡的邏輯原本是一碰到軌道就立刻平倉，導致錯失了衝出軌道外的「巨大峰頂/谷底」利潤。
                # 現在全部統一交給 _process_single_exit 裡面的「外軌峰谷平倉」邏輯去處理，
                # 它會等到 MA3 在軌道外形成峰頂/谷底才真正平倉！
                # 持倉中，不再由這裡執行平倉反手
                if self.account.positions.get(symbol):
                    curr_side = self.account.positions[symbol].get("side")
                    signal_progress.append(
                        f"{symbol.replace('/USDT', '')} {curr_side} 持倉中 | 等待外軌 MA3 峰谷平倉"
                    )
                    return signal_progress, detected_candidates

                # === 空倉：碰到 KC 邊界 → 直接開倉 ===
                if not existing_pos:
                    rip_ts = self._kc_rip_after.get(symbol, 0)
                    in_rip_recovery = rip_ts > 0
                    
                    if in_rip_recovery:
                        # --- KC 撕裂後：等待穩定再順勢入場 ---
                        # 穩定條件：價格回到 KC 通道內，且 MA3 均線轉向與趨勢一致
                        price_inside_kc = kc_lower < live_price < kc_upper
                        ma3_live = (df_1m_live["ma3"] if "ma3" in df_1m_live.columns else df_1m_live["close"].rolling(3).mean()).dropna()
                        
                        stable_direction = None
                        if price_inside_kc and len(ma3_live) >= 3:
                            ma3_prev2 = float(ma3_live.iloc[-3])
                            ma3_prev1 = float(ma3_live.iloc[-2])
                            ma3_curr_v = float(ma3_live.iloc[-1])
                            # 向上穩定：MA3 明顯向上走，開多
                            if ma3_curr_v > ma3_prev1 > ma3_prev2:
                                stable_direction = "LONG"
                            # 向下穩定：MA3 明顯向下走，開空
                            elif ma3_curr_v < ma3_prev1 < ma3_prev2:
                                stable_direction = "SHORT"
                        
                        if stable_direction:
                            # 行情穩定，清除冷卻記錄，順勢開倉
                            self._kc_rip_after.pop(symbol, None)
                            self.account.log(
                                f"✅ [KC 撕裂復原] {symbol} 行情穩定，MA3 轉{stable_direction}，重新入場",
                                "SUCCESS"
                            )
                            sig = {
                                "detected": True, "side": stable_direction, "score": 100,
                                "price": live_price,
                                "atr": float(live_candle.get("atr") or live_price * 0.015),
                                "entry_mode": "CHANNEL_SWING",
                                "profit_profile": "TREND_EXTENSION",
                                "action": "ENTER_MARKET", "target_price": None,
                                "signal_candle_low": float(live_candle["low"]),
                                "signal_candle_high": float(live_candle["high"]),
                                "symbol": symbol, "live_price": live_price,
                                "reason": f"Channel Swing 撕裂復原 → 行情穩定後順勢開{stable_direction}｜MA3 確認",
                            }
                            signal_progress["signals"].append(sig)
                            return signal_progress, detected_candidates
                        else:
                            elapsed = int(time.time() - rip_ts)
                            signal_progress.append(
                                f"{symbol.replace('/USDT', '')} KC撕裂後等待穩定 | 已等{elapsed}秒 | 價格{'在通道內' if price_inside_kc else '在通道外'}"
                            )
                            return signal_progress, detected_candidates
                    
                    # --- 正常 Channel Swing 空倉邏輯 ---
                    swing_direction = None
                    if live_price <= kc_lower:
                        swing_direction = "LONG"
                    elif live_price >= kc_upper:
                        swing_direction = "SHORT"
                    
                    if swing_direction:
                        # 猴市「挑利潤大的做」過濾：根據 1h SuperTrend 大方向
                        st_dir_1h = int(getattr(self, "st_direction_1h_cache", {}).get(symbol, 0))
                        if st_dir_1h != 0:
                            is_aligned = (swing_direction == "LONG" and st_dir_1h == 1) or (swing_direction == "SHORT" and st_dir_1h == -1)
                            if not is_aligned:
                                signal_progress.append(
                                    f"{symbol.replace('/USDT', '')} 猴市過濾：大級別偏{'多' if st_dir_1h == 1 else '空'}，放棄逆向 {swing_direction}"
                                )
                                swing_direction = None

                    if swing_direction:
                        sig = {
                            "detected": True, "side": swing_direction, "score": 100,
                            "price": live_price,
                            "atr": float(live_candle.get("atr") or live_price * 0.015),
                            "entry_mode": "CHANNEL_SWING",
                            "profit_profile": "TREND_EXTENSION",
                            "action": "ENTER_MARKET", "target_price": None,
                            "signal_candle_low": float(live_candle["low"]),
                            "signal_candle_high": float(live_candle["high"]),
                            "symbol": symbol, "live_price": live_price,
                            "reason": f"Channel Swing → {'KC 下軌開多' if swing_direction == 'LONG' else 'KC 上軌開空'}｜kc={kc_lower:.6g}~{kc_upper:.6g}",
                        }
                        signal_progress["signals"].append(sig)
                        return signal_progress, detected_candidates
                    else:
                        signal_progress.append(
                            f"{symbol.replace('/USDT', '')} 通道中央等待 | KC {kc_lower:.6g}~{kc_upper:.6g}, 現價={live_price:.6g}"
                        )
                        return signal_progress, detected_candidates
            # -----------------------------------

            # 只讀已收盤 1m K，避免未收線 RSI、量能與 MA3 重繪。
            df_1m = drop_unclosed_candle(df_1m, "1m")
            if len(df_1m) < 20:
                return signal_progress, detected_candidates
            df_1m = self.strategy.compute_indicators(df_1m)

            from core.indicators import detect_ma3_ma15_cross_and_turn
            pivot = detect_ma3_ma15_cross_and_turn(df_1m, allow_live_pivot=False)
            entry_type = pivot.get("entry_type")
            if entry_type in ("TROUGH_TURN", "PEAK_TURN"):
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
                entry_scan_allowed = not daily_halt and available_balance >= MIN_TRADE_USDT
                manage_continuous_position = ENABLE_CONTINUOUS_REVERSE_MODE and bool(self.account.positions)
                if entry_scan_allowed or manage_continuous_position:
                    signal_progress = []
                    detected_candidates = []

                    now_time = time.time()
                    
                    # 抓取 BTC 1m 判斷即時轉彎方向
                    btc_1m_turn = None
                    try:
                        btc_df_1m = await self.fetch_klines("BTC/USDT", timeframe="1m", limit=30)
                        if not btc_df_1m.empty and len(btc_df_1m) >= 8:
                            btc_live = float(self.tickers.get("BTC/USDT") or btc_df_1m['close'].iloc[-1])
                            btc_target = float(btc_df_1m['close'].iloc[-8])
                            btc_is_green = float(btc_df_1m['close'].iloc[-1]) > float(btc_df_1m['open'].iloc[-1])
                            btc_is_red = float(btc_df_1m['close'].iloc[-1]) < float(btc_df_1m['open'].iloc[-1])
                            
                            if btc_live > btc_target and btc_is_green:
                                btc_1m_turn = "LONG"
                            elif btc_live < btc_target and btc_is_red:
                                btc_1m_turn = "SHORT"
                    except Exception as e:
                        self.account.log(f"⚠️ 無法取得 BTC 1m 轉彎資料: {e}", "WARNING")

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
                    if detected_candidates:
                        # Sort by score descending
                        detected_candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
                        from core.config import MAX_SLOTS
                        for sig in detected_candidates:
                            symbol = sig["symbol"]
                            coin = symbol.replace("/USDT", "")
                            direction_text = "多單" if sig["side"] == "LONG" else "空單"
                            
                            if MAX_SLOTS > 0 and len(self.account.positions) >= MAX_SLOTS:
                                signal_progress.append(f"{coin} {direction_text} 資格未通過,槽位已滿({MAX_SLOTS})")
                                continue
                                
                            await self._place_structured_entry(
                                symbol,
                                sig,
                                sig["live_price"]
                            )

                    if (
                        CONTINUOUS_PIVOT_ONLY
                        and not self.account.positions
                        and not detected_candidates
                        and now_time - self._last_empty_pivot_rescan_at >= 60.0
                    ):
                        self._last_empty_pivot_rescan_at = now_time
                        self.symbol_rotation.last_rotation_at = 0.0
                        self.account.log(
                            "🔄 [峰谷無訊號] 目前牌面無可開倉谷底／峰頂轉向，要求重新掃描市場",
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
