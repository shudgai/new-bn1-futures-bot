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
)


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SELECTION_FILE = os.path.join(DATA_DIR, "symbol_selection.json")


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
        self.last_reason = "尚未執行"
        self.volatility_stats: Dict[str, dict] = {}
        self.atr_history: Dict[str, List[float]] = {}

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
        # 輪替名單是未來一段時間的「監控池」，不能要求輪替當下的 5m ST
        # 也已經同向；否則 5m 尚未翻轉的幣會被整輪移除，等真正出現入場
        # 訊號時反而不在掃描名單。輪替只保留 1h ST 與 ATR 健康門檻；
        # 1h EMA、5m ST 和歷史績效留給實際進場與探索倉位重新判斷。
        return (
            st_1h_aligned
            and MIN_ATR_PCT <= atr_pct <= MAX_ATR_PCT
            and not volatility_excluded
        )

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
    def market_candidates(
        tickers: dict, markets: dict = None, execution_symbols: set = None
    ) -> List[str]:
        normalized = SymbolRotation._normalize_tickers(tickers)
        allowed_crypto = None
        if markets:
            import time
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
            "1000PEPE", "APT", "FET", "TAO", "WIF",
            "USDC", "FDUSD", "TUSD", "USDP", "DAI", "USDE",
            "USD1", "BUSD", "USTC",
        }
        ranked = []
        for symbol, ticker in normalized.items():
            if not symbol.endswith("/USDT") or symbol in ENTRY_DISABLED_SYMBOLS:
                continue
            # 所有模式都可從主網全市場挑選；非紙上模式由 execution_symbols
            # 再限制為執行交易所實際存在且可下單的合約交集。
            if execution_symbols is not None and symbol not in execution_symbols:
                continue
            if allowed_crypto is not None and symbol not in allowed_crypto:
                continue
            base = symbol.split("/", 1)[0]
            quote_volume = float(ticker.get("quoteVolume") or 0.0)
            change_pct = abs(float(ticker.get("percentage") or 0.0))
            if (
                base in excluded_bases
                or base.endswith(("UP", "DOWN", "BULL", "BEAR"))
                or quote_volume < SYMBOL_MIN_QUOTE_VOLUME or change_pct > SYMBOL_MAX_24H_CHANGE_PCT
            ):
                continue
            ranked.append((quote_volume, symbol))
        ranked.sort(reverse=True)
        return [symbol for _, symbol in ranked[:SYMBOL_MARKET_SCAN_LIMIT]]

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
                listing_cutoff = time.time() * 1000 - SYMBOL_MIN_LISTING_DAYS * 86400 * 1000
                if float(df_1h.iloc[0]["timestamp"]) > listing_cutoff:
                    continue

                computed = self.strategy.compute_indicators(df)
                computed_1h = self.strategy.compute_indicators(df_1h)
                curr = computed.iloc[-1]
                st_direction_1h = int(computed_1h.iloc[-1]["st_direction"])
                price = float(curr["close"])

                # 急升急降過濾：回看最近 N 根5分K，漲跌幅超標則跳過
                if len(df) >= RAPID_MOVE_WINDOW + 1:
                    recent_close = float(df.iloc[-1]["close"])
                    past_close = float(df.iloc[-(RAPID_MOVE_WINDOW + 1)]["close"])
                    if past_close > 0:
                        recent_change_pct = abs((recent_close - past_close) / past_close * 100.0)
                        if recent_change_pct > RAPID_MOVE_THRESHOLD:
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
                liquidity = (log_volumes[symbol] - low) / spread
                ticker = normalized.get(symbol, {})
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
                    kc_target = float(curr["kc_upper"] if is_long else curr["kc_lower"])
                    kc_distance_atr = abs(price - kc_target) / atr
                    kc_score = max(0.0, 1.0 - kc_distance_atr / 2.0)
                    rsi_score = (
                        min(max((rsi - 45.0) / 15.0, 0.0), 1.0)
                        if is_long
                        else min(max((55.0 - rsi) / 15.0, 0.0), 1.0)
                    )
                    directional_change = change_pct if is_long else -change_pct
                    movement_score = max(0.0, 1.0 - abs(directional_change - 3.0) / 7.0)
                    # 波動品質：用實測 ATR% 是否落在策略實際會用到的可交易區間
                    # （MIN_ATR_PCT ~ MAX_ATR_PCT）中段來評分，跟 movement_score
                    # （看瞬間 24h 漲跌）是兩個角度——這裡看的是這個幣種「持續性的
                    # 5m 波動」跟策略實際下單門檻合不合拍，同一套公式跟
                    # strategy.py 的 quality_bonus 一致，不重新發明。
                    atr_mid = (MIN_ATR_PCT + MAX_ATR_PCT) / 2.0
                    atr_half_range = (MAX_ATR_PCT - MIN_ATR_PCT) / 2.0
                    volatility_quality = (
                        max(0.0, 1.0 - abs(atr_pct - atr_mid) / atr_half_range)
                        if atr_half_range > 0 else 0.0
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
                        (1.0 if trend_aligned else 0.0) * 20.0
                        + (1.0 if st_aligned else 0.0) * 15.0
                        + kc_score * 15.0
                        + rsi_score * 10.0
                        + min(volume_ratio / 0.8, 1.0) * 10.0
                        + liquidity * 15.0
                        + history_score * 10.0
                        + movement_score * 5.0
                        + volatility_quality * 5.0
                    ) - overheat_penalty
                    results.append({
                        "symbol": symbol,
                        "direction": direction,
                        "quant_score": quant_score,
                        "eligible": self._direction_is_eligible(
                            trend_aligned, st_5m_aligned, st_1h_aligned, atr_pct,
                            volatility_excluded, history_quarantined,
                        ) and (adx <= SYMBOL_MAX_ADX_RANGE) and (kc_width_pct >= SYMBOL_MIN_KC_WIDTH_PCT),
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
    def _blend_ai_scores(metrics: List[dict], ai_ranking: List[str]) -> Dict[str, float]:
        quant = {item["symbol"]: item["quant_score"] for item in metrics}
        if not ai_ranking:
            return quant
        count = max(len(ai_ranking) - 1, 1)
        ai_scores = {
            symbol: 1.0 - (index / count)
            for index, symbol in enumerate(ai_ranking)
        }
        return {
            symbol: (
                score * (1.0 - AI_ADVISOR_WEIGHT)
                + ai_scores.get(symbol, 0.5) * AI_ADVISOR_WEIGHT
            )
            for symbol, score in quant.items()
        }

    @staticmethod
    def choose_directional_symbols(
        current: List[str],
        held_positions: Dict[str, dict],
        metrics: List[dict],
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
                key=lambda item: item["final_score"],
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
                key=lambda item: item["final_score"],
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
                removed = min(replaceable, key=lambda item: item["final_score"])
                selected_items.remove(removed)
                used_symbols.discard(removed["symbol"])
            selected_items.append({
                "symbol": symbol,
                "direction": side,
                "final_score": 100.0,
                "protected_position": True,
            })
            used_symbols.add(symbol)

        desired_items = selected_items[:SYMBOL_ROTATION_COUNT]
        desired_by_symbol = {item["symbol"]: item for item in desired_items}
        best_by_symbol = {}
        for item in metrics:
            symbol = item["symbol"]
            if symbol not in best_by_symbol or item.get("final_score", 0.0) > best_by_symbol[symbol].get("final_score", 0.0):
                best_by_symbol[symbol] = item

        # 只保留本輪仍合格或已有持倉的幣；不合格幣立即退出，不再為了湊滿
        # 固定數量留在牌面。空缺只由本輪完整合格的 desired_items 補上。
        changes = []
        current_slice = list(current[:SYMBOL_ROTATION_COUNT])
        selected = [
            symbol for symbol in current_slice
            if symbol in held_positions
            or (symbol in desired_by_symbol and symbol not in ENTRY_DISABLED_SYMBOLS)
        ]
        removed = [
            symbol for symbol in current_slice
            if symbol not in selected and symbol not in held_positions
        ]
        incoming_items = sorted(
            [item for item in desired_items if item["symbol"] not in selected],
            key=lambda item: item.get("final_score", 0.0),
            reverse=True,
        )
        for incoming_item in incoming_items:
            if len(selected) >= SYMBOL_ROTATION_COUNT:
                break
            selected.append(incoming_item["symbol"])
            if removed:
                changes.append({
                    "out": removed.pop(0),
                    "in": incoming_item["symbol"],
                    "direction": incoming_item["direction"],
                })
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
            if symbol in held:
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
        )
        # 持倉中的幣強制保留，即使已被輪替出去也不可移除
        for held_symbol in self.account.positions:
            if held_symbol not in selected:
                selected.append(held_symbol)
                directions[held_symbol] = "BOTH"
        DEFAULT_SYMBOLS[:] = selected
        self.direction_map = directions
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
