"""Non-blocking, per-symbol AI chart labels used only as auxiliary entry vetoes."""

import asyncio
import base64
import json
import logging
import os
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

ADVISOR_TTL_SECONDS = 90.0
ADVISOR_TIMEOUT_SECONDS = 2.5
DEFAULT_ADVISOR_URL = "http://100.108.52.6:8888/analyze"
_PROMPT = (
    "Analyze this 1-minute candlestick chart with MA5, MA15 and KC bands. "
    "Return only JSON with boolean fields is_chop, is_exhausted, allow_long, "
    "allow_short, and a short string reason. Treat your output as advisory "
    "labels only; do not invent missing chart evidence."
)


class AIChartAdvisor:
    def __init__(
        self,
        *,
        endpoint: Optional[str] = None,
        enabled: Optional[bool] = None,
        ttl_seconds: float = ADVISOR_TTL_SECONDS,
        timeout_seconds: float = ADVISOR_TIMEOUT_SECONDS,
    ):
        self.endpoint = (
            endpoint
            if endpoint is not None
            else os.getenv("AI_CHART_ADVISOR_URL", DEFAULT_ADVISOR_URL)
        ).strip()
        self.enabled = (
            enabled
            if enabled is not None
            else os.getenv("AI_CHART_ADVISOR_ENABLED", "true").strip().lower()
            in {"true", "1", "yes"}
        )
        self.ttl_seconds = float(ttl_seconds)
        self.timeout_seconds = float(timeout_seconds)
        if self.ttl_seconds <= 0 or self.timeout_seconds <= 0:
            raise ValueError("AI chart advisor TTL and timeout must be positive")
        self._cache: dict[str, dict[str, Any]] = {}
        self._last_scheduled_bar: dict[str, float] = {}
        self._tasks: dict[tuple[str, float], asyncio.Task] = {}

    @staticmethod
    def _symbol_key(symbol: str) -> str:
        return str(symbol or "").strip().upper()

    @staticmethod
    def _parse_labels(response: Any) -> dict[str, Any]:
        if isinstance(response, str):
            response = json.loads(response)
        if not isinstance(response, dict):
            raise ValueError("AI advisor response must be a JSON object")
        required_flags = ("is_chop", "is_exhausted", "allow_long", "allow_short")
        if any(type(response.get(key)) is not bool for key in required_flags):
            raise ValueError("AI advisor response contains missing or invalid flags")
        reason = response.get("reason", "OK")
        if not isinstance(reason, str):
            raise ValueError("AI advisor reason must be a string")
        return {
            **{key: response[key] for key in required_flags},
            "reason": reason[:240],
            "trend": str(response.get("trend") or "UNKNOWN")[:32],
        }

    def schedule_update(
        self,
        symbol: str,
        closed_bar_id: Any,
        closed_frame: Any,
    ) -> bool:
        """Schedule at most one analysis per newly observed, verified closed bar."""
        key = self._symbol_key(symbol)
        try:
            bar_id = float(closed_bar_id)
        except (TypeError, ValueError, OverflowError):
            logger.warning(
                "AI_CHART_ADVISOR_SKIP symbol=%s reason=INVALID_BAR_ID", key,
            )
            return False
        if (
            not key
            or not self.enabled
            or not self.endpoint
            or closed_frame is None
            or getattr(closed_frame, "empty", True)
            or not bar_id > 0
        ):
            return False
        try:
            frame_bar_id = float(closed_frame.iloc[-1]["timestamp"])
            timeframe_ms = closed_frame.attrs.get("timeframe_ms")
            if (
                frame_bar_id != bar_id
                or timeframe_ms != 60_000
                or "is_closed" not in closed_frame
                or not bool(closed_frame["is_closed"].all())
            ):
                logger.warning(
                    "AI_CHART_ADVISOR_SKIP symbol=%s bar=%s "
                    "reason=UNVERIFIED_CLOSED_FRAME",
                    key, bar_id,
                )
                return False
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            logger.warning(
                "AI_CHART_ADVISOR_SKIP symbol=%s bar=%s "
                "reason=INVALID_CLOSED_FRAME",
                key, bar_id,
            )
            return False
        if bar_id <= self._last_scheduled_bar.get(key, 0.0):
            return False
        self._last_scheduled_bar[key] = bar_id
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error(
                "AI_CHART_ADVISOR_SCHEDULE_FAILED symbol=%s bar=%s "
                "reason=ASYNC_LOOP_UNAVAILABLE",
                key, bar_id,
            )
            return False
        task_key = (key, bar_id)
        task = loop.create_task(
            self.update_chart_async(key, bar_id, closed_frame.copy(deep=True)),
        )
        self._tasks[task_key] = task
        task.add_done_callback(
            lambda completed, identity=task_key: self._task_finished(
                identity, completed,
            )
        )
        return True

    def _task_finished(
        self,
        task_key: tuple[str, float],
        task: asyncio.Task,
    ) -> None:
        self._tasks.pop(task_key, None)
        if task.cancelled():
            logger.warning(
                "AI_CHART_ADVISOR_CANCELLED symbol=%s bar=%s",
                *task_key,
            )
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "AI_CHART_ADVISOR_TASK_FAILED symbol=%s bar=%s error=%s",
                *task_key, type(error).__name__,
                exc_info=(type(error), error, error.__traceback__),
            )

    def inspect_state(
        self,
        symbol: str,
        expected_closed_bar_id: Any,
    ) -> Optional[dict[str, Any]]:
        """Return a fresh label set only for the exact currently closed bar."""
        key = self._symbol_key(symbol)
        try:
            expected_bar_id = float(expected_closed_bar_id)
        except (TypeError, ValueError, OverflowError):
            return None
        state = self._cache.get(key)
        if (
            state is None
            or expected_bar_id < state["bar_id"]
            or expected_bar_id - state["bar_id"] > self.ttl_seconds * 1000.0
            or time.monotonic() >= state["expires_at"]
        ):
            return None
        return {
            name: value for name, value in state.items()
            if name not in {"expires_at"}
        }

    async def _render_chart(self, frame: Any) -> bytes:
        from core.vision.chart_guard import render_chart_bytes

        return await asyncio.to_thread(render_chart_bytes, frame)

    async def _request_labels(self, image: bytes) -> dict[str, Any]:
        payload = {
            "image": base64.b64encode(image).decode("ascii"),
            "prompt": _PROMPT,
        }
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self.endpoint, json=payload)
            response.raise_for_status()
            return self._parse_labels(response.json())

    async def update_chart_async(
        self,
        symbol: str,
        closed_bar_id: float,
        closed_frame: Any,
    ) -> bool:
        """Render and analyze in the background; failures leave math gates in control."""
        try:
            image = await self._render_chart(closed_frame)
            labels = await self._request_labels(image)
        except Exception as exc:
            logger.warning(
                "AI_CHART_ADVISOR_FAIL_OPEN symbol=%s bar=%s error=%s",
                symbol, closed_bar_id, type(exc).__name__,
            )
            return False
        key = self._symbol_key(symbol)
        previous = self._cache.get(key)
        if previous is not None and previous["bar_id"] > float(closed_bar_id):
            logger.info(
                "AI_CHART_ADVISOR_RESULT_DISCARDED symbol=%s bar=%s "
                "reason=NEWER_RESULT_ALREADY_CACHED",
                symbol, closed_bar_id,
            )
            return False
        self._cache[key] = {
            **labels,
            "bar_id": float(closed_bar_id),
            "completed_at": time.time(),
            "expires_at": time.monotonic() + self.ttl_seconds,
        }
        logger.info(
            "AI_CHART_ADVISOR_CACHE_UPDATED symbol=%s bar=%s "
            "chop=%s exhausted=%s allow_long=%s allow_short=%s",
            symbol, closed_bar_id, labels["is_chop"], labels["is_exhausted"],
            labels["allow_long"], labels["allow_short"],
        )
        return True


ai_chart_advisor = AIChartAdvisor()
