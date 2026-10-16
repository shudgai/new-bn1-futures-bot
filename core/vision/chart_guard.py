"""Chart Vision Guard: Dual-track (Local Qwen2-VL -> Cloud Gemini -> Fallback Indicator) with Dynamic Breakthrough Gatekeeper."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)
LONG = "LONG"
SHORT = "SHORT"

# --- 模型與端點設定 ---
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-3.8-flash")
API_KEY_ENV = "GEMINI_API_KEY"
LOOKBACK = 40
REQUEST_TIMEOUT_SEC = 20.0

LOCAL_CONNECT_TIMEOUT_SEC = 1.5
LOCAL_READ_TIMEOUT_SEC = 8.0

PROMPT = (
    "你是一位精通合約與妖幣/迷因幣（如龍蝦/CAP）爆發行情的量化審查員。"
    "這是一張 1M 級別的 K 線圖（包含 MA5、MA15、KC 通道與成交量），技術指標剛發出 {side} {signal_desc} 信號。\n"
    "本訊號模式的結構檢查：{mode_criteria}\n"
    "【不可放寬的結構性否決條件；任一成立必須判 TRAP】\n"
    "2. MA15 乖離：abs(close-ma15)/ATR > 2.8 時必須拒絕追單。\n"
    "3. K 棒衰竭：多單出現極端長上影線（upper_wick > body * 1.5 且 upper_wick 佔比 > 65%），或空單陰線下影線佔整根 K 棒全長 >65%，必須拒絕。\n"
    "4. 資訊不足、KC 寬度無效、ATR/MA15 缺失或方向不清時，一律判 TRAP；不可猜測或使用純指標備援批准。\n"
    "只有未觸發以上任何否決條件、且圖表確實支持候選方向時才判 REAL。模型判斷不得覆蓋任何程式化風控。\n"
    "只回傳 JSON：{{\"verdict\": \"REAL\" 或 \"TRAP\", \"reason\": \"一句話理由\"}}。"
)

SIGNAL_DESC = {
    "BREAKOUT": "KC 外軌破軌開倉",
    "CONTINUATION": "KC 外軌延續或中軌回踩轉向",
    "CROSSOVER": "MA5/MA15 交叉開倉",
    "REVERSAL": "確認峰谷反轉開倉",
    "MA_SWING": "MA5/MA15 交叉",
    "BREAKOUT_MOMENTUM": "破軌突破",
    "BASELINE_BREAK_AND_MA_CROSS": "通道基準突破與 MA 交叉",
    "TREND_CONTINUATION": "趨勢延續",
    "PULLBACK_MOMENTUM": "通道內順勢起漲",
    "SHORT_CONTINUATION": "通道內回踩延續開空",
    "EARLY_REVERSAL": "真轉向微觀領先提早進場",
    "CONTINUATION_ENTRY": "平倉後外軌延續開倉",
    "POST_CLOSE_CONTINUATION": "平倉後外軌延續開倉",
}

MODE_CRITERIA = {
    "BREAKOUT": (
        "收盤須突破同側 KC 外軌，方向中軌與外軌同步，通道寬度擴張；"
        "不得把通道持平或反向訊號判為有效破軌。"
    ),
    "CONTINUATION": (
        "順勢延續可位於多單 KC_Pos >= 0.35 或空單 KC_Pos <= 0.65，"
        "涵蓋同側外軌推進及 0.35-0.65 中軌回踩區；拒絕極端反向長影線（>65%）。"
    ),
    "CROSSOVER": (
        "必須有已收線 MA5/MA15 真正交叉及價格基準確認；"
        "允許順勢動能發起位，僅在出現反向極端長影線（>65%）時拒絕。"
    ),
    "REVERSAL": (
        "必須有 KC 外確認三點峰谷、右側突破樞紐 K 棒，及 MA5/MA15 交叉確認；"
        "未確認樞紐或極端反向長影線一律拒絕。"
    ),
}


def _vision_prompt(side: str, mode: str) -> str:
    mode = str(mode or "").upper()
    return PROMPT.format(
        side=side,
        signal_desc=SIGNAL_DESC.get(mode, "進場"),
        mode_criteria=MODE_CRITERIA.get(
            mode,
            "遵循現有已確認的方向訊號，不得用圖表印象覆蓋程式化 Gate。",
        ),
    )


def _qwen_entry_prompt(side: str, mode: str) -> str:
    prompt = _vision_prompt(side, mode).replace("TRAP", "REJECTED").replace(
        "REAL", "APPROVED",
    )
    return (
        prompt
        + "\n視覺審查是必要下單授權：只有所有檢查明確通過時才回傳 "
        '{"verdict":"APPROVED","reason":"..."}；否則回傳 '
        '{"verdict":"REJECTED","reason":"..."}。不得回傳 REAL、TRAP 或其他狀態。'
    )


# ==============================================================================
# 1. 審查觸發的「動態破局解鎖過濾器」 (Dynamic Breakthrough Gatekeeper)
# ==============================================================================
@dataclass
class RejectionContext:
    """記錄被 AI 審查拒絕時的關鍵結構點。"""
    symbol: str
    side: str
    rejected_bar_high: float    # 拒絕那根 K 棒的最高價 (做多破局標準)
    rejected_bar_low: float     # 拒絕那根 K 棒的最低價 (做空破局標準)
    rejected_bar_close: float
    bars_since_reject: int = 0  # 拒絕後完整收線之 K 棒根數


class DynamicVisionGatekeeper:
    """Require a fresh structure after rejection, except for confirmed MA turns."""

    def __init__(self, min_bars_interval: int = 2):
        self.min_bars_interval = min_bars_interval
        self._rejections: Dict[str, RejectionContext] = {}

    def on_bar_closed(self, symbol: str) -> None:
        """每根 1M K 棒收線時呼叫，推進計數器。"""
        if symbol in self._rejections:
            context = self._rejections[symbol]
            context.bars_since_reject += 1
            if context.bars_since_reject > 5:
                del self._rejections[symbol]
                logger.info(
                    "✅ [VISION_CLEARED] %s 動態拒絕鎖超過 5 根完整 K 棒，自動失效",
                    symbol,
                )

    @staticmethod
    def _is_confirmed_ma_turn(
        entry_pattern: Optional[str],
        structure_confirmation: Optional[str],
        structure_confirmed: bool,
    ) -> bool:
        if not structure_confirmed:
            return False
        pattern = str(entry_pattern or "").strip().upper()
        confirmation = str(structure_confirmation or "").strip().upper()
        if pattern in {"CROSSOVER", "REVERSAL"}:
            return confirmation in {
                "MA5_MA15_1M_CROSS",
                "MA5_MA15_5M_CONVERGENCE",
                "CONFIRMED_KC_OUTER_PIVOT_THEN_MA5_MA15_CROSS",
            }
        return False

    def clear_for_confirmed_ma_turn(
        self,
        symbol: str,
        *,
        entry_pattern: Optional[str] = None,
        structure_confirmation: Optional[str] = None,
        structure_confirmed: bool = False,
    ) -> bool:
        """Clear historical rejection state as soon as a confirmed MA turn appears."""
        if not self._is_confirmed_ma_turn(
            entry_pattern, structure_confirmation, structure_confirmed,
        ):
            return False
        if symbol in self._rejections:
            del self._rejections[symbol]
            logger.info(
                "✅ [VISION_CLEARED] %s 已確認 MA5/MA15 轉向，清除舊動態拒絕鎖",
                symbol,
            )
        return True

    def can_submit_for_vision(
        self,
        symbol: str,
        current_bar: Any,
        proposed_side: str,
        *,
        entry_pattern: Optional[str] = None,
        structure_confirmation: Optional[str] = None,
        structure_confirmed: bool = False,
    ) -> Tuple[bool, str]:
        """判定當前是否滿足送審條件。"""
        if self.clear_for_confirmed_ma_turn(
            symbol,
            entry_pattern=entry_pattern,
            structure_confirmation=structure_confirmation,
            structure_confirmed=structure_confirmed,
        ):
            return True, "UNLOCKED_CONFIRMED_MA_TURN"

        if symbol not in self._rejections:
            return True, "CLEAN_STATE"

        ctx = self._rejections[symbol]

        # 若方向與上次被拒相反，視為全新結構放行
        if proposed_side != ctx.side:
            del self._rejections[symbol]
            logger.info(
                "✅ [VISION_CLEARED] %s 出現相反方向候選，清除舊動態拒絕鎖",
                symbol,
            )
            return True, "OPPOSITE_SIDE_RESET"

        # 規則 A：最小 K 棒間隔（至少 2 根完整收線）
        if ctx.bars_since_reject < self.min_bars_interval:
            return (
                False,
                f"BLOCKED_INTERVAL: 距上次拒絕僅過 {ctx.bars_since_reject}/{self.min_bars_interval} 根 K 棒",
            )

        # 規則 B：市場結構破局突破
        if proposed_side == LONG:
            if current_bar.close <= ctx.rejected_bar_high:
                return (
                    False,
                    f"BLOCKED_STRUCTURE_LONG: 收盤 {current_bar.close:.5f} <= 拒絕高點 {ctx.rejected_bar_high:.5f} (未破局)",
                )
            return True, f"UNLOCKED_BREAK_HIGH: 突破拒絕高點 {ctx.rejected_bar_high:.5f}"

        elif proposed_side == SHORT:
            if current_bar.close >= ctx.rejected_bar_low:
                return (
                    False,
                    f"BLOCKED_STRUCTURE_SHORT: 收盤 {current_bar.close:.5f} >= 拒絕低點 {ctx.rejected_bar_low:.5f} (未破局)",
                )
            return True, f"UNLOCKED_BREAK_LOW: 跌破拒絕低點 {ctx.rejected_bar_low:.5f}"

        return False, "UNKNOWN_SIDE"

    def record_rejection(self, symbol: str, bar: Any, side: str) -> None:
        """AI 判定 TRAP / REJECTED 時調用。"""
        self._rejections[symbol] = RejectionContext(
            symbol=symbol,
            side=side,
            rejected_bar_high=bar.high,
            rejected_bar_low=bar.low,
            rejected_bar_close=bar.close,
            bars_since_reject=0,
        )
        logger.info(
            "🛡️ [VISION_DYNAMIC_LOCK] %s %s 被拒，錨定防守區 [%.5f, %.5f]，需等待間隔與結構突破才可再次送審（最多 5 根 K）",
            symbol, side, bar.low, bar.high,
        )

    def record_approved(self, symbol: str) -> None:
        """AI 判定 APPROVED 時清除鎖定。"""
        if symbol in self._rejections:
            del self._rejections[symbol]
            logger.info("✅ [VISION_CLEARED] %s 通過審查，清除動態鎖定", symbol)

    def reset(self, symbol: Optional[str] = None) -> None:
        """重置狀態（供測試或手動調試使用）。"""
        if symbol is None:
            self._rejections.clear()
        else:
            self._rejections.pop(symbol, None)


# 全域單例
dynamic_vision_gate = DynamicVisionGatekeeper(min_bars_interval=2)


# ==============================================================================
# 2. 圖表繪製與視覺狀態標記 (Chart Rendering)
# ==============================================================================
def render_chart_bytes(
    bars: Any,
    lookback: int = LOOKBACK,
    watermark: Optional[str] = None,
) -> bytes:
    """Render the last `lookback` bars to PNG bytes entirely in memory with optional status watermark."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    iterrows = getattr(bars, "iterrows", None)
    if callable(iterrows):
        tail = bars.tail(lookback) if callable(getattr(bars, "tail", None)) else bars
        data = [row for _, row in tail.iterrows()]
    else:
        data = list(bars)[-lookback:]
    if not data:
        raise ValueError("no bars to render")
    xs = list(range(len(data)))
    try:
        fig, (ax, axv) = plt.subplots(
            2, 1, figsize=(10, 6), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
        )
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

        if watermark:
            ax.text(
                0.5, 0.94, watermark,
                transform=ax.transAxes,
                fontsize=11, fontweight="bold",
                color="#e65100" if "降級" in watermark else "#2e7d32",
                ha="center", va="top",
                bbox=dict(boxstyle="round,pad=0.3", facecolor="#ffffff", alpha=0.85, edgecolor="#cccccc"),
            )

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


