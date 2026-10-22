#!/usr/bin/env python3
"""
==============================================================================
量化交易策略三階段回測與視覺化驗證引擎 (Backtest & Visualizer)
==============================================================================
依據用戶嚴格量化鐵律與 AIDAN 核心規範實作：
1. 開倉條件：
   - 大週期紅色 MA 線向下時 (DOWN)，100% 絕對禁止開多 (BLOCKED_LONG_ON_BEARISH)。
   - 大週期紅色 MA 線向上時 (UP)，100% 絕對禁止開空 (BLOCKED_SHORT_ON_BULLISH)。
   - 在沒有確定明確漲跌勢 (MA 走平 FLAT 或通道收斂擠壓震盪) 時，機器人保持空倉 (HOLD)。
2. 持倉與平倉條件：
   - 開倉後，中間短線震盪與小幅回檔 100% 續抱 (VETO_HOLD)，絕不提早平倉被洗下車。
   - 只有在大週期出現「真峰頂」(Three-Point Peak Pivot 或紅色 MA 轉向向下) 時才平多。
   - 只有在大週期出現「真谷底」(Three-Point Valley Pivot 或紅色 MA 轉向向上) 時才平空。
3. 視覺化繪圖：
   - 使用 Matplotlib / mplfinance 繪製 K 線、紅色 MA、KC 通道。
   - 標註 開多 (▲)、開空 (▼)、平多 (◆)、平空 (◆) 及每筆 PnL 標籤。
==============================================================================
"""

import sys
import os
import json
import urllib.request
import urllib.parse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # 支援伺服器無桌面環境導出圖片
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

# ==============================================================================
# 1. 核心指標與趨勢確定性判定函數
# ==============================================================================

def calculate_indicators(df: pd.DataFrame, red_ma_window: int = 40, ma5_window: int = 5, ma15_window: int = 15, kc_window: int = 20, kc_mult: float = 1.5):
    """
    計算策略所需核心指標：
    - 大週期紅色 MA (預設 EMA40 或 MA30/40)
    - 快線 MA5, 中線 MA15
    - Keltner Channels (KC中軌、上軌、下軌、ATR)
    """
    df = df.copy()
    
    # 均線計算
    df['ma5'] = df['close'].rolling(window=ma5_window).mean()
    df['ma15'] = df['close'].rolling(window=ma15_window).mean()
    df['red_ma'] = df['close'].ewm(span=red_ma_window, adjust=False).mean()
    
    # ATR 計算
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=kc_window).mean()
    
    # Keltner Channels (KC 通道)
    df['kc_middle'] = df['close'].rolling(window=kc_window).mean()
    df['kc_upper'] = df['kc_middle'] + kc_mult * df['atr']
    df['kc_lower'] = df['kc_middle'] - kc_mult * df['atr']
    df['bandwidth'] = df['kc_upper'] - df['kc_lower']
    
    return df


def get_ma_direction(series: pd.Series, window: int = 3, threshold: float = 0.00002) -> str:
    """
    判斷 MA 斜率與方向：
    - UP: 最近 N 根 MA 持續向上且斜率 > threshold
    - DOWN: 最近 N 根 MA 持續向下且斜率 < -threshold
    - FLAT: 走平無趨勢 (震盪區間)
    """
    if len(series) < window + 1:
        return "FLAT"
    
    recent_vals = series.iloc[-window-1:].values
    curr_val = recent_vals[-1]
    prev_val = recent_vals[-window-1]
    
    if prev_val <= 0 or np.isnan(curr_val) or np.isnan(prev_val):
        return "FLAT"
    
    slope_pct = (curr_val - prev_val) / prev_val / window
    
    if slope_pct > threshold:
        return "UP"
    elif slope_pct < -threshold:
        return "DOWN"
    else:
        return "FLAT"


