"""
backtest_compare.py - 幣安合約 3M KC + EMA5/EMA13 策略 A (中軌平倉) vs 策略 B (5大反轉出場) 對比回測腳本

【參數配置】
- 費率設定：單邊手續費 0.04%、單邊滑點 0.02% (雙邊開平共 0.12% 摩擦成本)
- KC 參數：中軌 EMA 20、ATR 週期 10、通道倍數 1.5
- 均線參數：EMA 5、EMA 13
- 策略 A：碰 KC 中軌才平倉 (傳統中軌保底平倉)
- 策略 B：五大谷底/頂部動能反轉平倉 (EMA5_TURN, REVERSAL_BAR, KC_RECLAIM, STRETCH_BOUNCE, NO_NEW_EXTREME)，中軌為最後保險
"""

import sys
import os
import math
import argparse
import urllib.request
import json
import pandas as pd
import numpy as np

# ── 1. 核心參數配置 (CFG) ──
CFG = {
    "kc_ema_period": 20,           # KC 中軌 EMA 週期
    "kc_atr_period": 10,           # KC ATR 週期
    "kc_mult": 1.5,                # KC 通道倍數
    "ema_fast": 5,                 # 快線 EMA5
    "ema_slow": 13,                # 慢線 EMA13
    
    # 摩擦成本 (依規則不得更改)
    "fee_rate": 0.0004,            # 單邊手續費 0.04%
    "slippage": 0.0002,            # 單邊滑點 0.02%
    
    # 策略 B 五大反轉參數
    "ema5_turn_bars": 2,           # EMA5 連續反向 2 根
    "reversal_bar_mult": 1.2,      # 反向 K 棒實體 >= 10 根均實體 1.2 倍
    "reversal_lookback": 10,       # 平均實體回溯 10 根
    "stretch_atr_mult": 2.5,       # 乖離過大門檻 2.5 * ATR
    "no_new_extreme_bars": 3,      # 連續 3 根未創新極值
    "reversal_min_profit_r": 1.0,  # 浮盈 >= 1R 啟用條件 4、5
}


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """計算 KC (EMA20, ATR10, 1.5x) 與 EMA5, EMA13"""
    df = df.copy()
    c = df['close']
    h = df['high']
    l = df['low']

    # 1. 均線
    df['ema5'] = c.ewm(span=CFG['ema_fast'], adjust=False).mean()
    df['ema13'] = c.ewm(span=CFG['ema_slow'], adjust=False).mean()
    df['kc_middle'] = c.ewm(span=CFG['kc_ema_period'], adjust=False).mean()

    # 2. ATR (True Range)
    prev_c = c.shift(1)
    tr = pd.concat([
        h - l,
        (h - prev_c).abs(),
        (l - prev_c).abs()
    ], axis=1).max(axis=1)
    df['atr'] = tr.rolling(CFG['kc_atr_period']).mean().bfill()

    # 3. KC 軌道
    df['kc_upper'] = df['kc_middle'] + CFG['kc_mult'] * df['atr']
    df['kc_lower'] = df['kc_middle'] - CFG['kc_mult'] * df['atr']

    # 4. K 棒實體與平均實體
    df['body'] = (c - df['open']).abs()
    df['avg_body'] = df['body'].rolling(CFG['reversal_lookback']).mean().bfill()
    return df


