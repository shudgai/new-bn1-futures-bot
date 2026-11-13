"""
core/services/smart_exit_evaluator.py
====================================
智慧平倉裁決器 (Smart Exit Evaluator)
原則：
1. 中軌向上 ➔ 無視短線回踩 MA5，波段未變絕不平倉 (HOLD)
2. 只有在「峰頂三點型態 100% 確立」或「中軌轉向且實體跌破中軌」時才離場 / 翻空
3. 空單對稱：中軌向下 ➔ 無視短線反抽 MA5，只有在「谷底三點型態確立」或「中軌轉向且實體突破中軌」時才離場 / 翻多
"""
from typing import Dict, Any, Union
import pandas as pd
from core.strategies.true_peak_trough_strategy import PositionState


def evaluate_smart_exit(
    df: pd.DataFrame,
    current_position: Union[PositionState, str],
) -> Dict[str, Any]:
    """波段主升段陽線保護、主跌段陰線保護與智慧平倉邏輯：
    1. 多單遇綠 K (陽線) 暴漲中 ➔ 100% 強行 HOLD，不准平多！
    2. 空單遇紅 K (陰線) 暴跌中 ➔ 100% 強行 HOLD，不准平空！
    3. 中軌向上 ➔ 無視短線回踩 MA5，波段未變絕不平倉
    4. 只有在「峰頂/谷底三點型態 100% 確立」或「實體反向突破中軌」時才離場
    """
    if df is None or len(df) < 3:
        return {"action": "HOLD", "reason": "數據不足，維持現狀"}

    # 統一轉換為字串判定
    pos_str = current_position.value if isinstance(current_position, PositionState) else str(current_position).upper()

    c0 = float(df["close"].iloc[-1])
    o0 = float(df["open"].iloc[-1])
    h0 = float(df["high"].iloc[-1])
    l0 = float(df["low"].iloc[-1])

    c1 = float(df["close"].iloc[-2])
    o1 = float(df["open"].iloc[-2])
    h1 = float(df["high"].iloc[-2])
    l1 = float(df["low"].iloc[-2])

    c2 = float(df["close"].iloc[-3])
    o2 = float(df["open"].iloc[-3])
    h2 = float(df["high"].iloc[-3])
    l2 = float(df["low"].iloc[-3])

    # 1. 判斷當前 K 線形態
    is_green_bar = c0 > o0  # 當前為陽線 (大綠 K)
    is_red_bar = c0 < o0    # 當前為陰線 (大紅 K)

    kc_col = "kc_mid" if "kc_mid" in df.columns else ("kc_middle" if "kc_middle" in df.columns else None)
    if kc_col is not None:
        kc_mid = float(df[kc_col].iloc[-1])
        kc_mid_prev = float(df[kc_col].iloc[-2])
    else:
        kc_mid = float(df["close"].iloc[-20:].mean())
        kc_mid_prev = float(df["close"].iloc[-21:-1].mean())

    # 中軌方向 (波段生命線)
    is_kc_mid_up = kc_mid > kc_mid_prev
    is_kc_mid_down = kc_mid < kc_mid_prev

    if pos_str == "LONG":
        # 🛑 【第一優先：陽線保護鎖】
        # 只要當前是綠 K (陽線拉升)，強制禁止平倉，讓利潤奔跑！
        if is_green_bar:
            return {
                "action": "HOLD",
                "reason": "🚀 強勢陽線 (大綠K) 暴漲中，鎖定 HOLD，不平倉以吃滿主升段！",
            }

        # 🔴 【第二優先：峰頂三點確立】
        # 只有在收陰線 (紅K) 且跌破前一根低點時，才判定峰頂確立並平多
        is_high_area = (h1 >= kc_mid) or (h2 >= kc_mid)
        is_peak_3bar = is_high_area and is_red_bar and (c0 < l1) and (h0 <= h1)

        if is_peak_3bar:
            return {
                "action": "FLIP_TO_SHORT",
                "reason": "🎯 頂部出現陰線反轉 (峰頂三點確立)，高位精準平多並翻空",
            }
        elif (not is_kc_mid_up) and (c0 < kc_mid):
            return {"action": "FLIP_TO_SHORT", "reason": "波段中軌轉向跌破，平多"}
        else:
            # 💡 中軌仍向上，中間回踩 MA5 一律 HOLD 抱緊！
            return {
                "action": "HOLD",
                "reason": "KC中軌持續向上，波段未變，無視短線拉回繼續持多",
            }

    elif pos_str == "SHORT":
        # 🛑 【第一優先：陰線保護鎖】
        # 只要當前是紅 K (陰線殺跌)，強制禁止平空，讓利潤奔跑！
        if is_red_bar:
            return {
                "action": "HOLD",
                "reason": "🔻 強勢陰線 (大紅K) 暴跌中，鎖定 HOLD，不平空以吃滿主跌段！",
            }

        # 🔴 【第二優先：谷底三點確立】
        # 只有在收陽線 (綠K) 且突破前一根高點時，才判定谷底確立並平空
        is_low_area = (l1 <= kc_mid) or (l2 <= kc_mid)
        is_trough_3bar = is_low_area and is_green_bar and (c0 > h1) and (l0 >= l1)

        if is_trough_3bar:
            return {
                "action": "FLIP_TO_LONG",
                "reason": "🎯 底部出現陽線反轉 (谷底三點確立)，低位精準平空並翻多",
            }
        elif (not is_kc_mid_down) and (c0 > kc_mid):
            return {"action": "FLIP_TO_LONG", "reason": "波段中軌轉向突破，平空"}
        else:
            # 💡 中軌仍向下，中間反抽 MA5 一律 HOLD 抱緊！
            return {
                "action": "HOLD",
                "reason": "KC中軌持續向下，波段未變，無視短線反抽繼續持空",
            }

    return {"action": "HOLD", "reason": "維持現狀"}