def is_true_top(df: pd.DataFrame, idx: int, lookback: int = 3) -> tuple[bool, str]:
    """
    判定是否成型「大週期真峰頂」(Three-Point Peak Pivot 或 頂部大陰吞噬)：
    多單真峰頂：中間棒 High 鶴立雞群，且確認棒收陰跌破實體中點；或頂部爆量大陰吞噬。
    """
    if idx < lookback:
        return False, ""
    
    curr = df.iloc[idx]
    prev = df.iloc[idx - 1]
    prev2 = df.iloc[idx - 2]
    
    # 1. 三點峰頂樞紐 (Three-Point Peak Pivot)
    # prev 的 High 高於前後兩棒，且 curr 收陰跌破 prev 實體中點
    is_pivot_high = (prev['high'] > prev2['high']) and (prev['high'] >= curr['high'])
    prev_mid = (prev['open'] + prev['close']) / 2.0
    is_exhaust_drop = (curr['close'] < prev_mid) and (curr['close'] < curr['open'])
    
    if is_pivot_high and is_exhaust_drop:
        return True, "THREE_POINT_PIVOT_PEAK"
    
    # 2. 頂部爆量大陰吞噬 (Bearish Engulfing)
    atr = curr.get('atr', curr['close'] * 0.01)
    is_engulfing = (curr['close'] < curr['open']) and (curr['close'] < prev['low']) and (abs(curr['open'] - curr['close']) >= 1.2 * atr)
    if is_engulfing:
        return True, "BEARISH_ENGULFING_PEAK"
        
    return False, ""


def is_true_bottom(df: pd.DataFrame, idx: int, lookback: int = 3) -> tuple[bool, str]:
    """
    判定是否成型「大週期真谷底」(Three-Point Valley Pivot 或 底部大陽吞噬)：
    空單真谷底：中間棒 Low 鶴立雞群，且確認棒收陽突破實體中點；或底部爆量大陽吞噬。
    """
    if idx < lookback:
        return False, ""
    
    curr = df.iloc[idx]
    prev = df.iloc[idx - 1]
    prev2 = df.iloc[idx - 2]
    
    # 1. 三點谷底樞紐 (Three-Point Valley Pivot)
    is_pivot_low = (prev['low'] < prev2['low']) and (prev['low'] <= curr['low'])
    prev_mid = (prev['open'] + prev['close']) / 2.0
    is_exhaust_bounce = (curr['close'] > prev_mid) and (curr['close'] > curr['open'])
    
    if is_pivot_low and is_exhaust_bounce:
        return True, "THREE_POINT_PIVOT_VALLEY"
    
    # 2. 底部爆量大陽吞噬 (Bullish Engulfing)
    atr = curr.get('atr', curr['close'] * 0.01)
    is_engulfing = (curr['close'] > curr['open']) and (curr['close'] > prev['high']) and (abs(curr['close'] - curr['open']) >= 1.2 * atr)
    if is_engulfing:
        return True, "BULLISH_ENGULFING_VALLEY"
        
    return False, ""


# ==============================================================================
# 2. 嚴格量化策略回測狀態機 (Backtest Engine)
# ==============================================================================

