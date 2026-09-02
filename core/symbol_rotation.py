import asyncio
import json
import math
import os
import time
from collections import defaultdict
from typing import Dict, Iterable, List

import pandas as pd

from core.ai_advisor import LocalAIAdvisor
from core.trade_history_analysis import TradeHistoryAnalyzer
from core.strategy import SuperTrendKeltnerStrategy
from core.indicators import drop_unclosed_candle
from core.config import (
    AI_ADVISOR_ENABLED,
    AI_ADVISOR_TIMEOUT_SEC,
    AI_ADVISOR_URL,
    AI_ADVISOR_WEIGHT,
    DEFAULT_SYMBOLS,
    DIRECTIONAL_MIN_SCORE,
    DIRECTIONAL_SIDE_COUNT,
    SYMBOL_CANDIDATE_POOL,
    SYMBOL_MARKET_SCAN_LIMIT,
    SYMBOL_MIN_QUOTE_VOLUME,
    SYMBOL_ROTATION_COUNT,
    SYMBOL_ROTATION_MIN_SCORE_GAP,
    SYMBOL_ROTATION_MAX_CHANGES,
    SYMBOL_MIN_LISTING_DAYS,
    SYMBOL_MAX_24H_CHANGE_PCT,
    SYMBOL_MAX_FUNDING_RATE,
    VOLATILITY_ROTATION_WEIGHT,
    SYMBOL_MAX_ADX_RANGE,
    SYMBOL_MIN_KC_WIDTH_PCT,
    SYMBOL_HISTORY_QUARANTINE_MIN_TRADES,
    SYMBOL_HISTORY_QUARANTINE_MAX_AVG_PNL,
    SYMBOL_HISTORY_QUARANTINE_MAX_STOP_RATE,
    EXPLORATION_MIN_DIRECTION_TRADES,
    EXPLORATION_POSITION_SIZE_MULTIPLIER,
    CONSECUTIVE_STOP_COOLDOWN_COUNT,
    CONSECUTIVE_STOP_COOLDOWN_SEC,
    ENTRY_DISABLED_SYMBOLS,
    TREND_FILTER_EMA_PERIOD,
    RAPID_MOVE_WINDOW,
    RAPID_MOVE_THRESHOLD,
    MIN_ATR_PCT,
    MAX_ATR_PCT,
    get_atr_based_leverage,
    get_signal_leverage,
    SIGNAL_LEVERAGE_CAPS,
    MAINSTREAM_SYMBOLS,
    WEAK_ENERGY_ADX_THRESHOLD,
    WEAK_ENERGY_LEVERAGE_CAP,
    KELTNER_MIN_VOLUME_RATIO,
)


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SELECTION_FILE = os.path.join(DATA_DIR, "symbol_selection.json")

# Keep a just-profited symbol off the board briefly while the bot finds a new setup.
PROFIT_EXIT_SYMBOL_COOLDOWN_SEC = float(
    os.getenv("PROFIT_EXIT_SYMBOL_COOLDOWN_SEC", "1800")
)
RESCAN_SYMBOL_COOLDOWN_SEC = float(
    os.getenv("RESCAN_SYMBOL_COOLDOWN_SEC", "300")
)
MEME_SCAN_RESERVE = max(0, int(os.getenv("MEME_SCAN_RESERVE", "10")))
MEME_MIN_VOLUME_FACTOR = min(1.0, max(0.05, float(
    os.getenv("MEME_MIN_VOLUME_FACTOR", "0.25")
)))
MEME_BASES = frozenset({
    "DOGE", "1000SHIB", "SHIB", "1000PEPE", "PEPE", "WIF",
    "1000BONK", "BONK", "1000FLOKI", "FLOKI", "MEME", "DOGS",
    "BRETT", "POPCAT", "PNUT", "PENGU", "TRUMP", "FARTCOIN",
    "PUMP", "NEIRO", "1000SATS", "SATS",
})


def _trade_ts(trade: dict) -> float:
    """從交易記錄中取出 Unix 時間戳（秒）。
    支援 timestamp（秒）和 id（毫秒）兩種欄位，找不到時回傳 0.0。
    """
    ts = trade.get("timestamp")
    if isinstance(ts, (int, float)) and ts > 0:
        return float(ts)
    trade_id = trade.get("id")
    if isinstance(trade_id, (int, float)) and trade_id > 0:
        return float(trade_id) / 1000.0
    return 0.0



