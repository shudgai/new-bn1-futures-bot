"""Defensive Trend Strategy (防禦性波段與趨勢交易策略)

嚴格實作四大開倉防禦門禁與持倉防守平倉機制：
一、 開倉防禦門禁 (一票否決制)：
   - 所有進場判定一律使用「上一根已收盤 K 棒 (Completed Bar, iloc[-2])」，嚴禁使用未走完的動態 Bar
   - 多單：KC 必須向上 (kc_trend == 'UP')、MA5 > MA15、MA5 實質向上勾頭 (斜率 > 0)、實體陽線 (無長上影竭盡)
   - 空單：KC 必須向下 (kc_trend == 'DOWN')、MA5 < MA15、MA5 向上絕對不開空 (必須斜率 < 0)、反向實體 K 線不要開空 (必須實體陰線，無長下影反彈)
   - 平倉後至少間隔 3 根 K 棒冷卻期 (Cooldown)

二、 持倉與及時平倉防守機制：
   - 開倉當根呼吸保護 (<= 60s)：給予波段展開呼吸空間，除非當根成型極端避雷針/針尖力竭
   - 趨勢逆轉強制全平：多單遇 KC 翻空全平；空單遇 KC 翻多全平
   - 均線死叉／金叉離場：多單 MA5 死叉 MA15 或下彎立即平多；空單 MA5 金叉 MA15 或連續 2 根站上 MA15 立即平空
   - 動態回撤止盈 (Trailing Stop)：達到浮盈高點後回撤 0.8%~1.2% 或跌破成本保本線立即平倉
   - 伺服器硬止損 (Hard Stop-Loss)：虧損達到門檻 (-1.5% ~ -2%) 或摜破結構防線
   - 健康回抽容忍：空單回抽 MA5/MA15 但收盤未站穩 MA15 且無金叉，視為健康回抽，嚴禁平空，避免被雙巴刷單
"""

import logging
import math
import time
from typing import Any, Dict, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


def get_bars_for_analysis(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, Optional[pd.Series]]:
    """獲取分析用 K 棒：(最新已收盤棒 last_closed, 前一根已收盤棒 prev_closed, 當前未收線動態棒 live_row)"""
    if df is None or len(df) < 2:
        raise ValueError("DataFrame 至少需包含 2 根 K 棒以進行有效性審查")

    live_row = None
    if "is_closed" in df.columns and not bool(df.iloc[-1]["is_closed"]):
        live_row = df.iloc[-1]
        last_closed = df.iloc[-2]
        prev_closed = df.iloc[-3] if len(df) >= 3 else last_closed
    else:
        last_closed = df.iloc[-1]
        prev_closed = df.iloc[-2]

    return last_closed, prev_closed, live_row


# ══════════════════════════════════════════════════════════════════════
# 一、 開倉防禦門禁 (Entry Guard Clauses)
# ══════════════════════════════════════════════════════════════════════