def parse_qwen_entry_approval(text: str) -> Tuple[bool, str]:
    """Only an explicit APPROVED verdict from the configured local Qwen is valid."""
    try:
        raw = (text or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            return False, "AI_INVALID_RESPONSE"
        obj = json.loads(raw[start:end + 1])
        verdict = str(obj.get("verdict", "")).strip()
        reason = str(obj.get("reason", "")).strip()
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return False, "AI_INVALID_RESPONSE"
    if verdict == "APPROVED":
        return True, reason or "QWEN_APPROVED"
    if verdict == "REJECTED":
        return False, reason or "QWEN_REJECTED"
    return False, "AI_EXPLICIT_APPROVAL_REQUIRED"


# ==============================================================================
# 3. 本地優先：Qwen2-VL (OpenAI 相容協定 + Port 自動輪巡)
# ==============================================================================
async def _call_local_qwen_once(client: httpx.AsyncClient, base_url: str, model_name: str, payload: dict) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    resp = await client.post(url, json=payload)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


async def _call_local_qwen(
    image: bytes,
    side: str,
    mode: str,
    *,
    require_explicit_approval: bool = False,
) -> Optional[str]:
    """優先呼叫本地 Qwen2-VL，支援多 Port (8000 -> 11434) 自動候補。"""
    primary_url = os.getenv("LOCAL_QWEN_URL", "http://127.0.0.1:8000/v1")
    model_name = os.getenv("LOCAL_QWEN_MODEL", "qwen2-vl")

    candidates: List[str] = [primary_url]
    fallback_ollama = "http://127.0.0.1:11434/v1"
    if fallback_ollama not in candidates:
        candidates.append(fallback_ollama)

    b64_image = base64.b64encode(image).decode("utf-8")
    prompt_text = (
        _qwen_entry_prompt(side, mode)
        if require_explicit_approval else _vision_prompt(side, mode)
    )

    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}},
                ],
            }
        ],
        "temperature": 0.0,
        "max_tokens": 128,
    }

    timeout = httpx.Timeout(LOCAL_READ_TIMEOUT_SEC, connect=LOCAL_CONNECT_TIMEOUT_SEC)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in candidates:
            try:
                logger.debug("嘗試連線本地 Qwen2-VL: %s (model: %s)", url, model_name)
                return await _call_local_qwen_once(client, url, model_name, payload)
            except Exception as exc:
                logger.warning("本地 Qwen2-VL 連線失敗 (%s): %s，切換下一候選端點", url, type(exc).__name__)

    return None


