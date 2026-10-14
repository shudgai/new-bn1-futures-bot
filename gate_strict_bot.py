import time
import ccxt
import pandas as pd
import numpy as np

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
    'symbol': 'BTC/USDT:USDT',                    # 或 1000PEPE/USDT:USDT / 龙虾合約標的
    'timeframe': '5m',
    'order_amount': 1,                           # 下單張數 / 數量

    # === 四重震盪與走平過濾門檻 ===
    'min_ma5_slope': 0.0006,                     # MA5 斜率門檻 (0.06%)，低於此值視為走平
    'min_ma_separation': 0.0015,                 # MA5 與 MA15 最小間距 (0.15%)，低於此值視為重合
    'min_kc_slope': 0.0004,                      # KC 中軌最小傾斜度 (0.04%)，低於此值視為水平平行
    'min_kc_bandwidth': 0.008,                   # KC 帶寬門檻 (0.8%)，低於此值視為震盪重整
    'atr_tolerance': 0.5,                        # 偏離頂底容忍度 (0.5 * ATR)，衝高/跌破不開

    # === 出場與風控門檻 ===
    'fee_pct': 0.0012,                           # 雙邊進出手續費預估 (0.12%)
    'hard_stop_pct': 0.015,                      # 絕對風控硬停損 (1.5%)
    'same_bar_tp_pct': 0.008,                    # 當根平倉利潤門檻 (0.8%)
    'cooldown_bars': 3,                          # 平倉後冷卻 K 棒數 (防止同區域連環刷單)

    # === 淨利棘輪鎖利 (2% 齒輪) ===
    'ratchet_trigger_pct': 0.10,                 # 淨利達 10% 啟動鎖利
    'ratchet_step_pct': 0.02,                    # 每階段推進 2% 鎖利
    'ratchet_pullback_pct': 0.20,                # 最高淨利回吐 20% 平倉

    'poll_interval': 10,                         # 輪詢間隔 (秒)
}

# ==============================================================================
# 2. 持倉狀態機 (State Machine)
# ==============================================================================
class PositionState:
    def __init__(self):
        self.side = None                         # 'LONG', 'SHORT', None
        self.entry_price = 0.0
        self.entry_bar_time = None
        self.cooldown_until_time = 0             # 冷卻截止時間戳
        
        # 淨利鎖利狀態
        self.max_net_pnl = 0.0
        self.ladder_locked_pnl = 0.0
        self.ratchet_active = False

    @property
    def has_position(self):
        return self.side is not None

    def open(self, side, price, bar_time):
        self.side = side
        self.entry_price = price
        self.entry_bar_time = bar_time
        self.max_net_pnl = 0.0
        self.ladder_locked_pnl = 0.0
        self.ratchet_active = False

    def close(self, current_bar_time, cooldown_seconds):
        self.side = None
        self.entry_price = 0.0
        self.entry_bar_time = None
        self.cooldown_until_time = current_bar_time + cooldown_seconds
        self.max_net_pnl = 0.0
        self.ladder_locked_pnl = 0.0
        self.ratchet_active = False

