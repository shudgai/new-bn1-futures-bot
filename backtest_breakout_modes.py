"""
backtest_breakout_modes.py - 破軌進場三種版本回測對比腳本

【回測版本比較】
1. 版本 A (ONLY_CONFIRM): 僅第二根確認
2. 版本 B (ALWAYS_SINGLE): 一律一根就進
3. 版本 C (HYBRID): 強突破一根(50%倉+加倉) + 普通突破第二根(100%倉)

【統計指標】
- 總交易筆數 (Trade Count)
- 勝率 (Win Rate %)
- 淨收益率 (Net PnL %)
- 盈虧比 (Profit Factor)
- 最大回撤 (MDD %)
- 平均 R (Avg R)
- BREAK_STRONG vs BREAK_CONFIRM 個別勝率與平均 R 統計
"""

import sys
import os
import math
import argparse
import urllib.request
import urllib.parse
import json
import pandas as pd
import numpy as np

from core.services.two_stage_breakout_service import (
    TwoStageBreakoutService,
    BREAK_STRONG,
    BREAK_CONFIRM,
    BREAK_STRONG_ADD,
)

FEE_RATE = 0.0004       # 單邊 0.04%
SLIPPAGE = 0.0002       # 單邊 0.02%


def fetch_klines_data(symbol: str, interval: str = "3m", limit: int = 1000) -> pd.DataFrame:
    """優先嘗試從幣安公開 API 下載，若失敗則從本地運行的 API 獲取"""
    clean_sym = symbol.replace('/', '').replace('_', '').upper()
    if not clean_sym.endswith('USDT') and 'PEPE' not in clean_sym:
        clean_sym += 'USDT'
    
    # 嘗試 1: 幣安公開 API
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={clean_sym}&interval={interval}&limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore']
        df = pd.DataFrame(data, columns=cols)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        df["is_closed"] = True
        return df
    except Exception:
        pass

    # 嘗試 2: 從本機運行的 API 獲取
    try:
        encoded_sym = urllib.parse.quote(symbol)
        local_url = f"http://127.0.0.1:8006/api/klines?symbol={encoded_sym}&timeframe={interval}&limit={limit}"
        req = urllib.request.Request(local_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            res_obj = json.loads(resp.read().decode())
        bars = res_obj.get("data", [])
        if bars:
            df = pd.DataFrame(bars)
            df['timestamp'] = df['time'] * 1000
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].astype(float)
            df["is_closed"] = True
            return df
    except Exception as exc:
        raise RuntimeError(f"無法載入 {symbol} 的 K 線: {exc}")

    raise RuntimeError(f"無法載入 {symbol} 的 K 線數據")


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """計算 KC (EMA20, ATR10, 1.5x) 與 EMA5, EMA13"""
    df = df.copy()
    c = df['close']
    h = df['high']
    l = df['low']

    df['ema5'] = c.ewm(span=5, adjust=False).mean()
    df['ema13'] = c.ewm(span=13, adjust=False).mean()
    df['kc_middle'] = c.ewm(span=20, adjust=False).mean()

    # ATR 10
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    df['atr'] = tr.rolling(10).mean().bfill()

    df['kc_upper'] = df['kc_middle'] + 1.5 * df['atr']
    df['kc_lower'] = df['kc_middle'] - 1.5 * df['atr']
    return df