async def evaluate_qwen_entry_vision(
    bars: Any,
    proposed_side: str,
    *,
    mode: str,
    timeout: Optional[float] = None,
) -> Tuple[bool, str]:
    """Require a fresh local Qwen-VL review; never use cloud or indicator fallback."""
    from core.gates.entry_pattern_permitted_gate import EntryPatternPermittedGate

    permitted, reason = EntryPatternPermittedGate.check_pattern(mode)
    if not permitted:
        return False, reason
    if proposed_side not in (LONG, SHORT):
        return False, "AI_INVALID_ENTRY_SIDE"
    if os.getenv("ENABLE_VISION_GUARD", "true").strip().lower() not in (
        "true", "1", "yes",
    ):
        return False, "VISION_GUARD_DISABLED"
    if os.getenv("USE_LOCAL_QWEN", "false").strip().lower() not in (
        "true", "1", "yes",
    ):
        return False, "LOCAL_QWEN_REQUIRED"

    try:
        image = render_chart_bytes(bars)
        response = await asyncio.wait_for(
            _call_local_qwen(
                image,
                proposed_side,
                EntryPatternPermittedGate.normalize(mode),
                require_explicit_approval=True,
            ),
            timeout=timeout or LOCAL_READ_TIMEOUT_SEC + LOCAL_CONNECT_TIMEOUT_SEC,
        )
    except Exception as exc:
        logger.warning(
            "本地 Qwen-VL 審查失敗 (%s): %s",
            type(exc).__name__, exc,
        )
        return False, f"QWEN_REVIEW_FAILED:{type(exc).__name__}"
    if not response:
        return False, "QWEN_REVIEW_UNAVAILABLE"
    return parse_qwen_entry_approval(response)


