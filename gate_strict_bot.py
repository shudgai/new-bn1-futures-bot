import time
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime

# ==============================================================================
# 1. 系統與策略配置 (Configuration)
# ==============================================================================
CONFIG = {
    'gate': {
        'apiKey': 'YOUR_GATE_API_KEY',
        'secret': 'YOUR_GATE_API_SECRET',
        'options': {'defaultType': 'swap'},       # Gate.io USDT 永續合約
        'enableRateLimit': True,
    },
    'symbol': 'BTC/USDT:USDT',                    # 合約標的
    'timeframe': '15m',                           # 操作週期
    'trade_amount': 1,                            # 下單張數 / 數量

    # === 風控與出場門檻 ===
    'min_profit_pct': 0.005,                      # 影線平倉最低獲利門檻 (0.5%)
    'stop_loss_atr_mult': 2.0,                    # 絕對停損 ATR 倍數
    'poll_interval': 5,                           # 輪詢間隔 (秒)
}

# ==============================================================================
# 2. 指標計算模組 (Keltner Channel, MA5, MA15, ATR)
# ==============================================================================
def calculate_indicators(df: pd.DataFrame, kc_period: int = 20, atr_multiplier: float = 2.0) -> pd.DataFrame:
    df = df.copy()
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma15'] = df['close'].rolling(window=15).mean()
    df['kc_mid'] = df['close'].ewm(span=kc_period, adjust=False).mean()

    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=kc_period).mean()

    df['kc_upper'] = df['kc_mid'] + (df['atr'] * atr_multiplier)
    df['kc_lower'] = df['kc_mid'] - (df['atr'] * atr_multiplier)
    return df

def is_small_bar(row, avg_body):
    """實體小於近期平均的 0.85 倍判定為小 K 棒（蓄勢）"""
    return abs(row['close'] - row['open']) <= (avg_body * 0.85)

def is_big_bar(current_price, open_price, avg_body):
    """【妖幣放寬】當前實體大於近期平均的 1.0 倍即可視為具備破軌動能"""
    return abs(current_price - open_price) >= (avg_body * 1.0)