class SymbolRotation:
    def __init__(self, account):
        self.account = account
        self.ai = LocalAIAdvisor(
            AI_ADVISOR_URL,
            enabled=AI_ADVISOR_ENABLED,
            timeout=AI_ADVISOR_TIMEOUT_SEC,
        )
        self.trade_analysis = TradeHistoryAnalyzer(self.ai)
        self.strategy = SuperTrendKeltnerStrategy()
        self.last_rotation_at = 0.0
        self.last_changes: List[dict] = []
        self.last_metrics: List[dict] = []
        self.direction_map: Dict[str, str] = {}
        self.fallback_symbols = list(DEFAULT_SYMBOLS)
        self.last_reason = "尚未執行"
        self.volatility_stats: Dict[str, dict] = {}
        self.atr_history: Dict[str, List[float]] = {}
        # Pending forces a fresh scan; cooldown prevents immediate reselection.
        self.next_rotation_exclusions: set[str] = set()
        self.replacement_cooldowns: Dict[str, float] = {}
        # Confirmed outer candidates stay on the two-symbol board until the
        # immediately-adjacent confirmation candle resolves.
        self.setup_protected_symbols: set[str] = set()
        self._restore_profit_exit_cooldowns()

    def set_setup_protected_symbols(self, symbols: Iterable[str]) -> None:
        self.setup_protected_symbols = {
            str(symbol).strip() for symbol in symbols if str(symbol).strip()
        }

    def _restore_profit_exit_cooldowns(self) -> None:
        now_time = time.time()
        for trade in getattr(self.account, "trades", []):
            reason = str(trade.get("reason") or "")
            if not str(trade.get("action") or "").startswith("CLOSE"):
                continue
            if not any(token in reason for token in (
                "KC_UPPER_RED_REENTRY_EXIT",
                "KC_LOWER_GREEN_REENTRY_EXIT",
            )):
                continue
            symbol = str(trade.get("symbol") or "").strip()
            cooldown_until = _trade_ts(trade) + PROFIT_EXIT_SYMBOL_COOLDOWN_SEC
            if symbol and cooldown_until > now_time:
                self.replacement_cooldowns[symbol] = cooldown_until
                self.next_rotation_exclusions.add(symbol)

    def request_replacement(self, symbol: str) -> None:
        symbol = str(symbol or "").strip()
        if not symbol:
            return
        self.next_rotation_exclusions.add(symbol)
        cooldowns = getattr(self, "replacement_cooldowns", None)
        if cooldowns is None:
            cooldowns = {}
            self.replacement_cooldowns = cooldowns
        cooldowns[symbol] = time.time() + PROFIT_EXIT_SYMBOL_COOLDOWN_SEC
        self.last_rotation_at = 0.0

    def request_rescan(self, symbols: Iterable[str]) -> None:
        """Force the next scan to use a different, one-cycle candidate board."""
        protected = set(getattr(self.account, "positions", {}).keys())
        protected.update(
            getattr(self.account, "pending_limit_orders", {}).keys()
        )
        rescanned = {
            str(symbol).strip() for symbol in symbols
            if str(symbol).strip() and str(symbol).strip() not in protected
        }
        self.next_rotation_exclusions.update(rescanned)
        cooldown_until = time.time() + RESCAN_SYMBOL_COOLDOWN_SEC
        for symbol in rescanned:
            self.replacement_cooldowns[symbol] = max(
                self.replacement_cooldowns.get(symbol, 0.0), cooldown_until,
            )
        self.last_rotation_at = 0.0

    def replacement_exclusions(self) -> set[str]:
        now_time = time.time()
        cooldowns = getattr(self, "replacement_cooldowns", None)
        if cooldowns is None:
            cooldowns = {}
            self.replacement_cooldowns = cooldowns
        expired = [symbol for symbol, until in cooldowns.items() if until <= now_time]
        for symbol in expired:
            cooldowns.pop(symbol, None)
        return set(getattr(self, "next_rotation_exclusions", set())) | set(cooldowns)



    @staticmethod
    def _closed_trade_stats(trades: Iterable[dict]) -> Dict[str, dict]:
        by_symbol = defaultdict(list)
        for trade in trades:
            if (
                str(trade.get("action", "")).startswith("CLOSE")
                and isinstance(trade.get("pnl"), (int, float))
            ):
                by_symbol[trade.get("symbol", "")].append(trade)

        stats = {}
        for symbol, rows in by_symbol.items():
            recent = rows[:20]
            count = len(recent)
            pnl_values = [float(row["pnl"]) for row in recent]
            stats[symbol] = {
                "trades": count,
                "avg_pnl": sum(pnl_values) / count,
                "win_rate": sum(value > 0 for value in pnl_values) / count,
                "stop_rate": sum(
                    "Stop-Loss" in str(row.get("reason", "")) for row in recent
                ) / count,
            }
        return stats

    @staticmethod
    def _closed_trade_direction_stats(trades: Iterable[dict]) -> Dict[tuple, dict]:
        grouped = defaultdict(list)
        for trade in trades:
            if str(trade.get("action", "")).startswith("CLOSE"):
                grouped[(trade.get("symbol", ""), trade.get("side", ""))].append(trade)
        stats = {}
        for key, rows in grouped.items():
            recent = rows[:20]
            pnls = [float(row.get("pnl") or 0.0) for row in recent]
            count = len(pnls)
            stats[key] = {
                "trades": count,
                "avg_pnl": sum(pnls) / count,
                "win_rate": sum(value > 0 for value in pnls) / count,
                "stop_rate": sum("Stop-Loss" in str(row.get("reason", "")) for row in recent) / count,
            }
        return stats

    @staticmethod
    def _normalize_tickers(tickers: dict) -> Dict[str, dict]:
        normalized = {}
        for raw_symbol, ticker in tickers.items():
            symbol = raw_symbol.replace(":USDT", "")
            normalized[symbol] = ticker
        return normalized


    @staticmethod
    def _history_quarantined(stat: dict) -> bool:
        return (
            stat.get("trades", 0) >= SYMBOL_HISTORY_QUARANTINE_MIN_TRADES
            and (
                stat.get("avg_pnl", 0.0) <= SYMBOL_HISTORY_QUARANTINE_MAX_AVG_PNL
                or stat.get("stop_rate", 0.0) >= SYMBOL_HISTORY_QUARANTINE_MAX_STOP_RATE
            )
        )


    @staticmethod
    def _direction_is_eligible(
        trend_aligned: bool, st_5m_aligned: bool, st_1h_aligned: bool, atr_pct: float,
        volatility_excluded: bool, history_quarantined: bool,
    ) -> bool:
        # 輪替名單保留 1h ST 與 ATR 健康門檻；5m ST、實際峰谷與其他進場條件
        # 留給交易掃描階段判斷，避免尚未轉向的可交易幣過早移出牌面。
        return (
            st_1h_aligned
            and MIN_ATR_PCT <= atr_pct <= MAX_ATR_PCT
            and not volatility_excluded
        )

    @staticmethod
    def _kc_entry_setup(
        price: float, kc_upper: float, kc_lower: float, atr: float, direction: str,
    ) -> dict:
        """Rank KC geography by how soon it can become an entry."""
        values = (price, kc_upper, kc_lower, atr)
        if (
            not all(math.isfinite(float(value)) for value in values)
            or atr <= 0.0
            or kc_upper <= kc_lower
        ):
            return {"priority": 0, "score": 0.0, "distance_atr": math.inf}

        side = str(direction or "").upper()
        if side == "LONG":
            if kc_upper <= price <= kc_upper + atr * 0.8:
                distance = (price - kc_upper) / atr
                return {"priority": 3, "score": 1.0 - (distance / 0.8) * 0.05, "distance_atr": distance}
            if kc_lower - atr * 0.8 <= price <= kc_lower + atr * 0.5:
                distance = abs(price - kc_lower) / atr
                return {"priority": 2, "score": 0.66 - min(distance, 1.0) * 0.05, "distance_atr": distance}
            if kc_upper - atr * 1.5 <= price < kc_upper:
                distance = (kc_upper - price) / atr
                return {"priority": 1, "score": 0.33 - min(distance / 1.5, 1.0) * 0.05, "distance_atr": distance}
        elif side == "SHORT":
            if kc_lower - atr * 0.8 <= price <= kc_lower:
                distance = (kc_lower - price) / atr
                return {"priority": 3, "score": 1.0 - (distance / 0.8) * 0.05, "distance_atr": distance}
            if kc_upper - atr * 0.5 <= price <= kc_upper + atr * 0.8:
                distance = abs(price - kc_upper) / atr
                return {"priority": 2, "score": 0.66 - min(distance, 1.0) * 0.05, "distance_atr": distance}
            if kc_lower < price <= kc_lower + atr * 1.5:
                distance = (price - kc_lower) / atr
                return {"priority": 1, "score": 0.33 - min(distance / 1.5, 1.0) * 0.05, "distance_atr": distance}
        return {"priority": 0, "score": 0.0, "distance_atr": math.inf}

    def get_history_allocation_factor(self, symbol: str, side: str) -> float:
        """保留探索機會，同時限制樣本不足或負期望方向的試錯成本。"""
        stat = self._closed_trade_direction_stats(self.account.trades).get(
            (symbol, side), {"trades": 0, "avg_pnl": 0.0, "stop_rate": 0.0}
        )
        exploratory = (
            stat["trades"] < EXPLORATION_MIN_DIRECTION_TRADES
            or stat["avg_pnl"] <= SYMBOL_HISTORY_QUARANTINE_MAX_AVG_PNL
            or stat["stop_rate"] >= SYMBOL_HISTORY_QUARANTINE_MAX_STOP_RATE
        )
        return EXPLORATION_POSITION_SIZE_MULTIPLIER if exploratory else 1.0


    def get_stop_cooldown_remaining(
        self, symbol: str, side: str, now: float = None,
    ) -> float:
        """同幣同方向連續硬停損後的剩餘冷卻秒數。

        只計算最近 CONSECUTIVE_STOP_COOLDOWN_SEC * 2 秒窗口內的止損，
        避免很久以前的舊止損一直被算進 streak，封鎖久遠後的同方向進場。
        """
        if CONSECUTIVE_STOP_COOLDOWN_SEC <= 0:
            return 0.0
        _now = float(time.time() if now is None else now)
        # 時間窗口：只看最近 max(冷卻時間*2, 24小時) 內的交易記錄
        lookback_window = max(CONSECUTIVE_STOP_COOLDOWN_SEC * 2, 86400.0)
        closed = [
            trade for trade in self.account.trades
            if trade.get("symbol") == symbol
            and str(trade.get("side", "")).upper() == str(side or "").upper()
            and str(trade.get("action", "")).startswith("CLOSE")
            and _trade_ts(trade) >= _now - lookback_window
        ]
        streak = 0
        latest_stop_at = 0.0
        for trade in closed:
            reason = str(trade.get("reason") or "")
            is_hard_stop = "Stop-Loss" in reason or (
                "止損" in reason and "移動" not in reason
            )
            if not is_hard_stop:
                break
            streak += 1
            if latest_stop_at <= 0:
                latest_stop_at = _trade_ts(trade)
        if streak < CONSECUTIVE_STOP_COOLDOWN_COUNT or latest_stop_at <= 0:
            return 0.0
        elapsed = max(0.0, _now - latest_stop_at)
        return max(0.0, CONSECUTIVE_STOP_COOLDOWN_SEC - elapsed)



    @staticmethod
    def _is_meme_symbol(symbol: str) -> bool:
        base = str(symbol or "").split("/", 1)[0].upper()
        return base in MEME_BASES

    @staticmethod
    def market_candidates(
        tickers: dict, markets: dict = None, execution_symbols: set = None
    ) -> List[str]:
        normalized = SymbolRotation._normalize_tickers(tickers)
        allowed_crypto = None
        if markets:
            now_ms = time.time() * 1000
            min_listing_ms = SYMBOL_MIN_LISTING_DAYS * 24 * 60 * 60 * 1000
            allowed_crypto = set()
            for market in markets.values():
                if (
                    market.get("active")
                    and market.get("swap")
                    and market.get("quote") == "USDT"
                    and market.get("info", {}).get("contractType") == "PERPETUAL"
                    and market.get("info", {}).get("underlyingType") == "COIN"
                ):
                    info = market.get("info", {})
                    if "monitoring" in info.get("tags", []):
                        continue
                    onboard = info.get("onboardDate") or info.get("deliveryDate")
                    if onboard and now_ms - int(onboard) < min_listing_ms:
                        continue
                    allowed_crypto.add(market["symbol"].replace(":USDT", ""))

        excluded_bases = {
            "BTC", "ETH", "BNB", "APT", "FET", "TAO",
            "USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE",
            "USD1", "BUSD", "USTC",
        }
        normal_ranked = []
        meme_ranked = []
        meme_min_volume = SYMBOL_MIN_QUOTE_VOLUME * MEME_MIN_VOLUME_FACTOR
        for symbol, ticker in normalized.items():
            if not symbol.endswith("/USDT") or symbol in ENTRY_DISABLED_SYMBOLS:
                continue
            if execution_symbols is not None and symbol not in execution_symbols:
                continue
            if allowed_crypto is not None and symbol not in allowed_crypto:
                continue
            base = symbol.split("/", 1)[0].upper()
            quote_volume = float(ticker.get("quoteVolume") or 0.0)
            change_pct = abs(float(ticker.get("percentage") or 0.0))
            if (
                base in excluded_bases
                or base.endswith(("UP", "DOWN", "BULL", "BEAR"))
                or change_pct > SYMBOL_MAX_24H_CHANGE_PCT
            ):
                continue
            if quote_volume >= SYMBOL_MIN_QUOTE_VOLUME:
                normal_ranked.append((quote_volume, symbol))
            if (
                SymbolRotation._is_meme_symbol(symbol)
                and quote_volume >= meme_min_volume
            ):
                meme_ranked.append((quote_volume, symbol))

        normal_ranked.sort(reverse=True)
        meme_ranked.sort(reverse=True)
        reserve = min(MEME_SCAN_RESERVE, SYMBOL_MARKET_SCAN_LIMIT)
        selected = [
            symbol for _, symbol in normal_ranked[:max(0, SYMBOL_MARKET_SCAN_LIMIT - reserve)]
        ]
        for _, symbol in meme_ranked:
            if symbol not in selected:
                selected.append(symbol)
            if sum(SymbolRotation._is_meme_symbol(item) for item in selected) >= reserve:
                break
        for _, symbol in normal_ranked:
            if len(selected) >= SYMBOL_MARKET_SCAN_LIMIT:
                break
            if symbol not in selected:
                selected.append(symbol)
        return selected[:SYMBOL_MARKET_SCAN_LIMIT]

    def build_metrics(self, tickers: dict, candidates: List[str] = None) -> List[dict]:
        candidates = candidates or SYMBOL_CANDIDATE_POOL
        normalized = self._normalize_tickers(tickers)
        history = self._closed_trade_stats(self.account.trades)
        volumes = []
        for symbol in candidates:
            ticker = normalized.get(symbol, {})
            volume = float(ticker.get("quoteVolume") or 0.0)
            volumes.append(math.log10(max(volume, 1.0)))
        low = min(volumes) if volumes else 0.0
        high = max(volumes) if volumes else 1.0
        spread = max(high - low, 1e-9)

        metrics = []
        for symbol, log_volume in zip(candidates, volumes):
            ticker = normalized.get(symbol, {})
            quote_volume = float(ticker.get("quoteVolume") or 0.0)
            change_pct = float(ticker.get("percentage") or 0.0)
            stat = history.get(
                symbol,
                {"trades": 0, "avg_pnl": 0.0, "win_rate": 0.5, "stop_rate": 0.0},
            )

            liquidity_score = (log_volume - low) / spread
            # 策略需要波動，但過熱行情容易追高；約 1%~5% 的 24h 變動給較高分。
            movement = abs(change_pct)
            movement_score = max(0.0, 1.0 - abs(movement - 3.0) / 7.0)
            market_score = liquidity_score * 0.75 + movement_score * 0.25

            if stat["trades"] >= 3:
                pnl_score = (math.tanh(stat["avg_pnl"] / 1.5) + 1.0) / 2.0
                performance_score = (
                    pnl_score * 0.50
                    + stat["win_rate"] * 0.30
                    + (1.0 - stat["stop_rate"]) * 0.20
                )
                quant_score = performance_score * 0.55 + market_score * 0.45
            else:
                # 樣本不足時不假裝有績效優勢，以流動性為主並給中性歷史分。
                quant_score = 0.50 * 0.45 + market_score * 0.55

            metrics.append(
                {
                    "symbol": symbol,
                    "quant_score": quant_score,
                    "trades": stat["trades"],
                    "avg_pnl": stat["avg_pnl"],
                    "win_rate": stat["win_rate"],
                    "stop_rate": stat["stop_rate"],
                    "quote_volume": quote_volume,
                    "change_pct": change_pct,
                }
            )
        return metrics

    async def build_directional_metrics(
        self, exchange, tickers: dict, candidates: List[str] = None
    ) -> List[dict]:
        """以 Binance 即時 5m/1h 資料分別評估 LONG 與 SHORT。"""
        candidates = candidates or SYMBOL_CANDIDATE_POOL
        normalized = self._normalize_tickers(tickers)
        history = self._closed_trade_direction_stats(self.account.trades)
        symbol_history = self._closed_trade_stats(self.account.trades)
        log_volumes = {
            symbol: math.log10(max(float(normalized.get(symbol, {}).get("quoteVolume") or 0.0), 1.0))
            for symbol in candidates
        }
        low = min(log_volumes.values()) if log_volumes else 0.0
        high = max(log_volumes.values()) if log_volumes else 1.0
        spread = max(high - low, 1e-9)
        results = []

        for symbol in candidates:
            if symbol in ENTRY_DISABLED_SYMBOLS:
                continue
            try:
                raw_5m = await exchange.fetch_ohlcv(symbol, timeframe="3m", limit=100)
                raw_1h = await exchange.fetch_ohlcv(symbol, timeframe="1h", limit=200)
                columns = ["timestamp", "open", "high", "low", "close", "volume"]
                df = drop_unclosed_candle(pd.DataFrame(raw_5m, columns=columns), "3m")
                df_1h = drop_unclosed_candle(pd.DataFrame(raw_1h, columns=columns), "1h")
                if len(df) < 50 or len(df_1h) < 30:
                    continue
                # 上市天數已在 market_candidates() 依 Binance onboardDate 過濾。
                # 這裡只抓 200 根 1h K（約 8 天），不可再用首根 K 誤判上市天數。

                computed = self.strategy.compute_indicators(df)
                computed_1h = self.strategy.compute_indicators(df_1h)
                curr = computed.iloc[-1]
                st_direction_1h = int(computed_1h.iloc[-1]["st_direction"])
                ticker = normalized.get(symbol, {})
                price = float(ticker.get("last") or curr["close"])

                # 近期方向加速度供迷因突發偵測使用；已超過急漲跌上限仍淘汰，避免追尾。
                recent_change_pct = 0.0
                if len(df) >= RAPID_MOVE_WINDOW + 1:
                    recent_close = float(df.iloc[-1]["close"])
                    past_close = float(df.iloc[-(RAPID_MOVE_WINDOW + 1)]["close"])
                    if past_close > 0:
                        recent_change_pct = (recent_close - past_close) / past_close * 100.0
                        if abs(recent_change_pct) > RAPID_MOVE_THRESHOLD:
                            continue

                atr = max(float(curr["atr"]), price * 0.0001)
                ema_1h = float(
                    df_1h["close"]
                    .ewm(span=min(len(df_1h), TREND_FILTER_EMA_PERIOD), adjust=False)
                    .mean()
                    .iloc[-1]
                )
                st_direction = int(curr["st_direction"])
                rsi = float(curr["rsi"])
                adx = float(curr["adx"]) if "adx" in curr else 0.0
                kc_middle = float(curr["kc_middle"]) if "kc_middle" in curr else price
                kc_width_pct = (float(curr["kc_upper"]) - float(curr["kc_lower"])) / kc_middle if kc_middle > 0 else 0.0
                vol_ma = float(curr["vol_ma_20"]) if not pd.isna(curr["vol_ma_20"]) else 0.0
                volume_ratio = float(curr["volume"]) / vol_ma if vol_ma > 0 else 0.0
                is_meme = self._is_meme_symbol(symbol)
                meme_burst_energy = bool(
                    is_meme
                    and volume_ratio >= max(KELTNER_MIN_VOLUME_RATIO, 1.5)
                    and 0.35 <= abs(recent_change_pct) <= RAPID_MOVE_THRESHOLD
                )
                liquidity = (log_volumes[symbol] - low) / spread
                quote_volume = float(ticker.get("quoteVolume") or 0.0)
                change_pct = float(ticker.get("percentage") or 0.0)

                # 波動率統計：用同一批已抓的 K 線算，不額外呼叫 API，純粹是
                # 幣種本身的市場價格，跟我們的持倉損益完全無關。
                # avg_daily_up_pct/avg_daily_down_pct 用 1h K 線（已抓 200 小時
                # ≈ 8~9 天）按 UTC 日期分組，算「當天開盤到最高/最低的漲跌幅」
                # 再取平均——這樣才是「這個幣一天大概能漲跌多少%」，而不是
                # 單根 5 分鐘 K 棒本身的漲跌（那個尺度太小，看不出真正空間）。
                atr_pct = atr / price if price > 0 else 0.0
                daily_up_pct, daily_down_pct = 0.0, 0.0
                df_daily = df_1h.copy()
                df_daily["date"] = pd.to_datetime(df_daily["timestamp"], unit="ms", utc=True).dt.date
                daily_group = df_daily.groupby("date").agg(
                    open=("open", "first"), high=("high", "max"), low=("low", "min")
                )
                daily_group = daily_group[daily_group["open"] > 0]
                if len(daily_group):
                    daily_up_series = (daily_group["high"] - daily_group["open"]) / daily_group["open"] * 100.0
                    daily_down_series = (daily_group["open"] - daily_group["low"]) / daily_group["open"] * 100.0
                    daily_up_pct = round(daily_up_series.mean(), 3)
                    daily_down_pct = round(daily_down_series.mean(), 3)
                # 硬性排除：波動率不是只看這一次的快照就下結論（單次量測可能剛好
                # 遇到雜訊），累積最近幾次輪替（最多保留 6 次 ≈ 6 小時歷史，滿
                # 2 次 ≈ 2 小時就開始判斷）的 ATR% 取平均，明顯持續偏離策略可
                # 交易區間（MIN_ATR_PCT ~ MAX_ATR_PCT）太多時，直接排除在候選
                # 之外，不用等 volatility_quality 慢慢把分數壓低、也不用你自己
                # 盯著波動率表手動判斷該不該留。門檻抓寬鬆（0.5x ~ 1.5x）只排除
                # 明顯不合的，貼著邊界的交給評分去比。ATR 本身已經是 10 根 5
                # 分鐘K棒（約50分鐘）的平滑值，不是單點雜訊，2 次確認足夠。
                atr_hist = self.atr_history.setdefault(symbol, [])
                atr_hist.append(atr_pct)
                del atr_hist[:-6]
                avg_recent_atr_pct = sum(atr_hist) / len(atr_hist)
                volatility_excluded = len(atr_hist) >= 2 and (
                    avg_recent_atr_pct < MIN_ATR_PCT * 0.5
                    or avg_recent_atr_pct > MAX_ATR_PCT * 1.5
                )

                self.volatility_stats[symbol] = {
                    "atr_pct": round(atr_pct * 100.0, 4),
                    "avg_daily_up_pct": daily_up_pct,
                    "avg_daily_down_pct": daily_down_pct,
                    "sample_days": int(daily_group.shape[0]) if len(daily_group) else 0,
                    "change_24h_pct": round(change_pct, 3),
                    "volatility_excluded": volatility_excluded,
                    "updated_at": time.time(),
                }

                overall_stat = symbol_history.get(
                    symbol,
                    {"trades": 0, "avg_pnl": 0.0, "win_rate": 0.5, "stop_rate": 0.0},
                )
                history_quarantined = self._history_quarantined(overall_stat)
                atr_eligible = MIN_ATR_PCT <= atr_pct <= MAX_ATR_PCT

                for direction in ("LONG", "SHORT"):
                    is_long = direction == "LONG"
                    trend_aligned = price >= ema_1h if is_long else price <= ema_1h
                    wanted_direction = 1 if is_long else -1
                    st_5m_aligned = st_direction == wanted_direction
                    st_1h_aligned = st_direction_1h == wanted_direction
                    st_aligned = st_5m_aligned and st_1h_aligned
                    kc_upper = float(curr["kc_upper"])
                    kc_lower = float(curr["kc_lower"])
                    kc_setup = self._kc_entry_setup(
                        price, kc_upper, kc_lower, atr, direction,
                    )
                    entry_priority = int(kc_setup["priority"])
                    kc_score = float(kc_setup["score"])
                    kc_distance_atr = float(kc_setup["distance_atr"])
                    rsi_score = (
                        min(max((rsi - 45.0) / 15.0, 0.0), 1.0)
                        if is_long
                        else min(max((55.0 - rsi) / 15.0, 0.0), 1.0)
                    )
                    directional_change = change_pct if is_long else -change_pct
                    meme_burst = bool(
                        meme_burst_energy
                        and ((is_long and recent_change_pct > 0.0)
                             or (not is_long and recent_change_pct < 0.0))
                    )
                    movement_score = max(0.0, 1.0 - abs(directional_change - 3.0) / 7.0)
                    # 在合格 ATR 區間內，優先選波動較大的主流合約；超出上限
                    # 仍會由 atr_eligible／volatility_excluded 淘汰，避免把極端
                    # 拉砸幣誤選成「高波動機會」。
                    volatility_priority = (
                        min(max((atr_pct - MIN_ATR_PCT) / (MAX_ATR_PCT - MIN_ATR_PCT), 0.0), 1.0)
                        if MAX_ATR_PCT > MIN_ATR_PCT else 0.0
                    )
                    stat = history.get(
                        (symbol, direction),
                        {"trades": 0, "avg_pnl": 0.0, "win_rate": 0.5, "stop_rate": 0.0},
                    )
                    overheat_penalty = min(max((abs(change_pct) - 15.0) / 15.0, 0.0), 1.0) * 15.0
                    if stat["trades"] >= 3:
                        pnl_score = (math.tanh(stat["avg_pnl"] / 1.5) + 1.0) / 2.0
                        history_score = (
                            stat["win_rate"] * 0.50
                            + (1.0 - stat["stop_rate"]) * 0.30
                            + pnl_score * 0.20
                        )
                    else:
                        history_score = 0.50
                    quant_score = (
                        (1.0 if trend_aligned else 0.0) * 10.0
                        + (1.0 if st_aligned else 0.0) * 10.0
                        + kc_score * 30.0
                        + rsi_score * 5.0
                        + min(volume_ratio / 0.8, 1.0) * 10.0
                        + liquidity * 15.0
                        + history_score * 10.0
                        + movement_score * 5.0
                        + volatility_priority * VOLATILITY_ROTATION_WEIGHT
                        + (12.0 if meme_burst else 0.0)
                    ) - overheat_penalty
                    results.append({
                        "symbol": symbol,
                        "direction": direction,
                        "quant_score": quant_score,
                        "eligible": (
                            self._direction_is_eligible(
                                trend_aligned, st_5m_aligned, st_1h_aligned, atr_pct,
                                volatility_excluded, history_quarantined,
                            ) or meme_burst
                        ) and (adx <= SYMBOL_MAX_ADX_RANGE) and (
                            kc_width_pct >= SYMBOL_MIN_KC_WIDTH_PCT
                        ) and entry_priority > 0 and (
                            volume_ratio >= KELTNER_MIN_VOLUME_RATIO
                        ),
                        "entry_priority": entry_priority,
                        "is_meme": is_meme,
                        "meme_burst": meme_burst,
                        "recent_change_pct": recent_change_pct,
                        "energy_eligible": volume_ratio >= KELTNER_MIN_VOLUME_RATIO,
                        "atr_eligible": atr_eligible,
                        "atr_pct": atr_pct,
                        "history_quarantined": history_quarantined,
                        "history_trades": overall_stat["trades"],
                        "history_avg_pnl": overall_stat["avg_pnl"],
                        "history_stop_rate": overall_stat["stop_rate"],
                        "volatility_excluded": volatility_excluded,
                        "price": price,
                        "ema_1h": ema_1h,
                        "st_aligned": st_aligned,
                        "st_5m_aligned": st_5m_aligned,
                        "st_1h_aligned": st_1h_aligned,
                        "st_direction_1h": st_direction_1h,
                        "kc_distance_atr": kc_distance_atr,
                        "rsi": rsi,
                        "volume_ratio": volume_ratio,
                        "quote_volume": quote_volume,
                        "change_pct": change_pct,
                        **stat,
                    })
            except Exception:
                continue
            await asyncio.sleep(0.05)
        return results


    @staticmethod
    def choose_directional_symbols(
        current: List[str],
        held_positions: Dict[str, dict],
        metrics: List[dict],
        setup_protected_symbols: Iterable[str] = (),
    ) -> tuple[List[str], Dict[str, str], List[dict]]:
        qualified = [
            item for item in metrics
            if (
                item.get("eligible")
                and item.get("final_score", 0.0) >= DIRECTIONAL_MIN_SCORE
                and item.get("symbol") not in ENTRY_DISABLED_SYMBOLS
            )
        ]
        selected_items = []
        used_symbols = set()
        for direction in ("LONG", "SHORT"):
            side_ranked = sorted(
                [item for item in qualified if item["direction"] == direction],
                key=lambda item: (
                    int(item.get("entry_priority") or 0),
                    item["final_score"],
                ),
                reverse=True,
            )
            for item in side_ranked:
                if item["symbol"] in used_symbols:
                    continue
                selected_items.append(item)
                used_symbols.add(item["symbol"])
                side_count = sum(row["direction"] == direction for row in selected_items)
                if side_count >= DIRECTIONAL_SIDE_COUNT:
                    break

        # 任一邊不足 6 個時，用兩邊剩餘還達標的候選依分數混合補滿介面 12 個。
        # 原本這裡固定只用多單補位，導致空單候選不夠 6 個時，池子會被多單
        # 灌滿——就算多單那陣子完全沒訊號，池子裡也擠不出空單可以做。
        if len(selected_items) < SYMBOL_ROTATION_COUNT:
            mixed_backfill = sorted(
                [item for item in qualified if item["symbol"] not in used_symbols],
                key=lambda item: (
                    int(item.get("entry_priority") or 0),
                    item["final_score"],
                ),
                reverse=True,
            )
            for item in mixed_backfill:
                selected_items.append(item)
                used_symbols.add(item["symbol"])
                if len(selected_items) >= SYMBOL_ROTATION_COUNT:
                    break

        for symbol, position in held_positions.items():
            if symbol in used_symbols:
                continue
            side = str(position.get("side", "LONG")).upper()
            replaceable = [
                item for item in selected_items
                if item["symbol"] not in held_positions and item["direction"] == side
            ]
            if not replaceable:
                replaceable = [
                    item for item in selected_items if item["symbol"] not in held_positions
                ]
            if replaceable:
                removed = min(
                    replaceable,
                    key=lambda item: (
                        int(item.get("entry_priority") or 0),
                        item["final_score"],
                    ),
                )
                selected_items.remove(removed)
                used_symbols.discard(removed["symbol"])
            selected_items.append({
                "symbol": symbol,
                "direction": side,
                "final_score": 100.0,
                "protected_position": True,
            })
            used_symbols.add(symbol)

        # A confirmed outer setup only has the next adjacent candle to enter.
        protected_setups = {
            str(symbol).strip() for symbol in setup_protected_symbols
            if str(symbol).strip() and str(symbol).strip() not in held_positions
        }
        protected_items = []
        for protected_symbol in protected_setups:
            if protected_symbol not in current:
                continue
            matching = [
                item for item in metrics
                if item.get("symbol") == protected_symbol
            ]
            protected_items.append(
                max(
                    matching,
                    key=lambda item: item.get("final_score", 0.0),
                )
                if matching else {
                    "symbol": protected_symbol, "direction": "BOTH",
                    "entry_priority": 4, "final_score": 100.0,
                }
            )
        protected_items.sort(
            key=lambda item: (
                int(item.get("entry_priority") or 0),
                item.get("final_score", 0.0),
            ),
            reverse=True,
        )
        for protected in protected_items:
            if protected["symbol"] in used_symbols:
                continue
            replaceable = [
                item for item in selected_items
                if item["symbol"] not in held_positions
                and item["symbol"] not in protected_setups
            ]
            if len(selected_items) >= SYMBOL_ROTATION_COUNT:
                if not replaceable:
                    break
                removed = min(
                    replaceable,
                    key=lambda item: (
                        int(item.get("entry_priority") or 0),
                        item.get("final_score", 0.0),
                    ),
                )
                selected_items.remove(removed)
                used_symbols.discard(removed["symbol"])
            selected_items.append(protected)
            used_symbols.add(protected["symbol"])

        desired_items = selected_items[:SYMBOL_ROTATION_COUNT]
        desired_by_symbol = {item["symbol"]: item for item in desired_items}
        best_by_symbol = {}
        for item in metrics:
            symbol = item["symbol"]
            if symbol not in best_by_symbol or item.get("final_score", 0.0) > best_by_symbol[symbol].get("final_score", 0.0):
                best_by_symbol[symbol] = item

        # 單槽的兩幣牌面必須真的是本輪最佳候選，不能沿用大牌面時
        # 「等待立即可交易者才換幣」的舊名單黏著邏輯。
        if SYMBOL_ROTATION_COUNT <= 2:
            selected = [item["symbol"] for item in desired_items]
            current_slice = list(current[:SYMBOL_ROTATION_COUNT])
            removed = [
                symbol for symbol in current_slice
                if symbol not in selected and symbol not in held_positions
            ]
            incoming = [symbol for symbol in selected if symbol not in current_slice]
            changes = []
            for index, outgoing in enumerate(removed):
                incoming_symbol = incoming[index] if index < len(incoming) else ""
                incoming_item = desired_by_symbol.get(incoming_symbol, {})
                changes.append({
                    "out": outgoing,
                    "in": incoming_symbol,
                    "direction": incoming_item.get("direction", ""),
                })
            for incoming_symbol in incoming[len(removed):]:
                incoming_item = desired_by_symbol.get(incoming_symbol, {})
                changes.append({
                    "out": "",
                    "in": incoming_symbol,
                    "direction": incoming_item.get("direction", ""),
                })
            directions = {
                item["symbol"]: item["direction"] for item in desired_items
            }
            return selected, directions, changes

        # 介面上的幣種要留足時間觀察。只有替代者已在 KC 可立即進場區
        # (entry_priority=3) 才換出原本的觀察幣；其餘尚在等待型的候選留在
        # 掃描池，避免牌面因分數短暫變動不停跳換。
        immediate_incoming = [
            item for item in desired_items
            if (
                item["symbol"] not in current[:SYMBOL_ROTATION_COUNT]
                and int(item.get("entry_priority") or 0) >= 3
            )
        ]

        # 先維持目前的非停用觀察幣與持倉；若有立即可交易的替代者，再只
        # 換出等量的最低優先級觀察幣，而非一次清空整個介面。
        changes = []
        current_slice = list(current[:SYMBOL_ROTATION_COUNT])
        selected = [
            symbol for symbol in current_slice
            if symbol in held_positions
            or symbol not in ENTRY_DISABLED_SYMBOLS
        ]
        removed = [
            symbol for symbol in current_slice
            if symbol not in selected and symbol not in held_positions
        ]
        incoming_pool = (
            [item for item in desired_items if item["symbol"] not in selected]
            if len(selected) < SYMBOL_ROTATION_COUNT
            else immediate_incoming
        )
        incoming_items = sorted(
            incoming_pool,
            key=lambda item: (
                int(item.get("entry_priority") or 0),
                item.get("final_score", 0.0),
            ),
            reverse=True,
        )
        for incoming_item in incoming_items:
            if len(selected) >= SYMBOL_ROTATION_COUNT:
                replaceable = [
                    symbol for symbol in selected
                    if symbol not in held_positions
                ]
                if not replaceable:
                    break
                outgoing = min(
                    replaceable,
                    key=lambda symbol: (
                        int((best_by_symbol.get(symbol) or {}).get("entry_priority") or 0),
                        float((best_by_symbol.get(symbol) or {}).get("final_score") or 0.0),
                    ),
                )
                selected.remove(outgoing)
                changes.append({
                    "out": outgoing,
                    "in": incoming_item["symbol"],
                    "direction": incoming_item["direction"],
                })
            selected.append(incoming_item["symbol"])
        for outgoing in removed:
            changes.append({"out": outgoing, "in": "", "direction": ""})

        selected_items = [
            desired_by_symbol.get(symbol)
            or best_by_symbol.get(symbol)
            or {"symbol": symbol, "direction": "LONG", "final_score": 0.0}
            for symbol in selected
        ]
        directions = {item["symbol"]: item["direction"] for item in selected_items}
        return selected, directions, changes

    async def purge_unhealthy(self, exchange) -> List[dict]:
        """輕量健康檢查：只用當下 ticker 資料，判斷候選觀察名單（尚未持倉）
        裡有沒有幣種已經變得不健康，不用等下一次整點的完整輪替（含AI+全池
        K線，最壞要等 SYMBOL_ROTATION_INTERVAL_SEC）才處理。已經有持倉的
        幣種不受影響，維持只等SL/TP/24h時間過濾出場，不會被這裡動到。"""
        tickers = await exchange.fetch_tickers()
        normalized = self._normalize_tickers(tickers)
        held = set(self.account.positions.keys())
        changes: List[dict] = []
        for symbol in list(DEFAULT_SYMBOLS):
            if symbol in held or symbol in self.setup_protected_symbols:
                continue
            if symbol in ENTRY_DISABLED_SYMBOLS:
                reason = "已暫停新倉"
            else:
                ticker = normalized.get(symbol)
                if not ticker:
                    continue
                quote_volume = float(ticker.get("quoteVolume") or 0.0)
                change_pct = abs(float(ticker.get("percentage") or 0.0))
                volatility_excluded = bool(
                    self.volatility_stats.get(symbol, {}).get("volatility_excluded")
                )

                if quote_volume < SYMBOL_MIN_QUOTE_VOLUME:
                    reason = f"流動性不足({quote_volume:.0f}<{SYMBOL_MIN_QUOTE_VOLUME:.0f})"
                elif change_pct > SYMBOL_MAX_24H_CHANGE_PCT:
                    reason = f"24h暴漲暴跌({change_pct:.1f}%>{SYMBOL_MAX_24H_CHANGE_PCT:.1f}%)"
                elif volatility_excluded:
                    reason = "波動率長期偏離可交易區間"
                else:
                    continue

            DEFAULT_SYMBOLS.remove(symbol)
            self.direction_map.pop(symbol, None)
            self.atr_history.pop(symbol, None)
            self.volatility_stats.pop(symbol, None)
            changes.append({"out": symbol, "in": "", "reason": reason})

        if changes:
            self._save()
        return changes

    async def rotate(self, exchange, execution_symbols: set = None) -> List[dict]:
        await exchange.load_markets()
        tickers = await exchange.fetch_tickers()
        active_usdt_perpetuals = sum(
            1 for market in exchange.markets.values()
            if market.get("active")
            and market.get("swap")
            and market.get("quote") == "USDT"
            and market.get("info", {}).get("contractType") == "PERPETUAL"
            and market.get("info", {}).get("underlyingType") == "COIN"
        )
        candidates = self.market_candidates(tickers, exchange.markets, execution_symbols)
        forced_exclusions = self.replacement_exclusions()
        if forced_exclusions:
            candidates = [
                symbol for symbol in candidates if symbol not in forced_exclusions
            ]
        try:
            funding_rates = await exchange.fetch_funding_rates()
            valid_candidates = []
            for symbol in candidates:
                fr_info = funding_rates.get(symbol, {})
                fr = abs(float(fr_info.get("fundingRate") or 0.0))
                if fr <= SYMBOL_MAX_FUNDING_RATE:
                    valid_candidates.append(symbol)
                else:
                    self.account.log(f"剔除 {symbol}: 資金費率偏離 ({fr*100:.3f}%)", "DEBUG")
            candidates = valid_candidates
        except Exception as e:
            self.account.log(f"獲取資金費率失敗，略過資金費率過濾: {e}", "WARNING")

        market_metrics = self.build_metrics(tickers, candidates)
        quant_ranked = sorted(market_metrics, key=lambda item: item["quant_score"], reverse=True)
        ai_ranking = await self.ai.rank_symbols(quant_ranked)
        ai_count = max(len(ai_ranking) - 1, 1)
        ai_scores = {
            symbol: 1.0 - (index / ai_count)
            for index, symbol in enumerate(ai_ranking)
        }
        directional = await self.build_directional_metrics(exchange, tickers, candidates)
        for item in directional:
            item["final_score"] = (
                item["quant_score"] * (1.0 - AI_ADVISOR_WEIGHT)
                + ai_scores.get(item["symbol"], 0.5) * 100.0 * AI_ADVISOR_WEIGHT
            )
        analyzed_by_symbol = {}
        for item in directional:
            analyzed_by_symbol.setdefault(item["symbol"], item)
        atr_low_count = sum(
            item.get("atr_pct", 0.0) < MIN_ATR_PCT
            for item in analyzed_by_symbol.values()
        )
        atr_high_count = sum(
            item.get("atr_pct", 0.0) > MAX_ATR_PCT
            for item in analyzed_by_symbol.values()
        )
        eligible_symbols = {
            item["symbol"] for item in directional if item.get("eligible")
        }
        qualified_symbols = {
            item["symbol"] for item in directional
            if item.get("eligible") and item.get("final_score", 0.0) >= DIRECTIONAL_MIN_SCORE
        }
        score_low_count = len(eligible_symbols - qualified_symbols)
        other_rejected_count = max(
            0, len(analyzed_by_symbol) - atr_low_count - atr_high_count - len(eligible_symbols)
        )
        unavailable_count = max(0, len(candidates) - len(analyzed_by_symbol))
        filter_text = (
            f"ATR過低 {atr_low_count}、ATR過高 {atr_high_count}、"
            f"其他資格淘汰 {other_rejected_count}、評分<{DIRECTIONAL_MIN_SCORE:g} {score_low_count}"
        )
        if unavailable_count:
            filter_text += f"、資料不足/急漲跌 {unavailable_count}"

        selected, directions, changes = self.choose_directional_symbols(
            list(DEFAULT_SYMBOLS),
            self.account.positions,
            directional,
            self.setup_protected_symbols,
        )
        if candidates and unavailable_count >= len(candidates) and not qualified_symbols:
            selected = [
                symbol for symbol in self.fallback_symbols
                if symbol not in ENTRY_DISABLED_SYMBOLS
                and symbol not in forced_exclusions
            ][:SYMBOL_ROTATION_COUNT]
            directions = {symbol: "BOTH" for symbol in selected}
            changes = []

        # 當沒有任何合格幣種時，放入指定的備用迷因幣
        if not selected:
            for meme in ["1000PEPE/USDT", "1000BONK/USDT", "1000SHIB/USDT"]:
                if meme not in ENTRY_DISABLED_SYMBOLS and meme not in forced_exclusions:
                    selected.append(meme)
                    directions[meme] = "BOTH"
                    changes.append({"out": "", "in": meme, "direction": "BOTH"})
        # 持倉中的幣強制保留，即使已被輪替出去也不可移除
        for held_symbol in self.account.positions:
            if held_symbol not in selected:
                selected.append(held_symbol)
                directions[held_symbol] = "BOTH"
        # 輪替評分仍同時比較多／空以挑選值得觀察的標的，但進場掃描
        # 不鎖死單一方向：同一幣的 KC 多、空條件都要被偵測。
        directions = {symbol: "BOTH" for symbol in selected}
        DEFAULT_SYMBOLS[:] = selected
        self.direction_map = directions
        self.next_rotation_exclusions.difference_update(forced_exclusions)
        self.last_rotation_at = time.time()
        self.last_changes = changes
        selected_lookup = {(item["symbol"], item["direction"]): item for item in directional}
        self.last_metrics = [
            selected_lookup.get(
                (symbol, directions[symbol]),
                {"symbol": symbol, "direction": directions[symbol], "protected_position": True},
            )
            for symbol in selected
        ]
        long_count = sum(side == "LONG" for side in directions.values())
        short_count = sum(side == "SHORT" for side in directions.values())
        ai_text = "AI 正常輔助" if ai_ranking else "AI 不可用，已使用純量化回退"
        shortfall_text = (
            f"；僅 {len(selected)}/{SYMBOL_ROTATION_COUNT} 幣合格，不以不合格幣補位"
            if len(selected) < SYMBOL_ROTATION_COUNT else ""
        )
        self.last_reason = (
            f"已讀取 Binance 活躍USDT永續 {active_usdt_perpetuals} 幣；成交量初篩後深度掃描 {len(candidates)} 幣；篩選結果 {filter_text}；方向評分參考 多 {long_count}、空 {short_count}，入選後皆可雙向交易；"
            f"{ai_text}{shortfall_text}"
        )
        self._save()
        return changes

    def _save(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        payload = {
            "updated_at": self.last_rotation_at,
            "symbols": list(DEFAULT_SYMBOLS),
            "changes": self.last_changes,
            "reason": self.last_reason,
            "ai": self.ai.status(),
            "metrics": self.last_metrics,
            "direction_map": self.direction_map,
            "trade_ai_analysis": self.trade_analysis.status(),
            "volatility_stats": self.volatility_stats,
        }
        with open(SELECTION_FILE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def status(self) -> dict:
        return {
            "last_rotation_at": self.last_rotation_at,
            "changes": self.last_changes,
            "reason": self.last_reason,
            "ai": self.ai.status(),
            "top_metrics": self.last_metrics[:12],
            "direction_map": self.direction_map,
            "trade_ai_analysis": self.trade_analysis.status(),
            "volatility_stats": self.volatility_stats,
        }

    def get_dynamic_leverage(self, symbol: str, score, adx: float = None) -> int:
        """依實測 ATR% 決定槓桿上限，取代原本用市值猜的 SYMBOL_LEVERAGE；
        該幣種還沒有實測資料時（例如剛啟動、還沒跑過第一次輪替），
        退回原本的靜態表，行為不變。

        adx 是這次訊號當下的動能強度（不是幣種歷史波動率）：ADX越低代表
        趨勢動能越弱、越可能已經走到這波行情的末端，不管分數/波動率算出
        來的上限多高，一律不套用高槓桿，避免用6x去賭一個動能已經在衰退
        的訊號。"""
        stats = self.volatility_stats.get(symbol)
        if not stats or not stats.get("atr_pct"):
            result = get_signal_leverage(symbol, score)
        else:
            atr_pct = stats["atr_pct"] / 100.0
            symbol_cap = get_atr_based_leverage(atr_pct)
            score_value = score or 0
            result = 1
            for threshold, cap in SIGNAL_LEVERAGE_CAPS:
                if score_value >= threshold:
                    result = symbol_cap if cap is None else min(symbol_cap, cap)
                    break
        if adx is not None and adx < WEAK_ENERGY_ADX_THRESHOLD:
            result = min(result, WEAK_ENERGY_LEVERAGE_CAP)
        return result
