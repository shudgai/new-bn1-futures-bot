import asyncio
import json
import os
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.config import (
    BINANCE_API_KEY,
    BINANCE_SECRET,
    TAKER_FEE_RATE,
    NET_PROFIT_GUARANTEE_BUFFER,
    BREAKEVEN_LOCK_MIN_ATR_MULT,
    TRAILING_ACTIVATION_ATR_MULT,
    get_trailing_distance_mult,
    MIN_STOP_DISTANCE_ATR_MULT,
    PARTIAL_CLOSE_THRESHOLDS,
    FLASH_MOVE_WINDOW_SEC,
    FLASH_MOVE_THRESHOLD_PCT,
    ENTRY_GRACE_SECONDS,
    ENTRY_GRACE_EXTRA_ATR,
    MAX_DAILY_LOSS_PCT,
    REVERSAL_EXIT_ATR_MULT,
    MIN_OPEN_SIGNAL_SCORE,
    DEFAULT_SYMBOLS,
    PENDING_REPOST_STREAK_LIMIT,
    PENDING_BACKOFF_MINUTES,
    STOP_LIMIT_SLIPPAGE_GUARD_PCT,
    STOP_LIMIT_UNFILLED_TIMEOUT_SEC,
    get_leverage,
    get_signal_leverage,
)
from core.strategy import compute_sl_tp_distance
from core.notifier import notify_email


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
STATE_FILE = os.path.join(DATA_DIR, "testnet_account.json")
TAIPEI_TZ = ZoneInfo("Asia/Taipei")


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
        self._last_trailing_log: Dict[str, float] = {}
        self._trailing_lock: set = set()
        self._orphan_protection_attempted: set = set()
        self._price_history: Dict[str, List[tuple]] = {}
        self._last_flash_log: Dict[str, float] = {}
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
        # 移動止損/趨勢反轉評估節流：/api/prices、/api/status 每 1~3 秒就
        # 會各自呼叫一次 update_positions()，跟主迴圈（5秒一次）完全不同步，
        # 同一個持倉可能在很短時間內被多個呼叫者重複評估、疊加觸發好幾次
        # 止損調整，price 已經跑掉時容易被交易所拒單、觸發不必要的緊急
        # 市價平倉。加上這個節流，同一個 symbol 的止損評估最多每 4 秒做一次。
        self._last_trailing_eval_at: Dict[str, float] = {}
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
        # 連續掛單失敗（撤單）計數與冷卻：同一 symbol 的限價單連續超時/
        # 條件變差被撤銷達 PENDING_REPOST_STREAK_LIMIT 次，代表反覆卡在
        # 同一個已經不新鮮的 setup，強制冷卻 PENDING_BACKOFF_MINUTES 分鐘，
        # 見 cancel_pending_limit()／place_limit_entry()。
        self.pending_repost_streak: Dict[str, int] = {}
        self.pending_backoff_until: Dict[str, float] = {}
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
                "is_breakeven_moved": bool(meta.get("is_breakeven_moved", False)),
                "highest_price": float(meta.get("highest_price") or entry_price),
                "lowest_price": float(meta.get("lowest_price") or entry_price),
                "peak_profit_pct": float(meta.get("peak_profit_pct") or 0.0),
                "peak_profit_updated_at": float(meta.get("peak_profit_updated_at") or 0.0),
                "open_timestamp": float(meta.get("open_timestamp") or now),
                "open_time": meta.get("open_time") or get_taipei_now_str(),
                "reason": meta.get("reason") or "Binance Testnet existing position",
                "signal_score": meta.get("signal_score"),
                "mark_price": mark_price,
                "unrealized_pnl": float(row.get("unRealizedProfit") or 0.0),
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
                "reason": "Binance Testnet 保護單成交",
            })
            self.log(
                f"🏁 Binance Testnet 已平倉 [{position['side']}] {symbol} | "
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

    async def update_positions(
        self,
        ticker_prices: Dict[str, float],
        trend_directions: Dict[str, int] = None,
        atr_overrides: Dict[str, float] = None,
    ) -> float:
        await self.refresh()
        trend_directions = trend_directions or {}
        atr_overrides = atr_overrides or {}

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

            if "peak_profit_pct" not in meta:
                meta["peak_profit_pct"] = 0.0
            if "peak_profit_updated_at" not in meta:
                meta["peak_profit_updated_at"] = pos.get("open_timestamp", time.time())
            if "highest_price" not in meta:
                meta["highest_price"] = entry_p
            if "lowest_price" not in meta:
                meta["lowest_price"] = entry_p

            if side == "LONG":
                curr_profit_pct = (mark_p - entry_p) / entry_p
                if mark_p > meta["highest_price"]:
                    meta["highest_price"] = mark_p
            else:
                curr_profit_pct = (entry_p - mark_p) / entry_p
                if mark_p < meta["lowest_price"]:
                    meta["lowest_price"] = mark_p

            # peak_profit_pct 只留給前端顯示「歷史最高無槓桿利潤」用，出場判斷
            # 已經改用下面的 ATR 移動停利（見 highest_price/lowest_price）。
            if curr_profit_pct > meta.get("peak_profit_pct", 0.0):
                meta["peak_profit_pct"] = curr_profit_pct
                meta["peak_profit_updated_at"] = time.time()

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

            is_flash = self._is_flash_move(symbol, curr_p, side, now_ts)
            if is_flash and now_ts - self._last_flash_log.get(symbol, 0) >= 30:
                self._last_flash_log[symbol] = now_ts
                self.log(
                    f"🌪️ [急殺辨識] {symbol} 短時間內劇烈逆勢波動，暫停收緊移動止利"
                    f"（原止損 {old_sl} 維持不變）",
                    "WARNING",
                )

            # 節流：/api/prices、/api/status 每 1~3 秒就會各自呼叫一次
            # update_positions()，跟主迴圈（5秒一次）完全不同步。同一個持倉
            # 的止損調整（緩衝期收尾 + 移動停利/趨勢反轉）最多每 4 秒評估
            # 一次，避免不同呼叫者在極短時間內重複觸發、疊加收緊，price
            # 已經跑掉時容易被交易所拒單觸發不必要的緊急市價平倉。
            can_eval_trailing = now_ts - self._last_trailing_eval_at.get(symbol, 0) >= 4.0
            if can_eval_trailing:
                self._last_trailing_eval_at[symbol] = now_ts

            # 進場緩衝期結束：把剛進場時放寬的止損收緊回原本策略設定的正常距離。
            # 若急殺正在發生就先不收緊（避免在最劇烈的當下把止損調緊），
            # 若移動止利已經把止損推得比目標還緊，直接清掉標記、不需要再處理。
            if can_eval_trailing and "target_sl" in meta and now_ts >= meta.get("grace_until", 0) and not is_flash:
                target_sl = meta["target_sl"]
                already_tighter = (
                    (side == "LONG" and old_sl >= target_sl)
                    or (side == "SHORT" and 0 < old_sl <= target_sl)
                )
                if already_tighter:
                    meta.pop("target_sl", None)
                else:
                    new_sl = float(self.exchange.price_to_precision(symbol, target_sl))
                    await self._replace_stop_order(symbol, side, pos["qty"], old_sl, new_sl, meta, pos)
                    meta.pop("target_sl", None)
                    self.log(f"⏱️ [緩衝期結束] {symbol} 止損收緊回正常距離 {new_sl}", "INFO")
                    old_sl = pos.get("sl", old_sl)

            # 移動止利：曾經用「峰值 - 0.5倍ATR」的 chandelier exit，但實測
            # 發現這個距離對正常的價格雜訊/反彈太敏感——BCH 這筆只往有利
            # 方向走了 0.09%（0.5倍ATR）就把止損收緊到幾乎貼著進場價，
            # 隨便一次正常反彈就洗出場，虧損幾乎全是手續費，行情根本沒走壞。
            # 改成跟著 Keltner 中軌（EMA20，見 engine.update_position_trends
            # 用已收盤5分K重算、每90秒更新一次，不會被單一 tick 雜訊牽動）
            # 走：只有中軌本身推進到比進場價更有利的位置，才會被納入止損
            # 候選，用真正的通道結構防守，而不是任意抓的 ATR 倍數。
            # 進場緩衝期內完全不做移動停利/趨勢反轉收緊：緩衝期本來就是刻意
            # 放寬止損、讓部位撐過剛進場的雜訊（見上面「進場緩衝期結束」），
            # 但這裡原本沒檢查 grace_until，導致進場後只要價格隨便跳一個
            # tick，就會立刻算出「現價 ± 0.5倍ATR」的超緊候選價，直接蓋掉
            # 緩衝期的保護，新止損送到交易所時常常價格已經穿越，觸發保護單
            # 被拒→緊急市價平倉，整個緩衝期形同虛設（實測 BTC 連續好幾筆
            # 進場後 1~11 秒內就被這樣洗出場，虧損金額精準卡在 0.5x ATR）。
            if can_eval_trailing and old_sl > 0 and not is_flash and now_ts >= meta.get("grace_until", 0):
                # 優先用 engine.update_position_trends() 定期重算的即時 ATR，
                # 反映當下波動度；還沒有即時值時（例如剛啟動、還沒輪到這個
                # 幣種重算）才退回進場當下存的舊值。同步寫回 meta，讓其他
                # 讀 meta["atr"] 的地方（如補建保護單、儀表板）也看到新值。
                atr_val = atr_overrides.get(symbol) or meta.get("atr") or entry_p * 0.015
                meta["atr"] = atr_val
                # 候選止損價取最嚴格的一個，只會愈收愈緊、不會放寬：
                # 1) 保本鎖：最高價（多單）/最低價（空單）扣掉手續費+滑點
                #    緩衝後淨賺，且至少推進滿 BREAKEVEN_LOCK_MIN_ATR_MULT
                #    倍 ATR（不是隨便有賺就鎖），才鎖到保本線。
                # 2) 動態階梯移動停利：取代原本的「通道中軌防守」，解決
                #    「跑得太快被雜訊洗掉、跑得太慢把利潤全部吐回」的矛盾。
                #    最高/最低價至少推進滿 TRAILING_ACTIVATION_ATR_MULT
                #    （0.7倍）才啟動；啟動後跟隨距離依獲利幅度分級收緊——
                #    剛啟動給 1.0倍ATR 呼吸空間，獲利 >1.5倍ATR 收到 0.7倍，
                #    獲利 >3.0倍ATR（噴出段）收到 0.4倍，儘量咬住大波段的
                #    利潤。跟保本鎖是互補：這裡的啟動門檻本身不保證淨賺
                #    （剛啟動時跟隨距離可能比門檻本身還寬），兩者都當候選
                #    交給下面取最嚴格的一個，各自守住不同階段。
                # 3) 趨勢反轉：持倉幣種 SuperTrend 方向（已收盤K棒算，見
                #    engine.update_position_trends）反轉時，收緊到「反轉當下
                #    價格 ± REVERSAL_EXIT_ATR_MULT 倍 ATR」——不是直接平倉，
                #    只是多一個候選價，部位已經獲利、移動停利已經收得更緊時
                #    這個候選會被比下去，不會互搶出場。主要補上「還沒獲利、
                #    移動停利尚未啟動」這段只能等固定止損的空窗期。
                st_dir = trend_directions.get(symbol)
                want_dir = 1 if side == "LONG" else -1
                reversed_trend = st_dir is not None and st_dir != want_dir
                if side == "LONG":
                    peak_price = meta["highest_price"]
                    breakeven_level = entry_p * (1.0 + NET_PROFIT_GUARANTEE_BUFFER)
                    candidates = [(old_sl, "原止損")]
                    # 要求最高價至少推進滿 BREAKEVEN_LOCK_MIN_ATR_MULT 倍
                    # ATR 才讓保本鎖生效，不是「有淨賺就鎖」——避免行情才
                    # 走一點點雜訊幅度就被鎖死在幾乎貼著進場價的止損，一次
                    # 正常反彈就洗出場（實測 AAVE/BCH/DOT 都是這個樣貌）。
                    if peak_price >= breakeven_level and (peak_price - entry_p) >= atr_val * BREAKEVEN_LOCK_MIN_ATR_MULT:
                        candidates.append((breakeven_level, "保本鎖"))
                    profit_atr_mult = (peak_price - entry_p) / atr_val if atr_val > 0 else 0.0
                    if profit_atr_mult >= TRAILING_ACTIVATION_ATR_MULT:
                        trail_dist_mult = get_trailing_distance_mult(profit_atr_mult)
                        candidates.append((peak_price - trail_dist_mult * atr_val, "動態移動停利"))
                    if reversed_trend:
                        candidates.append((mark_p - REVERSAL_EXIT_ATR_MULT * atr_val, "趨勢反轉"))
                    trail_sl, trail_reason = max(candidates, key=lambda c: c[0])
                    # 安全防線：不管哪個候選勝出，新止損跟「標記價格」之間都
                    # 至少留 MIN_STOP_DISTANCE_ATR_MULT 倍 ATR 距離才送單——
                    # 交易所是拿標記價格判斷新止損會不會立刻觸發，用最新成交
                    # 價算安全距離，兩者基差夠大時還是可能被拒單。通道中軌這
                    # 類「跟著一個價位」而非「跟現價保持固定距離」的候選，在
                    # 盤整時容易跟標記價收斂到太近甚至重疊，送單被交易所以
                    # 「Order would immediately trigger」拒絕，觸發不必要的
                    # 緊急市價平倉（實測 OP/USDT 就是這樣，行情根本沒反轉）。
                    trail_sl = min(trail_sl, mark_p - atr_val * MIN_STOP_DISTANCE_ATR_MULT)
                    # 移動幅度太小就不重新掛單：避免行情緩慢推進時每一輪主迴圈
                    # (5秒)都在 cancel/create 保護單，頻繁觸發 API 呼叫/速率限制。
                    # 保本鎖第一次啟動時通常是相對原本寬止損的一大步，不會被這個
                    # 門檻擋住，只有後續的細微調整才會被跳過。
                    if trail_sl > old_sl and (trail_sl - old_sl) >= atr_val * 0.05:
                        new_sl = float(self.exchange.price_to_precision(symbol, trail_sl))
                        # 移動止盈：止損還在收緊，代表趨勢仍在走，固定止盈價不該
                        # 提前把單子封頂——用標記價格重新算 ATR 距離往外推，只會
                        # 往外擴不會往內縮，出場主要交給移動止損判斷趨勢是否反轉。
                        _, tp_distance = compute_sl_tp_distance(mark_p, atr_val)
                        new_tp = max(mark_p + tp_distance, meta.get("tp") or 0.0)
                        new_tp_price = float(self.exchange.price_to_precision(symbol, new_tp))
                        await self._replace_stop_order(
                            symbol, side, pos["qty"], old_sl, new_sl, meta, pos, new_tp=new_tp_price
                        )
                        if now_ts - self._last_trailing_log.get(symbol, 0) >= 30:
                            self._last_trailing_log[symbol] = now_ts
                            mark_suffix = f"（現價 {curr_p:.6f}／標記價 {mark_p:.6f}）"
                            self.log(
                                f"📈 [{trail_reason}] {symbol} 最高價 {peak_price:.6f}，"
                                f"止損推至 {new_sl}，止盈同步推至 {new_tp_price}{mark_suffix}",
                                "SUCCESS",
                            )
                else:
                    trough_price = meta["lowest_price"]
                    breakeven_level = entry_p * (1.0 - NET_PROFIT_GUARANTEE_BUFFER)
                    candidates = [(old_sl, "原止損")]
                    # 同上（LONG 分支）：要求最低價至少推進滿一倍 ATR。
                    if trough_price <= breakeven_level and (entry_p - trough_price) >= atr_val * BREAKEVEN_LOCK_MIN_ATR_MULT:
                        candidates.append((breakeven_level, "保本鎖"))
                    profit_atr_mult = (entry_p - trough_price) / atr_val if atr_val > 0 else 0.0
                    if profit_atr_mult >= TRAILING_ACTIVATION_ATR_MULT:
                        trail_dist_mult = get_trailing_distance_mult(profit_atr_mult)
                        candidates.append((trough_price + trail_dist_mult * atr_val, "動態移動停利"))
                    if reversed_trend:
                        candidates.append((mark_p + REVERSAL_EXIT_ATR_MULT * atr_val, "趨勢反轉"))
                    trail_sl, trail_reason = min(candidates, key=lambda c: c[0])
                    # 同上（LONG 分支）：新止損跟標記價格至少留安全距離才送單。
                    trail_sl = max(trail_sl, mark_p + atr_val * MIN_STOP_DISTANCE_ATR_MULT)
                    if trail_sl < old_sl and (old_sl - trail_sl) >= atr_val * 0.05:
                        new_sl = float(self.exchange.price_to_precision(symbol, trail_sl))
                        _, tp_distance = compute_sl_tp_distance(mark_p, atr_val)
                        current_tp = meta.get("tp") or float("inf")
                        new_tp = min(mark_p - tp_distance, current_tp)
                        new_tp_price = float(self.exchange.price_to_precision(symbol, new_tp))
                        await self._replace_stop_order(
                            symbol, side, pos["qty"], old_sl, new_sl, meta, pos, new_tp=new_tp_price
                        )
                        if now_ts - self._last_trailing_log.get(symbol, 0) >= 30:
                            self._last_trailing_log[symbol] = now_ts
                            mark_suffix = f"（現價 {curr_p:.6f}／標記價 {mark_p:.6f}）"
                            self.log(
                                f"📉 [{trail_reason}] {symbol} 最低價 {trough_price:.6f}，"
                                f"止損推至 {new_sl}，止盈同步推至 {new_tp_price}{mark_suffix}",
                                "SUCCESS",
                            )

            if (time.time() - pos.get("open_timestamp", time.time())) >= 86400:
                await self.close_position(symbol, curr_p, "時間過濾 (24h 無效震盪離場)")
                continue

            phase = meta.get("partial_close_phase", 0)
            for i, (threshold, ratio) in enumerate(PARTIAL_CLOSE_THRESHOLDS):
                if phase <= i and curr_profit_pct >= threshold:
                    await self._partial_close(symbol, pos, meta, ratio, curr_profit_pct, i + 1)
                    break

            self.position_meta[symbol] = meta

        self.save_state()
        return self.unrealized_pnl

    def _is_flash_move(self, symbol: str, curr_p: float, side: str, now_ts: float) -> bool:
        """短窗口內逆勢劇烈波動（急殺/急拉）偵測，只看最近 FLASH_MOVE_WINDOW_SEC 秒。"""
        history = self._price_history.setdefault(symbol, [])
        history.append((now_ts, curr_p))
        cutoff = now_ts - FLASH_MOVE_WINDOW_SEC
        while history and history[0][0] < cutoff:
            history.pop(0)
        if len(history) < 2:
            return False
        oldest_price = history[0][1]
        if oldest_price <= 0:
            return False
        if side == "LONG":
            adverse_move = (oldest_price - curr_p) / oldest_price
        else:
            adverse_move = (curr_p - oldest_price) / oldest_price
        return adverse_move >= FLASH_MOVE_THRESHOLD_PCT

    async def _replace_stop_order(
        self, symbol: str, side: str, qty: float, old_sl: float, new_sl: float, meta: dict, pos: dict,
        new_tp: float = None,
    ) -> None:
        # 避免同一 symbol 的移動止利在 refresh() 節流窗口內被多個呼叫者
        # (/api/prices、/api/status、主循環) 重複觸發，造成保護單反覆 cancel/recreate。
        if symbol in self._trailing_lock:
            return
        self._trailing_lock.add(symbol)
        close_side = "sell" if side == "LONG" else "buy"
        try:
            await self._cancel_all_orders(symbol)
            try:
                await self._create_protection_order(
                    symbol, close_side, "STOP_MARKET", qty, new_sl,
                    limit_price=self._stop_limit_price(new_sl, close_side),
                )
            except Exception as exc:
                # 新止損價已經被市價（MARK_PRICE）穿越，交易所拒絕掛單
                # （-2021 Order would immediately trigger）。舊保護單已被取消，
                # 若放著不管部位會完全裸奔，直接市價平倉，等同止損已觸發。
                # 記下當時的現價（收盤價系列）跟標記價，方便事後判斷這次
                # 觸發是真的行情走了，還是標記價/現價基差造成的雜訊。
                mark_price = pos.get("mark_price")
                mark_suffix = f"（新止損 {new_sl}／標記價 {mark_price:.6f}）" if mark_price is not None else f"（新止損 {new_sl}）"
                self.log(
                    f"⚠️ {symbol} 移動止利新止損建立失敗（{type(exc).__name__}: {exc}），"
                    f"研判價格已穿越止利線，改為市價平倉{mark_suffix}",
                    "DANGER",
                )
                await self.close_position(symbol, new_sl, "移動止利保護單被拒，市價平倉")
                return
            meta["sl"] = new_sl
            if new_tp and new_tp > 0:
                meta["tp"] = new_tp
            self.position_meta[symbol] = meta
            tp = meta.get("tp") or 0.0
            if tp > 0:
                try:
                    await self._create_protection_order(
                        symbol, close_side, "TAKE_PROFIT_MARKET", qty, tp
                    )
                except Exception as exc:
                    self.log(
                        f"⚠️ {symbol} 移動止利後止盈單重建失敗：{type(exc).__name__}: {exc}"
                        f"（止損已生效，僅缺止盈保護）",
                        "WARNING",
                    )
            # 直接同步 pos["sl"]，不必等下一次節流的 refresh() 才能看到新止利價，
            # 否則後續 tick 會拿舊值重複判斷「有改善」而再次觸發本函式。
            pos["sl"] = new_sl
        except Exception as exc:
            self.log(
                f"⚠️ {symbol} 移動止利保護單更新失敗：{type(exc).__name__}: {exc}",
                "WARNING",
            )
        finally:
            self._trailing_lock.discard(symbol)

    async def _create_orphan_protection(self, symbol: str, pos: dict, meta: dict) -> None:
        side = pos["side"]
        entry_p = pos["entry_price"]
        atr = meta.get("atr") or entry_p * 0.015
        sl_distance, tp_distance = compute_sl_tp_distance(entry_p, atr)
        if side == "LONG":
            sl_price = float(self.exchange.price_to_precision(symbol, entry_p - sl_distance))
            tp_price = float(self.exchange.price_to_precision(symbol, entry_p + tp_distance))
        else:
            sl_price = float(self.exchange.price_to_precision(symbol, entry_p + sl_distance))
            tp_price = float(self.exchange.price_to_precision(symbol, entry_p - tp_distance))
        close_side = "sell" if side == "LONG" else "buy"
        try:
            await self._cancel_all_orders(symbol)
            await self._create_protection_order(
                symbol, close_side, "STOP_MARKET", pos["qty"], sl_price,
                limit_price=self._stop_limit_price(sl_price, close_side),
            )
            await self._create_protection_order(symbol, close_side, "TAKE_PROFIT_MARKET", pos["qty"], tp_price)
            meta["sl"] = sl_price
            meta["tp"] = tp_price
            meta["atr"] = atr
            self.position_meta[symbol] = meta
            self.save_state()
            self.log(
                f"🔧 [補建保護單] {symbol} 偵測到無止損止盈持倉，已自動建立 SL={sl_price} TP={tp_price}",
                "WARNING",
            )
        except Exception as exc:
            self.log(
                f"⚠️ {symbol} 補建保護單失敗：{type(exc).__name__}: {exc}",
                "WARNING",
            )

    async def _partial_close(
        self, symbol: str, pos: dict, meta: dict, ratio: float, profit_pct: float, phase: int
    ) -> None:
        if symbol in self.closing_lock:
            return
        self.closing_lock.add(symbol)
        try:
            close_qty_raw = pos["qty"] * ratio
            close_qty = float(self.exchange.amount_to_precision(symbol, close_qty_raw))
            if close_qty <= 0:
                return
            await self._cancel_all_orders(symbol)
            close_side = "sell" if pos["side"] == "LONG" else "buy"
            order = await self.exchange.create_order(
                symbol, "market", close_side, close_qty, None,
                {"reduceOnly": True, "newOrderRespType": "RESULT"},
            )
            exec_price = float(order.get("average") or 0)
            if pos["side"] == "LONG":
                raw_pnl = (exec_price - pos["entry_price"]) * close_qty
            else:
                raw_pnl = (pos["entry_price"] - exec_price) * close_qty
            open_fee = pos["entry_price"] * close_qty * TAKER_FEE_RATE
            close_fee = exec_price * close_qty * TAKER_FEE_RATE
            fee = open_fee + close_fee
            net_pnl = raw_pnl - fee
            self.realized_pnl += net_pnl
            remaining_qty = pos["qty"] - close_qty
            remaining_margin = pos.get("margin", 0.0) * (remaining_qty / pos["qty"])
            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"PARTIAL_CLOSE_{pos['side']}",
                "side": pos["side"],
                "price": exec_price,
                "qty": close_qty,
                "amount": remaining_margin,
                "fee": round(fee, 4),
                "pnl": round(net_pnl, 4),
                "status": "CLOSED",
                "reason": f"分批止盈 (Phase {phase}, 利潤 {profit_pct:.1%})",
                "exchange_order_id": order.get("id"),
            })
            old_sl = meta.get("sl", 0.0)
            meta["partial_close_phase"] = phase
            meta["sl"] = old_sl
            meta["partial_close_qty"] = close_qty
            self.position_meta[symbol] = meta
            new_sl = float(self.exchange.price_to_precision(symbol, old_sl))
            tp = meta.get("tp") or 0.0
            await self._create_protection_order(
                symbol, close_side, "STOP_MARKET", remaining_qty, new_sl,
                limit_price=self._stop_limit_price(new_sl, close_side),
            )
            if tp > 0:
                await self._create_protection_order(
                    symbol, close_side, "TAKE_PROFIT_MARKET", remaining_qty, tp
                )
            self.log(
                f"💰 [分批止盈] {symbol} 平倉 {ratio:.0%}（{close_qty:.2f}）@ {exec_price:.6f} | "
                f"淨損益: {net_pnl:+.2f} USDT | 剩餘 {remaining_qty:.2f} 繼續持有",
                "SUCCESS",
            )
            await self.refresh(force=True)
        except Exception as exc:
            self.log(
                f"⚠️ {symbol} 分批止盈失敗：{type(exc).__name__}: {exc}",
                "WARNING",
            )
        finally:
            self.closing_lock.discard(symbol)

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
    ) -> bool:
        """開倉單成交後的收尾：建立SL/TP保護單、寫入meta、記錄交易。
        market（open_position）與 limit（place_limit_entry 成交後）兩條
        路徑共用，避免重複程式碼。price_ref 是原本規劃進場的參考價（市價
        單是訊號當下價、限價單是掛單目標價），sl/tp 距離以它為基準換算，
        再套用到實際成交價上，讓成交價比預期更好時，止損止盈距離維持
        原本規劃的寬度，不會因為成交價落差而跟著偏移。"""
        try:
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
            tp_price = float(self.exchange.price_to_precision(symbol, adjusted_tp))
            atr_value = atr if atr > 0 else execution_price * 0.015
            # 進場緩衝期：剛進場的 ENTRY_GRACE_SECONDS 秒內，實際掛在交易所的
            # 止損先額外放寬 ENTRY_GRACE_EXTRA_ATR 倍 ATR，避開剛進場時最容易
            # 發生的 MARK_PRICE 瞬間偏離雜訊；緩衝期一過會自動收緊回 sl_price。
            grace_buffer = atr_value * ENTRY_GRACE_EXTRA_ATR
            grace_sl = (
                sl_price - grace_buffer if side == "LONG" else sl_price + grace_buffer
            )
            grace_sl_price = float(self.exchange.price_to_precision(symbol, grace_sl))
            try:
                # 限價單成交後，這裡跟主迴圈/網頁輪詢都可能同時偵測到「這個
                # symbol 有持倉但還沒保護單」而觸發 _create_orphan_protection，
                # 兩邊可能疊出重複的止損止盈單。先清一次掛單，確保接下來建的
                # 是唯一一組，不管是不是搶輸了孤兒保護機制一步。
                await self._cancel_all_orders(symbol)
                await self._create_protection_order(
                    symbol, close_side, "STOP_MARKET", qty, grace_sl_price,
                    limit_price=self._stop_limit_price(grace_sl_price, close_side),
                )
                await self._create_protection_order(
                    symbol, close_side, "TAKE_PROFIT_MARKET", qty, tp_price
                )
            except Exception:
                await self._cancel_all_orders(symbol)
                await self._emergency_flatten(symbol, side, qty)
                raise

            fee = qty * execution_price * TAKER_FEE_RATE
            meta = {
                "sl": grace_sl_price,
                "target_sl": sl_price,
                "grace_until": time.time() + ENTRY_GRACE_SECONDS,
                "tp": tp_price,
                "atr": atr_value,
                "open_timestamp": time.time(),
                "open_time": get_taipei_now_str(),
                "reason": reason,
                "signal_score": signal_score,
                "highest_price": execution_price,
                "lowest_price": execution_price,
                "peak_profit_pct": 0.0,
                "peak_profit_updated_at": time.time(),
                "is_breakeven_moved": False,
            }
            self.position_meta[symbol] = meta
            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"OPEN_{side}",
                "side": side,
                "price": round(execution_price, 8),
                "qty": qty,
                "amount": amount_usdt,
                "fee": round(fee, 4),
                "pnl": 0.0,
                "status": "OPEN",
                "leverage": leverage,
                "signal_score": signal_score,
                "reason": reason,
                "sl": sl_price,
                "tp": tp_price,
                "exchange_order_id": entry_order_id,
            })
            await self.refresh(force=True)
            self.log(
                f"🚀 Binance Testnet 開倉成功 [{side}] {symbol} @ "
                f"{execution_price:.6f} ({leverage}x，SL={sl_price}, TP={tp_price}，"
                f"{ENTRY_GRACE_SECONDS:.0f}秒緩衝期內實際止損暫寬至 {grace_sl_price})",
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
    ) -> bool:
        """在交易所掛真正的限價單進場，取代原本「軟體輪詢等到價再送市價單」
        的做法：不用等主迴圈下一輪才發現價格到了，交易所直接幫忙盯著；
        還沒成交、條件變差或超時的處理交給 check_pending_limit_orders()/
        engine.py 每輪呼叫的條件重新檢查（見 engine._main_loop 第4步）。"""
        if symbol in self.positions or symbol in self.closing_lock or symbol in self.pending_limit_orders:
            return False
        if time.time() < self.pending_backoff_until.get(symbol, 0.0):
            return False
        if signal_score is not None and signal_score < MIN_OPEN_SIGNAL_SCORE:
            self.log(
                f"🛑 {symbol} 訊號分數 {signal_score} 低於 {MIN_OPEN_SIGNAL_SCORE} 分下限，拒絕掛單",
                "WARNING",
            )
            return False
        await self._ensure_markets()
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

        try:
            await self._prepare_leverage(symbol, leverage)
            price_str = self.exchange.price_to_precision(symbol, target_price)
            order = await self.exchange.create_order(
                symbol, "limit", order_side, qty, float(price_str),
                {"timeInForce": "GTC"},
            )
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
        }
        self.log(
            f"📝 [限價掛單] {symbol} {side} 掛單 @ {price_str}（{leverage}x），等待成交",
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
                self.pending_repost_streak.pop(symbol, None)
                await self._finalize_new_position(
                    symbol, info["side"], execution_price, filled_qty,
                    info["target_price"], info["sl"], info["tp"], info["reason"],
                    info["atr"], info["leverage"], info["signal_score"], close_side,
                    info["order_id"], info["amount_usdt"],
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
                    self.pending_repost_streak.pop(symbol, None)
                    await self._finalize_new_position(
                        symbol, info["side"], execution_price, filled_qty,
                        info["target_price"], info["sl"], info["tp"], info["reason"],
                        info["atr"], info["leverage"], info["signal_score"], close_side,
                        info["order_id"], info["amount_usdt"],
                    )
                else:
                    self.log(f"↩️ {symbol} 限價單已被取消/拒絕，放棄本次進場", "INFO")

    async def cancel_pending_limit(self, symbol: str, reason: str, count_failure: bool = True) -> None:
        """主動撤單（超時或條件變差），engine.py 呼叫。

        count_failure=True（預設）時計入連續失敗次數：達到
        PENDING_REPOST_STREAK_LIMIT 就強制冷卻 PENDING_BACKOFF_MINUTES
        分鐘，避免同一個已經不新鮮的 setup 被反覆掛單-撤單。symbol 被
        輪替出牌面（不是「這次沒成交」）時 engine.py 會傳 False，不計入。
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
            self.pending_repost_streak.pop(symbol, None)
            await self._finalize_new_position(
                symbol, info["side"], execution_price, filled_qty,
                info["target_price"], info["sl"], info["tp"], info["reason"],
                info["atr"], info["leverage"], info["signal_score"], close_side,
                info["order_id"], info["amount_usdt"],
            )
            return

        self.log(f"↩️ [限價單撤銷] {symbol} {info['side']}：{reason}", "INFO")
        if count_failure:
            streak = self.pending_repost_streak.get(symbol, 0) + 1
            self.pending_repost_streak[symbol] = streak
            if streak >= PENDING_REPOST_STREAK_LIMIT:
                self.pending_backoff_until[symbol] = time.time() + PENDING_BACKOFF_MINUTES * 60
                self.pending_repost_streak[symbol] = 0
                self.log(
                    f"🧊 [連續掛單失敗冷卻] {symbol} 連續 {streak} 次掛單未成交/條件變差，"
                    f"暫停重新掛單 {PENDING_BACKOFF_MINUTES:.0f} 分鐘",
                    "WARNING",
                )

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
