import json
import math
import os
import time
from collections import defaultdict
from typing import Dict, Iterable, List

from core.ai_advisor import LocalAIAdvisor
from core.trade_history_analysis import TradeHistoryAnalyzer
from core.config import (
    AI_ADVISOR_ENABLED,
    AI_ADVISOR_TIMEOUT_SEC,
    AI_ADVISOR_URL,
    AI_ADVISOR_WEIGHT,
    DEFAULT_SYMBOLS,
    SYMBOL_CANDIDATE_POOL,
    SYMBOL_ROTATION_COUNT,
    SYMBOL_ROTATION_MIN_SCORE_GAP,
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
        self.last_rotation_at = 0.0
        self.last_changes: List[dict] = []
        self.last_metrics: List[dict] = []
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
    def _normalize_tickers(tickers: dict) -> Dict[str, dict]:
        normalized = {}
        for raw_symbol, ticker in tickers.items():
            symbol = raw_symbol.replace(":USDT", "")
            normalized[symbol] = ticker
        return normalized

    def build_metrics(self, tickers: dict) -> List[dict]:
        normalized = self._normalize_tickers(tickers)
        history = self._closed_trade_stats(self.account.trades)
        volumes = []
        for symbol in SYMBOL_CANDIDATE_POOL:
            ticker = normalized.get(symbol, {})
            volume = float(ticker.get("quoteVolume") or 0.0)
            volumes.append(math.log10(max(volume, 1.0)))
        low = min(volumes) if volumes else 0.0
        high = max(volumes) if volumes else 1.0
        spread = max(high - low, 1e-9)

        metrics = []
        for symbol, log_volume in zip(SYMBOL_CANDIDATE_POOL, volumes):
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
    def choose_symbols(
        current: List[str],
        held_symbols: Iterable[str],
        scores: Dict[str, float],
    ) -> tuple[List[str], List[dict]]:
        selected = [symbol for symbol in current if symbol in scores]
        held = set(held_symbols)
        ranked = sorted(scores, key=scores.get, reverse=True)
        for symbol in ranked:
            if len(selected) >= SYMBOL_ROTATION_COUNT:
                break
            if symbol not in selected:
                selected.append(symbol)

        changes = []
        while True:
            outsiders = [symbol for symbol in ranked if symbol not in selected]
            replaceable = [symbol for symbol in selected if symbol not in held]
            if not outsiders or not replaceable:
                break
            best_out = outsiders[0]
            worst_in = min(replaceable, key=scores.get)
            gap = scores[best_out] - scores[worst_in]
            if gap < SYMBOL_ROTATION_MIN_SCORE_GAP:
                break
            selected[selected.index(worst_in)] = best_out
            changes.append(
                {
                    "out": worst_in,
                    "in": best_out,
                    "score_gap": round(gap, 4),
                }
            )

        selected = sorted(selected, key=scores.get, reverse=True)[:SYMBOL_ROTATION_COUNT]
        return selected, changes

    async def rotate(self, exchange) -> List[dict]:
        tickers = await exchange.fetch_tickers(SYMBOL_CANDIDATE_POOL)
        metrics = self.build_metrics(tickers)
        quant_ranked = sorted(metrics, key=lambda item: item["quant_score"], reverse=True)
        ai_ranking = await self.ai.rank_symbols(quant_ranked)
        scores = self._blend_ai_scores(metrics, ai_ranking)
        selected, changes = self.choose_symbols(
            list(DEFAULT_SYMBOLS),
            self.account.positions.keys(),
            scores,
        )
        DEFAULT_SYMBOLS[:] = selected
        self.last_rotation_at = time.time()
        self.last_changes = changes
        self.last_metrics = sorted(
            [
                {**item, "final_score": scores[item["symbol"]]}
                for item in metrics
            ],
            key=lambda item: item["final_score"],
            reverse=True,
        )
        self.last_reason = "已完成量化評分；AI 正常輔助" if ai_ranking else "AI 不可用，已使用純量化回退"
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
            "trade_ai_analysis": self.trade_analysis.status(),
        }
