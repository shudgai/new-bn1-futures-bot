import asyncio
import re
import time
import ccxt.async_support as ccxt
import pandas as pd
from typing import Dict, List
from core.config import (
    DEFAULT_SYMBOLS, MAX_SLOTS, TRADE_AMOUNT_USDT, TREND_FILTER_EMA_PERIOD,
    PULLBACK_TIMEOUT_MINUTES, ENTRY_LIMIT_TIMEOUT_SEC,
    PULLBACK_TARGET_MAX_DRIFT_ATR, PULLBACK_RECLAIM_MIN_ATR,
    PULLBACK_RETRY_COOLDOWN_SEC, get_pullback_target_depth,
    SYMBOL_ROTATION_INTERVAL_SEC,
    UNHEALTHY_SYMBOL_CHECK_INTERVAL_SEC,
    BINANCE_API_KEY, BINANCE_SECRET, get_position_multiplier, MIN_TRADE_USDT,
    MIN_SCORE_THRESHOLD, USE_TESTNET,
    ADX_QUALITY_MIN, ADX_DECLINE_LOOKBACK_BARS_1H, TEST_BUDGET_CAP_USDT,
    HISTORY_RECENCY_DECAY, ENTRY_FRESHNESS_SCORE_MAX, MIN_FRESHNESS_SCORE,
    ENTRY_DISABLED_SYMBOLS,
)
from core.strategy import (
    SuperTrendKeltnerStrategy, compute_sl_tp_distance, compute_pullback_target,
    detect_ma7_reversal,
)
from core.testnet_account import BinanceTestnetAccount
from core.symbol_rotation import SymbolRotation
from core.indicators import drop_unclosed_candle, compute_position_trigger

