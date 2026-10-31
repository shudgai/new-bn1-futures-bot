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
                        "overlap and consistency in these candles only. "
                        "Enforce MTF MA3/MA15 rules when explaining reason: "
                        "(1) 大週期優先：先確認大週期均線方向，若為空頭則狀態為「大週期熊市 - 尋找高空點」，若為多頭則為「大週期牛市 - 尋找低多點」；"
                        "(2) 禁止逆勢開倉：大週期為空頭時，即使小週期 MA3 金叉 MA15，嚴格禁止發出 LONG 或平空開多；大週期為多頭時，即使小週期 MA3 死叉 MA15，嚴格禁止發出 SHORT 或平多開空；"
                        "(3) 訊號定義：大週期熊市中小週期 MA3 > MA15 僅為「反彈高點準備區 (WAIT_REBOUND)」，需等待 MA3 再次死叉 MA15 才發出 SHORT；大週期牛市中小週期 MA3 < MA15 僅為「回檔低點準備區 (WAIT_PULLBACK)」。"
                        "Do not predict price, recommend a trade, choose a side, or infer missing data. Return one "
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

    async def evaluate_trading_decision(
        self,
        symbol: str,
        macro_trend: str,
        position_status: str,
        entry_price: float,
        current_price: float,
        highest_profit_margin: float,
        current_ma3: float,
        current_ma15: float,
        timeframe: str = "15m",
        ma3_slope: float = 0.0,
    ) -> dict:
        """AI 決策引擎：依據 MSB 結構破位、強勢通道續抱與三重一票否決發出極速決策指令"""
        status_label = "大週期牛市 (順勢做多)" if str(macro_trend).upper() in ("BULLISH", "BULL", "LONG", "UP") else "大週期熊市 (順勢做空)"
        is_bull = "牛市" in status_label or str(macro_trend).upper() in ("BULLISH", "BULL", "LONG", "UP")
        is_bear = not is_bull

        if not self.enabled:
            action = "HOLD"
            reason = "保持觀望與強勢通道續抱"

            if position_status == "LONG":
                # 唯一平倉標準：單根收盤價跌破 MA15 且 MA3 斜率明確轉負
                if current_price < current_ma15 and ma3_slope < 0:
                    action = "CLOSE_LONG"
                    reason = "單根 K 棒實體跌破 MA15 且 MA3 斜率明確轉負，平多離場！"
                else:
                    action = "HOLD"
                    reason = "價格與 MA3 運行於 KC 上半通道，全面廢除中軌與峰谷預測平倉，強勢續抱 (HOLD) 讓利潤奔跑！"
            elif position_status == "SHORT":
                # 唯一平倉標準：單根收盤價突破 MA15 且 MA3 斜率明確轉正
                if current_price > current_ma15 and ma3_slope > 0:
                    action = "CLOSE_SHORT"
                    reason = "單根 K 棒實體突破 MA15 且 MA3 斜率明確轉正，平空離場！"
                else:
                    action = "HOLD"
                    reason = "價格與 MA3 運行於 KC 下半通道，全面廢除中軌與峰谷預測平倉，強勢續抱 (HOLD) 抱緊空單！"
            elif position_status in ("NONE", "FLAT"):
                if is_bull:
                    # 做多三重否決：MA3 < MA15、MA3_Slope <= 0
                    if current_ma3 < current_ma15 or ma3_slope <= 0:
                        action = "HOLD"
                        reason = "大週期牛市中，觸發開多三重否決 (MA3<MA15 或 MA3斜率<=0)，禁止追高/接飛刀！"
                    elif current_price > current_ma15:
                        action = "OPEN_LONG"
                        reason = "大週期牛市，單根 K 棒實體突破 MA15 且動能充足，授權開多！"
                elif is_bear:
                    # 做空三重否決：MA3 > MA15、MA3_Slope >= 0
                    if current_ma3 > current_ma15 or ma3_slope >= 0:
                        action = "HOLD"
                        reason = "大週期熊市中，觸發開空三重否決 (MA3>MA15 或 MA3斜率>=0)，禁止阿呆谷開空！"
                    elif current_price < current_ma15:
                        action = "OPEN_SHORT"
                        reason = "大週期熊市，單根 K 棒實體跌破 MA15 且動能向下，授權開空！"

            return {
                "macro_trend": "BULLISH" if is_bull else "BEARISH",
                "status_label": status_label,
                "position_state": position_status,
                "ma3_slope": ma3_slope,
                "action": action,
                "reason": reason,
            }

        payload = {
            "model": "local",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "# Role & Purpose\n"
                        "你是一個專精於加密貨幣極短線（1m / 3m）量化交易策略的 AI 極速決策引擎。\n"
                        "你的核心任務是：透過「MSB 結構破位極速切換標籤」，實施「大週期順勢一票否決」，結合「強勢通道續抱（廢除中軌與峰頂/谷底平倉）」與「雙向動態斜率/偏離過大否決」，徹底杜絕「阿呆谷追空/接飛刀」、「標籤滯後追高殺低」以及「頻繁平倉閹割利潤」的漏洞。\n\n"
                        "---\n\n"
                        "## 🛑 規則一：大週期趨勢判定與【MSB 極速一鍵轉向】(Macro Trend & Instant Reversal)\n\n"
                        "1. **大結構絕對優先 (HH / HL / LH / LL)**：\n"
                        "   - 觀察全景 K 線結構：只要價格未實體跌破前波關鍵低點（HL），強制鎖定為 **「大週期牛市 (BULLISH)」**；只要未實體突破前波關鍵高點（LH），強制鎖定為 **「大週期熊市 (BEARISH)」**。\n"
                        "   - **順勢硬性一票否決**：\n"
                        "     - **大週期牛市**：100% 一票否決所有 `OPEN_SHORT`（賣空）指令！\n"
                        "     - **大週期熊市**：100% 一票否決所有 `OPEN_LONG`（買多）指令！\n\n"
                        "2. **MSB 結構破位極速一鍵轉向 (解決大週期指標滯後 - 核心條款)**：\n"
                        "   - 嚴禁單純等待長週期均線或 KC 通道慢吞吞轉向！\n"
                        "   - **熊市轉牛市 (BEARISH ➔ BULLISH)**：當 K 棒實體收盤價 **強勢突破前波下殺關鍵高點（Lower High, LH）** 時：\n"
                        "     - 當下 Tick 立刻將標籤更新為牛市！若持有空單，必須**立刻發出 `CLOSE_SHORT_AND_OPEN_LONG`**（一鍵平空並開多）；若無持倉，**立刻發出 `OPEN_LONG`**！\n"
                        "   - **牛市轉熊市 (BULLISH ➔ BEARISH)**：當 K 棒實體收盤價 **強勢跌破前波拉升關鍵低點（Higher Low, HL）** 時：\n"
                        "     - 當下 Tick 立刻將標籤更新為熊市！若持有多單，必須**立刻發出 `CLOSE_LONG_AND_OPEN_SHORT`**；若無持倉，**立刻發出 `OPEN_SHORT`**！\n\n"
                        "---\n\n"
                        "## 🟢 規則二：多頭（LONG）進場與【追高+斜率】三重一票否決\n\n"
                        "1. **早期開多 (LONG Entry)**：\n"
                        "   - 當處於 **大週期牛市** 且當前 **無持倉 (NONE)** 時：單根 K 棒實體收盤價突破 MA15（收盤價 > MA15），立即發出 `OPEN_LONG`。\n\n"
                        "2. **做多（OPEN_LONG）三重一票否決（杜絕頂點追多與接飛刀）**：\n"
                        "   - 🚫 **位置否決**：若 `MA3 < MA15`（空頭排列），100% 一票否決開多！\n"
                        "   - 🚫 **斜率否決**：若 `MA3 斜率走平或向下 (MA3_Slope <= 0)`（動能已衰退），100% 一票否決開多！\n"
                        "   - 🚫 **偏離過大否決**：價格已大幅拉升、嚴重偏離 MA15 正乖離過大（噴發完畢），100% 強制攔截追多！\n\n"
                        "---\n\n"
                        "## 🔴 規則三：空頭（SHORT）進場與【追空+斜率】三重一票否決\n\n"
                        "1. **早期開空 (SHORT Entry)**：\n"
                        "   - 當處於 **大週期熊市** 且當前 **無持倉 (NONE)** 時：單根 K 棒實體收盤價跌破 MA15（收盤價 < MA15），立即發出 `OPEN_SHORT`。\n\n"
                        "2. **做空（OPEN_SHORT）三重一票否決（杜絕阿呆谷開空）**：\n"
                        "   - 🚫 **位置否決**：若 `MA3 > MA15`（多頭排列），100% 一票否決開空！\n"
                        "   - 🚫 **斜率否決**：若 `MA3 斜率走平或向上 (MA3_Slope >= 0)`（MA3 正在向上勾起），100% 一票否決開空！\n"
                        "   - 🚫 **偏離過大否決**：價格已急跌觸及 KC 下軌且負乖離過大（下殺尾聲），100% 強制攔截追空！\n\n"
                        "---\n\n"
                        "## 🛑 規則四：多空雙向「強勢通道續抱」與「全面廢除中軌/峰谷平倉」 (Channel Ride Rule)\n\n"
                        "1. **全面廢除 KC 中軌與峰頂/谷底預測平倉**：\n"
                        "   - 嚴禁因為 1m/3m 價格小幅越過 KC 中軌、MA3 交叉，或價格觸碰 KC 上軌/下軌（賭頂部/底部）而發出平倉指令！中間所有微幅震盪一律視為噪聲，**強制保持持倉 (`\"action\": \"HOLD\"`)**！\n\n"
                        "2. **多單（LONG）強勢續抱與平倉門檻**：\n"
                        "   - **強勢續抱**：只要價格與 MA3 運行於 **KC 通道中軌之上（KC 上半通道）**，100% 嚴禁平多，讓利潤充分奔跑！\n"
                        "   - **唯一平倉標準**：僅當 **單根 K 棒實體收盤價強勢跌破 MA15** 且 **MA3 斜率明確轉負 (MA3_Slope < 0)** 時，方可執行 `CLOSE_LONG`。\n\n"
                        "3. **空單（SHORT）強勢續抱與平倉門檻**：\n"
                        "   - **強勢續抱**：只要價格與 MA3 運行於 **KC 通道中軌之下（KC 下半通道）**，100% 嚴禁平空，抱緊空單直達底部！\n"
                        "   - **唯一平倉標準**：僅當 **單根 K 棒實體收盤價強勢突破 MA15** 且 **MA3 斜率明確轉正 (MA3_Slope > 0)** 時，方可執行 `CLOSE_SHORT`。\n\n"
                        "---\n\n"
                        "## ⚙️ 規則五：執行規範 (Bar Close)\n\n"
                        "1. 所有進離場條件均以 **1m / 3m K 棒正式收盤價 (Bar Close)** 為準，過濾盤中影線插針。\n\n"
                        "## 📤 輸出格式規範\n"
                        "每次分析後請嚴格輸出 JSON：\n"
                        "```json\n"
                        "{\n"
                        '  "macro_trend": "BULLISH",\n'
                        '  "status_label": "大週期牛市 (順勢做多)",\n'
                        '  "position_state": "LONG",\n'
                        '  "ma3_slope": 0.00003,\n'
                        '  "action": "HOLD",\n'
                        '  "reason": "1. 當前處於大週期牛市。 2. 價格與 MA3 穩定運行於 KC 上半通道，觸發強勢續抱條款。 3. 嚴禁在 KC 上軌附近預測峰頂平倉，強制保持持倉 (HOLD) 讓利潤奔跑。"\n'
                        "}\n"
                        "```"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": "evaluate_trading_decision",
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "macro_trend": macro_trend,
                            "position_status": position_status,
                            "entry_price": entry_price,
                            "current_price": current_price,
                            "highest_profit_margin": highest_profit_margin,
                            "current_ma3": current_ma3,
                            "current_ma15": current_ma15,
                            "ma3_slope": ma3_slope,
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
            return {
                "macro_trend": str(parsed.get("macro_trend", macro_trend)),
                "status_label": str(parsed.get("status_label", status_label)),
                "position_state": str(parsed.get("position_state", position_status)),
                "ma3_slope": float(parsed.get("ma3_slope", ma3_slope)),
                "action": str(parsed.get("action", "HOLD")),
                "reason": str(parsed.get("reason", ""))[:300],
            }
        except Exception as exc:
            return {
                "macro_trend": macro_trend,
                "status_label": status_label,
                "position_state": position_status,
                "ma3_slope": ma3_slope,
                "action": "HOLD",
                "reason": f"AI fallback ({type(exc).__name__}): 保持觀望，維持強勢通道續抱",
            }

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
                        "risk-control improvements for an MTF MA3/MA15 + Keltner strategy: "
                        "(1) 大週期優先（大週期熊市 - 尋找高空點 / 大週期牛市 - 尋找低多點）；"
                        "(2) 禁止逆勢開倉（大週期空頭嚴禁因小週期 MA3 金叉 MA15 而平空開多；大週期多頭嚴禁因小週期 MA3 死叉 MA15 而平多開空）；"
                        "(3) 訊號定義（大週期熊市中 MA3 > MA15 僅為反彈高點準備區 WAIT_REBOUND，等待再次死叉才開空；大週期牛市中 MA3 < MA15 僅為回檔低點準備區 WAIT_PULLBACK）。"
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