# ==============================================================================
# 4. 雲端備援：Gemini Vision
# ==============================================================================
def _call_gemini(image: bytes, side: str, api_key: str, mode: str = "BREAKOUT_MOMENTUM") -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(
        model=GEMINI_MODEL_NAME,
        contents=[
            types.Part.from_bytes(data=image, mime_type="image/png"),
            _vision_prompt(side, mode),
        ],
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0),
    )
    return resp.text or ""


MAX_ATTEMPTS = 3
RETRY_DELAY_SEC = 1.0
RETRYABLE_CODES = (500, 502, 503, 504)


async def _call_gemini_with_retry(image: bytes, side: str, api_key: str, mode: str) -> str:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await asyncio.to_thread(_call_gemini, image, side, api_key, mode)
        except Exception as exc:
            transient = getattr(exc, "code", None) in RETRYABLE_CODES
            if not transient or attempt == MAX_ATTEMPTS:
                raise
            logger.info("chart vision transient error %s, retry %d/%d", getattr(exc, "code", None), attempt, MAX_ATTEMPTS - 1)
            await asyncio.sleep(RETRY_DELAY_SEC)
    raise RuntimeError("unreachable")


def _fail_open() -> bool:
    return os.getenv("VISION_FAIL_OPEN", "0") == "1"