def check_long_entry(
    df: pd.DataFrame,
    symbol: str = "UNKNOWN",
    bars_since_last_exit: Optional[int] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """開多單嚴格條件 (Long Entry Guard Clauses - 一票否決制)
    
    1. 平倉冷卻門禁：同一方向平倉後，至少間隔 3 根 K 棒
    2. 背景趨勢門禁：KC 必須為向上 (kc_trend == 'UP')，KC 向下嚴禁開多
    3. 均線排列門禁：MA5 必須在 MA15 上方 (ma5 > ma15)
    4. 均線斜率門禁：MA5 必須實質向上勾頭 (ma5_curr - ma5_prev > 0)，走平或向下絕對不開多
    5. K 棒實體過濾：上一根收盤必須為實體陽線 (close > open)，嚴禁在陰線或長上影線竭盡棒摸頂開多
    """
    if df is None or len(df) < 2:
        return False, "BLOCKED_INSUFFICIENT_DATA", {}

    try:
        last_closed, prev_closed, _ = get_bars_for_analysis(df)
    except Exception as exc:
        return False, f"BLOCKED_BAR_EXTRACTION_FAILED: {exc}", {}

    open_p = float(last_closed["open"])
    close_p = float(last_closed["close"])
    high_p = float(last_closed["high"])
    low_p = float(last_closed["low"])

    ma5_curr = float(last_closed.get("ma5", last_closed.get("_wild_ma5", close_p)))
    ma5_prev = float(prev_closed.get("ma5", prev_closed.get("_wild_ma5", ma5_curr)))
    ma15_curr = float(last_closed.get("ma15", close_p))
    ma15_prev = float(prev_closed.get("ma15", ma15_curr))

    kc_mid_curr = float(last_closed.get("kc_middle", ma15_curr))
    kc_mid_prev = float(prev_closed.get("kc_middle", ma15_prev))
    kc_mid_slope = kc_mid_curr - kc_mid_prev

    kc_trend = str(last_closed.get("kc_trend", "")).upper()
    if kc_trend not in ("UP", "DOWN"):
        kc_trend = "UP" if kc_mid_slope > 1e-12 else ("DOWN" if kc_mid_slope < -1e-12 else "FLAT")

    body = abs(close_p - open_p)
    upper_wick = max(0.0, high_p - max(close_p, open_p))

    details = {
        "symbol": symbol,
        "side": "LONG",
        "close": close_p,
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "ma5_curr": ma5_curr,
        "ma5_prev": ma5_prev,
        "ma5_slope": ma5_curr - ma5_prev,
        "ma15_curr": ma15_curr,
        "kc_trend": kc_trend,
        "kc_mid_slope": kc_mid_slope,
        "body": body,
        "upper_wick": upper_wick,
        "bars_since_last_exit": bars_since_last_exit,
    }

    # 門禁 0：冷卻機制 (Cooldown >= 3 根)
    if bars_since_last_exit is not None and bars_since_last_exit < 3:
        logger.warning(
            f"[拒絕開多] {symbol} 處於平倉冷卻期 (已過 {bars_since_last_exit} 根 < 3 根冷卻門檻)"
        )
        return False, "BLOCKED_BY_EXIT_COOLDOWN", details

    # 門禁 1：背景趨勢門禁 (KC 必須為向上)
    if kc_trend != "UP" or kc_mid_slope <= 0:
        logger.warning(
            f"[拒絕開多] {symbol} KC 趨勢非向上 (kc_trend={kc_trend}, slope={kc_mid_slope:+.8g})"
        )
        return False, "BLOCKED_LONG_ON_BEARISH_KC_TREND", details

    # 門禁 2：均線排列門禁 (MA5 必須在 MA15 上方)
    if ma5_curr <= ma15_curr:
        logger.warning(
            f"[拒絕開多] {symbol} MA5 未在 MA15 上方 (MA5={ma5_curr:.6g} <= MA15={ma15_curr:.6g})"
        )
        return False, "BLOCKED_LONG_ON_MA_ORDER", details

    # 門禁 3：均線斜率門禁 (MA5 必須實質向上勾頭)
    if ma5_curr - ma5_prev <= 0:
        logger.warning(
            f"[拒絕開多] {symbol} MA5 正在向下勾頭或走平 (MA5斜率={ma5_curr - ma5_prev:+.8g} <= 0)"
        )
        return False, "BLOCKED_LONG_ON_MA5_SLOPE", details

    # 門禁 4：K 棒實體過濾 (上一根收盤必須為實體陽線)
    if close_p <= open_p:
        logger.warning(
            f"[拒絕開多] {symbol} 上一根為陰線實體，嚴禁摸頂開多 (close={close_p:.6g} <= open={open_p:.6g})"
        )
        return False, "BLOCKED_LONG_ON_BEARISH_CANDLE", details

    # 門禁 5：長上影線竭盡棒過濾 (上影線不得大於實體 1.5 倍)
    if upper_wick > 1.5 * max(body, 1e-9):
        logger.warning(
            f"[拒絕開多] {symbol} 上一根為長上影線竭盡避雷針 (上影線={upper_wick:.6g} > 1.5倍實體={1.5 * body:.6g})"
        )
        return False, "BLOCKED_LONG_ON_EXHAUSTION_WICK", details

    logger.info(
        f"[核准開多] {symbol} 所有防禦門禁全數通過 (KC={kc_trend}, MA5={ma5_curr:.6g}>MA15={ma15_curr:.6g}, "
        f"MA5斜率={ma5_curr - ma5_prev:+.6g}, 實體紅陽棒)"
    )
    return True, "PERMIT_LONG_ENTRY", details


def check_short_entry(
    df: pd.DataFrame,
    symbol: str = "UNKNOWN",
    bars_since_last_exit: Optional[int] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """開空單嚴格條件 (Short Entry Guard Clauses - 一票否決制)
    
    1. 平倉冷卻門禁：同一方向平倉後，至少間隔 3 根 K 棒
    2. 背景趨勢門禁：KC 必須為向下 (kc_trend == 'DOWN')，KC 向上嚴禁開空
    3. 均線排列門禁：MA5 必須在 MA15 下方 (ma5 < ma15)
    4. 均線斜率門禁 (核心新增)：MA5 向上絕對不開空！必須滿足 MA5 實質向下傾斜 (ma5_curr - ma5_prev < 0)
    5. K 棒實體過濾 (核心新增)：反向實體 K 線不要開空！上一根收盤必須為實體陰線 (close < open)，嚴禁在大陽線、長下影線 (下影線 > 實體 1.5 倍) 的反彈時開空
    """
    if df is None or len(df) < 2:
        return False, "BLOCKED_INSUFFICIENT_DATA", {}

    try:
        last_closed, prev_closed, _ = get_bars_for_analysis(df)
    except Exception as exc:
        return False, f"BLOCKED_BAR_EXTRACTION_FAILED: {exc}", {}

    open_p = float(last_closed["open"])
    close_p = float(last_closed["close"])
    high_p = float(last_closed["high"])
    low_p = float(last_closed["low"])

    ma5_curr = float(last_closed.get("ma5", last_closed.get("_wild_ma5", close_p)))
    ma5_prev = float(prev_closed.get("ma5", prev_closed.get("_wild_ma5", ma5_curr)))
    ma15_curr = float(last_closed.get("ma15", close_p))
    ma15_prev = float(prev_closed.get("ma15", ma15_curr))

    kc_mid_curr = float(last_closed.get("kc_middle", ma15_curr))
    kc_mid_prev = float(prev_closed.get("kc_middle", ma15_prev))
    kc_mid_slope = kc_mid_curr - kc_mid_prev

    kc_trend = str(last_closed.get("kc_trend", "")).upper()
    if kc_trend not in ("UP", "DOWN"):
        kc_trend = "UP" if kc_mid_slope > 1e-12 else ("DOWN" if kc_mid_slope < -1e-12 else "FLAT")

    body = abs(open_p - close_p)
    lower_wick = max(0.0, min(open_p, close_p) - low_p)

    details = {
        "symbol": symbol,
        "side": "SHORT",
        "close": close_p,
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "ma5_curr": ma5_curr,
        "ma5_prev": ma5_prev,
        "ma5_slope": ma5_curr - ma5_prev,
        "ma15_curr": ma15_curr,
        "kc_trend": kc_trend,
        "kc_mid_slope": kc_mid_slope,
        "body": body,
        "lower_wick": lower_wick,
        "bars_since_last_exit": bars_since_last_exit,
    }

    # 門禁 0：冷卻機制 (Cooldown >= 3 根)
    if bars_since_last_exit is not None and bars_since_last_exit < 3:
        logger.warning(
            f"[拒絕開空] {symbol} 處於平倉冷卻期 (已過 {bars_since_last_exit} 根 < 3 根冷卻門檻)"
        )
        return False, "BLOCKED_BY_EXIT_COOLDOWN", details

    # 門禁 1：背景趨勢門禁 (KC 必須為向下)
    if kc_trend != "DOWN" or kc_mid_slope >= 0:
        logger.warning(
            f"[拒絕開空] {symbol} KC 趨勢非向下 (kc_trend={kc_trend}, slope={kc_mid_slope:+.8g})"
        )
        return False, "BLOCKED_SHORT_ON_BULLISH_KC_TREND", details

    # 門禁 2：均線排列門禁 (MA5 必須在 MA15 下方)
    if ma5_curr >= ma15_curr:
        logger.warning(
            f"[拒絕開空] {symbol} MA5 未在 MA15 下方 (MA5={ma5_curr:.6g} >= MA15={ma15_curr:.6g})"
        )
        return False, "BLOCKED_SHORT_ON_MA_ORDER", details

    # 門禁 3：均線斜率門禁 (核心新增：MA5 向上絕對不開空！必須向下傾斜)
    if ma5_curr - ma5_prev >= 0:
        logger.warning(
            f"[拒絕開空] MA5 正在向上勾頭或走平 (MA5斜率={ma5_curr - ma5_prev:+.8g} >= 0)"
        )
        return False, "BLOCKED_SHORT_ON_MA5_SLOPE", details

    # 門禁 4：K 棒實體過濾 (核心新增：反向實體 K 線不要開空！必須為實體陰線)
    if close_p >= open_p:
        logger.warning(
            f"[拒絕開空] 上一根為陽線實體，反向實體 K 線禁止開空 (close={close_p:.6g} >= open={open_p:.6g})"
        )
        return False, "BLOCKED_SHORT_ON_BULLISH_CANDLE", details

    # 門禁 5：長下影線反彈棒過濾 (下影線不得大於實體 1.5 倍)
    if lower_wick > 1.5 * max(body, 1e-9):
        logger.warning(
            f"[拒絕開空] 上一根為長下影線反彈棒，嚴禁在反彈時開空 (下影線={lower_wick:.6g} > 1.5倍實體={1.5 * body:.6g})"
        )
        return False, "BLOCKED_SHORT_ON_REBOUND_WICK", details

    logger.info(
        f"[核准開空] {symbol} 所有防禦門禁全數通過 (KC={kc_trend}, MA5={ma5_curr:.6g}<MA15={ma15_curr:.6g}, "
        f"MA5斜率={ma5_curr - ma5_prev:+.6g}, 實體黑陰棒)"
    )
    return True, "PERMIT_SHORT_ENTRY", details


# ══════════════════════════════════════════════════════════════════════
# 二、 持倉與及時平倉防守機制 (Exit Guard Clauses)
# ══════════════════════════════════════════════════════════════════════

def check_long_exit(
    position: Dict[str, Any],
    df: pd.DataFrame,
    quote: Optional[float] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """持有多單審查 (Long Exit Guard Clauses)
    
    1. 開倉當根保護 (<= 60s)：免疫普通回踩與硬止損，除非成型極端避雷針
    2. 均線死叉離場：MA5 向下跌破 MA15
    3. 均線反轉離場：MA5 明確下彎
    4. 趨勢逆轉強制全平：kc_trend 翻為 'DOWN'，立即市價全平多單，嚴禁逆勢死扛
    5. 動態回撤止盈 (Trailing Stop)：浮盈曾達標 (>= 1.2%)，回撤超過 0.8%~1.2% 或跌破成本保本線
    6. 伺服器硬止損：虧損達到最大容忍門檻 (-1.5% ~ -2%) 或摜破 KC 中軌/下軌
    7. 健康回踩容忍：若未觸發上述條件，維持持倉，拒絕神經質頻繁刷單平倉
    """
    if df is None or len(df) < 2:
        return False, "HOLD_INSUFFICIENT_DATA", {}

    try:
        last_closed, prev_closed, live_row = get_bars_for_analysis(df)
    except Exception as exc:
        return False, f"HOLD_BAR_EXTRACTION_FAILED: {exc}", {}

    symbol = str(position.get("symbol", "UNKNOWN"))
    close_p = float(last_closed["close"])
    open_p = float(last_closed["open"])
    high_p = float(last_closed["high"])
    low_p = float(last_closed["low"])
    curr_price = float(quote) if quote is not None else (
        float(live_row["close"]) if live_row is not None and "close" in live_row else close_p
    )

    entry_p = float(position.get("entry_price") or curr_price)
    if entry_p <= 0:
        entry_p = curr_price

    highest_p = max(float(position.get("highest_price") or entry_p), curr_price, high_p)
    position["highest_price"] = highest_p

    live_ma5 = float(live_row["ma5"]) if live_row is not None and "ma5" in live_row else None
    live_ma15 = float(live_row["ma15"]) if live_row is not None and "ma15" in live_row else None

    ma5_curr = live_ma5 if live_ma5 is not None else float(last_closed.get("ma5", last_closed.get("_wild_ma5", close_p)))
    ma5_prev = float(prev_closed.get("ma5", prev_closed.get("_wild_ma5", ma5_curr)))
    ma15_curr = live_ma15 if live_ma15 is not None else float(last_closed.get("ma15", close_p))
    ma15_prev = float(prev_closed.get("ma15", ma15_curr))

    kc_mid_curr = float(last_closed.get("kc_middle", ma15_curr))
    kc_mid_prev = float(prev_closed.get("kc_middle", ma15_prev))
    kc_mid_slope = kc_mid_curr - kc_mid_prev
    kc_upper = float(last_closed.get("kc_upper", kc_mid_curr * 1.01))
    kc_lower = float(last_closed.get("kc_lower", kc_mid_curr * 0.99))

    kc_trend = str(last_closed.get("kc_trend", "")).upper()
    if kc_trend not in ("UP", "DOWN"):
        kc_trend = "UP" if kc_mid_slope > 1e-12 else ("DOWN" if kc_mid_slope < -1e-12 else "FLAT")

    atr = float(last_closed.get("atr", abs(kc_upper - kc_lower) / 4.0 or close_p * 0.01))
    if atr <= 0:
        atr = max(close_p * 0.01, 1e-8)

    death_cross = (ma5_curr < ma15_curr and ma5_prev >= ma15_prev)
    ma5_slope_down = (ma5_curr < ma5_prev)

    peak_gain_pct = (highest_p - entry_p) / entry_p if entry_p > 0 else 0.0
    pnl_pct = (curr_price - entry_p) / entry_p if entry_p > 0 else 0.0

    open_time = float(position.get("open_timestamp") or position.get("timestamp") or position.get("entry_timestamp") or 0.0)
    if open_time > 1e11:
        open_time /= 1000.0
    is_entry_bar = (open_time > 0 and (time.time() - open_time) < 60.0)

    details = {
        "symbol": symbol,
        "side": "LONG",
        "close": close_p,
        "entry_price": entry_p,
        "current_price": curr_price,
        "highest_price": highest_p,
        "peak_gain_pct": peak_gain_pct,
        "pnl_pct": pnl_pct,
        "ma5_curr": ma5_curr,
        "ma5_prev": ma5_prev,
        "ma15_curr": ma15_curr,
        "kc_trend": kc_trend,
        "kc_middle": kc_mid_curr,
        "is_entry_bar": is_entry_bar,
    }

    # 0. 開倉當根呼吸保護 (Entry Bar Lifecycle <= 60s)
    if is_entry_bar:
        body = abs(close_p - open_p)
        upper_wick = max(0.0, high_p - max(close_p, open_p))
        is_extreme_climax = (high_p >= kc_upper and upper_wick >= 2.0 * max(body, 0.2 * atr) and curr_price < (open_p + close_p) / 2.0)
        if is_extreme_climax:
            logger.info(f"[平倉觸發] {symbol} 多單 - 開倉當根爆量避雷針力竭平多")
            return True, "EXIT_LONG_BLOW_OFF_TOP", details
        return False, "HOLD: ENTRY_BAR_BREATHING", details

    # 1. 均線死叉離場
    if death_cross:
        logger.info(f"[平倉觸發] {symbol} 多單 - MA5/MA15 均線死叉平多 (MA5={ma5_curr:.6g} < MA15={ma15_curr:.6g})")
        return True, "EXIT_BY_MA_DEATH_CROSS", details

    # 2. 均線斜率下彎反轉
    if ma5_slope_down:
        logger.info(f"[平倉觸發] {symbol} 多單 - MA5 明確下彎平多 (MA5={ma5_curr:.6g}, 斜率={ma5_curr - ma5_prev:+.6g})")
        return True, "EXIT_BY_MA5_TREND_REVERSAL", details

    # 3. 趨勢逆轉強制全平 (Fail-Safe)
    if kc_trend == "DOWN" or kc_mid_slope < -1e-12:
        logger.info(f"[平倉觸發] {symbol} 多單 - KC翻空強制清倉 (kc_trend={kc_trend}, slope={kc_mid_slope:+.6g})")
        return True, "EXIT_BY_KC_TREND_REVERSAL", details

    # 4. 動態回撤止盈 (Trailing Stop) 與保本防守
    if peak_gain_pct >= 0.012 or highest_p >= kc_upper:
        pullback_pct = (highest_p - curr_price) / entry_p
        if pullback_pct >= 0.008:
            logger.info(f"[平倉觸發] {symbol} 多單 - 動態回撤止盈 (最高點={highest_p:.6g}, 回撤={pullback_pct:.2%})")
            return True, "EXIT_BY_TRAILING_PROFIT_LOCK", details
        fee_buffer = entry_p * 0.0015
        if curr_price <= entry_p + fee_buffer:
            logger.info(f"[平倉觸發] {symbol} 多單 - 跌破成本線保本平倉 (成本={entry_p:.6g}, 現價={curr_price:.6g})")
            return True, "EXIT_BY_BREAKEVEN", details

    # 5. 伺服器硬止損 (Hard Stop-Loss) 與結構破位
    if close_p < kc_mid_curr or close_p < kc_lower:
        logger.info(f"[平倉觸發] {symbol} 多單 - 實體收盤摜破 KC 中軌/下軌結構防線 (close={close_p:.6g} < middle={kc_mid_curr:.6g})")
        return True, "EXIT_BY_KC_BAND_BREACH", details

    if pnl_pct <= -0.015 or (entry_p - curr_price) >= 2.2 * atr:
        logger.info(f"[平倉觸發] {symbol} 多單 - 達到伺服器硬止損門檻 (虧損={pnl_pct:.2%})")
        return True, "EXIT_BY_HARD_STOP_LOSS", details

    # 6. 健康回踩容忍 (拒絕神經質頻繁刷單平倉)
    return False, "HOLD: HEALTHY_PULLBACK_TOLERANCE", details


def check_short_exit(
    position: Dict[str, Any],
    df: pd.DataFrame,
    quote: Optional[float] = None,
    allow_ma5_slope_exit: bool = True,
) -> Tuple[bool, str, Dict[str, Any]]:
    """持有空單審查 (Short Exit Guard Clauses)
    
    1. 開倉當根保護 (<= 60s)：免疫普通回抽與硬止損，除非成型極端谷底針尖
    2. 均線金叉離場：MA5 向上突破 MA15 (金叉)
    3. 連續 2 根收盤站上 MA15 離場
    4. 均線斜率反轉向上離場
    5. 趨勢逆轉強制全平：kc_trend 翻為 'UP'，立即市價全平空單，嚴禁逆勢死扛
    6. 動態回撤止盈 (Trailing Stop)：浮盈曾達標 (>= 1.2%)，反彈超過 0.8%~1.2% 或回升至成本保本線
    7. 伺服器硬止損：虧損達到最大容忍門檻 (-1.5% ~ -2%) 或突破 KC 中軌/上軌
    8. 健康回抽容忍：價格正常回踩 MA5 或 MA15，但收盤價尚未實質站穩 MA15 上方且未形成金叉，視為健康回抽，嚴禁平空！
    """
    if df is None or len(df) < 2:
        return False, "HOLD_INSUFFICIENT_DATA", {}

    try:
        last_closed, prev_closed, live_row = get_bars_for_analysis(df)
    except Exception as exc:
        return False, f"HOLD_BAR_EXTRACTION_FAILED: {exc}", {}

    symbol = str(position.get("symbol", "UNKNOWN"))
    close_p = float(last_closed["close"])
    open_p = float(last_closed["open"])
    high_p = float(last_closed["high"])
    low_p = float(last_closed["low"])
    prev_close = float(prev_closed["close"])
    curr_price = float(quote) if quote is not None else (
        float(live_row["close"]) if live_row is not None and "close" in live_row else close_p
    )

    entry_p = float(position.get("entry_price") or curr_price)
    if entry_p <= 0:
        entry_p = curr_price

    lowest_p = min(float(position.get("lowest_price") or entry_p), curr_price, low_p)
    position["lowest_price"] = lowest_p

    live_ma5 = float(live_row["ma5"]) if live_row is not None and "ma5" in live_row else None
    live_ma15 = float(live_row["ma15"]) if live_row is not None and "ma15" in live_row else None

    ma5_curr = live_ma5 if live_ma5 is not None else float(last_closed.get("ma5", last_closed.get("_wild_ma5", close_p)))
    ma5_prev = float(prev_closed.get("ma5", prev_closed.get("_wild_ma5", ma5_curr)))
    ma15_curr = live_ma15 if live_ma15 is not None else float(last_closed.get("ma15", close_p))
    ma15_prev = float(prev_closed.get("ma15", ma15_curr))

    kc_mid_curr = float(last_closed.get("kc_middle", ma15_curr))
    kc_mid_prev = float(prev_closed.get("kc_middle", ma15_prev))
    kc_mid_slope = kc_mid_curr - kc_mid_prev
    kc_upper = float(last_closed.get("kc_upper", kc_mid_curr * 1.01))
    kc_lower = float(last_closed.get("kc_lower", kc_mid_curr * 0.99))

    kc_trend = str(last_closed.get("kc_trend", "")).upper()
    if kc_trend not in ("UP", "DOWN"):
        kc_trend = "UP" if kc_mid_slope > 1e-12 else ("DOWN" if kc_mid_slope < -1e-12 else "FLAT")

    atr = float(last_closed.get("atr", abs(kc_upper - kc_lower) / 4.0 or close_p * 0.01))
    if atr <= 0:
        atr = max(close_p * 0.01, 1e-8)

    golden_cross = (ma5_curr > ma15_curr and ma5_prev <= ma15_prev)
    ma5_slope_up = (ma5_curr > ma5_prev)
    two_bars_above_ma15 = (close_p > ma15_curr and prev_close > ma15_prev)

    peak_gain_pct = (entry_p - lowest_p) / entry_p if entry_p > 0 else 0.0
    pnl_pct = (entry_p - curr_price) / entry_p if entry_p > 0 else 0.0

    open_time = float(position.get("open_timestamp") or position.get("timestamp") or position.get("entry_timestamp") or 0.0)
    if open_time > 1e11:
        open_time /= 1000.0
    is_entry_bar = (open_time > 0 and (time.time() - open_time) < 60.0)

    details = {
        "symbol": symbol,
        "side": "SHORT",
        "close": close_p,
        "entry_price": entry_p,
        "current_price": curr_price,
        "lowest_price": lowest_p,
        "peak_gain_pct": peak_gain_pct,
        "pnl_pct": pnl_pct,
        "ma5_curr": ma5_curr,
        "ma5_prev": ma5_prev,
        "ma15_curr": ma15_curr,
        "kc_trend": kc_trend,
        "kc_middle": kc_mid_curr,
        "is_entry_bar": is_entry_bar,
    }

    # 0. 開倉當根呼吸保護 (Entry Bar Lifecycle <= 60s)
    if is_entry_bar:
        body = abs(open_p - close_p)
        lower_wick = max(0.0, min(open_p, close_p) - low_p)
        is_extreme_climax = (low_p <= kc_lower and lower_wick >= 2.0 * max(body, 0.2 * atr) and curr_price > (open_p + close_p) / 2.0)
        if is_extreme_climax:
            logger.info(f"[平倉觸發] {symbol} 空單 - 開倉當根谷底針尖爆量平空")
            return True, "EXIT_SHORT_BLOW_OFF_BOTTOM", details
        return False, "HOLD: ENTRY_BAR_BREATHING", details

    # 1. 均線金叉離場
    if golden_cross:
        logger.info(f"[平倉觸發] {symbol} 空單 - MA5/MA15 均線金叉平空 (MA5={ma5_curr:.6g} > MA15={ma15_curr:.6g})")
        return True, "EXIT_BY_MA_GOLDEN_CROSS", details

    # 2. 均線斜率反轉向上
    if allow_ma5_slope_exit and ma5_slope_up:
        # 防守過濾：若價格與 MA5 均仍在 MA15 下方且無金叉，視為正常健康回踩，嚴禁神經質平空避免底部被雙巴刷單
        if ma5_curr <= ma15_curr and close_p <= ma15_curr:
            logger.info(
                f"[防守續抱] {symbol} 空單 - 價格與 MA5 仍在 MA15 下方 (MA5={ma5_curr:.6g}<=MA15={ma15_curr:.6g})，"
                f"判定為健康回踩，嚴禁神經質刷單平空"
            )
        else:
            logger.info(f"[平倉觸發] {symbol} 空單 - MA5 明確向上仰頭平空 (MA5={ma5_curr:.6g}, 斜率={ma5_curr - ma5_prev:+.6g})")
            return True, "EXIT_BY_MA5_TREND_REVERSAL", details

    # 3. 均線連 2 根站上 MA15 離場
    if two_bars_above_ma15:
        logger.info(
            f"[平倉觸發] {symbol} 空單 - 連續 2 根收盤站上 MA15 離場 (close={close_p:.6g}, prev={prev_close:.6g} > MA15={ma15_curr:.6g})"
        )
        return True, "EXIT_BY_MA15_BREAKOUT", details

    # 4. 趨勢逆轉強制全平 (Fail-Safe)
    if kc_trend == "UP" or kc_mid_slope > 1e-12:
        logger.info(f"[平倉觸發] {symbol} 空單 - KC翻多/中軌上行強制清倉 (kc_trend={kc_trend}, slope={kc_mid_slope:+.6g})")
        return True, "EXIT_BY_KC_TREND_REVERSAL", details

    # 5. 動態回撤止盈 (Trailing Stop) 與保本防守
    if peak_gain_pct >= 0.012 or lowest_p <= kc_lower:
        rebound_pct = (curr_price - lowest_p) / entry_p
        if rebound_pct >= 0.008:
            logger.info(f"[平倉觸發] {symbol} 空單 - 動態低點回抽止盈 (最低點={lowest_p:.6g}, 反彈={rebound_pct:.2%})")
            return True, "EXIT_BY_TRAILING_PROFIT_LOCK", details
        fee_buffer = entry_p * 0.0015
        if curr_price >= entry_p - fee_buffer:
            logger.info(f"[平倉觸發] {symbol} 空單 - 跌破成本線保本平空 (成本={entry_p:.6g}, 現價={curr_price:.6g})")
            return True, "EXIT_BY_BREAKEVEN", details

    # 6. 伺服器硬止損 (Hard Stop-Loss) 與結構破位
    if close_p > kc_mid_curr or close_p > kc_upper:
        logger.info(f"[平倉觸發] {symbol} 空單 - 實體收盤突破 KC 中軌/上軌結構防線 (close={close_p:.6g} > middle={kc_mid_curr:.6g})")
        return True, "EXIT_BY_KC_BAND_BREACH", details

    if pnl_pct <= -0.015 or (curr_price - entry_p) >= 2.2 * atr:
        logger.info(f"[平倉觸發] {symbol} 空單 - 達到伺服器硬止損門檻 (虧損={pnl_pct:.2%})")
        return True, "EXIT_BY_HARD_STOP_LOSS", details

    # 7. 健康回抽容忍：正常回踩 MA5/MA15 但未站穩 MA15 且無金叉，視為健康回抽，嚴禁平空！
    return False, "HOLD: HEALTHY_PULLBACK_TOLERANCE", details
