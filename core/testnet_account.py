import asyncio
import json
import os
import time
import ccxt.async_support as ccxt
from datetime import datetime
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.config import (
    BINANCE_API_KEY,
    BINANCE_SECRET,
    TAKER_FEE_RATE,
    MAX_DAILY_LOSS_PCT,
    MIN_OPEN_SIGNAL_SCORE,
    DEFAULT_SYMBOLS,
    STOP_LIMIT_SLIPPAGE_GUARD_PCT,
    STOP_LIMIT_UNFILLED_TIMEOUT_SEC,
    ENABLE_TRAILING_STOP,
    TRAILING_TRIGGER_PCT,
    CONTRARIAN_TRAILING_TRIGGER_PCT,
    TRAILING_PULLBACK_PCT,
    NET_PROFIT_GUARANTEE_BUFFER,
    get_trailing_pullback_pct,
    PROFIT_ALERT_GIVEBACK_RATIO,
    get_leverage,
    get_signal_leverage,
    DISABLE_TAKE_PROFIT,
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
)
from core.strategy import compute_sl_tp_distance
from core.notifier import notify_email


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "testnet_account.json")
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
ENTRY_CONTEXT_KEYS = (
    "btc_regime_at_entry", "btc_direction_1h_at_entry", "btc_score_penalty",
    "btc_allocation_factor", "btc_pre_penalty_score",
    "raw_signal_score", "btc_adjusted_score", "history_adjusted_score",
    "history_score_multiplier", "pullback_confirmation_score", "entry_mode",
    "is_contrarian_bottom_buy",
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
        # 限價止損（STOP，觸發後轉「觸發價±STOP_LIMIT_SLIPPAGE_GUARD_PCT」
        # 範圍內的限價單而非市價單）可能因為價格跳空滑出緩衝範圍而遲遲
        # 無法成交，導致部位裸奔。記錄「標記價開始穿越止損」的時間點，
        # 超過 STOP_LIMIT_UNFILLED_TIMEOUT_SEC 秒還沒平倉就強制市價出場，
        # 見 update_positions() 的檢查。
        self._stop_breach_since: Dict[str, float] = {}
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
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def log(self, message: str, level: str = "INFO") -> None:
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
        hit = loss_pct >= MAX_DAILY_LOSS_PCT
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

    async def initialize(self) -> None:
        if not self.credentials_configured():
            raise RuntimeError("8006 Testnet API Key 尚未設定")
        await self.exchange.load_markets()
        self._markets_loaded = True
        await self._cancel_orphan_entry_orders()
        await self.refresh(force=True)

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

        reasons = {
            "TP": "Binance Testnet 止盈單成交 (Take-Profit)",
            "SL": "Binance Testnet 止損單成交 (Stop-Loss)",
        }
        return exit_type or "OTHER", reasons.get(exit_type, "Binance Testnet 外部平倉（類型未識別）")

    async def _record_external_close(self, symbol: str, position: dict) -> None:
        if symbol in self.closing_lock:
            return
        self.closing_lock.add(symbol)
        self.last_closed_at[symbol] = time.time()
        self._stop_breach_since.pop(symbol, None)
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
                and symbol not in self._orphan_protection_attempted
                and symbol not in self.pending_limit_orders
            ):
                self._orphan_protection_attempted.add(symbol)
                await self._create_orphan_protection(symbol, pos, meta)

            old_sl = pos.get("sl", 0.0)
            now_ts = time.time()

            # 限價止損（STOP，觸發後轉「觸發價±STOP_LIMIT_SLIPPAGE_GUARD_PCT」
            # 範圍內的限價單）可能因為價格跳空滑出緩衝範圍而遲遲無法成交：
            # 標記價已經穿越止損價，但持倉還在，代表限價單掛著沒成交。
            # 超過 STOP_LIMIT_UNFILLED_TIMEOUT_SEC 秒還是這樣，直接強制
            # 市價平倉，不讓部位無限期裸奔等一張可能永遠不會成交的限價單。
            if old_sl > 0:
                breached = (
                    (side == "LONG" and mark_p <= old_sl)
                    or (side == "SHORT" and mark_p >= old_sl)
                )
                if breached:
                    breach_start = self._stop_breach_since.setdefault(symbol, now_ts)
                    if now_ts - breach_start >= STOP_LIMIT_UNFILLED_TIMEOUT_SEC:
                        self._stop_breach_since.pop(symbol, None)
                        self.log(
                            f"🚨 {symbol} 限價止損觸發後超過{STOP_LIMIT_UNFILLED_TIMEOUT_SEC:.0f}秒"
                            f"未成交（標記價 {mark_p:.6f} 已穿越止損 {old_sl}），強制市價平倉",
                            "DANGER",
                        )
                        await self.close_position(symbol, curr_p, "限價止損逾時未成交，強制市價平倉")
                        continue
                else:
                    self._stop_breach_since.pop(symbol, None)

            # ── 移動停利 / 原生 Trailing Stop 三階段升級 ──
            if ENABLE_TRAILING_STOP:
                atr_value = meta.get("atr", entry_p * 0.015)
                atr_pct = atr_value / entry_p if entry_p > 0 else 0.015
                pnl_pct = (mark_p - entry_p) / entry_p if side == "LONG" else (entry_p - mark_p) / entry_p
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
                    if profit_in_atr >= TRAILING_TIER1_TRIGGER_ATR_MULT and not meta.get("is_breakeven_moved"):
                        if side == "LONG":
                            breakeven_sl = entry_p * (1.0 + 0.0015)
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
                                        symbol, close_side_trail, "STOP_MARKET", qty_trail, new_sl_price,
                                        limit_price=self._stop_limit_price(new_sl_price, close_side_trail)
                                    )
                                    if pos.get("tp"):
                                        pos["tp"] = 0.0
                                        meta["tp"] = 0.0
                                except Exception as e:
                                    self.log(f"⚠️ {symbol} 設置本地保本單失敗: {e}", "WARNING")
                        else:  # SHORT
                            breakeven_sl = entry_p * (1.0 - 0.0015)
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
                                        symbol, close_side_trail, "STOP_MARKET", qty_trail, new_sl_price,
                                        limit_price=self._stop_limit_price(new_sl_price, close_side_trail)
                                    )
                                    if pos.get("tp"):
                                        pos["tp"] = 0.0
                                        meta["tp"] = 0.0
                                except Exception as e:
                                    self.log(f"⚠️ {symbol} 設置本地保本單失敗: {e}", "WARNING")

                    # 2. 後段：升級至交易所原生毫秒級 Trailing Stop (達 Tier 2 或 Tier 3)
                    target_tier = 0
                    if profit_in_atr >= TRAILING_TIER3_TRIGGER_ATR_MULT and current_tier < 3:
                        target_tier = 3
                    elif profit_in_atr >= TRAILING_TIER2_TRIGGER_ATR_MULT and current_tier < 2:
                        target_tier = 2

                    if target_tier > 0:
                        tier_labels = {2: "鎖利", 3: "極致追蹤"}
                        callback_rate = self._compute_callback_rate(atr_pct, target_tier, highest_pnl=highest_pnl)
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
                            )
                            actual_callback = result.get("callbackRate", callback_rate)
                            meta["native_trailing_tier"] = target_tier
                            meta["native_trailing_callback"] = actual_callback
                            # 接管後，清空本地 SL/TP 條件記錄
                            meta["sl"] = 0.0
                            pos["sl"] = 0.0
                            if pos.get("tp"):
                                pos["tp"] = 0.0
                                meta["tp"] = 0.0
                            self.log(
                                f"🎯 [原生 Trailing Tier{target_tier} – {tier_labels[target_tier]}] "
                                f"{symbol} 浮盈 {profit_in_atr:.1f} ATR ({pnl_pct:.2%}) | "
                                f"已取消本地條件單，升級掛載交易所原生 TRAILING_STOP_MARKET (callback={actual_callback}%)",
                                "SUCCESS",
                            )
                        except Exception as e:
                            self.log(
                                f"⚠️ {symbol} 升級掛載原生 Trailing Stop (Tier{target_tier}) 失敗: "
                                f"{type(e).__name__}: {e}",
                                "WARNING",
                            )

                else:
                    # ── 路徑 B：舊版百分比制輪詢移動止利（Testnet / fallback）──
                    # 逆勢承接單（MA7_ContrarianBottomBuy）用更早/更低的觸發
                    # 門檻，一旦有利潤就盡快接手保護，不限制往上空間。
                    trailing_trigger = (
                        CONTRARIAN_TRAILING_TRIGGER_PCT if meta.get("is_contrarian_bottom_buy")
                        else TRAILING_TRIGGER_PCT
                    )
                    if highest_pnl >= trailing_trigger:
                        opened_at = meta.get("open_timestamp") or pos.get("open_timestamp") or now_ts
                        pullback = get_trailing_pullback_pct(highest_pnl, opened_at)
                        if side == "LONG":
                            trail_sl = entry_p * (1.0 + highest_pnl * pullback)
                            npg_floor = entry_p * (1.0 + NET_PROFIT_GUARANTEE_BUFFER)
                            trail_sl = max(trail_sl, npg_floor)
                            new_sl_price = float(self.exchange.price_to_precision(symbol, trail_sl))
                            if new_sl_price > old_sl:
                                meta["sl"] = new_sl_price
                                pos["sl"] = new_sl_price
                                meta["is_breakeven_moved"] = True
                                pos["is_breakeven_moved"] = True
                                self.log(f"📈 [移動止利] {symbol} 無槓桿利潤峰值 {highest_pnl:.4%}，止利線推至 {new_sl_price}（回吐 {1-pullback:.0%} 平倉）", "SUCCESS")
                                try:
                                    await self._cancel_all_orders(symbol)
                                    await self._create_protection_order(
                                        symbol, close_side_trail, "STOP_MARKET", qty_trail, new_sl_price,
                                        limit_price=self._stop_limit_price(new_sl_price, close_side_trail)
                                    )
                                    if pos.get("tp"):
                                        pos["tp"] = 0.0
                                        meta["tp"] = 0.0
                                except Exception as e:
                                    self.log(f"⚠️ {symbol} 更新移動止利單失敗: {e}", "WARNING")
                        else:  # SHORT
                            trail_sl = entry_p * (1.0 - highest_pnl * pullback)
                            npg_ceiling = entry_p * (1.0 - NET_PROFIT_GUARANTEE_BUFFER)
                            trail_sl = min(trail_sl, npg_ceiling)
                            new_sl_price = float(self.exchange.price_to_precision(symbol, trail_sl))
                            if new_sl_price < old_sl or old_sl == 0.0:
                                meta["sl"] = new_sl_price
                                pos["sl"] = new_sl_price
                                meta["is_breakeven_moved"] = True
                                pos["is_breakeven_moved"] = True
                                self.log(f"📉 [移動止利] {symbol} 無槓桿利潤峰值 {highest_pnl:.4%}，止利線推至 {new_sl_price}（回吐 {1-pullback:.0%} 平倉）", "SUCCESS")
                                try:
                                    await self._cancel_all_orders(symbol)
                                    await self._create_protection_order(
                                        symbol, close_side_trail, "STOP_MARKET", qty_trail, new_sl_price,
                                        limit_price=self._stop_limit_price(new_sl_price, close_side_trail)
                                    )
                                    if pos.get("tp"):
                                        pos["tp"] = 0.0
                                        meta["tp"] = 0.0
                                except Exception as e:
                                    self.log(f"⚠️ {symbol} 更新移動止利單失敗: {e}", "WARNING")


            if (time.time() - pos.get("open_timestamp", time.time())) >= 86400:
                await self.close_position(symbol, curr_p, "時間過濾 (24h 無效震盪離場)")
                continue

            self.position_meta[symbol] = meta

        self.save_state()
        return self.unrealized_pnl

    async def _create_orphan_protection(self, symbol: str, pos: dict, meta: dict) -> None:
        side = pos["side"]
        entry_p = pos["entry_price"]
        atr = meta.get("atr") or entry_p * 0.015
        sl_distance, tp_distance = compute_sl_tp_distance(entry_p, atr)
        if side == "LONG":
            sl_price = float(self.exchange.price_to_precision(symbol, entry_p - sl_distance))
            tp_price = float(self.exchange.price_to_precision(symbol, entry_p + tp_distance)) if not DISABLE_TAKE_PROFIT else 0.0
        else:
            sl_price = float(self.exchange.price_to_precision(symbol, entry_p + sl_distance))
            tp_price = float(self.exchange.price_to_precision(symbol, entry_p - tp_distance)) if not DISABLE_TAKE_PROFIT else 0.0
        close_side = "sell" if side == "LONG" else "buy"
        try:
            await self._cancel_all_orders(symbol)
            await self._create_protection_order(
                symbol, close_side, "STOP_MARKET", pos["qty"], sl_price,
                limit_price=self._stop_limit_price(sl_price, close_side),
            )
            if not DISABLE_TAKE_PROFIT:
                await self._create_protection_order(symbol, close_side, "TAKE_PROFIT_MARKET", pos["qty"], tp_price)
            meta["sl"] = sl_price
            meta["tp"] = tp_price
            meta["atr"] = atr
            self.position_meta[symbol] = meta
            self.save_state()
            tp_msg = f" TP={tp_price}" if not DISABLE_TAKE_PROFIT else " (已停用初始止利)"
            self.log(
                f"🔧 [補建保護單] {symbol} 偵測到無止損保護持倉，已自動建立 SL={sl_price}{tp_msg}",
                "WARNING",
            )
        except Exception as exc:
            self.log(
                f"⚠️ {symbol} 補建保護單失敗：{type(exc).__name__}: {exc}",
                "WARNING",
            )

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
        limit_price: float = None,
    ) -> dict:
        """建立條件單。limit_price 有給值時，STOP_MARKET 改成限價止損
        （STOP，帶 price），觸發後轉成「觸發價±STOP_LIMIT_SLIPPAGE_GUARD_PCT」
        範圍內的限價單，而不是不管市價多差都吃單的市價單——實測 DOT/USDT
        保本鎖正確收緊止損後，觸發轉市價單滑價 0.66%，把理論上 +0.34 的
        小賺滑成 -2.30 的虧損。TAKE_PROFIT_MARKET 維持原本市價，profit-
        taking 滑價頂多少賺一點，不像止損滑價會擴大虧損那麼需要限制。"""
        params = {
            "algoType": "CONDITIONAL",
            "symbol": self._raw_symbol(symbol),
            "side": side.upper(),
            "quantity": self.exchange.amount_to_precision(symbol, qty),
            "triggerPrice": self.exchange.price_to_precision(symbol, trigger_price),
            "reduceOnly": "true",
            "workingType": "MARK_PRICE",
        }
        if limit_price is not None and order_type == "STOP_MARKET":
            params["type"] = "STOP"
            params["price"] = self.exchange.price_to_precision(symbol, limit_price)
        else:
            params["type"] = order_type
        return await self.exchange.request("algoOrder", "fapiPrivate", "POST", params)

    @staticmethod
    def _stop_limit_price(trigger_price: float, close_side: str) -> float:
        """算出限價止損的限價：買回補空單時願意多付一點（觸發價之上），
        賣出平多單時願意少賣一點（觸發價之下），緩衝之外寧可不成交。"""
        if close_side == "buy":
            return trigger_price * (1 + STOP_LIMIT_SLIPPAGE_GUARD_PCT)
        return trigger_price * (1 - STOP_LIMIT_SLIPPAGE_GUARD_PCT)

    @staticmethod
    def _compute_callback_rate(atr_pct: float, tier: int, highest_pnl: float = None) -> float:
        """根據進場時的 ATR% 與最高浮盈動態計算 Binance TRAILING_STOP_MARKET 的 callbackRate (%)。

        公式：base = atr_pct * 100 * NATIVE_TRAILING_ATR_RATE_FACTOR
        再依各 Tier 的上下限 clamp。

        安全防護：如果傳入最高浮盈 highest_pnl，則 callbackRate 不得超過最高浮盈的 40% (即最高利潤回吐不得超過 40%)，
        確保即使在小浮盈觸發時，扣除雙邊手續費與滑價後依然維持保本或微利，絕不轉虧。
        """
        base = atr_pct * 100.0 * NATIVE_TRAILING_ATR_RATE_FACTOR
        if tier == 1:
            rate = max(NATIVE_TRAILING_TIER1_CALLBACK_MIN,
                       min(NATIVE_TRAILING_TIER1_CALLBACK_MAX, base))
        elif tier == 2:
            rate = max(NATIVE_TRAILING_TIER2_CALLBACK_MIN,
                       min(NATIVE_TRAILING_TIER2_CALLBACK_MAX, base))
        else:  # Tier 3
            rate = max(NATIVE_TRAILING_TIER3_CALLBACK_MIN,
                       min(NATIVE_TRAILING_TIER3_CALLBACK_MAX, base))

        # ── 安全閥：回調幅度限制在最高浮盈的 40% 以內 ──
        if highest_pnl is not None and highest_pnl > 0:
            max_allowed_callback = highest_pnl * 100.0 * 0.4
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
    ) -> dict:
        """下 Binance 原生 TRAILING_STOP_MARKET 訂單。

        由交易所伺服器端以毫秒精度即時追蹤最高/最低標記價格，機器人斷線也
        不影響追蹤與觸發。callbackRate 由 ATR% 與最高浮盈動態計算（Tier1 最寬、Tier3 最緊）。

        activation_price（可選）：當標記價格達到此價位後才開始追蹤。
        """
        callback_rate = self._compute_callback_rate(atr_pct, tier, highest_pnl=highest_pnl)
        params = {
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
        result = await self.exchange.request("order", "fapiPrivate", "POST", params)
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
        if symbol in self.positions or symbol in self.closing_lock:
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
            sl_price = float(self.exchange.price_to_precision(symbol, adjusted_sl))
            tp_price = float(self.exchange.price_to_precision(symbol, adjusted_tp)) if not DISABLE_TAKE_PROFIT else 0.0
            atr_value = atr if atr > 0 else execution_price * 0.015
            try:
                # 限價單成交後，這裡跟主迴圈/網頁輪詢都可能同時偵測到「這個
                # symbol 有持倉但還沒保護單」而觸發 _create_orphan_protection，
                # 兩邊可能疊出重複的止損止盈單。先清一次掛單，確保接下來建的
                # 是唯一一組，不管是不是搶輸了孤兒保護機制一步。
                await self._cancel_all_orders(symbol)
                await self._create_protection_order(
                    symbol, close_side, "STOP_MARKET", qty, sl_price,
                    limit_price=self._stop_limit_price(sl_price, close_side),
                )
                if not DISABLE_TAKE_PROFIT:
                    await self._create_protection_order(
                        symbol, close_side, "TAKE_PROFIT_MARKET", qty, tp_price
                    )
            except Exception:
                await self._cancel_all_orders(symbol)
                await self._emergency_flatten(symbol, side, qty)
                raise

            fee = qty * execution_price * TAKER_FEE_RATE
            # 開倉時設好 SL/TP 之後就不再更動，只等價格碰到其中一個交易所
            # 保護單才平倉（回到最初版本的固定止損/止利方式，見 config.py
            # 的 STOP_LOSS_MULTIPLIER/TAKE_PROFIT_MULTIPLIER 註解）。
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
                "exchange_order_id": entry_order_id,
                **entry_context,
            })
            await self.refresh(force=True)
            self.log(
                f"🚀 Binance Testnet 開倉成功 [{side}] {symbol} @ "
                f"{execution_price:.6f} ({leverage}x，SL={sl_price}, TP={tp_price})",
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
    ) -> bool:
        """反轉確認後掛短效限價單；預設 GTX/Post-Only，避免確認後又追價。
        未成交、條件變差或超時由 engine.py 主動撤單。"""
        if symbol in self.positions or symbol in self.closing_lock or symbol in self.pending_limit_orders:
            return False
        if signal_score is not None and signal_score < MIN_OPEN_SIGNAL_SCORE:
            self.log(
                f"🛑 {symbol} 訊號分數 {signal_score} 低於 {MIN_OPEN_SIGNAL_SCORE} 分下限，拒絕掛單",
                "WARNING",
            )
            return False
        # 見 open_position() 同一道防線的說明：amount_usdt<=0 時 qty 會是 0，
        # 直接送進 exchange.amount_to_precision() 會炸出未捕捉的交易所例外，
        # 拖垮整個主迴圈，這裡提前擋掉。
        if amount_usdt <= 0:
            self.log(f"🛑 {symbol} 掛單金額為 0，拒絕掛單", "WARNING")
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
                key: value for key, value in dict(entry_context or {}).items()
                if key in ENTRY_CONTEXT_KEYS
            },
        }
        if self._pending_retry_streak.get(symbol, 0) == 0:
            self.log(
                f"📝 [短效 Maker 限價掛單] {symbol} {side} @ {price_str}（{leverage}x），等待成交",
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
        self, symbol: str, current_price: float, close_reason: str
    ) -> bool:
        if symbol not in self.positions or symbol in self.closing_lock:
            return False
        self.closing_lock.add(symbol)
        self.last_closed_at[symbol] = time.time()
        self._stop_breach_since.pop(symbol, None)
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
            self.log(
                f"🚨 Binance Testnet 平倉失敗 {symbol}："
                f"{type(exc).__name__}: {exc}",
                "DANGER",
            )
            return False
        finally:
            self.closing_lock.discard(symbol)

    async def partial_close_position(
        self, symbol: str, current_price: float, close_reason: str, fraction: float = 0.5
    ) -> bool:
        if symbol not in self.positions or symbol in self.closing_lock:
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
                if sl_price > 0:
                    await self._create_protection_order(
                        symbol, close_side, "STOP_MARKET", remaining_qty, sl_price,
                        limit_price=self._stop_limit_price(sl_price, close_side),
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
        """移動限價止損：取消舊止損單，在新位置重新掛保護單。
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
        close_side = "sell" if position["side"] == "LONG" else "buy"
        qty = position["qty"]
        try:
            new_sl_price = float(self.exchange.price_to_precision(symbol, new_sl_price))
            # 取消所有現有保護單
            await self._cancel_all_orders(symbol)
            # 重新掛新止損單
            await self._create_protection_order(
                symbol, close_side, "STOP_MARKET", qty, new_sl_price,
                limit_price=self._stop_limit_price(new_sl_price, close_side),
            )
            # 如果止利仍啟用，同步重建止利單
            tp_price = meta.get("tp", 0.0)
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