def simulate_breakout_mode(df: pd.DataFrame, mode: str) -> list:
    """
    依指定模式 (ONLY_CONFIRM, ALWAYS_SINGLE, HYBRID) 執行逐根回測
    """
    trades = []
    position = None

    for i in range(25, len(df)):
        sub_df = df.iloc[:i+1]
        row = df.iloc[i]
        c_close = row['close']
        c_high = row['high']
        c_low = row['low']

        # ── 1. 若有持倉，先檢查是否平倉或加倉 ──
        if position is not None:
            side = position['side']
            entry_p = position['entry_price']
            sl_p = position['stop_loss']
            size_ratio = position['size_ratio']
            initial_risk = position['initial_risk']

            exit_triggered = False
            exit_price = c_close
            exit_reason = ""

            # A. 止損檢查 (收盤或盤中擊穿突破棒另一端外加 0.3*ATR)
            if side == "LONG":
                if c_low <= sl_p or c_close <= sl_p:
                    exit_triggered = True
                    exit_price = min(c_close, sl_p)
                    exit_reason = "STOP_LOSS"
                elif c_close < row['ema5'] or c_close < row['kc_upper']:
                    exit_triggered = True
                    exit_price = c_close
                    exit_reason = "EMA5_OR_KC_EXIT"
            else:
                if c_high >= sl_p or c_close >= sl_p:
                    exit_triggered = True
                    exit_price = max(c_close, sl_p)
                    exit_reason = "STOP_LOSS"
                elif c_close > row['ema5'] or c_close > row['kc_lower']:
                    exit_triggered = True
                    exit_price = c_close
                    exit_reason = "EMA5_OR_KC_EXIT"

            if exit_triggered:
                # 結算交易
                d = 1.0 if side == "LONG" else -1.0
                raw_ret = d * (exit_price - entry_p) / entry_p
                # 扣除雙邊手續費與滑點
                net_ret = raw_ret - 2 * (FEE_RATE + SLIPPAGE)
                weighted_pnl = net_ret * size_ratio
                r_mult = (d * (exit_price - entry_p)) / max(1e-9, initial_risk)

                trades.append({
                    "entry_source": position['entry_source'],
                    "side": side,
                    "entry_price": entry_p,
                    "exit_price": exit_price,
                    "size_ratio": size_ratio,
                    "raw_pnl_pct": raw_ret,
                    "net_pnl_pct": weighted_pnl,
                    "r_multiple": r_mult,
                    "exit_reason": exit_reason,
                })
                position = None
                continue

            # B. 檢查加倉 (僅 HYBRID 模式)
            if mode == "HYBRID" and position['entry_source'] == BREAK_STRONG and not position.get("has_scaled_in", False):
                add_sig = TwoStageBreakoutService.evaluate(
                    sub_df, side, position=position, mode=mode,
                )
                if add_sig is not None and add_sig.get("is_scale_in"):
                    # 加倉 50% 至 100%
                    new_size = 1.0
                    avg_entry = (position['entry_price'] * position['size_ratio'] + c_close * add_sig['size_ratio']) / new_size
                    position['entry_price'] = avg_entry
                    position['size_ratio'] = new_size
                    position['has_scaled_in'] = True
            continue

        # ── 2. 若無持倉，掃描新進場 ──
        for scan_side in ("LONG", "SHORT"):
            sig = TwoStageBreakoutService.evaluate(
                sub_df, scan_side, position=None, mode=mode,
            )
            if sig is not None:
                side = sig['side']
                entry_source = sig['entry_source']
                size_ratio = float(sig.get('size_ratio', 1.0))
                sl_price = float(sig['initial_sl'])
                risk = abs(c_close - sl_price)

                position = {
                    "side": side,
                    "entry_source": entry_source,
                    "entry_price": c_close,
                    "size_ratio": size_ratio,
                    "stop_loss": sl_price,
                    "initial_risk": risk,
                    "has_scaled_in": False,
                }
                break

    return trades


def calculate_metrics(trades: list) -> dict:
    if not trades:
        return {
            "count": 0, "win_rate": 0.0, "net_pnl_pct": 0.0,
            "profit_factor": 0.0, "mdd_pct": 0.0, "avg_r": 0.0,
        }

    df_t = pd.DataFrame(trades)
    count = len(df_t)
    wins = df_t[df_t['net_pnl_pct'] > 0]
    losses = df_t[df_t['net_pnl_pct'] <= 0]
    win_rate = (len(wins) / count) * 100.0

    total_gain = wins['net_pnl_pct'].sum()
    total_loss = abs(losses['net_pnl_pct'].sum())
    profit_factor = (total_gain / total_loss) if total_loss > 0 else 999.0

    total_net_pnl = df_t['net_pnl_pct'].sum() * 100.0
    avg_r = df_t['r_multiple'].mean()

    # MDD
    equity = (1.0 + df_t['net_pnl_pct']).cumprod()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    mdd = abs(dd.min()) * 100.0 if not dd.empty else 0.0

    return {
        "count": count,
        "win_rate": round(win_rate, 2),
        "net_pnl_pct": round(total_net_pnl, 2),
        "profit_factor": round(profit_factor, 2),
        "mdd_pct": round(mdd, 2),
        "avg_r": round(avg_r, 2),
    }