# ==============================================================================
# 5. 核心對外整合入口 (三順位安全防護)
# ==============================================================================
async def evaluate_breakout_vision(
    bars: Any,
    proposed_side: str,
    timeout: Optional[float] = None,
    mode: str = "BREAKOUT_MOMENTUM",
) -> Tuple[bool, str]:
    """雙軌視覺審查主入口 (本地優先 -> Gemini 備援 -> 純指標降級)。

    回傳：(approved, reason)
    - 第一順位：若 USE_LOCAL_QWEN=true，嘗試本地 Qwen (8000 -> 11434)。
    - 第二順位：若本地未開啟或連線失敗，無縫降級至雲端 Gemini Vision。
    - 第三順位：若 Gemini 亦發生 429 或配額用罄，自動降級為純指標模式 (FALLBACK_INDICATOR_ONLY)。
    """
    enable_guard = os.getenv("ENABLE_VISION_GUARD", "true").strip().lower() in ("true", "1", "yes")
    if not enable_guard:
        return False, "VISION_GUARD_DISABLED"

    image = render_chart_bytes(bars)
    use_local = os.getenv("USE_LOCAL_QWEN", "false").strip().lower() in ("true", "1", "yes")

    # --- 第一順位：本地 Qwen2-VL 審查 ---
    if use_local:
        try:
            local_text = await _call_local_qwen(image, proposed_side, mode)
            if local_text:
                approved, reason = parse_verdict(local_text)
                logger.info("視覺審查來源 [本地Qwen]: %s (approved=%s)", reason, approved)
                return approved, reason
        except Exception as exc:
            logger.warning("本地 Qwen 審查過程異常: %s，自動降級至 Gemini 備援", type(exc).__name__)

    # --- 第二順位：雲端 Gemini Vision 備援 ---
    api_key = os.getenv(API_KEY_ENV, "").strip()
    if not api_key:
        if _fail_open():
            logger.warning("⚠️ AI 審查降級 (純指標運行中: GEMINI_API_KEY 未設定)")
            return True, "VISION_UNAVAILABLE: FALLBACK_INDICATOR_ONLY (GEMINI_API_KEY not set)"
        return False, "VISION_UNAVAILABLE: GEMINI_API_KEY not set"

    try:
        gemini_text = await asyncio.wait_for(
            _call_gemini_with_retry(image, proposed_side, api_key, mode),
            timeout=timeout or REQUEST_TIMEOUT_SEC,
        )
        approved, reason = parse_verdict(gemini_text)
        logger.info("視覺審查來源 [雲端Gemini]: %s (approved=%s)", reason, approved)
        return approved, reason
    except Exception as exc:
        # --- 第三順位：完全降級（純指標模式，不阻斷交易） ---
        logger.warning(
            "⚠️ 雲端 Gemini Vision 異常 (%s: %s)，自動啟動第三順位：純技術指標降級運行",
            type(exc).__name__, exc,
        )
        if _fail_open():
            return True, f"VISION_UNAVAILABLE: FALLBACK_INDICATOR_ONLY ({type(exc).__name__})"
        return False, f"VISION_UNAVAILABLE: {type(exc).__name__}"
