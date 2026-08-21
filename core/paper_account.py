import json
import os
import time
import re
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
    ENABLE_EARLY_PROFIT_GUARD,
    ENABLE_PROFIT_GIVEBACK_EXIT,
    EARLY_PROFIT_GUARD_TRIGGER_PCT,
    EARLY_PROFIT_GUARD_EXIT_PCT,
    BOUNCE_EARLY_PROFIT_GUARD_TRIGGER_PCT,
    BOUNCE_EARLY_PROFIT_GUARD_EXIT_PCT,
    TRAILING_TRIGGER_PCT,
    TRAILING_CALLBACK_PCT,
    NET_PROFIT_GUARANTEE_BUFFER,
    ENABLE_PROFIT_BANK,
    PROFIT_BANK_TRIGGER_PCT,
    PROFIT_BANK_LOCK_PCT,
    PROFIT_BANK_CAPTURE_RATIO,
    get_profit_bank_capture_ratio,
    PROFIT_BANK_MIN_STEP_PCT,
    get_trailing_pullback_pct,
    PROFIT_ALERT_GIVEBACK_RATIO,
    PROFIT_ALERT_MIN_PEAK_PCT,
    PROFIT_ALERT_MIN_NET_PCT,
    get_leverage,
    get_signal_leverage,
    DISABLE_TAKE_PROFIT,
    DISABLE_STOP_LOSS,
    ONLY_CLOSE_ON_PROFIT,
    ONLY_CLOSE_ON_PROFIT_MIN_NET_USDT,
    CLOSE_ON_PROFIT_MIN_PNL_TO_FEE_RATIO,
    ENABLE_24H_TIME_FILTER,
    MAX_ACCEPTABLE_LOSS_PCT,
    PARTIAL_CLOSE_THRESHOLDS,
    CONTRARIAN_TRAILING_TRIGGER_PCT,
    TRAILING_TRIGGER_R_MULT,
    TRAILING_CALLBACK_R_MULT,
    ENABLE_DCA_LIMIT,
    DCA_STAGE_DEPTHS,
    PAPER_MAKER_FILL_PENETRATION_PCT,
    SUPPORT_PULLBACK_RECLAIM_ATR_MULT,
    SUPPORT_PULLBACK_RECLAIM_MIN_SEC,
    SUPPORT_PULLBACK_MAX_ADVERSE_ATR_MULT,
    PAPER_SUPPORT_PULLBACK_REQUIRE_RECLAIM,
    TREND_EXTENSION_GUARD_TRIGGER_PCT, TREND_EXTENSION_GUARD_EXIT_PCT,
    TREND_EXTENSION_MIN_CAPTURE_RATIO,
    get_bounce_capture_ratio,
    STRUCTURED_NET_RR_FILTER_ENABLED, STRUCTURED_MIN_NET_REWARD_RISK, STRUCTURED_NET_RR_HARD_FLOOR,
    BOUNCE_NO_FOLLOW_THROUGH_SEC,
    BOUNCE_NO_FOLLOW_THROUGH_MIN_MFE_PCT,
    SL_ONLY_AFTER_PEAK_PCT,
    MIN_SL_DISTANCE_PCT,
    STOP_LOSS_MULTIPLIER,
)
from core.strategy import compute_net_reward_risk, compute_sl_tp_distance, validate_sl_tp_pair

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "paper_account.json")

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
ACCOUNTING_VERSION = 2
ENTRY_CONTEXT_KEYS = (
    "btc_regime_at_entry", "btc_direction_1h_at_entry", "btc_score_penalty",
    "btc_allocation_factor", "btc_pre_penalty_score",
    "raw_signal_score", "btc_adjusted_score", "history_adjusted_score",
    "history_score_multiplier", "pullback_confirmation_score", "entry_mode",
    "is_contrarian_bottom_buy", "initial_sl", "initial_risk",
    "signal_candle_low", "signal_candle_high",
    "touch_price", "reclaim_confirmed", "reclaim_wait_sec",
    "profit_profile", "profit_room_pct",
    "bounce_capture_ratio", "bounce_target_pct",
    "structured_net_rr", "high_readiness_low_room",
    "low_room_allocation_factor",
    "dca_stage", "dca_base_price", "dca_original_amount",
    "eligibility_note",
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
    - MA7 的非 Post-Only 對手價單立即成交並計入滑點；Post-Only/Maker
      限價單則保留在 pending，只有最新價穿越目標價才按掛單價成交，避免
      把Maker掛單錯算成較差價格的Taker成交。
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
        self.latest_prices: Dict[str, float] = {}
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
        self.accounting_version: int = ACCOUNTING_VERSION

        self.load_state()

    async def initialize(self) -> None:
        """純本地模擬，不用連線、不用載入交易所市場資料。"""
        self._check_daily_reset()
        restored = False
        if not DISABLE_STOP_LOSS:
            for symbol, pos in self.positions.items():
                if float(pos.get("sl") or 0.0) > 0:
                    continue
                meta = self.position_meta.setdefault(symbol, {})
                entry_price = float(pos.get("entry_price") or 0.0)
                if entry_price <= 0:
                    continue
                atr = float(pos.get("atr") or meta.get("atr") or entry_price * 0.015)
                sl_distance, _ = compute_sl_tp_distance(entry_price, atr)
                sl_price = (
                    entry_price - sl_distance
                    if pos.get("side") == "LONG"
                    else entry_price + sl_distance
                )
                pos["sl"] = sl_price
                meta["sl"] = sl_price
                meta["atr"] = atr
                if "initial_sl" in pos or "initial_sl" in meta:
                    pos["initial_sl"] = sl_price
                    pos["initial_risk"] = sl_distance
                    meta["initial_sl"] = sl_price
                    meta["initial_risk"] = sl_distance
                self.log(
                    f"🔧 [啟動保護遷移] {symbol} 已重建紙上硬停損 SL={sl_price:.8g}",
                    "WARNING",
                )
                restored = True
        if restored:
            self.save_state()
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
        self.pending_limit_orders = data.get("pending_limit_orders", {})
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
        stored_accounting_version = int(data.get("accounting_version", 1))
        self.accounting_version = ACCOUNTING_VERSION
        if stored_accounting_version < ACCOUNTING_VERSION:
            recorded_open_fees = sum(
                float(trade.get("fee") or 0.0)
                for trade in self.trades
                if str(trade.get("action", "")).startswith("OPEN_")
            )
            remaining_open_fees = sum(
                float(pos.get("entry_price") or 0.0)
                * float(pos.get("qty") or 0.0)
                * TAKER_FEE_RATE
                for pos in self.positions.values()
            )
            missing_partial_refunds = sum(
                float(trade.get("amount") or 0.0) + float(trade.get("pnl") or 0.0)
                for trade in self.trades
                if str(trade.get("action", "")).startswith("PARTIAL_CLOSE_")
            )
            repair_amount = (
                max(0.0, recorded_open_fees - remaining_open_fees)
                + missing_partial_refunds
            )
            if repair_amount:
                self.balance += repair_amount
                if self.daily_start_balance > 0:
                    self.daily_start_balance += repair_amount
                self.logs.append({
                    "time": get_taipei_time_short(),
                    "timestamp": time.time(),
                    "text": (
                        "🔧 [紙上帳戶會計遷移] 已補回重複開倉費與分批平倉未入帳金額 "
                        f"{repair_amount:.4f} USDT"
                    ),
                    "level": "WARNING",
                })
        self.available_balance = self.get_available_balance()
        if stored_accounting_version < ACCOUNTING_VERSION:
            self.save_state()

    def save_state(self) -> None:
        data = {
            "balance": self.balance,
            "realized_pnl": self.realized_pnl,
            "positions": self.positions,
            "position_meta": self.position_meta,
            "pending_limit_orders": self.pending_limit_orders,
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
            "accounting_version": self.accounting_version,
        }
        tmp_file = f"{STATE_FILE}.tmp"
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file, STATE_FILE)
        except Exception:
            pass

    def log(self, message: str, level: str = "INFO") -> None:
        # 視覺層過濾：將 'Mandatory_Fail: KEY(...)' 顯示成括號內的中文說明，或移除前綴並替換下劃線
        if isinstance(message, str) and "Mandatory_Fail:" in message:
            m = re.search(r"Mandatory_Fail:\s*[A-Za-z0-9_]+\(([^)]*)\)", message)
            if m:
                # 使用括號內文字（通常為中文說明）
                message = message.replace(m.group(0), m.group(1))
            else:
                # 未含括號時，僅移除前綴並把 KEY 裡的下劃線改成空格
                message = re.sub(r"Mandatory_Fail:\s*", "", message).replace("_", " ")

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
        # self.balance 已在開倉時扣除保證金；不可再扣一次持倉 margin。
        return max(0.0, self.balance)

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
        apply_slippage: bool = True,
    ) -> bool:
        if symbol in self.positions or symbol in self.closing_lock:
            return False
        if signal_score is not None and signal_score < MIN_OPEN_SIGNAL_SCORE:
            self.log(f"🛑 {symbol} 訊號分數 {signal_score} 低於 {MIN_OPEN_SIGNAL_SCORE} 分下限，拒絕開倉", "WARNING")
            return False
        if amount_usdt <= 0:
            self.log(f"🛑 {symbol} 下單金額為 0，拒絕開倉", "WARNING")
            return False
        try:
            validate_sl_tp_pair(price, side, sl, tp)
        except ValueError as exc:
            self.log(f"🛑 {symbol} 進場前 SL/TP 驗證失敗：{exc}", "WARNING")
            return False

        leverage = leverage or (
            get_signal_leverage(symbol, signal_score) if signal_score is not None else get_leverage(symbol)
        )
        # 市價/對手價單計入不利滑點；Maker限價成交使用原掛單價。
        if apply_slippage:
            execution_price = price * (1 + SLIPPAGE_PCT) if side == "LONG" else price * (1 - SLIPPAGE_PCT)
        else:
            execution_price = price
        if apply_slippage:
            sl_distance = abs(price - sl)
            tp_distance = abs(tp - price) if tp else 0.0
            sl = execution_price - sl_distance if side == "LONG" else execution_price + sl_distance
            if tp_distance > 0:
                tp = execution_price + tp_distance if side == "LONG" else execution_price - tp_distance
        if DISABLE_STOP_LOSS:
            sl = 0.0
        else:
            # Ensure SL is on correct side and at least a conservative minimum distance
            try:
                effective_atr = atr if atr and atr > 0 else execution_price * 0.015
                min_dist = max(execution_price * MIN_SL_DISTANCE_PCT, effective_atr * STOP_LOSS_MULTIPLIER)
                if side == "LONG":
                    if sl >= execution_price - 1e-12:
                        sl = execution_price - min_dist
                else:
                    if sl <= execution_price + 1e-12:
                        sl = execution_price + min_dist
            except Exception:
                pass
        qty = (amount_usdt * leverage) / max(execution_price, 1e-12)
        fee = qty * execution_price * TAKER_FEE_RATE
        self.balance -= (amount_usdt + fee)

        entry_context = {
            key: value for key, value in dict(entry_context or {}).items()
            if key in ENTRY_CONTEXT_KEYS
        }
        if entry_context.get("initial_sl") is not None:
            entry_context["initial_sl"] = sl
            entry_context["initial_risk"] = abs(execution_price - sl)
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
            # 監控：預估淨風報比（projected_net_rr）與獲利空間百分比
            **({
                "projected_net_rr": (lambda rp: (compute_net_reward_risk(execution_price, sl, rp)[0] if rp and rp > 0 else None))(float(entry_context.get("bounce_target_pct") or entry_context.get("profit_room_pct") or 0.0)),
                "profit_room_pct": float(entry_context.get("profit_room_pct") or entry_context.get("bounce_target_pct") or 0.0),
            } if entry_context else {}),
            **entry_context,
        })
        fill_note = "含滑點" if apply_slippage else "Maker限價成交"
        self.log(
            f"🚀 [紙上交易] 開倉成功 [{side}] {symbol} @ {execution_price:.6g} "
            f"({leverage}x，{fill_note}，SL={sl:.6g}, TP={pos['tp']:.6g})",
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
        timeframe: str = "5m",
    ) -> bool:
        """非Post-Only對手價單立即成交；Post-Only保留至市價穿越掛單價。"""
        if not post_only:
            return await self.open_position(
                symbol, side, target_price, amount_usdt, sl, tp, reason,
                atr=atr, leverage=leverage, signal_score=signal_score,
                entry_context=entry_context,
            )

        # DCA 分批掛單處理
        entry_ctx = dict(entry_context or {})
        is_dca_call = "dca_stage" in entry_ctx
        if ENABLE_DCA_LIMIT and not is_dca_call:
            entry_ctx["dca_stage"] = 1
            entry_ctx["dca_base_price"] = float(target_price)
            entry_ctx["dca_original_amount"] = float(amount_usdt)
            amount_usdt = amount_usdt / 3.0

        if symbol in self.positions:
            # 只有在非 DCA 首次進場（也就是 DCA 2、3 階加倉）時，才允許在已有持倉時繼續掛單
            if not is_dca_call:
                return False
        elif symbol in self.pending_limit_orders or symbol in self.closing_lock:
            return False

        if signal_score is not None and signal_score < MIN_OPEN_SIGNAL_SCORE:
            self.log(
                f"🛑 {symbol} 訊號分數 {signal_score} 低於 {MIN_OPEN_SIGNAL_SCORE} 分下限，拒絕掛單",
                "WARNING",
            )
            return False
        if amount_usdt <= 0 or self.get_available_balance() < amount_usdt:
            return False
        try:
            validate_sl_tp_pair(target_price, side, sl, tp)
        except ValueError as exc:
            self.log(f"🛑 {symbol} 進場前 SL/TP 驗證失敗：{exc}", "WARNING")
            return False

        current_price = self.latest_prices.get(symbol)
        # If we don't have a latest market price for this symbol, skip the maker-range sanity check
        if current_price is not None and float(current_price) > 0:
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

        leverage = leverage or (
            get_signal_leverage(symbol, signal_score)
            if signal_score is not None else get_leverage(symbol)
        )
        self.pending_limit_orders[symbol] = {
            "side": side,
            "target_price": float(target_price),
            "amount_usdt": float(amount_usdt),
            "sl": float(sl),
            "tp": float(tp),
            "reason": reason,
            "atr": float(atr),
            "leverage": leverage,
            "signal_score": signal_score,
            "placed_at": time.time(),
            "post_only": True,
            "entry_context": {
                key: value for key, value in entry_ctx.items()
                if key in ENTRY_CONTEXT_KEYS
            },
        }
        dca_note = f" (DCA 階 {entry_ctx['dca_stage']})" if "dca_stage" in entry_ctx else ""
        self.log(f"📝 [紙上Maker掛單] {symbol} {side} @ {target_price:.8g}{dca_note}，等待觸價", "INFO")
        self.save_state()
        return True

    async def check_pending_limit_orders(self) -> None:
        """模擬掛單；支撐反轉單觸價後須先確認回收，避免接住持續下跌。"""
        for symbol, info in list(self.pending_limit_orders.items()):
            current_price = self.latest_prices.get(symbol)
            if current_price is None:
                continue
            side = info["side"]
            target = float(info["target_price"])
            threshold_pct = PAPER_MAKER_FILL_PENETRATION_PCT
            touched = (
                (side == "LONG" and current_price <= target * (1.0 - threshold_pct))
                or (side == "SHORT" and current_price >= target * (1.0 + threshold_pct))
            )
            entry_mode = (info.get("entry_context") or {}).get("entry_mode")

            entry_context = info.get("entry_context") or {}
            selective_reclaim = (
                bool(entry_context.get("high_readiness_low_room"))
                or float(entry_context.get("btc_allocation_factor") or 1.0) < 1.0
            )
            if (
                entry_mode == "SUPPORT_PULLBACK"
                and (PAPER_SUPPORT_PULLBACK_REQUIRE_RECLAIM or selective_reclaim)
            ):
                if not info.get("touched_at"):
                    if touched:
                        info["touched_at"] = time.time()
                        info["touch_price"] = float(current_price)
                        self.log(
                            f"👀 [紙上支撐觸價] {symbol} {side} @ {current_price:.8g}，"
                            f"等待回收 {SUPPORT_PULLBACK_RECLAIM_ATR_MULT:.2f} ATR 確認承接",
                            "INFO",
                        )
                        self.save_state()
                    continue

                atr = max(float(info.get("atr") or 0.0), target * 1e-6)
                adverse = target - current_price if side == "LONG" else current_price - target
                if adverse >= atr * SUPPORT_PULLBACK_MAX_ADVERSE_ATR_MULT:
                    self.pending_limit_orders.pop(symbol, None)
                    self.log(
                        f"↩️ [紙上反轉撤單] {symbol}：觸價後反向穿越 "
                        f"{adverse / atr:.2f} ATR，承接失敗",
                        "INFO",
                    )
                    self.save_state()
                    continue

                waited = time.time() - float(info["touched_at"])
                reclaim = atr * SUPPORT_PULLBACK_RECLAIM_ATR_MULT
                reclaimed = (
                    current_price >= target + reclaim if side == "LONG"
                    else current_price <= target - reclaim
                )
                if waited < SUPPORT_PULLBACK_RECLAIM_MIN_SEC or not reclaimed:
                    continue

                entry_ctx = dict(info.get("entry_context") or {})
                if entry_ctx.get("profit_profile") == "BOUNCE":
                    reward_pct = float(entry_ctx.get("bounce_target_pct") or 0.0)
                    if reward_pct > 0:
                        projected_entry = current_price * (
                            1 + SLIPPAGE_PCT if side == "LONG" else 1 - SLIPPAGE_PCT
                        )
                        stop_distance = abs(current_price - float(info["sl"]))
                        projected_sl = (
                            projected_entry - stop_distance
                            if side == "LONG" else projected_entry + stop_distance
                        )
                        net_rr, _, _ = compute_net_reward_risk(
                            projected_entry, projected_sl, reward_pct,
                        )
                        required_net_rr = (
                            STRUCTURED_MIN_NET_REWARD_RISK
                            if STRUCTURED_NET_RR_FILTER_ENABLED
                            else STRUCTURED_NET_RR_HARD_FLOOR
                        )
                        if net_rr + 1e-12 < required_net_rr:
                            self.pending_limit_orders.pop(symbol, None)
                            self.log(
                                f"↩️ [紙上反轉撤單] {symbol}：回收後淨風報比 "
                                f"{net_rr:.2f}:1 低於 {required_net_rr:.2f}:1",
                                "INFO",
                            )
                            self.save_state()
                            continue

                self.pending_limit_orders.pop(symbol, None)
                entry_ctx.update({
                    "touch_price": info.get("touch_price", target),
                    "reclaim_confirmed": True,
                    "reclaim_wait_sec": round(waited, 1),
                })
                opened = await self.open_position(
                    symbol, side, current_price, info["amount_usdt"], info["sl"], info["tp"],
                    info["reason"], atr=info["atr"], leverage=info["leverage"],
                    signal_score=info["signal_score"], entry_context=entry_ctx,
                    apply_slippage=True,
                )
                if opened:
                    self.log(
                        f"✅ [紙上反轉確認成交] {symbol} {side} @ {current_price:.8g}｜"
                        f"觸價後等待 {waited:.0f}秒、回收 {SUPPORT_PULLBACK_RECLAIM_ATR_MULT:.2f} ATR",
                        "SUCCESS",
                    )
                continue

            if not touched:
                continue
            self.pending_limit_orders.pop(symbol, None)
            opened = await self.open_position(
                symbol, side, target, info["amount_usdt"], info["sl"], info["tp"],
                info["reason"], atr=info["atr"], leverage=info["leverage"],
                signal_score=info["signal_score"], entry_context=info.get("entry_context"),
                apply_slippage=False,
            )
            if opened:
                self.log(f"✅ [紙上Maker成交] {symbol} {side} @ {target:.8g}", "SUCCESS")

    async def cancel_pending_limit(self, symbol: str, reason: str) -> None:
        if self.pending_limit_orders.pop(symbol, None) is not None:
            self.log(f"↩️ [紙上Maker撤單] {symbol}：{reason}", "INFO")
            self.save_state()

    async def close_position(self, symbol: str, current_price: float, close_reason: str, is_manual: bool = False) -> bool:
        if symbol not in self.positions or symbol in self.closing_lock:
            return False
        # 若全域關閉自動停損，非手動呼叫一律拒絕自動平倉
        if DISABLE_STOP_LOSS and not is_manual:
            self.log(f"⏸️ [自動停損已停用] 拒絕自動平倉 {symbol} ({close_reason})", "INFO")
            return False
        if not is_manual and ONLY_CLOSE_ON_PROFIT:
            pos = self.positions[symbol]
            side = pos["side"]
            entry_price = pos["entry_price"]
            qty = pos["qty"]
            exec_close_price = current_price * (1 - SLIPPAGE_PCT) if side == "LONG" else current_price * (1 + SLIPPAGE_PCT)
            raw_pnl = (
                (exec_close_price - entry_price) * qty if side == "LONG"
                else (entry_price - exec_close_price) * qty
            )
            open_fee = qty * entry_price * TAKER_FEE_RATE
            close_fee = qty * exec_close_price * TAKER_FEE_RATE
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
        self.closing_lock.add(symbol)
        try:
            pos = self.positions.pop(symbol)
            meta = self.position_meta.pop(symbol, {})
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
            peak_pnl_pct = max(
                float(pos.get("peak_pnl_pct") or 0.0),
                float(meta.get("highest_pnl_pct") or 0.0),
            )
            realized_price_move_pct = (
                (exec_close_price - entry_price) / entry_price
                if side == "LONG"
                else (entry_price - exec_close_price) / entry_price
            )
            # 開倉費已在 open_position() 扣除；這裡只扣平倉費，避免重複計費。
            self.balance += margin + raw_pnl - close_fee
            self.realized_pnl += net_pnl
            self.last_closed_at[symbol] = time.time()

            # 移動止利的SL可能是在價格觸發時「回吐前一波峰值」推上去的，
            # 但下一次檢查價格已跳空穿越（含滑點/手續費），實際平倉價
            # 反而落到成本價以下——這時若仍標示「移動止利」會誤導使用者
            # 以為是獲利了結。用實際淨損益結果覆寫標籤，才能反映真相。
            if close_reason in (
                "觸發移動止利 (Trailing Take-Profit)",
                "觸發止損 (Stop-Loss)",
            ):
                close_reason = (
                    "觸發移動止利 (Trailing Take-Profit)" if net_pnl >= 0
                    else "觸發止損 (Stop-Loss)"
                )

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
                "peak_pnl_pct": round(peak_pnl_pct, 8),
                "realized_price_move_pct": round(realized_price_move_pct, 8),
                "profit_capture_ratio": (
                    round(realized_price_move_pct / peak_pnl_pct, 4)
                    if peak_pnl_pct > 0 else None
                ),
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
            released_margin = pos.get("margin", 0.0) * fraction
            # 退回本次釋放的保證金及平倉損益；開倉費早已在進場時扣除。
            self.balance += released_margin + raw_pnl - close_fee
            self.realized_pnl += net_pnl

            self.trades.insert(0, {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"PARTIAL_CLOSE_{side}",
                "side": side,
                "price": round(exec_close_price, 8),
                "qty": round(close_qty, 8),
                "amount": round(released_margin, 4),
                "fee": round(total_fee, 4),
                "pnl": round(net_pnl, 4),
                "status": "PARTIAL_CLOSED",
                "reason": close_reason,
                **{key: pos.get(key) for key in ENTRY_CONTEXT_KEYS},
            })

            remaining_qty = qty - close_qty
            pos["qty"] = remaining_qty
            pos["margin"] = pos.get("margin", 0.0) - released_margin
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
        """移動停損：只往有利方向移動，呼叫端負責確認 new_sl_price
        已經比目前SL更好。mark_profit_locked 預設True（真正的移動停利，
        止損已經鎖到保本以上）；軟性警訊收緊止損只是把止損往進場價方向
        拉近、不保證已經是正的，呼叫時要傳 mark_profit_locked=False，
        否則會誤標記is_breakeven_moved，導致平倉原因誤顯示「移動止利」，
        還會讓5m出場防線誤判成「已經保護過」而提早放行。"""
        if symbol not in self.positions or symbol in self.closing_lock:
            return False
        pos = self.positions[symbol]
        meta = self.position_meta.setdefault(symbol, {})
        tp_price = float(pos.get("tp") or meta.get("tp") or 0.0)
        if tp_price > 0:
            try:
                validate_sl_tp_pair(
                    float(pos.get("entry_price") or meta.get("entry_price") or 0.0),
                    pos["side"], new_sl_price, tp_price, allow_profit_lock=True,
                )
            except ValueError:
                self.log(
                    f"🛑 {symbol} 移動止損更新失敗：SL/TP 方向或風報比不合法，忽略更新（SL={new_sl_price}，TP={tp_price}）",
                    "WARNING",
                )
                return False
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
        # 即使目前沒有持倉，也要保留最新價供 Post-Only 掛單判斷是否觸價。
        for symbol, price in ticker_prices.items():
            if price is not None:
                self.latest_prices[str(symbol)] = float(price)

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

            # Migrate positions opened before MomentumCross received an
            # explicit trend profile. Those records were stored as BOUNCE
            # with no room/target and hit the very tight bounce guard.
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

            pnl_pct = (curr_p - entry_p) / entry_p if side == "LONG" else (entry_p - curr_p) / entry_p
            if "highest_pnl_pct" not in meta:
                meta["highest_pnl_pct"] = pnl_pct
            highest_pnl = meta["highest_pnl_pct"]
            if pnl_pct > highest_pnl:
                highest_pnl = pnl_pct
                meta["highest_pnl_pct"] = highest_pnl
                meta["peak_profit_updated_at"] = now_ts
            if "peak_profit_updated_at" not in meta:
                meta["peak_profit_updated_at"] = pos.get("open_timestamp") or now_ts

            # 階梯式移動停利：首次至少鎖 0.25%，之後隨峰值持續上移，
            # 但保留部分回檔空間讓趨勢延伸。保護線永遠不會往回放寬。
            if ENABLE_PROFIT_BANK and highest_pnl + 1e-12 >= PROFIT_BANK_TRIGGER_PCT:
                bank_lock_pct = min(
                    max(PROFIT_BANK_LOCK_PCT, highest_pnl * get_profit_bank_capture_ratio(highest_pnl, PROFIT_BANK_CAPTURE_RATIO)),
                    max(0.0, highest_pnl - SLIPPAGE_PCT),
                )
                bank_sl = entry_p * (
                    1.0 + bank_lock_pct
                    if side == "LONG" else 1.0 - bank_lock_pct
                )
                current_sl = float(pos.get("sl") or meta.get("sl") or 0.0)
                min_step = entry_p * PROFIT_BANK_MIN_STEP_PCT
                improves = (
                    bank_sl > current_sl + min_step if side == "LONG"
                    else current_sl <= 0.0 or bank_sl < current_sl - min_step
                )
                if improves:
                    pos["sl"] = bank_sl
                    meta["sl"] = bank_sl
                    pos["is_breakeven_moved"] = True
                    meta["is_breakeven_moved"] = True
                    pos["profit_bank_armed"] = True
                    meta["profit_bank_armed"] = True
                    self.log(
                        f"📈 [階梯移動停利] {symbol} 峰值 {highest_pnl:.4%}，"
                        f"已鎖 {bank_lock_pct:.4%}，保護線上移至 {bank_sl:.6g}",
                        "SUCCESS",
                    )

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
            if (
                meta.get("profit_profile") == "BOUNCE"
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
                and now_ts - float(pos.get("open_timestamp") or now_ts)
                    >= BOUNCE_NO_FOLLOW_THROUGH_SEC
                and highest_pnl < BOUNCE_NO_FOLLOW_THROUGH_MIN_MFE_PCT
                and pnl_pct <= 0
            ):
                await self.close_position(symbol, curr_p, "反彈逾時未延續平倉")
                continue

            # 移動停利（百分比制，跟 USE_NATIVE_TRAILING_STOP=false 時的
            # testnet 邏輯相同——紙上帳戶沒有真實交易所可以掛原生
            # TRAILING_STOP_MARKET，統一用這一套）。逆勢承接單用更早/更低
            # 的觸發門檻，一旦有利潤就盡快接手保護（不限制往上空間，利潤
            # 持續走高一樣會繼續推移，只是啟動得比一般順勢單早）。
            configured_trigger = (
                CONTRARIAN_TRAILING_TRIGGER_PCT if meta.get("is_contrarian_bottom_buy")
                else TRAILING_TRIGGER_PCT
            )
            # 有 initial_risk 的策略單至少走到設定 R 倍數才啟動；沒有風險資料的
            # 舊單才沿用百分比門檻。止利線仍保證落在扣除成本後的安全區。
            initial_risk = float(pos.get("initial_risk") or meta.get("initial_risk") or 0.0)
            risk_pct = initial_risk / entry_p if entry_p > 0 else 0.0
            # 新單有明確 initial_risk 時，R 倍數就是唯一一致的尺度。
            # 若再和固定百分比取 max，窄止損單雖已在 1.5R 分批止盈，
            # 剩餘部位卻可能尚未啟動 trailing，最後又回到原始 -1R 止損。
            trailing_trigger = (
                risk_pct * TRAILING_TRIGGER_R_MULT
                if risk_pct > 0 else configured_trigger
            )
            trailing_callback = (
                max(TRAILING_CALLBACK_PCT, risk_pct * TRAILING_CALLBACK_R_MULT)
                if risk_pct > 0 else TRAILING_CALLBACK_PCT
            )

            # 正式 1.5R 移動停利之前的早期保護層。曾有小幅有效浮盈後若
            # 明顯回吐，先在成本上方附近退出，避免 +0.3% 一路退成完整 -1R。
            # 費用與預估滑點是最低門檻，確保保護價不設定在必然淨虧處。
            round_trip_cost_pct = 2 * TAKER_FEE_RATE + SLIPPAGE_PCT
            is_bounce = meta.get("profit_profile") == "BOUNCE"
            early_guard_trigger = max(
                BOUNCE_EARLY_PROFIT_GUARD_TRIGGER_PCT
                if is_bounce else EARLY_PROFIT_GUARD_TRIGGER_PCT,
                round_trip_cost_pct,
            )
            early_guard_exit = max(
                BOUNCE_EARLY_PROFIT_GUARD_EXIT_PCT
                if is_bounce else EARLY_PROFIT_GUARD_EXIT_PCT,
                round_trip_cost_pct,
            )
            is_trend_extension = meta.get("profit_profile") == "TREND_EXTENSION"
            if is_trend_extension:
                early_guard_trigger = max(TREND_EXTENSION_GUARD_TRIGGER_PCT, round_trip_cost_pct)
                early_guard_exit = max(
                    TREND_EXTENSION_GUARD_EXIT_PCT,
                    round_trip_cost_pct,
                    highest_pnl * TREND_EXTENSION_MIN_CAPTURE_RATIO,
                )
                trailing_trigger = max(trailing_trigger, early_guard_trigger)
                meta["dynamic_profit_floor_pct"] = early_guard_exit
                pos["dynamic_profit_floor_pct"] = early_guard_exit
            if (
                ENABLE_EARLY_PROFIT_GUARD
                and highest_pnl >= early_guard_trigger
                and (is_trend_extension or highest_pnl < trailing_trigger)
                and not meta.get("early_profit_guard_armed")
            ):
                guard_price = entry_p * (
                    1.0 + early_guard_exit if side == "LONG" else 1.0 - early_guard_exit
                )
                meta["early_profit_guard_armed"] = True
                meta["early_profit_guard_price"] = guard_price
                pos["early_profit_guard_price"] = guard_price
                self.log(
                    f"🛡️ [早期獲利保護] {symbol} 峰值 {highest_pnl:.4%} 已達"
                    f" {early_guard_trigger:.4%}，立即設定保護價 {guard_price:.6g}"
                    f"（回吐至 {early_guard_exit:.4%} 離場）",
                    "SUCCESS",
                )
            if (
                ENABLE_EARLY_PROFIT_GUARD
                and meta.get("early_profit_guard_armed")
                and (is_trend_extension or highest_pnl < trailing_trigger)
                and pnl_pct <= early_guard_exit
            ):
                close_reason = (
                    "趨勢延伸峰值保護平倉" if is_trend_extension
                    else "早期獲利保護回吐平倉"
                )
                # 保護啟動當下視為已掛出 STOP；下次報價即使已跳過觸發價，
                # 也以保護價（再加平倉滑價）模擬成交，不追逐已浮虧的現價。
                guard_price = float(
                    meta.get("early_profit_guard_price")
                    or entry_p * (
                        1.0 + early_guard_exit if side == "LONG" else 1.0 - early_guard_exit
                    )
                )
                await self.close_position(symbol, guard_price, close_reason)
                continue

            if ENABLE_TRAILING_STOP and highest_pnl >= trailing_trigger:
                old_sl = pos.get("sl", 0.0)
                if side == "LONG":
                    trail_sl = entry_p * (1.0 + highest_pnl - trailing_callback)
                    npg_floor = entry_p * (1.0 + NET_PROFIT_GUARANTEE_BUFFER)
                    trail_sl = max(trail_sl, npg_floor)
                    if trail_sl > old_sl:
                        pos["sl"] = trail_sl
                        pos["is_breakeven_moved"] = True
                        meta["sl"] = trail_sl
                        meta["is_breakeven_moved"] = True
                        self.log(f"📈 [紙上交易/移動止利] {symbol} 無槓桿利潤峰值 {highest_pnl:.4%}，止利線推至 {trail_sl:.6g}（回吐 {trailing_callback:.4%} 平倉）", "SUCCESS")
                else:
                    trail_sl = entry_p * (1.0 - highest_pnl + trailing_callback)
                    npg_ceiling = entry_p * (1.0 - NET_PROFIT_GUARANTEE_BUFFER)
                    trail_sl = min(trail_sl, npg_ceiling)
                    if trail_sl < old_sl or old_sl == 0.0:
                        pos["sl"] = trail_sl
                        pos["is_breakeven_moved"] = True
                        meta["sl"] = trail_sl
                        meta["is_breakeven_moved"] = True
                        self.log(f"📉 [紙上交易/移動止利] {symbol} 無槓桿利潤峰值 {highest_pnl:.4%}，止利線推至 {trail_sl:.6g}（回吐 {trailing_callback:.4%} 平倉）", "SUCCESS")

            # 24小時時間過濾
            if ENABLE_24H_TIME_FILTER and (now_ts - pos.get("open_timestamp", now_ts)) >= 86400:
                await self.close_position(symbol, curr_p, "時間過濾 (24h 無效震盪離場)")
                continue

            # 災難性硬防線止損 (不論是否關閉止損，一旦價格虧損超過此負值門檻即強制平倉)
            if MAX_ACCEPTABLE_LOSS_PCT < 0:
                current_loss_pct = (curr_p - entry_p) / entry_p if side == "LONG" else (entry_p - curr_p) / entry_p
                if current_loss_pct <= MAX_ACCEPTABLE_LOSS_PCT:
                    self.log(
                        f"🚨 [紙上交易/災難止損] {symbol} {side} 虧損 {current_loss_pct:.2%} 已觸碰或超過硬防線 {MAX_ACCEPTABLE_LOSS_PCT:.2%}，強制市價平倉",
                        "DANGER"
                    )
                    await self.close_position(symbol, curr_p, "本地最大虧損門檻觸發")
                    continue

            # 獲利了結參考提醒（跟 BinanceTestnetAccount 邏輯一致）：從高點
            # 回吐超過門檻時亮警訊。警訊亮起後不是一有反彈就立刻平倉——
            # 只要浮盈還在持續往上爬，就繼續讓它跑；只有等反彈自己也開始
            # 回落（找到這次反彈的高點）時，才把握那個高點平倉，不要在
            # 反彈剛起步、還在往上時就提早出場。
            #
            # 這裡的 pnl_pct 是價格原始漲跌幅（未扣手續費/滑價），實測
            # ADA/USDT 17:01 這筆就是抓到一個只有 0.001% 的極小反彈就平倉，
            # 扣掉開平倉手續費(2×TAKER_FEE_RATE)+平倉滑價(SLIPPAGE_PCT)後
            # 實際是淨損-0.20——加上最低獲利門檻，反彈高點的獲利要先蓋過
            # 來回成本才值得把握。
            round_trip_cost_pct = 2 * TAKER_FEE_RATE + SLIPPAGE_PCT
            min_rebound_exit_pct = round_trip_cost_pct + PROFIT_ALERT_MIN_NET_PCT
            peak_pnl_pct = highest_pnl
            profit_giveback_ratio = (peak_pnl_pct - pnl_pct) / peak_pnl_pct if peak_pnl_pct > 0 else 0.0
            profit_alert = (
                peak_pnl_pct >= PROFIT_ALERT_MIN_PEAK_PCT
                and pnl_pct > min_rebound_exit_pct
                and profit_giveback_ratio >= PROFIT_ALERT_GIVEBACK_RATIO
            )
            if ENABLE_PROFIT_GIVEBACK_EXIT and profit_alert:
                # 直接於峰值回吐時平倉，避免讓獲利峰值回撤後再反彈。
                await self.close_position(symbol, curr_p, "峰值回吐平倉")
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
                    # 僅在已啟用移動保本或部位曾達到設定峰值比例時，才把本地 SL 視為真正平倉
                    highest_peak = float(meta.get("highest_pnl_pct", -999.0))
                    if pos.get("is_breakeven_moved") or highest_peak >= float(SL_ONLY_AFTER_PEAK_PCT):
                        # Simulate an already-resting protective stop at its trigger;
                        # close_position adds the configured market slippage.
                        await self.close_position(symbol, sl_price, reason)
                        continue
                    else:
                        # 忽略此輪穿越，視為觀察線；記錄日誌以便追蹤
                        self.log(
                            f"🔍 [紙上交易] {symbol} 觸及 SL={sl_price:.6g} 但未達峰值 {SL_ONLY_AFTER_PEAK_PCT:.4%}，暫不平倉",
                            "INFO",
                        )
                        continue
            else:
                if tp_price > 0 and curr_p <= tp_price:
                    await self.close_position(symbol, curr_p, "觸發止盈 (Take-Profit)")
                    continue
                if sl_price > 0 and curr_p >= sl_price:
                    reason = "觸發移動止利 (Trailing Take-Profit)" if pos.get("is_breakeven_moved") else "觸發止損 (Stop-Loss)"
                    highest_peak = float(meta.get("highest_pnl_pct", -999.0))
                    if pos.get("is_breakeven_moved") or highest_peak >= float(SL_ONLY_AFTER_PEAK_PCT):
                        # Simulate an already-resting protective stop at its trigger;
                        # close_position adds the configured market slippage.
                        await self.close_position(symbol, sl_price, reason)
                        continue
                    else:
                        self.log(
                            f"🔍 [紙上交易] {symbol} 觸及 SL={sl_price:.6g} 但未達峰值 {SL_ONLY_AFTER_PEAK_PCT:.4%}，暫不平倉",
                            "INFO",
                        )
                        continue

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
