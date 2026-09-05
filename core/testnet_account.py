import asyncio
import json
import math
import os
import time
import ccxt.async_support as ccxt
import re
from datetime import datetime
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.config import (
    BINANCE_API_KEY,
    BINANCE_SECRET,
    TAKER_FEE_RATE,
    SLIPPAGE_PCT,
    MAX_DAILY_LOSS_PCT,
    MIN_OPEN_SIGNAL_SCORE,
    DEFAULT_SYMBOLS,
    ENABLE_TRAILING_STOP,
    ENABLE_EARLY_PROFIT_GUARD,
    ENABLE_PROFIT_GIVEBACK_EXIT,
    EARLY_PROFIT_GUARD_TRIGGER_PCT,
    EARLY_PROFIT_GUARD_EXIT_PCT,
    BOUNCE_EARLY_PROFIT_GUARD_TRIGGER_PCT,
    BOUNCE_EARLY_PROFIT_GUARD_EXIT_PCT,
    TRAILING_TRIGGER_PCT,
    TRAILING_CALLBACK_PCT,
    TRAILING_TRIGGER_R_MULT,
    TRAILING_CALLBACK_R_MULT,
    CONTRARIAN_TRAILING_TRIGGER_PCT,
    TRAILING_PULLBACK_PCT,
    NET_PROFIT_GUARANTEE_BUFFER,
    ENABLE_PROFIT_BANK,
    PROFIT_BANK_TRIGGER_PCT,
    PROFIT_BANK_LOCK_PCT,
    PROFIT_BANK_CAPTURE_RATIO,
    get_profit_bank_capture_ratio,
    PROFIT_BANK_MIN_STEP_PCT,
    ENABLE_FIXED_PROFIT_LOCK_PCT, FIXED_PROFIT_LOCK_TRIGGER_PCT,
    FIXED_PROFIT_LOCK_FLOOR_PCT,
    get_trailing_pullback_pct,
    PROFIT_ALERT_GIVEBACK_RATIO,
    PROFIT_ALERT_MIN_PEAK_PCT,
    get_leverage,
    get_signal_leverage,
    DISABLE_TAKE_PROFIT,
    ENABLE_EXCHANGE_INITIAL_STOP_LOSS,
    DISABLE_STOP_LOSS,
    ONLY_CLOSE_ON_PROFIT,
    ONLY_CLOSE_ON_PROFIT_MIN_NET_USDT,
    CLOSE_ON_PROFIT_MIN_PNL_TO_FEE_RATIO,
    ENABLE_24H_TIME_FILTER,
    USE_NATIVE_TRAILING_STOP,
    NATIVE_TRAILING_ATR_RATE_FACTOR,
    NATIVE_TRAILING_TIER1_CALLBACK_MIN,
    NATIVE_TRAILING_TIER1_CALLBACK_MAX,
    NATIVE_TRAILING_TIER2_CALLBACK_MIN,
    NATIVE_TRAILING_TIER2_CALLBACK_MAX,
    NATIVE_TRAILING_TIER3_CALLBACK_MIN,
    NATIVE_TRAILING_TIER3_CALLBACK_MAX,
    TRAILING_TIER1_TRIGGER_ATR_MULT,
    TRAILING_TIER2_TRIGGER_ATR_MULT,
    TRAILING_TIER3_TRIGGER_ATR_MULT,
    TRAILING_TIER2_LOCK_ATR_MULT,
    MAX_ACCEPTABLE_LOSS_PCT,
    MAX_POSITION_MARGIN_LOSS_RATIO,
    cap_stop_loss_to_margin_risk,
    MIN_SL_DISTANCE_PCT,
    STOP_LOSS_MULTIPLIER,
    ENABLE_DCA_LIMIT,
    DCA_STAGE_DEPTHS,
    get_bounce_capture_ratio,
    BOUNCE_NO_FOLLOW_THROUGH_SEC,
    BOUNCE_NO_FOLLOW_THROUGH_MIN_MFE_PCT,
    ENABLE_RAPID_ADVERSE_DROP,
    RAPID_ADVERSE_DROP_PCT,
    RAPID_DROP_COOLDOWN_SEC,
    ENABLE_FIXED_PROFIT_LOCK_LADDER,
    FIXED_PROFIT_LOCK_LADDER_STEP_PCT,
    FIXED_PROFIT_LOCK_LADDER_FIRST_PCT,
    ENABLE_PROFIT_LOCK_USDT,
    OUTER_RUN_NET_GIVEBACK_USDT,
    compute_channel_swing_profit_lock_usdt,
    ENABLE_BOUNCE_TARGET_EXIT,
    EXHAUSTION_SNIPER_GRACE_SEC, EXHAUSTION_SNIPER_STOP_LOSS_PCT,
)
from core.strategy import compute_sl_tp_distance, validate_sl_tp_pair
from core.notifier import notify_email


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "testnet_account.json")
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
ENTRY_CONTEXT_KEYS = (
    "btc_regime_at_entry", "btc_direction_1h_at_entry", "btc_score_penalty",
    "btc_allocation_factor", "btc_pre_penalty_score",
    "raw_signal_score", "btc_adjusted_score", "history_adjusted_score",
    "history_score_multiplier", "pullback_confirmation_score", "entry_mode",
    "is_contrarian_bottom_buy", "initial_sl", "initial_risk",
    "signal_candle_low", "signal_candle_high",
    "channel_turn_low", "channel_turn_high",
    "profit_profile", "profit_room_pct",
    "bounce_capture_ratio", "bounce_target_pct",
    "structured_net_rr", "high_readiness_low_room",
    "low_room_allocation_factor",
    "dca_stage", "dca_base_price", "dca_original_amount", "wave_regime",
    "channel_entry_profile", "channel_entry_profile_basis",
    "profit_lock_usdt_v2",
)


def get_taipei_now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return datetime.now(TAIPEI_TZ).strftime(fmt)


def get_taipei_time_short() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%H:%M:%S")