# ==============================================================================
# 3. 核心決策層 (Strategy Evaluator - 扁平無巢狀)
# ==============================================================================
class StrategyEvaluator:
    def __init__(self, cfg):
        self.cfg = cfg

    # --------------------------------------------------------------------------
    # 進場檢核：四重防禦 (走平不開、重合不開、平行不開、收縮不開) + 頂底過濾
    # --------------------------------------------------------------------------
    def evaluate_entry(self, closed_bar, prev_closed_bar, state: PositionState, current_time):
        if state.has_position:
            return None

        # 0. 處於平倉冷卻期 -> 嚴格拒絕開倉
        if current_time < state.cooldown_until_time:
            return None

        price = closed_bar['close']
        atr = closed_bar['atr']
        max_dist = self.cfg['atr_tolerance'] * atr

        # 指標取值
        ma5_now = closed_bar['ma5']
        ma5_prev = prev_closed_bar['ma5']
        ma15_now = closed_bar['ma15']
        kc_mid_now = closed_bar['kc_middle']
        kc_mid_prev = prev_closed_bar['kc_middle']

        # === 防禦 1：MA5 走平檢核 (斜率未達標準不開) ===
        ma5_slope = (ma5_now - ma5_prev) / price
        is_ma5_up = ma5_slope > self.cfg['min_ma5_slope']
        is_ma5_down = ma5_slope < -self.cfg['min_ma5_slope']
        if not (is_ma5_up or is_ma5_down):
            return None  # MA5 走平，不開

        # === 防禦 2：MA5 與 MA15 重合檢核 (兩線黏合不開) ===
        ma_sep = abs(ma5_now - ma15_now) / price
        if ma_sep < self.cfg['min_ma_separation']:
            return None  # 均線糾纏重合，不開

        # === 防禦 3：KC 線平行檢核 (通道水平無方向不開) ===
        kc_slope = (kc_mid_now - kc_mid_prev) / price
        is_kc_up = kc_slope > self.cfg['min_kc_slope']
        is_kc_down = kc_slope < -self.cfg['min_kc_slope']
        if not (is_kc_up or is_kc_down):
            return None  # KC 線水平平行，不開

        # === 防禦 4：震盪重整檢核 (帶寬收縮不開) ===
        kc_bandwidth = (closed_bar['kc_upper'] - closed_bar['kc_lower']) / price
        if kc_bandwidth < self.cfg['min_kc_bandwidth']:
            return None  # 震盪重整區，不開

        # === 多單開倉判定 (MA5向上 + KC向上 + 只開在底) ===
        if (ma5_now > ma15_now) and is_ma5_up and is_kc_up:
            # 衝上去不開：偏離下軌過多
            if (price - closed_bar['kc_lower']) > max_dist:
                return None
            # 破底長黑不接飛刀
            if price < closed_bar['kc_lower'] and closed_bar['close'] < closed_bar['open']:
                return None
            if abs(price - closed_bar['kc_lower']) <= max_dist and closed_bar['close'] >= closed_bar['open']:
                return 'OPEN_LONG'

        # === 空單開倉判定 (MA5向下 + KC向下 + 只開在頂) ===
        if (ma5_now < ma15_now) and is_ma5_down and is_kc_down:
            # 跌下去不開：偏離上軌過多
            if (closed_bar['kc_upper'] - price) > max_dist:
                return None
            # 長紅暴拉不摸頂
            if price > closed_bar['kc_upper'] and closed_bar['close'] > closed_bar['open']:
                return None
            if abs(closed_bar['kc_upper'] - price) <= max_dist and closed_bar['close'] <= closed_bar['open']:
                return 'OPEN_SHORT'

        return None

    # --------------------------------------------------------------------------
    # 出場檢核：硬停損 + 同根防洗 + 淨利 10% 鎖利 + 趨勢護航否決 (未破 MA15 嚴禁平倉)
    # --------------------------------------------------------------------------
    def evaluate_exit(self, closed_bar, realtime_price, realtime_bar_time, state: PositionState):
        if not state.has_position:
            return None

        # 計算淨利比例
        if state.side == 'LONG':
            gross_pnl = (realtime_price - state.entry_price) / state.entry_price
        else:
            gross_pnl = (state.entry_price - realtime_price) / state.entry_price
        net_pnl = gross_pnl - self.cfg['fee_pct']

        # === 關卡 1：絕對硬停損 (即時風控) ===
        if net_pnl <= -self.cfg['hard_stop_pct']:
            return 'EXIT_HARD_STOP'

        # === 關卡 2：當根 K 棒防洗鎖定 ===
        if realtime_bar_time == state.entry_bar_time:
            if net_pnl >= self.cfg['same_bar_tp_pct']:
                return 'EXIT_SAME_BAR_TP'
            return None  # 當根未獲利達標，其餘平倉一律封鎖

        # === 關卡 3：淨利 10% 棘輪鎖利 (每階 2%、回吐 20%) ===
        if net_pnl > state.max_net_pnl:
            state.max_net_pnl = net_pnl

        if state.max_net_pnl >= self.cfg['ratchet_trigger_pct']:
            state.ratchet_active = True
            steps = int((state.max_net_pnl - self.cfg['ratchet_trigger_pct']) / self.cfg['ratchet_step_pct'])
            step_lock = self.cfg['ratchet_step_pct'] * (1 + steps)
            if step_lock > state.ladder_locked_pnl:
                state.ladder_locked_pnl = step_lock

            pullback_line = state.max_net_pnl * (1.0 - self.cfg['ratchet_pullback_pct'])
            final_stop = max(state.ladder_locked_pnl, pullback_line)

            if net_pnl <= final_stop:
                print(f"[鎖利觸發] 峰值: {state.max_net_pnl*100:.2f}%, 防線: {final_stop*100:.2f}%, 現值: {net_pnl*100:.2f}%")
                return 'EXIT_NET_PROFIT_RATCHET'

        # === 關卡 4：單邊趨勢全程護航閘門 (一票否決權 VETO) ===
        # 嚴格規則：多單未跌破 MA15、空單未站上 MA15，100% 駁回常規平倉
        if state.side == 'LONG':
            if closed_bar['close'] >= closed_bar['ma15']:
                return None  # 趨勢完好，強制死抱
            if closed_bar['close'] < closed_bar['ma15']:
                return 'EXIT_TREND_BROKEN'

        elif state.side == 'SHORT':
            if closed_bar['close'] <= closed_bar['ma15']:
                return None  # 趨勢完好，強制死抱
            if closed_bar['close'] > closed_bar['ma15']:
                return 'EXIT_TREND_BROKEN'

        return None