# 便捷別名
evaluate_strategy_exit_protection = evaluate_smart_exit
evaluate_dual_strategy_exit = evaluate_smart_exit


def evaluate_short_position_holding(
    df: pd.DataFrame, current_position: Union[PositionState, str] = PositionState.SHORT
) -> Dict[str, Any]:
    """空單波段持倉保護與谷底第 3 點平空邏輯

    1. 當前為紅 K (陰線) 下砸中 ➔ 100% 強行 HOLD，絕不平空！
    2. KC 中軌向下波段中 ➔ 中間無效小反彈一律無視 (HOLD)
    3. 只有出現強勢大綠 K 且實體突破前高 (谷底第 3 點確立) ➔ 才允許平空/翻多
    """
    if df is None or len(df) < 2:
        return {"action": "HOLD", "reason": "數據不足，維持現狀"}

    c0, o0, h0, l0 = (
        float(df["close"].iloc[-1]),
        float(df["open"].iloc[-1]),
        float(df["high"].iloc[-1]),
        float(df["low"].iloc[-1]),
    )
    c1, o1, h1, l1 = (
        float(df["close"].iloc[-2]),
        float(df["open"].iloc[-2]),
        float(df["high"].iloc[-2]),
        float(df["low"].iloc[-2]),
    )

    if "kc_mid" in df.columns:
        kc_mid = float(df["kc_mid"].iloc[-1])
        kc_mid_prev = float(df["kc_mid"].iloc[-2])
    elif "kc_middle" in df.columns:
        kc_mid = float(df["kc_middle"].iloc[-1])
        kc_mid_prev = float(df["kc_middle"].iloc[-2])
    else:
        # 降級計算 20 EMA 作為 KC 中軌
        ema20 = df["close"].ewm(span=20, adjust=False).mean()
        kc_mid = float(ema20.iloc[-1])
        kc_mid_prev = float(ema20.iloc[-2])

    is_green_bar = c0 > o0  # 當前為陽線 (綠 K)
    is_red_bar = c0 < o0    # 當前為陰線 (紅 K)
    is_kc_mid_down = kc_mid < kc_mid_prev  # 中軌持續向下

    # 實體比例判定 (過濾長影線插針)
    candle_range = h0 - l0
    body_size = abs(c0 - o0)
    is_solid_bar = (
        (body_size / candle_range >= 0.55) if candle_range > 0 else False
    )

    is_kc_mid_up = kc_mid > kc_mid_prev    # 中軌持續向上
    is_kc_mid_down = kc_mid < kc_mid_prev  # 中軌持續向下

    pos_str = current_position.value if isinstance(current_position, PositionState) else str(current_position).upper()

    # =========================================================
    # 🔴 目前持有「空單 (SHORT)」
    # =========================================================
    if pos_str == "SHORT":

        # 🎯 【離場鎖 A：谷底第 3 點確理解鎖（最高優先）】
        # 必須收實體大綠 K + 實體超越前一根高點 + 實體比例 >= 55%
        is_trough_3bar_confirmed = (
            is_green_bar and (c0 > h1) and is_solid_bar and (c0 <= kc_mid)
        )
        if is_trough_3bar_confirmed:
            return {
                "action": "FLIP_TO_LONG",
                "reason": "🎯 谷底第 3 點確立 (強勢綠 K 實體突破前高)，精準平空並瞬間翻多！",
            }

        # 🔄 【關鍵修正：KC 中軌已向上 + 紅 K 回踩 ➔ 第一時間平空翻多】
        # 中軌已轉向上，且出現紅 K 回踩，波段確立轉多，第一時間離場
        if is_kc_mid_up and is_red_bar:
            return {
                "action": "FLIP_TO_LONG",
                "reason": "🚨 KC 中軌已向上且出現紅 K 回踩，波段確立轉多，第一時間平空並瞬間翻多！",
            }

        # 🛡️ 【保護鎖 1：中軌未向上 + 紅 K 下砸 ➔ 強行持空】
        if is_red_bar and not is_kc_mid_up:
            return {
                "action": "HOLD",
                "reason": "📉 空頭下砸中 (紅K)，強行持空不平倉！",
            }

        # 🛡️ 【保護鎖 2：中軌向下 ➔ 無視反彈強行 HOLD】
        # 中軌持續向下，中間任何反彈（綠 K 或紅 K）皆判定為洗盤，繼續持空！
        if is_kc_mid_down:
            return {
                "action": "HOLD",
                "reason": "📉 龍蝦空頭波段未變 (KC中軌向下)，無視短線反彈，強行持空！",
            }

        return {"action": "HOLD", "reason": "等待離場訊號"}

    return {"action": "HOLD", "reason": "維持現狀"}