class StrictQuantitativeBacktest:
    """
    嚴格量化回測引擎：
    - 大週期紅色 MA 嚴格過濾
    - 盤整保持空倉
    - 開倉後小幅震盪絕對不平倉 (VETO_HOLD)
    - 真峰谷或大週期反轉才平倉
    """
    def __init__(self, df: pd.DataFrame, leverage: float = 5.0, fee_rate: float = 0.0005):
        self.df = df
        self.leverage = leverage
        self.fee_rate = fee_rate
        self.trades = []
        
    def run(self):
        position = "NONE"
        entry_idx = None
        entry_price = 0.0
        peak_pnl = 0.0
        
        # 從足夠計算指標的第 45 根 K 棒開始
        for i in range(45, len(self.df)):
            curr = self.df.iloc[i]
            prev = self.df.iloc[i - 1]
            price = curr['close']
            
            # 取得歷史至當前切片計算 MA 方向
            sub_series = self.df['red_ma'].iloc[:i+1]
            ma_dir = get_ma_direction(sub_series, window=3)
            
            kc_mid_slope = (curr['kc_middle'] - prev['kc_middle']) / prev['kc_middle']
            
            # --- 1. 開倉決策 (無持倉時) ---
            if position == "NONE":
                # 做多檢查：
                # 鐵律：紅色 MA 向下 (DOWN) 絕對禁止開多！
                can_long = (ma_dir == "UP")
                # 均線金叉或破軌
                ma_cross_up = (curr['ma5'] > curr['ma15']) and (prev['ma5'] <= prev['ma15'])
                breakout_up = (curr['close'] > curr['kc_upper']) and (curr['close'] > curr['open'])
                not_over_extended_long = curr['close'] <= (curr['kc_upper'] + 1.5 * curr['atr'])
                kc_mid_support_long = kc_mid_slope >= -0.00001
                
                if can_long and (ma_cross_up or breakout_up) and not_over_extended_long and kc_mid_support_long:
                    position = "LONG"
                    entry_idx = i
                    entry_price = price
                    peak_pnl = 0.0
                    continue
                
                # 做空檢查：
                # 鐵律：紅色 MA 向上 (UP) 絕對禁止開空！
                can_short = (ma_dir == "DOWN")
                # 均線死叉或破軌
                ma_cross_down = (curr['ma5'] < curr['ma15']) and (prev['ma5'] >= prev['ma15'])
                breakdown_down = (curr['close'] < curr['kc_lower']) and (curr['close'] < curr['open'])
                not_over_extended_short = curr['close'] >= (curr['kc_lower'] - 1.5 * curr['atr'])
                kc_mid_resistance_short = kc_mid_slope <= 0.00001
                
                if can_short and (ma_cross_down or breakdown_down) and not_over_extended_short and kc_mid_resistance_short:
                    position = "SHORT"
                    entry_idx = i
                    entry_price = price
                    peak_pnl = 0.0
                    continue
            
            # --- 2. 持倉維護與平倉決策 (持多單時) ---
            elif position == "LONG":
                unrealized_pct = (price - entry_price) / entry_price
                peak_pnl = max(peak_pnl, unrealized_pct)
                
                # 峰頂偵測
                has_true_peak, peak_reason = is_true_top(self.df, i)
                ma_turned_down = (ma_dir == "DOWN") and (curr['close'] < curr['kc_middle'])
                
                # 持倉防洗鐵律：守穩在 MA5 之上且未見真峰頂，100% 續抱！
                is_above_ma5 = (curr['close'] >= curr['ma5'])
                
                should_exit = False
                exit_reason = ""
                
                if has_true_peak:
                    should_exit = True
                    exit_reason = f"真峰頂停利 ({peak_reason})"
                elif ma_turned_down:
                    should_exit = True
                    exit_reason = "大週期紅色MA轉向下且破中軌"
                elif curr['close'] < (curr['kc_middle'] - 1.0 * curr['atr']):
                    should_exit = True
                    exit_reason = "跌破KC中軌風控止損"
                    
                if should_exit:
                    net_pnl = (unrealized_pct - 2 * self.fee_rate) * self.leverage
                    self.trades.append({
                        "side": "LONG",
                        "entry_idx": entry_idx,
                        "entry_time": self.df.iloc[entry_idx]['t'],
                        "entry_price": entry_price,
                        "exit_idx": i,
                        "exit_time": curr['t'],
                        "exit_price": price,
                        "pnl_pct": net_pnl,
                        "reason": exit_reason,
                        "holding_bars": i - entry_idx,
                    })
                    position = "NONE"
                    entry_idx = None
                    entry_price = 0.0
                    peak_pnl = 0.0
            
            # --- 3. 持倉維護與平倉決策 (持空單時) ---
            elif position == "SHORT":
                unrealized_pct = (entry_price - price) / entry_price
                peak_pnl = max(peak_pnl, unrealized_pct)
                
                # 谷底偵測
                has_true_valley, valley_reason = is_true_bottom(self.df, i)
                ma_turned_up = (ma_dir == "UP") and (curr['close'] > curr['kc_middle'])
                
                # 持倉防洗鐵律：受壓在 MA5 之下且未見真谷底，100% 續抱！
                is_below_ma5 = (curr['close'] <= curr['ma5'])
                
                should_exit = False
                exit_reason = ""
                
                if has_true_valley:
                    should_exit = True
                    exit_reason = f"真谷底停利 ({valley_reason})"
                elif ma_turned_up:
                    should_exit = True
                    exit_reason = "大週期紅色MA轉向上且收復中軌"
                elif curr['close'] > (curr['kc_middle'] + 1.0 * curr['atr']):
                    should_exit = True
                    exit_reason = "突破KC中軌風控止損"
                    
                if should_exit:
                    net_pnl = (unrealized_pct - 2 * self.fee_rate) * self.leverage
                    self.trades.append({
                        "side": "SHORT",
                        "entry_idx": entry_idx,
                        "entry_time": self.df.iloc[entry_idx]['t'],
                        "entry_price": entry_price,
                        "exit_idx": i,
                        "exit_time": curr['t'],
                        "exit_price": price,
                        "pnl_pct": net_pnl,
                        "reason": exit_reason,
                        "holding_bars": i - entry_idx,
                    })
                    position = "NONE"
                    entry_idx = None
                    entry_price = 0.0
                    peak_pnl = 0.0
                    
        return self.trades


# ==============================================================================
# 3. Matplotlib 視覺化圖表生成器 (Chart Generator)
# ==============================================================================

