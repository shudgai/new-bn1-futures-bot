"""
run_pipeline_backtest_diagnosis.py
==================================
精確診斷真實 UnifiedPipelineGates (EntryGate & ExitGate) 在幣安合約 3m 真實 K 線上的表現。
針對用戶「第二階段：回測虧損診斷與策略修正」全面審計。
"""

import os
import sys
import math
import json
import urllib.request
import pandas as pd
import numpy as np
from datetime import datetime

# 導入真實 Pipeline 核心元件
from core.services.data_pipeline import DataPipeline, MarketDataSnapshot
from core.gates.unified_pipeline_gates import EntryGate, ExitGate

# 費率與滑價設定 (依交易所與策略標準)
FEE_RATE = 0.0004   # 單邊手續費 0.04%
SLIPPAGE = 0.0002   # 單邊滑點 0.02% (雙邊摩擦成本 0.12%)


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
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df


def run_pipeline_simulation(df: pd.DataFrame, symbol: str) -> list:
    """
    使用實際 DataPipeline 與 UnifiedPipelineGates (EntryGate & ExitGate) 進行逐棒回測
    """
    trades = []
    in_pos = False
    pos_state = {}
    last_exit_record = {}
    hourly_entries = []

    n = len(df)
    # 預留 60 根 K 棒讓指標 (EMA60, KC, ATR) 充分穩定
    for i in range(60, n):
        # 取得直到當前棒的歷史 slice
        sub_df = df.iloc[:i+1].copy()
        curr_bar = df.iloc[i]
        bar_ts = curr_bar['timestamp'] / 1000.0

        # 清理 1 小時前的開倉記錄
        hourly_entries = [t for t in hourly_entries if (bar_ts - t) < 3600.0]

        # 1. 建立 MarketDataSnapshot
        snapshot = DataPipeline.calculate_snapshot(
            symbol=symbol,
            df=sub_df,
            current_price=curr_bar['close'],
            position=pos_state if in_pos else None
        )
        if not snapshot:
            continue

        # ── 2. 若有持倉：評估 ExitGate ──
        if in_pos:
            # 追蹤 MFE 與 MAE
            entry_p = pos_state['entry_price']
            side = pos_state['side']
            if side == 'LONG':
                pos_state['mfe'] = max(pos_state['mfe'], curr_bar['high'] - entry_p)
                pos_state['mae'] = max(pos_state['mae'], entry_p - curr_bar['low'])
                pos_state['max_price'] = max(pos_state['max_price'], curr_bar['high'])
                cur_move = (curr_bar['close'] - entry_p) / entry_p
            else:
                pos_state['mfe'] = max(pos_state['mfe'], entry_p - curr_bar['low'])
                pos_state['mae'] = max(pos_state['mae'], curr_bar['high'] - entry_p)
                pos_state['min_price'] = min(pos_state['min_price'], curr_bar['low'])
                cur_move = (entry_p - curr_bar['close']) / entry_p
            
            pos_state['peak_roe'] = max(pos_state['peak_roe'], cur_move * 5.0)

            # 裁決出場
            should_exit, exit_reason, exit_meta = ExitGate.evaluate(
                pos=pos_state,
                market_data=snapshot
            )

            # 同根 K 棒盤中 3 ATR 硬止損檢查 (保護本金)
            initial_sl = pos_state['initial_sl']
            intrabar_hard_sl = False
            if side == 'LONG' and curr_bar['low'] <= initial_sl:
                should_exit = True
                exit_reason = 'CIRCUIT_BREAKER_HARD_SL_INTRABAR'
                intrabar_hard_sl = True
            elif side == 'SHORT' and curr_bar['high'] >= initial_sl:
                should_exit = True
                exit_reason = 'CIRCUIT_BREAKER_HARD_SL_INTRABAR'
                intrabar_hard_sl = True

            if should_exit:
                if intrabar_hard_sl:
                    raw_exit_p = initial_sl
                else:
                    raw_exit_p = curr_bar['close']

                # 扣除出場滑價
                if side == 'LONG':
                    actual_exit_p = raw_exit_p * (1.0 - SLIPPAGE)
                    gross_pnl_pct = (actual_exit_p - entry_p) / entry_p
                else:
                    actual_exit_p = raw_exit_p * (1.0 + SLIPPAGE)
                    gross_pnl_pct = (entry_p - actual_exit_p) / entry_p

                # 扣除雙邊手續費 (開平名目價值 * FEE_RATE)
                net_pnl_pct = gross_pnl_pct - (FEE_RATE * 2)

                # 檢查出場後未來 5 根價格是否朝原方向延伸 (判斷是否賣在半山腰/過早出場)
                future_bars = df.iloc[i+1:min(i+6, n)]
                continued_in_direction = False
                if not future_bars.empty:
                    if side == 'LONG':
                        continued_in_direction = (future_bars['high'].max() > pos_state['max_price'] + snapshot.atr * 0.5)
                    else:
                        continued_in_direction = (future_bars['low'].min() < pos_state['min_price'] - snapshot.atr * 0.5)

                trades.append({
                    "symbol": symbol,
                    "entry_idx": pos_state['entry_idx'],
                    "exit_idx": i,
                    "entry_time": pos_state['entry_time'],
                    "exit_time": curr_bar['datetime'],
                    "side": side,
                    "entry_mode": pos_state['entry_mode'],
                    "entry_price": entry_p,
                    "exit_price": actual_exit_p,
                    "exit_reason": exit_reason,
                    "gross_pnl_pct": gross_pnl_pct * 100.0,
                    "net_pnl_pct": net_pnl_pct * 100.0,
                    "mfe_pct": (pos_state['mfe'] / entry_p) * 100.0,
                    "mae_pct": (pos_state['mae'] / entry_p) * 100.0,
                    "hold_bars": i - pos_state['entry_idx'],
                    "ema5_entry": pos_state['ema5_entry'],
                    "ema13_entry": pos_state['ema13_entry'],
                    "atr_entry": pos_state['atr_entry'],
                    "macro_trend_entry": pos_state['macro_trend_entry'],
                    "continued_after_exit": continued_in_direction,
                })

                last_exit_record = {
                    "timestamp": bar_ts,
                    "exit_price": actual_exit_p,
                    "side": side,
                    "exit_reason": exit_reason
                }
                in_pos = False
                pos_state = {}
                continue

        # ── 3. 若無持倉：評估 EntryGate ──
        else:
            # 冷卻檢查 (平倉後至少 3 根 K 棒 = 540 秒)
            if last_exit_record:
                elapsed = bar_ts - last_exit_record["timestamp"]
                if elapsed < 540:  # 3 * 3m = 9 分鐘
                    continue

            if len(hourly_entries) >= 3:
                continue

            # 產生 candidate_signal 依 execution_manager 邏輯
            ma5_curr = snapshot.curr_k.get("ma5", snapshot.current_price)
            ma5_prev = snapshot.prev_k.get("ma5", ma5_curr)
            ma15_curr = snapshot.curr_k.get("ma15", snapshot.ma15)
            macro_trend = snapshot.macro_trend

            is_above_slow = (snapshot.curr_k["close"] >= snapshot.ema_slow)
            is_slow_up = snapshot.slow_slope >= 0
            is_bull_align = (snapshot.ema_fast >= snapshot.ema_slow)

            is_below_slow = (snapshot.curr_k["close"] <= snapshot.ema_slow)
            is_slow_down = snapshot.slow_slope <= 0
            is_bear_align = (snapshot.ema_fast <= snapshot.ema_slow)

            is_bullish_wave = (
                macro_trend == "BULLISH"
                or is_bull_align
                or (is_above_slow and is_slow_up)
                or (snapshot.kc_middle_slope >= -0.000005 and snapshot.curr_k["close"] >= snapshot.kc_middle)
            )
            is_bearish_wave = (
                macro_trend == "BEARISH"
                or is_bear_align
                or (is_below_slow and is_slow_down)
                or (snapshot.kc_middle_slope <= 0.000005 and snapshot.curr_k["close"] <= snapshot.kc_middle)
            )

            candidate_signal = None
            detected_mode = None

            # 模式 3: 破軌衝擊信號
            is_breakout_long = (
                (snapshot.current_price > snapshot.kc_upper or snapshot.curr_k["close"] > snapshot.kc_upper)
                and snapshot.kc_middle_slope >= -0.000005 and ma5_curr > ma5_prev
            )
            is_breakdown_short = (
                (snapshot.current_price < snapshot.kc_lower or snapshot.curr_k["close"] < snapshot.kc_lower)
                and snapshot.kc_middle_slope <= 0.000005 and ma5_curr < ma5_prev
            )

            # 模式 1: EMA 交叉發動
            is_cross_long = (
                ma5_curr >= ma15_curr and ma5_curr > ma5_prev
                and snapshot.kc_middle_slope >= -0.000005
                and snapshot.curr_k["close"] > snapshot.curr_k["open"]
                and snapshot.curr_k["close"] >= snapshot.kc_middle
            )
            is_cross_short = (
                ma5_curr <= ma15_curr and ma5_curr < ma5_prev
                and snapshot.kc_middle_slope <= 0.000005
                and snapshot.curr_k["close"] < snapshot.curr_k["open"]
                and snapshot.curr_k["close"] <= snapshot.kc_middle
            )

            # 模式 2: 均線回踩轉強
            is_pullback_long = (
                is_bullish_wave
                and snapshot.curr_k["low"] <= snapshot.ma5
                and snapshot.curr_k["close"] > snapshot.ma5
                and snapshot.curr_k["close"] > snapshot.curr_k["open"]
                and ma5_curr >= ma5_prev
            )
            is_pullback_short = (
                is_bearish_wave
                and snapshot.curr_k["high"] >= snapshot.ma5
                and snapshot.curr_k["close"] < snapshot.ma5
                and snapshot.curr_k["close"] < snapshot.curr_k["open"]
                and ma5_curr <= ma5_prev
            )

            if not is_bearish_wave:
                if is_breakout_long:
                    p_up = snapshot.prev_k.get("kc_upper", snapshot.kc_upper)
                    p_close = snapshot.prev_k.get("close", snapshot.current_price)
                    candidate_signal = "CONTINUATION_OUTSIDE_KC_LONG" if p_close > p_up else "BREAKOUT_LONG"
                    detected_mode = "MODE_3_BREAKOUT"
                elif is_pullback_long:
                    candidate_signal = "TREND_PULLBACK_LONG"
                    detected_mode = "MODE_2_PULLBACK"
                elif is_cross_long:
                    candidate_signal = "ENTRY_LONG_EMA_CROSS_MID"
                    detected_mode = "MODE_1_TREND_START"

            elif not is_bullish_wave:
                if is_breakdown_short:
                    p_low = snapshot.prev_k.get("kc_lower", snapshot.kc_lower)
                    p_close = snapshot.prev_k.get("close", snapshot.current_price)
                    candidate_signal = "CONTINUATION_OUTSIDE_KC_SHORT" if p_close < p_low else "BREAKDOWN_SHORT"
                    detected_mode = "MODE_3_BREAKOUT"
                elif is_pullback_short:
                    candidate_signal = "TREND_PULLBACK_SHORT"
                    detected_mode = "MODE_2_PULLBACK"
                elif is_cross_short:
                    candidate_signal = "ENTRY_SHORT_EMA_CROSS_MID"
                    detected_mode = "MODE_1_TREND_START"

            if candidate_signal:
                last_exit_ctx = {
                    **(last_exit_record or {}),
                    "hourly_entry_count": len(hourly_entries),
                    "current_time": bar_ts,
                }
                should_entry, entry_reason, entry_meta = EntryGate.evaluate(
                    signal_type=candidate_signal,
                    market_data=snapshot,
                    last_exit_context=last_exit_ctx
                )

                if should_entry:
                    side = entry_meta.get("side", "LONG" if "LONG" in candidate_signal else "SHORT")
                    # 進場滑價
                    if side == "LONG":
                        actual_entry_p = curr_bar['close'] * (1.0 + SLIPPAGE)
                        initial_sl = actual_entry_p - 3.0 * snapshot.atr
                    else:
                        actual_entry_p = curr_bar['close'] * (1.0 - SLIPPAGE)
                        initial_sl = actual_entry_p + 3.0 * snapshot.atr

                    in_pos = True
                    hourly_entries.append(bar_ts)
                    pos_state = {
                        "symbol": symbol,
                        "side": side,
                        "entry_idx": i,
                        "entry_time": curr_bar['datetime'],
                        "entry_price": actual_entry_p,
                        "entry_mode": detected_mode,
                        "entry_reason": entry_reason,
                        "initial_sl": initial_sl,
                        "atr": snapshot.atr,
                        "atr_entry": snapshot.atr,
                        "ema5_entry": snapshot.ma5,
                        "ema13_entry": snapshot.ma15,
                        "macro_trend_entry": snapshot.macro_trend,
                        "mfe": 0.0,
                        "mae": 0.0,
                        "max_price": curr_bar['high'],
                        "min_price": curr_bar['low'],
                        "peak_roe": 0.0,
                        "leverage": 5.0,
                    }

    return trades


