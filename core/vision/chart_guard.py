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
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-3.8-flash").strip()
if GEMINI_MODEL_NAME in {"gemini-2.0-flash", "gemini-2.0-flash-001"}:
    logger.warning(
        "GEMINI_MODEL_NAME=%s is shut down; using gemini-3.8-flash",
        GEMINI_MODEL_NAME,
    )
    GEMINI_MODEL_NAME = "gemini-3.8-flash"
API_KEY_ENV = "GEMINI_API_KEY"
ENTRY_VISION_PROVIDER = os.getenv("ENTRY_VISION_PROVIDER", "GEMINI").strip().upper()
LOOKBACK = 40
REQUEST_TIMEOUT_SEC = 20.0

LOCAL_CONNECT_TIMEOUT_SEC = float(os.getenv("LOCAL_CONNECT_TIMEOUT_SEC", "2.5"))
LOCAL_READ_TIMEOUT_SEC = float(os.getenv("LOCAL_READ_TIMEOUT_SEC", "15.0"))

PROMPT = (
    "你是一位精通合約與妖幣/迷因幣（如龍蝦/CAP）爆發行情的量化審查員。"
    "這是一張 1M 級別的 K 線圖（包含 MA5、MA15、KC 通道與成交量），技術指標剛發出 {side} {signal_desc} 信號。\n"
    "【重要認知：數值引擎已由後端精確計算並產生本模式候選；盤中訊號不代表收線訊號】\n"
    "你的任務是進行宏觀形態防雷審查，防止天花板追多、地板追空、或極端假突破反轉陷阱。\n"
    "本訊號模式的結構檢查：{mode_criteria}\n"
    "【不可放寬的結構性否決條件；任一成立必須判 TRAP】\n"
    "1. 嚴重逆勢：嚴禁多單逆著暴跌主浪摸底，或空單逆著暴漲主浪摸頂。\n"
    "2. MA15 乖離：abs(close-ma15)/ATR > 2.8 時必須拒絕追單。\n"
    "3. K 棒衰竭：多單出現極端長上影線（upper_wick > body * 1.5 且 upper_wick 佔比 > 65%），或空單陰線下影線佔整根 K 棒全長 >65%，必須拒絕。\n"
    "4. 資訊不足、重大缺失或方向不清時，才判 TRAP；模型判斷不得覆蓋任何程式化風控。\n"
    "若未觸發上述任何否決條件、且圖表符合順勢動能推進，必須判 REAL。\n"
    "只回傳 JSON：{{\"verdict\": \"REAL\" 或 \"TRAP\", \"reason\": \"一句話理由\"}}。"
)