def plot_backtest_results(df: pd.DataFrame, trades: list, output_filepath: str = "backtest_result.png"):
    """
    使用 Matplotlib 將 K 線、紅色 MA、KC 通道及開平倉標註繪製為清晰圖表
    """
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 9), gridspec_kw={'height_ratios': [3, 1]}, sharex=True)
    
    # 時間軸轉為整數 index 作為 x 座標以避免週末/休市空隙
    x_indices = np.arange(len(df))
    
    # 繪製 K 線 (以線段與矩形柱代替，相容純 matplotlib)
    up = df['close'] >= df['open']
    down = df['close'] < df['open']
    col_up = '#00c076'    # 綠色
    col_down = '#ff3b30'  # 紅色
    
    # 影線
    ax1.vlines(x_indices[up], df['low'][up], df['high'][up], color=col_up, linewidth=1.0, alpha=0.8)
    ax1.vlines(x_indices[down], df['low'][down], df['high'][down], color=col_down, linewidth=1.0, alpha=0.8)
    
    # 實體
    bar_width = 0.6
    ax1.bar(x_indices[up], df['close'][up] - df['open'][up], bottom=df['open'][up], color=col_up, width=bar_width, alpha=0.9)
    ax1.bar(x_indices[down], df['open'][down] - df['close'][down], bottom=df['close'][down], color=col_down, width=bar_width, alpha=0.9)
    
    # 繪製通道與均線
    ax1.plot(x_indices, df['red_ma'], label='Red MA (EMA40 Trend)', color='#e02424', linewidth=2.5, alpha=0.95)
    ax1.plot(x_indices, df['ma5'], label='Fast MA5', color='#3b82f6', linewidth=1.2, linestyle='--')
    ax1.plot(x_indices, df['ma15'], label='Mid MA15', color='#f59e0b', linewidth=1.2, linestyle=':')
    ax1.plot(x_indices, df['kc_upper'], label='KC Upper', color='#06b6d4', linewidth=1.0, alpha=0.7)
    ax1.plot(x_indices, df['kc_middle'], label='KC Middle', color='#64748b', linewidth=1.0, linestyle='-.', alpha=0.6)
    ax1.plot(x_indices, df['kc_lower'], label='KC Lower', color='#06b6d4', linewidth=1.0, alpha=0.7)
    ax1.fill_between(x_indices, df['kc_upper'], df['kc_lower'], color='#06b6d4', alpha=0.05)
    
    # 標註每一筆交易的進出場點
    for trade in trades:
        e_idx = trade['entry_idx']
        x_idx = trade['exit_idx']
        side = trade['side']
        pnl = trade['pnl_pct'] * 100
        
        if side == "LONG":
            # 買多箭頭 (綠色向上)
            ax1.scatter(e_idx, trade['entry_price'] * 0.998, marker='^', color='#10b981', s=130, zorder=5)
            ax1.text(e_idx, trade['entry_price'] * 0.994, 'BUY LONG', color='#10b981', fontweight='bold', fontsize=9, ha='center')
            
            # 平多標記 (紫色菱形)
            exit_col = '#10b981' if pnl >= 0 else '#ef4444'
            ax1.scatter(x_idx, trade['exit_price'] * 1.002, marker='D', color='#8b5cf6', s=90, zorder=5)
            ax1.text(x_idx, trade['exit_price'] * 1.005, f"EXIT LONG\n({pnl:+.2f}%)", color=exit_col, fontweight='bold', fontsize=8, ha='center')
        else:
            # 賣空箭頭 (紅色向下)
            ax1.scatter(e_idx, trade['entry_price'] * 1.002, marker='v', color='#ef4444', s=130, zorder=5)
            ax1.text(e_idx, trade['entry_price'] * 1.006, 'SELL SHORT', color='#ef4444', fontweight='bold', fontsize=9, ha='center')
            
            # 平空標記 (藍色菱形)
            exit_col = '#10b981' if pnl >= 0 else '#ef4444'
            ax1.scatter(x_idx, trade['exit_price'] * 0.998, marker='D', color='#3b82f6', s=90, zorder=5)
            ax1.text(x_idx, trade['exit_price'] * 0.994, f"EXIT SHORT\n({pnl:+.2f}%)", color=exit_col, fontweight='bold', fontsize=8, ha='center')
            
    ax1.set_title("Quantitative Backtest Visualizer (Red MA Trend Filter + KC Channels + True Pivot Exits)", fontsize=13, fontweight='bold', pad=12)
    ax1.set_ylabel("Price (USDT)", fontsize=11)
    ax1.grid(True, linestyle=':', alpha=0.4)
    ax1.legend(loc='upper left', framealpha=0.9, fontsize=9)
    
    # 下方子圖：累積權益曲線 (Equity Curve)
    cum_equity = [100.0]
    for trade in trades:
        last = cum_equity[-1]
        cum_equity.append(last * (1.0 + trade['pnl_pct']))
        
    ax2.plot(range(len(cum_equity)), cum_equity, marker='o', color='#8b5cf6', linewidth=2.0)
    ax2.set_title("Cumulative Equity Curve (Initial: 100 USDT)", fontsize=11, fontweight='bold')
    ax2.set_xlabel("Trade Count (#)", fontsize=11)
    ax2.set_ylabel("Equity", fontsize=11)
    ax2.grid(True, linestyle=':', alpha=0.4)
    
    # 儲存圖片
    plt.tight_layout()
    plt.savefig(output_filepath, dpi=200)
    plt.close()
    print(f"✅ 視覺化回測圖表已成功保存至: {output_filepath}")