def simulate_trades(df: pd.DataFrame, strategy_type: str = 'B', disabled_conditions: set = None) -> list:
    """
    回測主迴圈：
    進場：EMA5/EMA13 交叉 或 破軌第 2 根同色
    strategy_type:
      'A' - 碰中軌平倉 (傳統中軌保底)
      'B' - 5 大動能反轉平倉 (中軌為備用保險)
    disabled_conditions: 在 B' 測試時可動態關閉指定條件
    """
    if disabled_conditions is None:
        disabled_conditions = set()

    trades = []
    in_pos = False
    side = None
    entry_idx = 0
    entry_price = 0.0
    initial_sl = 0.0
    risk_r = 0.0
    mfe = 0.0
    mae = 0.0
    extreme_price = 0.0

    n = len(df)
    for i in range(25, n):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        prev2 = df.iloc[i-2]

        c_close = row['close']
        c_open = row['open']
        c_high = row['high']
        c_low = row['low']
        c_ma5 = row['ema5']
        p_ma5 = prev['ema5']
        p2_ma5 = prev2['ema5']
        c_mid = row['kc_middle']
        c_upper = row['kc_upper']
        c_lower = row['kc_lower']
        p_upper = prev['kc_upper']
        p_lower = prev['kc_lower']
        c_atr = row['atr']
        avg_body = row['avg_body']

        # ── 1. 持倉更新與出場判定 ──
        if in_pos:
            # 追蹤 MFE 與 MAE
            if side == 'LONG':
                mfe = max(mfe, c_high - entry_price)
                mae = max(mae, entry_price - c_low)
                extreme_price = max(extreme_price, c_high)
                unrealized_pnl = c_close - entry_price
            else:
                mfe = max(mfe, entry_price - c_low)
                mae = max(mae, c_high - entry_price)
                extreme_price = min(extreme_price, c_low)
                unrealized_pnl = entry_price - c_close

            profit_r = unrealized_pnl / risk_r if risk_r > 0 else 0.0
            exit_reason = None

            # ── 策略 A：碰中軌才平倉 ──
            if strategy_type == 'A':
                if side == 'LONG' and c_close < c_mid:
                    exit_reason = 'KC_MIDDLE'
                elif side == 'SHORT' and c_close > c_mid:
                    exit_reason = 'KC_MIDDLE'

            # ── 策略 B* (完美進化版)：浮盈 >= 1R 啟用反轉，徹底禁用 REVERSAL_BAR ──
            elif strategy_type == 'B_STAR':
                is_reversal_allowed = (profit_r >= CFG['reversal_min_profit_r'])
                if side == 'LONG':
                    if is_reversal_allowed:
                        if (prev['close'] - prev['ema13']) > CFG['stretch_atr_mult'] * prev['atr'] and c_close < prev['close'] and c_close < c_open:
                            exit_reason = 'STRETCH_BOUNCE'
                        elif i - entry_idx >= 3 and df['high'].iloc[i-2:i+1].max() <= extreme_price and c_close < c_ma5:
                            exit_reason = 'NO_NEW_EXTREME'
                        elif prev['close'] > p_upper and c_close < c_upper and c_close < c_open:
                            exit_reason = 'KC_RECLAIM'
                        elif c_ma5 < p_ma5 < p2_ma5 and c_close < c_open:
                            exit_reason = 'EMA5_TURN'
                    if not exit_reason and c_close < c_mid:
                        exit_reason = 'KC_MIDDLE_SAFETY'
                elif side == 'SHORT':
                    if is_reversal_allowed:
                        if (prev['ema13'] - prev['close']) > CFG['stretch_atr_mult'] * prev['atr'] and c_close > prev['close'] and c_close > c_open:
                            exit_reason = 'STRETCH_BOUNCE'
                        elif i - entry_idx >= 3 and df['low'].iloc[i-2:i+1].min() >= extreme_price and c_close > c_ma5:
                            exit_reason = 'NO_NEW_EXTREME'
                        elif prev['close'] < p_lower and c_close > c_lower and c_close > c_open:
                            exit_reason = 'KC_RECLAIM'
                        elif c_ma5 > p_ma5 > p2_ma5 and c_close > c_open:
                            exit_reason = 'EMA5_TURN'
                    if not exit_reason and c_close > c_mid:
                        exit_reason = 'KC_MIDDLE_SAFETY'

            # ── 策略 B：五大谷底/頂部動能反轉出場 ──
            elif strategy_type in ('B', 'B_PRIME'):
                if side == 'LONG':
                    # 1. EMA5_TURN: 連續 2 根下降且當根收陰
                    if 'EMA5_TURN' not in disabled_conditions:
                        if c_ma5 < p_ma5 < p2_ma5 and c_close < c_open:
                            exit_reason = 'EMA5_TURN'
                    # 2. REVERSAL_BAR: 反向陰線實體 >= 1.2倍均實體且跌破前低
                    if not exit_reason and 'REVERSAL_BAR' not in disabled_conditions:
                        if c_close < c_open and (c_open - c_close) >= CFG['reversal_bar_mult'] * avg_body and c_close < prev['low']:
                            exit_reason = 'REVERSAL_BAR'
                    # 3. KC_RECLAIM: 前一根在上軌外，當根收回上軌內且收陰
                    if not exit_reason and 'KC_RECLAIM' not in disabled_conditions:
                        if prev['close'] > p_upper and c_close < c_upper and c_close < c_open:
                            exit_reason = 'KC_RECLAIM'
                    # 4. STRETCH_BOUNCE (需 profit_r >= 1.0)
                    if not exit_reason and 'STRETCH_BOUNCE' not in disabled_conditions:
                        if profit_r >= CFG['reversal_min_profit_r']:
                            if (prev['close'] - prev['ema13']) > CFG['stretch_atr_mult'] * prev['atr'] and c_close < prev['close'] and c_close < c_open:
                                exit_reason = 'STRETCH_BOUNCE'
                    # 5. NO_NEW_EXTREME (需 profit_r >= 1.0)
                    if not exit_reason and 'NO_NEW_EXTREME' not in disabled_conditions:
                        if profit_r >= CFG['reversal_min_profit_r'] and i - entry_idx >= 3:
                            recent_highs = df['high'].iloc[i-2:i+1]
                            if recent_highs.max() <= extreme_price and c_close < c_ma5:
                                exit_reason = 'NO_NEW_EXTREME'
                    # 6. 中軌最後保險
                    if not exit_reason and c_close < c_mid:
                        exit_reason = 'KC_MIDDLE_SAFETY'

                elif side == 'SHORT':
                    # 1. EMA5_TURN: 連續 2 根上升且當根收陽
                    if 'EMA5_TURN' not in disabled_conditions:
                        if c_ma5 > p_ma5 > p2_ma5 and c_close > c_open:
                            exit_reason = 'EMA5_TURN'
                    # 2. REVERSAL_BAR: 反向陽線實體 >= 1.2倍均實體且突破前高
                    if not exit_reason and 'REVERSAL_BAR' not in disabled_conditions:
                        if c_close > c_open and (c_close - c_open) >= CFG['reversal_bar_mult'] * avg_body and c_close > prev['high']:
                            exit_reason = 'REVERSAL_BAR'
                    # 3. KC_RECLAIM: 前一根在下軌外，當根收回下軌內且收陽
                    if not exit_reason and 'KC_RECLAIM' not in disabled_conditions:
                        if prev['close'] < p_lower and c_close > c_lower and c_close > c_open:
                            exit_reason = 'KC_RECLAIM'
                    # 4. STRETCH_BOUNCE (需 profit_r >= 1.0)
                    if not exit_reason and 'STRETCH_BOUNCE' not in disabled_conditions:
                        if profit_r >= CFG['reversal_min_profit_r']:
                            if (prev['ema13'] - prev['close']) > CFG['stretch_atr_mult'] * prev['atr'] and c_close > prev['close'] and c_close > c_open:
                                exit_reason = 'STRETCH_BOUNCE'
                    # 5. NO_NEW_EXTREME (需 profit_r >= 1.0)
                    if not exit_reason and 'NO_NEW_EXTREME' not in disabled_conditions:
                        if profit_r >= CFG['reversal_min_profit_r'] and i - entry_idx >= 3:
                            recent_lows = df['low'].iloc[i-2:i+1]
                            if recent_lows.min() >= extreme_price and c_close > c_ma5:
                                exit_reason = 'NO_NEW_EXTREME'
                    # 6. 中軌最後保險
                    if not exit_reason and c_close > c_mid:
                        exit_reason = 'KC_MIDDLE_SAFETY'

            # 執行平倉結算
            if exit_reason:
                # 扣除滑點：多單平倉賣出 價格乘 (1 - slippage)，空單平倉買入 乘 (1 + slippage)
                if side == 'LONG':
                    actual_exit_price = c_close * (1.0 - CFG['slippage'])
                    raw_pnl_pct = (actual_exit_price - entry_price) / entry_price
                else:
                    actual_exit_price = c_close * (1.0 + CFG['slippage'])
                    raw_pnl_pct = (entry_price - actual_exit_price) / entry_price

                # 扣除雙邊手續費
                net_pnl_pct = raw_pnl_pct - (CFG['fee_rate'] * 2)
                trade_r = ((actual_exit_price - entry_price) if side == 'LONG' else (entry_price - actual_exit_price)) / risk_r if risk_r > 0 else 0.0

                # 利潤回吐計算
                max_profit_pct = (mfe / entry_price) if entry_price > 0 else 0.0
                giveback_pct = max(0.0, max_profit_pct - net_pnl_pct) / max_profit_pct if max_profit_pct > 0.001 else 0.0

                trades.append({
                    "entry_idx": entry_idx,
                    "exit_idx": i,
                    "side": side,
                    "entry_price": entry_price,
                    "exit_price": actual_exit_price,
                    "exit_reason": exit_reason,
                    "raw_pnl_pct": raw_pnl_pct,
                    "net_pnl_pct": net_pnl_pct,
                    "r_multiple": trade_r,
                    "mfe": mfe,
                    "mae": mae,
                    "giveback_pct": giveback_pct,
                    "hold_bars": i - entry_idx,
                })
                in_pos = False
                continue

        # ── 2. 開倉判定 (EMA5/EMA13 交叉 OR 破軌第二根同色) ──
        if not in_pos:
            # 多單訊號
            long_ema_cross = (prev['ema5'] <= prev['ema13'] and c_ma5 > row['ema13'])
            long_kc_break = (prev['close'] > p_upper and c_close > c_upper and c_close > c_open)
            
            # 空單訊號
            short_ema_cross = (prev['ema5'] >= prev['ema13'] and c_ma5 < row['ema13'])
            short_kc_break = (prev['close'] < p_lower and c_close < c_lower and c_close < c_open)

            if long_ema_cross or long_kc_break:
                in_pos = True
                side = 'LONG'
                entry_idx = i
                # 進場滑點：買進乘 (1 + slippage)
                entry_price = c_close * (1.0 + CFG['slippage'])
                initial_sl = prev['low'] - 0.3 * c_atr
                risk_r = max(entry_price - initial_sl, c_atr * 0.8)
                mfe = 0.0
                mae = 0.0
                extreme_price = c_high
            elif short_ema_cross or short_kc_break:
                in_pos = True
                side = 'SHORT'
                entry_idx = i
                # 進場滑點：賣出乘 (1 - slippage)
                entry_price = c_close * (1.0 - CFG['slippage'])
                initial_sl = prev['high'] + 0.3 * c_atr
                risk_r = max(initial_sl - entry_price, c_atr * 0.8)
                mfe = 0.0
                mae = 0.0
                extreme_price = c_low

    return trades