class TradingEngine:
    def __init__(self):
        # 真實市場公開行情與8006獨立Testnet執行帳戶分流。
        self.exchange = ccxt.binanceusdm({"enableRateLimit": True})
        self.execution_exchange = ccxt.binanceusdm({
            "apiKey": BINANCE_API_KEY,
            "secret": BINANCE_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        self.execution_exchange.set_sandbox_mode(USE_TESTNET)
        self.strategy = SuperTrendKeltnerStrategy()
        self.account = BinanceTestnetAccount(self.execution_exchange)
        self.symbol_rotation = SymbolRotation(self.account)
        self.is_running = False
        self.task: asyncio.Task = None
        self.rotation_task: asyncio.Task = None
        self.analysis_task: asyncio.Task = None
        self.analysis_event = asyncio.Event()
        self.account.on_trade_closed = self.request_trade_analysis
        self.tickers: Dict[str, float] = {}
        self.ticker_volumes: Dict[str, float] = {}  # 24小時成交量 (USDT)
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
        self.last_signal_progress_log_at: float = 0.0
        # 持倉手動平倉參考指標（跌破/站上關鍵均線、跌破前低/站上前高）：
        # 純粹給使用者按「平倉」前參考用，不是自動出場條件，不影響
        # 止損/止利/24h時間過濾等既有的自動平倉邏輯。
        self.position_triggers: Dict[str, dict] = {}
        self.trigger_task: asyncio.Task = None
        # 歷史係數降分 log 節流：同一個 symbol 在績效數據沒變的情況下，
        # 每輪主迴圈都會重算出同樣的係數/分數，導致同一則訊息每 5~10 秒
        # 就重複印一次（實測 ZEC/USDT 這樣連續洗了好幾分鐘）。只記錄狀態
        # 有變化時才印，同樣的狀態只顯示一次。
        self._history_coeff_logged: Dict[str, tuple] = {}
        # 診斷與影子比較每分鐘落盤一次，避免每 5 秒主迴圈造成過度寫檔。
        self._last_diagnostic_stats_save_at: float = 0.0

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
            match = re.search(r"Score\((\d+)\)", signal.get("reason", ""))
            score = int(match.group(1)) if match else 0
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
        if self.symbol_rotation.last_rotation_at <= 0:
            return
        if symbols_snapshot != list(DEFAULT_SYMBOLS):
            return
        if not entries or now_time - self.last_signal_progress_log_at < 60:
            return
        self.account.log(f"📊 [{len(symbols_snapshot)}幣訊號進度]\n" + "\n".join(f"• {entry}" for entry in entries), "INFO")
        self.last_signal_progress_log_at = now_time

    async def start(self):
        if self.is_running:
            return
        await self.account.initialize()
        self.is_running = True
        if USE_TESTNET:
            self.account.log(f"▶️ 8006 Binance Futures Testnet 機器人啟動（達標訊號全數回踩確認 / {len(DEFAULT_SYMBOLS)}幣雙向交易）")
        else:
            self.account.log(
                "🔴🔴🔴 正式實盤模式已啟用（USE_TESTNET=false）：本次啟動將用真實資金下單！",
                "DANGER",
            )
        self.task = asyncio.create_task(self._main_loop())
        # 幣種輪替（含 AI 呼叫，最壞情況耗時數十秒）獨立成背景任務，
        # 避免跟主迴圈共用同一個 await 鏈，卡住停損停利檢查。
        self.rotation_task = asyncio.create_task(self._rotation_loop())
        # 歷史分析是第三條完全獨立的工作，不等待主交易或幣種輪替。
        self.analysis_task = asyncio.create_task(self._analysis_loop())
        # 持倉平倉參考指標同樣獨立成背景任務，抓K線失敗/變慢不影響主迴圈。
        self.trigger_task = asyncio.create_task(self._position_trigger_loop())
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
        if self.trigger_task:
            self.trigger_task.cancel()
        await self.exchange.close()
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
                now_time = time.time()
                if now_time - self.symbol_rotation.last_rotation_at >= SYMBOL_ROTATION_INTERVAL_SEC:
                    changes = await self.symbol_rotation.rotate(self.exchange)
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

    async def _position_trigger_loop(self):
        """持倉手動平倉參考指標：用 EMA20（策略本身 Keltner 通道用的同一條
        基準線）跟近 20 根 5 分K的前低/前高，判斷「跌破均線」「跌破前低」
        （多單）或「站上均線」「站上前高」（空單）。純粹是給使用者按網頁
        「平倉」按鈕前參考用的視覺提示，不會觸發任何自動平倉，獨立成
        背景任務、抓K線失敗或變慢也不影響主迴圈的止損止利判斷。"""
        while self.is_running:
            try:
                for symbol, position in list(self.account.positions.items()):
                    df = await self.fetch_klines(symbol, timeframe="5m", limit=30)
                    trigger = compute_position_trigger(df, position.get("side"))
                    trigger["updated_at"] = time.time()
                    self.position_triggers[symbol] = trigger
                for symbol in set(self.position_triggers) - set(self.account.positions):
                    self.position_triggers.pop(symbol, None)
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.account.log(f"⚠️ [平倉參考指標] 暫時失敗：{type(exc).__name__}: {exc}", "WARNING")
                await asyncio.sleep(30)

    async def fetch_klines(self, symbol: str, timeframe: str = "5m", limit: int = 100) -> pd.DataFrame:
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            # 丟棄還沒收盤的最後一根 K 棒，只在這個共用入口做一次，
            # evaluate_signal/confirm_pullback_entry 等下游邏輯用 df.iloc[-1]
            # 時就天然拿到「最後一根已收盤」的資料，不用逐處修改。
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

        # 1m MA7 拐頭向上/向下確認
        closes = candles_1m["close"].astype(float)
        ma7 = closes.rolling(window=7).mean()
        if pd.isna(ma7.iloc[-1]) or pd.isna(ma7.iloc[-2]):
            return False
        ma7_curr = ma7.iloc[-1]
        ma7_prev = ma7.iloc[-2]

        target = float(candidate["target_price"])
        atr = max(float(candidate.get("atr") or 0.0), target * 1e-6)
        reclaim = atr * PULLBACK_RECLAIM_MIN_ATR
        open_price = float(candle["open"])
        close_price = float(candle["close"])
        if candidate["side"] == "LONG":
            return bool(
                close_price > open_price
                and close_price >= target + reclaim
                and ma7_curr > ma7_prev
            )
        return bool(
            close_price < open_price
            and close_price <= target - reclaim
            and ma7_curr < ma7_prev
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
            entry_mode = sig.get("entry_mode", "CURRENT_MAKER" if score >= 90 else "PULLBACK")
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
            base_amount = min(
                max(TRADE_AMOUNT_USDT * get_position_multiplier(score), MIN_TRADE_USDT),
                TRADE_AMOUNT_USDT,
            )
            allocation_factor = float(sig.get("btc_allocation_factor", 1.0) or 1.0)
            amount = base_amount * allocation_factor
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
                "amount_usdt": amount,
                "base_amount_usdt": base_amount,
                "btc_regime_mode": sig.get("btc_regime_mode", "UNKNOWN"),
                "btc_direction_1h": sig.get("btc_direction_1h", 0),
                "btc_score_penalty": sig.get("btc_score_penalty", 0),
                "btc_allocation_factor": allocation_factor,
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
        if candidate["side"] == "LONG":
            sl, tp = live_price - sl_distance, live_price + tp_distance
        else:
            sl, tp = live_price + sl_distance, live_price - tp_distance
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

    async def _place_ma7_reversal_entry(
        self, symbol: str, side: str, ma7_sig: dict, live_price: float, now: float
    ) -> bool:
        """MA7 谷底/峰頂拐頭確認後，以對手價（LONG 用 Ask, SHORT 用 Bid）送出限價單直接吃單成交。不進回踩候選池。"""
        committed = len(self.account.positions) + len(self.account.pending_limit_orders)
        if MAX_SLOTS > 0 and committed >= MAX_SLOTS:
            return False
        score = int(ma7_sig.get("score") or MIN_SCORE_THRESHOLD)
        base_amount = min(
            max(TRADE_AMOUNT_USDT * get_position_multiplier(score), MIN_TRADE_USDT),
            TRADE_AMOUNT_USDT,
        )
        allocation_factor = float(ma7_sig.get("btc_allocation_factor", 1.0) or 1.0)
        amount_usdt = base_amount * allocation_factor
        if self.account.get_available_balance() < amount_usdt:
            return False

        # 獲取對手價，確保能立刻成交
        target_price = live_price
        if hasattr(self.exchange, "fetch_order_book"):
            try:
                book = await self.exchange.fetch_order_book(symbol, limit=3)
                if side == "LONG" and book.get("asks") and len(book["asks"]) > 0:
                    target_price = float(book["asks"][0][0])
                elif side == "SHORT" and book.get("bids") and len(book["bids"]) > 0:
                    target_price = float(book["bids"][0][0])
            except Exception:
                pass

        atr = max(float(ma7_sig.get("atr") or 0.0), target_price * 1e-6)
        sl_distance, tp_distance = compute_sl_tp_distance(target_price, atr)
        if side == "LONG":
            sl, tp = target_price - sl_distance, target_price + tp_distance
        else:
            sl, tp = target_price + sl_distance, target_price - tp_distance

        placed = await self.account.place_limit_entry(
            symbol=symbol, side=side, target_price=target_price,
            amount_usdt=amount_usdt, sl=sl, tp=tp,
            reason=ma7_sig.get("reason", "MA7_Reversal_Entry"),
            atr=atr,
            leverage=self.symbol_rotation.get_dynamic_leverage(symbol, score),
            signal_score=score,
            post_only=False, # 不啟用 Post-Only，允許直接吃單
            entry_context={
                "entry_mode": "MA7_REVERSAL",
                "btc_regime_at_entry": ma7_sig.get("btc_regime_mode", "UNKNOWN"),
                "btc_allocation_factor": allocation_factor,
                "ma7_curr": ma7_sig.get("ma7_curr"),
                "ma7_prev": ma7_sig.get("ma7_prev"),
                "ma7_prev2": ma7_sig.get("ma7_prev2"),
            },
        )
        if placed:
            self._record_pullback_outcome("ma7_reversal_placed")
            direction_note = "MA7谷底轉彎向上" if side == "LONG" else "MA7峰頂轉彎向下"
            self.account.log(
                f"⚡ [MA7拐頭進場] {symbol} {side} {score}分 @ {target_price:.8g}（{direction_note}，對手價直接成交）",
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

            confirm_df = await self.fetch_klines(symbol, timeframe="5m", limit=100)
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
            if not self._pullback_reversal_confirmed(candidate_for_check, candles_1m):
                continue
            live_price = self.tickers.get(symbol, live_price)
            reclaimed = live_price >= fresh_target if candidate["side"] == "LONG" else live_price <= fresh_target
            if not reclaimed:
                self._drop_pullback_candidate(symbol, "反轉確認後又跌回/漲回目標錯側", now)
                continue
            committed = len(self.account.positions) + len(self.account.pending_limit_orders)
            if MAX_SLOTS > 0 and committed >= MAX_SLOTS:
                self._drop_pullback_candidate(symbol, "可用持倉槽位已滿", now, cooldown=False)
                continue
            if self.account.get_available_balance() < candidate["amount_usdt"]:
                self._drop_pullback_candidate(symbol, "可用保證金不足", now, cooldown=False)
                continue

            sl_distance, tp_distance = compute_sl_tp_distance(fresh_target, fresh_atr)
            if candidate["side"] == "LONG":
                sl, tp = fresh_target - sl_distance, fresh_target + tp_distance
            else:
                sl, tp = fresh_target + sl_distance, fresh_target - tp_distance
            placed = await self.account.place_limit_entry(
                symbol=symbol, side=candidate["side"], target_price=fresh_target,
                amount_usdt=candidate["amount_usdt"], sl=sl, tp=tp,
                reason=f"Pullback_Confirmed_Limit | {candidate['reason']}", atr=fresh_atr,
                leverage=candidate["leverage"], signal_score=candidate["score"],
                post_only=True, entry_context={
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
            if now - info["placed_at"] > ENTRY_LIMIT_TIMEOUT_SEC:
                self._record_pullback_outcome("maker_timeout")
                await self.account.cancel_pending_limit(
                    symbol, f"短效 Maker 掛單 {ENTRY_LIMIT_TIMEOUT_SEC:.0f} 秒未成交"
                )
                self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
                continue
            entry_mode = (info.get("entry_context") or {}).get("entry_mode")
            if entry_mode in ("CURRENT_MAKER", "MA7_REVERSAL"):
                # 90+ 現價單與 MA7 拐頭現價單只短暫存活 15 秒，不套用回踩二次確認與目標漂移。
                # 每日熔斷、幣種停用及掛單逾時仍在上方保留。
                continue
            confirm_df = await self.fetch_klines(symbol, timeframe="5m", limit=100)
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
                self.account.save_state()

    async def _main_loop(self):
        while self.is_running:
            try:
                # 1. 更新實時價格
                await self.update_market_prices()

                # 幣種輪替已移到獨立的 _rotation_loop() 背景任務執行，
                # 不再佔用這個迴圈的 await 鏈，停損停利不會被 AI 呼叫延遲。

                # 2. 更新與執行持倉部位
                await self.account.update_positions(self.tickers)
                # 冷卻時間唯一資料來源是 self.account.last_closed_at（見
                # testnet_account.py），不管平倉是這裡的主迴圈觸發，還是
                # /api/prices、/api/status 這些跟主迴圈不同步的網頁輪詢
                # 呼叫觸發，都會準確記錄，不會像原本這裡自己拿前後快照
                # 判斷那樣，漏掉別的呼叫者觸發的平倉。

                # 3. 10分鐘定時刷新 1h EMA200 快取 (防止 API Rate Limit 封鎖)
                await self.update_1h_trend_cache()

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
                if not daily_halt and available_balance >= MIN_TRADE_USDT:
                    signal_progress = []

                    now_time = time.time()
                    # 幣種輪替現在跑在獨立背景任務，可能在這個迴圈 await 期間改動 DEFAULT_SYMBOLS，
                    # 用 list(...) 先拍一份快照，避免邊跑邊被換牌造成跳過或重複掃描。
                    symbols_snapshot = list(DEFAULT_SYMBOLS)
                    for symbol in symbols_snapshot:
                        direction_text = "雙向"
                        coin = symbol.replace("/USDT", "")
                        if symbol in self.account.positions:
                            position = self.account.positions[symbol]
                            position_score = position.get("signal_score") or 0
                            position_direction = "多單" if position.get("side") == "LONG" else "空單"
                            signal_progress.append(
                                f"{coin} {position_direction} {position_score}分,持倉中"
                            )
                            continue

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
                            continue

                        # 如果已經掛著限價單，跳過訊號偵測（避免重複掛單）
                        if symbol in self.account.pending_limit_orders:
                            pending = self.account.pending_limit_orders[symbol]
                            entry_context = pending.get("entry_context") or {}
                            pullback_score = entry_context.get("pullback_confirmation_score")
                            confirmation_reason = (
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
                            continue

                        retry_after = self._pullback_retry_after.get(symbol, 0.0)
                        if retry_after > now_time:
                            signal_progress.append(
                                f"{coin} {direction_text} 資格未通過,回踩失效冷卻{int(retry_after - now_time)}秒"
                            )
                            self._record_entry_filter(
                                symbol, {"action": "HOLD", "reason": "回踩失效冷卻"},
                                direction_text, "pullback_retry_cooldown",
                            )
                            continue

                        # 冷卻時間檢查 (剛平倉 15 分鐘內禁止重複進場)

                        if symbol in ENTRY_DISABLED_SYMBOLS:
                            signal_progress.append(
                                f"{coin} {direction_text} 資格未通過,暫停新倉"
                            )
                            self._record_entry_filter(
                                symbol, {"action": "HOLD", "reason": "暫停新倉"},
                                direction_text, "symbol_disabled",
                            )
                            continue
                        last_closed = self.account.last_closed_at.get(symbol)
                        if last_closed is not None and (now_time - last_closed) < 900:
                            remaining = max(0, int((900 - (now_time - last_closed)) / 60) + 1)
                            signal_progress.append(
                                f"{coin} {direction_text} 資格未通過,冷卻剩{remaining}分鐘"
                            )
                            self._record_entry_filter(
                                symbol, {"action": "HOLD", "reason": "平倉冷卻"},
                                direction_text, "post_close_cooldown",
                            )
                            continue

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
                            continue

                        df = await self.fetch_klines(symbol, timeframe="5m", limit=100)
                        if df.empty or len(df) < 50:
                            signal_progress.append(
                                f"{coin} {direction_text} 資格未通過,K線資料不足"
                            )
                            self._record_entry_filter(
                                symbol, {"action": "HOLD", "reason": "K線資料不足",
                                         "diagnostics": {"bars": int(len(df))}},
                                direction_text, "kline_data_short",
                            )
                            continue

                        # 取出 1h 快取值
                        ema_50_1h = self.ema_50_1h_cache.get(symbol)

                        # 防插針價格選擇 (SpikeFilter_L2)
                        if 'close_price_spike_filtered' in df.columns and not pd.isna(df.iloc[-1]['close_price_spike_filtered']):
                            price = float(df.iloc[-1]['close_price_spike_filtered'])
                        else:
                            price = float(df.iloc[-1]['close'])

                        self.tickers[symbol] = price

                        # 4.2 計算真實動態 ATR (非固定 1.5%)
                        high = df['high']
                        low = df['low']
                        close = df['close']
                        tr1 = high - low
                        tr2 = (high - close.shift(1)).abs()
                        tr3 = (low - close.shift(1)).abs()
                        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                        real_atr = tr.rolling(window=10).mean().iloc[-1]
                        if pd.isna(real_atr) or real_atr <= 0:
                            real_atr = price * 0.015

                        # 4.3 防插針檢查 (5x 真實 ATR)
                        recent_high = df.iloc[-1]['high']
                        recent_low = df.iloc[-1]['low']
                        candle_spread = recent_high - recent_low

                        if candle_spread > (real_atr * 5.0):
                            self.account.log(f"🛡️ [防插針觸發] {symbol} 最新 K 線振幅過大 ({candle_spread:.4f} > 5x 真實ATR)，過濾潛在假突破訊號", "WARNING")
                            signal_progress.append(
                                f"{coin} {direction_text} 資格未通過,防插針過濾"
                            )
                            self._record_entry_filter(
                                symbol, {"action": "HOLD", "reason": "防插針過濾",
                                         "diagnostics": {"candle_spread_atr": float(candle_spread / real_atr)}},
                                direction_text, "spike_filter",
                            )
                            continue

                        # 計算指標以取得 rsi 與 kc 通道等欄位
                        df = self.strategy.compute_indicators(df)

                        # MA7 拐頭主觸發路徑：符合直接以現價進場，其餘狀態均視為 HOLD。
                        current_direction = (
                            "LONG" if int(df.iloc[-1]["st_direction"]) == 1 else "SHORT"
                        )
                        ma7_sig = detect_ma7_reversal(
                            df,
                            side=current_direction,
                            ema_50_1h=self.ema_50_1h_cache.get(symbol),
                            st_direction_1h=self.st_direction_1h_cache.get(symbol),
                            btc_st_direction_1h=self.btc_1h_st_direction,
                            btc_st_flip_age=self.btc_1h_st_flip_age,
                            symbol=symbol,
                            indicators_precomputed=True,
                        )
                        
                        if ma7_sig["detected"]:
                            daily_halt_now, _ = self.account.daily_loss_limit_hit()
                            if not daily_halt_now:
                                ma7_placed = await self._place_ma7_reversal_entry(
                                    symbol, ma7_sig["side"], ma7_sig, price, now_time
                                )
                                if ma7_placed:
                                    signal_progress.append(
                                        f"{symbol.replace('/USDT', '')} {current_direction} "
                                        f"{ma7_sig.get('score', 65)}分,MA7拐頭進場"
                                    )
                                    continue
                        
                        # 未觸發拐頭進場，日誌進度顯示 HOLD 理由
                        reason = ma7_sig.get("reason", "等待MA7拐頭及KC回踩")
                        score = ma7_sig.get("score", 0)
                        
                        # 模擬 evaluate_signal 回傳結構，以正確記錄日誌和影子指標
                        holding_sig = {
                            "action": "HOLD",
                            "score": score,
                            "reason": reason,
                            "eligible": False,
                            "diagnostics": {
                                "st_direction_5m": current_direction,
                                "st_direction_1h": self.st_direction_1h_cache.get(symbol),
                                "price": price,
                                "ema_50_1h": ema_50_1h,
                                "adx": float(df.iloc[-1].get("adx") or 0.0),
                                "rsi": float(df.iloc[-1].get("rsi") or 50.0),
                            }
                        }
                        self._record_shadow_parameter_comparison(symbol, df, holding_sig, current_direction)
                        self._record_entry_filter(symbol, holding_sig, current_direction)
                        
                        direction_text = {"LONG": "多單", "SHORT": "空單"}.get(current_direction, "雙向")
                        coin = symbol.replace("/USDT", "")
                        
                        # 簡化日誌進度顯示
                        if "SuperTrend方向不符" in reason:
                            stage = "SuperTrend方向不符"
                        elif "1h_ST" in reason:
                            stage = "個幣1h趨勢不符"
                        elif "1h_EMA50" in reason:
                            stage = "1h EMA50方向不符"
                        elif "ADX" in reason:
                            stage = "ADX過低過濾"
                        elif "ATR" in reason:
                            stage = "波動過濾"
                        elif "RSI" in reason:
                            stage = "RSI方向不合"
                        elif "MA7" in reason:
                            stage = "等待MA7拐頭轉彎"
                        elif "KC" in reason or "K棒未曾觸碰" in reason:
                            stage = "待碰觸KC軌道回踩確認"
                        else:
                            stage = "條件未完成"
                            
                        signal_progress.append(f"{coin} {direction_text} {score}分,{stage}")

                    self._log_signal_progress(signal_progress, now_time, symbols_snapshot)
                    if now_time - self._last_diagnostic_stats_save_at >= 60.0:
                        self.account.save_state()
                        self._last_diagnostic_stats_save_at = now_time

                await asyncio.sleep(5)
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

