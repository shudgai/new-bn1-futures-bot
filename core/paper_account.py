import os
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Callable, Dict, List, Optional
from core.config import INITIAL_BALANCE, TAKER_FEE_RATE, SLIPPAGE_PCT, TRAILING_LOCK_ATR_MULT, BREAKEVEN_LOCK_ATR_MULT, BREAKEVEN_LOCK_PROFIT_PCT, NET_PROFIT_GUARANTEE_BUFFER, get_leverage, get_signal_leverage

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

            # 1. 追蹤最高/最低獲利價 (Trailing High/Low Tracking)
            if side == "LONG":
                if curr_p > pos["highest_price"]:
                    pos["highest_price"] = curr_p

                # 每次都用目前記錄的最高價重新檢查門檻，不再只在創新高當下才判斷，
                # 避免獲利卡在「沒達到追蹤止利門檻、之後也沒再創新高」而一直沒鎖利。
                max_profit = pos["highest_price"] - entry_p

                if max_profit >= atr * TRAILING_LOCK_ATR_MULT:
                    trail_sl = entry_p + (max_profit * 0.60)  # 鎖住 60% 獲利底線（給行情更多回調空間）

                    # ╔═ Net Profit Guarantee (保本線強制 clamp) ══════════════════╗
                    # 確保 trail_sl 比「進場價 + 手續費安全帶」更高，
                    # 也就是說就算在 trail_sl 觸發平倉，扣完全部手續費+滑點後淨損益一定是正數。
                    npg_floor = entry_p * (1.0 + NET_PROFIT_GUARANTEE_BUFFER)
                    trail_sl = max(trail_sl, npg_floor)
                    # ╙════════════════════════════════════════════════════════

                    if trail_sl > pos["sl"]:
                        pos["sl"] = trail_sl
                        pos["is_breakeven_moved"] = True
                        self.log(f"📈 [獲利追蹤止利上推] {symbol} 創新高 ({pos['highest_price']:.4f})，已鎖定 75% 獲利底線價 ({pos['sl']:.4f}) [保本線地板: {npg_floor:.4f}]", "SUCCESS")
                elif max_profit >= atr * BREAKEVEN_LOCK_ATR_MULT:
                    # 獲利還沒到全額移動止利門檻，先鎖住已獲利的一部分（而非只鎖保本），避免回吐成虧損
                    npg_floor = entry_p * (1.0 + NET_PROFIT_GUARANTEE_BUFFER)
                    partial_lock = entry_p + (max_profit * BREAKEVEN_LOCK_PROFIT_PCT)
                    lock_sl = max(partial_lock, npg_floor)
                    if lock_sl > pos["sl"]:
                        pos["sl"] = lock_sl
                        pos["is_breakeven_moved"] = True
                        self.log(f"🔒 [保本鎖利] {symbol} 獲利達 {BREAKEVEN_LOCK_ATR_MULT}x ATR，SL 上移至 {pos['sl']:.4f}（鎖住已獲利 {BREAKEVEN_LOCK_PROFIT_PCT:.0%}）", "SUCCESS")
            else: # SHORT
                if curr_p < pos["lowest_price"]:
                    pos["lowest_price"] = curr_p

                max_profit = entry_p - pos["lowest_price"]

                if max_profit >= atr * TRAILING_LOCK_ATR_MULT:
                    # SHORT 的 SL 在 entry 上方，追蹤止利應把 SL 往下調（收緊保護獲利）
                    trail_sl = entry_p - (max_profit * 0.60)  # 鎖住 60% 獲利底線

                    # ╔═ Net Profit Guarantee (保本線強制 clamp) ══════════════════╗
                    # SHORT 的保本線：trail_sl 必須低於「進場價 × (1 - NPG_BUFFER)」，
                    # 確保就算在 trail_sl 觸發平倉，扣完手續費+滑點後淨損益一定是正數。
                    npg_ceiling = entry_p * (1.0 - NET_PROFIT_GUARANTEE_BUFFER)
                    trail_sl = min(trail_sl, npg_ceiling)
                    # ╙════════════════════════════════════════════════════════

                    if trail_sl < pos["sl"]:  # ✅ SHORT: 新 SL 比原 SL 更低（更非市價）才更新
                        pos["sl"] = trail_sl
                        pos["is_breakeven_moved"] = True
                        self.log(f"📉 [獲利追蹤止利下推] {symbol} 創新低 ({pos['lowest_price']:.4f})，已鎖定 75% 獲利底線價 ({pos['sl']:.4f}) [保本線上限: {npg_ceiling:.4f}]", "SUCCESS")
                elif max_profit >= atr * BREAKEVEN_LOCK_ATR_MULT:
                    npg_ceiling = entry_p * (1.0 - NET_PROFIT_GUARANTEE_BUFFER)
                    partial_lock = entry_p - (max_profit * BREAKEVEN_LOCK_PROFIT_PCT)
                    lock_sl = min(partial_lock, npg_ceiling)
                    if lock_sl < pos["sl"]:
                        pos["sl"] = lock_sl
                        pos["is_breakeven_moved"] = True
                        self.log(f"🔒 [保本鎖利] {symbol} 獲利達 {BREAKEVEN_LOCK_ATR_MULT}x ATR，SL 下移至 {pos['sl']:.4f}（鎖住已獲利 {BREAKEVEN_LOCK_PROFIT_PCT:.0%}）", "SUCCESS")

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
                    reason = "觸發追蹤止利 (75% 獲利鎖定)" if pos.get("is_breakeven_moved") else "觸發止損 (Stop-Loss)"
                    self.close_position(symbol, curr_p, reason)
            else: # SHORT
                if curr_p <= pos["tp"]:
                    reason = "觸發止盈 (Take-Profit)"
                    self.close_position(symbol, curr_p, reason)
                elif curr_p >= pos["sl"]:
                    reason = "觸發追蹤止利 (75% 獲利鎖定)" if pos.get("is_breakeven_moved") else "觸發止損 (Stop-Loss)"
                    self.close_position(symbol, curr_p, reason)

            # 計算未實現損益
            if side == "LONG":
                unrealized = (curr_p - entry_p) * pos["qty"]
            else:
                unrealized = (entry_p - curr_p) * pos["qty"]
            total_unrealized += unrealized

        return total_unrealized

