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
    TREND_FILTER_EMA_PERIOD,
)


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SELECTION_FILE = os.path.join(DATA_DIR, "symbol_selection.json")


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
    def market_candidates(tickers: dict, markets: dict = None) -> List[str]:
        normalized = SymbolRotation._normalize_tickers(tickers)
        allowed_crypto = None
        if markets:
            allowed_crypto = {
                market["symbol"].replace(":USDT", "")
                for market in markets.values()
                if market.get("active")
                and market.get("swap")
                and market.get("quote") == "USDT"
                and market.get("info", {}).get("contractType") == "PERPETUAL"
                and market.get("info", {}).get("underlyingType") == "COIN"
            }
        excluded_bases = {
            "1000PEPE", "APT", "ETH", "FET", "TAO", "WIF",
            "USDC", "FDUSD", "TUSD", "USDP",
        }
        ranked = []
        for symbol, ticker in normalized.items():
            if not symbol.endswith("/USDT"):
                continue
            if allowed_crypto is not None and symbol not in allowed_crypto:
                continue
            base = symbol.split("/", 1)[0]
            quote_volume = float(ticker.get("quoteVolume") or 0.0)
            change_pct = abs(float(ticker.get("percentage") or 0.0))
            if base in excluded_bases or quote_volume < SYMBOL_MIN_QUOTE_VOLUME or change_pct > SYMBOL_MAX_24H_CHANGE_PCT:
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
        log_volumes = {
            symbol: math.log10(max(float(normalized.get(symbol, {}).get("quoteVolume") or 0.0), 1.0))
            for symbol in candidates
        }
        low = min(log_volumes.values()) if log_volumes else 0.0
        high = max(log_volumes.values()) if log_volumes else 1.0
        spread = max(high - low, 1e-9)
        results = []

        for symbol in candidates:
            try:
                raw_5m = await exchange.fetch_ohlcv(symbol, timeframe="5m", limit=100)
                raw_1h = await exchange.fetch_ohlcv(symbol, timeframe="1h", limit=200)
                columns = ["timestamp", "open", "high", "low", "close", "volume"]
                df = pd.DataFrame(raw_5m, columns=columns)
                df_1h = pd.DataFrame(raw_1h, columns=columns)
                if len(df) < 50 or len(df_1h) < 30:
                    continue
                listing_cutoff = time.time() * 1000 - SYMBOL_MIN_LISTING_DAYS * 86400 * 1000
                if float(df_1h.iloc[0]["timestamp"]) > listing_cutoff:
                    continue

                computed = self.strategy.compute_indicators(df)
                curr = computed.iloc[-1]
                price = float(curr["close"])
                atr = max(float(curr["atr"]), price * 0.0001)
                ema_1h = float(
                    df_1h["close"]
                    .ewm(span=min(len(df_1h), TREND_FILTER_EMA_PERIOD), adjust=False)
                    .mean()
                    .iloc[-1]
                )
                st_direction = int(curr["st_direction"])
                rsi = float(curr["rsi"])
                vol_ma = float(curr["vol_ma_20"]) if not pd.isna(curr["vol_ma_20"]) else 0.0
                volume_ratio = float(curr["volume"]) / vol_ma if vol_ma > 0 else 0.0
                liquidity = (log_volumes[symbol] - low) / spread
                ticker = normalized.get(symbol, {})
                quote_volume = float(ticker.get("quoteVolume") or 0.0)
                change_pct = float(ticker.get("percentage") or 0.0)

                for direction in ("LONG", "SHORT"):
                    is_long = direction == "LONG"
                    trend_aligned = price >= ema_1h if is_long else price <= ema_1h
                    st_aligned = st_direction == (1 if is_long else -1)
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
                    ) - overheat_penalty
                    results.append({
                        "symbol": symbol,
                        "direction": direction,
                        "quant_score": quant_score,
                        "eligible": trend_aligned,
                        "price": price,
                        "ema_1h": ema_1h,
                        "st_aligned": st_aligned,
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
            if item.get("eligible") and item.get("final_score", 0.0) >= DIRECTIONAL_MIN_SCORE
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

        # 空單不足 6 個時，以其餘達標多單依分數補滿介面 12 個。
        if len(selected_items) < SYMBOL_ROTATION_COUNT:
            long_backfill = sorted(
                [
                    item for item in qualified
                    if item["direction"] == "LONG" and item["symbol"] not in used_symbols
                ],
                key=lambda item: item["final_score"],
                reverse=True,
            )
            for item in long_backfill:
                selected_items.append(item)
                used_symbols.add(item["symbol"])
                if len(selected_items) >= SYMBOL_ROTATION_COUNT:
                    break

        # 若達 60 分的多單仍不足，僅以其餘多單排名補足介面；
        # 這不會繞過策略本身的 70 分實際開倉門檻。
        if len(selected_items) < SYMBOL_ROTATION_COUNT:
            display_backfill = sorted(
                [
                    item for item in metrics
                    if item["direction"] == "LONG" and item["symbol"] not in used_symbols
                ],
                key=lambda item: item.get("final_score", 0.0),
                reverse=True,
            )
            for item in display_backfill:
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

        changes = []
        if len(current) >= SYMBOL_ROTATION_COUNT:
            # 防抖：每輪最多換三幣，且新幣必須比被換幣至少高五分。
            selected = list(current[:SYMBOL_ROTATION_COUNT])
            incoming_items = sorted(
                [item for item in desired_items if item["symbol"] not in selected],
                key=lambda item: item.get("final_score", 0.0),
                reverse=True,
            )
            for incoming_item in incoming_items:
                if len(changes) >= SYMBOL_ROTATION_MAX_CHANGES:
                    break
                replaceable = [
                    symbol for symbol in selected
                    if symbol not in desired_by_symbol and symbol not in held_positions
                ]
                if not replaceable:
                    break
                outgoing = min(
                    replaceable,
                    key=lambda symbol: best_by_symbol.get(symbol, {}).get("final_score", 0.0),
                )
                outgoing_score = best_by_symbol.get(outgoing, {}).get("final_score", 0.0)
                incoming_score = incoming_item.get("final_score", 0.0)
                if incoming_score < outgoing_score + SYMBOL_ROTATION_MIN_SCORE_GAP:
                    break
                selected[selected.index(outgoing)] = incoming_item["symbol"]
                changes.append({
                    "out": outgoing,
                    "in": incoming_item["symbol"],
                    "direction": incoming_item["direction"],
                })
        else:
            selected = [item["symbol"] for item in desired_items]

        selected_items = [
            desired_by_symbol.get(symbol)
            or best_by_symbol.get(symbol)
            or {"symbol": symbol, "direction": "LONG", "final_score": 0.0}
            for symbol in selected
        ]
        directions = {item["symbol"]: item["direction"] for item in selected_items}
        return selected, directions, changes

    async def rotate(self, exchange) -> List[dict]:
        await exchange.load_markets()
        tickers = await exchange.fetch_tickers()
        candidates = self.market_candidates(tickers, exchange.markets)
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
        selected, directions, changes = self.choose_directional_symbols(
            list(DEFAULT_SYMBOLS),
            self.account.positions,
            directional,
        )
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
        display_backfill_count = sum(
            not item.get("eligible") or item.get("final_score", 0.0) < DIRECTIONAL_MIN_SCORE
            for item in self.last_metrics
            if not item.get("protected_position")
        )
        backfill_text = (
            f"；候選牌面補位 {display_backfill_count}"
            if display_backfill_count else ""
        )
        self.last_reason = (
            f"已掃描 Binance 合約市場 {len(candidates)} 幣；方向評分參考 多 {long_count}、空 {short_count}，入選後皆可雙向交易；"
            f"{ai_text}{backfill_text}"
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
        }
