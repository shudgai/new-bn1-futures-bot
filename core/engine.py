from core.services.logger_service import format_signal_progress, format_ma5_wait_detail, ma5_exit_ready, bottom_entry_grace, trend_follow_breach, history_adjusted_score, entry_filter_outcome, shadow_ready
from core.services.scan_service import entry_scan_symbol_snapshot, candidate_board_refresh_needed
from core.services.rank_service import (
    directional_trend_quality, channel_volume_ratio, channel_held_volume_is_declining,
    channel_held_momentum_is_declining, channel_long_profit_room, channel_profit_room,
    candidate_profit_potential, touch_entry_math_favorable, channel_entry_requires_profit_room,
    channel_recent_candles_whipsawing, channel_candidate_energy, channel_confirmed_candidate_energy,
    channel_price_is_outside_for_side, select_strongest_same_side_candidates,
    channel_same_side_committed, channel_takeover_net_pnl
)
from core.services.entry_service import (
    channel_candidate_bar_id, channel_outer_directional_entry_allowed,
    channel_closed_body_break_entry_allowed, channel_closed_body_break_has_outer_ma3_reversal,
    channel_closed_body_break_entry_action, channel_outer_continuation_entry_action,
    channel_outer_uptrend_entry_action, channel_outer_downtrend_entry_action,
    channel_outer_trend_entry_action, channel_strong_first_outer_touch_action,
    channel_immediate_outer_break_action
)
from core.services.swing_service import (
    channel_macro_market_mode, channel_mature_outer_trend_is_weak, channel_terminal_market,
    record_channel_chop_event, record_channel_signal_event, channel_chop_state,
    channel_chop_breakout_action, channel_entry_reuses_exit_bar, channel_peak_exit_reentry_blocked,
    channel_peak_reversal_action, channel_entry_min_profit_ok, channel_peak_exit_entry_gate,
    channel_upper_red_short_reversal_allowed, channel_is_upper_red_peak_short,
    channel_exit_requests_rotation, channel_slope_entry_gate, channel_macro_continuation_entry_gate,
    channel_closed_body_volume_gate, channel_near_chop_entry_gate, channel_chop_gate,
    channel_ma3_outside, channel_outer_half_space_hold, check_parabolic_reversal_exit,
    channel_impulse_turn_allowed, channel_ma15_convergence_is_gradual, channel_outer_gap_expanding,
    channel_trend_exit_reason, channel_position_path, channel_impulse_first_turn,
    channel_all_same_color_inside, channel_closed_waves_falling, channel_swing_action,
    channel_ck_exit_reason, channel_ck_exit_with_tolerance, two_bar_structure_failure_exit, adverse_kc_outer_breached,
    confirmed_outer_reversal, range_swing_reverse_side, pivot_pullback_ready,
    detect_strict_pivot_prealert
)
from core.services.pivot_service import (
    validate_strict_pivot_entry, resolve_entry_atr, pivot_confirmation_body_atr,
    strong_burst_live_entry_is_valid, resolve_trailing_atr, opposite_closed_candle_exit,
    outer_run_second_candle_status
)
from core.services.pullback_service import (
    quality_bonus, format_pullback_order_log, pullback_reversal_confirmed, classify_pullback_drop
)
from core.services.pulse_service import (
    detect_btc_1m_pulse, begin_btc_lead_shadow, record_btc_lead_shadow_candidate, btc_pulse_blocks_entry
)
from core.services.surveillance_service import (
    sample_reference_price, market_crash_entries_paused, btc_flash_crash_close_symbols, continuous_entry_price_is_safe, MarketSurveillanceService
)
from core.guards.risk_guard import same_side_entry_allowed, RiskGuardManager
from core.guards.abnormal_guard import channel_adverse_exit_reason, channel_live_ma3_turn_exit, AbnormalMarketGuard
from core.routes.legacy_routes import place_ma5_reversal_entry_legacy, validate_pending_limit_orders_legacy
from core.services.exits.fading_exit_service import fading_ma3_turn, next_breakout_ready, STATE_KEY as FADING_STATE_KEY, EXIT_REASON as FADING_EXIT_REASON, IMMEDIATE_EXIT_REASON
from core.services.exits.hard_stop_service import enforce_hard_stop
from core.services.strategies.live_pivot_strategy import LivePivot
from core.services.strategies.direct_reverse_strategy import authorized as reverse_authorized, quote_ready as reverse_quote_ready
import asyncio
import copy
from core.services.strategies.outer_strategy import (
    LIVE_BODY_BREAKOUT_CODES, ENTRY_TREND_CODES, entry_trend_direction, OUTER_CODES, TREND_CODES,
    outside_entry, continuation_entry, outside_reentry, abnormal_pullback_ready,
    three_closed_short_breakout_ready, aligned_entry, aligned_entry_ready,
    live_adverse_entry_safe, ck_direction, live_ma3_direction_ready, LIVE_OUTER_CODES
)
from core.services.strategies.pivot_strategy import PIVOT_CODES, pivot_entry
from core.services.exits.profit_protection_service import protection, reentry_gate, long_entry_ready, directional_entry_ready
from core.guards.risk_guard import same_side_entry_allowed, candidate_bar_invalid_locked
from core.guards.abnormal_guard import channel_adverse_exit_reason, channel_live_ma3_turn_exit, opposite_entry_releases
import math
import re
import time
import ccxt.async_support as ccxt
import ccxt.pro as ccxtpro
import pandas as pd
import weakref
from collections import deque
from typing import Dict, List
from core.config import (
    DEFAULT_SYMBOLS, MAX_SLOTS, MAX_SAME_SIDE_POSITIONS, TRADE_AMOUNT_USDT, MAX_SLOT_TRADE_USDT, get_effective_slot_count, TREND_FILTER_EMA_PERIOD,
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
    TAKER_FEE_RATE, SLIPPAGE_PCT, NET_PROFIT_GUARANTEE_BUFFER, MAX_TRADE_RISK_USDT, PAPER_TRADING, SOFT_WARNING_PERSIST_SEC, ENABLE_SOFT_WARNING_TIGHTEN,
    ENABLE_PROFIT_LOCK_USDT,
    CONTRARIAN_POSITION_SIZE_MULTIPLIER, MAINSTREAM_SYMBOLS, MA5_EARLY_CONFIRM_SCANS,
    MA5_REVERSAL_MIN_ATR_MULT, MA5_FAST_MIN_ATR_MULT, MA5_FAST_MAX_ATR_MULT,
    MA5_FAST_MIN_VOLUME_RATIO,
    RAPID_PIVOT_IMMEDIATE_REVERSE_ENABLED, RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR,
    CHANNEL_WATERFALL_BODY_ATR, KLINE_FETCH_ATTEMPTS, KLINE_FETCH_TIMEOUT_SEC, PROFIT_REENTRY_TICKET_TTL_SEC,
    API_WEIGHT_LIMIT_PER_MIN, API_WEIGHT_WARN_PCT,
    KLINE_FETCH_RETRY_PAUSE_SEC, SCAN_1M_KLINE_LIMIT,
    CONTINUOUS_TREND_ONLY, CONTINUOUS_PIVOT_ONLY, DISABLE_CONTINUOUS_TREND_ENTRIES, PIVOT_LONG_ONLY, PIVOT_EARLY_ENTRY_MAX_REBOUND_ATR, PIVOT_MIN_KC_WIDTH_PCT, MA3_MARKET_ENTRY_MAX_DISTANCE_ATR,
    PIVOT_STRONG_BODY_ATR_MULT,
    TREND_ENTRY_MIN_KC_MIDDLE_DISTANCE_ATR, CONTINUOUS_ENTRY_OUTER_ZONE_RATIO, CONTINUOUS_OUTER_RAIL_EXIT_ONLY,
    ABNORMAL_MARKET_GUARD_ENABLED, ABNORMAL_MARKET_MAX_CANDLE_RANGE_ATR,
    ABNORMAL_MARKET_MAX_CANDLE_RANGE_PCT, ABNORMAL_MARKET_ADVERSE_MOVE_PCT,
    CHANNEL_SWING_MIN_OUTER_DEPTH_RATIO,
    CHANNEL_SWING_TURN_LOOKBACK_BARS,
    BTC_1M_PULSE_FILTER_ENABLED, BTC_1M_PULSE_LOOKBACK_BARS,
    BTC_1M_PULSE_MIN_ATR, BTC_FLASH_CRASH_WINDOW_SEC, BTC_FLASH_CRASH_DROP_PCT,
    BTC_FLASH_CRASH_PUMP_PCT, MARKET_CRASH_ENTRY_COOLDOWN_SEC, RAPID_DROP_COOLDOWN_SEC,
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
    MIN_ENTRY_PROFIT_ROOM_PCT,
    KELTNER_MIN_VOLUME_RATIO,
    SYMBOL_MIN_QUOTE_VOLUME, SYMBOL_MAX_24H_CHANGE_PCT, SYMBOL_MIN_LISTING_DAYS,
    FULL_MARKET_SURVEILLANCE_ENABLED, FULL_MARKET_SURVEILLANCE_SIDE_COUNT,
    FULL_MARKET_SURVEILLANCE_SHORT_WINDOW_SEC,
    FULL_MARKET_SURVEILLANCE_LONG_WINDOW_SEC,
    FULL_MARKET_SURVEILLANCE_MIN_MOVE_PCT,
    FULL_MARKET_SURVEILLANCE_STEADY_SIDE_COUNT,
    FULL_MARKET_SURVEILLANCE_STEADY_WINDOW_SEC,
    FULL_MARKET_SURVEILLANCE_STEADY_MIN_MOVE_PCT,
    FULL_MARKET_SURVEILLANCE_STEADY_MIN_EFFICIENCY,
    FULL_MARKET_SURVEILLANCE_STEADY_RETENTION_SEC,
)
from core.indicators import strict_pivot_type
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
        self.surveillance_service = MarketSurveillanceService()
        self.risk_guard = RiskGuardManager()
        self.abnormal_guard = AbnormalMarketGuard()
        self.is_running = False
        self.task: asyncio.Task = None
        self.rotation_task: asyncio.Task = None
        self.analysis_task: asyncio.Task = None
        self.trend_cache_task: asyncio.Task = None
        self.analysis_event = asyncio.Event()
        self.rotation_event = asyncio.Event()
        self._post_close_rotation_generation = 0
        self._entry_waiting_for_post_close_rotation = False
        self.account.on_trade_closed = self._on_trade_closed
        self.tickers: Dict[str, float] = {}
        self.ticker_volumes: Dict[str, float] = {}  # 24小時成交量 (USDT)
        # Binance 全合約 miniTicker 每秒雷達。UI 牌面只負責顯示，不再限制
        # 真正被監看的市場；只有多空各自排名最前的少量標的進完整 K 線檢查。
        self.market_surveillance_contracts = None
        self._market_price_samples: Dict[str, deque] = {}
        self._market_ticker_snapshots: Dict[str, dict] = {}
        self.market_prebreakout_symbols: List[str] = []
        self.market_prebreakout_directions: Dict[str, str] = {}
        self.market_prebreakout_profiles: Dict[str, str] = {}
        # 穩定趨勢短名單短暫保留，避免一個無方向 tick 就讓 ADA 類候選消失。
        self._market_steady_candidates: Dict[str, dict] = {}
        # 各幣種的即時爆發力分數（abs momentum score），供候選排序使用。
        self._market_surveillance_scores: Dict[str, float] = {}
        self.market_surveillance_updated_at: float = 0.0
        # 非紙上模式可下單合約集合；None代表紙上模式不需執行市場交集。
        self.execution_symbols = None
        self.last_ticker_success_ts: float = time.time()
        self._last_stale_ticker_log: float = 0.0
        # BTC 插針偵測：保留最近幾秒的 BTC 即時報價樣本（timestamp, price）。
        # 當窗口內跌幅 >= BTC_FLASH_CRASH_DROP_PCT 時觸發緊急平多。
        self._btc_price_samples: deque = deque(maxlen=200)
        self._btc_flash_crash_last_triggered_at: float = 0.0
        self._market_crash_entry_cooldown_until: float = 0.0
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
        # Channel Swing 下單前重驗失敗後，同一幣種／方向／已收盤候選 K
        # 在本進程內永久失效；只有新的候選 K ID 可再次嘗試。
        self._channel_invalid_entry_candidates: set[tuple[str, str, object]] = set()
        # Channel Swing 平倉後，同一根 live K 不得再次用任何入口重開。
        # 下一根已收盤 K 出現後自動解鎖；即時同 K 反手規格不受影響。
        self._channel_swing_last_exit_bar: Dict[str, object] = {}
        self._channel_swing_last_exit_at: Dict[str, float] = {}
        self._channel_swing_last_exit_side: Dict[str, str] = {}
        # Emergency waterfall exits wait for the next confirmed KC outer
        # breakout before allowing a directional re-entry.
        self._channel_emergency_reentry_wait: Dict[str, bool] = {}
        self._channel_outer_reentry_after_exit: Dict[str, str] = {}
        # 三點峰谷平倉後，記錄幣種、被平倉方向（同向重開倉在通道外應被封鎖）
        # 以及平倉時的 closed_bar_id，用於後續重開倉冷卻判斷。
        # 格式：{symbol: {"side": "LONG"|"SHORT", "bar_id": ..., "bar_count": int}}
        self._channel_swing_peak_exit_info: Dict[str, dict] = {}
        # 盤整鎖：均線與 KC 中軌反覆交叉時，外軌 V 只可作為持倉離場
        # 確認，不得開新倉或平倉後立即反手。需兩根已收盤 K 明確同向才解鎖。
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
        # 突破初次出現但利潤空間不足時保留候選，後續達標再開倉。
        self._channel_profit_wait_candidates: Dict[str, str] = {}
        # KC 內已形成短線趨勢的介面幣保留在牌面，直到趨勢失效或成交。
        self._channel_inner_trend_hold: Dict[str, str] = {}
        # ADX + MA3/MA15 距離的雙門檻狀態；預設 RANGE，需連續 3 根確認才進 TREND。
        self._continuous_wave_regime: Dict[str, str] = {}
        # 在短週期 TREND 之上，以個幣自己的 1h 趨勢確認牛／熊市；
        # RANGE 保留給猴市的峰谷交易，BULL/BEAR 則只做個幣同向順勢單。
        self._continuous_market_mode: Dict[str, str] = {}
        # RANGE -> BULL/BEAR 的首次轉換時間，供短窗即時外軌突破使用。
        self._market_mode_transition_at: Dict[str, float] = {}
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

    _format_signal_progress = staticmethod(format_signal_progress)
    _format_ma5_wait_detail = staticmethod(format_ma5_wait_detail)
    _ma5_exit_ready = staticmethod(ma5_exit_ready)
    _bottom_entry_grace = staticmethod(bottom_entry_grace)
    _trend_follow_breach = staticmethod(trend_follow_breach)
    _history_adjusted_score = staticmethod(history_adjusted_score)
    _entry_filter_outcome = staticmethod(entry_filter_outcome)
    _shadow_ready = staticmethod(shadow_ready)

    def _symbol_recent_performance(self, symbol: str, side: str) -> dict:
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

    def _record_entry_filter(self, symbol: str, signal: dict, direction: str, outcome: str = None) -> None:
        pass

    def _record_shadow_parameter_comparison(self, symbol: str, df: pd.DataFrame, baseline: dict, direction: str) -> None:
        pass

    def _log_signal_progress(self, entries: List[str], now_time: float, symbols_snapshot: List[str]) -> None:
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
            markets = await self.exchange.load_markets()
        except Exception as exc:
            self.account.log(f"⚠️ [幣種名單核對] 無法載入市場資料，略過本次核對：{exc}", "WARNING")
            return
        now_ms = time.time() * 1000
        min_listing_ms = SYMBOL_MIN_LISTING_DAYS * 24 * 60 * 60 * 1000
        self.market_surveillance_contracts = {
            market["symbol"].replace(":USDT", "")
            for market in markets.values()
            if market.get("active")
            and market.get("swap")
            and market.get("quote") == "USDT"
            and market.get("info", {}).get("contractType") == "PERPETUAL"
            and market.get("info", {}).get("underlyingType") == "COIN"
            and "monitoring" not in market.get("info", {}).get("tags", [])
            and (
                not (market.get("info", {}).get("onboardDate") or market.get("info", {}).get("deliveryDate"))
                or now_ms - int(market.get("info", {}).get("onboardDate") or market.get("info", {}).get("deliveryDate"))
                >= min_listing_ms
            )
        }
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
        self._announce_active_rules()
        # 歷史分析是第三條完全獨立的工作，不等待主交易或幣種輪替。
        self.analysis_task = asyncio.create_task(self._analysis_loop())
        # 持倉平倉參考指標同樣獨立成背景任務，抓K線失敗/變慢不影響主迴圈。
        self.trigger_task = asyncio.create_task(self._position_trigger_loop())
        self.fixed_stop_task = asyncio.create_task(self._fixed_stop_loss_loop())
        # KC失敗、ATR追蹤、RR分批與1h翻向出場背景任務
        self.trend_follow_task = asyncio.create_task(self._run_structured_exits())
        # 舊移動停損任務保留但預設停用，避免與結構ATR追蹤衝突
        self.trailing_sl_task = asyncio.create_task(self._run_trailing_sl_loop())
        # 行情任務獨立於交易開關；停止交易後仍供網頁與持倉估值使用。
        self.start_market_data()
        # 啟動時檢查既有歷史；摘要未變時會由 digest 快取直接略過。
        self.request_trade_analysis()

    def _announce_active_rules(self) -> None:
        """Log the single rule set this process actually applies (read-only)."""
        from core.services.rule_registry import rule_banner, stale_environment_keys
        for line in rule_banner():
            self.account.log(line, "INFO")
        stale = stale_environment_keys()
        if stale:
            shown = "、".join(stale[:12])
            more = f"（另有 {len(stale) - 12} 項）" if len(stale) > 12 else ""
            self.account.log(f"⚠️ [設定檢查] .env 中目前不影響程式的項目：{shown}{more}", "WARNING")

    def start_market_data(self) -> None:
        """Keep one ticker stream alive independently from trading tasks."""
        if self.ticker_task is None or self.ticker_task.done():
            self.ticker_task = asyncio.create_task(self._ticker_loop())

    async def stop(self, close_exchanges: bool = False):
        was_running = self.is_running
        self.is_running = False
        task_names = [
            "task", "rotation_task", "analysis_task", "trend_cache_task",
            "trigger_task", "fixed_stop_task", "trend_follow_task",
            "trailing_sl_task",
        ]
        if close_exchanges:
            task_names.append("ticker_task")
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
        """分析請求只設旗標，絕不阻塞交易與風控路徑。"""
        self.analysis_event.set()

    def _on_trade_closed(self) -> None:
        """平倉後先鎖住新倉，觸發一次全合約市場的新鮮排名。"""
        self.request_trade_analysis()
        if not SYMBOL_ROTATION_ENABLED:
            return
        self._post_close_rotation_generation += 1
        self._entry_waiting_for_post_close_rotation = True
        closed_symbol = next((
            str(trade.get("symbol") or "")
            for trade in getattr(self.account, "trades", [])
            if str(trade.get("action") or "").startswith("CLOSE")
        ), "")
        if closed_symbol:
            self.symbol_rotation.request_replacement(closed_symbol)
        else:
            self.symbol_rotation.last_rotation_at = 0.0
        self.rotation_event.set()
        self.account.log(
            "🔎 [平倉後全市場掃描] 暫停新倉，重新掃描 Binance USDT 永續合約後再選擇",
            "INFO",
        )

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
                    set_setup_protection = getattr(
                        self.symbol_rotation, "set_setup_protected_symbols", None,
                    )
                    if callable(set_setup_protection):
                        set_setup_protection(
                            set(self._channel_outer_trend_wait)
                            | set(self._channel_inner_trend_hold)
                        )
                    rotation_generation = self._post_close_rotation_generation
                    force_fresh = self._entry_waiting_for_post_close_rotation
                    changes = await self.symbol_rotation.rotate(
                        self.exchange, self.execution_symbols,
                        force_fresh=force_fresh,
                    )
                    if (
                        force_fresh
                        and rotation_generation == self._post_close_rotation_generation
                    ):
                        self._entry_waiting_for_post_close_rotation = False
                        self.account.log(
                            "✅ [平倉後全市場掃描] 新鮮排名完成，恢復新倉判斷",
                            "INFO",
                        )
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
                    self._take_over_manual_position(symbol, position)
                    entry_mode = str(position.get("entry_mode") or "")

                    if entry_price > 0 and live_price > 0:
                        if side == "LONG":
                            pnl_pct = (live_price - entry_price) / entry_price
                        else:
                            pnl_pct = (entry_price - live_price) / entry_price

                        import core.config as config

                        # Channel Swing ignores ordinary MA3 exits, but retains a
                        # last-resort KC breach stop after the configured margin-risk limit.
                        if entry_mode.upper() == "CHANNEL_SWING":
                            highest_pnl = float(position.get("peak_pnl_pct") or pnl_pct)
                            if pnl_pct > highest_pnl:
                                position["peak_pnl_pct"] = pnl_pct
                            trigger = self.position_triggers.get(symbol, {})
                            kc_upper = float(trigger.get("kc_upper") or 0)
                            kc_lower = float(trigger.get("kc_lower") or 0)
                            leverage = max(float(position.get("leverage") or config.LEVERAGE), 1.0)
                            max_loss_pct = max(
                                float(config.MIN_SL_DISTANCE_PCT),
                                float(config.MAX_POSITION_MARGIN_LOSS_RATIO) / leverage,
                            )
                            if (
                                pnl_pct <= -max_loss_pct
                                and kc_upper > 0
                                and kc_lower > 0
                                and self._adverse_kc_outer_breached(
                                    side, live_price, kc_upper, kc_lower,
                                )
                            ):
                                self.account.log(
                                    f"🆘 [Channel Swing 緊急停損] {symbol} {side} "
                                    f"逆向破 KC 外軌且虧損達 {max_loss_pct:.2%}，停止死抱",
                                    "WARNING",
                                )
                                await self.account.close_position(
                                    symbol, live_price,
                                    f"Channel Swing KC 破軌緊急停損 ({max_loss_pct:.2%})",
                                )
                            continue
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
                        pos_entry_mode in ("MA3_MA15_MARKET", "STRONG_LONG_BURST", "CHANNEL_SWING")
                        or any(k in pos_reason for k in (
                            "TROUGH_TURN", "PEAK_TURN", "RANGE_SWING_REVERSE",
                            "KC_MIDDLE_PEAK_REVERSE", "KC_MIDDLE_TROUGH_REVERSE",
                            "CROSS_UP", "CROSS_DOWN", "TREND_LONG", "TREND_SHORT",
                            # Channel Swing entry reasons
                            "KC_UPPER_BREAKOUT", "KC_LOWER_BREAKOUT",
                            "KC_UPPER_GREEN_REVERSE_LONG", "KC_LOWER_RED_REVERSE_SHORT",
                            "KC_UP_TREND_PULLBACK_TROUGH", "KC_DOWN_TREND_PULLBACK_PEAK",
                            "KC_UP_TREND_UPPER_BREAKOUT", "KC_DOWN_TREND_LOWER_BREAKOUT",
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
        """Adopt legacy holdings; technical exits belong to the Channel Swing scan."""
        self._take_over_manual_position(symbol, position)

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




    def api_weight_usage(self) -> dict:
        """Binance 回報的每分鐘請求權重用量（取公開與下單連線中較新的一筆）。

        標頭由 ccxt 存在 last_response_headers，例如 X-MBX-USED-WEIGHT-1M。
        純讀取、不發任何請求，供儀表板顯示與超量警示。
        """
        used = 0
        for ex in (getattr(self, "exchange", None), getattr(self, "execution_exchange", None)):
            headers = getattr(ex, "last_response_headers", None) or {}
            try:
                items = headers.items()
            except AttributeError:
                continue
            for key, value in items:
                if str(key).lower() in ("x-mbx-used-weight-1m", "x-mbx-used-weight"):
                    try:
                        used = max(used, int(float(value)))
                    except (TypeError, ValueError):
                        pass
        limit = API_WEIGHT_LIMIT_PER_MIN
        percent = (used / limit * 100.0) if limit else 0.0
        return {
            "used_weight_1m": used,
            "limit_per_min": limit,
            "percent": round(percent, 1),
            "warn_percent": API_WEIGHT_WARN_PCT,
            "is_warning": percent >= API_WEIGHT_WARN_PCT,
        }

    async def fetch_klines(self, symbol: str, timeframe: str = "3m", limit: int = 100, keep_live: bool = False) -> pd.DataFrame:
        """Fetch klines from Binance with retries; empty payloads count as failures."""
        last_error = None
        for attempt in range(KLINE_FETCH_ATTEMPTS):
            try:
                ohlcv = await asyncio.wait_for(
                    self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit),
                    timeout=KLINE_FETCH_TIMEOUT_SEC,
                )
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                if df.empty:
                    raise ValueError("empty kline payload")
                # 丟棄還沒收盤的最後一根 K 棒，只在這個共用入口做一次，
                # evaluate_signal/confirm_pullback_entry 等下游邏輯用 df.iloc[-1]
                # 時就天然拿到「最後一根已收盤」的資料，不用逐處修改。
                if keep_live:
                    return df
                return drop_unclosed_candle(df, timeframe)
            except Exception as e:
                last_error = e
                if attempt + 1 < KLINE_FETCH_ATTEMPTS:
                    await asyncio.sleep(KLINE_FETCH_RETRY_PAUSE_SEC)
        print(f"fetch_klines ERROR after {KLINE_FETCH_ATTEMPTS} attempts: {last_error}")
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
                    self._observe_channel_entry_quote(clean_sym, price, t.get("timestamp"))
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

    async def _try_live_pivot_entry(self, symbol, frame, price, daily_halt=False):
        """Use an observed live turn, retaining the shared structured order gates."""
        side = aligned_entry(frame, price).get('side')
        pivot_ready = self._live_pivot_ready(symbol, frame, price, side)
        outer_ready = aligned_entry_ready(frame, price, side)
        if not pivot_ready and not outer_ready:
            return False
        quoted = getattr(self, '_channel_entry_quote_times', {}).get(symbol, float('nan'))
        if (not math.isfinite(quoted) or not 0 <= time.time() - quoted <= 5
                or float(frame.iloc[-1]['timestamp']) != math.floor(time.time() / 60) * 60000):
            return False
        if daily_halt or self._ck_reverse_new_leg_halted():
            return False
        rotation = getattr(self, 'symbol_rotation', None)
        if SYMBOL_ROTATION_ENABLED and (getattr(rotation, 'last_rotation_at', 0) <= 0
                or getattr(self, '_entry_waiting_for_post_close_rotation', False)):
            return False
        self._release_resolved_abnormal_exit(symbol, frame, price)
        if symbol in getattr(self.account, 'channel_profit_reentries', {}):
            await self._try_profit_reentry(symbol, frame, price, daily_halt)
            return symbol in self.account.positions
        if not pivot_ready:
            return await self._execute_confirmed_channel_break(symbol, frame, price, side, daily_halt)
        signal = dict(side=side, score=100, entry_mode='CHANNEL_SWING',
                      action='ENTER_MARKET', live_pivot=True,
                      reason='Channel Swing KC_LIVE_PIVOT_' + side,
                      signal_code='KC_LIVE_PIVOT_' + side,
                      candidate_bar_id='live:' + str(float(frame.iloc[-1]['timestamp'])),
                      profit_profile='TREND_EXTENSION', atr=float(frame.iloc[-2]['atr']))
        return await self._place_structured_entry(symbol, signal, price)

    async def _channel_quote_pivot_entry(self, symbol, price):
        if not getattr(self, 'is_running', False) or symbol not in DEFAULT_SYMBOLS:
            return
        locks = getattr(self, '_channel_symbol_locks', None)
        if locks is None:
            locks = self._channel_symbol_locks = {}
        async with locks.setdefault(symbol, asyncio.Lock()):
            if symbol in self.account.positions:
                return
            frame = getattr(self, '_channel_exit_frames', {}).get(symbol)
            if frame is not None:
                await self._try_live_pivot_entry(symbol, frame, price)

    async def _channel_quote_exit(self, symbol, price, quote_ms=None):
        """Evaluate held exits on a received quote without waiting for the scan."""
        if not getattr(self, "is_running", False):
            return
        position = self.account.positions.get(symbol)
        if not position:
            return
        identity = (position.get("side"), position.get("open_timestamp"))
        try:
            quoted_at = float(quote_ms) / 1000 if quote_ms is not None else time.time()
            if not math.isfinite(quoted_at) or not 0 <= time.time() - quoted_at <= 5:
                return
            locks = getattr(self, "_channel_symbol_locks", None)
            if locks is None:
                locks = self._channel_symbol_locks = {}
            async with locks.setdefault(symbol, asyncio.Lock()):
                current = self.account.positions.get(symbol)
                if not current or (current.get("side"), current.get("open_timestamp")) != identity:
                    return
                frame = getattr(self, "_channel_exit_frames", {}).get(symbol)
                bar = math.floor(quoted_at / 60) * 60000
                if frame is None or frame.empty or float(frame.iloc[-1]["timestamp"]) != bar:
                    frame = await self.fetch_klines(symbol, timeframe="1m", limit=200, keep_live=True)
                    if frame is None or frame.empty or float(frame.iloc[-1]["timestamp"]) != bar:
                        return
                    frame = self.strategy.compute_indicators(frame.copy())
                current = self.account.positions.get(symbol)
                if not current or (current.get("side"), current.get("open_timestamp")) != identity:
                    return
                await self._process_single_symbol_locked(
                    symbol, quoted_at, None, False, exit_frame=frame,
                    exit_quote=price, exit_only=True)
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            self.account.log(f"⚠️ [{symbol}] 即時出口行情無效: {exc}", "WARNING")

    async def _ticker_loop(self):
        """接收 Binance 全合約 ticker；UI 名單不再是行情監控邊界。"""
        while True:
            try:
                # symbols=None 對 Binance USD-M 會使用 !miniTicker@arr，一條
                # WebSocket 即可接收所有合約，不會為 500 多個幣建立 REST 請求。
                tickers = await self.ws_exchange.watch_tickers()

                for sym, ticker in tickers.items():
                    if ticker.get("last") is not None:
                        price = float(ticker["last"])
                        clean_sym = sym.replace(":USDT", "") if sym.endswith(":USDT") else sym
                        self.tickers[clean_sym] = price
                        self.tickers[sym] = price
                        self._observe_channel_entry_quote(clean_sym, price, ticker.get("timestamp"))
                    if ticker.get("quoteVolume") is not None:
                        clean_sym = sym.replace(":USDT", "") if sym.endswith(":USDT") else sym
                        self.ticker_volumes[clean_sym] = float(ticker["quoteVolume"])
                        self.ticker_volumes[sym] = float(ticker["quoteVolume"])

                now = time.time()
                self._update_market_surveillance(tickers, now)
                self.last_ticker_success_ts = now

                # ── BTC 插針偵測 ─────────────────────────────────────
                # 用 WebSocket 毫秒級報價做滑動視窗；比等到 1m K 收盤快太多。
                if BTC_FLASH_CRASH_DROP_PCT > 0 or BTC_FLASH_CRASH_PUMP_PCT > 0:
                    btc_raw = (
                        tickers.get("BTC/USDT:USDT")
                        or tickers.get("BTC/USDT")
                        or {}
                    )
                    btc_live = btc_raw.get("last")
                    if btc_live is not None:
                        btc_live = float(btc_live)
                        self._btc_price_samples.append((now, btc_live))
                        # 找到窗口起始參考價（BTC_FLASH_CRASH_WINDOW_SEC 秒前）
                        cutoff = now - BTC_FLASH_CRASH_WINDOW_SEC
                        ref_price: float | None = None
                        for ts, px in self._btc_price_samples:
                            if ts <= cutoff:
                                ref_price = px
                            else:
                                break
                        if ref_price and ref_price > 0:
                            drop_pct = (ref_price - btc_live) / ref_price * 100.0
                            # 冷卻：同一次插針事件最多觸發一次（60 秒內不重複）
                            cooldown_ok = now - self._btc_flash_crash_last_triggered_at > 60.0
                            pump_pct = -drop_pct
                            crash_down = drop_pct >= BTC_FLASH_CRASH_DROP_PCT
                            crash_up = pump_pct >= BTC_FLASH_CRASH_PUMP_PCT
                            if cooldown_ok and (crash_down or crash_up):
                                self._btc_flash_crash_last_triggered_at = now
                                close_side = "LONG" if crash_down else "SHORT"
                                side_label = "多" if close_side == "LONG" else "空"
                                event_label = "急跌" if crash_down else "急拉"
                                event_move = drop_pct if crash_down else pump_pct
                                self._market_crash_entry_cooldown_until = max(
                                    float(getattr(self, "_market_crash_entry_cooldown_until", 0.0)),
                                    now + MARKET_CRASH_ENTRY_COOLDOWN_SEC,
                                )
                                positions_to_close = self._btc_flash_crash_close_symbols(
                                    self.account.positions,
                                    getattr(self.account, "position_meta", {}),
                                    close_side,
                                )
                                self.account.log(
                                    f"🚨 [全市場熔斷] BTC {BTC_FLASH_CRASH_WINDOW_SEC:.0f}秒內{event_label} "
                                    f"{event_move:.2f}%；立即平掉所有{side_label}單，"
                                    f"停止新倉 {MARKET_CRASH_ENTRY_COOLDOWN_SEC:.0f} 秒"
                                    + ("：" + ", ".join(positions_to_close) if positions_to_close else ""),
                                    "DANGER",
                                )
                                for sym in list(getattr(self.account, "pending_limit_orders", {})):
                                    asyncio.create_task(self.account.cancel_pending_limit(
                                        sym, "全市場熔斷，取消等待開倉掛單",
                                    ))
                                for sym in positions_to_close:
                                    close_price = float(
                                        self.tickers.get(sym)
                                        or self.tickers.get(f"{sym}:USDT")
                                        or 0.0
                                    )
                                    if close_price > 0:
                                        asyncio.create_task(self.account.close_position(
                                            sym, close_price,
                                            f"全市場熔斷 BTC{event_label} ({event_move:.2f}%/{BTC_FLASH_CRASH_WINDOW_SEC:.0f}s)",
                                            is_manual=True,
                                        ))

                await asyncio.gather(*(
                    self._channel_quote_pivot_entry(
                        sym.replace(":USDT", ""), float(ticker["last"]))
                    for sym, ticker in tickers.items() if ticker.get("last") is not None
                    and sym.replace(":USDT", "") not in self.account.positions
                ))

                await asyncio.gather(*(
                    self._channel_quote_exit(
                        sym.replace(":USDT", "") if sym.endswith(":USDT") else sym,
                        float(ticker["last"]), ticker.get("timestamp"))
                    for sym, ticker in tickers.items() if ticker.get("last") is not None
                    and (sym.replace(":USDT", "") if sym.endswith(":USDT") else sym) in self.account.positions
                ))

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.account.log(
                    f"⚠️ [WebSocket Ticker Loop] 錯誤: {exc}，暫時退回 REST 抓取...",
                    "WARNING",
                )
                try:
                    await self.update_market_prices()
                except Exception as rest_exc:
                    self.account.log(f"⚠️ [REST Fallback] 錯誤: {rest_exc}", "WARNING")
                await asyncio.sleep(2)

    _sample_reference_price = staticmethod(sample_reference_price)

    def _market_crash_entries_paused(self, now: float | None = None) -> bool:
        return market_crash_entries_paused(getattr(self, "_market_crash_entry_cooldown_until", 0.0), now)

    _btc_flash_crash_close_symbols = staticmethod(btc_flash_crash_close_symbols)

    def _update_market_surveillance(self, tickers: dict, now: float | None = None) -> None:
        """用全市場秒級價格速度選多空短名單；不在這一層產生交易訊號。"""
        if not FULL_MARKET_SURVEILLANCE_ENABLED:
            self.market_prebreakout_symbols = []
            self.market_prebreakout_directions = {}
            return
        if self.market_surveillance_contracts is None:
            return
        now = float(now if now is not None else time.time())
        excluded_bases = {
            "BTC", "ETH", "BNB", "APT", "FET", "TAO",
            "USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE",
            "USD1", "BUSD", "USTC",
        }
        for raw_symbol, ticker in tickers.items():
            symbol = str(raw_symbol).replace(":USDT", "")
            if symbol not in self.market_surveillance_contracts:
                continue
            last = ticker.get("last")
            if last is None or float(last) <= 0:
                continue
            quote_volume = float(ticker.get("quoteVolume") or 0.0)
            percentage = float(ticker.get("percentage") or 0.0)
            self._market_ticker_snapshots[symbol] = {
                "last": float(last),
                "quote_volume": quote_volume,
                "percentage": percentage,
                "updated_at": now,
            }
            samples = self._market_price_samples.setdefault(
                symbol,
                deque(maxlen=max(
                    30,
                    int(max(
                        FULL_MARKET_SURVEILLANCE_LONG_WINDOW_SEC,
                        FULL_MARKET_SURVEILLANCE_STEADY_WINDOW_SEC,
                    ) * 2) + 10,
                )),
            )
            if not samples or now > samples[-1][0]:
                samples.append((now, float(last)))

        ranked_long = []
        ranked_short = []
        steady_ranked_long = []
        steady_ranked_short = []

        btc_1h_dir = getattr(self, "st_direction_1h_cache", {}).get("BTC/USDT", 0)
        steady_candidates = getattr(self, "_market_steady_candidates", {})

        for symbol, snapshot in self._market_ticker_snapshots.items():
            if now - float(snapshot.get("updated_at") or 0.0) > 5.0:
                continue
            if symbol in ENTRY_DISABLED_SYMBOLS:
                continue
            if self.execution_symbols is not None and symbol not in self.execution_symbols:
                continue
            base = symbol.split("/", 1)[0].upper()
            if base in excluded_bases or base.endswith(("UP", "DOWN", "BULL", "BEAR")):
                continue
            if float(snapshot.get("quote_volume") or 0.0) < SYMBOL_MIN_QUOTE_VOLUME:
                continue
            if abs(float(snapshot.get("percentage") or 0.0)) > SYMBOL_MAX_24H_CHANGE_PCT:
                continue
            samples = self._market_price_samples.get(symbol) or deque()

            # 短線爆發分數計算
            short_ref = self._sample_reference_price(
                samples, now - FULL_MARKET_SURVEILLANCE_SHORT_WINDOW_SEC,
            )
            if not short_ref:
                continue
            last = float(snapshot["last"])
            short_move_pct = (last / short_ref - 1.0) * 100.0
            long_ref = self._sample_reference_price(
                samples, now - FULL_MARKET_SURVEILLANCE_LONG_WINDOW_SEC,
            )
            long_move_pct = (
                (last / long_ref - 1.0) * 100.0 if long_ref else short_move_pct
            )

            # 5分鐘穩定趨勢計算
            steady_ref = self._sample_reference_price(
                samples, now - FULL_MARKET_SURVEILLANCE_STEADY_WINDOW_SEC,
            )
            steady_move_pct = (last / steady_ref - 1.0) * 100.0 if steady_ref else 0.0

            if steady_move_pct >= FULL_MARKET_SURVEILLANCE_STEADY_MIN_MOVE_PCT and btc_1h_dir >= 0:
                steady_ranked_long.append((steady_move_pct, symbol))
            elif steady_move_pct <= -FULL_MARKET_SURVEILLANCE_STEADY_MIN_MOVE_PCT and btc_1h_dir <= 0:
                steady_ranked_short.append((abs(steady_move_pct), symbol))

            score = 0.75 * short_move_pct + 0.25 * long_move_pct
            if max(abs(short_move_pct), abs(long_move_pct)) < FULL_MARKET_SURVEILLANCE_MIN_MOVE_PCT:
                continue
            row = (abs(score), abs(short_move_pct), symbol)
            if score > 0:
                ranked_long.append(row)
            elif score < 0:
                ranked_short.append(row)

        ranked_long.sort(reverse=True)
        ranked_short.sort(reverse=True)
        steady_ranked_long.sort(reverse=True)
        steady_ranked_short.sort(reverse=True)

        selected_long = ranked_long[:FULL_MARKET_SURVEILLANCE_SIDE_COUNT]
        selected_short = ranked_short[:FULL_MARKET_SURVEILLANCE_SIDE_COUNT]

        # 穩定趨勢保留機制
        for _, symbol in steady_ranked_long[:FULL_MARKET_SURVEILLANCE_STEADY_SIDE_COUNT]:
            steady_candidates[symbol] = {
                "direction": "LONG",
                "retained_until": now + FULL_MARKET_SURVEILLANCE_STEADY_RETENTION_SEC
            }
        for _, symbol in steady_ranked_short[:FULL_MARKET_SURVEILLANCE_STEADY_SIDE_COUNT]:
            steady_candidates[symbol] = {
                "direction": "SHORT",
                "retained_until": now + FULL_MARKET_SURVEILLANCE_STEADY_RETENTION_SEC
            }

        # 清除過期的穩定趨勢候選
        expired = [s for s, d in steady_candidates.items() if now > d.get("retained_until", 0)]
        for s in expired:
            del steady_candidates[s]

        # 合併爆發名單與穩定趨勢名單
        self.market_prebreakout_symbols = list(dict.fromkeys([
            row[2] for row in [*selected_long, *selected_short]
        ] + list(steady_candidates.keys())))

        self.market_prebreakout_directions = {
            **{row[2]: "LONG" for row in selected_long},
            **{row[2]: "SHORT" for row in selected_short},
            **{s: d["direction"] for s, d in steady_candidates.items()}
        }

        # 每輪整批替換，避免已離開即時雷達的舊幣仍帶著過期高分。
        self._market_surveillance_scores = {
            row[2]: float(row[0]) for row in [*ranked_long, *ranked_short]
        }
        self.market_surveillance_updated_at = now

    def market_surveillance_status(self) -> dict:
        return {
            "enabled": bool(FULL_MARKET_SURVEILLANCE_ENABLED),
            "mode": "binance_all_contracts_websocket",
            "tracked_contracts": len(self._market_ticker_snapshots),
            "eligible_contracts": len(self.market_surveillance_contracts or ()),
            "shortlist": list(self.market_prebreakout_symbols),
            "directions": dict(self.market_prebreakout_directions),
            "updated_at": self.market_surveillance_updated_at,
        }

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

    _quality_bonus = staticmethod(quality_bonus)
    _format_pullback_order_log = staticmethod(format_pullback_order_log)
    _pullback_reversal_confirmed = staticmethod(pullback_reversal_confirmed)
    _classify_pullback_drop = staticmethod(classify_pullback_drop)

    def _fresh_pullback_target(self, df: pd.DataFrame, side: str, score: int) -> tuple[float, float]:
        computed = self.strategy.compute_indicators(df)
        curr = computed.iloc[-1]
        atr = float(curr["atr"])
        ema_20 = float(curr["ema_20"])
        kc_edge = float(curr["kc_upper"] if side == "LONG" else curr["kc_lower"])
        target, _distance, room_ok = compute_pullback_target(kc_edge, ema_20, atr, side, score)
        if not room_ok:
            return None, atr
        return target, atr

    def _record_pullback_outcome(self, key: str) -> None:
        stats = getattr(self.account, "pullback_outcome_stats", None)
        if stats is None:
            stats = {}
            self.account.pullback_outcome_stats = stats
        stats[key] = int(stats.get(key, 0)) + 1

    def _drop_pullback_candidate(self, symbol: str, reason: str, now: float, cooldown: bool = True) -> None:
        candidate = self.pending_pullback_candidates.pop(symbol, None)
        if not candidate:
            return
        if cooldown:
            self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
        self._record_pullback_outcome(self._classify_pullback_drop(reason, candidate))
        self.account.log(f"↩️ [回踩候選取消] {symbol}：{reason}", "INFO")

    def _entry_direction_allowed(self, symbol: str, side: str, planned_price: float, log_on_fail: bool = True) -> bool:
        return True

    def _ma3_ma15_entry_allowed(self, symbol: str, side: str, df: pd.DataFrame, log_on_fail: bool = True, entry_type: str = "") -> bool:
        return True

    def _continuous_market_mode_for(self, symbol: str, wave_regime: str, price: float) -> str:
        return "TREND"

    def _same_side_entry_allowed(self, symbol: str, side: str) -> bool:
        return same_side_entry_allowed(self.account.positions, self.account.pending_limit_orders, side, MAX_SAME_SIDE_POSITIONS)

    def _ma5_stop_cooldown_remaining(self, symbol: str, side: str, now: float) -> float:
        return 0.0

    def _ma2_confirmation_allowed(self, symbol: str, side: str, signal: dict) -> bool:
        return True

    @staticmethod
    def _structured_stop_cooldown_blocks(entry_mode: str, remaining: float) -> bool:
        return False
    def _observe_channel_entry_quote(self, symbol, price, quote_ms=None):
        now = time.time()
        quote_times = getattr(self, "_channel_entry_quote_times", None)
        if quote_times is None:
            quote_times = self._channel_entry_quote_times = {}
        try:
            quoted = now if quote_ms is None else float(quote_ms) / 1000
        except (TypeError, ValueError):
            quoted = float("nan")
        quote_times[symbol] = quoted
        pivot = getattr(self, '_channel_live_pivots', None)
        if pivot is None:
            pivot = self._channel_live_pivots = LivePivot()
        if pivot is not None:
            frame = getattr(self, '_channel_exit_frames', {}).get(symbol)
            if symbol in self.account.positions or not math.isfinite(quoted) or not 0 <= now - quoted <= 5:
                pivot.reset(symbol)
            elif frame is not None:
                pivot.observe(symbol, frame, price, ck_direction(frame), quoted)
        watcher = getattr(self, "_channel_intrabar_entries", None)
        if watcher is None:
            return
        if symbol in self.account.positions:
            watcher.reset(symbol)
            return
        if not math.isfinite(quoted) or not 0 <= now - quoted <= 5:
            watcher.reset(symbol)
            return
        watcher.observe(symbol, price, quoted)

    def _live_pivot_ready(self, symbol, frame, price, side):
        """Only confirmed outer breaks may enter; old live-pivot signals are inert."""
        return False

    def _channel_intrabar_ready(self, symbol, frame, price, side, ck_reverse=False, live_pivot=False):
        """Validate the current quote without requiring an observed pullback."""
        if ck_reverse:
            ticket = getattr(self.account, 'channel_profit_reentries', {}).get(symbol, {})
            return (self._ck_reverse_order_authorized(symbol, {'side': side, 'profit_reentry_token': ticket.get('token')})
                    and reverse_quote_ready(self, symbol, frame, price, side))
        quoted = getattr(self, "_channel_entry_quote_times", {}).get(symbol)
        if quoted is not None and (not math.isfinite(quoted) or not 0 <= time.time() - quoted <= 5):
            return False
        if live_pivot:
            return self._live_pivot_ready(symbol, frame, price, side)
        ready = (ck_direction(frame) == side and live_adverse_entry_safe(frame, price, side)
                 and live_ma3_direction_ready(frame, price, side)
                 if ck_reverse else aligned_entry_ready(frame, price, side))
        return symbol not in self.account.positions and ready

    async def _fresh_channel_entry_snapshot(
        self, symbol: str, side: str, candidate_bar_id: object = None,
        allow_live_outer: bool = False, allow_lower_reclaim: bool = False, allow_upper_reclaim: bool = False,
        confirmed_reverse: bool = False, profit_reentry_token: str | None = None, live_pivot: bool = False,
    ) -> dict | None:
        """Revalidate the same closed confirmation; legacy live flags cannot bypass it."""
        import core.config as config
        try:
            frame = await self.fetch_klines(
                symbol, timeframe=config.CONTINUOUS_REVERSE_TIMEFRAME,
                limit=200, keep_live=True,
            )
            if frame is None or frame.empty or len(frame) < 4:
                return None
            frame = self.strategy.compute_indicators(frame.copy())
            latest = frame.iloc[-1]
            # Use the ticker for execution price and closed candles for the signal.
            price = float(
                getattr(self, "tickers", {}).get(symbol)
                or latest["close"]
            )
            upper = float(latest["kc_upper"])
            lower = float(latest["kc_lower"])
        except (TypeError, ValueError, IndexError, KeyError):
            return None
        if not all(math.isfinite(v) and v > 0 for v in (price, upper, lower)) or lower >= upper:
            return None
        if self._channel_terminal_market(frame):
            return None
        if profit_reentry_token and self._ck_reverse_order_authorized(
                symbol, {'side': side, 'profit_reentry_token': profit_reentry_token}):
            if not reverse_quote_ready(self, symbol, frame, price, side):
                return None
            return dict(price=price, kc_upper=upper, kc_lower=lower, frame=frame,
                        signal_code='KC_DIRECT_REVERSE_' + side)
        self._release_resolved_abnormal_exit(symbol, frame, price)
        # Closing a held position on a confirmed reversal must not wait for the
        # new long's trough. The new leg is independently gated before its order.
        closing_reverse = confirmed_reverse and symbol in self.account.positions
        ticket = getattr(self.account, 'channel_profit_reentries', {}).get(symbol, {})
        if profit_reentry_token and ticket.get('mode') == 'ck_reverse':
            if (ticket.get('token') != profit_reentry_token or ticket.get('side') != side
                    or not self._profit_reentry_ready(symbol, ticket, frame, price)):
                return None
            return dict(price=price, kc_upper=upper, kc_lower=lower, frame=frame,
                        signal_code='KC_REVERSE_' + side)
        if live_pivot:
            if not profit_reentry_token and candidate_bar_id != 'live:' + str(float(frame.iloc[-1]['timestamp'])):
                return None
            if not self._live_pivot_ready(symbol, frame, price, side):
                return None
            if ticket:
                if (ticket.get('token') != profit_reentry_token
                        or not self._profit_reentry_ready(symbol, ticket, frame, price)):
                    return None
            elif profit_reentry_token:
                return None
            return dict(price=price, kc_upper=upper, kc_lower=lower, frame=frame,
                        signal_code='KC_LIVE_PIVOT_' + side)
        # Channel Swing new legs use the live MA3 outer-cross/continuation
        # entry for every route; the retired two-closed-body rule is not used.
        # Room is evaluated after the snapshot, so a temporary shortage cannot
        # become a candidate-invalidated lock through a None snapshot.
        entry_ready = aligned_entry_ready(frame, price, side)
        if not entry_ready:
            return None
        if profit_reentry_token is not None:
            ticket = getattr(self.account, "channel_profit_reentries", {}).get(symbol)
            if (not ticket or ticket.get("token") != profit_reentry_token
                    or ticket.get("side") != side or ticket.get("phase") != "closed"
                    or symbol in self.account.positions):
                return None
            ready = self._profit_reentry_ready(symbol, ticket, frame, price)
            if not ready:
                return None
            return {"price": price, "kc_upper": upper, "kc_lower": lower, "frame": frame,
                    "signal_code": (outside_reentry(frame, price, side) if ticket.get("mode") == "outer_cycle"
                                    else self._channel_swing_action(frame, price, check_profit_room=False))["reason"],
                    "outer_cycle_reentry": ticket.get("mode") == "outer_cycle"}
        exit_info = getattr(self, "_channel_swing_peak_exit_info", {}).get(symbol)
        if exit_info and exit_info.get("require_new_closed_break") and self._channel_peak_exit_reentry_blocked(
            "ENTER", False, side, frame, exit_info, symbol, live_price=price,
        ):
            return None
        profit_ticket = getattr(self.account, "channel_profit_reentries", {}).get(symbol)
        if profit_ticket:
            return None
        fresh_candidate_bar_id = self._channel_candidate_bar_id(frame)
        if (
            not all(math.isfinite(value) for value in (price, upper, lower))
            or price <= 0.0 or lower >= upper
            or (
                candidate_bar_id is not None
                and fresh_candidate_bar_id != candidate_bar_id
            )
            or self._channel_swing_action(
                frame, price,
                self.account.positions.get(symbol, {}).get("side") if confirmed_reverse else None,
                position_open_timestamp=self.account.positions.get(symbol, {}).get("open_timestamp"),
                position_path=self.account.positions.get(symbol, {}).get("channel_position_path"),
                allow_live_entry=bool(allow_live_outer),
                outer_entry_only=confirmed_reverse,
                check_profit_room=False,
            ).get("side") != side
        ):
            return None
        return {
            "price": price, "kc_upper": upper, "kc_lower": lower,
            "frame": frame,
            "signal_code": self._channel_swing_action(frame, price, check_profit_room=False)["reason"] if not confirmed_reverse else None,
        }

    def _channel_candle_entry_blocked(self, symbol: str, now: float | None = None) -> bool:
        """Use fill timestamps, not signal candles, to limit churn per UTC minute."""
        minute = int((time.time() if now is None else now) // 60)
        if getattr(self, "_channel_entry_minute", {}).get(symbol) == minute:
            return True

        def same_minute(value, divisor=1):
            try:
                stamp = float(value) / divisor
                return math.isfinite(stamp) and stamp > 0 and int(stamp // 60) == minute
            except (TypeError, ValueError, OverflowError):
                return False

        if same_minute(getattr(self.account, "last_closed_at", {}).get(symbol)):
            return True
        if same_minute(self.account.positions.get(symbol, {}).get("open_timestamp")):
            return True
        return any(
            trade.get("symbol") == symbol
            and trade.get("action") in {"OPEN_LONG", "OPEN_SHORT", "CLOSE_LONG", "CLOSE_SHORT"}
            and same_minute(trade.get("id"), 1000)
            for trade in getattr(self.account, "trades", [])
        )

    async def _place_structured_entry(
        self, symbol: str, signal: dict, live_price: float, channel_snapshot: dict | None = None
    ) -> bool:
        locks = getattr(self, "_channel_entry_locks", None)
        if locks is None:
            locks = self._channel_entry_locks = {}
        async with locks.setdefault(symbol, asyncio.Lock()):
            if self._channel_candle_entry_blocked(symbol) and not self._ck_reverse_order_authorized(symbol, signal):
                self.account.log(f"⏳ {symbol} KC_ONE_ENTRY_PER_CANDLE：本根1分鐘K已開倉或平倉，等待下一根再評估", "INFO")
                return False
            return await self._place_structured_entry_locked(symbol, signal, live_price, channel_snapshot)

    async def _place_structured_entry_locked(
        self, symbol: str, signal: dict, live_price: float, channel_snapshot: dict | None = None
    ) -> bool:
        """Place one of the three non-MA5 entries with an exchange hard stop."""
        # DEFAULT_SYMBOLS is the final execution allowlist. Full-market
        # surveillance may inspect other contracts for crash protection and
        # diagnostics, but those observations must never become an order.
        if symbol not in DEFAULT_SYMBOLS:
            return False
        committed = len(self.account.positions) + len(self.account.pending_limit_orders)
        if MAX_SLOTS > 0 and committed >= MAX_SLOTS:
            return False
        score = int(signal.get("score") or 0)
        side = signal["side"]
        entry_mode = signal["entry_mode"]
        if entry_mode != "CHANNEL_SWING":
            self.account.log(f"🛑 {symbol} 舊策略 {entry_mode} 已停用", "WARNING")
            return False
        signal_volume_ratio = signal.get("volume_ratio")
        import core.config as runtime_config
        min_entry_volume_ratio = (
            runtime_config.CHANNEL_SWING_ENTRY_MIN_VOLUME_RATIO
            if entry_mode == "CHANNEL_SWING"
            else KELTNER_MIN_VOLUME_RATIO
        )
        # Channel Swing 不再設置 1 倍量能門檻；依使用者要求，外軌與一般峰谷
        # 訊號都允許進場，僅保留異常行情與預估淨成本安全檢查。
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
        if entry_mode == "CHANNEL_SWING":
            candidate_bar_id = signal.get("candidate_bar_id")
            # A profit-close token authorizes one fresh reentry attempt; a
            # transient price rejection must remain eligible for later quotes.
            live_outer_entry = bool(signal.get("profit_reentry_token"))
            validation_bar_id = candidate_bar_id
            invalid_candidate_key = (
                symbol, str(side).upper(), validation_bar_id,
            ) if validation_bar_id is not None else None
            invalid_candidates = getattr(
                self, "_channel_invalid_entry_candidates", set(),
            )
            if not live_outer_entry and invalid_candidate_key in invalid_candidates:
                self.account.log(
                    f"🛑 {symbol} {side} 候選K {candidate_bar_id} 已通過失效鎖拒絕，不再重試",
                    "WARNING",
                )
                return False
            if (getattr(self.account, "channel_profit_reentries", {}).get(symbol)
                    and not signal.get("profit_reentry_token")):
                # Refresh through the shared release check; never trust a cached release candle.
                channel_snapshot = None
            # Cached scans cannot authorize an order after CK/gap conditions change.
            fresh_snapshot = None
            if fresh_snapshot is None:
                fresh_snapshot = await self._fresh_channel_entry_snapshot(
                    symbol, side, validation_bar_id,
                    allow_live_outer=True,
                    confirmed_reverse=bool(signal.get("channel_reversal")),
                    profit_reentry_token=signal.get("profit_reentry_token"),
                    live_pivot=bool(signal.get('live_pivot')),
                )
            if fresh_snapshot is None:
                watcher = getattr(self, "_channel_intrabar_entries", None)
                if watcher is not None:
                    watcher.reset(symbol)
                if invalid_candidate_key is not None and not live_outer_entry and not signal.get("live_pivot") and not signal.get("live_outer") and signal.get("signal_code") not in ENTRY_TREND_CODES:
                    if not hasattr(self, "_channel_invalid_entry_candidates"):
                        self._channel_invalid_entry_candidates = set()
                    self._channel_invalid_entry_candidates.add(invalid_candidate_key)
                self.account.log(
                    f"🛑 {symbol} {side} 下單前最新行情已不符合進場確認，取消開倉",
                    "WARNING",
                )
                return False
            if fresh_snapshot.get("signal_code"):
                signal["signal_code"] = fresh_snapshot["signal_code"]
            planned_price = float(fresh_snapshot["price"])
            signal["kc_upper"] = float(fresh_snapshot["kc_upper"])
            signal["kc_lower"] = float(fresh_snapshot["kc_lower"])
            fresh_frame = fresh_snapshot.get("frame")
            if self._channel_terminal_market(fresh_frame):
                self.account.log(f"⏳ {symbol} KC_TREND_END_WAIT：末端期間多空均禁止開倉", "INFO")
                return False
            entry_quote = getattr(self, "tickers", {}).get(symbol) or planned_price
            planned_price = float(entry_quote)
            if not live_adverse_entry_safe(fresh_frame, entry_quote, side):
                self.account.log(f"🛑 {symbol} {side} KC_LIVE_ADVERSE_ENTRY_WAIT：當根反向異常風險，取消開倉", "WARNING")
                return False
            ck_reverse = self._ck_reverse_order_authorized(symbol, signal)
            live_pivot = bool(signal.get('live_pivot'))
            if not ck_reverse and not live_pivot:
                final_entry = self._channel_swing_action(fresh_frame, planned_price)
                if final_entry.get("action") != "ENTER" or final_entry.get("side") != side:
                    self.account.log(
                        f"⏳ {symbol} {side} {final_entry.get('reason', 'KC_ENTRY_WAIT')}：最新快照已不適合追入",
                        "INFO",
                    )
                    return False
            if not (self._live_pivot_ready(symbol, fresh_frame, planned_price, side) if live_pivot else
                    reverse_quote_ready(self, symbol, fresh_frame, planned_price, side) if ck_reverse else aligned_entry_ready(fresh_frame, planned_price, side)):
                watcher = getattr(self, "_channel_intrabar_entries", None)
                if watcher is not None:
                    watcher.reset(symbol)
                self.account.log(
                    f"🛑 {symbol} {side} CK方向或入口確認失效，取消開倉",
                    "WARNING",
                )
                return False
            if not (self._channel_intrabar_ready(symbol, fresh_frame, planned_price, side, live_pivot=True) if live_pivot else
                    self._channel_intrabar_ready(symbol, fresh_frame, planned_price, side, ck_reverse=True)
                    if ck_reverse else self._channel_intrabar_ready(symbol, fresh_frame, planned_price, side)):
                self.account.log(f"⏳ {symbol} {side} KC_ENTRY_QUOTE_WAIT：報價過期或進場條件失效", "INFO")
                return False
            if isinstance(fresh_frame, pd.DataFrame) and not fresh_frame.empty:
                fresh_live = fresh_frame.iloc[-2]
                for field in ("open", "high", "low", "close"):
                    signal[f"signal_candle_{field}"] = float(fresh_live[field])
                signal["atr"] = float(fresh_live.get("atr") or signal.get("atr") or 0.0)
            room = self._channel_profit_room(fresh_frame, planned_price, side)
            signal.pop("profit_room_pct", None)
            signal.pop("estimated_profit_target", None)
            signal.pop("entry_trend_stage", None)
            signal["profit_room_checked"] = room.get("checked", False)
            if not room["allowed"]:
                self.account.log(f"⏳ {symbol} {side} {room['reason']}：{room.get('detail', '')}", "INFO")
                return False
            if "net_room_pct" in room:
                signal["profit_room_pct"] = room["net_room_pct"] / 100.0
            if "target" in room:
                signal["estimated_profit_target"] = room["target"]
            signal["entry_trend_stage"] = room.get("stage")
            getattr(self, "tickers", {})[symbol] = planned_price
        atr = max(float(signal.get("atr") or 0.0), planned_price * 1e-6)
        # Keep closed signal metadata for the order, but assess current market
        # risk from the forming candle and latest execution price.
        risk_candle = fresh_frame.iloc[-1] if isinstance(fresh_frame, pd.DataFrame) and not fresh_frame.empty else None
        risk_open = float(risk_candle["open"]) if risk_candle is not None else float(signal.get("signal_candle_open") or planned_price)
        risk_high = max(float(risk_candle["high"]), planned_price) if risk_candle is not None else float(signal.get("signal_candle_high") or planned_price)
        risk_low = min(float(risk_candle["low"]), planned_price) if risk_candle is not None else float(signal.get("signal_candle_low") or planned_price)
        risk_close = planned_price if risk_candle is not None else float(signal.get("signal_candle_close") or planned_price)
        risk_atr = float(risk_candle.get("atr") or atr) if risk_candle is not None else atr
        if not self._abnormal_market_entry_allowed(
            symbol, side, planned_price, risk_atr,
            risk_open, risk_high, risk_low, risk_close,
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
            amount = self._continuous_entry_amount()
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
        entry_middle = None
        if signal.get("signal_code") in PIVOT_CODES:
            entry_row = fresh_frame.iloc[-1]
            entry_middle = entry_row.get("ema_20", float("nan"))
            if pd.isna(entry_middle):
                entry_middle = entry_row.get("kc_middle", float("nan"))
            if pd.isna(entry_middle):
                entry_middle = (signal["kc_upper"] + signal["kc_lower"]) / 2
            entry_middle = float(entry_middle)
        entry_context = {
            "channel_reverse_wait_ck": bool(ck_reverse),
            "channel_confirmation_bar_id": signal.get("candidate_bar_id") if entry_mode == "CHANNEL_SWING" else None,
            "entry_mode": entry_mode,
            "channel_favorable_rail_reached": False,
            "channel_pivot_entry": signal.get("signal_code") in PIVOT_CODES,
            "channel_pivot_middle_reached": False,
            "entry_kc_middle": entry_middle,
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
            "entry_market_mode": signal.get("market_mode"),
            "channel_entry_profile": signal.get("channel_entry_profile"),
            "channel_entry_profile_basis": signal.get("channel_entry_profile_basis"),
            "entry_kc_upper": float(signal.get("kc_upper") or 0.0),
            "entry_kc_lower": float(signal.get("kc_lower") or 0.0),
            "entry_signal_code": str(signal.get("signal_code") or signal.get("reason") or ""),
            "outer_chase_entry": str(signal.get("signal_code") or signal.get("reason") or "") in {
                "KC_LIVE_UPPER_BREAK_LONG", "KC_LIVE_LOWER_BREAK_SHORT",
                "KC_UPPER_TOUCH_LONG", "KC_LOWER_TOUCH_SHORT",
            },
            # Only positions opened after the new ladder was enabled receive
            # this marker; persisted positions keep their original exit rules.
            "profit_lock_usdt_v2": bool(ENABLE_PROFIT_LOCK_USDT),
        }
        kwargs = dict(
            symbol=symbol, side=side, amount_usdt=amount, sl=sl, tp=0.0,
            reason=signal["reason"], atr=atr, leverage=leverage,
            signal_score=score, entry_context=entry_context,
        )
        # Account refreshes can observe a close while quote/risk checks await.
        if self._channel_candle_entry_blocked(symbol) and not self._ck_reverse_order_authorized(symbol, signal):
            self.account.log(f"⏳ {symbol} KC_ONE_ENTRY_PER_CANDLE：送單前確認當根已有成交，取消重開", "INFO")
            return False
        if entry_mode == "CHANNEL_SWING":
            if (live_pivot or signal.get("live_outer") or signal.get("signal_code") in ENTRY_TREND_CODES) and self._ck_reverse_new_leg_halted():
                return False
            if ck_reverse and (self._ck_reverse_new_leg_halted()
                               or not self._ck_reverse_order_authorized(symbol, signal)):
                return False
            latest_price = float(getattr(self, "tickers", {}).get(symbol) or planned_price)
            if (latest_price != planned_price
                    or not (self._channel_intrabar_ready(symbol, fresh_frame, latest_price, side, live_pivot=True) if live_pivot else
                            self._channel_intrabar_ready(symbol, fresh_frame, latest_price, side, ck_reverse=True)
                            if ck_reverse else self._channel_intrabar_ready(symbol, fresh_frame, latest_price, side))):
                self.account.log(f"⏳ {symbol} {side} KC_INTRABAR_RECHECK：價格或進場條件已變，等待重新評估", "INFO")
                return False
        if is_limit:
            placed = await self.account.place_limit_entry(
                target_price=planned_price, post_only=True, **kwargs
            )
        else:
            placed = await self.account.open_position(
                price=planned_price, **kwargs
            )
        if placed:
            pivot = getattr(self, '_channel_live_pivots', None)
            if pivot is not None:
                pivot.reset(symbol)
            watcher = getattr(self, "_channel_intrabar_entries", None)
            if watcher is not None:
                watcher.reset(symbol)
            if not hasattr(self, "_channel_entry_minute"):
                self._channel_entry_minute = {}
            self._channel_entry_minute[symbol] = int(time.time() // 60)
            if channel_swing_no_stop:
                position = getattr(self.account, "positions", {}).get(symbol)
                if isinstance(position, dict):
                    position["channel_trend_quality"] = float(
                        signal.get("trend_quality") or 0.0
                    )
                    position["channel_volume_ratio"] = float(
                        signal.get("volume_ratio") or 0.0
                    )
                    position["channel_energy_score"] = self._channel_candidate_energy(signal)
            order_type = "支撐限價" if is_limit else "市價"
            protection_text = (
                "MA3順KC軌外進場／延續；同根限次、鎖利與緊急出口沿用"
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
        from core.routes.legacy_routes import place_ma5_reversal_entry_legacy
        return await place_ma5_reversal_entry_legacy(self, symbol, side, ma5_sig, live_price, now)

    async def _monitor_pullback_candidates(self, now: float) -> None:
        self.pending_pullback_candidates.clear()
        return

    async def _validate_pending_limit_orders(self, now: float) -> None:
        from core.routes.legacy_routes import validate_pending_limit_orders_legacy
        return await validate_pending_limit_orders_legacy(self, now)

    _validate_strict_pivot_entry = staticmethod(validate_strict_pivot_entry)
    _resolve_entry_atr = staticmethod(resolve_entry_atr)
    _pivot_confirmation_body_atr = staticmethod(pivot_confirmation_body_atr)
    _strong_burst_live_entry_is_valid = staticmethod(strong_burst_live_entry_is_valid)
    _resolve_trailing_atr = staticmethod(resolve_trailing_atr)
    _opposite_closed_candle_exit = staticmethod(opposite_closed_candle_exit)
    _outer_run_second_candle_status = staticmethod(outer_run_second_candle_status)
    _detect_btc_1m_pulse = staticmethod(detect_btc_1m_pulse)
    _begin_btc_lead_shadow = staticmethod(begin_btc_lead_shadow)
    _record_btc_lead_shadow_candidate = staticmethod(record_btc_lead_shadow_candidate)
    _btc_pulse_blocks_entry = staticmethod(btc_pulse_blocks_entry)
    _directional_trend_quality = staticmethod(directional_trend_quality)
    _channel_volume_ratio = staticmethod(channel_volume_ratio)
    _channel_held_volume_is_declining = staticmethod(channel_held_volume_is_declining)
    _channel_held_momentum_is_declining = staticmethod(channel_held_momentum_is_declining)
    _channel_long_profit_room = staticmethod(channel_long_profit_room)
    _channel_profit_room = staticmethod(channel_profit_room)
    _candidate_profit_potential = staticmethod(candidate_profit_potential)
    _touch_entry_math_favorable = staticmethod(touch_entry_math_favorable)
    _channel_entry_requires_profit_room = staticmethod(channel_entry_requires_profit_room)
    _channel_recent_candles_whipsawing = staticmethod(channel_recent_candles_whipsawing)
    _channel_candidate_energy = staticmethod(channel_candidate_energy)
    _channel_confirmed_candidate_energy = staticmethod(channel_confirmed_candidate_energy)
    _channel_price_is_outside_for_side = staticmethod(channel_price_is_outside_for_side)
    _select_strongest_same_side_candidates = staticmethod(select_strongest_same_side_candidates)
    _channel_same_side_committed = staticmethod(channel_same_side_committed)
    _channel_takeover_net_pnl = staticmethod(channel_takeover_net_pnl)
    _channel_candidate_bar_id = staticmethod(channel_candidate_bar_id)
    _channel_outer_directional_entry_allowed = staticmethod(channel_outer_directional_entry_allowed)
    _channel_closed_body_break_entry_allowed = staticmethod(channel_closed_body_break_entry_allowed)
    _channel_closed_body_break_has_outer_ma3_reversal = staticmethod(channel_closed_body_break_has_outer_ma3_reversal)
    _channel_closed_body_break_entry_action = staticmethod(channel_closed_body_break_entry_action)
    _channel_outer_continuation_entry_action = staticmethod(channel_outer_continuation_entry_action)
    _channel_outer_uptrend_entry_action = staticmethod(channel_outer_uptrend_entry_action)
    _channel_outer_downtrend_entry_action = staticmethod(channel_outer_downtrend_entry_action)
    _channel_outer_trend_entry_action = staticmethod(channel_outer_trend_entry_action)
    _channel_strong_first_outer_touch_action = staticmethod(channel_strong_first_outer_touch_action)
    _channel_immediate_outer_break_action = staticmethod(channel_immediate_outer_break_action)
    _channel_macro_market_mode = staticmethod(channel_macro_market_mode)
    _channel_mature_outer_trend_is_weak = staticmethod(channel_mature_outer_trend_is_weak)
    _channel_terminal_market = staticmethod(channel_terminal_market)
    _record_channel_chop_event = staticmethod(record_channel_chop_event)
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
            "WAIT_CLOSED_BODY_ADJACENT_BREAK": "等待已收盤外軌K的下一根突破",
            "WAIT_ADJACENT_OUTER_CANDIDATE": "等待緊接的外軌候選K",
            "WAIT_CLOSE_GREEN": "觸下軌，等待綠K收盤",
            "WAIT_CLOSE_RED": "觸上軌，等待紅K收盤",
            "WAIT_BREAK_HIGH": "多方候選成立，等待下一根破高",
            "WAIT_BREAK_LOW": "空方候選成立，等待下一根破低",
            "CANCEL_LONG": "多方候選先破低，已取消",
            "CANCEL_SHORT": "空方候選先破高，已取消",
            "V_TOO_CLOSE_KC": "V線離KC外軌太近",
            "KC_WIDTH_TOO_NARROW": "KC寬度不足設定門檻",
            "CHOP_WAIT_NO_ENTRY": "CHOP_WAIT阻擋一般峰谷進場",
            "CHOP_WAIT_NO_MOMENTUM": "CHOP_WAIT等待有效動能",
            "WAIT_CHOP_MOMENTUM_BREAK": "CHOP動能候選等待下一根確認",
            "CANCEL_CHOP_BREAKOUT": "CHOP動能突破候選已取消",
            "WAIT_DYNAMIC_TREND": "上軌外多方趨勢品質不足",
            "WAIT_DYNAMIC_DOWNTREND": "下軌外空方趨勢品質不足",
            "KC_UPPER_EXTENSION_LATE": "上漲延伸過大，不追多",
            "KC_LOWER_EXTENSION_LATE": "下跌延伸過大，不追空",
            "KC_UPPER_MA3_REVERSAL_BLOCK_LONG": "MA3 已轉跌至 MA15 下方，不追上軌多單",
            "KC_LOWER_MA3_REVERSAL_BLOCK_SHORT": "MA3 已轉升至 MA15 上方，不追下軌空單",
            "KC_UPPER_MATURE_TREND_WEAK": "漲勢已成熟且量能不足，不在末端追多",
            "KC_TREND_TAIL_WAIT": "趨勢已連續同向走滿設定根數，末端不再開倉",
            "KC_ENTRY_BODY_OVERHEAT_WAIT": "進場當根順向實體過大（過熱），不追價",
            "KC_ENTRY_PREV_BODY_WAIT": "前一根已收線是大K，純趨勢進場不追價",
            "KC_LOWER_MATURE_TREND_WEAK": "跌勢已成熟且量能不足，不在末端追空",
            "KC_CLOSED_BODY_BREAK_LOW_VOLUME": "收盤實體突破量能不足，繼續找其他幣",
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
                "V_TOO_CLOSE_KC",
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
            f"🧭 [Channel Swing狀態] {symbol} {label}",
            level,
        )

    _channel_chop_state = staticmethod(channel_chop_state)
    _channel_chop_breakout_action = staticmethod(channel_chop_breakout_action)
    _channel_entry_reuses_exit_bar = staticmethod(channel_entry_reuses_exit_bar)
    @staticmethod
    def _channel_peak_exit_reentry_blocked(
        action: str,
        has_position: bool,
        entry_side: str | None,
        frame: "pd.DataFrame",
        peak_exit_info: dict | None,
        symbol: str,
        max_bars: int = 3,
        live_price: float | None = None,
    ) -> bool:
        """三點峰谷平倉後，在價格仍位於外軌一側時封鎖同方向重開倉。

        解鎖條件（滿足任一即可）：
        1. 至少有一根已收盤 K 的收盤價回到 KC 通道內（確認轉折）。
        2. 自峰谷平倉後，已過了 max_bars 根已收盤 K。
        """
        if not (
            action == "ENTER"
            and not has_position
            and entry_side
            and isinstance(peak_exit_info, dict)
        ):
            return False
        if live_price is not None and aligned_entry_ready(frame, live_price, entry_side):
            decision = aligned_entry(frame, live_price)
            is_breakout = decision.get("reason", "").startswith("KC_LIVE_BODY_BREAKOUT")
            try:
                current_bar = float(frame.iloc[-1]['timestamp'])
                exit_bar = float(peak_exit_info['exit_bar_id'])
                blocked = not (math.isfinite(current_bar) and math.isfinite(exit_bar) and current_bar > exit_bar)
                return blocked and not is_breakout
            except (TypeError, ValueError, KeyError, IndexError):
                return True
        if peak_exit_info.get("require_new_closed_break") and peak_exit_info.get("allow_new_outer_signal"):
            try:
                exit_bar = float(peak_exit_info["exit_bar_id"])
                live_bar = float(frame.iloc[-1].get("timestamp", frame.index[-1]))
                breakout_bar = float(frame.iloc[-3].get("timestamp", frame.index[-3]))
                price = float(frame.iloc[-1]["close"]) if live_price is None else live_price
                if entry_side == "SHORT" and three_closed_short_breakout_ready(frame, price):
                    breakout_bar = float(frame.iloc[-4].get("timestamp", frame.index[-4]))
                decision = TradingEngine._channel_swing_action(frame, price)
                reason = str(decision.get("reason", ""))
                if reason in TREND_CODES or reason.startswith("KC_NEXT_LIVE_PUSH_"):
                    signal_bar = float(frame.iloc[-2].get("timestamp", frame.index[-2]))
                elif reason.startswith("LIVE_"):
                    signal_bar = live_bar
                else:
                    signal_bar = breakout_bar
                opposite = str(peak_exit_info.get("side", "")).upper() != str(entry_side).upper()
                # Closing a short during the breakout bar does not invalidate
                # that bar's subsequent closed confirmation for a new long.
                fresh = signal_bar > exit_bar or (
                    opposite and signal_bar == exit_bar and live_bar > exit_bar)
                return not (fresh and decision.get("action") == "ENTER" and decision.get("side") == entry_side)
            except (TypeError, ValueError, KeyError, IndexError):
                return True
        if peak_exit_info.get("require_new_closed_break"):
            try:
                price = float(frame.iloc[-1]["close"]) if live_price is None else live_price
                decision = aligned_entry(frame, price)
                if decision.get("reason") in TREND_CODES and decision.get("side") == entry_side:
                    signal_time = float(frame.iloc[-2].get("timestamp", frame.index[-2]))
                    exit_time = float(peak_exit_info["exit_bar_id"])
                    return not (math.isfinite(signal_time) and math.isfinite(exit_time) and signal_time > exit_time)
                breakout, confirmation = frame.iloc[-3], frame.iloc[-2]
                price = float(frame.iloc[-1]["close"]) if live_price is None else live_price
                three_short = entry_side == "SHORT" and three_closed_short_breakout_ready(frame, price)
                offset = -4 if three_short else -3
                breakout = frame.iloc[offset]
                breakout_id = breakout.get("timestamp", frame.index[offset])
                breakout_time, exit_time = float(breakout_id), float(peak_exit_info["exit_bar_id"])
                if not all(math.isfinite(value) for value in (breakout_time, exit_time)) or breakout_time <= exit_time:
                    return True
                if entry_side == "LONG":
                    return not (float(breakout["open"]) <= float(breakout["kc_upper"]) < float(breakout["close"])
                                and float(confirmation["close"]) > max(float(confirmation["open"]), float(confirmation["kc_upper"])))
                if entry_side == "SHORT":
                    return not (float(breakout["open"]) >= float(breakout["kc_lower"]) > float(breakout["close"])
                                and float(confirmation["close"]) < min(float(confirmation["open"]), float(confirmation["kc_lower"])))
            except (TypeError, ValueError, KeyError, IndexError):
                pass
            return True
        exited_side = str(peak_exit_info.get("side") or "").upper()
        if exited_side != str(entry_side or "").upper():
            # 反向開倉不受峰谷冷卻限制
            return False
        bar_count = int(peak_exit_info.get("bar_count") or 0)
        if bar_count >= max_bars:
            return False
        # 若最近一根已收盤 K 已回到 KC 通道內，解除封鎖
        try:
            required = {"close", "kc_upper", "kc_lower"}
            if frame is not None and len(frame) >= 2 and required.issubset(frame.columns):
                last_closed = frame.iloc[-2]
                close_val = float(last_closed["close"])
                upper_val = float(last_closed["kc_upper"])
                lower_val = float(last_closed["kc_lower"])
                if lower_val < close_val < upper_val:
                    # 已收盤 K 回到通道內，解除封鎖
                    return False
        except (TypeError, ValueError, KeyError, IndexError):
            pass
        return True

    _channel_peak_reversal_action = staticmethod(channel_peak_reversal_action)
    _channel_entry_min_profit_ok = staticmethod(channel_entry_min_profit_ok)
    _channel_peak_exit_entry_gate = staticmethod(channel_peak_exit_entry_gate)
    _channel_upper_red_short_reversal_allowed = staticmethod(channel_upper_red_short_reversal_allowed)
    _channel_is_upper_red_peak_short = staticmethod(channel_is_upper_red_peak_short)
    _channel_exit_requests_rotation = staticmethod(channel_exit_requests_rotation)
    _channel_slope_entry_gate = staticmethod(channel_slope_entry_gate)
    _channel_macro_continuation_entry_gate = staticmethod(channel_macro_continuation_entry_gate)
    _channel_closed_body_volume_gate = staticmethod(channel_closed_body_volume_gate)
    _channel_near_chop_entry_gate = staticmethod(channel_near_chop_entry_gate)
    _channel_chop_gate = staticmethod(channel_chop_gate)
    _channel_ma3_outside = staticmethod(channel_ma3_outside)
    _channel_outer_half_space_hold = staticmethod(channel_outer_half_space_hold)
    _check_parabolic_reversal_exit = staticmethod(check_parabolic_reversal_exit)
    _channel_impulse_turn_allowed = staticmethod(channel_impulse_turn_allowed)
    _channel_ma15_convergence_is_gradual = staticmethod(channel_ma15_convergence_is_gradual)
    _channel_outer_gap_expanding = staticmethod(channel_outer_gap_expanding)
    _channel_trend_exit_reason = staticmethod(channel_trend_exit_reason)
    _channel_position_path = staticmethod(channel_position_path)
    _channel_impulse_first_turn = staticmethod(channel_impulse_first_turn)
    _channel_all_same_color_inside = staticmethod(channel_all_same_color_inside)
    _channel_closed_waves_falling = staticmethod(channel_closed_waves_falling)
    _channel_swing_action = staticmethod(channel_swing_action)
    _channel_ck_exit_reason = staticmethod(channel_ck_exit_reason)
    _channel_ck_exit_with_tolerance = staticmethod(channel_ck_exit_with_tolerance)
    _channel_adverse_exit_reason = staticmethod(channel_adverse_exit_reason)
    _two_bar_structure_failure_exit = staticmethod(two_bar_structure_failure_exit)
    _adverse_kc_outer_breached = staticmethod(adverse_kc_outer_breached)
    _confirmed_outer_reversal = staticmethod(confirmed_outer_reversal)
    _range_swing_reverse_side = staticmethod(range_swing_reverse_side)
    _pivot_pullback_ready = staticmethod(pivot_pullback_ready)
    _detect_strict_pivot_prealert = staticmethod(detect_strict_pivot_prealert)

    async def _try_channel_stronger_symbol_takeover(
        self, candidate: dict, now_time: float, daily_halt: bool,
    ) -> tuple[bool, bool]:
        """Close one stalled Channel Swing position before opening a stronger symbol."""
        positions = getattr(self.account, "positions", {})
        pending = getattr(self.account, "pending_limit_orders", {})
        signal_code = str(
            candidate.get("signal_code") or candidate.get("reason") or ""
        )
        confirmed_takeover_signals = {
            "KC_UPPER_TREND_CONFIRMED_LONG",
            "KC_LOWER_TREND_CONFIRMED_SHORT",
            "KC_UPPER_RETEST_BREAK_LONG",
            "KC_LOWER_RETEST_BREAK_SHORT",
            "KC_CLOSED_BODY_HIGH_BREAK_LONG",
            "KC_CLOSED_BODY_LOW_BREAK_SHORT",
        }
        if (
            daily_halt
            or not positions
            or pending
            or str(candidate.get("entry_mode") or "").upper() != "CHANNEL_SWING"
            or int(candidate.get("priority") or 0) < 4
            or signal_code not in confirmed_takeover_signals
        ):
            return False, False

        candidate_side = str(candidate.get("side") or "").upper()
        if candidate_side not in ("LONG", "SHORT"):
            return False, False

        held_candidates = [
            (symbol, position) for symbol, position in positions.items()
            if str(position.get("entry_mode") or "").upper() == "CHANNEL_SWING"
        ]
        if not held_candidates:
            return False, False
        held_symbol, held_position = min(
            held_candidates,
            key=lambda item: (
                str(item[1].get("side") or "").upper() != candidate_side,
                float(
                    item[1].get("channel_confirmed_energy_score")
                or item[1].get("channel_energy_score")
                    or 0.0
                ),
            ),
        )
        candidate_energy = self._channel_confirmed_candidate_energy(candidate)
        held_energy = max(
            0.0,
            float(
                held_position.get("channel_confirmed_energy_score")
                or held_position.get("channel_energy_score")
                or 0.0
            ),
        )
        new_symbol = str(candidate.get("symbol") or "")
        if (
            not new_symbol
            or new_symbol == held_symbol
            or str(held_position.get("entry_mode") or "").upper() != "CHANNEL_SWING"
        ):
            return False, False

        opened_at = float(held_position.get("open_timestamp") or now_time)
        age_sec = max(0.0, float(now_time) - opened_at)
        held_mark = float(
            self.tickers.get(held_symbol)
            or held_position.get("mark_price")
            or held_position.get("entry_price")
            or 0.0
        )
        net_pnl = self._channel_takeover_net_pnl(held_position, held_mark)
        side = candidate_side
        candidate_price = float(
            candidate.get("live_price") or candidate.get("price") or 0.0
        )
        candidate_outside = self._channel_price_is_outside_for_side(
            candidate_price, side,
            float(candidate.get("kc_upper") or 0.0),
            float(candidate.get("kc_lower") or 0.0),
        )
        held_momentum_declining = bool(
            held_position.get("channel_momentum_declining")
        )

        held_side = str(held_position.get("side") or "").upper()
        held_kc_upper = float(held_position.get("channel_kc_upper") or float("inf"))
        held_kc_lower = float(held_position.get("channel_kc_lower") or 0.0)
        held_outside = False
        if held_side == "LONG" and held_mark >= held_kc_upper:
            held_outside = True
        elif held_side == "SHORT" and held_mark <= held_kc_lower:
            held_outside = True

        if held_outside:
            # 如果目前持倉幣種還在 KC 外側（代表趨勢還很強），不允許被強勢換倉
            return False, False

        if not candidate_outside or not held_momentum_declining:
            return False, False
        energy_takeover = bool(
            candidate_energy >= 1.00
            and float(candidate.get("confirmed_trend_quality") or 0.0) >= 0.75
            and float(candidate.get("confirmed_volume_ratio") or 0.0) >= 1.00
            and candidate_energy >= max(
                held_energy * 2.00, held_energy + 0.75,
            )
        )
        if not energy_takeover:
            return False, False
        fresh_snapshot = None
        candidate_bar_id = candidate.get("candidate_bar_id")
        if candidate_bar_id is not None:
            invalid_candidate_key = (new_symbol, side, candidate_bar_id)
            invalid_candidates = getattr(
                self, "_channel_invalid_entry_candidates", set(),
            )
            if invalid_candidate_key in invalid_candidates:
                return True, False
            fresh_snapshot = await self._fresh_channel_entry_snapshot(
                new_symbol, side, candidate_bar_id,
            )
            if fresh_snapshot is None:
                if not hasattr(self, "_channel_invalid_entry_candidates"):
                    self._channel_invalid_entry_candidates = set()
                self._channel_invalid_entry_candidates.add(invalid_candidate_key)
                return True, False
            candidate_price = float(fresh_snapshot["price"])
            candidate["kc_upper"] = float(fresh_snapshot["kc_upper"])
            candidate["kc_lower"] = float(fresh_snapshot["kc_lower"])

        if not await self._execution_price_is_safe(new_symbol, side):
            return True, False

        planned_price = candidate_price
        if planned_price <= 0.0:
            return True, False
        atr = max(float(candidate.get("atr") or 0.0), planned_price * 1e-6)
        if not self._abnormal_market_entry_allowed(
            new_symbol, side, planned_price, atr,
            float(candidate.get("signal_candle_open") or planned_price),
            float(candidate.get("signal_candle_high") or planned_price),
            float(candidate.get("signal_candle_low") or planned_price),
            float(candidate.get("signal_candle_close") or planned_price),
        ):
            return True, False

        self.account.log(
            f"🔄 [強勢換倉] {held_symbol} 已持有 {age_sec / 60.0:.1f} 分鐘、"
            f"估算淨損益 {net_pnl:.3f}U、能量 {held_energy:.2f}；"
            "舊倉已由三根收盤K確認動能連續衰退；"
            f"新幣能量 {candidate_energy:.2f}，"
            f"{new_symbol} {side} 出現 confirmed 強訊號，"
            "先平舊倉再切換", "WARNING",
        )
        closed = await self.account.close_position(
            held_symbol, held_mark,
            f"Channel Swing stronger-symbol takeover -> {new_symbol} {side}",
            is_manual=True,
        )
        if not closed:
            return True, False

        request_replacement = getattr(self.symbol_rotation, "request_replacement", None)
        if callable(request_replacement):
            request_replacement(held_symbol)
        rotation_event = getattr(self, "rotation_event", None)
        if rotation_event is not None:
            rotation_event.set()

        opened = (
            await self._place_structured_entry(
                new_symbol, candidate, planned_price, fresh_snapshot,
            )
            if fresh_snapshot is not None
            else await self._place_structured_entry(
                new_symbol, candidate, planned_price,
            )
        )
        if opened:
            self.account.log(
                f"✅ [強勢換倉] {held_symbol} → {new_symbol} {side} 完成", "SUCCESS",
            )
        else:
            self.account.log(
                f"⚠️ [強勢換倉] {held_symbol} 已平倉，但 {new_symbol} 最終安全檢查"
                "未通過，維持空手等待", "WARNING",
            )
        return True, bool(opened)


    @staticmethod
    def _entry_scan_symbol_snapshot(
        default_symbols: list[str], broad_symbols: list[str],
        positions: dict, pending_orders: dict, entry_scan_allowed: bool,
        max_slots: int,
    ) -> list[str]:
        """Scan the safe pool with capacity, or the active board for takeover."""
        committed = len(positions) + len(pending_orders)
        has_capacity = max_slots <= 0 or committed < max_slots
        if (
            entry_scan_allowed
            and not pending_orders
            and (has_capacity or bool(positions))
        ):
            entry_symbols = list(broad_symbols)
        else:
            entry_symbols = []
        return list(dict.fromkeys([
            *positions.keys(), *pending_orders.keys(), *entry_symbols,
        ]))


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


    def _take_over_manual_position(self, symbol: str, position: dict) -> bool:
        """Adopt a manually opened position into the Channel Swing manager."""
        meta = self.account.position_meta.setdefault(symbol, {})
        entry_mode = str(
            position.get("entry_mode") or meta.get("entry_mode") or ""
        ).upper()
        reason = str(position.get("reason") or meta.get("reason") or "")
        is_manual = (
            entry_mode == "MANUAL"
            or position.get("manual_entry") or meta.get("manual_entry")
            or "手動開倉" in reason
            or "MANUAL" in reason.upper()
        )
        if not is_manual:
            return False
        if entry_mode == "CHANNEL_SWING":
            changed = not (
                position.get("managed_by_bot") is True
                and meta.get("managed_by_bot") is True
            )
            if changed:
                position["manual_entry"] = True
                position["managed_by_bot"] = True
                meta["manual_entry"] = True
                meta["managed_by_bot"] = True
                self.account.save_state()
            return changed
        position["entry_mode"] = "CHANNEL_SWING"
        position["manual_entry"] = True
        position["managed_by_bot"] = True
        meta["entry_mode"] = "CHANNEL_SWING"
        meta["manual_entry"] = True
        meta["managed_by_bot"] = True
        self.account.save_state()
        self.account.log(
            f"🤖 [手動倉接管] {symbol} {position.get('side')} 已交由 Channel Swing 管理",
            "INFO",
        )
        return True


    async def _execute_confirmed_channel_break(self, symbol, frame, price, side, daily_halt=False):
        """Submit on this scan, retaining every structured-order account safety check."""

        # Clear CK + aligned live MA3 outside the rail no longer waits for two bodies.
        if not aligned_entry_ready(frame, price, side):
            return False

        lock = getattr(self, "_channel_break_execution_lock", None)
        if lock is None:
            lock = self._channel_break_execution_lock = asyncio.Lock()
        async with lock:
            pending = getattr(self, "_channel_outer_reentry_after_exit", None)
            if pending is None:
                pending = self._channel_outer_reentry_after_exit = {}
            position = self.account.positions.get(symbol)
            if position:
                return False
            exit_info = getattr(self, "_channel_swing_peak_exit_info", {}).get(symbol)
            if not position and exit_info and exit_info.get("require_new_closed_break") and self._channel_peak_exit_reentry_blocked(
                "ENTER", False, side, frame, exit_info, symbol, live_price=price,
            ):
                return False
            bar_id = self._channel_candidate_bar_id(frame)
            used = getattr(self, "_channel_used_confirmation", None)
            if used is None:
                used = self._channel_used_confirmation = {}
            # Persisted entry records also protect against reuse after restart.
            already_filled = used.get(symbol) == (side, bar_id) or any(
                trade.get("symbol") == symbol
                and trade.get("action") == f"OPEN_{side}"
                and trade.get("channel_confirmation_bar_id") == bar_id
                for trade in getattr(self.account, "trades", [])
            )
            if already_filled:
                pending.pop(symbol, None)
                return False
            reverse_bars = getattr(self, "_channel_pending_reverse_bar", None)
            if reverse_bars is None:
                reverse_bars = self._channel_pending_reverse_bar = {}
            retry_reverse = reverse_bars.get(symbol) == (side, bar_id)
            held_side = position.get("side") if position else (
                ("SHORT" if side == "LONG" else "LONG") if retry_reverse else None
            )
            decision = self._channel_swing_action(
                frame, price, position.get("side") if position else None,
                position_open_timestamp=position.get("open_timestamp") if position else None,
                position_path=position.get("channel_position_path") if position else None,
                allow_live_entry=not bool(position),
                outer_entry_only=retry_reverse,
            )
            if decision.get("side") != side or decision.get("action") not in {"ENTER", "REVERSE"}:
                pending.pop(symbol, None)
                reverse_bars.pop(symbol, None)
                return False
            if position:
                if not (position.get("channel_profit_protection") or {}).get("armed"):
                    return False
                snapshot = await self._fresh_channel_entry_snapshot(
                    symbol, side, bar_id, confirmed_reverse=True,
                )
                if snapshot is None or self.account.positions.get(symbol) is not position:
                    return False
                price = float(snapshot["price"])
                closed = await self.account.close_position(
                    symbol, price, f"Channel Swing {decision['reason']} confirmed close-first {side}", is_manual=True,
                )
                if not closed or symbol in self.account.positions:
                    self.account.log(f"⚠️ [真突破] {symbol} 舊倉尚未平妥，不送反向單", "WARNING")
                    return False
                reverse_bars[symbol] = (side, bar_id)
                self.account.log(f"✅ [真突破平倉] {symbol} 舊倉已平，接著評估 {side} 新倉", "SUCCESS")
            pending[symbol] = side
            if daily_halt:
                self.account.log(f"⏸️ [真突破] {symbol} 帳戶風控暫停新倉，保留重試", "WARNING")
                return False
            latest = frame.iloc[-2]
            confirmation_label = ("live body crossed KC outer rail" if decision["reason"] in LIVE_BODY_BREAKOUT_CODES else
                                  "confirmed price pivot" if decision["reason"] in PIVOT_CODES else
                                  "live MA3 outside CK outer rail" if decision["reason"] in OUTER_CODES | LIVE_OUTER_CODES else
                                  "confirmed CK middle trend" if decision["reason"] in TREND_CODES else
                                  "closed breakout continuation" if decision["reason"] in {"KC_CONTINUATION_LONG", "KC_CONTINUATION_SHORT"} else
                                  "next live breakout candle")
            signal = {
                "symbol": symbol, "side": side, "score": 100,
                "entry_mode": "CHANNEL_SWING", "action": "ENTER_MARKET",
                "signal_code": decision["reason"], "live_outer": decision["reason"] in LIVE_OUTER_CODES,
                "candidate_bar_id": self._channel_candidate_bar_id(frame),
                "reason": f"Channel Swing {decision['reason']} {confirmation_label} {side}",
                "channel_reversal": bool(position or retry_reverse),
                "atr": float(latest.get("atr") or abs(price) * .015),
                "profit_profile": "TREND_EXTENSION", "wave_regime": "TREND",
                **{f"signal_candle_{k}": float(latest[k]) for k in ("open", "high", "low", "close")},
            }
            # Do not bypass abnormal-market, same-side, stop-cooldown, balance,
            # slot, execution-price or account checks in the existing route.
            opened = await self._place_structured_entry(symbol, signal, price)
            if opened:
                used[symbol] = (side, bar_id)
                reverse_bars.pop(symbol, None)
                pending.pop(symbol, None)
                getattr(self, "_channel_swing_peak_exit_info", {}).pop(symbol, None)
                self.account.log(f"✅ [真突破] {symbol} 已直接開 {side}", "SUCCESS")
            else:
                self.account.log(f"⚠️ [真突破] {symbol} {side} 未成交，請查看前述風控原因；本根確認仍有效才重試", "WARNING")
            return bool(opened)

    def release_manual_close_state(self, symbol: str) -> None:
        """Let the strategy fully re-evaluate a symbol after a manual close."""
        if getattr(self.account, "channel_profit_reentries", {}).pop(symbol, None) is not None:
            self.account.save_state()
        for state_name in (
            "_channel_swing_last_exit_bar",
            "_channel_swing_last_reverse_bar",
            "_continuous_last_entry_bar",
            "_channel_profit_wait_candidates",
            "_channel_swing_last_exit_at",
            "_channel_swing_last_exit_side",
            "_channel_emergency_reentry_wait",
            "_channel_outer_reentry_after_exit",
            "_channel_swing_peak_exit_info",
            "_channel_pending_reverse_bar",
        ):
            state = getattr(self, state_name, None)
            if isinstance(state, dict):
                state.pop(symbol, None)
        self._channel_outer_trend_wait.pop(symbol, None)
        self._channel_inner_trend_hold.pop(symbol, None)
        self._channel_invalid_entry_candidates = {
            candidate for candidate in getattr(
                self, "_channel_invalid_entry_candidates", set(),
            ) if candidate[0] != symbol
        }
        self.rotation_event.set()

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
        fraction = (
            CONTINUOUS_SINGLE_SLOT_MARGIN_FRACTION
            if effective_slots == 1
            else 1.0 / effective_slots
            if effective_slots > 1
            else 1.0
        )
        return max(
            0.0,
            min(available, wallet_balance * fraction, TRADE_AMOUNT_USDT, MAX_SLOT_TRADE_USDT),
        )

    _continuous_entry_price_is_safe = staticmethod(continuous_entry_price_is_safe)

    def _abnormal_market_entry_allowed(
        self, symbol: str, side: str, price: float, atr: float,
        candle_open: float, candle_high: float, candle_low: float,
        candle_close: float,
    ) -> bool:
        """阻止異常拉砸期間的新倉；絕不觸發既有持倉的平倉。"""
        now = time.time()
        cooldowns = getattr(self.account, "_rapid_drop_cooldown", {})
        cooldown_at = float(cooldowns.get(symbol) or 0.0) if isinstance(cooldowns, dict) else 0.0
        cooldown_active = bool(
            cooldown_at > 0.0
            and now - cooldown_at < RAPID_DROP_COOLDOWN_SEC
        )
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
        body_atr = abs(candle_close - candle_open) / atr
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
        # A large body aligned with a qualified entry is momentum, not a reason
        # to force a late entry. Opposite-side large bodies remain protected.
        direction_aligned_impulse = bool(
            (requested == "LONG" and signed_move_pct > 0.0)
            or (requested == "SHORT" and signed_move_pct < 0.0)
        )
        strong_live_candle = (
            PIVOT_STRONG_BODY_ATR_MULT > 0
            and body_atr >= PIVOT_STRONG_BODY_ATR_MULT
            and not direction_aligned_impulse
        )
        if not excessive_range and not adverse_impulse and not strong_live_candle:
            # A fresh qualified signal is evaluated against the current candle.
            # A prior 300-second cooldown must not blindly lock a now-calm entry.
            if cooldown_active and isinstance(cooldowns, dict):
                cooldowns.pop(symbol, None)
                self.account.log(
                    f"[Crash cooldown released] {symbol} current candle is calm; allow {str(side).upper()} entry",
                    "SUCCESS",
                )
            return True

        reasons = []
        if excessive_range:
            reasons.append(f"K線振幅 {range_atr:.1f} ATR / {range_pct:.2%}")
        if adverse_impulse:
            reasons.append(f"逆向單根變動 {signed_move_pct:.2%}")
        if strong_live_candle:
            candle_color = "紅／下跌" if candle_close < candle_open else "綠／上漲"
            reasons.append(f"{candle_color}長實體K {body_atr:.2f} ATR")
        cooldowns = getattr(self.account, "_rapid_drop_cooldown", None)
        if isinstance(cooldowns, dict):
            cooldowns[symbol] = time.time()
        self.account.log(
            f"🛡️ {symbol} 異常拉砸／流動性風險，暫停新開{requested}："
            + "；".join(reasons)
            + f"；進入{RAPID_DROP_COOLDOWN_SEC:.0f}秒冷卻",
            "WARNING",
        )
        return False


    def _release_resolved_abnormal_exit(self, symbol, frame, price):
        if next_breakout_ready(self.account, symbol, frame, price):
            self.account.channel_profit_reentries.pop(symbol)
            self.account.save_state()
            return True
        if opposite_entry_releases(self.account, symbol, frame, price):
            ticket = self.account.channel_profit_reentries.pop(symbol)
            self.account.save_state()
            self.account.log(
                f"✅ {symbol} CK與即時MA3已轉向，解除異常平{ticket['side']}舊票據；重新驗證一般入口與風控",
                'INFO',
            )
            return True
        return self._release_resolved_upward_exit(symbol, frame, price)

    def _release_resolved_upward_exit(self, symbol, frame, price):
        """A later effective closed green releases a confirmed upward-exit ticket."""
        tickets = getattr(self.account, "channel_profit_reentries", {})
        ticket = tickets.get(symbol)
        if (not ticket or symbol in self.account.positions or ticket.get("phase") != "closed"
                or ticket.get("side") != "SHORT" or not ticket.get("requires_pullback")
                or ticket.get("close_reason") not in {
                    "Channel Swing KC_SHORT_LIVE_GREEN_LONG_EXIT",
                    "Channel Swing EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL",
                    "Channel Swing EMERGENCY_EXIT_CLOSED_ADVERSE_WATERFALL",
                    "Channel Swing EMERGENCY_EXIT_2_CANDLE_ADVERSE",
                    "Channel Swing EMERGENCY_EXIT_LIVE_ADVERSE_ABNORMAL"}):
            return False
        try:
            exited = float(ticket["exit_bar_id"])
            requested = float(ticket["close_requested_at_ms"])
            if not all(math.isfinite(v) and v > 0 for v in (exited, requested)):
                return False
            if not any(t.get("symbol") == symbol and t.get("action") == "CLOSE_SHORT"
                       and t.get("reason") == ticket["close_reason"]
                       and requested <= float(t.get("id") or 0) < exited + 60_000
                       for t in getattr(self.account, "trades", [])):
                return False
            if frame is None or len(frame) < 4:
                return False
            for _, row in frame.iloc[:-1].iterrows():
                bar, opened, high, low, closed = (float(row[k]) for k in
                    ("timestamp", "open", "high", "low", "close"))
                if (all(math.isfinite(v) and v > 0 for v in (bar, opened, high, low, closed))
                        and bar > exited and low <= opened < closed <= high
                        and (closed - opened) / (high - low) >= .20):
                    tickets.pop(symbol)
                    self.account.save_state()
                    self.account.log(f"✅ {symbol} 後續已收線有效綠K解除舊異常平空等待，重新評估正常入口", "INFO")
                    return True
        except (TypeError, ValueError, KeyError, IndexError, OverflowError):
            return False
        return False

    def _ck_reverse_new_leg_halted(self):
        daily_check = getattr(self.account, 'daily_loss_limit_hit', None)
        return bool((daily_check and daily_check()[0]) or self._market_crash_entries_paused(time.time()))

    def _ck_reverse_order_authorized(self, symbol, signal):
        """Only a fresh matched eligible close grants the direct reverse exception."""
        return reverse_authorized(self.account, symbol, signal, time.time())

    async def _try_ck_reverse(self, symbol, frame, price, daily_halt):
        """CK changes no longer close positions or authorize direct reversals."""
        tickets = getattr(self.account, 'channel_profit_reentries', {})
        changed = False
        if tickets.get(symbol, {}).get('mode') in ('ck_reverse', 'direct_reverse', 'ma3_turn_wait'):
            tickets.pop(symbol)
            changed = True
        for state in (self.account.positions.get(symbol, {}), self.account.position_meta.get(symbol, {})):
            for key in ('channel_reverse_wait_ck', 'channel_terminal_turn'):
                if key in state:
                    state.pop(key)
                    changed = True
        if changed:
            self.account.save_state()
        return False

    def _profit_reentry_ready(self, symbol, ticket, frame, price):
        if (ticket.get("phase") != "closed" or ticket.get("side") not in ("LONG", "SHORT")
                or symbol in self.account.positions):
            return False
        if ticket.get("mode") == "next_breakout":
            decision = self._channel_swing_action(frame, price)
            is_breakout = decision.get("reason", "").startswith("KC_LIVE_BODY_BREAKOUT")
            try:
                current_bar = float(frame.iloc[-1].get("timestamp", frame.index[-1]))
                exit_bar = float(ticket["exit_bar_id"])
                if not (math.isfinite(current_bar) and math.isfinite(exit_bar) and current_bar > exit_bar) and not is_breakout:
                    return False
            except (AttributeError, TypeError, ValueError, KeyError, IndexError):
                return False
            return decision.get("action") == "ENTER" and decision.get("side") == ticket["side"]
        if ticket.get('mode') == 'direct_reverse':
            return (self._ck_reverse_order_authorized(symbol, {'side': ticket['side'], 'profit_reentry_token': ticket['token']})
                    and reverse_quote_ready(self, symbol, frame, price, ticket['side']))
        if ticket.get('mode') == 'ck_reverse':
            return False
        if ticket.get("mode") != "outer_cycle":
            ticket["mode"] = "outer_cycle"
            self.account.save_state()
        # Migrate known normal profit tickets; unknown legacy closes stay conservative.
        reason = str(ticket.get("close_reason") or "")
        if ticket.get("requires_pullback") and ("PROFIT_PROTECTION" in reason or
                (not reason and "opened_at" in ticket)):
            ticket["requires_pullback"] = False
            self.account.save_state()

        if "exit_bar_id" not in ticket:
            # With no reliable close candle, begin observing from this candle.
            try:
                ticket["exit_bar_id"] = float(frame.iloc[-1].get("timestamp", frame.index[-1]))
            except (AttributeError, TypeError, ValueError, IndexError):
                return False
            self.account.save_state()
            return False
        before = ticket.get("pullback_bar")
        if ticket.get("requires_pullback", True):
            ready = abnormal_pullback_ready(ticket, frame, price)
            if ready:
                rail = float(frame.iloc[-1]["kc_upper" if ticket["side"] == "LONG" else "kc_lower"])
                ready = (1 if ticket["side"] == "LONG" else -1) * (price - rail) > 0
        else:
            try:
                bar = float(frame.iloc[-1].get("timestamp", frame.index[-1]))
                exited = float(ticket["exit_bar_id"])
                ready = math.isfinite(bar) and math.isfinite(exited) and bar > exited
            except (AttributeError, TypeError, ValueError, IndexError):
                ready = False
        if before != ticket.get("pullback_bar"):
            self.account.save_state()
        if ready and not ticket.get('requires_pullback', True) and self._live_pivot_ready(symbol, frame, price, ticket['side']):
            return True
        decision = outside_reentry(frame, price, ticket["side"])
        if not self._profit_pivot_is_new(ticket, frame):
            return False
        return ready and decision.get("side") == ticket["side"]

    async def _try_profit_reentry(self, symbol, frame, price, daily_halt):
        lock = getattr(self, "_channel_profit_reentry_lock", None)
        if lock is None:
            lock = self._channel_profit_reentry_lock = asyncio.Lock()
        async with lock:
            await self._try_profit_reentry_locked(symbol, frame, price, daily_halt)

    async def _try_profit_reentry_locked(self, symbol, frame, price, daily_halt):
        ticket = getattr(self.account, "channel_profit_reentries", {}).get(symbol)
        if not ticket or symbol in self.account.positions:
            return
        if ticket.get('mode') in ('direct_reverse', 'ck_reverse', 'ma3_turn_wait'):
            self.account.channel_profit_reentries.pop(symbol, None)
            self.account.save_state()
            return
        # 2026-09-11: 票據超過期限就作廢，避免舊票據在很久之後才觸發重開。
        requested = ticket.get("close_requested_at_ms")
        if requested:
            try:
                if time.time() * 1000 - float(requested) > PROFIT_REENTRY_TICKET_TTL_SEC * 1000:
                    self.account.channel_profit_reentries.pop(symbol, None)
                    self.account.save_state()
                    return
            except (TypeError, ValueError):
                pass
        if ticket.get("phase") == "closing":
            reason = ticket.get("close_reason") or "Channel Swing PROFIT_PROTECTION " + ticket["token"]
            if not any(t.get("symbol") == symbol and t.get("reason") == reason
                       and t.get("action") == "CLOSE_" + ticket.get('old_side', ticket["side"])
                       and (not ticket.get("close_reason") or
                            float(t.get("id") or 0) >= float(ticket.get("close_requested_at_ms") or math.inf))
                       for t in getattr(self.account, "trades", [])):
                self.account.channel_profit_reentries.pop(symbol, None)
                self.account.save_state()
                return
            ticket["phase"] = "closed"
            self.account.save_state()
        if ticket.get('mode') == 'ck_reverse' and (
                self._channel_candidate_bar_id(frame) != ticket.get('confirmation_bar_id')
                or ck_direction(frame) != ticket['side']):
            self.account.channel_profit_reentries.pop(symbol, None)
            self.account.save_state()
            return
        candidate = "profit:" + ticket["token"]
        if any(t.get("symbol") == symbol and t.get("action") == "OPEN_" + ticket["side"]
               and t.get("channel_confirmation_bar_id") == candidate
               for t in getattr(self.account, "trades", [])):
            self.account.channel_profit_reentries.pop(symbol, None)
            self.account.save_state()
            return
        if daily_halt or not self._profit_reentry_ready(symbol, ticket, frame, price):
            return
        decision = ({'reason': 'KC_REVERSE_' + ticket['side']} if ticket.get('mode') == 'ck_reverse' else
                    outside_reentry(frame, price, ticket["side"]) if ticket.get("mode") == "outer_cycle"
                    else self._channel_swing_action(frame, price))
        live_pivot = (ticket.get('mode') == 'outer_cycle' and not ticket.get('requires_pullback', True)
                      and self._live_pivot_ready(symbol, frame, price, ticket['side']))
        if live_pivot:
            decision = {'reason': 'KC_LIVE_PIVOT_' + ticket['side']}
        signal = {"live_pivot": live_pivot, "live_outer": decision['reason'] in LIVE_OUTER_CODES, "side": ticket["side"], "score": 100, "entry_mode": "CHANNEL_SWING",
                  "action": "ENTER_MARKET", "reason": "Channel Swing PROFIT_REENTRY " + decision["reason"] + " " + ticket["token"],
                  "profit_reentry_token": ticket["token"], "signal_code": decision["reason"],
                  "candidate_bar_id": candidate, "profit_profile": "TREND_EXTENSION",
                  "atr": float(frame.iloc[-1].get("atr") or price * .015)}
        # Revalidate quote, remaining profit room, momentum and account risk
        # for every reentry, including same-side outer-cycle tickets.
        if await self._place_structured_entry(symbol, signal, price):
            self.account.channel_profit_reentries.pop(symbol, None)
            getattr(self, "_channel_swing_peak_exit_info", {}).pop(symbol, None)
            self.account.save_state()
            self.account.log(f"✅ [獲利保護重開] {symbol} {ticket['side']} 入口確認與安全檢查通過，已重開", "SUCCESS")

    @staticmethod
    def _profit_pivot_is_new(ticket, frame, price=None, decision=None):
        try:
            # For strict breakouts, we can bypass the closed bar delay
            if decision and decision.get("reason", "").startswith("KC_LIVE_BODY_BREAKOUT"):
                return True
            # Confirmation on the closing candle becomes eligible only once
            # that candle has closed; already closed signals cannot be reused.
            confirmed = float(frame.iloc[-2].get("timestamp", frame.index[-2]))
            exited = float(ticket["exit_bar_id"])
            return math.isfinite(confirmed) and math.isfinite(exited) and confirmed >= exited
        except (KeyError, IndexError, TypeError, ValueError):
            return False

    async def _process_single_symbol(self, symbol, now_time, btc_1m_turn, daily_halt):
        locks = getattr(self, "_channel_symbol_locks", None)
        if locks is None:
            locks = self._channel_symbol_locks = {}
        async with locks.setdefault(symbol, asyncio.Lock()):
            return await self._process_single_symbol_locked(symbol, now_time, btc_1m_turn, daily_halt)

    @staticmethod
    def _channel_exception_exit(position, frame, price):
        """Use the full live body, including entry-bar abnormalities, and retry exits."""
        pending = position.get("channel_exception_exit_pending")
        if pending in {"EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL",
                       "EMERGENCY_EXIT_CLOSED_ADVERSE_WATERFALL", "EMERGENCY_EXIT_2_CANDLE_ADVERSE"}:
            return pending
        try:
            if frame is None or len(frame) < 3:
                return None
            opened = float(position.get("open_timestamp") or 0)
            entry = float(position.get("entry_price") or 0)
            if not all(math.isfinite(v) and v > 0 for v in (opened, entry, float(price))):
                return None
            recent = frame.iloc[-3:].copy()
            for idx, row in recent.iloc[:-1].iterrows():
                bar = float(row["timestamp"]) / 1000
                if not math.isfinite(bar) or bar <= 0:
                    return None
                if bar < opened:
                    # Never attribute a completed pre-entry candle to this holding.
                    recent.loc[idx, "open"] = float(row["close"])
            live_bar = float(recent.iloc[-1]["timestamp"]) / 1000
            if not math.isfinite(live_bar) or live_bar <= 0 or opened >= live_bar + 60:
                return None
            # The live candle can already be abnormal when the order fills.
            # Keep its actual open instead of restarting the body at entry.
            return TradingEngine._channel_adverse_exit_reason(
                recent, position.get("side"), float(price), float(frame.iloc[-2]["atr"]))
        except (TypeError, ValueError, KeyError, IndexError, OverflowError):
            return None

    async def _process_single_symbol_locked(
        self, symbol, now_time, btc_1m_turn, daily_halt,
        exit_frame=None, exit_quote=None, exit_only=False
    ):
        from core.services.symbol_runner import process_single_symbol_runner
        return await process_single_symbol_runner(
            self, symbol, now_time, btc_1m_turn, daily_halt,
            exit_frame=exit_frame, exit_quote=exit_quote, exit_only=exit_only
        )

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
                daily_halt = daily_halt or self._market_crash_entries_paused(now_time)
                available_balance = self.account.get_available_balance()
                if TEST_BUDGET_CAP_USDT > 0:
                    available_balance = min(available_balance, TEST_BUDGET_CAP_USDT)
                from core.config import ENABLE_CONTINUOUS_REVERSE_MODE
                # 開機先完成第一輪全市場排名，避免種子幣在真正最強候選出爐前搶走唯一槽位。
                rotation_ready = (
                    not SYMBOL_ROTATION_ENABLED
                    or (
                        self.symbol_rotation.last_rotation_at > 0.0
                        and not self._entry_waiting_for_post_close_rotation
                    )
                )
                entry_scan_allowed = (
                    not daily_halt
                    and available_balance >= MIN_TRADE_USDT
                    and rotation_ready
                )
                manage_continuous_position = bool(self.account.positions)
                candidate_scan_allowed = bool(
                    not daily_halt
                    and rotation_ready
                    and (
                        available_balance >= MIN_TRADE_USDT
                        or bool(self.account.positions)
                    )
                )
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

                    # 空槽掃描新候選；輪替開啟時才併入全市場 shortlist。
                    # 固定幣種模式只掃 DEFAULT_SYMBOLS 與既有持倉。
                    wallet_balance = float(self.account.get_wallet_balance())

                    active_trade_symbols = list(DEFAULT_SYMBOLS)

                    effective_slot_limit = get_effective_slot_count(wallet_balance)
                    # 輪替模式使用市場短名單 + active_trade_symbols + 已達標候選；
                    # 固定模式則嚴格以 active_trade_symbols 作為新倉掃描白名單。
                    broad_entry_symbols = (
                        list(active_trade_symbols)
                        if not SYMBOL_ROTATION_ENABLED
                        else list(dict.fromkeys([
                            *self.market_prebreakout_symbols,
                            *active_trade_symbols,
                            *getattr(self.symbol_rotation, "entry_scan_symbols", []),
                        ]))
                    )
                    symbols_snapshot = self._entry_scan_symbol_snapshot(
                        list(active_trade_symbols),
                        broad_entry_symbols,
                        self.account.positions,
                        self.account.pending_limit_orders,
                        candidate_scan_allowed,
                        effective_slot_limit,
                    )
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
                    if not candidate_scan_allowed:
                        detected_candidates = []
                    opened_any = False
                    if detected_candidates:
                        score_map = {
                            m["symbol"]: float(m.get("final_score", 0.0))
                            for m in getattr(self.symbol_rotation, "last_metrics", [])
                        }
                        detected_candidates, skipped_same_side = (
                            self._select_strongest_same_side_candidates(
                                detected_candidates, score_map,
                                self._market_surveillance_scores,
                            )
                        )
                        for skipped in skipped_same_side:
                            skipped_coin = skipped["symbol"].replace("/USDT", "")
                            signal_progress.append(
                                f"{skipped_coin} {skipped['side']} 同向候選未入選；"
                                f"預估利潤空間 "
                                f"{float(skipped.get('profit_potential') or 0.0):.2f}%／"
                                f"趨勢品質 {float(skipped.get('trend_quality') or 0.0):.2f}／"
                                f"能量 {self._channel_candidate_energy(skipped):.2f}"
                            )
                        for sig in detected_candidates:
                            symbol = sig["symbol"]
                            coin = symbol.replace("/USDT", "")
                            direction_text = "多單" if sig["side"] == "LONG" else "空單"

                            if (
                                btc_1m_turn in ("LONG", "SHORT")
                                and str(sig.get("side") or "").upper() != btc_1m_turn
                            ):
                                signal_progress.append(
                                    f"{coin} {direction_text} BTC 1m {btc_1m_turn} 強脈衝期間拒絕逆向開倉"
                                )
                                continue

                            takeover_handled, takeover_opened = (
                                await self._try_channel_stronger_symbol_takeover(
                                    sig, now_time, daily_halt,
                                )
                            )
                            if takeover_handled:
                                opened_any = takeover_opened or opened_any
                                if takeover_opened or not self.account.positions:
                                    break
                                continue

                            same_side_committed = self._channel_same_side_committed(
                                self.account.positions,
                                self.account.pending_limit_orders,
                                sig["side"],
                            )
                            if same_side_committed:
                                signal_progress.append(
                                    f"{coin} {direction_text} 已有同向持倉；"
                                    "新候選尚未強到符合換倉門檻"
                                )
                                continue

                            committed_slots = (
                                len(self.account.positions)
                                + len(self.account.pending_limit_orders)
                            )
                            if (
                                effective_slot_limit > 0
                                and committed_slots >= effective_slot_limit
                            ):
                                signal_progress.append(f"{coin} {direction_text} 資格未通過,有效槽位已滿({effective_slot_limit})")
                                continue

                            opened = await self._place_structured_entry(
                                symbol,
                                sig,
                                sig["live_price"]
                            )
                            opened_any = bool(opened) or opened_any
                            if opened:
                                self._channel_outer_reentry_after_exit.pop(
                                    symbol, None,
                                )
                                self._channel_emergency_reentry_wait.pop(
                                    symbol, None,
                                )

                    refresh_needed = self._candidate_board_refresh_needed(
                        opened_any,
                        len(self.account.positions),
                        len(self.account.pending_limit_orders),
                        effective_slot_limit,
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
