import ccxt
import math
import time

def calculate_step_ratchet_stop(
    side: str,
    entry_price: float,
    extreme_price: float,
    current_stop: float | None = None,
    activation_pct: float = 0.05,
    step_size: float = 0.02,
    callback_pct: float = 0.01,
) -> float | None:
    side = side.upper()
    if side not in ("LONG", "SHORT"):
        raise ValueError("side 必須為 'LONG' 或 'SHORT'")
        
    # 1. 計算浮盈比例
    if side == "LONG":
        max_profit_pct = (extreme_price - entry_price) / entry_price
    else:
        max_profit_pct = (entry_price - extreme_price) / entry_price
        
    if max_profit_pct < activation_pct:
        return current_stop
        
    # 2. 計算階梯基準
    steps_beyond = int((max_profit_pct - activation_pct + 1e-9) // step_size)
    current_step_level = activation_pct + (steps_beyond * step_size)
    locked_profit_pct = current_step_level - callback_pct
    
    # 3. 棘輪更新原則
    if side == "LONG":
        target_stop = entry_price * (1.0 + locked_profit_pct)
        if current_stop is None or target_stop > current_stop:
            return round(target_stop, 8)
        return current_stop
    else:
        target_stop = entry_price * (1.0 - locked_profit_pct)
        if current_stop is None or target_stop < current_stop:
            return round(target_stop, 8)
        return current_stop

class GateFuturesRatchetManager:
    def __init__(self, api_key: str, secret: str, is_testnet: bool = False):
        self.exchange = ccxt.gate({
            'apiKey': api_key,
            'secret': secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',  # 指定為 USDT 永續合約
            }
        })
        if is_testnet:
            self.exchange.set_sandbox_mode(True)
        # 紀錄已掛出的條件單 ID，方便替換
        self.last_order_id = None

    def cancel_previous_stop_order(self, symbol: str):
        """取消先前掛出的舊條件單"""
        if not self.last_order_id:
            return
        try:
            # Gate.io 觸發單通常需帶 stop 標記或透過 cancel_order
            self.exchange.cancel_order(
                id=self.last_order_id,
                symbol=symbol,
                params={'stop': True}
            )
            print(f"[Gate.io] 已撤銷舊止損委託單: {self.last_order_id}")
            self.last_order_id = None
        except Exception as e:
            print(f"[Gate.io] 撤銷舊單失敗 (可能已觸發或已取消): {e}")
            self.last_order_id = None

    def place_gate_ratchet_stop(self, symbol: str, side: str, amount: float, stop_price: float):
        """向 Gate.io 掛出新的觸發止損市價單 (Close-on-trigger)"""
        # 多單平倉方向為 sell，空單平倉方向為 buy
        close_side = 'sell' if side.upper() == 'LONG' else 'buy'
        # 觸發條件比較運算子：多單跌破出場 (<=)，空單漲破出場 (>=)
        rule = 2 if side.upper() == 'LONG' else 1  # Gate API rule: 1 為 >=, 2 為 <=
        
        # 1. 撤銷舊單
        self.cancel_previous_stop_order(symbol)
        
        # 2. 發送 Gate.io 條件單 (Trigger Price Order)
        params = {
            'stop': True,
            'stopPrice': stop_price,
            'rule': rule,
            'reduceOnly': True,  # 僅平倉，防止反向開倉
        }
        
        try:
            order = self.exchange.create_order(
                symbol=symbol,
                type='market',
                side=close_side,
                amount=amount,
                price=None,
                params=params
            )
            self.last_order_id = order.get('id')
            print(f"[Gate.io] 成功掛入階梯鎖利單 | ID: {self.last_order_id} | 觸發價: {stop_price} | 方向: {close_side}")
            return order
        except Exception as e:
            print(f"[Gate.io] 下單失敗: {e}")
            return None

    def update_position_stop(
        self,
        symbol: str,
        side: str,
        amount: float,
        entry_price: float,
        extreme_price: float,
        current_stop: float | None
    ) -> float | None:
        """主入口：計算新階梯並在有更新時同步到 Gate.io"""
        new_stop = calculate_step_ratchet_stop(
            side=side,
            entry_price=entry_price,
            extreme_price=extreme_price,
            current_stop=current_stop
        )
        # 當鎖利線向前推升時，更新 Gate.io 訂單
        if new_stop is not None and new_stop != current_stop:
            print(f"[{symbol}] 階梯推進！舊止損: {current_stop} -> 新鎖利線: {new_stop}")
            self.place_gate_ratchet_stop(
                symbol=symbol,
                side=side,
                amount=amount,
                stop_price=new_stop
            )
            return new_stop
        return current_stop

def execute_retracement_close_on_gate(
    exchange,
    symbol: str,               # 例如 'BTC/USDT:USDT'
    amount: float,             # 持倉數量
    current_price: float,      # 當前即時價格 (Tick / Last Price)
    highest_price: float,      # 持倉期間歷史最高價
    entry_price: float,        # 開倉均價
    activation_pct: float = 0.05,  # 啟動門檻 5%
    callback_pct: float = 0.01     # 回踩 1%
) -> bool:
    # 1. 檢查是否達 5% 啟動門檻
    max_profit_pct = (highest_price - entry_price) / entry_price
    if max_profit_pct < activation_pct:
        return False

    # 2. 計算回踩 1% 的觸發價
    trigger_price = highest_price * (1.0 - callback_pct)
    
    # 3. 觸及回踩價格，立即向 Gate.io 送出市價平多單
    if current_price <= trigger_price:
        print(f"[觸發回踩平倉] 最高點: {highest_price}, 當前價: {current_price} <= 觸發價: {trigger_price}")
        try:
            # 向 Gate.io 發送市價平倉單 (多單平倉方向為 sell，加 reduceOnly 確保只平倉不反開)
            order = exchange.create_order(
                symbol=symbol,
                type='market',
                side='sell',
                amount=amount,
                params={'reduceOnly': True}
            )
            print(f"[Gate.io] 成功平倉並結算利潤！訂單編號: {order.get('id')}")
            return True
        except Exception as e:
            print(f"[Gate.io] 平倉下單失敗: {e}")
            return False

    return False