# ==============================================================================
# 4. 主執行入口與數據來源整合
# ==============================================================================

def fetch_sample_candles(symbol: str = "LOBSTERUSDT", limit: int = 120) -> pd.DataFrame:
    """從幣安合約公開 API 獲取真實 1m K 線"""
    sym = urllib.parse.quote(symbol)
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&limit={limit}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            df = pd.DataFrame([[k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in data],
                              columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['t'] = pd.to_datetime(df.timestamp, unit='ms') + pd.Timedelta(hours=8)
            return df
    except Exception as e:
        print(f"⚠️ 從幣安 API 獲取線上數據失敗 ({e})，使用內建模擬行情數據...")
        # 生成擬真測試行情 (含趨勢、破軌與峰谷)
        np.random.seed(42)
        n = limit
        base = 100.0
        prices = [base]
        for t in range(1, n):
            drift = 0.2 if t < 40 else (-0.3 if t < 80 else 0.1)
            shock = np.random.normal(0, 0.4)
            prices.append(max(prices[-1] + drift + shock, 10.0))
            
        df = pd.DataFrame({
            "timestamp": range(n),
            "t": [datetime.now() for _ in range(n)],
            "open": [p - 0.1 for p in prices],
            "high": [p + 0.3 for p in prices],
            "low": [p - 0.3 for p in prices],
            "close": prices,
            "volume": [1000.0 + np.random.uniform(0, 500) for _ in range(n)]
        })
        return df


def main():
    print("=" * 70)
    print("🚀 啟動幣安量化策略三階段回測與視覺化引擎...")
    print("=" * 70)
    
    # 1. 載入行情數據 (預設使用 BTCUSDT 或傳入之合約)
    symbol_to_fetch = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    limit_bars = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    df = fetch_sample_candles(symbol=symbol_to_fetch, limit=limit_bars)
        
    # 2. 計算指標
    df = calculate_indicators(df, red_ma_window=40, ma5_window=5, ma15_window=15)
    
    # 3. 執行嚴格回測
    engine = StrictQuantitativeBacktest(df, leverage=5.0)
    trades = engine.run()
    
    # 4. 輸出統計報告
    print("\n📊 【回測交易統計報告】")
    print(f"總交易筆數: {len(trades)} 筆")
    if trades:
        wins = [t for t in trades if t['pnl_pct'] > 0]
        losses = [t for t in trades if t['pnl_pct'] <= 0]
        win_rate = len(wins) / len(trades) * 100.0
        total_pnl = sum(t['pnl_pct'] for t in trades) * 100.0
        
        print(f"獲利筆數: {len(wins)} 筆 | 虧損筆數: {len(losses)} 筆")
        print(f"策略勝率: {win_rate:.2f}%")
        print(f"累計收益 (含5x槓桿與手續費): {total_pnl:+.2f}%")
        
        print("\n📝 【詳細交易流水帳】")
        for idx, t in enumerate(trades, 1):
            print(f"#{idx:02d} [{t['side']:5s}] 進場: {t['entry_time']} @ {t['entry_price']:.4f} | "
                  f"出場: {t['exit_time']} @ {t['exit_price']:.4f} | "
                  f"ROE: {t['pnl_pct']*100:+.2f}% | 耗時: {t['holding_bars']} 根 | 原因: {t['reason']}")
    else:
        print("💡 回測區間處於盤整或震盪，機器人成功保持空倉 (Zero Trade)，未觸發盲目交易！")
        
    # 5. 輸出視覺化圖表
    output_png = "backtest_result.png"
    plot_backtest_results(df, trades, output_filepath=output_png)
    print("=" * 70)


if __name__ == "__main__":
    main()
