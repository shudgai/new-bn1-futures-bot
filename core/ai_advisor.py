import asyncio
import json
import math
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional


MARKET_REGIME_MAX_AGE_SECONDS = 180


class LocalAIAdvisor:
    """llama.cpp OpenAI-compatible advisor. It may rank symbols, never place orders."""

    def __init__(
        self,
        url: str,
        enabled: bool = True,
        timeout: float = 20.0,
        request_fn: Optional[Callable] = None,
    ):
        self.url = url
        self.enabled = enabled
        self.timeout = timeout
        self.request_fn = request_fn or self._request
        self.last_status = "disabled" if not enabled else "not_called"
        self.last_error = ""
        self.last_summary = ""
        self.last_model = ""
        self.last_history_status = "disabled" if not enabled else "not_called"
        self.last_history_error = ""
        self.last_history_model = ""
        self.last_history_summary = ""
        self._market_regimes: Dict[str, dict] = {}

    def _request(self, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _extract_json(content: str) -> dict:
        content = (content or "").strip()
        if content.startswith("```"):
            content = content.replace("```json", "", 1).replace("```", "").strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            if start < 0:
                raise
            parsed, _ = json.JSONDecoder().raw_decode(content[start:])
            return parsed

    def set_market_regime_unavailable(self, symbol: str, error: str) -> None:
        self._market_regimes[symbol] = {
            "state": "UNKNOWN",
            "status": "unavailable",
            "updated_at": time.time(),
            "error": str(error)[:300],
            "model": "",
        }

    async def analyze_market_regime(self, symbol: str, candles) -> dict:
        """Classify one symbol's 30 completed 1-minute candles; never place orders."""
        self.set_market_regime_unavailable(symbol, "analysis_pending")
        if not self.enabled:
            self.set_market_regime_unavailable(symbol, "advisor_disabled")
            return self.market_regime_status()[symbol]

        if candles is None or len(candles) != 30:
            self.set_market_regime_unavailable(symbol, "expected_30_completed_candles")
            return self.market_regime_status()[symbol]

        compact_candles = []
        try:
            for candle in candles:
                if isinstance(candle, dict):
                    item = candle
                else:
                    item = candle.to_dict()
                closed = item.get("is_closed", True)
                if not (closed is True or (
                    type(closed).__name__ == "bool_" and bool(closed)
                )):
                    raise ValueError("market_regime_requires_completed_candles")
                values = {
                    key: float(item[key])
                    for key in ("open", "high", "low", "close", "volume")
                }
                timestamp = float(item["timestamp"])
                if (not math.isfinite(timestamp)
                        or not all(math.isfinite(value) for value in values.values())
                        or min(values["open"], values["high"], values["low"], values["close"]) <= 0
                        or values["volume"] < 0
                        or values["high"] < max(values["open"], values["close"])
                        or values["low"] > min(values["open"], values["close"])
                        or values["high"] <= values["low"]):
                    raise ValueError("invalid_completed_candle")
                compact_candles.append({
                    "timestamp": int(timestamp),
                    **{key: round(value, 12) for key, value in values.items()},
                })
            stamps = [item["timestamp"] for item in compact_candles]
            if any(second - first != 60_000 for first, second in zip(stamps, stamps[1:])):
                raise ValueError("completed_candles_must_be_contiguous_1m")
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
            self.set_market_regime_unavailable(symbol, f"{type(exc).__name__}: {exc}")
            return self.market_regime_status()[symbol]

        payload = {
            "model": "local",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Classify the supplied single-symbol sequence of completed 1-minute "
                        "candles as exactly CHOPPY or TRENDING. Use directional progress, "
                        "overlap and consistency in these candles only. Do not predict price, "
                        "recommend a trade, choose a side, or infer missing data. Return one "
                        "JSON object with regime and a brief reason."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "classify_market_regime",
                            "symbol": symbol,
                            "timeframe": "1m",
                            "candles": compact_candles,
                            "schema": {
                                "regime": "CHOPPY or TRENDING",
                                "reason": "brief explanation",
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": 256,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
        }

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self.request_fn, payload),
                timeout=self.timeout + 2.0,
            )
            message = response["choices"][0]["message"]
            content = message.get("content") or message.get("reasoning_content", "")
            parsed = self._extract_json(content)
            state = parsed.get("regime")
            if state not in ("CHOPPY", "TRENDING"):
                raise ValueError("AI returned an unsupported market regime")
            self._market_regimes[symbol] = {
                "state": state,
                "status": "ok",
                "updated_at": time.time(),
                "error": "",
                "model": str(response.get("model", "")),
                "reason": str(parsed.get("reason", ""))[:300],
            }
        except (
            asyncio.TimeoutError,
            urllib.error.URLError,
            AttributeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self.set_market_regime_unavailable(symbol, f"{type(exc).__name__}: {exc}")
        return dict(self._market_regimes[symbol])

    def market_regime_for(self, symbol: str) -> str:
        """Return only a fresh, successfully classified regime; otherwise fail closed."""
        result = self._market_regimes.get(symbol)
        if not result or result.get("status") != "ok":
            return "UNKNOWN"
        age = time.time() - float(result.get("updated_at", 0.0))
        if age < 0 or age > MARKET_REGIME_MAX_AGE_SECONDS:
            return "UNKNOWN"
        return result["state"]

    def market_regime_status(self) -> Dict[str, dict]:
        return {symbol: dict(result) for symbol, result in self._market_regimes.items()}

    def clear_market_regimes(self) -> None:
        self._market_regimes.clear()

    async def rank_symbols(self, metrics: List[dict]) -> List[str]:
        if not self.enabled:
            return []

        compact_metrics = [
            {
                "symbol": item["symbol"],
                "quant_score": round(item["quant_score"], 4),
                "trades": item["trades"],
                "avg_pnl": round(item["avg_pnl"], 4),
                "win_rate": round(item["win_rate"], 3),
                "stop_rate": round(item["stop_rate"], 3),
                "quote_volume": round(item["quote_volume"], 2),
                "change_pct": round(item["change_pct"], 3),
            }
            for item in metrics
        ]
        payload = {
            "model": "local",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a conservative crypto futures screening assistant. "
                        "Rank only the supplied symbols for suitability to a 5m "
                        "SuperTrend + Keltner breakout strategy. Prefer liquidity, "
                        "stable positive history and moderate movement; penalize high "
                        "stop-loss rate. Do not propose trades, direction or leverage. "
                        "Return exactly one JSON object."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "rank_symbols",
                            "metrics": compact_metrics,
                            "schema": {
                                "ranked_symbols": ["SYMBOL/USDT"],
                                "summary": "short Traditional Chinese explanation",
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": 768,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
        }

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self.request_fn, payload),
                timeout=self.timeout + 2.0,
            )
            self.last_model = str(response.get("model", ""))
            message = response["choices"][0]["message"]
            content = message.get("content") or message.get("reasoning_content", "")
            parsed = self._extract_json(content)
            allowed = {item["symbol"] for item in metrics}
            ranked = []
            for symbol in parsed.get("ranked_symbols", []):
                if symbol in allowed and symbol not in ranked:
                    ranked.append(symbol)
            if not ranked:
                raise ValueError("AI 未回傳有效 ranked_symbols")
            self.last_status = "ok"
            self.last_error = ""
            self.last_summary = str(parsed.get("summary", ""))[:300]
            return ranked
        except (
            asyncio.TimeoutError,
            urllib.error.URLError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self.last_status = "fallback"
            self.last_error = f"{type(exc).__name__}: {exc}"[:300]
            self.last_summary = ""
            return []

    async def analyze_trade_history(self, history: dict) -> dict:
        """分析去識別化交易紀錄；只提供建議，不得直接改參數或下單。"""
        if not self.enabled:
            return {}

        payload = {
            "model": "local",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a conservative crypto futures performance analyst. "
                        "Analyze only the supplied historical trade statistics and samples. "
                        "Find repeatable strengths, loss patterns, stop-loss clusters and "
                        "risk-control improvements for a 5m SuperTrend + Keltner strategy. "
                        "Never place trades, never change settings, and never claim certainty. "
                        "Return exactly one JSON object in Traditional Chinese."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "analyze_trade_history",
                            "history": history,
                            "schema": {
                                "summary": "short Traditional Chinese overview",
                                "strengths": ["observation"],
                                "weaknesses": ["observation"],
                                "recommendations": ["advisory-only improvement"],
                                "risk_flags": ["risk"],
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False},
        }

        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(self.request_fn, payload),
                timeout=self.timeout + 2.0,
            )
            self.last_history_model = str(response.get("model", ""))
            message = response["choices"][0]["message"]
            content = message.get("content") or message.get("reasoning_content", "")
            parsed = self._extract_json(content)
            summary = str(parsed.get("summary", "")).strip()[:500]
            if not summary:
                raise ValueError("AI 未回傳歷史分析摘要")

            def clean_list(name: str) -> List[str]:
                values = parsed.get(name, [])
                if not isinstance(values, list):
                    return []
                return [str(value).strip()[:300] for value in values[:6] if str(value).strip()]

            result = {
                "summary": summary,
                "strengths": clean_list("strengths"),
                "weaknesses": clean_list("weaknesses"),
                "recommendations": clean_list("recommendations"),
                "risk_flags": clean_list("risk_flags"),
            }
            self.last_history_status = "ok"
            self.last_history_error = ""
            self.last_history_summary = summary
            return result
        except (
            asyncio.TimeoutError,
            urllib.error.URLError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self.last_history_status = "fallback"
            self.last_history_error = f"{type(exc).__name__}: {exc}"[:300]
            self.last_history_summary = ""
            return {}

    def history_status(self) -> Dict[str, str]:
        return {
            "enabled": self.enabled,
            "status": self.last_history_status,
            "model": self.last_history_model,
            "summary": self.last_history_summary,
            "error": self.last_history_error,
        }

    def status(self) -> Dict[str, str]:
        return {
            "enabled": self.enabled,
            "status": self.last_status,
            "model": self.last_model,
            "summary": self.last_summary,
            "error": self.last_error,
            "url": self.url,
        }
