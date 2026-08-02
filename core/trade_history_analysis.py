import hashlib
import json
import os
import time
from collections import defaultdict
from typing import Dict, Iterable, List


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
ANALYSIS_FILE = os.path.join(DATA_DIR, "ai_trade_analysis.json")


class TradeHistoryAnalyzer:
    """將完整平倉歷史做本機統計，僅把去識別化摘要與近期樣本交給 AI。"""

    def __init__(self, advisor, analysis_file: str = ANALYSIS_FILE, retry_after_sec: float = 900.0):
        self.advisor = advisor
        self.analysis_file = analysis_file
        self.retry_after_sec = retry_after_sec
        self.analysis = {
            "status": "not_analyzed",
            "updated_at": 0.0,
            "trade_count": 0,
            "summary": "尚未分析歷史交易",
            "strengths": [],
            "weaknesses": [],
            "recommendations": [],
            "risk_flags": [],
            "statistics": {},
            "error": "",
        }
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.analysis_file):
            return
        try:
            with open(self.analysis_file, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            if isinstance(saved, dict):
                self.analysis.update(saved)
        except (OSError, json.JSONDecodeError):
            pass

    def _save(self) -> None:
        directory = os.path.dirname(self.analysis_file)
        os.makedirs(directory, exist_ok=True)
        temp_file = f"{self.analysis_file}.tmp"
        with open(temp_file, "w", encoding="utf-8") as handle:
            json.dump(self.analysis, handle, ensure_ascii=False, indent=2)
        os.replace(temp_file, self.analysis_file)

    @staticmethod
    def _infer_exit_type(trade: dict, opened: dict) -> str:
        explicit = str(trade.get("exit_type") or "").upper()
        if explicit in {"TP", "SL", "PROFIT_PROTECT", "OTHER", "UNKNOWN"}:
            return explicit
        reason = str(trade.get("reason") or "")
        if "Take-Profit" in reason or "止盈" in reason:
            return "TP"
        if "Stop-Loss" in reason or "止損" in reason:
            return "SL"
        if "保護單成交" not in reason:
            return "OTHER"

        # 舊資料只有籠統的「保護單成交」時，以當時記錄的原始 SL/TP 價格
        # 回推；價格沒有碰到任一邊界的移動止損舊單維持 UNKNOWN，不偽造分類。
        side = str(trade.get("side") or opened.get("side") or "")
        close_price = float(trade.get("price") or 0.0)
        sl = float(opened.get("sl") or 0.0)
        tp = float(opened.get("tp") or 0.0)
        tolerance = abs(close_price) * 0.002
        if side == "LONG":
            if tp > 0 and close_price >= tp - tolerance:
                return "TP"
            if sl > 0 and close_price <= sl + tolerance:
                return "SL"
        elif side == "SHORT":
            if tp > 0 and close_price <= tp + tolerance:
                return "TP"
            if sl > 0 and close_price >= sl - tolerance:
                return "SL"
        return "UNKNOWN"

    @staticmethod
    def pair_closed_trades(trades: Iterable[dict]) -> List[dict]:
        """配對開平倉，避免把 API Key、餘額、持倉等帳戶資料送入 AI。"""
        open_by_symbol: Dict[str, dict] = {}
        closed = []
        for trade in reversed(list(trades)):
            action = str(trade.get("action", ""))
            symbol = str(trade.get("symbol", ""))
            if action.startswith("OPEN_"):
                open_by_symbol[symbol] = trade
                continue
            if action.startswith("PARTIAL_CLOSE_"):
                opened = open_by_symbol.get(symbol, {})
                closed.append(
                    {
                        "symbol": symbol,
                        "side": str(trade.get("side") or opened.get("side") or ""),
                        "opened_at": str(opened.get("time", "")),
                        "closed_at": str(trade.get("time", "")),
                        "entry_price": float(opened.get("price") or 0.0),
                        "exit_price": float(trade.get("price") or 0.0),
                        "amount": float(trade.get("amount") or opened.get("amount") or 0.0),
                        "leverage": int(opened.get("leverage") or 0),
                        "signal_score": opened.get("signal_score"),
                        "raw_signal_score": opened.get("raw_signal_score"),
                        "btc_adjusted_score": opened.get("btc_adjusted_score"),
                        "history_adjusted_score": opened.get("history_adjusted_score"),
                        "pullback_confirmation_score": opened.get("pullback_confirmation_score"),
                        "entry_reason": str(opened.get("reason", ""))[:300],
                        "exit_reason": str(trade.get("reason", ""))[:200],
                        "exit_type": TradeHistoryAnalyzer._infer_exit_type(trade, opened),
                        "fee": float(trade.get("fee") or 0.0),
                        "net_pnl": float(trade.get("pnl") or 0.0),
                    }
                )
                continue
            if not action.startswith("CLOSE_"):
                continue

            opened = open_by_symbol.pop(symbol, {})
            closed.append(
                {
                    "symbol": symbol,
                    "side": str(trade.get("side") or opened.get("side") or ""),
                    "opened_at": str(opened.get("time", "")),
                    "closed_at": str(trade.get("time", "")),
                    "entry_price": float(opened.get("price") or 0.0),
                    "exit_price": float(trade.get("price") or 0.0),
                    "amount": float(trade.get("amount") or opened.get("amount") or 0.0),
                    "leverage": int(opened.get("leverage") or 0),
                    "signal_score": opened.get("signal_score"),
                    "raw_signal_score": opened.get("raw_signal_score"),
                    "btc_adjusted_score": opened.get("btc_adjusted_score"),
                    "history_adjusted_score": opened.get("history_adjusted_score"),
                    "pullback_confirmation_score": opened.get("pullback_confirmation_score"),
                    "entry_reason": str(opened.get("reason", ""))[:300],
                    "exit_reason": str(trade.get("reason", ""))[:200],
                    "exit_type": TradeHistoryAnalyzer._infer_exit_type(trade, opened),
                    "fee": float(trade.get("fee") or 0.0),
                    "net_pnl": float(trade.get("pnl") or 0.0),
                }
            )
        return closed

    @staticmethod
    def _score_bucket(score) -> str:
        """把精細評分（70~100+）分成幾個區間，樣本數少時比逐分分組更有統計意義。"""
        if score in (None, ""):
            return "未記錄"
        try:
            value = float(score)
        except (TypeError, ValueError):
            return "未記錄"
        if value < 71:
            return "70(壓線)"
        if value <= 75:
            return "71-75"
        if value <= 80:
            return "76-80"
        if value <= 90:
            return "81-90"
        return "91+"

    @staticmethod
    def _is_stop(row: dict) -> bool:
        return row.get("exit_type") == "SL"

    @staticmethod
    def _group_stats(records: List[dict], key: str) -> List[dict]:
        groups = defaultdict(list)
        for row in records:
            value = row.get(key)
            label = "未記錄" if value in (None, "") else str(value)
            groups[label].append(row)

        result = []
        for label, rows in groups.items():
            pnls = [row["net_pnl"] for row in rows]
            result.append(
                {
                    key: label,
                    "trades": len(rows),
                    "net_pnl": round(sum(pnls), 4),
                    "avg_pnl": round(sum(pnls) / len(rows), 4),
                    "win_rate": round(sum(value > 0 for value in pnls) / len(rows), 4),
                    "stop_rate": round(
                        sum(TradeHistoryAnalyzer._is_stop(row) for row in rows) / len(rows), 4
                    ),
                }
            )
        return sorted(result, key=lambda item: (-item["trades"], item[key]))

    @classmethod
    def build_history(cls, trades: Iterable[dict]) -> dict:
        records = cls.pair_closed_trades(trades)
        for row in records:
            row["score_bucket"] = cls._score_bucket(row.get("signal_score"))
        pnls = [row["net_pnl"] for row in records]
        gross_profit = sum(value for value in pnls if value > 0)
        gross_loss = abs(sum(value for value in pnls if value < 0))
        count = len(records)
        stop_count = sum(cls._is_stop(row) for row in records)
        tp_count = sum(row.get("exit_type") == "TP" for row in records)
        profit_protect_count = sum(
            row.get("exit_type") == "PROFIT_PROTECT" for row in records
        )
        classified_protection_count = stop_count + tp_count
        overview = {
            "closed_trades": count,
            "net_pnl": round(sum(pnls), 4),
            "total_fees": round(sum(row["fee"] for row in records), 4),
            "win_rate": round(sum(value > 0 for value in pnls) / count, 4) if count else 0.0,
            "avg_pnl": round(sum(pnls) / count, 4) if count else 0.0,
            "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
            "stop_rate": round(stop_count / count, 4) if count else 0.0,
            "protection_stop_rate": round(stop_count / classified_protection_count, 4) if classified_protection_count else 0.0,
            "tp_count": tp_count,
            "sl_count": stop_count,
            "profit_protect_count": profit_protect_count,
            "unknown_exit_count": sum(row.get("exit_type") == "UNKNOWN" for row in records),
        }
        digest_source = json.dumps(records, ensure_ascii=False, sort_keys=True).encode("utf-8")
        bucket_order = ["70(壓線)", "71-75", "76-80", "81-90", "91+", "未記錄"]
        by_score_bucket = sorted(
            cls._group_stats(records, "score_bucket"),
            key=lambda item: bucket_order.index(item["score_bucket"])
            if item["score_bucket"] in bucket_order else 999,
        )
        return {
            "history_digest": hashlib.sha256(digest_source).hexdigest(),
            "overview": overview,
            "by_symbol": cls._group_stats(records, "symbol"),
            "by_side": cls._group_stats(records, "side"),
            "by_signal_score": cls._group_stats(records, "signal_score"),
            "by_score_bucket": by_score_bucket,
            "by_exit_type": cls._group_stats(records, "exit_type"),
            "by_exit_reason": cls._group_stats(records, "exit_reason"),
            "recent_closed_trades": records[-50:],
        }

    async def analyze_if_changed(self, trades: Iterable[dict]) -> bool:
        history = self.build_history(trades)
        trade_count = history["overview"]["closed_trades"]
        if trade_count == 0:
            self.analysis.update(
                {
                    "status": "no_data",
                    "trade_count": 0,
                    "summary": "目前沒有已平倉交易可供 AI 分析",
                }
            )
            return False

        digest = history["history_digest"]
        if digest == self.analysis.get("history_digest"):
            if self.analysis.get("status") == "ok":
                return False
            elapsed = time.time() - float(self.analysis.get("updated_at") or 0.0)
            if elapsed < self.retry_after_sec:
                return False

        ai_input = {
            "overview": history["overview"],
            "by_symbol": history["by_symbol"],
            "by_side": history["by_side"],
            "by_signal_score": history["by_signal_score"],
            "by_score_bucket": history["by_score_bucket"],
            "by_exit_type": history["by_exit_type"],
            "by_exit_reason": history["by_exit_reason"],
            "recent_closed_trades": history["recent_closed_trades"],
        }
        result = await self.advisor.analyze_trade_history(ai_input)
        ai_status = self.advisor.history_status()
        self.analysis = {
            "status": "ok" if result else ai_status.get("status", "fallback"),
            "updated_at": time.time(),
            "trade_count": trade_count,
            "history_digest": digest,
            "model": ai_status.get("model", ""),
            "summary": result.get("summary", "AI 暫時無法完成分析，已保留程式統計"),
            "strengths": result.get("strengths", []),
            "weaknesses": result.get("weaknesses", []),
            "recommendations": result.get("recommendations", []),
            "risk_flags": result.get("risk_flags", []),
            "statistics": {
                "overview": history["overview"],
                "by_symbol": history["by_symbol"],
                "by_signal_score": history["by_signal_score"],
                "by_score_bucket": history["by_score_bucket"],
                "by_exit_type": history["by_exit_type"],
                "by_exit_reason": history["by_exit_reason"],
            },
            "error": ai_status.get("error", ""),
        }
        self._save()
        return bool(result)

    def status(self) -> dict:
        return dict(self.analysis)
