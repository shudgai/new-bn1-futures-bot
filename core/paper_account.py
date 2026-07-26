import os
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Callable, Dict, List, Optional
from core.config import INITIAL_BALANCE, TAKER_FEE_RATE, SLIPPAGE_PCT, TRAILING_TRIGGER_PCT, TRAILING_PULLBACK_PCT, NET_PROFIT_GUARANTEE_BUFFER, get_leverage, get_signal_leverage

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "paper_account.json")

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

def get_taipei_now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return datetime.now(TAIPEI_TZ).strftime(fmt)

def get_taipei_time_short() -> str:
    return datetime.now(TAIPEI_TZ).strftime("%H:%M:%S")

class PaperAccount:
    def __init__(self):
        self.balance = INITIAL_BALANCE
        self.realized_pnl = 0.0
        self.positions: Dict[str, dict] = {} # symbol -> pos_info
        self.trades: List[dict] = []
        self.logs: List[dict] = []
        self.closing_lock: set = set() # 避免重複平倉鎖定集合
        self.on_trade_closed: Optional[Callable[[], None]] = None
        self.load_state()

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.balance = data.get("balance", INITIAL_BALANCE)
                    self.realized_pnl = data.get("realized_pnl", 0.0)
                    self.positions = data.get("positions", {})
                    self.trades = data.get("trades", [])
                    self.logs = data.get("logs", [])
            except Exception:
                pass

    def save_state(self):
        data = {
            "balance": self.balance,
            "realized_pnl": self.realized_pnl,
            "positions": self.positions,
            "trades": self.trades,
            "logs": self.logs[-200:]
        }
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def log(self, message: str, level: str = "INFO"):
        entry = {
            "time": get_taipei_time_short(),
            "timestamp": time.time(),
            "text": message,
            "level": level
        }
        self.logs.append(entry)
        self.save_state()

    def open_position(self, symbol: str, side: str, price: float, amount_usdt: float, sl: float, tp: float, reason: str, atr: float = 0.0, leverage: int = None, signal_score: int = None):
        if symbol in self.positions or symbol in self.closing_lock:
            return False

        if leverage is None:
            leverage = get_signal_leverage(symbol, signal_score) if signal_score is not None else get_leverage(symbol)

        # 模擬 0.03% 市價單滑點成本 (Slippage Reserve)
        execution_price = price * (1 + SLIPPAGE_PCT) if side == "LONG" else price * (1 - SLIPPAGE_PCT)
        qty = (amount_usdt * leverage) / execution_price
        fee = (qty * execution_price) * TAKER_FEE_RATE
        self.balance -= (amount_usdt + fee)  # ✅ 扣除保證金 + 手續費（原本只扣手續費，導致餘額越交易越高）

        pos = {
            "symbol": symbol,
            "side": side,
            "entry_price": execution_price,
            "qty": qty,
            "margin": amount_usdt,
            "leverage": leverage,
            "sl": sl,
            "tp": tp,
            "atr": atr if atr > 0 else execution_price * 0.015,
            "is_breakeven_moved": False,
            "highest_price": execution_price,
            "lowest_price": execution_price,
            "peak_profit_pct": 0.0,
            "open_timestamp": time.time(),
            "open_time": get_taipei_now_str(),
            "reason": reason,
            "signal_score": signal_score
        }
        self.positions[symbol] = pos

        trade = {
            "id": int(time.time() * 1000),
            "time": get_taipei_now_str("%m/%d %H:%M:%S"),
            "symbol": symbol,
            "action": f"OPEN_{side}",
            "side": side,
            "price": round(execution_price, 4),
            "qty": round(qty, 4),
            "amount": amount_usdt,
            "fee": round(fee, 4),
            "pnl": 0.0,
            "status": "OPEN",
            "leverage": leverage,
            "signal_score": signal_score,
            "reason": reason,
            "sl": sl,
            "tp": tp
        }
        self.trades.insert(0, trade)
        self.log(f"🚀 開倉成功 [{side}] {symbol} @ {execution_price:.4f} ({leverage}x槓桿, 含滑點 0.03%, 止損: {sl:.4f}, 止利: {tp:.4f})", "SUCCESS")
        self.save_state()
        return True

    def close_position(self, symbol: str, current_price: float, close_reason: str):
        # 防範重複平倉 (Duplicate Close Lock)
        if symbol not in self.positions or symbol in self.closing_lock:
            return False

        self.closing_lock.add(symbol)
        try:
            pos = self.positions.pop(symbol)
            side = pos["side"]
            entry_price = pos["entry_price"]
            qty = pos["qty"]
            margin = pos["margin"]

            # 平倉滑點預留
            exec_close_price = current_price * (1 - SLIPPAGE_PCT) if side == "LONG" else current_price * (1 + SLIPPAGE_PCT)

            if side == "LONG":
                raw_pnl = (exec_close_price - entry_price) * qty
            else:
                raw_pnl = (entry_price - exec_close_price) * qty

            open_fee = (qty * entry_price) * TAKER_FEE_RATE
            close_fee = (qty * exec_close_price) * TAKER_FEE_RATE
            total_fee = open_fee + close_fee
            net_pnl = raw_pnl - total_fee
            self.balance += margin + net_pnl
            self.realized_pnl += net_pnl

            trade = {
                "id": int(time.time() * 1000),
                "time": get_taipei_now_str("%m/%d %H:%M:%S"),
                "symbol": symbol,
                "action": f"CLOSE_{side}",
                "side": side,
                "price": current_price,
                "qty": round(qty, 4),
                "amount": margin,
                "fee": round(total_fee, 4),
                "pnl": round(net_pnl, 4),
                "status": "CLOSED",
                "reason": close_reason
            }
            self.trades.insert(0, trade)
            log_level = "SUCCESS" if net_pnl > 0 else "DANGER"
            self.log(f"🏁 平倉 [{side}] {symbol} @ {current_price:.4f} | 淨損益: {net_pnl:+.2f} USDT ({close_reason})", log_level)
            self.save_state()
            if self.on_trade_closed:
                try:
                    # 僅發送非同步工作通知；不得讓分析錯誤影響平倉成功。
                    self.on_trade_closed()
                except Exception:
                    pass
            return True
        finally:
            self.closing_lock.discard(symbol)

    def update_positions(self, ticker_prices: Dict[str, float]):
        total_unrealized = 0.0
        now_ts = time.time()

        for symbol, pos in list(self.positions.items()):
            curr_p = ticker_prices.get(symbol) or ticker_prices.get(f"{symbol}:USDT") or ticker_prices.get(symbol.replace('/USDT', ''))
            if curr_p is None:
                continue

            side = pos["side"]
            entry_p = pos["entry_price"]
            atr = pos.get("atr", entry_p * 0.015)
            open_ts = pos.get("open_timestamp", now_ts)

            # 初始化最高 / 最低價紀錄
            if "highest_price" not in pos: pos["highest_price"] = entry_p
            if "lowest_price" not in pos: pos["lowest_price"] = entry_p

            # 1. 移動止利（百分比制）：無槓桿利潤達 TRAILING_TRIGGER_PCT 啟動，
            #    利潤從高點回落 TRAILING_PULLBACK_PCT 時平倉。
            if "peak_profit_pct" not in pos:
                pos["peak_profit_pct"] = 0.0

            if side == "LONG":
                curr_profit_pct = (curr_p - entry_p) / entry_p
            else:
                curr_profit_pct = (entry_p - curr_p) / entry_p

            # 更新無槓桿利潤百分比歷史最高值
            if curr_profit_pct > pos["peak_profit_pct"]:
                pos["peak_profit_pct"] = curr_profit_pct

            peak = pos["peak_profit_pct"]

            if peak >= TRAILING_TRIGGER_PCT:
                # 計算 trailing stop 價格：鎖住 peak 的 TRAILING_PULLBACK_PCT
                if side == "LONG":
                    trail_sl = entry_p * (1.0 + peak * TRAILING_PULLBACK_PCT)
                    # Net Profit Guarantee：確保扣完手續費後仍為正
                    npg_floor = entry_p * (1.0 + NET_PROFIT_GUARANTEE_BUFFER)
                    trail_sl = max(trail_sl, npg_floor)
                    if trail_sl > pos["sl"]:
                        pos["sl"] = trail_sl
                        pos["is_breakeven_moved"] = True
                        self.log(f"📈 [移動止利] {symbol} 無槓桿利潤峰值 {peak:.4%}，止利線推至 {pos['sl']:.4f}（回吐 {1-TRAILING_PULLBACK_PCT:.0%} 平倉）", "SUCCESS")
                else:  # SHORT
                    trail_sl = entry_p * (1.0 - peak * TRAILING_PULLBACK_PCT)
                    npg_ceiling = entry_p * (1.0 - NET_PROFIT_GUARANTEE_BUFFER)
                    trail_sl = min(trail_sl, npg_ceiling)
                    if trail_sl < pos["sl"]:
                        pos["sl"] = trail_sl
                        pos["is_breakeven_moved"] = True
                        self.log(f"📉 [移動止利] {symbol} 無槓桿利潤峰值 {peak:.4%}，止利線推至 {pos['sl']:.4f}（回吐 {1-TRAILING_PULLBACK_PCT:.0%} 平倉）", "SUCCESS")

            # 2. 24小時時間過濾 (超時平倉)
            if (now_ts - open_ts) >= 86400:
                self.close_position(symbol, curr_p, "時間過濾 (24h 無效震盪離場)")
                continue

            # 3. 觸發平倉比對
            if side == "LONG":
                if curr_p >= pos["tp"]:
                    reason = "觸發止盈 (Take-Profit)" 
                    self.close_position(symbol, curr_p, reason)
                elif curr_p <= pos["sl"]:
                    reason = "觸發移動止利 (Trailing Take-Profit)" if pos.get("is_breakeven_moved") else "觸發止損 (Stop-Loss)"
                    self.close_position(symbol, curr_p, reason)
            else: # SHORT
                if curr_p <= pos["tp"]:
                    reason = "觸發止盈 (Take-Profit)"
                    self.close_position(symbol, curr_p, reason)
                elif curr_p >= pos["sl"]:
                    reason = "觸發移動止利 (Trailing Take-Profit)" if pos.get("is_breakeven_moved") else "觸發止損 (Stop-Loss)"
                    self.close_position(symbol, curr_p, reason)

            # 計算未實現損益
            if side == "LONG":
                unrealized = (curr_p - entry_p) * pos["qty"]
            else:
                unrealized = (entry_p - curr_p) * pos["qty"]
            total_unrealized += unrealized

        return total_unrealized