def main():
    parser = argparse.ArgumentParser(description="破軌三種版本回測對比")
    parser.add_argument("--symbols", nargs="+", default=["CAPUSDT", "1000LOBSTERUSDT"], help="回測幣種")
    args = parser.parse_args()

    print("=" * 85)
    print("🚀 啟動破軌進場三種版本對比回測 (3M K線, 雙邊摩擦 0.12%)")
    print("版本 1: 僅第二根確認 (ONLY_CONFIRM) - 100% 倉位")
    print("版本 2: 一律一根就進 (ALWAYS_SINGLE) - 100% 倉位")
    print("版本 3: 強突破一根 + 普通第二根 (HYBRID: 建議版) - 50% 啟動 + 次根補倉")
    print("=" * 85)

    all_hybrid_trades = []

    for sym in args.symbols:
        clean_sym = sym.replace('/', '').replace('_', '').upper()
        if not clean_sym.endswith('USDT'):
            clean_sym += 'USDT'
        print(f"\n[*] 載入 {sym} 3M 歷史數據 (1000 根)...")
        try:
            df = fetch_klines_data(sym, interval="3m", limit=1000)
            df = calculate_indicators(df)
        except Exception as e:
            print(f"[!] 載入失敗 {sym}: {e}")
            continue

        trades_confirm = simulate_breakout_mode(df, "ONLY_CONFIRM")
        trades_single = simulate_breakout_mode(df, "ALWAYS_SINGLE")
        trades_hybrid = simulate_breakout_mode(df, "HYBRID")

        m_confirm = calculate_metrics(trades_confirm)
        m_single = calculate_metrics(trades_single)
        m_hybrid = calculate_metrics(trades_hybrid)

        all_hybrid_trades.extend(trades_hybrid)

        print(f"\n【{clean_sym} 回測結果對比】")
        print(f"{'版本模式':<25} | {'筆數':<5} | {'Net PnL %':<10} | {'勝率 %':<8} | {'盈虧比':<6} | {'MDD %':<7} | {'Avg R':<6}")
        print("-" * 80)
        print(f"{'1. ONLY_CONFIRM (僅兩根)':<25} | {m_confirm['count']:<5} | {m_confirm['net_pnl_pct']:<10} | {m_confirm['win_rate']:<8} | {m_confirm['profit_factor']:<6} | {m_confirm['mdd_pct']:<7} | {m_confirm['avg_r']:<6}")
        print(f"{'2. ALWAYS_SINGLE (一律一根)':<25} | {m_single['count']:<5} | {m_single['net_pnl_pct']:<10} | {m_single['win_rate']:<8} | {m_single['profit_factor']:<6} | {m_single['mdd_pct']:<7} | {m_single['avg_r']:<6}")
        print(f"{'3. HYBRID (強一根+普通兩根)':<25} | {m_hybrid['count']:<5} | {m_hybrid['net_pnl_pct']:<10} | {m_hybrid['win_rate']:<8} | {m_hybrid['profit_factor']:<6} | {m_hybrid['mdd_pct']:<7} | {m_hybrid['avg_r']:<6}")

    # 分別統計 BREAK_STRONG 與 BREAK_CONFIRM 的勝率與平均 R
    if all_hybrid_trades:
        df_h = pd.DataFrame(all_hybrid_trades)
        print("\n" + "=" * 85)
        print("📊 HYBRID 建議版：BREAK_STRONG vs BREAK_CONFIRM 個別效益深度統計")
        print("=" * 85)
        for src in [BREAK_STRONG, BREAK_CONFIRM]:
            subset = df_h[df_h['entry_source'] == src]
            cnt = len(subset)
            if cnt > 0:
                wins = subset[subset['net_pnl_pct'] > 0]
                wr = (len(wins) / cnt) * 100.0
                avg_r = subset['r_multiple'].mean()
                net_pnl = subset['net_pnl_pct'].sum() * 100.0
                print(f"來源: {src:<15} | 交易筆數: {cnt:<4} | 勝率: {wr:.1f}% | 平均 R: {avg_r:+.2f} | 總貢獻 PnL: {net_pnl:+.2f}%")
            else:
                print(f"來源: {src:<15} | 交易筆數: 0")
        print("=" * 85)


if __name__ == "__main__":
    main()