def calculate_metrics(trades: list) -> dict:
    """計算回測核心指標"""
    if not trades:
        return {
            "count": 0, "net_pnl_pct": 0.0, "win_rate": 0.0,
            "profit_factor": 0.0, "mdd_pct": 0.0, "avg_r": 0.0, "giveback_pct": 0.0
        }

    df_t = pd.DataFrame(trades)
    count = len(df_t)
    net_pnls = df_t['net_pnl_pct']
    total_net_pnl = net_pnls.sum() * 100.0  # 百分比

    wins = df_t[df_t['net_pnl_pct'] > 0]
    losses = df_t[df_t['net_pnl_pct'] <= 0]
    win_rate = (len(wins) / count) * 100.0 if count > 0 else 0.0

    gross_profit = wins['net_pnl_pct'].sum()
    gross_loss = abs(losses['net_pnl_pct'].sum())
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

    # 計算權益曲線與 MDD
    equity_curve = (1.0 + net_pnls).cumprod()
    peak = equity_curve.cummax()
    drawdown = (equity_curve - peak) / peak
    mdd_pct = abs(drawdown.min()) * 100.0 if not drawdown.empty else 0.0

    avg_r = df_t['r_multiple'].mean()
    avg_giveback = df_t['giveback_pct'].mean() * 100.0

    return {
        "count": count,
        "net_pnl_pct": round(total_net_pnl, 2),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "mdd_pct": round(mdd_pct, 2),
        "avg_r": round(avg_r, 2),
        "giveback_pct": round(avg_giveback, 2),
    }