SIGNAL_DESC = {
    "INSTANT_BREAK": "即時盤中 KC 外軌突破",
    "NORMAL_BREAK": "兩根收線確認 KC 外軌突破",
    "MA_CROSS": "MA5/MA15 收線交叉",
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
    "INSTANT_BREAK": (
        "當根尚未收線，價格由 KC 通道內穿出同側外軌，且順勢實體達近期平均實體 1.2 倍；"
        "前方必須符合小反色棒蓄勢組合。此入口不檢查 MA5 斜率。"
    ),
    "NORMAL_BREAK": (
        "首根已收線同色實體突破外軌，第二根同色且已收線仍在外軌外；"
        "MA5 斜率順向且 KC 通道擴張。"
    ),
    "MA_CROSS": (
        "已收線 MA5/MA15 剛完成同方向交叉，MA5 順向且 KC 通道擴張；"
        "必須遵循數值 Gate 的趨勢、實體及風控結果。"
    ),
    "BREAKOUT": (
        "數值系統已精確驗證收盤突破 KC 外軌且通道寬度擴張；"
        "審查重點在於防止假突破陷阱與反向極端天線，若無反向長針或嚴重逆勢，應判定合格。"
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


QWEN_OPTION_A_PROMPT = (
    "你是一個專精於短線量價型態與假突破識別的資深加密貨幣交易員。\n"
    "目前底層數值引擎【已透過精確浮點數運算，確認當前 K 線有效突破 Keltner Channel (KC) 軌道且具備通道動能】，無須用肉眼重新校驗微幅破軌或通道像素寬度。\n\n"
    "你的唯一職責是：【審查最新 1~2 根 K 棒是否呈現反向誘單或極端假突破陷阱】。\n\n"
    "【交易訊號資訊】\n"
    "- 幣種：{symbol}\n"
    "- 訊號方向：{side} ({side_desc})\n"
    "- 破軌類型：{breakout_type}\n\n"
    "【審核規則】\n"
    "1. 若為 LONG (做多)：\n"
    "   - 拒絕條件 (REJECTED)：最新 K 線出現極端「衝高回落爆長上影線」（上影線長度 > 實體長度的 2 倍），或當前形成巨大反向大陰線吞噬。\n"
    "   - 放行條件 (APPROVED)：未出現上述極端反向長上影，實體順勢或正常小影線，代表買盤動能未被瞬間瓦解。\n\n"
    "2. 若為 SHORT (做空)：\n"
    "   - 拒絕條件 (REJECTED)：最新 K 線出現極端「探底回升爆長下影線」（下影線長度 > 實體長度的 2 倍），或當前形成巨大反向大陽線吞噬。\n"
    "   - 放行條件 (APPROVED)：未出現上述極端反向長下影，實體順勢或正常小影線，代表賣盤動能未被瞬間承接。\n\n"
    "3. 原則：【預設放行 (Fail-Open to Trade)】。微幅影線、窄幅實體破軌均屬正常動能啟動，切勿因微小的肉眼誤差否決數值系統的判斷。除非看到非常明顯的誘多/誘空反向長針，否則一律核准。\n\n"
    "【輸出格式規範】\n"
    "請嚴格僅回傳以下 JSON 格式，不要包含任何額外說明或 Markdown 標籤：\n"
    '{{\n  "decision": "APPROVED",\n  "reason": "無極端反向長影線，順勢破軌結構成立"\n}}\n'
    "或\n"
    '{{\n  "decision": "REJECTED",\n  "reason": "具體簡述偵測到的反向長影線或反轉型態"\n}}'
)


def _qwen_entry_prompt(side: str, mode: str, symbol: Optional[str] = None) -> str:
    sym = symbol or "合約標的"
    side_desc = "LONG 做多" if side == "LONG" else "SHORT 做空"
    breakout_type = SIGNAL_DESC.get(mode, mode)
    return QWEN_OPTION_A_PROMPT.format(
        symbol=sym,
        side=side,
        side_desc=side_desc,
        breakout_type=breakout_type,
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
    iterrows = getattr(bars, "iterrows", None)
    if callable(iterrows):
        tail = bars.tail(lookback) if callable(getattr(bars, "tail", None)) else bars
        data = [row for _, row in tail.iterrows()]
    else:
        data = list(bars)[-lookback:]
    if not data:
        raise ValueError("no bars to render")
    try:
        import matplotlib
    except ModuleNotFoundError:
        return _render_chart_with_pillow(data, watermark)

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    xs = list(range(len(data)))
    try:
        fig, (ax, axv) = plt.subplots(
            2, 1, figsize=(10, 6), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
        )
        for x, b in zip(xs, data):
            up = b.close >= b.open
            # Match Binance's configured palette: green up candles, red down candles.
            color = "#2ca02c" if up else "#d62728"
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


def _render_chart_with_pillow(data: list[Any], watermark: Optional[str]) -> bytes:
    """Draw a compact candlestick chart when optional Matplotlib is unavailable."""
    from PIL import Image, ImageDraw

    def value(bar: Any, name: str) -> float:
        try:
            return float(bar[name])
        except (TypeError, KeyError, IndexError):
            if name == "volume":
                return 0.0
            return float(getattr(bar, name))

    rows = [
        {
            name: value(bar, name)
            for name in (
                "open", "high", "low", "close", "volume", "ma5", "ma15",
                "kc_upper", "kc_lower",
            )
        }
        for bar in data
    ]
    prices = [row[name] for row in rows for name in (
        "high", "low", "ma5", "ma15", "kc_upper", "kc_lower",
    )]
    low_price, high_price = min(prices), max(prices)
    if high_price <= low_price:
        high_price = low_price + max(abs(low_price) * 1e-6, 1e-9)

    width, height = 1000, 620
    left, right = 58, width - 20
    top, bottom = 32, 450
    volume_top, volume_bottom = 492, height - 35
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((left, top, right, bottom), outline="#bbbbbb")
    draw.rectangle((left, volume_top, right, volume_bottom), outline="#bbbbbb")

    def x_at(index: int) -> float:
        return left + (right - left) * index / max(len(rows) - 1, 1)

    def y_at(price: float) -> float:
        return bottom - (price - low_price) * (bottom - top) / (high_price - low_price)

    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = top + (bottom - top) * fraction
        draw.line((left, y, right, y), fill="#eeeeee", width=1)

    colors = {
        "kc_upper": "#777777", "kc_lower": "#777777",
        "ma5": "#1565c0", "ma15": "#ef6c00",
    }
    for name, color in colors.items():
        points = [(x_at(index), y_at(row[name])) for index, row in enumerate(rows)]
        if len(points) > 1:
            draw.line(points, fill=color, width=2)

    max_volume = max(max(row["volume"], 0.0) for row in rows) or 1.0
    candle_width = max(2, min(14, (right - left) / max(len(rows), 1) * 0.62))
    for index, row in enumerate(rows):
        x = x_at(index)
        rising = row["close"] >= row["open"]
        color = "#c62828" if rising else "#2e7d32"
        draw.line((x, y_at(row["high"]), x, y_at(row["low"])), fill=color, width=1)
        body_top = y_at(max(row["open"], row["close"]))
        body_bottom = y_at(min(row["open"], row["close"]))
        if body_bottom - body_top < 2:
            body_bottom = body_top + 2
        draw.rectangle(
            (x - candle_width / 2, body_top, x + candle_width / 2, body_bottom),
            fill=color,
            outline=color,
        )
        volume_y = volume_bottom - max(row["volume"], 0.0) / max_volume * (
            volume_bottom - volume_top
        )
        draw.rectangle((x - candle_width / 2, volume_y, x + candle_width / 2,
                        volume_bottom), fill=color)

    draw.text((left, 8), "1m candles | KC | MA5 | MA15", fill="#222222")
    if watermark:
        draw.text((left + 260, 8), str(watermark)[:80], fill="#e65100")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def parse_verdict(text: str) -> Tuple[bool, str]:
    """REAL -> approve, TRAP -> veto; anything else raises (treated as outage)."""
    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("unparseable verdict")
    obj = json.loads(raw[start:end + 1])
    verdict = str(obj.get("decision") or obj.get("verdict") or "").strip().upper()
    reason = str(obj.get("reason", "")).strip()
    if verdict in ("REAL", "APPROVED"):
        return True, reason
    if verdict in ("TRAP", "REJECTED"):
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
        verdict = str(obj.get("decision") or obj.get("verdict") or "").strip()
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
    symbol: Optional[str] = None,
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
        _qwen_entry_prompt(side, mode, symbol=symbol)
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
    symbol: Optional[str] = None,
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

    if symbol is None:
        if hasattr(bars, "symbol"):
            symbol = str(bars.symbol)
        elif hasattr(bars, "columns") and "symbol" in bars.columns and len(bars) > 0:
            symbol = str(bars["symbol"].iloc[-1])

    try:
        image = render_chart_bytes(bars)
        response = await asyncio.wait_for(
            _call_local_qwen(
                image,
                proposed_side,
                EntryPatternPermittedGate.normalize(mode),
                symbol=symbol,
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
    """Review an entry chart using the selected vision provider.

    回傳：(approved, reason)
    - GEMINI (default): paid/Free Gemini API using GEMINI_API_KEY.
    - QWEN: local vision only when ENTRY_VISION_PROVIDER=QWEN.
    - Unavailable or invalid review blocks entry unless explicit fail-open is set.
    """
    enable_guard = os.getenv("ENABLE_VISION_GUARD", "true").strip().lower() in ("true", "1", "yes")
    if not enable_guard:
        return False, "VISION_GUARD_DISABLED"

    image = render_chart_bytes(bars)
    use_local = (
        ENTRY_VISION_PROVIDER in {"QWEN", "LOCAL_QWEN"}
        and os.getenv("USE_LOCAL_QWEN", "false").strip().lower()
        in ("true", "1", "yes")
    )

    # --- 第一順位：本地 Qwen2-VL 審查 ---
    if use_local:
        try:
            local_text = await _call_local_qwen(image, proposed_side, mode)
            if local_text:
                approved, reason = parse_verdict(local_text)
                logger.info("視覺審查來源 [本地Qwen]: %s (approved=%s)", reason, approved)
                return approved, reason
        except Exception as exc:
            logger.warning("本地 Qwen 審查失敗: %s", type(exc).__name__)

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
        # Vision review is fail-closed by default; do not silently bypass it.
        logger.warning(
            "Gemini Vision review failed (%s: %s); entry remains blocked unless fail-open is explicitly enabled",
            type(exc).__name__, exc,
        )
        if _fail_open():
            return True, f"VISION_UNAVAILABLE: FALLBACK_INDICATOR_ONLY ({type(exc).__name__})"
        return False, f"VISION_UNAVAILABLE: {type(exc).__name__}"