def analyze_trades(trades: list, symbol: str) -> dict:
    if not trades:
        print(f"\n[!] {symbol}: 無任何成交交易 (可能全部被 EntryGate 嚴格過濾阻擋)")
        return {}

    df_t = pd.DataFrame(trades)
    total_trades = len(df_t)
    longs = df_t[df_t['side'] == 'LONG']
    shorts = df_t[df_t['side'] == 'SHORT']

    win_trades = df_t[df_t['net_pnl_pct'] > 0]
    loss_trades = df_t[df_t['net_pnl_pct'] <= 0]
    win_rate = (len(win_trades) / total_trades) * 100.0

    total_net_pnl = df_t['net_pnl_pct'].sum()
    avg_win = win_trades['net_pnl_pct'].mean() if not win_trades.empty else 0.0
    avg_loss = loss_trades['net_pnl_pct'].mean() if not loss_trades.empty else 0.0
    gross_win = win_trades['net_pnl_pct'].sum() if not win_trades.empty else 0.0
    gross_loss = abs(loss_trades['net_pnl_pct'].sum()) if not loss_trades.empty else 0.0001
    profit_factor = gross_win / gross_loss

    # 權益曲線與 MDD
    equity = (1.0 + df_t['net_pnl_pct'] / 100.0).cumprod()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    mdd = abs(dd.min()) * 100.0

    # 四大分類情境診斷
    # A. 進場太晚，行情回踩就止損 (hold_bars <= 3 且 net_pnl < 0 且 mae > mfe)
    cat_a = df_t[(df_t['net_pnl_pct'] < 0) & (df_t['mae_pct'] > df_t['mfe_pct']) & (df_t['hold_bars'] <= 4)]
    # B. 進場方向正確，但出場過早，錯失後續趨勢 (continued_after_exit == True 且 mfe > 1.0)
    cat_b = df_t[df_t['continued_after_exit'] == True]
    # C. 進場方向錯誤，續抱規則延長虧損 (hold_bars > 8 且 net_pnl < -1.0)
    cat_c = df_t[(df_t['hold_bars'] > 8) & (df_t['net_pnl_pct'] < -1.0)]
    # D. 交易本身有微利，但手續費滑點侵蝕為負 (gross_pnl > 0 但 net_pnl <= 0)
    cat_d = df_t[(df_t['gross_pnl_pct'] > 0) & (df_t['net_pnl_pct'] <= 0)]

    print(f"\n{'='*80}")
    print(f"📊 標的: {symbol} 真實 UnifiedPipeline 診斷結果")
    print(f"{'='*80}")
    print(f"總交易次數: {total_trades} | 多單: {len(longs)} | 空單: {len(shorts)}")
    print(f"手續費後淨損益: {total_net_pnl:.2f}% | 勝率: {win_rate:.2f}% | 盈虧比: {profit_factor:.2f} | MDD: {mdd:.2f}%")
    print(f"平均獲利: +{avg_win:.2f}% | 平均虧損: {avg_loss:.2f}% | 平均持倉 K 棒: {df_t['hold_bars'].mean():.1f} 根")

    # 多空分開
    print(f"\n--- 多空獨立績效 ---")
    if not longs.empty:
        l_wins = longs[longs['net_pnl_pct'] > 0]
        print(f"多單 ({len(longs)} 筆): 淨利 {longs['net_pnl_pct'].sum():.2f}%, 勝率 {len(l_wins)/len(longs)*100:.1f}%, 平均 {longs['net_pnl_pct'].mean():.2f}%")
    if not shorts.empty:
        s_wins = shorts[shorts['net_pnl_pct'] > 0]
        print(f"空單 ({len(shorts)} 筆): 淨利 {shorts['net_pnl_pct'].sum():.2f}%, 勝率 {len(s_wins)/len(shorts)*100:.1f}%, 平均 {shorts['net_pnl_pct'].mean():.2f}%")

    # 三大進場模式獨立績效
    print(f"\n--- 三大進場模式獨立績效 ---")
    for mode in ["MODE_1_TREND_START", "MODE_2_PULLBACK", "MODE_3_BREAKOUT"]:
        m_df = df_t[df_t['entry_mode'] == mode]
        if not m_df.empty:
            m_wins = m_df[m_df['net_pnl_pct'] > 0]
            print(f"[{mode}] ({len(m_df)} 筆): 淨利 {m_df['net_pnl_pct'].sum():.2f}%, 勝率 {len(m_wins)/len(m_df)*100:.1f}%, 平均 PnL {m_df['net_pnl_pct'].mean():.2f}%")
        else:
            print(f"[{mode}]: 0 筆")

    # 出場 Reason Code 統計
    print(f"\n--- 出場原因分佈 (Exit Reason) ---")
    for reason, grp in df_t.groupby('exit_reason'):
        print(f"  {reason:<35} | 次數: {len(grp):<3} | 淨利貢獻: {grp['net_pnl_pct'].sum():>6.2f}% | 勝率: {(len(grp[grp['net_pnl_pct']>0])/len(grp))*100:>5.1f}%")

    # 四大情境診斷比例
    print(f"\n--- 四大虧損/錯失情境佔比 ---")
    print(f"A. 進場太晚，回踩即止損: {len(cat_a)} 筆 ({len(cat_a)/total_trades*100:.1f}%)")
    print(f"B. 出場過早，錯失後續趨勢: {len(cat_b)} 筆 ({len(cat_b)/total_trades*100:.1f}%)")
    print(f"C. 進場錯誤，續抱延長虧損: {len(cat_c)} 筆 ({len(cat_c)/total_trades*100:.1f}%)")
    print(f"D. 微利被手續費滑點侵蝕: {len(cat_d)} 筆 ({len(cat_d)/total_trades*100:.1f}%)")

    return {
        "symbol": symbol,
        "total_trades": total_trades,
        "net_pnl": total_net_pnl,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "mdd": mdd,
        "cat_a": len(cat_a),
        "cat_b": len(cat_b),
        "cat_c": len(cat_c),
        "cat_d": len(cat_d),
    }


def main():
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    results = {}
    all_trades = []

    for sym in symbols:
        print(f"\n[*] 正在下載並診斷 {sym} 3M 真實數據 (1000 根 K 棒)...")
        df = fetch_binance_klines(sym, interval="3m", limit=1000)
        trades = run_pipeline_simulation(df, sym)
        res = analyze_trades(trades, sym)
        results[sym] = res
        all_trades.extend(trades)

    # 輸出所有交易明細到 CSV 供逐筆核對
    if all_trades:
        df_all = pd.DataFrame(all_trades)
        df_all.to_csv("real_pipeline_trades.csv", index=False)
        print(f"\n[+] 已導出全部真實 Pipeline 交易明細至: real_pipeline_trades.csv (共 {len(df_all)} 筆)")


if __name__ == "__main__":
    main()
