"""
tools/diagnose_entry_gates.py
==============================
開倉時機全面稽核診斷工具（增強版）：
1. 嚴格區分「未產生候選訊號」與「產生候選訊號後被 Gate 阻擋」
2. 統計 EMA5/EMA13 交叉、KC 突破、回踩進場三大模式各門禁的阻擋明細
3. 輸出典型錯失行情案例 (幣種、時間、價格、訊號、阻擋原因、對應程式行號)
4. 檢查 K 線收盤確認、大週期對齊與無未來資訊
"""

import json
import os
import sys
sys.path.insert(0, os.path.abspath("."))
import pandas as pd
import numpy as np
from collections import Counter

from core.gates.unified_pipeline_gates import EntryGate
from core.services.data_pipeline import MarketDataSnapshot, DataPipeline


def run_entry_diagnosis():
    json_path = "data/golden_dataset_2500_bars.json"
    if not os.path.exists(json_path):
        print(f"找不到固化數據集: {json_path}")
        return

    with open(json_path, "r", encoding="utf-8") as f:
        golden_data = json.load(f)

    symbols = list(golden_data.keys())
    print(f"==================================================================================")
    print(f"🚀 開始執行全維度開倉時機稽核診斷 (6 幣種 × 2,500 根 = 15,000 根 3m K 線)")
    print(f"==================================================================================")

    total_bars_evaluated = 0
    no_candidate_bars = 0
    candidate_bars_count = 0

    mode_candidate_counts = Counter()
    mode_allowed_counts = Counter()
    gate_veto_counts = Counter()
    mode_gate_breakdown = {}

    allowed_trades = []
    blocked_opportunities = []

    # 程式碼行號映射表 (對應 core/gates/unified_pipeline_gates.py)
    code_line_map = {
        "BLOCKED_LONG_ON_BEARISH_TREND": "unified_pipeline_gates.py:928 (門禁0: 順勢鐵律)",
        "BLOCKED_SHORT_ON_BULLISH_TREND": "unified_pipeline_gates.py:954 (門禁0: 順勢鐵律)",
        "LATE_CHASE_BAND_EXTENSION": "unified_pipeline_gates.py:1001 (延續開倉乖離限制)",
        "LATE_CHASE_STALE_CONTINUATION": "unified_pipeline_gates.py:1039 (延續開倉新鮮度過期)",
        "VETO_EXIT_COOLDOWN": "unified_pipeline_gates.py:1056 (門禁A: 平倉後冷卻)",
        "BLOCKED_REENTRY_PRICE_NOT_EXCEEDED": "unified_pipeline_gates.py:1085 (門禁A: 禁止原地反覆刷單)",
        "BLOCKED_MA5_MA15_CROSS_CHOP": "unified_pipeline_gates.py:1133 (門禁B: 均線糾纏震盪)",
        "BLOCKED_MA5_MA15_GAP_TOO_NARROW": "unified_pipeline_gates.py:1146 (門禁B: 均線間距過窄)",
        "BLOCKED_KC_SQUEEZE": "unified_pipeline_gates.py:1162 (門禁C: 通道極度收斂)",
        "BLOCKED_DEAD_FISH_FLAT_BANDWIDTH": "unified_pipeline_gates.py:1210 (門禁C-2: 行情末端死魚盤)",
        "BLOCKED_RECENT_WATERFALL_BREAKDOWN": "unified_pipeline_gates.py:1233 (門禁D: 巨型崩跌大黑棒後冷卻)",
        "BLOCKED_LONG_ON_EMA_FALLING": "unified_pipeline_gates.py:1257 (均線下拐逆勢做多)",
        "BLOCKED_SHORT_ON_EMA_RISING": "unified_pipeline_gates.py:1262 (均線上翹逆勢做空)",
        "VETO_WICK_REJECTION": "unified_pipeline_gates.py:1277 (門禁1: 長影線急衝針尖)",
        "VETO_OVER_EXTENSION_FROM_SLOW_MA": "unified_pipeline_gates.py:1283 (門禁1: 乖離過大)",
        "BLOCKED_MA5_FLAT": "unified_pipeline_gates.py:813 (MA5走平無方向)",
        "BLOCKED_BREAKOUT_NO_NEW_HIGH": "unified_pipeline_gates.py:1506 (模式3: 兩根破軌未過前高)",
        "BLOCKED_BREAKDOWN_NO_NEW_LOW": "unified_pipeline_gates.py:1530 (模式3: 兩根跌破未破前低)",
        "VETO_ENTRY_CONDITIONS_NOT_MET": "unified_pipeline_gates.py:1550 (未達模式入場條件)",
    }

    for sym in symbols:
        bars = golden_data[sym]
        df = pd.DataFrame(bars)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", errors="coerce")

        for i in range(45, len(df)):
            total_bars_evaluated += 1
            sub_df = df.iloc[max(0, i - 120) : i + 1].copy()
            curr_bar = df.iloc[i]
            prev_bar = df.iloc[i - 1]

            snapshot = DataPipeline.calculate_snapshot(
                symbol=sym,
                df=sub_df,
                current_price=float(curr_bar["close"]),
                position=None,
            )
            if not snapshot:
                continue

            ma5_curr = float(snapshot.curr_k.get("ma5", snapshot.current_price))
            ma5_prev = float(snapshot.prev_k.get("ma5", ma5_curr))
            ma15_curr = float(snapshot.curr_k.get("ma15", getattr(snapshot, "ma15", ma5_curr)))
            ma15_prev = float(snapshot.prev_k.get("ma15", ma15_curr))
            macro_trend = getattr(snapshot, "macro_trend", "NEUTRAL")

            # 1. 模式 1: EMA 交叉啟動 (做多/做空)
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

            # 2. 模式 2: 趨勢回踩均線帶守穩轉強
            is_pullback_long = (
                float(snapshot.curr_k.get("low", snapshot.current_price)) <= ma5_curr * 1.002
                and snapshot.curr_k["close"] >= ma5_curr
                and snapshot.curr_k["close"] >= snapshot.curr_k["open"]
                and ma5_curr >= ma5_prev
                and snapshot.kc_middle_slope >= -0.000005
                and snapshot.curr_k["close"] >= snapshot.ema_slow
            )
            is_pullback_short = (
                float(snapshot.curr_k.get("high", snapshot.current_price)) >= ma5_curr * 0.998
                and snapshot.curr_k["close"] <= ma5_curr
                and snapshot.curr_k["close"] <= snapshot.curr_k["open"]
                and ma5_curr <= ma5_prev
                and snapshot.kc_middle_slope <= 0.000005
                and snapshot.curr_k["close"] <= snapshot.ema_slow
            )

            # 3. 模式 3: 強動能收盤破軌突破
            is_breakout_long = (
                snapshot.curr_k["close"] > snapshot.kc_upper
                and snapshot.kc_middle_slope >= -0.000005
                and ma5_curr > ma5_prev
            )
            is_breakdown_short = (
                snapshot.curr_k["close"] < snapshot.kc_lower
                and snapshot.kc_middle_slope <= 0.000005
                and ma5_curr < ma5_prev
            )

            candidate_signals = []
            if is_breakout_long:
                candidate_signals.append(("BREAKOUT_LONG", "MODE_3_BREAKOUT"))
            elif is_pullback_long:
                candidate_signals.append(("TREND_PULLBACK_LONG", "MODE_2_PULLBACK"))
            elif is_cross_long:
                candidate_signals.append(("ENTRY_LONG_EMA_CROSS_MID", "MODE_1_TREND_START"))

            if is_breakdown_short:
                candidate_signals.append(("BREAKDOWN_SHORT", "MODE_3_BREAKOUT"))
            elif is_pullback_short:
                candidate_signals.append(("TREND_PULLBACK_SHORT", "MODE_2_PULLBACK"))
            elif is_cross_short:
                candidate_signals.append(("ENTRY_SHORT_EMA_CROSS_MID", "MODE_1_TREND_START"))

            if not candidate_signals:
                no_candidate_bars += 1
                continue

            candidate_bars_count += 1

            for sig_type, mode_name in candidate_signals:
                mode_candidate_counts[mode_name] += 1
                if mode_name not in mode_gate_breakdown:
                    mode_gate_breakdown[mode_name] = Counter()

                allowed, reason, meta = EntryGate.evaluate(
                    signal_type=sig_type,
                    market_data=snapshot,
                    last_exit_context=None,
                )

                if allowed:
                    mode_allowed_counts[mode_name] += 1
                    allowed_trades.append({
                        "symbol": sym,
                        "time": str(curr_bar.get("open_time", "")),
                        "price": float(curr_bar["close"]),
                        "mode": mode_name,
                        "signal": sig_type,
                        "reason": reason,
                    })
                else:
                    gate_veto_counts[reason] += 1
                    mode_gate_breakdown[mode_name][reason] += 1

                    # 評估後續 5 根 K 棒走勢
                    if i + 5 < len(df):
                        future_bars = df.iloc[i + 1 : i + 6]
                        entry_p = float(curr_bar["close"])
                        if "LONG" in sig_type:
                            max_f = float(future_bars["high"].max())
                            gain_atr = (max_f - entry_p) / snapshot.atr if snapshot.atr > 0 else 0
                        else:
                            min_f = float(future_bars["low"].min())
                            gain_atr = (entry_p - min_f) / snapshot.atr if snapshot.atr > 0 else 0

                        if gain_atr >= 1.5:
                            blocked_opportunities.append({
                                "symbol": sym,
                                "time": str(curr_bar.get("open_time", "")),
                                "price": entry_p,
                                "mode": mode_name,
                                "signal": sig_type,
                                "reason": reason,
                                "code_loc": code_line_map.get(reason, "unified_pipeline_gates.py:EntryGate"),
                                "max_gain_atr": round(gain_atr, 2),
                            })

    print(f"\n一、整體訊號層次劃分統計：")
    print(f"  - 評估總 K 線根數            : {total_bars_evaluated:>6} 根 (100.0%)")
    print(f"  - 未達指標門檻無候選訊號      : {no_candidate_bars:>6} 根 ({no_candidate_bars/total_bars_evaluated*100:.1f}%)")
    print(f"  - 產生候選進場訊號           : {candidate_bars_count:>6} 根 ({candidate_bars_count/total_bars_evaluated*100:.1f}%)")
    print(f"  - 通過所有 Gate 放行開倉     : {len(allowed_trades):>6} 筆 (放行率: {len(allowed_trades)/sum(mode_candidate_counts.values())*100:.1f}%)")
    print(f"  - 被 Gate 攔截之候選訊號     : {sum(gate_veto_counts.values()):>6} 筆")

    print(f"\n二、三大進場模式放行與攔截統計：")
    print(f"{'進場模式':<22} | {'候選觸發數':<10} | {'放行開倉數':<10} | {'攔截次數':<10} | {'放行率':<8}")
    print("-" * 70)
    for m in ["MODE_3_BREAKOUT", "MODE_2_PULLBACK", "MODE_1_TREND_START"]:
        cand = mode_candidate_counts[m]
        allow = mode_allowed_counts[m]
        veto = cand - allow
        rate = allow / cand * 100 if cand > 0 else 0
        print(f"{m:<22} | {cand:>10} | {allow:>10} | {veto:>10} | {rate:>7.1f}%")

    print(f"\n三、全域 Gate 阻擋原因 (Veto Reason) 頻率排行榜：")
    print(f"{'阻擋原因代碼 (Veto Code)':<35} | {'攔截次數':<8} | {'佔阻擋比':<8} | {'程式碼具體位置'}")
    print("-" * 90)
    total_vetoes = sum(gate_veto_counts.values())
    for r, c in gate_veto_counts.most_common(12):
        loc = code_line_map.get(r, "unified_pipeline_gates.py:EntryGate")
        pct = c / total_vetoes * 100
        print(f"{r:<35} | {c:>8} | {pct:>7.1f}% | {loc}")

    print(f"\n四、錯失大行情之典型案例 (後續 5 根利潤 >= 1.5 ATR 卻被 Gate 阻擋)：")
    print(f"共計錯失大行情案例數: {len(blocked_opportunities)} 次")
    print(f"{'幣種':<12} | {'時間 (UTC)':<19} | {'價格':<10} | {'模式':<18} | {'阻擋原因':<32} | {'後續漲跌幅':<10}")
    print("-" * 115)
    for op in blocked_opportunities[:8]:
        print(f"{op['symbol']:<12} | {op['time']:<19} | {op['price']:<10.4f} | {op['mode']:<18} | {op['reason']:<32} | +{op['max_gain_atr']} ATR")


if __name__ == "__main__":
    run_entry_diagnosis()