# ==============================================================================
# 4. Gate.io 執行層 (Exchange Executor)
# ==============================================================================
class GateExecutor:
    def __init__(self, cfg):
        self.cfg = cfg
        self.exchange = ccxt.gate(cfg['gate'])

    def fetch_market_data(self):
        ohlcv = self.exchange.fetch_ohlcv(self.cfg['symbol'], self.cfg['timeframe'], limit=60)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        # 均線 MA5 與 MA15
        df['ma5'] = df['close'].rolling(5).mean()
        df['ma15'] = df['close'].rolling(15).mean()

        # ATR (14)
        hl = df['high'] - df['low']
        hc = (df['high'] - df['close'].shift()).abs()
        lc = (df['low'] - df['close'].shift()).abs()
        df['atr'] = pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(14).mean()

        # KC 肯特納通道 (20 EMA + 2 * ATR)
        df['kc_middle'] = df['close'].ewm(span=20, adjust=False).mean()
        df['kc_upper'] = df['kc_middle'] + (df['atr'] * 2.0)
        df['kc_lower'] = df['kc_middle'] - (df['atr'] * 2.0)

        return df

    def execute_order(self, action, price, bar_time, state: PositionState):
        symbol = self.cfg['symbol']
        amount = self.cfg['order_amount']
        cooldown_sec = self.cfg['cooldown_bars'] * 300  # 5m = 300秒

        try:
            if action == 'OPEN_LONG':
                print(f"[{time.strftime('%X')}] >>> 執行 Gate 市價開多: {price}")
                self.exchange.create_market_order(symbol, 'buy', amount)
                state.open('LONG', price, bar_time)

            elif action == 'OPEN_SHORT':
                print(f"[{time.strftime('%X')}] >>> 執行 Gate 市價開空: {price}")
                self.exchange.create_market_order(symbol, 'sell', amount)
                state.open('SHORT', price, bar_time)

            elif action.startswith('EXIT'):
                print(f"[{time.strftime('%X')}] >>> 執行 Gate 平倉 ({action}): {price}")
                close_side = 'sell' if state.side == 'LONG' else 'buy'
                self.exchange.create_market_order(symbol, close_side, amount, params={'reduceOnly': True})
                state.close(bar_time, cooldown_sec)

        except Exception as e:
            print(f"[{time.strftime('%X')}] [Gate 下單異常] {e}")

# ==============================================================================
# 5. 主調度引擎 (Main Loop)
# ==============================================================================
def main():
    state = PositionState()
    evaluator = StrategyEvaluator(CONFIG)
    executor = GateExecutor(CONFIG)

    print("Gate.io 嚴格趨勢進場與持倉護航系統已啟動...")

    while True:
        try:
            df = executor.fetch_market_data()
            
            # 【核心修正】：判斷一律採用已收盤定型 K 棒 (iloc[-2] 與 iloc[-3])
            closed_bar = df.iloc[-2]
            prev_closed_bar = df.iloc[-3]
            active_bar = df.iloc[-1]
            
            realtime_price = active_bar['close']
            realtime_time = active_bar['timestamp']

            # 1. 優先檢核出場 (淨利鎖利、未破 MA15 護航、硬停損)
            exit_action = evaluator.evaluate_exit(closed_bar, realtime_price, realtime_time, state)
            if exit_action:
                executor.execute_order(exit_action, realtime_price, realtime_time, state)

            # 2. 無持倉時檢核嚴格開倉 (MA5/KC走平、重合、平行、收縮 100% 拒絕)
            elif not state.has_position:
                entry_action = evaluator.evaluate_entry(closed_bar, prev_closed_bar, state, realtime_time)
                if entry_action:
                    executor.execute_order(entry_action, realtime_price, realtime_time, state)

            time.sleep(CONFIG['poll_interval'])

        except Exception as err:
            print(f"[{time.strftime('%X')}] [主循環錯誤] {err}")
            time.sleep(5)

if __name__ == '__main__':
    main()
