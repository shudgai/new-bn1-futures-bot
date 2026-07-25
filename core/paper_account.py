import os
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional
from core.config import INITIAL_BALANCE, LEVERAGE, TAKER_FEE_RATE, SLIPPAGE_PCT

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

        # 模擬 0.03% 市價單滑點成本 (Slippage Reserve)
        execution_price = price * (1 + SLIPPAGE_PCT) if side == "LONG" else price * (1 - SLIPPAGE_PCT)
        qty = (amount_usdt * LEVERAGE) / execution_price
        fee = (qty * execution_price) * TAKER_FEE_RATE
        self.balance -= fee

        pos = {
            "symbol": symbol,
            "side": side,
            "entry_price": execution_price,
            "qty": qty,
            "margin": amount_usdt,
            "sl": sl,
            "tp": tp,
            "atr": atr if atr > 0 else execution_price * 0.015,
            "is_breakeven_moved": False,
            "highest_price": execution_price,
            "lowest_price": execution_price,
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
            "price": round(execution_price, 4),
            "qty": round(qty, 4),
            "amount": amount_usdt,
            "fee": round(fee, 4),
            "pnl": 0.0,
            "status": "OPEN"
        }
        self.trades.insert(0, trade)
        self.log(f"🚀 開倉成功 [{side}] {symbol} @ {execution_price:.4f} (含滑點 0.03%, 止損: {sl:.4f}, 止利: {tp:.4f})", "SUCCESS")
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
            return True
        finally:
            self.closing_lock.discard(symbol)

    def update_positions(self, ticker_prices: Dict[str, float]):
        total_unrealized = 0.0
        now_ts = time.time()

        for symbol, pos in list(self.positions.items()):
            # 獲取當前價格（處理不同可能的 Key 格式）
            curr_p = ticker_prices.get(symbol) or ticker_prices.get(f"{symbol}:USDT") or ticker_prices.get(symbol.replace('/USDT', ''))
            if curr_p is None:
                continue

            side = pos["side"]
            entry_p = pos["entry_price"]
            atr = pos.get("atr", entry_p * 0.015)
            open_ts = pos.get("open_timestamp", now_ts)

            # --- 趨勢獵人動態追蹤止利模式 ---

            # 確保有初始化最高/最低價 (若開倉時沒設，則以入場價為基礎)
            if "highest_price" not in pos: pos["highest_price"] = entry_p
            if "lowest_price" not in pos: pos["lowest_price"] = entry_p

            # 階段一：保本鎖定 (當獲利達 0.8x ATR 時)
            if not pos.get("is_breakeven_moved", False):
                if side == "LONG" and (curr_p - entry_p) >= (0.8 * atr):
                    pos["sl"] = entry_p
                    pos["is_breakeven_moved"] = True
                    pos["highest_price"] = max(pos.get("highest_price", entry_p), curr_p)
                    self.log(f"🛡️ [保本觸發] {symbol} 獲利達 0.8x ATR，止損已鎖定保本價 ({entry_p:.4f})", "SUCCESS")
                elif side == "SHORT" and (entry_p - curr_p) >= (0.8 * atr):
                    pos["sl"] = entry_p
                    pos["is_breakeven_moved"] = True
                    pos["lowest_price"] = min(pos.get("lowest_price", entry_p), curr_p)
                    self.log(f"🛡️ [保本觸發] {symbol} 獲利達 0.8x ATR，止損已鎖定保本價 ({entry_p:.4f})", "SUCCESS")

            # 階段二：動態追蹤與 TP 無上限推升 (當已進入保本狀態時)
            if pos.get("is_breakeven_moved", False):
                if side == "LONG":
                    # 更新最高價
                    if curr_p > pos["highest_price"]:
                        pos["highest_price"] = curr_p
                        
                        # 1. 動態推升止損 (SL)：鎖定最高獲利的 75%
                        # 公式：新止損 = 入場價 + (最高獲利 * 0.75)
                        new_sl = entry_p + (pos["highest_price"] - entry_p) * 0.75
                        if new_sl > pos["sl"]:
                            pos["sl"] = new_sl
                            # 2. 同步推升止利 (TP)：讓利潤繼續奔跑
                            # 每創新高，TP 自動往上推 1.5x ATR
                            pos["tp"] = pos["highest_price"] + (1.5 * atr)
                            self.log(f"📈 [動態追蹤] {symbol} 突破新高 ({curr_p:.4f})，SL 推升至 ({pos['sl']:.4f})，TP 延展至 ({pos['tp']:.4f})", "SUCCESS")
                
                else: # SHORT 邏輯
                    # 更新最低價
                    if curr_p < pos["lowest_price"]:
                        pos["lowest_price"] = curr_p
                        
                        # 1. 動態推升止損 (SL)：鎖定最高獲利（對空單是最低點）的 75%
                        # 公式：新止損 = 入場價 - (最高獲利 * 0.75)
                        new_sl = entry_p - (entry_p - pos["lowest_price"]) * 0.75
                        if new_sl < pos["sl"]:
                            pos["sl"] = new_sl
                            # 2. 同步推升止利 (TP)：讓利潤繼續奔跑
                            pos["tp"] = pos["lowest_price"] - (1.5 * atr)
                            self.log(f"📉 [動態追蹤] {symbol} 突破新低 ({curr_p:.4f})，SL 推升至 ({pos['sl']:.4f})，TP 延展至 ({pos['tp']:.4f})", "SUCCESS")

            # --- 階段三：最終平倉判定 ---
            
            # 24小時時間過濾 (自動離場)
            if (now_ts - open_ts) >= 86400:
                self.close_position(symbol, curr_p, "時間過濾 (24h 無效震盪離場)")
                continue

            # 正常 SL / TP 比對平倉
            if side == "LONG":
                # 判斷跌破動態止損 (SL)
                if curr_p <= pos["sl"]:
                    reason = "觸發保本止損" if pos.get("is_breakeven_moved") else "觸發止損 (Stop-Loss)"
                    self.close_position(symbol, curr_p, reason)
                # 判斷達到動態止利 (TP)
                elif curr_p >= pos["tp"]:
                    self.close_position(symbol, curr_p, "觸發動態止利 (Dynamic TP)")
            
            else: # SHORT
                # 判斷突破動態止損 (SL)
                if curr_p >= pos["sl"]:
                    reason = "觸發保本止損" if pos.get("is_breakeven_moved") else "觸發止損 (Stop-Loss)"
                    self.close_position(symbol, curr_p, reason)
                # 判斷跌破動態止利 (TP)
                elif curr_p <= pos["tp"]:
                    self.close_position(symbol, curr_p, "觸發動態止利 (Dynamic TP)")

            # 計算未實現損益
            if side == "LONG":
                unrealized = (curr_p - entry_p) * pos["qty"]
            else:
                unrealized = (entry_p - curr_p) * pos["qty"]
            total_unrealized += unrealized

        return total_unrealized

