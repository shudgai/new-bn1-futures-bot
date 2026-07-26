import asyncio
import json
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional


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
            marker = content.find("\"ranked_symbols\"")
            if marker < 0:
                raise
            start = content.rfind("{", 0, marker)
            if start < 0:
                raise
            parsed, _ = json.JSONDecoder().raw_decode(content[start:])
            return parsed

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

    def status(self) -> Dict[str, str]:
        return {
            "enabled": self.enabled,
            "status": self.last_status,
            "model": self.last_model,
            "summary": self.last_summary,
            "error": self.last_error,
            "url": self.url,
        }
