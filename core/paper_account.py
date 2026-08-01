import json
import os
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from core.config import (
    INITIAL_BALANCE,
    TAKER_FEE_RATE,
    SLIPPAGE_PCT,
    MAX_DAILY_LOSS_PCT,
    MIN_OPEN_SIGNAL_SCORE,
    ENABLE_TRAILING_STOP,
    TRAILING_TRIGGER_PCT,
    NET_PROFIT_GUARANTEE_BUFFER,
    get_trailing_pullback_pct,
    PROFIT_ALERT_GIVEBACK_RATIO,
    get_leverage,
    get_signal_leverage,
    DISABLE_TAKE_PROFIT,
    PARTIAL_CLOSE_THRESHOLDS,
    CONTRARIAN_TRAILING_TRIGGER_PCT,
)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "paper_account.json")

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


class PaperAccount:
    """純本地模擬帳戶：不連接任何真實交易所，完全不受幣安測試網伺服器
    穩不穩定影響。刻意跟 BinanceTestnetAccount 維持相同的公開介面（方法
    名稱、參數、async/await、position/trade 欄位結構），engine.py 跟
    services/api.py 完全不用改就能切換，也讓網頁UI顯示邏輯一致。

    跟真實下單最大的差異：
    - place_limit_entry() 直接視為立即成交（不用真的排隊等撮合），因為
      MA7拐頭進場本來就是設計成「對手價直接成交」，在真實環境裡也幾乎
      是瞬間成交。
    - 移動停利只實作百分比制（跟 USE_NATIVE_TRAILING_STOP=false 時的
      testnet 邏輯相同公式），沒有「原生Trailing Stop」這種需要真實
      交易所撮合引擎才有意義的分級——紙上帳戶沒有真正的交易所可以掛
      TRAILING_STOP_MARKET。
    - SL/TP 用每輪 update_positions() 拿到的最新價格跟本地記錄的止損/
      止利價比對，價格穿越就在本地直接觸發平倉，不需要真實保護單。
    """

    def __init__(self):
        self.balance = INITIAL_BALANCE
        self.available_balance = INITIAL_BALANCE
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.positions: Dict[str, dict] = {}
        self.position_meta: Dict[str, dict] = {}
        self.pending_limit_orders: Dict[str, dict] = {}
        self.trades: List[dict] = []
        self.logs: List[dict] = []
        self.closing_lock: set = set()
        self.last_closed_at: Dict[str, float] = {}
        self.on_trade_closed: Optional[Callable[[], None]] = None

        self.daily_date: Optional[str] = None
        self.daily_start_balance: float = 0.0
        self.daily_start_realized_pnl: float = 0.0
        self.daily_halt_logged: bool = False

        # 動態附加欄位（entry_filter_last/stats、pullback_outcome_stats、
        # shadow_parameter_*）由 engine.py 用 getattr/setattr 存取，一般
        # Python物件本來就支援動態屬性，不用預先宣告。
        self.pullback_outcome_stats: dict = {}
        self.entry_filter_stats: dict = {}
        self.entry_filter_last: dict = {}
        self.shadow_parameter_stats: dict = {}
        self.shadow_parameter_last: dict = {}

        self.load_state()

    async def initialize(self) -> None:
        """純本地模擬，不用連線、不用載入交易所市場資料。"""
        self._check_daily_reset()
        self.log("▶️ 紙上交易帳戶已啟動（純本地模擬，不連接真實交易所）")

    def load_state(self) -> None:
        if not os.path.exists(STATE_FILE):
            return
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        self.balance = float(data.get("balance", INITIAL_BALANCE))
        self.realized_pnl = float(data.get("realized_pnl", 0.0))
        self.positions = data.get("positions", {})
        self.position_meta = data.get("position_meta", {})
        self.trades = data.get("trades", [])
        self.logs = data.get("logs", [])
        self.last_closed_at = {
            str(k): float(v) for k, v in data.get("last_closed_at", {}).items()
        }
        self.daily_date = data.get("daily_date")
        self.daily_start_balance = float(data.get("daily_start_balance", 0.0))
        self.daily_start_realized_pnl = float(data.get("daily_start_realized_pnl", 0.0))
        self.daily_halt_logged = bool(data.get("daily_halt_logged", False))
        self.pullback_outcome_stats = data.get("pullback_outcome_stats", {})
        self.entry_filter_stats = data.get("entry_filter_stats", {})
        self.entry_filter_last = data.get("entry_filter_last", {})
        self.shadow_parameter_stats = data.get("shadow_parameter_stats", {})
        self.shadow_parameter_last = data.get("shadow_parameter_last", {})
        self.available_balance = self.get_available_balance()

    def save_state(self) -> None:
        data = {
            "balance": self.balance,
            "realized_pnl": self.realized_pnl,
            "positions": self.positions,
            "position_meta": self.position_meta,
            "trades": self.trades[:500],
            "logs": self.logs[-200:],
            "last_closed_at": self.last_closed_at,
            "daily_date": self.daily_date,
            "daily_start_balance": self.daily_start_balance,
            "daily_start_realized_pnl": self.daily_start_realized_pnl,
            "daily_halt_logged": self.daily_halt_logged,
            "pullback_outcome_stats": self.pullback_outcome_stats,
            "entry_filter_stats": self.entry_filter_stats,
            "entry_filter_last": self.entry_filter_last,
            "shadow_parameter_stats": self.shadow_parameter_stats,
            "shadow_parameter_last": self.shadow_parameter_last,
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def log(self, message: str, level: str = "INFO") -> None:
        self.logs.append({
            "time": get_taipei_time_short(),
            "timestamp": time.time(),
            "text": message,
            "level": level,
        })
        self.save_state()

    def _check_daily_reset(self) -> None:
        today = get_taipei_now_str("%Y-%m-%d")
        if self.daily_date != today:
            self.daily_date = today
            self.daily_start_balance = self.balance
            self.daily_start_realized_pnl = self.realized_pnl
            self.daily_halt_logged = False

    def daily_loss_limit_hit(self) -> tuple:
        """回傳 (是否觸發熔斷, 今日虧損百分比)。跟 BinanceTestnetAccount
        同一套邏輯：觸發時只暫停開新倉，既有持倉的止損/止利不受影響。"""
        self._check_daily_reset()
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
        used_margin = sum(pos.get("margin", 0.0) for pos in self.positions.values())
        return max(0.0, self.balance - used_margin)

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
        if symbol in self.positions or symbol in self.closing_lock:
            return False
        if signal_score is not None and signal_score < MIN_OPEN_SIGNAL_SCORE:
            self.log(f"🛑 {symbol} 訊號分數 {signal_score} 低於 {MIN_OPEN_SIGNAL_SCORE} 分下限，拒絕開倉", "WARNING")
            return False
        if amount_usdt <= 0:
            self.log(f"🛑 {symbol} 下單金額為 0，拒絕開倉", "WARNING")
            return False

        leverage = leverage or (
            get_signal_leverage(symbol, signal_score) if signal_score is not None else get_leverage(symbol)
        )
        # 模擬市價單滑點成本
        execution_price = price * (1 + SLIPPAGE_PCT) if side == "LONG" else price * (1 - SLIPPAGE_PCT)
        qty = (amount_usdt * leverage) / max(execution_price, 1e-12)
        fee = qty * execution_price * TAKER_FEE_RATE
        self.balance -= (amount_usdt + fee)

        entry_context = {
            key: value for key, value in dict(entry_context or {}).items()
            if key in ENTRY_CONTEXT_KEYS
        }
        now = time.time()
        pos = {
            "symbol": symbol,
            "side": side,
            "entry_price": execution_price,
            "qty": qty,
            "margin": amount_usdt,
            "leverage": leverage,
            "sl": sl,
            "tp": tp if not DISABLE_TAKE_PROFIT else 0.0,
            "atr": atr if atr > 0 else execution_price * 0.015,
            "open_timestamp": now,
            "open_time": get_taipei_now_str(),
            "reason": reason,
            "signal_score": signal_score,
            "mark_price": execution_price,
            "unrealized_pnl": 0.0,
            "peak_pnl_pct": 0.0,
            "profit_alert": False,
            **entry_context,
        }
        self.positions[symbol] = pos
        self.position_meta[symbol] = {
            "sl": sl, "tp": pos["tp"], "atr": pos["atr"],
            "open_timestamp": now, "open_time": pos["open_time"],
            "reason": reason, "signal_score": signal_score,
            "is_breakeven_moved": False,
            "highest_pnl_pct": 0.0,
            "peak_profit_updated_at": now,
            **entry_context,
        }

        self.trades.insert(0, {
            "id": int(now * 1000),
            "time": get_taipei_now_str("%m/%d %H:%M:%S"),
            "symbol": symbol,
            "action": f"OPEN_{side}",
            "side": side,
            "price": round(execution_price, 8),
            "qty": round(qty, 8),
            "amount": amount_usdt,
            "fee": round(fee, 4),
            "pnl": 0.0,
            "status": "OPEN",
            "leverage": leverage,
            "signal_score": signal_score,
            "reason": reason,
            "sl": sl,
            "tp": pos["tp"],
            **entry_context,
        })
        self.log(
            f"🚀 [紙上交易] 開倉成功 [{side}] {symbol} @ {execution_price:.6g} "
            f"({leverage}x，含滑點，SL={sl:.6g}, TP={pos['tp']:.6g})",
            "SUCCESS",
        )
        self.save_state()
        return True

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
        """紙上帳戶沒有真實委託簿要排隊撮合，直接視為立即以目標價成交
        （對手價直接成交本來在真實環境也幾乎是瞬間成交，這裡忠實模擬
        同樣的「馬上進場」結果，不用另外維護一份pending狀態機）。"""
        return await self.open_position(
            symbol, side, target_price, amount_usdt, sl, tp, reason,
            atr=atr, leverage=leverage, signal_score=signal_score,
            entry_context=entry_context,
        )

    async def check_pending_limit_orders(self) -> None:
        """紙上帳戶沒有真的待成交限價單（place_limit_entry立即成交），
        維持空實作只是為了介面相容。"""
        return None

    async def cancel_pending_limit(self, symbol: str, reason: str) -> None:
        self.pending_limit_orders.pop(symbol, None)

    async def close_position(self, symbol: str, current_price: float, close_reason: str) -> bool:
        if symbol not in self.positions or symbol in self.closing_lock:
            return False
        self.closing_lock.add(symbol)
        try:
            pos = self.positions.pop(symbol)
            self.position_meta.pop(symbol, None)
            side = pos["side"]
            entry_price = pos["entry_price"]
            qty = pos["qty"]
            margin = pos.get("margin", 0.0)

            exec_close_price = current_price * (1 - SLIPPAGE_PCT) if side == "LONG" else current_price * (1 + SLIPPAGE_PCT)
            raw_pnl = (
                (exec_close_price - entry_price) * qty if side == "LONG"
                else (entry_price - exec_close_price) * qty
            )
            open_fee = qty * entry_price * TAKER_FEE_RATE
            close_fee = qty * exec_close_price * TAKER_FEE_RATE
            total_fee = open_fee + close_fee
            net_pnl = raw_pnl - total_fee
            self.balance += margin + net_pnl
            self.realized_pnl += net_pnl
            self.last_closed_at[symbol] = time.time()

            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"CLOSE_{side}",
                "side": side,
                "price": round(exec_close_price, 8),
                "qty": round(qty, 8),
                "amount": margin,
                "fee": round(total_fee, 4),
                "pnl": round(net_pnl, 4),
                "status": "CLOSED",
                "reason": close_reason,
                **{key: pos.get(key) for key in ENTRY_CONTEXT_KEYS},
            })
            log_level = "SUCCESS" if net_pnl >= 0 else "DANGER"
            self.log(
                f"🏁 [紙上交易] 平倉 [{side}] {symbol} @ {exec_close_price:.6g} | "
                f"淨損益: {net_pnl:+.2f} USDT ({close_reason})",
                log_level,
            )
            self.save_state()
            if self.on_trade_closed:
                try:
                    self.on_trade_closed()
                except Exception:
                    pass
            return True
        finally:
            self.closing_lock.discard(symbol)

    async def partial_close_position(
        self, symbol: str, current_price: float, close_reason: str, fraction: float = 0.5
    ) -> bool:
        if symbol not in self.positions or symbol in self.closing_lock:
            return False
        self.closing_lock.add(symbol)
        try:
            pos = self.positions[symbol]
            meta = self.position_meta.setdefault(symbol, {})
            qty = pos["qty"]
            close_qty = qty * fraction
            if close_qty <= 0:
                return False
            side = pos["side"]
            exec_close_price = current_price * (1 - SLIPPAGE_PCT) if side == "LONG" else current_price * (1 + SLIPPAGE_PCT)
            raw_pnl = (
                (exec_close_price - pos["entry_price"]) * close_qty if side == "LONG"
                else (pos["entry_price"] - exec_close_price) * close_qty
            )
            open_fee = pos["entry_price"] * close_qty * TAKER_FEE_RATE
            close_fee = exec_close_price * close_qty * TAKER_FEE_RATE
            total_fee = open_fee + close_fee
            net_pnl = raw_pnl - total_fee
            self.realized_pnl += net_pnl

            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"PARTIAL_CLOSE_{side}",
                "side": side,
                "price": round(exec_close_price, 8),
                "qty": round(close_qty, 8),
                "amount": round(pos.get("margin", 0.0) * fraction, 4),
                "fee": round(total_fee, 4),
                "pnl": round(net_pnl, 4),
                "status": "PARTIAL_CLOSED",
                "reason": close_reason,
                **{key: pos.get(key) for key in ENTRY_CONTEXT_KEYS},
            })

            remaining_qty = qty - close_qty
            pos["qty"] = remaining_qty
            pos["margin"] = pos.get("margin", 0.0) * (1 - fraction)
            meta["is_half_closed"] = True
            self.log(
                f"💰 [紙上交易/分批止盈] {symbol} 平倉 {fraction:.0%} @ {exec_close_price:.6g} | "
                f"淨損益: {net_pnl:+.2f} USDT | 剩餘 {remaining_qty:.6g} 繼續持有",
                "SUCCESS",
            )
            self.save_state()
            return True
        finally:
            self.closing_lock.discard(symbol)

    async def trail_stop_loss(
        self, symbol: str, new_sl_price: float, mark_profit_locked: bool = True
    ) -> bool:
        """移動限價止損：只往有利方向移動，呼叫端負責確認 new_sl_price
        已經比目前SL更好。mark_profit_locked 預設True（真正的移動停利，
        止損已經鎖到保本以上）；軟性警訊收緊止損只是把止損往進場價方向
        拉近、不保證已經是正的，呼叫時要傳 mark_profit_locked=False，
        否則會誤標記is_breakeven_moved，導致平倉原因誤顯示「移動止利」，
        還會讓5m出場防線誤判成「已經保護過」而提早放行。"""
        if symbol not in self.positions or symbol in self.closing_lock:
            return False
        pos = self.positions[symbol]
        meta = self.position_meta.setdefault(symbol, {})
        pos["sl"] = new_sl_price
        meta["sl"] = new_sl_price
        if mark_profit_locked:
            pos["is_breakeven_moved"] = True
            meta["is_breakeven_moved"] = True
        self.save_state()
        return True

    async def update_positions(self, ticker_prices: Dict[str, float]) -> float:
        self._check_daily_reset()
        total_unrealized = 0.0
        now_ts = time.time()

        for symbol, pos in list(self.positions.items()):
            curr_p = (
                ticker_prices.get(symbol)
                or ticker_prices.get(f"{symbol}:USDT")
                or ticker_prices.get(symbol.replace("/USDT", ""))
            )
            if curr_p is None:
                continue
            curr_p = float(curr_p)
            side = pos["side"]
            entry_p = pos["entry_price"]
            meta = self.position_meta.setdefault(symbol, {})

            pnl_pct = (curr_p - entry_p) / entry_p if side == "LONG" else (entry_p - curr_p) / entry_p
            highest_pnl = meta.get("highest_pnl_pct", pnl_pct)
            if pnl_pct > highest_pnl:
                highest_pnl = pnl_pct
                meta["highest_pnl_pct"] = highest_pnl
                meta["peak_profit_updated_at"] = now_ts
            if "peak_profit_updated_at" not in meta:
                meta["peak_profit_updated_at"] = pos.get("open_timestamp") or now_ts

            # 移動停利（百分比制，跟 USE_NATIVE_TRAILING_STOP=false 時的
            # testnet 邏輯相同——紙上帳戶沒有真實交易所可以掛原生
            # TRAILING_STOP_MARKET，統一用這一套）。逆勢承接單用更早/更低
            # 的觸發門檻，一旦有利潤就盡快接手保護（不限制往上空間，利潤
            # 持續走高一樣會繼續推移，只是啟動得比一般順勢單早）。
            trailing_trigger = (
                CONTRARIAN_TRAILING_TRIGGER_PCT if meta.get("is_contrarian_bottom_buy")
                else TRAILING_TRIGGER_PCT
            )
            if ENABLE_TRAILING_STOP and highest_pnl >= trailing_trigger:
                pullback = get_trailing_pullback_pct(highest_pnl, meta["peak_profit_updated_at"])
                old_sl = pos.get("sl", 0.0)
                if side == "LONG":
                    trail_sl = entry_p * (1.0 + highest_pnl * pullback)
                    npg_floor = entry_p * (1.0 + NET_PROFIT_GUARANTEE_BUFFER)
                    trail_sl = max(trail_sl, npg_floor)
                    if trail_sl > old_sl:
                        pos["sl"] = trail_sl
                        pos["is_breakeven_moved"] = True
                        meta["sl"] = trail_sl
                        meta["is_breakeven_moved"] = True
                        self.log(f"📈 [紙上交易/移動止利] {symbol} 無槓桿利潤峰值 {highest_pnl:.4%}，止利線推至 {trail_sl:.6g}（回吐 {1-pullback:.0%} 平倉）", "SUCCESS")
                else:
                    trail_sl = entry_p * (1.0 - highest_pnl * pullback)
                    npg_ceiling = entry_p * (1.0 - NET_PROFIT_GUARANTEE_BUFFER)
                    trail_sl = min(trail_sl, npg_ceiling)
                    if trail_sl < old_sl or old_sl == 0.0:
                        pos["sl"] = trail_sl
                        pos["is_breakeven_moved"] = True
                        meta["sl"] = trail_sl
                        meta["is_breakeven_moved"] = True
                        self.log(f"📉 [紙上交易/移動止利] {symbol} 無槓桿利潤峰值 {highest_pnl:.4%}，止利線推至 {trail_sl:.6g}（回吐 {1-pullback:.0%} 平倉）", "SUCCESS")

            # 24小時時間過濾
            if (now_ts - pos.get("open_timestamp", now_ts)) >= 86400:
                await self.close_position(symbol, curr_p, "時間過濾 (24h 無效震盪離場)")
                continue

            # 分批止盈：到達利潤門檻先平一部分
            phase = meta.get("partial_close_phase", 0)
            triggered_partial = False
            for i, (threshold, ratio) in enumerate(PARTIAL_CLOSE_THRESHOLDS):
                if phase <= i and pnl_pct >= threshold:
                    meta["partial_close_phase"] = i + 1
                    await self.partial_close_position(symbol, curr_p, f"分批止盈 (Phase {i+1})", fraction=ratio)
                    triggered_partial = True
                    break
            if triggered_partial:
                continue

            # SL/TP 本地觸價比對（沒有真實交易所保護單，價格穿越就在
            # 這裡直接判定平倉）
            sl_price = pos.get("sl", 0.0)
            tp_price = pos.get("tp", 0.0)
            if side == "LONG":
                if tp_price > 0 and curr_p >= tp_price:
                    await self.close_position(symbol, curr_p, "觸發止盈 (Take-Profit)")
                    continue
                if sl_price > 0 and curr_p <= sl_price:
                    reason = "觸發移動止利 (Trailing Take-Profit)" if pos.get("is_breakeven_moved") else "觸發止損 (Stop-Loss)"
                    await self.close_position(symbol, curr_p, reason)
                    continue
            else:
                if tp_price > 0 and curr_p <= tp_price:
                    await self.close_position(symbol, curr_p, "觸發止盈 (Take-Profit)")
                    continue
                if sl_price > 0 and curr_p >= sl_price:
                    reason = "觸發移動止利 (Trailing Take-Profit)" if pos.get("is_breakeven_moved") else "觸發止損 (Stop-Loss)"
                    await self.close_position(symbol, curr_p, reason)
                    continue

            # 獲利了結參考提醒（純顯示，邏輯跟 BinanceTestnetAccount 一致）
            peak_pnl_pct = highest_pnl
            profit_giveback_ratio = (peak_pnl_pct - pnl_pct) / peak_pnl_pct if peak_pnl_pct > 0 else 0.0
            profit_alert = pnl_pct > 0 and profit_giveback_ratio >= PROFIT_ALERT_GIVEBACK_RATIO

            unrealized = (curr_p - entry_p) * pos["qty"] if side == "LONG" else (entry_p - curr_p) * pos["qty"]
            pos["mark_price"] = curr_p
            pos["unrealized_pnl"] = unrealized
            pos["peak_pnl_pct"] = peak_pnl_pct
            pos["profit_alert"] = profit_alert
            pos["sl"] = sl_price
            pos["tp"] = tp_price
            total_unrealized += unrealized

        self.unrealized_pnl = total_unrealized
        self.available_balance = self.get_available_balance()
        self.save_state()
        return total_unrealized
