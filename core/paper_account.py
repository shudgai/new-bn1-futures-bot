import os
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional
from core.config import INITIAL_BALANCE, LEVERAGE

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

    def open_position(self, symbol: str, side: str, price: float, amount_usdt: float, sl: float, tp: float, reason: str, atr: float = 0.0):
        if symbol in self.positions or symbol in self.closing_lock:
            return False

        qty = (amount_usdt * LEVERAGE) / price
        fee = amount_usdt * 0.0004
        self.balance -= fee

        pos = {
            "symbol": symbol,
            "side": side,
            "entry_price": price,
            "qty": qty,
            "margin": amount_usdt,
            "sl": sl,
            "tp": tp,
            "atr": atr if atr > 0 else price * 0.015,
            "is_breakeven_moved": False,
            "open_timestamp": time.time(),
            "open_time": get_taipei_now_str(),
            "reason": reason
        }
        self.positions[symbol] = pos

        trade = {
            "id": int(time.time() * 1000),
            "time": get_taipei_now_str("%m/%d %H:%M:%S"),
            "symbol": symbol,
            "action": f"OPEN_{side}",
            "side": side,
            "price": price,
            "qty": round(qty, 4),
            "amount": amount_usdt,
            "fee": round(fee, 4),
            "pnl": 0.0,
            "status": "OPEN"
        }
        self.trades.insert(0, trade)
        self.log(f"🚀 開倉成功 [{side}] {symbol} @ {price:.4f} (止損: {sl:.4f}, 止利: {tp:.4f})", "SUCCESS")
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

            if side == "LONG":
                raw_pnl = (current_price - entry_price) * qty
            else:
                raw_pnl = (entry_price - current_price) * qty

            open_fee = margin * 0.0004
            close_fee = (qty * current_price) * 0.0004
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
            sl = pos["sl"]
            tp = pos["tp"]
            atr = pos.get("atr", entry_p * 0.015)
            open_ts = pos.get("open_timestamp", now_ts)

            # 1. 移動止損至保本價 (Trailing Stop to Breakeven when gain >= 0.8x ATR)
            if not pos.get("is_breakeven_moved", False):
                if side == "LONG" and (curr_p - entry_p) >= (0.8 * atr):
                    pos["sl"] = entry_p # 上移止損至保本價
                    pos["is_breakeven_moved"] = True
                    self.log(f"🛡️ [保本移動止損] {symbol} 盈利達 0.8x ATR ({curr_p:.4f})，止損位已自動移至保本價 ({entry_p:.4f})", "SUCCESS")
                elif side == "SHORT" and (entry_p - curr_p) >= (0.8 * atr):
                    pos["sl"] = entry_p # 下移止損至保本價
                    pos["is_breakeven_moved"] = True
                    self.log(f"🛡️ [保本移動止損] {symbol} 盈利達 0.8x ATR ({curr_p:.4f})，止損位已自動移至保本價 ({entry_p:.4f})", "SUCCESS")

            # 2. 時間過濾超時平倉 (24 小時無效震盪自動平倉)
            if (now_ts - open_ts) >= 86400: # 86400秒 = 24小時
                self.close_position(symbol, curr_p, "時間過濾 (24h 無效震盪離場)")
                continue

            # 3. 正常 SL / TP 比對平倉
            if side == "LONG":
                unrealized = (curr_p - entry_p) * pos["qty"]
                if curr_p <= pos["sl"]:
                    reason = "觸發保本止損 (Breakeven)" if pos.get("is_breakeven_moved") else "觸發止損 (Stop-Loss)"
                    self.close_position(symbol, curr_p, reason)
                elif curr_p >= tp:
                    self.close_position(symbol, curr_p, "觸發止利 (Take-Profit)")
            else: # SHORT
                unrealized = (curr_p - entry_p) * pos["qty"] * -1
                if curr_p >= pos["sl"]:
                    reason = "觸發保本止損 (Breakeven)" if pos.get("is_breakeven_moved") else "觸發止損 (Stop-Loss)"
                    self.close_position(symbol, curr_p, reason)
                elif curr_p <= tp:
                    self.close_position(symbol, curr_p, "觸發止利 (Take-Profit)")

            total_unrealized += unrealized

        return total_unrealized