# ==============================================================================
# 3. 策略信號判斷核心 (純量化四大開倉模式)
# ==============================================================================
def check_all_entry_signals(df: pd.DataFrame, current_price: float, position: int) -> dict:
    signal = {"action": None, "type": None}
    if position != 0:
        return signal

    last_bar = df.iloc[-1]   # 未收線 Bar (即時)
    prev_bar = df.iloc[-2]   # 前一根已收線
    b2 = df.iloc[-3]
    b3 = df.iloc[-4]

    # 計算近期 14 根已收線 K 棒平均實體長度
    avg_body = (df['close'] - df['open']).abs().iloc[-15:-1].mean()

    # 通道與均線變化
    kc_width = df['kc_upper'] - df['kc_lower']
    channel_expanding = kc_width.iloc[-2] > kc_width.iloc[-3]
    ma5_slope_up = df['ma5'].iloc[-2] > df['ma5'].iloc[-3]
    ma5_slope_down = df['ma5'].iloc[-2] < df['ma5'].iloc[-3]

    is_bear = lambda r: r['close'] < r['open']
    is_bull = lambda r: r['close'] > r['open']

    curr_open = last_bar['open']
    curr_kc_lower = last_bar['kc_lower']
    curr_kc_upper = last_bar['kc_upper']

    # ---------------------------------------------------------
    # 模式 1: 即時破軌開倉 (蓄勢結構 + 大陰/大陽穿軌，不看 MA5 斜率)
    # ---------------------------------------------------------
    # (A) 空單即時破軌
    curr_is_big_red = (current_price < curr_open) and is_big_bar(current_price, curr_open, avg_body)
    curr_breaks_lower = (curr_open >= curr_kc_lower) and (current_price < curr_kc_lower)

    if curr_is_big_red and curr_breaks_lower:
        # 妖幣放寬：前一根只要不是反向超大實體吞噬 (>= 1.5 avg_body)，均放行破軌開空
        prev_not_huge_bull = not (is_bull(prev_bar) and abs(prev_bar['close'] - prev_bar['open']) >= avg_body * 1.5)
        if prev_not_huge_bull:
            return {"action": "SHORT", "type": "INSTANT_BREAK"}

    # (B) 多單即時破軌
    curr_is_big_green = (current_price > curr_open) and is_big_bar(current_price, curr_open, avg_body)
    curr_breaks_upper = (curr_open <= curr_kc_upper) and (current_price > curr_kc_upper)

    if curr_is_big_green and curr_breaks_upper:
        # 妖幣放寬：前一根只要不是反向超大實體吞噬 (>= 1.5 avg_body)，均放行破軌開多
        prev_not_huge_bear = not (is_bear(prev_bar) and abs(prev_bar['close'] - prev_bar['open']) >= avg_body * 1.5)
        if prev_not_huge_bear:
            return {"action": "LONG", "type": "INSTANT_BREAK"}

    # ---------------------------------------------------------
    # 模式 2: 延續開倉 (已在軌外順勢推進)
    # ---------------------------------------------------------
    continue_short = (current_price < curr_kc_lower) and \
                     (last_bar['ma5'] < last_bar['kc_mid']) and \
                     ma5_slope_down and \
                     (current_price < prev_bar['low'])

    continue_long = (current_price > curr_kc_upper) and \
                    (last_bar['ma5'] > last_bar['kc_mid']) and \
                    ma5_slope_up and \
                    (current_price > prev_bar['high'])

    if continue_short:
        return {"action": "SHORT", "type": "CONTINUATION"}
    if continue_long:
        return {"action": "LONG", "type": "CONTINUATION"}

    # ---------------------------------------------------------
    # 模式 3: 一般破軌開倉 (雙棒收線確認 + 通道張開)
    # ---------------------------------------------------------
    bar1_break_lower = (prev_bar['close'] < prev_bar['kc_lower']) and (prev_bar['open'] > prev_bar['kc_lower']) and is_bear(prev_bar)
    bar2_confirm_lower = (last_bar['close'] < last_bar['kc_lower']) and is_bear(last_bar)
    if bar1_break_lower and bar2_confirm_lower and channel_expanding and ma5_slope_down:
        return {"action": "SHORT", "type": "NORMAL_BREAK"}

    bar1_break_upper = (prev_bar['close'] > prev_bar['kc_upper']) and (prev_bar['open'] < prev_bar['kc_upper']) and is_bull(prev_bar)
    bar2_confirm_upper = (last_bar['close'] > last_bar['kc_upper']) and is_bull(last_bar)
    if bar1_break_upper and bar2_confirm_upper and channel_expanding and ma5_slope_up:
        return {"action": "LONG", "type": "NORMAL_BREAK"}

    # ---------------------------------------------------------
    # 模式 4: MA5 / MA15 交叉後開倉
    # ---------------------------------------------------------
    death_cross = (df['ma5'].iloc[-3] >= df['ma15'].iloc[-3]) and (df['ma5'].iloc[-2] < df['ma15'].iloc[-2])
    golden_cross = (df['ma5'].iloc[-3] <= df['ma15'].iloc[-3]) and (df['ma5'].iloc[-2] > df['ma15'].iloc[-2])

    if death_cross and channel_expanding and ma5_slope_down:
        return {"action": "SHORT", "type": "MA_CROSS"}
    if golden_cross and channel_expanding and ma5_slope_up:
        return {"action": "LONG", "type": "MA_CROSS"}

    return signal

# ==============================================================================
# 4. 出場風控驗證 (影線不平倉，除非有利潤；實體收線破中軌平倉)
# ==============================================================================
def check_exit_signals(df: pd.DataFrame, current_price: float, position: int, entry_price: float, min_profit_pct: float = 0.005) -> bool:
    if position == 0 or entry_price <= 0:
        return False

    # 1. 計算當前浮動盈虧比例
    if position == -1:  # 空單
        profit_ratio = (entry_price - current_price) / entry_price
    else:               # 多單
        profit_ratio = (current_price - entry_price) / entry_price

    has_profit = profit_ratio >= min_profit_pct

    # 2. 取【已收線完畢】的 K 棒檢驗中軌實體反破 (杜絕盤中插針/長影線誤觸)
    closed_bar = df.iloc[-2]
    kc_mid = closed_bar['kc_mid']

    # --- 空單出場 ---
    if position == -1:
        # 條件 (A)：嚴格等待收線，實體收盤價站上 KC 中軌才平倉 (杜絕下影線誤判)
        if closed_bar['close'] > kc_mid:
            print(f"[{datetime.now()}] 【空單平倉】已收線 K 棒實體站上 KC 中軌 (Close: {closed_bar['close']:.4f} > Mid: {kc_mid:.4f})")
            return True

        # 條件 (B)：影線唯一平倉例外 ——「帶有利潤時的移動止盈」
        if has_profit:
            lock_profit_price = entry_price * (1.0 - min_profit_pct * 0.5)
            if current_price >= lock_profit_price:
                print(f"[{datetime.now()}] 【空單止盈】帶有利潤 ({profit_ratio*100:.2f}%)，觸發動態止盈價 {lock_profit_price:.4f}")
                return True
        else:
            # 無利潤或虧損時，下影線反抽一律不平倉
            return False

    # --- 多單出場 ---
    elif position == 1:
        # 條件 (A)：嚴格等待收線，實體收盤價跌破 KC 中軌才平倉 (杜絕上影線誤判)
        if closed_bar['close'] < kc_mid:
            print(f"[{datetime.now()}] 【多單平倉】已收線 K 棒實體跌破 KC 中軌 (Close: {closed_bar['close']:.4f} < Mid: {kc_mid:.4f})")
            return True

        # 條件 (B)：影線唯一平倉例外 ——「帶有利潤時的移動止盈」
        if has_profit:
            lock_profit_price = entry_price * (1.0 + min_profit_pct * 0.5)
            if current_price <= lock_profit_price:
                print(f"[{datetime.now()}] 【多單止盈】帶有利潤 ({profit_ratio*100:.2f}%)，觸發動態止盈價 {lock_profit_price:.4f}")
                return True
        else:
            return False

    return False

