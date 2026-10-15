"""Chart Vision Guard: Gemini veto on breakout entries (in-memory chart, no disk files)."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
from typing import Optional, Sequence, Tuple

from core.swing.models import Bar

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-3.5-flash"
API_KEY_ENV = "GEMINI_API_KEY"
LOOKBACK = 40
REQUEST_TIMEOUT_SEC = 20.0

PROMPT = (
    "你是一個高頻量化波段審查員。這是一張 1M 級別的 K 線圖（包含 MA5、MA15、KC 軌道與成交量），"
    "目前技術指標剛發出 {side} {signal_desc}信號。\n"
    "請依據形態學判定：這是『真實強勢突破（放量起漲、剛出發）』，"
    "還是『誘多/誘空陷阱（衝高回落長影線、縮量假突破、已連續大漲跌的末端追價、盤整區內的假破、"
    "橫盤均線纏繞的假金叉/假死叉、滯後追高追空）』。\n"
    "只回傳 JSON：{{\"verdict\": \"REAL\" 或 \"TRAP\", \"reason\": \"一句話理由\"}}。"
)


def render_chart_bytes(bars: Sequence[Bar], lookback: int = LOOKBACK) -> bytes:
    """Render the last `lookback` bars to PNG bytes entirely in memory."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = list(bars)[-lookback:]
    if not data:
        raise ValueError("no bars to render")
    xs = list(range(len(data)))
    try:
        fig, (ax, axv) = plt.subplots(
            2, 1, figsize=(10, 6), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
        for x, b in zip(xs, data):
            up = b.close >= b.open
            color = "#d62728" if up else "#2ca02c"
            ax.vlines(x, b.low, b.high, color=color, linewidth=1)
            lo, hi = sorted((b.open, b.close))
            ax.bar(x, max(hi - lo, 1e-12), bottom=lo, width=0.6, color=color)
            axv.bar(x, b.volume, width=0.6, color=color)
        ax.plot(xs, [b.ma5 for b in data], color="#1f77b4", linewidth=1.2, label="MA5")
        ax.plot(xs, [b.ma15 for b in data], color="#ff7f0e", linewidth=1.2, label="MA15 (KC mid)")
        ax.plot(xs, [b.kc_upper for b in data], color="#7f7f7f", linestyle="--", linewidth=1, label="KC upper")
        ax.plot(xs, [b.kc_lower for b in data], color="#7f7f7f", linestyle="--", linewidth=1, label="KC lower")
        ax.legend(loc="upper left", fontsize=7)
        axv.set_ylabel("Volume")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        return buf.getvalue()
    finally:
        plt.close("all")


def parse_verdict(text: str) -> Tuple[bool, str]:
    """REAL -> approve, TRAP -> veto; anything else raises (treated as outage)."""
    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("unparseable verdict")
    obj = json.loads(raw[start:end + 1])
    verdict = str(obj.get("verdict", "")).strip().upper()
    reason = str(obj.get("reason", "")).strip()
    if verdict == "REAL":
        return True, reason
    if verdict == "TRAP":
        return False, reason
    raise ValueError("unknown verdict")


SIGNAL_DESC = {"MA_SWING": "MA5/MA15 交叉", "BREAKOUT_MOMENTUM": "破軌突破"}


def _call_gemini(image: bytes, side: str, api_key: str, mode: str = "BREAKOUT_MOMENTUM") -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=MODEL_NAME,
        contents=[
            types.Part.from_bytes(data=image, mime_type="image/png"),
            PROMPT.format(side=side, signal_desc=SIGNAL_DESC.get(mode, "進場")),
        ],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0),
    )
    return resp.text or ""


MAX_ATTEMPTS = 3
RETRY_DELAY_SEC = 1.0
RETRYABLE_CODES = (429, 500, 502, 503, 504)


async def _call_with_retry(image: bytes, side: str, api_key: str, mode: str) -> str:
    """Retry transient Gemini overload/server errors; other errors raise at once."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(_call_gemini, image, side, api_key, mode)
        except Exception as exc:
            transient = getattr(exc, "code", None) in RETRYABLE_CODES
            if not transient or attempt == MAX_ATTEMPTS:
                raise
            logger.info("chart vision transient error %s, retry %d/%d", exc.code, attempt, MAX_ATTEMPTS - 1)
            await asyncio.sleep(RETRY_DELAY_SEC)
    raise RuntimeError("unreachable")


def _fail_open() -> bool:
    return os.getenv("VISION_FAIL_OPEN", "1") != "0"


async def evaluate_breakout_vision(
    bars: Sequence[Bar], proposed_side: str, timeout: Optional[float] = None,
    mode: str = "BREAKOUT_MOMENTUM",
) -> Tuple[bool, str]:
    """Return (approved, reason). Only an explicit TRAP verdict vetoes.

    Missing key, API error, timeout or unparseable reply is an outage, not a
    pattern judgement: approve (fail-open) unless VISION_FAIL_OPEN=0.
    """
    api_key = os.getenv(API_KEY_ENV, "").strip()
    if not api_key:
        return _fail_open(), "VISION_UNAVAILABLE: GEMINI_API_KEY not set"
    try:
        image = render_chart_bytes(bars)
        text = await asyncio.wait_for(
            _call_with_retry(image, proposed_side, api_key, mode),
            timeout=timeout or REQUEST_TIMEOUT_SEC,
        )
        approved, reason = parse_verdict(text)
        return approved, reason or ("REAL" if approved else "TRAP")
    except Exception as exc:  # outage must never break the trading loop
        logger.warning("chart vision unavailable: %s", type(exc).__name__)
        return _fail_open(), f"VISION_UNAVAILABLE: {type(exc).__name__}"