def evaluate_long_position_holding(
    df: pd.DataFrame, current_position: Union[PositionState, str] = PositionState.LONG
) -> Dict[str, Any]:
    """多單波段持倉保護與峰頂第 3 點精準平多邏輯 (防 21:00 過早平多)

    1. 當前為綠 K (陽線) 拉升中 ➔ 100% 強力 HOLD，絕不平多！
    2. KC 中軌持續向上 (is_kc_mid_up) ➔ 無視中間小紅 K 洗盤，強行 HOLD 抱緊！
    3. 只有高位出現大紅 K 且實體跌破前低 (c0 < l1) ➔ 才允許峰頂第 3 點平多翻空
    """
    if df is None or len(df) < 2:
        return {"action": "HOLD", "reason": "數據不足，維持現狀"}

    c0, o0, h0, l0 = (
        float(df["close"].iloc[-1]),
        float(df["open"].iloc[-1]),
        float(df["high"].iloc[-1]),
        float(df["low"].iloc[-1]),
    )
    c1, o1, h1, l1 = (
        float(df["close"].iloc[-2]),
        float(df["open"].iloc[-2]),
        float(df["high"].iloc[-2]),
        float(df["low"].iloc[-2]),
    )

    if "kc_mid" in df.columns:
        kc_mid = float(df["kc_mid"].iloc[-1])
        kc_mid_prev = float(df["kc_mid"].iloc[-2])
    elif "kc_middle" in df.columns:
        kc_mid = float(df["kc_middle"].iloc[-1])
        kc_mid_prev = float(df["kc_middle"].iloc[-2])
    else:
        ema20 = df["close"].ewm(span=20, adjust=False).mean()
        kc_mid = float(ema20.iloc[-1])
        kc_mid_prev = float(ema20.iloc[-2])

    is_green_bar = c0 > o0  # 當前為陽線 (綠 K)
    is_red_bar = c0 < o0    # 當前為陰線 (紅 K)
    is_kc_mid_up = kc_mid > kc_mid_prev    # 中軌持續向上
    is_kc_mid_down = kc_mid < kc_mid_prev  # 中軌持續向下

    # 防假突破/插針：實體比例判定 (Body Ratio >= 55%)
    candle_range = h0 - l0
    body_size = abs(c0 - o0)
    is_solid_bar = (
        (body_size / candle_range >= 0.55) if candle_range > 0 else False
    )

    pos_str = current_position.value if isinstance(current_position, PositionState) else str(current_position).upper()

    # =========================================================
    # 🟢 目前持有「多單 (LONG)」
    # =========================================================
    if pos_str == "LONG":

        # 🎯 【離場鎖 A：峰頂第 3 點確理解鎖（最高優先）】
        # 必須收實體大紅 K + 實體跌破前一根低點 + 實體比例 >= 55%
        is_peak_3bar_confirmed = (
            is_red_bar and (c0 < l1) and is_solid_bar and (c0 >= kc_mid)
        )
        if is_peak_3bar_confirmed:
            return {
                "action": "FLIP_TO_SHORT",
                "reason": "🎯 峰頂第 3 點確立 (實體紅 K 跌破前低)，高位精準平多並瞬間翻空！",
            }

        # 🔄 【關鍵修正：KC 中軌已向下 + 綠 K 反彈 ➔ 第一時間平多翻空】
        # 中軌已轉向下行，且出現綠 K 反彈（代表波段確立轉空），第一時間離場
        if is_kc_mid_down and is_green_bar:
            return {
                "action": "FLIP_TO_SHORT",
                "reason": "🚨 KC 中軌已向下且出現綠 K 反彈，波段確立轉空，第一時間平多並瞬間翻空！",
            }

        # 🛡️ 【保護鎖 1：中軌未向下 + 綠 K 陽線暴漲保護】
        if is_green_bar and not is_kc_mid_down:
            return {
                "action": "HOLD",
                "reason": "🚀 [21:00後續] 當前為強勢綠 K 暴漲中，鎖定 HOLD 絕不平多！",
            }

        # 🛡️ 【保護鎖 2：KC 中軌向上防洗盤鎖】
        # 只要 KC 中軌持續向上，中間任何無效小紅 K 回踩一律無視！
        if is_kc_mid_up:
            return {
                "action": "HOLD",
                "reason": "🚀 龍蝦 KC 中軌持續向上，判定為高位洗盤，強行 HOLD 抱緊部位！",
            }

        return {
            "action": "HOLD",
            "reason": "未滿足峰頂轉折與中軌破位，繼續持多",
        }

    return {"action": "HOLD", "reason": "維持現狀"}


def evaluate_long_exit_protection(
    df: pd.DataFrame, current_position: Union[PositionState, str] = PositionState.LONG
) -> Dict[str, Any]:
    """多單離場保護別名，指向強化版 evaluate_long_position_holding"""
    return evaluate_long_position_holding(df, current_position)


def evaluate_short_exit_protection(
    df: pd.DataFrame, current_position: Union[PositionState, str] = PositionState.SHORT
) -> Dict[str, Any]:
    """空單離場保護別名，指向強化版 evaluate_short_position_holding"""
    return evaluate_short_position_holding(df, current_position)


from core.strategies.true_peak_trough_strategy import evaluate_high_precision_strategy