class BinanceTestnetAccount:
    """Binance USDM legacy testnet execution account.

    Public market data remains on the production public endpoint. Only account,
    position and order calls use the authenticated testnet exchange passed in by
    the engine.
    """

    def __init__(self, exchange):
        self.exchange = exchange
        self.balance = 0.0
        self.available_balance = 0.0
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.positions: Dict[str, dict] = {}
        self.trades: List[dict] = []
        self.logs: List[dict] = []
        self.position_meta: Dict[str, dict] = {}
        self.closing_lock: set = set()
        self.on_trade_closed: Optional[Callable[[], None]] = None
        self.last_sync_at = 0.0
        self._markets_loaded = False
        self._orphan_protection_attempted: set = set()
        self.daily_date: Optional[str] = None
        self.daily_start_balance: float = 0.0
        self.daily_start_realized_pnl: float = 0.0
        self.daily_halt_logged: bool = False
        # 平倉時間唯一資料來源：不管平倉是主迴圈觸發還是 /api/prices、
        # /api/status 這些網頁輪詢呼叫觸發，都會記在這裡。原本冷卻時間是
        # engine.py 自己拿「這輪呼叫前後持倉少了誰」來判斷，但平倉如果是
        # 被網頁輪詢（跟主迴圈完全不同步、各自獨立呼叫 update_positions）
        # 觸發的，主迴圈的前後快照根本不會注意到，冷卻就完全不會生效。
        self.last_closed_at: Dict[str, float] = {}
        self._auto_close_reject_logged_at: Dict[tuple, float] = {}
        # 回踩漏斗事件與未成交原因；保存於既有 state，重啟後持續累積。
        self.pullback_outcome_stats: Dict[str, int] = {}
        # 初始開倉漏斗與各幣最新斷點；與交易狀態一起保存，重啟不歸零。
        self.entry_filter_stats: Dict[str, dict] = {
            "evaluations": 0, "outcomes": {}, "components": {}, "adjustments": {},
        }
        self.entry_filter_last: Dict[str, dict] = {}
        # 影子參數只比較候選資格，不下單；統計與每幣最新結果跨重啟保存。
        self.shadow_parameter_stats: Dict[str, dict] = {"evaluations": 0, "profiles": {}}
        self.shadow_parameter_last: Dict[str, dict] = {}
        # 真正掛在交易所的限價回調進場單追蹤，取代原本「軟體輪詢價格到了
        # 再送市價單」的 pending_pullbacks（見 engine.py）。keyed by symbol，
        # 一個 symbol 同時最多一張掛單。
        self.pending_limit_orders: Dict[str, dict] = {}
        # 同一 symbol 反覆掛單-撤單（見 place_limit_entry/cancel_pending_limit）
        # 時，只印第一次「掛單中」，之後同一個 symbol 連續沒成交就不再重複
        # 印掛單/撤銷——同一個 symbol 一直顯示卻沒有新結果，畫面上只是雜訊。
        # 真正成交時一定會印（見 _finalize_new_position 的開倉成功訊息），
        # 撤單/重掛的邏輯本身完全不受影響，只是省略中間重複的日誌行；
        # 想看目前是不是還在等，「📊 12幣訊號進度」摘要裡本來就有顯示。
        self._pending_retry_streak: Dict[str, int] = {}
        # ✅ 修正 Bug2：平倉失敗後的冷卻計時器，防止網路抖動期間連續暴力重試 API
        self._close_retry_after: Dict[str, float] = {}
        # 閃崩偵測：記錄上一次各 symbol 的 ticker 價格，用於計算單週期逆向幅度
        self._last_ticker_prices: Dict[str, float] = {}
        # 閃崩偵測：記錄各 symbol 上次觸發閃崩平倉的時間戳（冷卻計時）
        self._rapid_drop_cooldown: Dict[str, float] = {}
        self.tickers: Dict[str, float] = {}
        self._load_state()

    @staticmethod
    def credentials_configured() -> bool:
        return bool(BINANCE_API_KEY and BINANCE_SECRET)

    @staticmethod
    def _raw_symbol(symbol: str) -> str:
        return symbol.upper().replace("/", "").replace(":USDT", "")

    @staticmethod
    def _clean_symbol(symbol: str) -> str:
        raw = str(symbol).upper().replace("/", "").replace(":USDT", "")
        return f"{raw[:-4]}/USDT" if raw.endswith("USDT") else raw



    def _load_state(self) -> None:
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.realized_pnl = float(data.get("realized_pnl", 0.0))
            self.trades = data.get("trades", [])
            self.logs = data.get("logs", [])
            self.position_meta = data.get("position_meta", {})
            self.daily_date = data.get("daily_date")
            self.daily_start_balance = float(data.get("daily_start_balance", 0.0))
            self.daily_start_realized_pnl = float(data.get("daily_start_realized_pnl", 0.0))
            self.daily_halt_logged = bool(data.get("daily_halt_logged", False))
            self.last_closed_at = {
                str(k): float(v) for k, v in data.get("last_closed_at", {}).items()
            }
            self.pullback_outcome_stats = {
                str(k): int(v) for k, v in data.get("pullback_outcome_stats", {}).items()
            }
            loaded_filter_stats = data.get("entry_filter_stats", {})
            if isinstance(loaded_filter_stats, dict):
                self.entry_filter_stats.update(loaded_filter_stats)
            loaded_filter_last = data.get("entry_filter_last", {})
            if isinstance(loaded_filter_last, dict):
                self.entry_filter_last = loaded_filter_last
            loaded_shadow_stats = data.get("shadow_parameter_stats", {})
            if isinstance(loaded_shadow_stats, dict):
                self.shadow_parameter_stats.update(loaded_shadow_stats)
            loaded_shadow_last = data.get("shadow_parameter_last", {})
            if isinstance(loaded_shadow_last, dict):
                self.shadow_parameter_last = loaded_shadow_last
        except Exception:
            pass

    def save_state(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        now_ts = time.time()
        last_closed_at = {
            symbol: ts for symbol, ts in self.last_closed_at.items()
            if now_ts - ts < 3600
        }
        payload = {
            "environment": "binance_usdm_legacy_testnet",
            "realized_pnl": self.realized_pnl,
            "trades": self.trades,
            "logs": self.logs[-200:],
            "position_meta": self.position_meta,
            "daily_date": self.daily_date,
            "daily_start_balance": self.daily_start_balance,
            "daily_start_realized_pnl": self.daily_start_realized_pnl,
            "daily_halt_logged": self.daily_halt_logged,
            "last_closed_at": last_closed_at,
            "pullback_outcome_stats": self.pullback_outcome_stats,
            "entry_filter_stats": self.entry_filter_stats,
            "entry_filter_last": self.entry_filter_last,
            "shadow_parameter_stats": self.shadow_parameter_stats,
            "shadow_parameter_last": self.shadow_parameter_last,
        }
        tmp_file = f"{STATE_FILE}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp_file, STATE_FILE)
        except Exception:
            pass

    def log(self, message: str, level: str = "INFO") -> None:
        # 視覺層過濾：將 'Mandatory_Fail: KEY(...)' 顯示成括號內的中文說明，或移除前綴並替換下劃線
        if isinstance(message, str) and "Mandatory_Fail:" in message:
            m = re.search(r"Mandatory_Fail:\s*[A-Za-z0-9_]+\(([^)]*)\)", message)
            if m:
                message = message.replace(m.group(0), m.group(1))
            else:
                message = re.sub(r"Mandatory_Fail:\s*", "", message).replace("_", " ")

        self.logs.append({
            "time": get_taipei_time_short(),
            "timestamp": time.time(),
            "text": message,
            "level": level,
        })
        self.save_state()
        if level == "DANGER":
            notify_email(f"[Binance Bot] {message}")

    def _check_daily_reset(self) -> None:
        """台北時區跨日就重置今日虧損熔斷的計算基準。"""
        today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
        if self.daily_date != today:
            self.daily_date = today
            self.daily_start_balance = self.balance
            self.daily_start_realized_pnl = self.realized_pnl
            self.daily_halt_logged = False

    def daily_loss_limit_hit(self) -> tuple:
        """回傳 (是否觸發熔斷, 今日虧損百分比)。觸發時只暫停開新倉，
        既有持倉的止損/止利不受影響，隔天（台北時區）自動重置。"""
        if self.daily_start_balance <= 0:
            return False, 0.0
        daily_pnl = self.realized_pnl - self.daily_start_realized_pnl
        loss_pct = max(0.0, -daily_pnl / self.daily_start_balance * 100.0)
        # 0 或負數代表測試時明確停用每日熔斷。
        hit = MAX_DAILY_LOSS_PCT > 0 and loss_pct >= MAX_DAILY_LOSS_PCT
        if hit and not self.daily_halt_logged:
            self.daily_halt_logged = True
            self.log(
                f"🛑 [每日熔斷] 今日已實現虧損 {loss_pct:.1f}%，達到門檻 "
                f"{MAX_DAILY_LOSS_PCT:.0f}%，暫停開新倉（既有持倉仍正常管理），"
                f"明日（台北時區）自動重置",
                "DANGER",
            )
        return hit, loss_pct

    def get_available_balance(self) -> float:
        return max(0.0, self.available_balance)

    def get_wallet_balance(self) -> float:
        return self.balance

    async def initialize(self) -> None:
        if not self.credentials_configured():
            raise RuntimeError("8006 Testnet API Key 尚未設定")
        await self.exchange.load_markets()
        self._markets_loaded = True
        await self._cancel_orphan_entry_orders()
        await self.refresh(force=True)
        await self._restore_exchange_initial_stops()

    async def _cancel_orphan_entry_orders(self) -> None:
        """開機時清掉「軟體重啟後失去追蹤，但交易所還留著」的孤兒進場限價單。
        place_limit_entry() 掛的是一般 LIMIT 單；SL/TP 保護單是另一種
        algoOrder（CONDITIONAL）類型，不會被這裡誤刪。如果機器人剛好在
        限價單還沒成交時重啟，pending_limit_orders 這個記憶體追蹤會被
        清空，但單子還留在交易所——放著不管，之後萬一意外成交，就會變成
        一個沒有止損止盈保護的裸倉。開機時主動清掉這種殘留單，之後由
        正常的訊號掃描重新評估要不要掛新單。

        Binance USDM 合約的 fetchOpenOrders() 不指定 symbol 時會被限流拒絕
        （"fetching open orders without specifying a symbol is rate-limited"），
        所以逐幣種查詢目前牌面（DEFAULT_SYMBOLS）——涵蓋絕大多數會發生的
        情況，牌面之外的舊幣種孤兒單機率很低，先不處理。"""
        for symbol in DEFAULT_SYMBOLS:
            try:
                open_orders = await self.exchange.fetch_open_orders(symbol)
            except Exception as exc:
                self.log(f"⚠️ 檢查 {symbol} 孤兒掛單失敗（略過）：{type(exc).__name__}: {exc}", "WARNING")
                continue
            for order in open_orders:
                if order.get("type") != "limit":
                    continue
                try:
                    await self.exchange.cancel_order(order["id"], symbol)
                    self.log(
                        f"🧹 [孤兒掛單清理] 取消重啟前殘留的限價單 {symbol} "
                        f"{order.get('side')} @ {order.get('price')}",
                        "WARNING",
                    )
                except Exception as exc:
                    self.log(f"⚠️ 清理孤兒掛單失敗 {symbol}：{type(exc).__name__}: {exc}", "WARNING")
            await asyncio.sleep(0.05)

    async def _recover_order_after_timeout(
        self, symbol: str, order_side: str, price_str: str
    ) -> Optional[dict]:
        """create_order 逾時（執行狀態未知）後，查詢交易所目前掛單，確認
        這筆訂單究竟有沒有真的送出去。重試幾次是因為交易所端處理延遲的單
        可能要一兩秒才會出現在 fetch_open_orders() 裡，不是查一次沒有就
        代表真的沒掛上。"""
        for _ in range(3):
            await asyncio.sleep(1.0)
            try:
                open_orders = await self.exchange.fetch_open_orders(symbol)
            except Exception:
                continue
            for order in open_orders:
                if (
                    order.get("type") == "limit"
                    and str(order.get("side", "")).lower() == order_side
                    and abs(float(order.get("price") or 0.0) - float(price_str)) < 1e-9
                ):
                    return order
        return None

    async def refresh(self, force: bool = False) -> float:
        now = time.time()
        if not force and now - self.last_sync_at < 5.0:
            return self.unrealized_pnl

        previous = dict(self.positions)
        balance_rows = await self.exchange.fapiPrivateV2GetBalance()
        usdt = next((row for row in balance_rows if row.get("asset") == "USDT"), {})
        self.balance = float(usdt.get("balance") or 0.0)
        self.available_balance = float(usdt.get("availableBalance") or self.balance)
        self._check_daily_reset()

        raw_positions = await self.exchange.fapiPrivateV2GetPositionRisk()
        active = {}
        for row in raw_positions:
            signed_qty = float(row.get("positionAmt") or 0.0)
            if abs(signed_qty) <= 0:
                continue
            symbol = self._clean_symbol(row.get("symbol", ""))
            side = "LONG" if signed_qty > 0 else "SHORT"
            qty = abs(signed_qty)
            entry_price = float(row.get("entryPrice") or 0.0)
            mark_price = float(row.get("markPrice") or entry_price)
            leverage = int(float(row.get("leverage") or get_leverage(symbol)))
            meta = self.position_meta.get(symbol, {})
            # 獲利了結參考提醒（純顯示，不影響三階段自動移動停利）：目前
            # 無槓桿浮盈跟至今最高浮盈（跟 update_positions() 的
            # highest_pnl_pct 共用同一個 meta 欄位）比較，回吐超過
            # PROFIT_ALERT_GIVEBACK_RATIO 就標記提醒，讓使用者比自動
            # Tier3 機制更早看到「已經從高點回落不少」。
            pnl_pct = (
                (mark_price - entry_price) / entry_price if side == "LONG"
                else (entry_price - mark_price) / entry_price
            ) if entry_price > 0 else 0.0
            peak_pnl_pct = max(meta.get("highest_pnl_pct", pnl_pct), pnl_pct)
            profit_giveback_ratio = (
                (peak_pnl_pct - pnl_pct) / peak_pnl_pct if peak_pnl_pct > 0 else 0.0
            )
            profit_alert = pnl_pct > 0 and profit_giveback_ratio >= PROFIT_ALERT_GIVEBACK_RATIO
            active[symbol] = {
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "qty": qty,
                "margin": abs(entry_price * qty) / max(leverage, 1),
                "leverage": leverage,
                "sl": float(meta.get("sl") or 0.0),
                "tp": float(meta.get("tp") or 0.0),
                "atr": float(meta.get("atr") or entry_price * 0.015),
                "open_timestamp": float(meta.get("open_timestamp") or now),
                "open_time": meta.get("open_time") or get_taipei_now_str(),
                "reason": meta.get("reason") or "Binance Testnet existing position",
                "signal_score": meta.get("signal_score"),
                **{key: meta.get(key) for key in ENTRY_CONTEXT_KEYS},
                "mark_price": mark_price,
                "liquidation_price": float(row.get("liquidationPrice") or 0.0),
                "unrealized_pnl": float(row.get("unRealizedProfit") or 0.0),
                "peak_pnl_pct": peak_pnl_pct,
                "profit_alert": profit_alert,
            }

        self.positions = active
        self.unrealized_pnl = sum(
            float(position.get("unrealized_pnl") or 0.0)
            for position in active.values()
        )
        self.last_sync_at = now

        for symbol, old_position in previous.items():
            if symbol in active or symbol in self.closing_lock:
                continue
            await self._record_external_close(symbol, old_position)

        active_symbols = set(active)
        for symbol in list(self.position_meta):
            if symbol not in active_symbols and symbol not in self.closing_lock:
                self.position_meta.pop(symbol, None)
        self.save_state()
        return self.unrealized_pnl

    @staticmethod
    def _protection_type_from_order(order: dict) -> Optional[str]:
        info = order.get("info") if isinstance(order.get("info"), dict) else {}
        values = [
            order.get("type"), order.get("orderType"), order.get("origType"),
            info.get("type"), info.get("orderType"), info.get("origType"),
        ]
        normalized = " ".join(str(value).upper() for value in values if value)
        if "TAKE_PROFIT" in normalized:
            return "TP"
        if "STOP" in normalized:
            return "SL"
        return None

    async def _classify_external_close(
        self, symbol: str, position: dict, closing_fills: List[dict], close_price: float
    ) -> tuple[str, str]:
        """優先讀實際成交訂單類型；API 未提供時才用 SL/TP 觸發價兜底。"""
        for fill in closing_fills:
            exit_type = self._protection_type_from_order(fill)
            if exit_type:
                break
            info = fill.get("info") if isinstance(fill.get("info"), dict) else {}
            order_id = fill.get("order") or fill.get("orderId") or info.get("orderId")
            if not order_id or not hasattr(self.exchange, "fetch_order"):
                continue
            try:
                order = await self.exchange.fetch_order(str(order_id), symbol)
                exit_type = self._protection_type_from_order(order)
                if exit_type:
                    break
            except Exception:
                continue
        else:
            exit_type = None

        if not exit_type:
            sl = float(position.get("sl") or 0.0)
            tp = float(position.get("tp") or 0.0)
            tolerance = abs(close_price) * 0.002
            if position.get("side") == "LONG":
                if tp > 0 and close_price >= tp - tolerance:
                    exit_type = "TP"
                elif sl > 0 and close_price <= sl + tolerance:
                    exit_type = "SL"
            else:
                if tp > 0 and close_price <= tp + tolerance:
                    exit_type = "TP"
                elif sl > 0 and close_price >= sl - tolerance:
                    exit_type = "SL"

        # 移動保本／移動停利仍是透過 STOP 類訂單成交，但它不是原始虧損
        # 止損。只要保護線已移到進場價有利側，或實際成交價仍在有利側，
        # 就獨立標記為獲利保護，避免停損率把正收益出場算成策略失敗。
        if exit_type == "SL":
            side = str(position.get("side") or "").upper()
            entry_price = float(position.get("entry_price") or 0.0)
            sl = float(position.get("sl") or 0.0)
            favorable_stop = (
                (side == "LONG" and sl > entry_price)
                or (side == "SHORT" and 0 < sl < entry_price)
            ) if entry_price > 0 else False
            favorable_fill = (
                (side == "LONG" and close_price > entry_price)
                or (side == "SHORT" and 0 < close_price < entry_price)
            ) if entry_price > 0 else False
            if favorable_stop or favorable_fill:
                exit_type = "PROFIT_PROTECT"

        reasons = {
            "TP": "Binance Testnet 止盈單成交 (Take-Profit)",
            "SL": "Binance Testnet 止損單成交 (Stop-Loss)",
            "PROFIT_PROTECT": "Binance Testnet 獲利保護單成交 (Profit-Protect)",
        }
        return exit_type or "OTHER", reasons.get(exit_type, "Binance Testnet 外部平倉（類型未識別）")

    async def _record_external_close(self, symbol: str, position: dict) -> None:
        if symbol in self.closing_lock:
            return
        self.closing_lock.add(symbol)
        self.last_closed_at[symbol] = time.time()
        try:
            await self._cancel_all_orders(symbol)
            self.positions.pop(symbol, None)
            # position["mark_price"]/["unrealized_pnl"] 是上一次 refresh()（最多5秒前）
            # 快取的近似值，不是真正觸發保護單當下的成交價，會跟實際損益有落差。
            # 改成向交易所查詢最近的實際成交紀錄，取真正的平倉均價來算損益。
            close_price = float(position.get("mark_price") or position["entry_price"])
            closing_fills: List[dict] = []
            try:
                close_side = "sell" if position["side"] == "LONG" else "buy"
                recent_trades = await self.exchange.fetch_my_trades(symbol, limit=5)
                cutoff_ms = (time.time() - 300) * 1000
                closing_fills = [
                    t for t in recent_trades
                    if t.get("side") == close_side and (t.get("timestamp") or 0) >= cutoff_ms
                ]
                fill_qty = sum(float(t["amount"]) for t in closing_fills)
                if fill_qty > 0:
                    close_price = sum(float(t["price"]) * float(t["amount"]) for t in closing_fills) / fill_qty
            except Exception:
                pass  # 查詢失敗就退回用快取的 mark_price 估算
            exit_type, exit_reason = await self._classify_external_close(
                symbol, position, closing_fills, close_price
            )
            # 損益改用 Binance 官方的已實現損益帳本（REALIZED_PNL income）為準，
            # 不再自己拿成交明細反推——反推容易把移動止利換單過程中的零碎成交
            # 也算進去，導致跟實際損益對不上。close_price 只用來顯示、算手續費。
            raw_pnl = None
            try:
                income_rows = await self.exchange.fapiPrivateGetIncome({
                    "symbol": self._raw_symbol(symbol),
                    "incomeType": "REALIZED_PNL",
                    "limit": 20,
                })
                cutoff_ms = (time.time() - 300) * 1000
                recent_pnl_rows = [
                    r for r in income_rows if float(r.get("time", 0)) >= cutoff_ms
                ]
                if recent_pnl_rows:
                    raw_pnl = sum(float(r.get("income", 0)) for r in recent_pnl_rows)
            except Exception:
                pass
            if raw_pnl is None:
                if position["side"] == "LONG":
                    raw_pnl = (close_price - position["entry_price"]) * position["qty"]
                else:
                    raw_pnl = (position["entry_price"] - close_price) * position["qty"]
            open_fee = position["entry_price"] * position["qty"] * TAKER_FEE_RATE
            close_fee = close_price * position["qty"] * TAKER_FEE_RATE
            total_fee = open_fee + close_fee
            net_pnl = raw_pnl - total_fee
            self.realized_pnl += net_pnl
            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"CLOSE_{position['side']}",
                "side": position["side"],
                "price": close_price,
                "qty": position["qty"],
                "amount": position.get("margin", 0.0),
                "fee": round(total_fee, 4),
                "pnl": round(net_pnl, 4),
                "status": "CLOSED",
                "reason": exit_reason,
                "exit_type": exit_type,
                **{key: position.get(key) for key in ENTRY_CONTEXT_KEYS},
            })
            self.log(
                f"🏁 Binance Testnet 已平倉 [{position['side']}] {symbol} | {exit_reason} | "
                f"損益: {net_pnl:+.2f} USDT (手續費: {total_fee:.4f})",
                "SUCCESS" if net_pnl >= 0 else "DANGER",
            )
            if self.on_trade_closed:
                try:
                    self.on_trade_closed()
                except Exception:
                    pass
        finally:
            self.closing_lock.discard(symbol)
    async def update_positions(self, ticker_prices: Dict[str, float]) -> float:
        self.tickers = ticker_prices
        await self.refresh()

        for symbol, pos in list(self.positions.items()):
            curr_p = ticker_prices.get(symbol) or ticker_prices.get(f"{symbol}:USDT") or ticker_prices.get(symbol.replace('/USDT', ''))
            if curr_p is None:
                continue
            # 交易所拿標記價格（Mark Price）算浮動損益、也拿標記價格觸發
            # 止損止盈（見 _create_protection_order 的 workingType=MARK_PRICE），
            # 但 curr_p 是最新成交價（ticker 的 last），兩者本來就有基差。
            # 移動停利要收緊到什麼程度、判斷行情走了多少，必須跟交易所
            # 實際觸發的基準一致，才不會發生「策略覺得沒動，但標記價格
            # 早就達標」的資訊落差——這裡改用標記價格為主，沒有標記價格
            # 時才退回最新成交價。
            mark_p = pos.get("mark_price") or curr_p

            meta = self.position_meta.get(symbol, {})
            side = pos["side"]
            entry_p = pos["entry_price"]

            # symbol 還有我們自己在追蹤的限價掛單時，就算交易所那邊已經
            # 部分成交、看起來像「持倉但沒保護單」，也不要插手——這種情況
            # 交給 check_pending_limit_orders()/_finalize_new_position() 統一
            # 處理，避免兩邊搶著建立不一致的止損止盈單。
            if (
                pos.get("sl", 0.0) <= 0
                and str(pos.get("entry_mode") or meta.get("entry_mode") or "").upper()
                    != "CHANNEL_SWING"
                and symbol not in self._orphan_protection_attempted
                and symbol not in self.pending_limit_orders
            ):
                # ✅ 修正 Bug1：先呼叫，成功後才加入集合
                # 原本先 add() 再呼叫：若 _create_orphan_protection 內部 except 靜默失敗，
                # symbol 永遠留在集合裡，裸倉無法再次觸發補建，直到 bot 重啟。
                await self._create_orphan_protection(symbol, pos, meta)
                if meta.get("sl", 0.0) > 0:
                    # 保護單確實建立（meta["sl"] 有值）才標記，失敗時下輪自動重試
                    self._orphan_protection_attempted.add(symbol)

            old_sl = pos.get("sl", 0.0)
            now_ts = time.time()

            liq_p = pos.get("liquidation_price", 0.0)
            if liq_p > 0:
                dist_pct = (mark_p - liq_p) / mark_p if side == "LONG" else (liq_p - mark_p) / mark_p
                if dist_pct < 0.05:
                    self.log(f"🚨🚨🚨 [強平警戒] {symbol} {side} 距離強平價僅剩 {dist_pct:.2%}！強平價: {liq_p:.6g}, 當前價: {mark_p:.6g}", "DANGER")

            # Backward compatibility for MomentumCross positions created
            # before the signal carried an explicit trend profit profile.
            entry_mode = pos.get("entry_mode") or meta.get("entry_mode")
            profit_profile = pos.get("profit_profile") or meta.get("profit_profile")
            bounce_target = float(
                pos.get("bounce_target_pct") or meta.get("bounce_target_pct") or 0.0
            )
            if (
                entry_mode == "MOMENTUM_CROSS"
                and profit_profile in (None, "BOUNCE")
                and bounce_target <= 0
            ):
                profit_profile = "TREND_EXTENSION"
                pos["profit_profile"] = profit_profile
                meta["profit_profile"] = profit_profile

            pnl_pct = (
                (mark_p - entry_p) / entry_p
                if side == "LONG" else (entry_p - mark_p) / entry_p
            )
            stored_highest_pnl = meta.get("highest_pnl_pct")
            highest_pnl = max(
                float(stored_highest_pnl) if stored_highest_pnl is not None else pnl_pct,
                pnl_pct,
            )
            if stored_highest_pnl is None or highest_pnl > float(stored_highest_pnl):
                meta["highest_pnl_pct"] = highest_pnl
                meta["peak_profit_updated_at"] = now_ts
            wave_regime = str(
                pos.get("wave_regime") or meta.get("wave_regime") or ""
            ).upper()
            is_structure_exit_mode = wave_regime in ("RANGE", "TREND")

            # OUTER_RUN峰谷出現前完全不停利；峰谷出現後等待正式出場期間，
            # 若最高淨利固定回吐1U，才以保護性例外提前平倉。
            outer_run_active = bool(
                pos.get("outer_run_active") or meta.get("outer_run_active")
            )
            if ENABLE_PROFIT_LOCK_USDT and (outer_run_active or is_structure_exit_mode):
                qty = float(pos.get("qty") or 0.0)
                notional_value = qty * entry_p
                round_trip_fee = notional_value * TAKER_FEE_RATE * 2.0
                estimated_net_usdt = (
                    pnl_pct * notional_value - round_trip_fee
                    - notional_value * SLIPPAGE_PCT
                )
                peak_net_key = "outer_run_peak_net_usdt"
                peak_net_usdt = max(
                    float(meta.get(peak_net_key) or estimated_net_usdt),
                    estimated_net_usdt,
                )
                meta[peak_net_key] = peak_net_usdt
                giveback_usdt = OUTER_RUN_NET_GIVEBACK_USDT
                outer_run_protect = bool(
                    not is_structure_exit_mode
                    and outer_run_active
                    and (
                        pos.get("outer_run_pivot_protect_armed")
                        or meta.get("outer_run_pivot_protect_armed")
                    )
                )
                kc_structure_protect = False
                if (
                    (outer_run_protect or kc_structure_protect)
                    and peak_net_usdt > 0.0
                    and peak_net_usdt - estimated_net_usdt >= giveback_usdt
                ):
                    protect_scope = "OUTER_RUN" if outer_run_protect else "KC峰谷後"
                    await self.close_position(
                        symbol, mark_p,
                        f"{protect_scope}最高淨利回吐{giveback_usdt:.2f}U保護平倉",
                    )
                    continue
            exhaustion_grace = (
                entry_mode in ("EXHAUSTION_SNIPER", "PIVOT_TURN")
                and now_ts - float(pos.get("open_timestamp") or meta.get("open_timestamp") or now_ts)
                < EXHAUSTION_SNIPER_GRACE_SEC
            )
            if exhaustion_grace:
                # 交易所的 1.2% STOP_MARKET 繼續有效；前三分鐘不移動保護線，
                # 也不執行任何獲利／技術型出場。
                continue
            # RANGE／TREND 結構出場交由主引擎；Channel Swing 額外只保留大瀑布防護。
            is_channel_swing = str(entry_mode or "").upper() == "CHANNEL_SWING"
            if is_structure_exit_mode or is_channel_swing:
                channel_profit_lock_v2 = bool(
                    is_channel_swing
                    and ENABLE_PROFIT_LOCK_USDT
                    and (pos.get("profit_lock_usdt_v2") or meta.get("profit_lock_usdt_v2"))
                )
                if channel_profit_lock_v2:
                    qty = float(pos.get("qty") or 0.0)
                    notional_value = qty * entry_p
                    gross_usdt = pnl_pct * notional_value
                    estimated_cost_usdt = notional_value * (
                        2.0 * TAKER_FEE_RATE + SLIPPAGE_PCT
                    )
                    peak_key = "profit_lock_v2_peak_gross_usdt"
                    peak_gross_usdt = max(
                        float(meta.get(peak_key) or gross_usdt), gross_usdt,
                    )
                    meta[peak_key] = peak_gross_usdt
                    momentum_declining = bool(
                        pos.get("channel_momentum_declining")
                        or meta.get("channel_momentum_declining")
                    )
                    plan = compute_channel_swing_profit_lock_usdt(
                        peak_gross_usdt, estimated_cost_usdt, momentum_declining,
                    )
                    if plan is not None and qty > 0.0:
                        floor_usdt, _activation_usdt, completed_steps = plan
                        floor_move = floor_usdt / qty
                        floor_sl = (
                            entry_p + floor_move
                            if side == "LONG" else entry_p - floor_move
                        )
                        floor_already_breached = (
                            mark_p <= floor_sl if side == "LONG" else mark_p >= floor_sl
                        )
                        if floor_already_breached:
                            await self.close_position(
                                symbol, mark_p, "Channel Swing U階梯應鎖利潤已回吐",
                            )
                            continue
                        current_sl = float(pos.get("sl") or meta.get("sl") or 0.0)
                        improves = (
                            floor_sl > current_sl + entry_p * 1e-12
                            if side == "LONG"
                            else current_sl <= 0.0 or floor_sl < current_sl - entry_p * 1e-12
                        )
                        if improves and await self.trail_stop_loss(
                            symbol, floor_sl, mark_profit_locked=True,
                        ):
                            meta["profit_lock_usdt_armed"] = True
                            pos["profit_lock_usdt_armed"] = True
                            meta["profit_lock_mode"] = "CHANNEL_V2_2U"
                            pos["profit_lock_mode"] = "CHANNEL_V2_2U"
                            self.log(
                                f"🔐 [Channel U階梯鎖利] {symbol} 峰值毛利 {peak_gross_usdt:.2f}U "
                                f"→ 鎖毛利 {floor_usdt:.2f}U（估計成本 {estimated_cost_usdt:.2f}U，"
                                f"淨利至少 {floor_usdt - estimated_cost_usdt:.2f}U，"
                                f"趨勢衰退階梯 {completed_steps}），保護線 {floor_sl:.6g}",
                                "SUCCESS",
                            )
                # Channel Swing 不使用固定或移動停利，也不使用一般停損；
                # 新版標記倉位另使用 U 階梯；所有倉位保留逆向大瀑布保護。
                if is_channel_swing and ENABLE_RAPID_ADVERSE_DROP:
                    prev_p = self._last_ticker_prices.get(symbol)
                    last_cd = self._rapid_drop_cooldown.get(symbol, 0.0)
                    if prev_p and prev_p > 0 and (now_ts - last_cd) > RAPID_DROP_COOLDOWN_SEC:
                        adverse_move = (
                            (prev_p - mark_p) / prev_p
                            if side == "LONG" else (mark_p - prev_p) / prev_p
                        )
                        if adverse_move >= RAPID_ADVERSE_DROP_PCT:
                            self.log(
                                f"⚡ [Channel Swing 大瀑布保護] {symbol} {side} "
                                f"單次 ticker 急速逆向 {adverse_move:.3%}，立即平倉",
                                "DANGER",
                            )
                            self._rapid_drop_cooldown[symbol] = now_ts
                            self._last_ticker_prices[symbol] = mark_p
                            await self.close_position(
                                symbol, mark_p, "Channel Swing 急速逆向大瀑布保護平倉"
                            )
                            continue
                    self._last_ticker_prices[symbol] = mark_p
                continue
            bounce_capture_ratio = float(
                pos.get("bounce_capture_ratio")
                or meta.get("bounce_capture_ratio")
                or 0.0
            )
            bounce_target_pct = float(
                pos.get("bounce_target_pct") or meta.get("bounce_target_pct") or 0.0
            )
            if meta.get("profit_profile") == "BOUNCE" and bounce_target_pct <= 0:
                profit_room_pct = float(
                    pos.get("profit_room_pct") or meta.get("profit_room_pct") or 0.0
                )
                if profit_room_pct > 0:
                    bounce_capture_ratio = get_bounce_capture_ratio(
                        int(pos.get("signal_score") or meta.get("signal_score") or 75)
                    )
                    bounce_target_pct = profit_room_pct * bounce_capture_ratio
                    pos["bounce_capture_ratio"] = bounce_capture_ratio
                    pos["bounce_target_pct"] = bounce_target_pct
                    meta["bounce_capture_ratio"] = bounce_capture_ratio
                    meta["bounce_target_pct"] = bounce_target_pct

            # 唯一獲利出場：峰值每跨一個 0.2% 階梯，鎖利線同步上移。
            if (
                ENABLE_FIXED_PROFIT_LOCK_LADDER
                and FIXED_PROFIT_LOCK_LADDER_STEP_PCT > 0
                and FIXED_PROFIT_LOCK_LADDER_FIRST_PCT > 0
                and entry_p > 0
            ):
                completed_steps = math.floor(
                    max(0.0, highest_pnl - FIXED_PROFIT_LOCK_LADDER_FIRST_PCT)
                    / FIXED_PROFIT_LOCK_LADDER_STEP_PCT + 1e-12
                )
                lock_pct = (
                    FIXED_PROFIT_LOCK_LADDER_FIRST_PCT
                    + completed_steps * FIXED_PROFIT_LOCK_LADDER_STEP_PCT
                    if highest_pnl + 1e-12 >= FIXED_PROFIT_LOCK_LADDER_FIRST_PCT
                    else 0.0
                )
                if lock_pct > 0:
                    ladder_sl = entry_p * (1.0 + lock_pct if side == "LONG" else 1.0 - lock_pct)
                    current_sl = float(pos.get("sl") or meta.get("sl") or 0.0)
                    improves = (
                        ladder_sl > current_sl + entry_p * 1e-12 if side == "LONG"
                        else current_sl <= 0.0 or ladder_sl < current_sl - entry_p * 1e-12
                    )
                    if improves and await self.trail_stop_loss(symbol, ladder_sl, mark_profit_locked=True):
                        meta["fixed_profit_lock_ladder"] = True
                        meta["fixed_profit_lock_pct"] = lock_pct
                        self.log(
                            f"🔐 [固定階梯鎖利] {symbol} 峰值 {highest_pnl:.2%} → 鎖利 {lock_pct:.2%}",
                            "SUCCESS",
                        )

            profit_giveback_ratio = (
                (highest_pnl - pnl_pct) / highest_pnl if highest_pnl > 0 else 0.0
            )
            profit_alert = (
                highest_pnl >= PROFIT_ALERT_MIN_PEAK_PCT
                and pnl_pct > 0
                and profit_giveback_ratio >= PROFIT_ALERT_GIVEBACK_RATIO
            )
            if ENABLE_PROFIT_GIVEBACK_EXIT and profit_alert:
                # 峰值回吐平倉優先於後續的本地止損判斷，避免先被 Stop-Loss 搶走。
                await self.close_position(symbol, curr_p, "峰值回吐平倉")
                continue

            if (
                ENABLE_BOUNCE_TARGET_EXIT
                and meta.get("profit_profile") == "BOUNCE"
                and bounce_target_pct > 0
                and pnl_pct + 1e-12 >= bounce_target_pct
            ):
                await self.close_position(
                    symbol, curr_p, f"反彈空間{bounce_capture_ratio:.0%}目標平倉"
                )
                continue

            if (
                meta.get("profit_profile") == "BOUNCE"
                and BOUNCE_NO_FOLLOW_THROUGH_SEC > 0
                and now_ts - float(meta.get("open_timestamp") or now_ts)
                    >= BOUNCE_NO_FOLLOW_THROUGH_SEC
                and highest_pnl < BOUNCE_NO_FOLLOW_THROUGH_MIN_MFE_PCT
                and pnl_pct <= 0
            ):
                await self.close_position(symbol, curr_p, "反彈逾時未延續平倉")
                continue

            # 第一階段在 +0.5% 鎖住；之後依峰值級距保留70%／80%／85%。沿用既有
            # STOP_MARKET 安全撤換流程，實盤模擬與紙上帳戶一致。
            fixed_pct_active = (
                ENABLE_FIXED_PROFIT_LOCK_PCT
                and bool(pos.get("outer_run_active") or meta.get("outer_run_active"))
                and FIXED_PROFIT_LOCK_TRIGGER_PCT > 0
                and highest_pnl + 1e-12 >= FIXED_PROFIT_LOCK_TRIGGER_PCT
            )
            profit_bank_active = (
                ENABLE_PROFIT_BANK
                and highest_pnl + 1e-12 >= PROFIT_BANK_TRIGGER_PCT
            )
            if fixed_pct_active or profit_bank_active:
                if fixed_pct_active:
                    bank_lock_pct = FIXED_PROFIT_LOCK_FLOOR_PCT
                else:
                    bank_lock_pct = min(
                        max(PROFIT_BANK_LOCK_PCT, highest_pnl * get_profit_bank_capture_ratio(highest_pnl, PROFIT_BANK_CAPTURE_RATIO)),
                        max(0.0, highest_pnl - SLIPPAGE_PCT),
                    )
                raw_bank_sl = entry_p * (
                    1.0 + bank_lock_pct
                    if side == "LONG" else 1.0 - bank_lock_pct
                )
                bank_sl = float(self.exchange.price_to_precision(symbol, raw_bank_sl))
                min_step = entry_p * (
                    0.00001 if fixed_pct_active else PROFIT_BANK_MIN_STEP_PCT
                )
                improves = (
                    bank_sl > old_sl + min_step if side == "LONG"
                    else old_sl <= 0.0 or bank_sl < old_sl - min_step
                )
                if improves:
                    protection_installed = not ENABLE_EXCHANGE_INITIAL_STOP_LOSS
                    close_side_bank = "sell" if side == "LONG" else "buy"
                    tp_price = float(meta.get("tp") or pos.get("tp") or 0.0)
                    if ENABLE_EXCHANGE_INITIAL_STOP_LOSS:
                        try:
                            await self._cancel_all_orders(symbol)
                            await self._create_protection_order(
                                symbol, close_side_bank, "STOP_MARKET", pos["qty"], bank_sl,
                            )
                            protection_installed = True
                            if tp_price > 0 and not DISABLE_TAKE_PROFIT:
                                try:
                                    await self._create_protection_order(
                                        symbol, close_side_bank, "TAKE_PROFIT_MARKET",
                                        pos["qty"], tp_price,
                                    )
                                except Exception as tp_exc:
                                    self.log(
                                        f"⚠️ [淨利入庫] {symbol} 入庫停損已建立，但 TP 重掛失敗："
                                        f"{type(tp_exc).__name__}: {tp_exc}",
                                        "WARNING",
                                    )
                        except Exception as exc:
                            restored = False
                            if old_sl > 0:
                                try:
                                    await self._create_protection_order(
                                        symbol, close_side_bank, "STOP_MARKET", pos["qty"], old_sl,
                                    )
                                    if tp_price > 0 and not DISABLE_TAKE_PROFIT:
                                        await self._create_protection_order(
                                            symbol, close_side_bank, "TAKE_PROFIT_MARKET",
                                            pos["qty"], tp_price,
                                        )
                                    restored = True
                                except Exception:
                                    pass
                            self.log(
                                f"⚠️ [淨利入庫] {symbol} 保護單建立失敗："
                                f"{type(exc).__name__}: {exc}；"
                                f"{'已恢復原停損' if restored else '原停損恢復失敗，下輪重試'}",
                                "WARNING" if restored else "DANGER",
                            )
                    if protection_installed:
                        meta["sl"] = bank_sl
                        pos["sl"] = bank_sl
                        meta["is_breakeven_moved"] = True
                        pos["is_breakeven_moved"] = True
                        if fixed_pct_active:
                            meta["fixed_profit_lock_pct_armed"] = True
                            pos["fixed_profit_lock_pct_armed"] = True
                        else:
                            meta["profit_bank_armed"] = True
                            pos["profit_bank_armed"] = True
                        old_sl = bank_sl
                        label = (
                            f"0.5%觸發／固定鎖{FIXED_PROFIT_LOCK_FLOOR_PCT:.1%}"
                            if fixed_pct_active else "階梯移動停利"
                        )
                        self.log(
                            f"📈 [{label}] {symbol} 峰值 {highest_pnl:.4%}，"
                            f"已鎖 {bank_lock_pct:.4%}，保護線上移至 {bank_sl:.6g}",
                            "SUCCESS",
                        )

            # 結構反彈單專用的早期獲利保護。這層獨立於原生 Trailing，
            # 讓 Testnet 與紙上帳戶在小幅浮盈回吐時採取一致行為。
            if ENABLE_EARLY_PROFIT_GUARD and meta.get("profit_profile") == "BOUNCE":
                round_trip_cost_pct = 2 * TAKER_FEE_RATE + SLIPPAGE_PCT
                early_guard_trigger = max(
                    BOUNCE_EARLY_PROFIT_GUARD_TRIGGER_PCT,
                    round_trip_cost_pct,
                )
                early_guard_exit = max(
                    BOUNCE_EARLY_PROFIT_GUARD_EXIT_PCT,
                    round_trip_cost_pct,
                )
                if (
                    highest_pnl >= early_guard_trigger
                    and not meta.get("early_profit_guard_armed")
                ):
                    guard_price = entry_p * (
                        1.0 + early_guard_exit
                        if side == "LONG" else 1.0 - early_guard_exit
                    )
                    protection_installed = not ENABLE_EXCHANGE_INITIAL_STOP_LOSS
                    if ENABLE_EXCHANGE_INITIAL_STOP_LOSS:
                        close_side_guard = "sell" if side == "LONG" else "buy"
                        try:
                            await self._cancel_all_orders(symbol)
                            await self._create_protection_order(
                                symbol, close_side_guard, "STOP_MARKET", pos["qty"], guard_price,
                            )
                            protection_installed = True
                        except Exception as exc:
                            restored = False
                            if old_sl > 0:
                                try:
                                    await self._create_protection_order(
                                        symbol, close_side_guard, "STOP_MARKET", pos["qty"], old_sl,
                                    )
                                    restored = True
                                except Exception:
                                    pass
                            self.log(
                                f"⚠️ [反彈早期獲利保護] {symbol} 保護單建立失敗："
                                f"{type(exc).__name__}: {exc}；"
                                f"{'已恢復原停損' if restored else '原停損恢復失敗，下輪重試'}",
                                "WARNING" if restored else "DANGER",
                            )
                    if protection_installed:
                        meta["early_profit_guard_armed"] = True
                        meta["early_profit_guard_price"] = guard_price
                        meta["sl"] = guard_price
                        meta["is_breakeven_moved"] = True
                        pos["early_profit_guard_price"] = guard_price
                        pos["sl"] = guard_price
                        pos["is_breakeven_moved"] = True
                        mode = "立即掛出 STOP_MARKET" if ENABLE_EXCHANGE_INITIAL_STOP_LOSS else "立即設定本地保護價"
                        self.log(
                            f"🛡️ [反彈早期獲利保護] {symbol} 峰值 {highest_pnl:.4%} 已達"
                            f" {early_guard_trigger:.4%}，{mode} {guard_price:.6g}"
                            f"（回吐至 {early_guard_exit:.4%} 觸發）",
                            "SUCCESS",
                        )
                        # 先讓新保護單穩定存在；下一輪才允許移動止利繼續往
                        # 更有利方向收緊，避免同一輪建立後又立即取消替換。
                        continue
                if meta.get("early_profit_guard_armed") and pnl_pct <= early_guard_exit:
                    await self.close_position(symbol, curr_p, "反彈早期獲利保護回吐平倉")
                    continue

            # ── 急速逆向閃崩偵測（Rapid Adverse Drop Guard）──
            # 在單次 ticker 更新周期（約 5 秒）內，若持倉方向屑生急速逆向移動超過門檣
            # 則立即市價平倉，不等 K 線收線，應對閃崩（Flash Crash）行情。
            if ENABLE_RAPID_ADVERSE_DROP:
                prev_p = self._last_ticker_prices.get(symbol)
                last_cd = self._rapid_drop_cooldown.get(symbol, 0.0)
                if prev_p and prev_p > 0 and (now_ts - last_cd) > RAPID_DROP_COOLDOWN_SEC:
                    adverse_move = (
                        (prev_p - curr_p) / prev_p if side == "LONG"
                        else (curr_p - prev_p) / prev_p
                    )
                    if adverse_move >= RAPID_ADVERSE_DROP_PCT:
                        self.log(
                            f"⚡ [閃崩偵測] {symbol} {side} 單次 ticker 周期急速逆向 "
                            f"{adverse_move:.3%}（門檣 {RAPID_ADVERSE_DROP_PCT:.3%}），"
                            f"當機立斷平倉！（前價: {prev_p:.6g} → 現價: {curr_p:.6g}）",
                            "DANGER"
                        )
                        self._rapid_drop_cooldown[symbol] = now_ts
                        self._last_ticker_prices[symbol] = curr_p
                        await self.close_position(symbol, curr_p, "急速逆向閃崩觸發平倉")
                        continue

            # 動態本金防線：以實際投入保證金為基準，未來本金放大時自動縮放。
            margin_used = float(pos.get("margin") or 0.0)
            leverage = float(pos.get("leverage") or 1.0)
            if margin_used <= 0:
                margin_used = abs(entry_p * float(pos.get("qty") or 0.0)) / max(leverage, 1.0)
            max_margin_loss_usdt = margin_used * MAX_POSITION_MARGIN_LOSS_RATIO
            current_loss_usdt = max(0.0, -pnl_pct * margin_used * leverage)
            if max_margin_loss_usdt > 0 and current_loss_usdt >= max_margin_loss_usdt:
                self.log(
                    f"🚨 [動態本金防線] {symbol} {side} 毛虧損 "
                    f"{current_loss_usdt:.2f}U 已達本金上限 {max_margin_loss_usdt:.2f}U "
                    f"({MAX_POSITION_MARGIN_LOSS_RATIO:.0%})，強制市價平倉",
                    "DANGER",
                )
                await self.close_position(symbol, curr_p, "動態本金最大虧損門檻觸發")
                continue

            # 災難性硬防線止損 (不論是否關閉止損，一旦價格虧損超過此負值門檻即強制平倉)
            if MAX_ACCEPTABLE_LOSS_PCT < 0:
                current_loss_pct = (mark_p - entry_p) / entry_p if side == "LONG" else (entry_p - mark_p) / entry_p
                if current_loss_pct <= MAX_ACCEPTABLE_LOSS_PCT:
                    self.log(
                        f"🚨 [災難止損] {symbol} {side} 虧損 {current_loss_pct:.2%} 已觸碰或超過硬防線 {MAX_ACCEPTABLE_LOSS_PCT:.2%}，強制市價平倉",
                        "DANGER"
                    )
                    await self.close_position(symbol, curr_p, "本地最大虧損門檻觸發")
                    continue

            # 停用交易所初始停損時，old_sl 是純本地觀察線；啟用時則完全
            # 交給交易所 STOP_MARKET 處理，不再需要限價未成交後備。
            if not ENABLE_EXCHANGE_INITIAL_STOP_LOSS and old_sl > 0:
                breached = (
                    (side == "LONG" and mark_p <= old_sl)
                    or (side == "SHORT" and mark_p >= old_sl)
                )
                if breached:
                    current_loss_pct = (mark_p - entry_p) / entry_p if side == "LONG" else (entry_p - mark_p) / entry_p
                    if MAX_ACCEPTABLE_LOSS_PCT < 0 and current_loss_pct > MAX_ACCEPTABLE_LOSS_PCT:
                        self.log(
                            f"⏸️ [{symbol}] 止損已觸發但虧損 {current_loss_pct:.2%} 未超過允許值 {MAX_ACCEPTABLE_LOSS_PCT:.2%}，"
                            f"耐心等待利潤回來... (止損價: {old_sl}, 目前價: {mark_p:.6f})",
                            "INFO",
                        )
                        continue
                    self.log(
                        f"🚨 {symbol} 本地停損觀察線已穿越（標記價 {mark_p:.6f}，觀察線 {old_sl}），"
                        f"虧損 {current_loss_pct:.2%} 超過限制 {MAX_ACCEPTABLE_LOSS_PCT:.2%}，強制市價平倉",
                        "DANGER",
                    )
                    await self.close_position(symbol, curr_p, "本地最大虧損門檻觸發")
                    continue

            # ── 移動停利 / 原生 Trailing Stop 三階段升級 ──
            if ENABLE_TRAILING_STOP and bool(
                pos.get("outer_run_active") or meta.get("outer_run_active")
            ):
                atr_value = meta.get("atr", entry_p * 0.015)
                atr_pct = atr_value / entry_p if entry_p > 0 else 0.015
                highest_pnl = meta.get("highest_pnl_pct", pnl_pct)
                if pnl_pct > highest_pnl:
                    highest_pnl = pnl_pct
                    meta["highest_pnl_pct"] = highest_pnl
                    meta["peak_profit_updated_at"] = now_ts

                if "peak_profit_updated_at" not in meta:
                    meta["peak_profit_updated_at"] = pos.get("open_timestamp") or now_ts

                # 浮盈換算為 ATR 倍數（無槓桿）
                profit_in_atr = (highest_pnl / atr_pct) if atr_pct > 0 else 0.0
                close_side_trail = "sell" if side == "LONG" else "buy"
                qty_trail = pos["qty"]

                if USE_NATIVE_TRAILING_STOP:
                    # ── 分段式混合追蹤模式 (Hybrid Trailing Stop) ──
                    # 前段 (< 2.0 ATR)：使用本地保本防守，掛載固定 STOP_MARKET 鎖定保本價
                    # 後段 (>= 3.5 ATR)：升級為 Binance 原生 TRAILING_STOP_MARKET (Tier 2 & Tier 3)
                    current_tier = meta.get("native_trailing_tier", 0)
                    
                    # 1. 前段：本地精準保本 (達 1.2 ATR)
                    # 補償約 0.15% (進出場 Taker 手續費 + 微利)
                    tier1_profit_ready = (
                        pnl_pct >= NET_PROFIT_GUARANTEE_BUFFER + TAKER_FEE_RATE
                    )
                    if (
                        profit_in_atr >= TRAILING_TIER1_TRIGGER_ATR_MULT
                        and tier1_profit_ready
                        and not meta.get("is_breakeven_moved")
                    ):
                        if side == "LONG":
                            breakeven_sl = entry_p * (1.0 + NET_PROFIT_GUARANTEE_BUFFER)
                            new_sl_price = float(self.exchange.price_to_precision(symbol, breakeven_sl))
                            if new_sl_price > old_sl:
                                meta["sl"] = new_sl_price
                                pos["sl"] = new_sl_price
                                meta["is_breakeven_moved"] = True
                                pos["is_breakeven_moved"] = True
                                meta["native_trailing_tier"] = 1
                                self.log(f"🛡️ [本地保本] {symbol} 達到 {profit_in_atr:.1f} ATR 浮盈，止損鎖定在保本價 {new_sl_price}", "SUCCESS")
                                try:
                                    await self._cancel_all_orders(symbol)
                                    await self._create_protection_order(
                                        symbol, close_side_trail, "STOP_MARKET", qty_trail, new_sl_price
                                    )
                                    tp_price = float(meta.get("tp") or pos.get("tp") or 0.0)
                                    if tp_price > 0 and not DISABLE_TAKE_PROFIT:
                                        await self._create_protection_order(
                                            symbol, close_side_trail, "TAKE_PROFIT_MARKET", qty_trail, tp_price
                                        )
                                except Exception as e:
                                    self.log(f"⚠️ {symbol} 設置本地保本單失敗: {e}", "WARNING")
                        else:  # SHORT
                            breakeven_sl = entry_p * (1.0 - NET_PROFIT_GUARANTEE_BUFFER)
                            new_sl_price = float(self.exchange.price_to_precision(symbol, breakeven_sl))
                            if new_sl_price < old_sl or old_sl == 0.0:
                                meta["sl"] = new_sl_price
                                pos["sl"] = new_sl_price
                                meta["is_breakeven_moved"] = True
                                pos["is_breakeven_moved"] = True
                                meta["native_trailing_tier"] = 1
                                self.log(f"🛡️ [本地保本] {symbol} 達到 {profit_in_atr:.1f} ATR 浮盈，止損鎖定在保本價 {new_sl_price}", "SUCCESS")
                                try:
                                    await self._cancel_all_orders(symbol)
                                    await self._create_protection_order(
                                        symbol, close_side_trail, "STOP_MARKET", qty_trail, new_sl_price
                                    )
                                    tp_price = float(meta.get("tp") or pos.get("tp") or 0.0)
                                    if tp_price > 0 and not DISABLE_TAKE_PROFIT:
                                        await self._create_protection_order(
                                            symbol, close_side_trail, "TAKE_PROFIT_MARKET", qty_trail, tp_price
                                        )
                                except Exception as e:
                                    self.log(f"⚠️ {symbol} 設置本地保本單失敗: {e}", "WARNING")

                    # 2. 後段：升級至交易所原生毫秒級 Trailing Stop (達 Tier 2 或 Tier 3)
                    target_tier = 0
                    
                    # 猴市時，提早啟動鎖利與極致追蹤
                    is_range_mode = (meta.get("market_mode") == "RANGE" or pos.get("market_mode") == "RANGE")
                    tier3_trigger = TRAILING_TIER3_TRIGGER_ATR_MULT * (0.6 if is_range_mode else 1.0)
                    tier2_trigger = TRAILING_TIER2_TRIGGER_ATR_MULT * (0.6 if is_range_mode else 1.0)
                    
                    if profit_in_atr >= tier3_trigger and current_tier < 3:
                        target_tier = 3
                    elif profit_in_atr >= tier2_trigger and current_tier < 2:
                        target_tier = 2

                    retry_after = float(meta.get("native_trailing_retry_after", 0.0) or 0.0)
                    if target_tier > 0 and now_ts < retry_after:
                        target_tier = 0

                    if target_tier > 0:
                        tier_labels = {2: "鎖利", 3: "極致追蹤"}
                        callback_rate = self._compute_callback_rate(
                            atr_pct, target_tier, highest_pnl=highest_pnl
                        )
                        # 升級前先保存一條可立即重建的固定止損。若目前已由
                        # native Tier2 接管（本地 sl=0），就用 Tier2 鎖利價
                        # 作為安全回復；否則沿用現有固定止損。
                        fallback_sl = float(meta.get("sl") or pos.get("sl") or 0.0)
                        if fallback_sl <= 0:
                            locked_distance = atr_value * TRAILING_TIER2_LOCK_ATR_MULT
                            if side == "LONG":
                                fallback_sl = min(
                                    entry_p + locked_distance,
                                    mark_p * (1.0 - 0.001),
                                )
                            else:
                                fallback_sl = max(
                                    entry_p - locked_distance,
                                    mark_p * (1.0 + 0.001),
                                )
                            fallback_sl = float(
                                self.exchange.price_to_precision(symbol, fallback_sl)
                            )
                        try:
                            await self._cancel_all_orders(symbol)
                            result = await self._place_native_trailing_stop(
                                symbol,
                                close_side_trail,
                                qty_trail,
                                atr_pct=atr_pct,
                                tier=target_tier,
                                highest_pnl=highest_pnl,
                                activation_price=mark_p,  # 從當前標記價開始即時追蹤
                                is_range_mode=is_range_mode,
                            )
                            actual_callback = result.get("callbackRate", callback_rate)
                            meta["native_trailing_tier"] = target_tier
                            meta["native_trailing_callback"] = actual_callback
                            meta.pop("native_trailing_retry_after", None)
                            # 接管後，清空本地 SL/TP 條件記錄
                            meta["sl"] = 0.0
                            pos["sl"] = 0.0
                            tp_price = float(meta.get("tp") or pos.get("tp") or 0.0)
                            if tp_price > 0 and not DISABLE_TAKE_PROFIT:
                                await self._create_protection_order(
                                    symbol, close_side_trail, "TAKE_PROFIT_MARKET", qty_trail, tp_price
                                )
                            self.log(
                                f"🎯 [原生 Trailing Tier{target_tier} – {tier_labels[target_tier]}] "
                                f"{symbol} 浮盈 {profit_in_atr:.1f} ATR ({pnl_pct:.2%}) | "
                                f"已取消本地條件單，升級掛載交易所原生 TRAILING_STOP_MARKET (callback={actual_callback}%)",
                                "SUCCESS",
                            )
                        except Exception as e:
                            # 舊保護已被取消；原生掛單失敗時必須立刻補回
                            # 固定止損，並短暫冷卻避免每輪反覆撤掛。
                            meta["native_trailing_retry_after"] = now_ts + 60.0
                            restored = False
                            try:
                                if fallback_sl > 0:
                                    await self._create_protection_order(
                                        symbol,
                                        close_side_trail,
                                        "STOP_MARKET",
                                        qty_trail,
                                        fallback_sl,
                                    )
                                    meta["sl"] = fallback_sl
                                    pos["sl"] = fallback_sl
                                    restored = True
                            except Exception as restore_exc:
                                self.log(
                                    f"🚨 {symbol} 原生 Trailing 失敗後固定止損也恢復失敗: "
                                    f"{type(restore_exc).__name__}: {restore_exc}",
                                    "DANGER",
                                )
                            restore_status = "已恢復" if restored else "未能恢復"
                            self.log(
                                f"⚠️ {symbol} 升級掛載原生 Trailing Stop (Tier{target_tier}) 失敗: "
                                f"{type(e).__name__}: {e} | 固定止損{restore_status}",
                                "WARNING" if restored else "DANGER",
                            )

                else:
                    # ── 路徑 B：舊版百分比制輪詢移動止利（Testnet / fallback）──
                    # 逆勢承接單（MA7_ContrarianBottomBuy）用更早/更低的觸發
                    # 門檻，一旦有利潤就盡快接手保護，不限制往上空間。
                    configured_trigger = (
                        CONTRARIAN_TRAILING_TRIGGER_PCT if meta.get("is_contrarian_bottom_buy")
                        else TRAILING_TRIGGER_PCT
                    )
                    initial_risk = float(pos.get("initial_risk") or meta.get("initial_risk") or 0.0)
                    risk_pct = initial_risk / entry_p if entry_p > 0 else 0.0
                    # 有明確 initial_risk 的新單直接用 R 倍數啟動。
                    # 固定百分比只供缺少風險資料的舊單使用，否則窄止損單
                    # 會在 1.5R 已分批後，剩餘倉仍未獲 trailing 保護。
                    trailing_trigger = (
                        risk_pct * TRAILING_TRIGGER_R_MULT
                        if risk_pct > 0 else configured_trigger
                    )
                    trailing_callback = (
                        max(TRAILING_CALLBACK_PCT, risk_pct * TRAILING_CALLBACK_R_MULT)
                        if risk_pct > 0 else TRAILING_CALLBACK_PCT
                    )
                    if highest_pnl >= trailing_trigger:
                        if side == "LONG":
                            trail_sl = entry_p * (1.0 + highest_pnl - trailing_callback)
                            npg_floor = entry_p * (1.0 + NET_PROFIT_GUARANTEE_BUFFER)
                            trail_sl = max(trail_sl, npg_floor)
                            new_sl_price = float(self.exchange.price_to_precision(symbol, trail_sl))
                            if new_sl_price > old_sl:
                                meta["sl"] = new_sl_price
                                pos["sl"] = new_sl_price
                                meta["is_breakeven_moved"] = True
                                pos["is_breakeven_moved"] = True
                                self.log(f"📈 [移動止利] {symbol} 無槓桿利潤峰值 {highest_pnl:.4%}，止利線推至 {new_sl_price}（回吐 {trailing_callback:.4%} 平倉）", "SUCCESS")
                                try:
                                    await self._cancel_all_orders(symbol)
                                    await self._create_protection_order(
                                        symbol, close_side_trail, "STOP_MARKET", qty_trail, new_sl_price
                                    )
                                    tp_price = float(meta.get("tp") or pos.get("tp") or 0.0)
                                    if tp_price > 0 and not DISABLE_TAKE_PROFIT:
                                        await self._create_protection_order(
                                            symbol, close_side_trail, "TAKE_PROFIT_MARKET", qty_trail, tp_price
                                        )
                                except Exception as e:
                                    self.log(f"⚠️ {symbol} 更新移動止利單失敗: {e}", "WARNING")
                        else:  # SHORT
                            trail_sl = entry_p * (1.0 - highest_pnl + trailing_callback)
                            npg_ceiling = entry_p * (1.0 - NET_PROFIT_GUARANTEE_BUFFER)
                            trail_sl = min(trail_sl, npg_ceiling)
                            new_sl_price = float(self.exchange.price_to_precision(symbol, trail_sl))
                            if new_sl_price < old_sl or old_sl == 0.0:
                                meta["sl"] = new_sl_price
                                pos["sl"] = new_sl_price
                                meta["is_breakeven_moved"] = True
                                pos["is_breakeven_moved"] = True
                                self.log(f"📉 [移動止利] {symbol} 無槓桿利潤峰值 {highest_pnl:.4%}，止利線推至 {new_sl_price}（回吐 {trailing_callback:.4%} 平倉）", "SUCCESS")
                                try:
                                    await self._cancel_all_orders(symbol)
                                    await self._create_protection_order(
                                        symbol, close_side_trail, "STOP_MARKET", qty_trail, new_sl_price
                                    )
                                    tp_price = float(meta.get("tp") or pos.get("tp") or 0.0)
                                    if tp_price > 0 and not DISABLE_TAKE_PROFIT:
                                        await self._create_protection_order(
                                            symbol, close_side_trail, "TAKE_PROFIT_MARKET", qty_trail, tp_price
                                        )
                                except Exception as e:
                                    self.log(f"⚠️ {symbol} 更新移動止利單失敗: {e}", "WARNING")


            if ENABLE_24H_TIME_FILTER and (time.time() - pos.get("open_timestamp", time.time())) >= 86400:
                await self.close_position(symbol, curr_p, "時間過濾 (24h 無效震盪離場)")
                continue

            self.position_meta[symbol] = meta
            # 閃崩偵測：更新此週期的價格快照，供下一輪計算逆向幅度
            self._last_ticker_prices[symbol] = curr_p

        self.save_state()
        return self.unrealized_pnl


    async def _create_orphan_protection(self, symbol: str, pos: dict, meta: dict) -> None:
        side = pos["side"]
        entry_p = pos["entry_price"]
        atr = meta.get("atr") or entry_p * 0.015
        sl_distance, tp_distance = compute_sl_tp_distance(entry_p, atr)
        if side == "LONG":
            sl_price = float(self.exchange.price_to_precision(symbol, entry_p - sl_distance)) if not DISABLE_STOP_LOSS else 0.0
            tp_price = float(self.exchange.price_to_precision(symbol, entry_p + tp_distance)) if not DISABLE_TAKE_PROFIT else 0.0
        else:
            sl_price = float(self.exchange.price_to_precision(symbol, entry_p + sl_distance)) if not DISABLE_STOP_LOSS else 0.0
            tp_price = float(self.exchange.price_to_precision(symbol, entry_p - tp_distance)) if not DISABLE_TAKE_PROFIT else 0.0
        if not DISABLE_STOP_LOSS:
            sl_price = cap_stop_loss_to_margin_risk(entry_p, side, sl_price, pos["leverage"])
            sl_price = float(self.exchange.price_to_precision(symbol, sl_price))
        close_side = "sell" if side == "LONG" else "buy"
        try:
            await self._cancel_all_orders(symbol)
            if ENABLE_EXCHANGE_INITIAL_STOP_LOSS:
                await self._create_protection_order(
                    symbol, close_side, "STOP_MARKET", pos["qty"], sl_price,
                )
            if not DISABLE_TAKE_PROFIT:
                await self._create_protection_order(symbol, close_side, "TAKE_PROFIT_MARKET", pos["qty"], tp_price)
            meta["sl"] = sl_price
            meta["tp"] = tp_price
            meta["atr"] = atr
            self.position_meta[symbol] = meta
            self.save_state()
            sl_msg = f"SL={sl_price}" if ENABLE_EXCHANGE_INITIAL_STOP_LOSS else f"本地SL觀察線={sl_price}"
            tp_msg = f" TP={tp_price}" if not DISABLE_TAKE_PROFIT else " (已停用初始止利)"
            self.log(
                f"🔧 [補建保護] {symbol} 偵測到缺少保護資料，已建立 {sl_msg}{tp_msg}",
                "WARNING",
            )
        except Exception as exc:
            self.log(
                f"⚠️ {symbol} 補建保護單失敗：{type(exc).__name__}: {exc}",
                "WARNING",
            )

    async def _restore_exchange_initial_stops(self) -> None:
        """啟用交易所初始停損後，替重啟前的既有持倉重建硬停損。

        position_meta 會跨重啟保留 SL，但先前若以本地觀察線模式開倉，交易所
        並沒有對應條件單。啟動時取消該幣舊保護單後依原 SL/TP 重建，避免全域
        開關已切回交易所模式、本地監控也不再觸發，卻留下裸倉。已升級原生
        trailing 的部位不動，避免把鎖利單降級回初始固定停損。
        """
        if not ENABLE_EXCHANGE_INITIAL_STOP_LOSS:
            return
        channel_swing_cleared = False
        for symbol, pos in list(self.positions.items()):
            meta = self.position_meta.get(symbol, {})
            entry_mode = str(
                pos.get("entry_mode") or meta.get("entry_mode") or ""
            ).upper()
            if entry_mode == "CHANNEL_SWING":
                if any(float(source.get(key) or 0.0) != 0.0 for source in (pos, meta) for key in (
                    "sl", "tp", "initial_sl", "initial_risk",
                )):
                    for source in (pos, meta):
                        source["sl"] = 0.0
                        source["tp"] = 0.0
                        source["initial_sl"] = 0.0
                        source["initial_risk"] = 0.0
                    channel_swing_cleared = True
                continue
            sl_price = float(meta.get("sl") or pos.get("sl") or 0.0)
            if sl_price <= 0 or int(meta.get("native_trailing_tier") or 0) > 0:
                continue
            close_side = "sell" if pos["side"] == "LONG" else "buy"
            sl_price = cap_stop_loss_to_margin_risk(
                pos["entry_price"], pos["side"], sl_price, pos["leverage"]
            )
            sl_price = float(self.exchange.price_to_precision(symbol, sl_price))
            try:
                await self._cancel_all_orders(symbol)
                await self._create_protection_order(
                    symbol, close_side, "STOP_MARKET", pos["qty"], sl_price,
                )
                tp_price = float(meta.get("tp") or pos.get("tp") or 0.0)
                if tp_price > 0 and not DISABLE_TAKE_PROFIT:
                    await self._create_protection_order(
                        symbol, close_side, "TAKE_PROFIT_MARKET", pos["qty"], tp_price,
                    )
                self.log(
                    f"🔧 [啟動保護遷移] {symbol} 已依既有 SL={sl_price} 重建交易所硬停損",
                    "WARNING",
                )
            except Exception as exc:
                self.log(
                    f"🚨 {symbol} 啟動時重建交易所硬停損失敗：{type(exc).__name__}: {exc}",
                    "DANGER",
                )
        if channel_swing_cleared:
            self.save_state()

    async def _ensure_markets(self) -> None:
        if not self._markets_loaded:
            await self.exchange.load_markets()
            self._markets_loaded = True

    async def _cancel_all_orders(self, symbol: str) -> None:
        try:
            await self.exchange.cancel_all_orders(symbol)
        except Exception:
            pass
        try:
            await self.exchange.request(
                "algoOpenOrders",
                "fapiPrivate",
                "DELETE",
                {"symbol": self._raw_symbol(symbol)},
            )
        except Exception:
            pass

    async def _create_protection_order(
        self, symbol: str, side: str, order_type: str, qty: float, trigger_price: float,
    ) -> dict:
        """建立交易所條件單；停損使用 STOP_MARKET，觸發後直接市價平倉。"""
        params = {
            "algoType": "CONDITIONAL",
            "symbol": self._raw_symbol(symbol),
            "side": side.upper(),
            "quantity": self.exchange.amount_to_precision(symbol, qty),
            "triggerPrice": self.exchange.price_to_precision(symbol, trigger_price),
            "reduceOnly": "true",
            "workingType": "MARK_PRICE",
        }
        params["type"] = order_type
        return await self.exchange.request("algoOrder", "fapiPrivate", "POST", params)

    @staticmethod
    def _compute_callback_rate(atr_pct: float, tier: int, highest_pnl: float = None, is_range_mode: bool = False) -> float:
        """根據進場時的 ATR% 與最高浮盈動態計算 Binance TRAILING_STOP_MARKET 的 callbackRate (%)。

        公式：base = atr_pct * 100 * NATIVE_TRAILING_ATR_RATE_FACTOR
        再依各 Tier 的上下限 clamp。

        安全防護：如果傳入最高浮盈 highest_pnl，則 callbackRate 不得超過最高浮盈的 40% (即最高利潤回吐不得超過 40%)，
        確保即使在小浮盈觸發時，扣除雙邊手續費與滑價後依然維持保本或微利，絕不轉虧。
        """
        base = atr_pct * 100.0 * NATIVE_TRAILING_ATR_RATE_FACTOR
        if is_range_mode:
            base *= 0.5  # 猴市時回調比例直接減半，把利潤鎖得更緊
            
        if tier == 1:
            rate = max(NATIVE_TRAILING_TIER1_CALLBACK_MIN,
                       min(NATIVE_TRAILING_TIER1_CALLBACK_MAX, base))
        elif tier == 2:
            rate = max(NATIVE_TRAILING_TIER2_CALLBACK_MIN,
                       min(NATIVE_TRAILING_TIER2_CALLBACK_MAX, base))
        else:  # Tier 3
            rate = max(NATIVE_TRAILING_TIER3_CALLBACK_MIN,
                       min(NATIVE_TRAILING_TIER3_CALLBACK_MAX, base))

        # ── 安全閥：回調幅度限制在最高浮盈的 40% 以內 (猴市限制在 30%) ──
        if highest_pnl is not None and highest_pnl > 0:
            max_allowed_callback = highest_pnl * 100.0 * (0.3 if is_range_mode else 0.4)
            rate = min(rate, max_allowed_callback)

        # Binance 限制：0.1 ~ 5.0，最多 1 位小數
        return round(max(0.1, min(5.0, rate)), 1)

    async def _place_native_trailing_stop(
        self,
        symbol: str,
        close_side: str,
        qty: float,
        atr_pct: float,
        tier: int,
        highest_pnl: float = None,
        activation_price: float = None,
        is_range_mode: bool = False,
    ) -> dict:
        """下 Binance 原生 TRAILING_STOP_MARKET 訂單。

        由交易所伺服器端以毫秒精度即時追蹤最高/最低標記價格，機器人斷線也
        不影響追蹤與觸發。callbackRate 由 ATR% 與最高浮盈動態計算（Tier1 最寬、Tier3 最緊）。

        activation_price（可選）：當標記價格達到此價位後才開始追蹤。
        """
        callback_rate = self._compute_callback_rate(atr_pct, tier, highest_pnl=highest_pnl, is_range_mode=is_range_mode)
        params = {
            "algoType": "CONDITIONAL",
            "symbol": self._raw_symbol(symbol),
            "side": close_side.upper(),
            "type": "TRAILING_STOP_MARKET",
            "quantity": self.exchange.amount_to_precision(symbol, qty),
            "callbackRate": callback_rate,
            "reduceOnly": "true",
            "workingType": "MARK_PRICE",
        }
        if activation_price is not None:
            params["activationPrice"] = self.exchange.price_to_precision(
                symbol, activation_price
            )
        result = await self.exchange.request(
            "algoOrder", "fapiPrivate", "POST", params
        )
        return {"result": result, "callbackRate": callback_rate}

    async def _emergency_flatten(self, symbol: str, side: str, qty: float) -> None:
        close_side = "sell" if side == "LONG" else "buy"
        try:
            await self.exchange.create_order(
                symbol,
                "market",
                close_side,
                qty,
                None,
                {"reduceOnly": True},
            )
        except Exception as exc:
            self.log(
                f"🚨 {symbol} 保護單建立失敗且緊急平倉也失敗："
                f"{type(exc).__name__}: {exc}",
                "DANGER",
            )
            raise

    async def open_position(
        self,
        symbol: str,
        side: str,
        price: float,
        amount_usdt: float,
        sl: float,
        tp: float,
        reason: str,
        atr: float = 0.0,
        leverage: int = None,
        signal_score: int = None,
        entry_context: dict = None,
    ) -> bool:
        """市價進場（手動下單、或任何需要立即成交的路徑用這個）。
        訊號驅動的回調進場改用 place_limit_entry()，見下方。"""
        if (
            symbol in self.positions
            or symbol in self.pending_limit_orders
            or symbol in self.closing_lock
        ):
            return False
        # 最後一道防線：不管呼叫端邏輯有沒有正確擋住，訊號分數低於
        # MIN_OPEN_SIGNAL_SCORE 一律拒絕下單。手動下單（signal_score 為
        # None）不受影響，這只針對訊號驅動的自動開倉。
        if signal_score is not None and signal_score < MIN_OPEN_SIGNAL_SCORE:
            self.log(
                f"🛑 {symbol} 訊號分數 {signal_score} 低於 {MIN_OPEN_SIGNAL_SCORE} 分下限，拒絕開倉",
                "WARNING",
            )
            return False
        # 下單金額為 0（或負數）時，qty 算出來也會是 0，若直接送進
        # exchange.amount_to_precision() 會被交易所丟出精度例外，這個
        # 例外沒有包在 try/except 裡，會一路往上炸穿整個主迴圈（實測
        # DOGE/USDT 這筆就是這樣：MIN_SCORE_THRESHOLD 調到 65 但
        # POSITION_SIZE_TIERS 最低檔還停在 70，65~69 分的訊號算出來的
        # amount_usdt 直接是 0，主迴圈每輪都在同一個地方反覆炸掉）。
        # 在這裡提前擋掉，用原本就有的「金額/精度不足」警告取代未捕捉例外。
        if amount_usdt <= 0:
            self.log(f"🛑 {symbol} 下單金額為 0，拒絕開倉", "WARNING")
            return False
        entry_mode = str(dict(entry_context or {}).get("entry_mode") or "").upper()
        if entry_mode == "CHANNEL_SWING":
            sl = 0.0
            tp = 0.0
        else:
            try:
                validate_sl_tp_pair(price, side, sl, tp)
            except ValueError as exc:
                self.log(f"🛑 {symbol} 進場前 SL/TP 驗證失敗：{exc}", "WARNING")
                return False
        await self._ensure_markets()
        leverage = leverage or (
            get_signal_leverage(symbol, signal_score)
            if signal_score is not None
            else get_leverage(symbol)
        )
        order_side = "buy" if side == "LONG" else "sell"
        close_side = "sell" if side == "LONG" else "buy"
        qty = float(self.exchange.amount_to_precision(
            symbol,
            (amount_usdt * leverage) / max(price, 1e-12),
        ))
        if qty <= 0:
            self.log(f"🛑 {symbol} 下單數量低於交易所最小精度", "WARNING")
            return False

        try:
            await self._prepare_leverage(symbol, leverage)
            entry_order = await self.exchange.create_order(
                symbol,
                "market",
                order_side,
                qty,
                None,
                {"newOrderRespType": "RESULT"},
            )
            execution_price = float(entry_order.get("average") or price)
        except Exception as exc:
            self.log(
                f"🛑 Binance Testnet 開倉失敗 {symbol}："
                f"{type(exc).__name__}: {exc}",
                "DANGER",
            )
            await self.refresh(force=True)
            return False

        return await self._finalize_new_position(
            symbol, side, execution_price, qty, price, sl, tp, reason, atr,
            leverage, signal_score, close_side, entry_order.get("id"), amount_usdt,
            entry_context=entry_context,
        )

    async def _prepare_leverage(self, symbol: str, leverage: int) -> None:
        # 強制逐倉保證金模式：專案從沒設定過保證金模式，代表可能一直用帳戶
        # 預設的全倉——全倉下一筆爆倉會吃掉整個帳戶保證金，不只該筆本金，
        # 直接打破 MAX_SLOTS/TRADE_AMOUNT_USDT「每筆只賭固定金額」的風控
        # 假設。已經是 ISOLATED 時 Binance 會回錯誤（-4046 No need to change
        # margin type），單純忽略即可，不影響下單流程。
        try:
            await self.exchange.set_margin_mode("ISOLATED", symbol)
        except Exception:
            pass
        await self.exchange.set_leverage(leverage, symbol)

    async def _finalize_new_position(
        self,
        symbol: str,
        side: str,
        execution_price: float,
        qty: float,
        price_ref: float,
        sl: float,
        tp: float,
        reason: str,
        atr: float,
        leverage: int,
        signal_score,
        close_side: str,
        entry_order_id,
        amount_usdt: float,
        entry_context: dict = None,
    ) -> bool:
        """開倉單成交後的收尾：建立SL/TP保護單、寫入meta、記錄交易。
        market（open_position）與 limit（place_limit_entry 成交後）兩條
        路徑共用，避免重複程式碼。price_ref 是原本規劃進場的參考價（市價
        單是訊號當下價、限價單是掛單目標價），sl/tp 距離以它為基準換算，
        再套用到實際成交價上，讓成交價比預期更好時，止損止盈距離維持
        原本規劃的寬度，不會因為成交價落差而跟著偏移。"""
        try:
            entry_context = {
                key: value for key, value in dict(entry_context or {}).items()
                if key in ENTRY_CONTEXT_KEYS
            }
            is_channel_swing = str(entry_context.get("entry_mode") or "").upper() == "CHANNEL_SWING"
            if is_channel_swing:
                sl = 0.0
                tp = 0.0
            else:
                try:
                    validate_sl_tp_pair(execution_price, side, sl, tp)
                except ValueError as exc:
                    self.log(f"🛑 {symbol} 進場後 SL/TP 驗證失敗：{exc}", "WARNING")
                    return False
            is_exhaustion_sniper = entry_context.get("entry_mode") in ("EXHAUSTION_SNIPER", "PIVOT_TURN")
            sl_distance = abs(price_ref - sl)
            tp_distance = abs(tp - price_ref)
            adjusted_sl = (
                execution_price - sl_distance if side == "LONG"
                else execution_price + sl_distance
            )
            adjusted_tp = (
                execution_price + tp_distance if side == "LONG"
                else execution_price - tp_distance
            )
            if is_exhaustion_sniper:
                adjusted_sl = execution_price * (
                    1.0 - EXHAUSTION_SNIPER_STOP_LOSS_PCT
                    if side == "LONG"
                    else 1.0 + EXHAUSTION_SNIPER_STOP_LOSS_PCT
                )
            # Ensure SL sits on correct side and respect a minimum distance
            if is_channel_swing:
                sl_price = 0.0
            elif not DISABLE_STOP_LOSS or is_exhaustion_sniper:
                atr_value = atr if atr > 0 else execution_price * 0.015
                min_dist = max(price_ref * MIN_SL_DISTANCE_PCT, atr_value * STOP_LOSS_MULTIPLIER)
                if side == "LONG":
                    if adjusted_sl >= execution_price - 1e-12:
                        adjusted_sl = execution_price - min_dist
                else:
                    if adjusted_sl <= execution_price + 1e-12:
                        adjusted_sl = execution_price + min_dist
                sl_price = float(self.exchange.price_to_precision(symbol, adjusted_sl))
            else:
                sl_price = 0.0
            if not is_channel_swing and not DISABLE_STOP_LOSS and not is_exhaustion_sniper:
                sl_price = cap_stop_loss_to_margin_risk(execution_price, side, sl_price, leverage)
                sl_price = float(self.exchange.price_to_precision(symbol, sl_price))
            structure_exit_only = str(entry_context.get("wave_regime") or "").upper() in ("RANGE", "TREND")
            tp_price = (
                float(self.exchange.price_to_precision(symbol, adjusted_tp))
                if not DISABLE_TAKE_PROFIT and not structure_exit_only else 0.0
            )
            if entry_context.get("initial_sl") is not None:
                entry_context["initial_sl"] = sl_price
                entry_context["initial_risk"] = abs(execution_price - sl_price)
            atr_value = atr if atr > 0 else execution_price * 0.015
            try:
                # 限價單成交後，這裡跟主迴圈/網頁輪詢都可能同時偵測到「這個
                # symbol 有持倉但還沒保護單」而觸發 _create_orphan_protection，
                # 兩邊可能疊出重複的止損止盈單。先清一次掛單，確保接下來建的
                # 是唯一一組，不管是不是搶輸了孤兒保護機制一步。
                await self._cancel_all_orders(symbol)
                if ENABLE_EXCHANGE_INITIAL_STOP_LOSS and sl_price > 0:
                    await self._create_protection_order(
                        symbol, close_side, "STOP_MARKET", qty, sl_price,
                    )
                if not DISABLE_TAKE_PROFIT and not structure_exit_only:
                    await self._create_protection_order(
                        symbol, close_side, "TAKE_PROFIT_MARKET", qty, tp_price
                    )
            except Exception:
                await self._cancel_all_orders(symbol)
                try:
                    await self._emergency_flatten(symbol, side, qty)
                except Exception:
                    # ✅ 修正 Bug3：緊急平倉也失敗時，清除 position_meta 的 sl 記錄，
                    # 確保 update_positions() 下輪偵測到 sl=0 後由孤兒保護機制接手重建。
                    # 若不清除：refresh() 會從 meta 把舊 sl 帶入 pos，
                    # 孤兒偵測的 pos["sl"] <= 0 條件不成立，裸倉永久缺乏保護。
                    self.position_meta.pop(symbol, None)
                raise

            fee = qty * execution_price * TAKER_FEE_RATE
            # 即使停用交易所初始停損，仍保存 SL 作為本地觀察線；TP 以及
            # 獲利後的移動保本／移動停利維持原本的交易所保護方式。
            meta = {
                "sl": sl_price,
                "tp": tp_price,
                "atr": atr_value,
                "open_timestamp": time.time(),
                "open_time": get_taipei_now_str(),
                "reason": reason,
                "signal_score": signal_score,
                **entry_context,
            }
            self.position_meta[symbol] = meta
            # 「金額」用實際成交的 qty×成交價÷槓桿算，不要直接沿用呼叫端
            # 傳入的 amount_usdt（原本打算下的預算）——限價單部分成交時
            # （見 check_pending_limit_orders/cancel_pending_limit 的部分
            # 成交路徑），真正吃到的 qty 會比預算算出來的量少，此時若照
            # 舊寫法把 amount_usdt 原封不動記錄下去，開倉那筆金額會跟平倉
            # 時用真實 qty 反推的金額對不上（實測 SUI/USDT 07/29 03:20 這筆
            # 差了 6.12 USDT），讓人誤以為部位沒平乾淨。
            actual_margin = abs(execution_price * qty) / max(leverage, 1)
            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"OPEN_{side}",
                "side": side,
                "price": round(execution_price, 8),
                "qty": qty,
                "amount": round(actual_margin, 8),
                "fee": round(fee, 4),
                "pnl": 0.0,
                "status": "OPEN",
                "leverage": leverage,
                "signal_score": signal_score,
                "reason": reason,
                "sl": sl_price,
                "tp": tp_price,
                # monitoring fields
                **({
                    "projected_net_rr": (lambda rp: (compute_net_reward_risk(execution_price, sl_price, rp)[0] if rp and rp > 0 else None))(float(entry_context.get("bounce_target_pct") or entry_context.get("profit_room_pct") or 0.0)),
                    "profit_room_pct": float(entry_context.get("profit_room_pct") or entry_context.get("bounce_target_pct") or 0.0),
                } if entry_context else {}),
                "exchange_order_id": entry_order_id,
                **entry_context,
            })
            await self.refresh(force=True)
            sl_label = "SL" if ENABLE_EXCHANGE_INITIAL_STOP_LOSS else "本地SL觀察線"
            self.log(
                f"🚀 Binance Testnet 開倉成功 [{side}] {symbol} @ "
                f"{execution_price:.6f} ({leverage}x，{sl_label}={sl_price}, TP={tp_price})",
                "SUCCESS",
            )
            return True
        except Exception as exc:
            self.log(
                f"🛑 Binance Testnet 開倉收尾失敗 {symbol}："
                f"{type(exc).__name__}: {exc}",
                "DANGER",
            )
            await self.refresh(force=True)
            return False

    async def place_limit_entry(
        self,
        symbol: str,
        side: str,
        target_price: float,
        amount_usdt: float,
        sl: float,
        tp: float,
        reason: str,
        atr: float = 0.0,
        leverage: int = None,
        signal_score: int = None,
        post_only: bool = True,
        entry_context: dict = None,
        timeframe: str = "3m",
    ) -> bool:
        """反轉確認後掛短效限價單；預設 GTX/Post-Only，避免確認後又追價。
        post_only=False 代表要求立即成交，改走 open_position() 市價單，不建立
        會在 15 秒後被撤銷的 GTC 限價單。

        ✅ 修正：若訊號分數達 90 分以上（強勢訊號），強制將 post_only 設為 False，
        改以市價單（open_position）立即成交進場，避免價格快速拉升時錯過行情。
        """
        # ✅ 修正：配合突破確認後限價回踩優化，90 分以上也完全恢復使用限價單，移除先前強制轉市價的設定。

        # DCA 分批掛單處理
        entry_ctx = dict(entry_context or {})
        is_dca_call = "dca_stage" in entry_ctx
        if ENABLE_DCA_LIMIT and not is_dca_call:
            entry_ctx["dca_stage"] = 1
            entry_ctx["dca_base_price"] = float(target_price)
            entry_ctx["dca_original_amount"] = float(amount_usdt)
            amount_usdt = amount_usdt / 3.0

        if symbol in self.positions:
            held = self.positions[symbol]
            held_mode = str(
                held.get("entry_mode")
                or self.position_meta.get(symbol, {}).get("entry_mode")
                or ""
            ).upper()
            dca_stage = entry_ctx.get("dca_stage")
            valid_dca_top_up = bool(
                ENABLE_DCA_LIMIT
                and is_dca_call
                and isinstance(dca_stage, (int, float))
                and int(dca_stage) >= 2
                and str(held.get("side") or "").upper() == str(side or "").upper()
                and held_mode != "CHANNEL_SWING"
            )
            if not valid_dca_top_up:
                return False
        elif symbol in self.closing_lock or symbol in self.pending_limit_orders:
            return False

        if not post_only:
            return await self.open_position(
                symbol=symbol, side=side, price=target_price,
                amount_usdt=amount_usdt, sl=sl, tp=tp, reason=reason,
                atr=atr, leverage=leverage, signal_score=signal_score,
                entry_context=entry_ctx,
            )
        if signal_score is not None and signal_score < MIN_OPEN_SIGNAL_SCORE:
            self.log(
                f"🛑 {symbol} 訊號分數 {signal_score} 低於 {MIN_OPEN_SIGNAL_SCORE} 分下限，拒絕掛單",
                "WARNING",
            )
            return False

        current_price = float(self.tickers.get(symbol) or 0.0)
        if current_price > 0:
            from core.config import get_maker_limit_offset_pct, MAKER_LIMIT_ORDER_MIN_OFFSET_PCT
            offset_pct = get_maker_limit_offset_pct(current_price, float(atr or 0.0), timeframe=timeframe)
            if side == "LONG":
                min_price = current_price * (1.0 - offset_pct)
                max_price = current_price * (1.0 - MAKER_LIMIT_ORDER_MIN_OFFSET_PCT)
                if target_price < min_price or target_price > max_price:
                    self.log(
                        f"🛑 {symbol} 多單掛單超出合理回踩區：目標價 {target_price:.8g} 不在 {min_price:.8g}～{max_price:.8g} 之間，拒絕掛單（週期={timeframe}, ATR={atr:.6g}, offset={offset_pct:.4%}）",
                        "WARNING",
                    )
                    return False
            elif side == "SHORT":
                min_price = current_price * (1.0 + MAKER_LIMIT_ORDER_MIN_OFFSET_PCT)
                max_price = current_price * (1.0 + offset_pct)
                if target_price < min_price or target_price > max_price:
                    self.log(
                        f"🛑 {symbol} 空單掛單超出合理回踩區：目標價 {target_price:.8g} 不在 {min_price:.8g}～{max_price:.8g} 之間，拒絕掛單（週期={timeframe}, ATR={atr:.6g}, offset={offset_pct:.4%}）",
                        "WARNING",
                    )
                    return False
        # 見 open_position() 同一道防線的說明：amount_usdt<=0 時 qty 會是 0，
        # 直接送進 exchange.amount_to_precision() 會炸出未捕捉的交易所例外，
        # 拖垮整個主迴圈，這裡提前擋掉。
        if amount_usdt <= 0:
            self.log(f"🛑 {symbol} 掛單金額為 0，拒絕掛單", "WARNING")
            return False
        try:
            validate_sl_tp_pair(target_price, side, sl, tp)
        except ValueError as exc:
            self.log(f"🛑 {symbol} 進場前 SL/TP 驗證失敗：{exc}", "WARNING")
            return False
        await self._ensure_markets()
        # 現價 Post-Only 必須留在 maker 一側：多單最多掛最佳買價，空單
        # 最少掛最佳賣價。回踩目標若本來更保守，則維持原目標不追價。
        if post_only and hasattr(self.exchange, "fetch_order_book"):
            try:
                book = await self.exchange.fetch_order_book(symbol, limit=5)
                if side == "LONG" and book.get("bids"):
                    target_price = min(float(target_price), float(book["bids"][0][0]))
                elif side == "SHORT" and book.get("asks"):
                    target_price = max(float(target_price), float(book["asks"][0][0]))
            except Exception:
                # 深度查詢失敗仍可依呼叫端價格送 GTX；交易所若判定會吃單，
                # 會直接拒絕而不會意外變成 taker。
                pass
        leverage = leverage or (
            get_signal_leverage(symbol, signal_score)
            if signal_score is not None
            else get_leverage(symbol)
        )
        order_side = "buy" if side == "LONG" else "sell"
        qty = float(self.exchange.amount_to_precision(
            symbol,
            (amount_usdt * leverage) / max(target_price, 1e-12),
        ))
        if qty <= 0:
            self.log(f"🛑 {symbol} 掛單數量低於交易所最小精度", "WARNING")
            return False

        # price_to_precision 純本地格式化（不會打 API），在 try 之外先算好，
        # 確保就算下面 _prepare_leverage/create_order 逾時，except 區塊裡
        # 查詢孤兒單要用的 price_str 一定已經有值，不會是未賦值狀態
        # （實測曾在 _prepare_leverage 逾時時觸發 UnboundLocalError，把
        # 整個主迴圈拖垮）。
        price_str = self.exchange.price_to_precision(symbol, target_price)
        try:
            await self._prepare_leverage(symbol, leverage)
            order_params = {"timeInForce": "GTX" if post_only else "GTC"}
            order = await self.exchange.create_order(
                symbol, "limit", order_side, qty, float(price_str),
                order_params,
            )
        except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
            # 逾時（例如幣安 -1007："執行狀態未知"）不能直接當失敗結束——
            # 單子有可能其實已經在交易所端掛成功，只是回應沒送到。若貿然
            # 回傳 False，主迴圈下一輪（5秒後）會用同一份訊號重新掛單，
            # 在交易所端疊出好幾張不受 pending_limit_orders 追蹤的孤兒
            # 限價單。掛完後主動查詢交易所目前掛單，撈到吻合的單就接手
            # 追蹤，查無此單才真的算失敗。
            order = await self._recover_order_after_timeout(symbol, order_side, price_str)
            if order is None:
                self.log(
                    f"🛑 {symbol} 限價掛單失敗（逾時且查無成交紀錄）：{type(exc).__name__}: {exc}",
                    "WARNING",
                )
                return False
            self.log(f"✅ {symbol} 限價掛單逾時但查詢確認已成功掛上，接手追蹤", "SUCCESS")
        except Exception as exc:
            self.log(
                f"🛑 {symbol} 限價掛單失敗：{type(exc).__name__}: {exc}",
                "WARNING",
            )
            return False

        self.pending_limit_orders[symbol] = {
            "order_id": order.get("id"),
            "side": side,
            "qty": qty,
            "target_price": float(price_str),
            "amount_usdt": amount_usdt,
            "sl": sl,
            "tp": tp,
            "reason": reason,
            "atr": atr,
            "leverage": leverage,
            "signal_score": signal_score,
            "placed_at": time.time(),
            "post_only": post_only,
            "entry_context": {
                key: value for key, value in entry_ctx.items()
                if key in ENTRY_CONTEXT_KEYS
            },
        }
        if self._pending_retry_streak.get(symbol, 0) == 0:
            dca_note = f" (DCA 階 {entry_ctx['dca_stage']})" if "dca_stage" in entry_ctx else ""
            self.log(
                f"📝 [短效 Maker 限價掛單] {symbol} {side} @ {price_str}（{leverage}x）{dca_note}，等待成交",
                "INFO",
            )
        return True

    async def check_pending_limit_orders(self) -> None:
        """每輪主迴圈呼叫：檢查所有限價掛單狀態，成交就補建保護單並記錄
        交易；被交易所取消/拒絕的直接清掉追蹤。超時/條件變差的主動撤單
        判斷交給 engine.py（需要策略/K線資料才能重新驗證條件），這裡只
        處理「有沒有成交」。"""
        for symbol, info in list(self.pending_limit_orders.items()):
            try:
                order_status = await self.exchange.fetch_order(info["order_id"], symbol)
            except Exception as exc:
                self.log(
                    f"⚠️ {symbol} 查詢限價單狀態失敗：{type(exc).__name__}: {exc}",
                    "WARNING",
                )
                continue

            status = order_status.get("status")
            filled_qty = float(order_status.get("filled") or 0.0)
            close_side = "sell" if info["side"] == "LONG" else "buy"

            if status == "closed" and filled_qty > 0:
                execution_price = float(order_status.get("average") or info["target_price"])
                del self.pending_limit_orders[symbol]
                # 提前佔用孤兒保護標記：限價單成交後到 _finalize_new_position
                # 真正建好保護單這段期間，主迴圈或網頁輪詢的 update_positions()
                # 可能會搶先看到「有持倉但還沒保護單」而觸發孤兒保護機制，
                # 跟這裡疊出重複止損止盈單。先佔位讓孤兒保護機制略過這個
                # symbol，交給 _finalize_new_position 統一處理。
                self._orphan_protection_attempted.add(symbol)
                self._pending_retry_streak.pop(symbol, None)
                await self._finalize_new_position(
                    symbol, info["side"], execution_price, filled_qty,
                    info["target_price"], info["sl"], info["tp"], info["reason"],
                    info["atr"], info["leverage"], info["signal_score"], close_side,
                    info["order_id"], info["amount_usdt"],
                    entry_context=info.get("entry_context"),
                )
            elif status in ("canceled", "rejected", "expired"):
                del self.pending_limit_orders[symbol]
                if filled_qty > 0:
                    # 部分成交後被取消：剩餘數量不會再成交了，直接用已成交
                    # 的量入場，不留一個「半個部位」在系統外面沒人管。
                    execution_price = float(order_status.get("average") or info["target_price"])
                    self.log(
                        f"⚠️ {symbol} 限價單部分成交（{filled_qty}/{info['qty']}）後停止，"
                        f"以實際成交量入場",
                        "WARNING",
                    )
                    self._orphan_protection_attempted.add(symbol)
                    self._pending_retry_streak.pop(symbol, None)
                    await self._finalize_new_position(
                        symbol, info["side"], execution_price, filled_qty,
                        info["target_price"], info["sl"], info["tp"], info["reason"],
                        info["atr"], info["leverage"], info["signal_score"], close_side,
                        info["order_id"], info["amount_usdt"],
                        entry_context=info.get("entry_context"),
                    )
                else:
                    self.log(f"↩️ {symbol} 限價單已被取消/拒絕，放棄本次進場", "INFO")

    async def cancel_pending_limit(self, symbol: str, reason: str) -> None:
        """主動撤單（超時或條件變差），engine.py 呼叫。

        不設連續失敗冷卻：同一 symbol 可以無限次重新掛單，每次掛單前
        （place_limit_entry 的 MIN_OPEN_SIGNAL_SCORE 檢查）都要求分數
        重新達標，未成交/條件變差就撤單，下一輪分數夠不夠再決定要不要
        重掛，不用因為連續撤單幾次就強制冷卻一段時間錯過真正的訊號。
        """
        info = self.pending_limit_orders.get(symbol)
        if not info:
            return
        try:
            await self.exchange.cancel_order(info["order_id"], symbol)
        except Exception as exc:
            # 可能剛好在這個瞬間「完全」成交了，撤單會失敗；不主動刪追蹤
            # 記錄，讓下一輪 check_pending_limit_orders() 去確認實際狀態
            # 並收尾。
            self.log(
                f"⚠️ {symbol} 撤銷限價單失敗（可能剛好成交）：{type(exc).__name__}: {exc}",
                "WARNING",
            )
            return

        # 幣安允許取消「部分成交」單剩餘未成交的數量，這種情況 cancel_order
        # 不會丟例外，但撤單生效前那一刻可能已經有一部分數量真的成交在
        # 交易所了。撤單成功後一定要重新查一次實際成交量，不然這部分持倉
        # 會繞過 _finalize_new_position()，缺少策略算好的止損止盈距離，
        # 只能等孤兒保護機制事後用固定寬距離補一個粗糙止損（實測
        # DOT/USDT 07/28 15:03 就是這樣：策略算出來的止損距離現價只有
        # 約 0.5%，孤兒保護補的卻是 3% 開外，形同整個持倉期間沒有真正
        # 合理的風控距離，直到保本鎖第一次觸發就被正常反彈洗出場）。
        try:
            order_status = await self.exchange.fetch_order(info["order_id"], symbol)
            filled_qty = float(order_status.get("filled") or 0.0)
        except Exception:
            filled_qty = 0.0

        del self.pending_limit_orders[symbol]

        if filled_qty > 0:
            execution_price = float(order_status.get("average") or info["target_price"])
            close_side = "sell" if info["side"] == "LONG" else "buy"
            self.log(
                f"⚠️ {symbol} 撤單瞬間已部分成交（{filled_qty}/{info['qty']}），"
                f"以實際成交量入場並補上正確止損止盈",
                "WARNING",
            )
            self._orphan_protection_attempted.add(symbol)
            self._pending_retry_streak.pop(symbol, None)
            await self._finalize_new_position(
                symbol, info["side"], execution_price, filled_qty,
                info["target_price"], info["sl"], info["tp"], info["reason"],
                info["atr"], info["leverage"], info["signal_score"], close_side,
                info["order_id"], info["amount_usdt"],
                entry_context=info.get("entry_context"),
            )
            return

        streak = self._pending_retry_streak.get(symbol, 0)
        if streak == 0:
            self.log(f"↩️ [限價單撤銷] {symbol} {info['side']}：{reason}", "INFO")
        self._pending_retry_streak[symbol] = streak + 1

    async def close_position(
        self, symbol: str, current_price: float, close_reason: str, is_manual: bool = False
    ) -> bool:
        if symbol not in self.positions or symbol in self.closing_lock:
            return False
        # 若全域關閉自動停損，非手動呼叫一律拒絕自動平倉
        if DISABLE_STOP_LOSS and not is_manual:
            reject_key = (symbol, close_reason)
            now_ts = time.time()
            if now_ts - self._auto_close_reject_logged_at.get(reject_key, 0.0) >= 30.0:
                self._auto_close_reject_logged_at[reject_key] = now_ts
                self.log(f"⏸️ [自動停損已停用] 拒絕自動平倉 {symbol} ({close_reason})", "INFO")
            return False
        if not is_manual and ONLY_CLOSE_ON_PROFIT:
            position = self.positions[symbol]
            side = position["side"]
            entry_price = position["entry_price"]
            qty = position["qty"]
            exec_close_price = current_price * (1 - SLIPPAGE_PCT) if side == "LONG" else current_price * (1 + SLIPPAGE_PCT)
            raw_pnl = (
                (exec_close_price - entry_price) * qty if side == "LONG"
                else (entry_price - exec_close_price) * qty
            )
            open_fee = entry_price * qty * TAKER_FEE_RATE
            close_fee = exec_close_price * qty * TAKER_FEE_RATE
            total_fee = open_fee + close_fee
            net_pnl = raw_pnl - total_fee
            is_profit_enough = (
                net_pnl >= ONLY_CLOSE_ON_PROFIT_MIN_NET_USDT
                and raw_pnl >= total_fee * CLOSE_ON_PROFIT_MIN_PNL_TO_FEE_RATIO
            )
            if not is_profit_enough:
                self.log(
                    f"Attempted to close position {symbol} {side} ({close_reason}), but skipped: "
                    f"Net PnL is {net_pnl:.4f} USDT, Raw PnL is {raw_pnl:.4f} USDT (Fee: {total_fee:.4f} USDT). "
                    f"Required: Net PnL >= {ONLY_CLOSE_ON_PROFIT_MIN_NET_USDT} USDT and Raw PnL >= {CLOSE_ON_PROFIT_MIN_PNL_TO_FEE_RATIO}x Fee. "
                    f"Entry Price: {entry_price:.6g}, Current Price: {current_price:.6g}, Qty: {qty:.6g}.",
                    "WARNING"
                )
                return False
        # ✅ 修正：若是手動平倉，直接跳過自動冷卻計時器，避免用戶手動平倉卡住
        _now = time.time()
        if not is_manual and _now < self._close_retry_after.get(symbol, 0.0):
            return False
        self.closing_lock.add(symbol)
        self.last_closed_at[symbol] = _now
        position = dict(self.positions[symbol])
        try:
            await self._cancel_all_orders(symbol)
            close_side = "sell" if position["side"] == "LONG" else "buy"
            order = await self.exchange.create_order(
                symbol,
                "market",
                close_side,
                position["qty"],
                None,
                {"reduceOnly": True, "newOrderRespType": "RESULT"},
            )
            execution_price = float(order.get("average") or current_price)
            raw_pnl = (
                (execution_price - position["entry_price"]) * position["qty"]
                if position["side"] == "LONG"
                else (position["entry_price"] - execution_price) * position["qty"]
            )
            open_fee = position["entry_price"] * position["qty"] * TAKER_FEE_RATE
            close_fee = execution_price * position["qty"] * TAKER_FEE_RATE
            total_fee = open_fee + close_fee
            net_pnl = raw_pnl - total_fee
            self.realized_pnl += net_pnl
            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"CLOSE_{position['side']}",
                "side": position["side"],
                "price": execution_price,
                "qty": position["qty"],
                "amount": position.get("margin", 0.0),
                "fee": round(total_fee, 4),
                "pnl": round(net_pnl, 4),
                "status": "CLOSED",
                "reason": close_reason,
                "exchange_order_id": order.get("id"),
                **{key: position.get(key) for key in ENTRY_CONTEXT_KEYS},
            })
            self.position_meta.pop(symbol, None)
            self.positions.pop(symbol, None)
            self.pending_limit_orders.pop(symbol, None)
            await self.refresh(force=True)
            self.log(
                f"🏁 Binance Testnet 平倉 [{position['side']}] {symbol} @ "
                f"{execution_price:.6f} | 淨損益: {net_pnl:+.2f} USDT ({close_reason})",
                "SUCCESS" if net_pnl >= 0 else "DANGER",
            )
            if self.on_trade_closed:
                try:
                    self.on_trade_closed()
                except Exception:
                    pass
            return True
        except Exception as exc:
            # ✅ 修正 Bug2：失敗後登記冷卻時間，30 秒後主迴圈才允許再試
            self._close_retry_after[symbol] = time.time() + 30.0
            self.log(
                f"🚨 Binance Testnet 平倉失敗 {symbol}（30 秒後自動重試）："
                f"{type(exc).__name__}: {exc}",
                "DANGER",
            )
            return False
        else:
            # 平倉成功，清除冷卻記錄
            self._close_retry_after.pop(symbol, None)
        finally:
            self.closing_lock.discard(symbol)

    async def partial_close_position(
        self, symbol: str, current_price: float, close_reason: str, fraction: float = 0.5
    ) -> bool:
        if symbol not in self.positions or symbol in self.closing_lock:
            return False
        if not 0.0 < float(fraction) < 1.0:
            return False
        self.closing_lock.add(symbol)
        position = dict(self.positions[symbol])
        meta = self.position_meta.get(symbol, {})
        try:
            qty = position["qty"]
            close_qty = float(self.exchange.amount_to_precision(symbol, qty * fraction))
            if close_qty <= 0:
                return False

            # 1. 撤銷所有現有止損止盈單
            await self._cancel_all_orders(symbol)
            self.pending_limit_orders.pop(symbol, None)

            # 2. 市價單平倉一半
            close_side = "sell" if position["side"] == "LONG" else "buy"
            order = await self.exchange.create_order(
                symbol,
                "market",
                close_side,
                close_qty,
                None,
                {"reduceOnly": True, "newOrderRespType": "RESULT"},
            )
            execution_price = float(order.get("average") or current_price)

            # 3. 計算部分平倉的損益與手續費
            raw_pnl = (
                (execution_price - position["entry_price"]) * close_qty
                if position["side"] == "LONG"
                else (position["entry_price"] - execution_price) * close_qty
            )
            open_fee = position["entry_price"] * close_qty * TAKER_FEE_RATE
            close_fee = execution_price * close_qty * TAKER_FEE_RATE
            total_fee = open_fee + close_fee
            net_pnl = raw_pnl - total_fee
            self.realized_pnl += net_pnl

            # 4. 記錄部分平倉交易
            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"PARTIAL_CLOSE_{position['side']}",
                "side": position["side"],
                "price": execution_price,
                "qty": close_qty,
                "amount": position.get("margin", 0.0) * fraction,
                "fee": round(total_fee, 4),
                "pnl": round(net_pnl, 4),
                "status": "PARTIAL_CLOSED",
                "reason": close_reason,
                "exchange_order_id": order.get("id"),
                **{key: position.get(key) for key in ENTRY_CONTEXT_KEYS},
            })

            # 5. 更新本地持倉狀態
            remaining_qty = float(self.exchange.amount_to_precision(symbol, qty - close_qty))
            if remaining_qty > 0:
                position["qty"] = remaining_qty
                position["margin"] = position.get("margin", 0.0) * (1 - fraction)
                self.positions[symbol] = position
                meta["is_half_closed"] = True
                self.position_meta[symbol] = meta

                # 6. 重建剩餘部位的保護單
                sl_price = position.get("sl", 0.0)
                tp_price = position.get("tp", 0.0)
                if sl_price > 0 and ENABLE_EXCHANGE_INITIAL_STOP_LOSS:
                    await self._create_protection_order(
                        symbol, close_side, "STOP_MARKET", remaining_qty, sl_price,
                    )
                if tp_price > 0 and not DISABLE_TAKE_PROFIT:
                    await self._create_protection_order(
                        symbol, close_side, "TAKE_PROFIT_MARKET", remaining_qty, tp_price
                    )
                self.log(
                    f"🏁 Binance Testnet 部分平倉 [{position['side']}] {symbol} 減倉 {close_qty} @ "
                    f"{execution_price:.6g} | 淨損益: {net_pnl:+.2f} USDT (剩餘數量: {remaining_qty})",
                    "SUCCESS" if net_pnl >= 0 else "DANGER",
                )
            else:
                self.position_meta.pop(symbol, None)
                self.positions.pop(symbol, None)
                self.log(
                    f"🏁 Binance Testnet 部分平倉後無剩餘倉位 {symbol}",
                    "SUCCESS"
                )

            await self.refresh(force=True)
            self.save_state()

            if self.on_trade_closed:
                try:
                    self.on_trade_closed()
                except Exception:
                    pass
            return True
        except Exception as exc:
            self.log(
                f"🚨 Binance Testnet 部分平倉失敗 {symbol}："
                f"{type(exc).__name__}: {exc}",
                "DANGER",
            )
            return False
        finally:
            self.closing_lock.discard(symbol)

    async def trail_stop_loss(
        self, symbol: str, new_sl_price: float, mark_profit_locked: bool = True
    ) -> bool:
        """移動市價止損：取消舊止損單，在新位置重新掛保護單。
        止損線只往有利方向移動，呼叫端負責確認 new_sl_price 已經比 current_sl 更好。
        mark_profit_locked 預設True（真正的移動停利，止損已經鎖到保本以上）；
        軟性警訊收緊止損只是把止損往進場價方向拉近、不保證已經是正的，
        呼叫時要傳 mark_profit_locked=False，避免誤標記 is_breakeven_moved
        （會讓平倉原因誤顯示「移動止利」，還會讓5m出場防線誤判成「已經
        保護過」而提早放行）。"""
        if symbol not in self.positions or symbol in self.closing_lock:
            return False
        position = self.positions[symbol]
        meta = self.position_meta.get(symbol, {})
        tp_price = float(meta.get("tp") or position.get("tp") or 0.0)
        entry_price = float(position.get("entry_price") or meta.get("entry_price") or 0.0)
        if tp_price > 0 and entry_price > 0:
            try:
                validate_sl_tp_pair(
                    entry_price, position["side"], new_sl_price, tp_price,
                    allow_profit_lock=True,
                )
            except ValueError:
                self.log(
                    f"🛑 {symbol} 移動止損更新失敗：SL/TP 方向或風報比不合法，忽略更新（SL={new_sl_price}，TP={tp_price}）",
                    "WARNING",
                )
                return False
        close_side = "sell" if position["side"] == "LONG" else "buy"
        qty = position["qty"]
        try:
            new_sl_price = float(self.exchange.price_to_precision(symbol, new_sl_price))
            # 取消所有現有保護單
            await self._cancel_all_orders(symbol)
            # 重新掛新止損單
            await self._create_protection_order(
                symbol, close_side, "STOP_MARKET", qty, new_sl_price,
            )
            # 如果止利仍啟用，同步重建止利單
            if tp_price > 0 and not DISABLE_TAKE_PROFIT:
                await self._create_protection_order(
                    symbol, close_side, "TAKE_PROFIT_MARKET", qty, tp_price
                )
            # 更新本地 meta 和 position 的 sl 紀錄
            meta["sl"] = new_sl_price
            if mark_profit_locked:
                meta["is_breakeven_moved"] = True
                position["is_breakeven_moved"] = True
            self.position_meta[symbol] = meta
            position["sl"] = new_sl_price
            self.positions[symbol] = position
            self.save_state()
            self.log(
                f"🔼 [移動止損] {symbol} 止損線移至 {new_sl_price:.6g}",
                "INFO",
            )
            return True
        except Exception as exc:
            self.log(
                f"⚠️ [移動止損] {symbol} 更新止損單失敗：{type(exc).__name__}: {exc}",
                "WARNING",
            )
            return False