# ==============================================================================
# 5. Gate.io 執行層 (Exchange Executor)
# ==============================================================================
class GateExecutor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.exchange = ccxt.gate(cfg['gate'])

    def get_position(self, symbol: str) -> dict:
        res = {"direction": 0, "size": 0, "entry_price": 0.0}
        try:
            positions = self.exchange.fetch_positions([symbol])
            for pos in positions:
                size = float(pos.get('contracts', 0) or pos.get('size', 0))
                if size > 0:
                    side = str(pos.get('side', '')).lower()
                    res["direction"] = 1 if side == 'long' else -1
                    res["size"] = size
                    res["entry_price"] = float(pos.get('entryPrice', 0))
                    break
        except Exception as e:
            print(f"[{datetime.now()}] 查詢持倉異常: {e}")
        return res

    def execute_order(self, symbol: str, side: str, amount: float, reduce_only: bool = False):
        try:
            params = {}
            if reduce_only:
                params['reduceOnly'] = True
            order = self.exchange.create_order(
                symbol=symbol,
                type='market',
                side=side,
                amount=amount,
                params=params
            )
            print(f">>> Gate.io 送單成功 | 方向: {side.upper()} | 數量: {amount} 手 | ReduceOnly: {reduce_only} | 委託編號: {order['id']} <<<")
            return order
        except Exception as e:
            print(f"[{datetime.now()}] Gate.io 下單失敗: {e}")
            return None

    def fetch_market_data(self):
        ohlcv = self.exchange.fetch_ohlcv(self.cfg['symbol'], self.cfg['timeframe'], limit=60)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return calculate_indicators(df)

# ==============================================================================
# 6. 主循環引擎 (Main Cycle)
# ==============================================================================
def run_gate_bot_cycle(executor: GateExecutor, cfg: dict):
    symbol = cfg['symbol']
    df = executor.fetch_market_data()
    ticker = executor.exchange.fetch_ticker(symbol)
    current_price = float(ticker['last'])

    pos_info = executor.get_position(symbol)
    current_pos = pos_info["direction"]
    pos_size = pos_info["size"] if pos_info["size"] > 0 else cfg['trade_amount']
    entry_price = pos_info["entry_price"]
    last_bar = df.iloc[-1]

    # A. 持倉狀態管理 (影線不平倉除非有利潤 + 實體反向破中軌)
    if current_pos != 0:
        should_exit = check_exit_signals(
            df=df,
            current_price=current_price,
            position=current_pos,
            entry_price=entry_price,
            min_profit_pct=cfg['min_profit_pct']
        )
        if should_exit:
            order_side = 'buy' if current_pos == -1 else 'sell'
            executor.execute_order(symbol, order_side, pos_size, reduce_only=True)
        return

    # B. 未持倉狀態：評估開倉信號
    sig = check_all_entry_signals(df, current_price, current_pos)
    if sig["action"]:
        direction = sig["action"]
        entry_type = sig["type"]

        # 即時破軌二次校驗 (防止毛刺縮回)
        if entry_type == "INSTANT_BREAK":
            if direction == "SHORT" and current_price >= last_bar['kc_lower']:
                print("即時破軌已縮回軌內，取消開空送單。")
                return
            if direction == "LONG" and current_price <= last_bar['kc_upper']:
                print("即時破軌已縮回軌內，取消開多送單。")
                return

        print(f"[{datetime.now()}] 觸發開倉訊號: 方向={direction}, 模式={entry_type}, 最新報價={current_price}")
        order_side = 'buy' if direction == 'LONG' else 'sell'
        executor.execute_order(symbol, order_side, cfg['trade_amount'], reduce_only=False)

def main():
    print(f"=== Gate.io KC 量化合約策略啟動 ===")
    executor = GateExecutor(CONFIG)

    while True:
        try:
            run_gate_bot_cycle(executor, CONFIG)
        except Exception as e:
            print(f"[{datetime.now()}] 循環異常: {e}")
        time.sleep(CONFIG['poll_interval'])

if __name__ == '__main__':
    main()