def fetch_binance_klines(symbol: str, interval: str = "3m", limit: int = 1000) -> pd.DataFrame:
    """從幣安合約公開 API 下載真實 K 線數據"""
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    
    cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore']
    df = pd.DataFrame(data, columns=cols)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    return df


def load_dataset(source: str) -> pd.DataFrame:
    """支援載入本地 CSV 或直接從幣安 API 抓取真實代幣數據"""
    if os.path.exists(source):
        df = pd.read_csv(source)
        # 欄位大小寫標準化
        df.columns = [c.lower() for c in df.columns]
        for req in ['open', 'high', 'low', 'close']:
            if req in df.columns:
                df[req] = df[req].astype(float)
        return df
    else:
        # 當作幣安 symbol 下載
        clean_symbol = source.replace('/', '').replace('_', '').upper()
        if not clean_symbol.endswith('USDT'):
            clean_symbol += 'USDT'
        print(f"[*] 正在從幣安下載 {clean_symbol} 3M 真實數據...")
        return fetch_binance_klines(clean_symbol, interval="3m", limit=1000)


def main():
    parser = argparse.ArgumentParser(description="KC + EMA5/13 策略 A vs B 回測比較")
    parser.add_argument("--files", nargs="+", default=["CAPUSDT", "1000LOBSTERUSDT", "BTCUSDT"], help="3 份資料檔案路徑或幣種名稱")
    args = parser.parse_args()

    files = args.files
    if len(files) < 3:
        files = files + ["BTCUSDT", "ETHUSDT"][:3 - len(files)]

    print("=" * 80)
    print("🚀 啟動回測比對：策略 A (中軌平倉) vs 策略 B (5大反轉出場)")
    print(f"費率設定: 單邊手續費 {CFG['fee_rate']*100:.2f}%, 單邊滑點 {CFG['slippage']*100:.2f}%")
    print(f"KC 設定: EMA {CFG['kc_ema_period']}, ATR {CFG['kc_atr_period']}, 倍數 {CFG['kc_mult']}")
    print("=" * 80)

    results_A = []
    results_B = []
    all_trades_B = []

    for f in files[:3]:
        df = load_dataset(f)
        df = calculate_indicators(df)

        trades_a = simulate_trades(df, strategy_type='A')
        trades_b = simulate_trades(df, strategy_type='B')

        for t in trades_b:
            t['dataset'] = f
        all_trades_B.extend(trades_b)

        res_a = calculate_metrics(trades_a)
        res_b = calculate_metrics(trades_b)

        res_a['dataset'] = f
        res_b['dataset'] = f
        results_A.append(res_a)
        results_B.append(res_b)

    # 輸出 trades_B.csv
    df_b_trades = pd.DataFrame(all_trades_B)
    df_b_trades.to_csv("trades_B.csv", index=False)
    print("\n[+] 已導出全部策略 B 成交紀錄至: trades_B.csv")

    # 格式化輸出對比表
    print("\n" + "=" * 90)
    print(f"{'資料來源':<15} | {'策略':<5} | {'筆數':<5} | {'Net PnL %':<10} | {'勝率 %':<8} | {'盈虧比':<6} | {'MDD %':<7} | {'Avg R':<6} | {'利潤回吐 %':<10}")
    print("-" * 90)
    for a, b in zip(results_A, results_B):
        d_name = os.path.basename(a['dataset'])
        print(f"{d_name:<15} | {'A':<5} | {a['count']:<5} | {a['net_pnl_pct']:<10} | {a['win_rate']:<8} | {a['profit_factor']:<6} | {a['mdd_pct']:<7} | {a['avg_r']:<6} | {a['giveback_pct']:<10}")
        print(f"{d_name:<15} | {'B':<5} | {b['count']:<5} | {b['net_pnl_pct']:<10} | {b['win_rate']:<8} | {b['profit_factor']:<6} | {b['mdd_pct']:<7} | {b['avg_r']:<6} | {b['giveback_pct']:<10}")
        print("-" * 90)

    # 統計 5 個反轉條件的貢獻
    print("\n" + "=" * 80)
    print("📊 策略 B 五大反轉條件個別貢獻度統計 (trades_B.csv)")
    print("=" * 80)
    condition_stats = []
    for cond in ['EMA5_TURN', 'REVERSAL_BAR', 'KC_RECLAIM', 'STRETCH_BOUNCE', 'NO_NEW_EXTREME', 'KC_MIDDLE_SAFETY']:
        cond_trades = df_b_trades[df_b_trades['exit_reason'] == cond] if not df_b_trades.empty else pd.DataFrame()
        c_count = len(cond_trades)
        c_avg_r = cond_trades['r_multiple'].mean() if c_count > 0 else 0.0
        c_win_rate = (len(cond_trades[cond_trades['net_pnl_pct'] > 0]) / c_count * 100) if c_count > 0 else 0.0
        c_net_pnl = cond_trades['net_pnl_pct'].sum() * 100 if c_count > 0 else 0.0
        condition_stats.append({
            "condition": cond,
            "count": c_count,
            "avg_r": round(c_avg_r, 2),
            "win_rate": round(c_win_rate, 2),
            "net_pnl": round(c_net_pnl, 2),
        })
        print(f"條件: {cond:<18} | 觸發次數: {c_count:<4} | 勝率: {c_win_rate:.1f}% | 平均 R: {c_avg_r:+.2f} | 總貢獻 PnL: {c_net_pnl:+.2f}%")

    # 找出表現最差的條件進行 B' 測試
    # 判斷標準：負貢獻最大或 Avg R 最差的條件
    underperforming = [c['condition'] for c in condition_stats if c['condition'] != 'KC_MIDDLE_SAFETY' and (c['avg_r'] < 0 or c['net_pnl'] < 0)]
    disable_target = set(underperforming[:1]) if underperforming else {'NO_NEW_EXTREME'}
    print(f"\n[*] 建議關閉之負貢獻條件: {disable_target}")

    # 跑 B' 測試
    print("\n" + "=" * 80)
    print(f"🔄 執行 B' 測試 (關閉條件: {disable_target}) 與 A、B 三者對比")
    print("=" * 80)
    results_B_prime = []
    for f in files[:3]:
        df = load_dataset(f)
        df = calculate_indicators(df)
        trades_bp = simulate_trades(df, strategy_type='B_PRIME', disabled_conditions=disable_target)
        res_bp = calculate_metrics(trades_bp)
        res_bp['dataset'] = f
        results_B_prime.append(res_bp)

    print(f"{'資料來源':<15} | {'版本':<7} | {'Net PnL %':<10} | {'勝率 %':<8} | {'盈虧比':<6} | {'MDD %':<7} | {'Avg R':<6} | {'利潤回吐 %':<10}")
    print("-" * 80)
    for a, b, bp in zip(results_A, results_B, results_B_prime):
        d_name = os.path.basename(a['dataset'])
        print(f"{d_name:<15} | {'A(中軌)':<7} | {a['net_pnl_pct']:<10} | {a['win_rate']:<8} | {a['profit_factor']:<6} | {a['mdd_pct']:<7} | {a['avg_r']:<6} | {a['giveback_pct']:<10}")
        print(f"{d_name:<15} | {'B(全開)':<7} | {b['net_pnl_pct']:<10} | {b['win_rate']:<8} | {b['profit_factor']:<6} | {b['mdd_pct']:<7} | {b['avg_r']:<6} | {b['giveback_pct']:<10}")
        print(f"{d_name:<15} | {'B_prime':<7} | {bp['net_pnl_pct']:<10} | {bp['win_rate']:<8} | {bp['profit_factor']:<6} | {bp['mdd_pct']:<7} | {bp['avg_r']:<6} | {bp['giveback_pct']:<10}")
        print("-" * 80)


if __name__ == "__main__":
    main()
