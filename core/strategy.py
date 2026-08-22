import math
import pandas as pd
import numpy as np
from core.config import (
    STOP_LOSS_MULTIPLIER, TAKE_PROFIT_MULTIPLIER, TAKER_FEE_RATE, MIN_NET_REWARD_RISK,
    MIN_REWARD_RISK_RATIO,
    SLIPPAGE_PCT,
    KELTNER_BREAKOUT_MARGIN_PCT, KELTNER_MIN_VOLUME_RATIO, FRESHNESS_DECAY_BARS,
    ENTRY_FRESHNESS_SCORE_MAX,
    MIN_FRESHNESS_SCORE,
    RSI_LONG_THRESHOLD, RSI_SHORT_THRESHOLD, RSI_LONG_MAX, RSI_SHORT_MIN,
    MIN_SCORE_THRESHOLD, ENTRY_MIN_QUALITY_BONUS, PULLBACK_ZONE_PCT, MAX_ATR_PCT, MIN_ATR_PCT,
    KELTNER_ATR_MULTIPLIER, get_pullback_target_depth, MIN_SL_DISTANCE_PCT,
    ADX_PERIOD, ADX_QUALITY_MIN, ADX_QUALITY_FULL, ADX_DECLINE_LOOKBACK_BARS,
    WEAK_ENERGY_ADX_THRESHOLD,
    ADX_DECLINE_MIN_DROP, ADX_DECLINE_MIN_DROP_RATIO,
    EMA_EXTENSION_MAX_ATR_MULT, PULLBACK_SCORE_THRESHOLD, DISASTER_STOP_MULTIPLIER,
    ADX_MANDATORY_MIN, BREAKOUT_CONFIRM_BARS, POST_BREAKOUT_VOL_SUSTAIN_RATIO,
    PULLBACK_TARGET_MIN_ATR_MULT, ADVERSE_PULLBACK_VOLUME_SPIKE_RATIO,
    ADVERSE_PULLBACK_BODY_MIN_ATR_MULT,
    TREND_AGREE_EMA_MARGIN_PCT,
    BTC_REGIME_FILTER_ENABLED, BTC_REGIME_ALLOW_CONTRARY,
    BTC_REGIME_FLIP_BUFFER_BARS, BTC_REGIME_SCORE_PENALTY,
    BTC_REGIME_ALLOCATION_FACTOR,
    MA7_EARLY_ENTRY_ENABLED, MA7_EARLY_MIN_ATR_MULT, MA7_REVERSAL_MIN_ATR_MULT,
    MA7_FAST_ENTRY_ENABLED, MA7_FAST_MIN_ATR_MULT, MA7_FAST_MAX_ATR_MULT,
    MA7_FAST_MIN_VOLUME_RATIO, MA7_DYNAMIC_ATR_FLOOR_PCT,
    MA7_BOTTOM_ENTRY_ENABLED, MA7_BOTTOM_OFFSET_ATR_MULT,
    BOTTOM_FILTER_ENABLED, BOTTOM_OVERSOLD_RSI_15M_LIMIT, BOTTOM_OVERBOUGHT_RSI_15M_LIMIT,
    STRUCTURED_VOLUME_MIN_RATIO, STRUCTURED_SWING_LOOKBACK,
    STRUCTURED_SUPPORT_NEAR_ATR, STRUCTURED_RSI_LONG_TRIGGER,
    STRUCTURED_RSI_SHORT_TRIGGER, ENABLE_MOMENTUM_CROSS_ENTRY, ENABLE_BREAKOUT_ENTRY,
    MOMENTUM_CROSS_REQUIRE_CONTINUATION, MOMENTUM_CROSS_MIN_PROFIT_ROOM_PCT,
    BREAKOUT_ENTRY_SCORE,
    ENABLE_1H_EMA50_FILTER, STRUCTURED_1H_EMA50_TOLERANCE_PCT,
    SUPPORT_PULLBACK_RSI_LONG_MIN, SUPPORT_PULLBACK_RSI_SHORT_MAX,
    SUPPORT_PULLBACK_RSI_LONG_MAX, SUPPORT_PULLBACK_RSI_SHORT_MIN,
    SUPPORT_PULLBACK_MIN_BODY_ATR_MULT, SUPPORT_PULLBACK_MAKER_OFFSET_ATR_MULT,
    SUPPORT_PULLBACK_MIN_VOLUME_RATIO, SUPPORT_PULLBACK_MAX_VOLUME_RATIO,
    SUPPORT_PULLBACK_LOCATION_MEMORY_BARS, SUPPORT_PULLBACK_CONFIRM_MEMORY_BARS,
    TREND_EXTENSION_MIN_ROOM_PCT, TREND_EXTENSION_MIN_VOLUME_RATIO,
    TREND_EXTENSION_MIN_BODY_ATR_MULT, MIN_ENTRY_PROFIT_ROOM_PCT,
    get_bounce_capture_ratio,
    HIGH_SCORE_ATR_LIMIT_PCT, HIGH_SCORE_THRESHOLD,
    KELTNER_MIN_WIDTH_ATR_MULT_LONG, SUPPORT_PULLBACK_MIN_VOLUME_RATIO_LONG,
    SUPPORT_PULLBACK_RSI_LONG_MIN_ENHANCED,
)
import core.config as _core_config

# Ensure runtime config edits are respected when this module is reloaded during tests
STRUCTURED_SUPPORT_NEAR_ATR = getattr(_core_config, "STRUCTURED_SUPPORT_NEAR_ATR", STRUCTURED_SUPPORT_NEAR_ATR)
from core.indicators import bars_since_supertrend_flip
from core.config import (
    ADX_DECLINE_LOOKBACK_BARS, ADX_DECLINE_MIN_DROP, ADX_DECLINE_MIN_DROP_RATIO,
    KC_TOUCH_LOOKBACK_BARS,
    MAINSTREAM_SYMBOLS, VOLUME_DIVERGENCE_LOOKBACK_BARS, VOLUME_DIVERGENCE_MAX_RATIO,
    PRICE_NEAR_SUPPORT_PCT,
)


def has_volume_divergence(df: pd.DataFrame, want_dir: int) -> bool:
    """價格仍創新高/新低，但成交量較前段明顯萎縮 -> 量縮背離（主力收手）。

    把最近 VOLUME_DIVERGENCE_LOOKBACK_BARS 根拆成前後兩半：
      多單（探底）：後半段的最低價 <= 前半段最低價，但後半段平均量能
      明顯低於前半段 -> 底部量縮，賣壓竭盡。
      空單（探頂）：後半段的最高價 >= 前半段最高價，但後半段平均量能
      明顯低於前半段 -> 頂部量縮，買盤竭盡。
    """
    if len(df) < VOLUME_DIVERGENCE_LOOKBACK_BARS:
        return False
    window = df.iloc[-VOLUME_DIVERGENCE_LOOKBACK_BARS:]
    half = VOLUME_DIVERGENCE_LOOKBACK_BARS // 2
    early, recent = window.iloc[:half], window.iloc[half:]
    early_volume = float(early['volume'].mean())
    if early_volume <= 0:
        return False
    volume_shrinking = float(recent['volume'].mean()) <= early_volume * VOLUME_DIVERGENCE_MAX_RATIO
    if not volume_shrinking:
        return False
    if want_dir == 1:
        return float(recent['low'].min()) <= float(early['low'].min())
    return float(recent['high'].max()) >= float(early['high'].max())

def detect_macd_divergence(df: pd.DataFrame, side: str, lookback: int = 30) -> bool:
    if not getattr(_core_config, "ENABLE_MACD_DIVERGENCE_FILTER", True):
        return False
    if len(df) < lookback + 5:
        return False
    closes = df['close'].values
    macd_hists = df['macd_hist'].values
    
    if side == "LONG":
        # Bullish divergence: price is making new lows but MACD hist is rising
        min_price_idx = -lookback + np.argmin(closes[-lookback:-3])
        if closes[-1] <= closes[min_price_idx] * 1.01 and macd_hists[-1] > macd_hists[min_price_idx] + 1e-6:
            return True
    else:
        # Bearish divergence: price is making new highs but MACD hist is falling
        max_price_idx = -lookback + np.argmax(closes[-lookback:-3])
        if closes[-1] >= closes[max_price_idx] * 0.99 and macd_hists[-1] < macd_hists[max_price_idx] - 1e-6:
            return True
    return False


def is_tail_end_rebound_guard(
    df: pd.DataFrame,
    side: str,
    price: float,
    atr: float,
    volume_ratio: float,
    recent_bars: int = 8,
    near_extreme_pct: float = 0.015,
    weak_volume_ratio: float = 0.90,
) -> bool:
    """拒絕反彈尾段的最後一口：價格已接近最近極值，但量能弱且沒有延續。

    這正是你前面那幾筆最典型的敗因：價格只是回到前高/前低附近，並沒有
    形成確實的突破或持續動能，最後一筆反彈很容易在沒有延續時直接回吐，
    把前面已獲利的部位整個吞掉。
    """
    if df is None or len(df) < recent_bars:
        return False
    side = str(side).upper()
    if side not in {"LONG", "SHORT"}:
        return False
    atr = float(atr or 0.0)
    if atr <= 0:
        return False

    recent = df.iloc[-recent_bars:]
    if side == "LONG":
        recent_high = float(recent["high"].max())
        prev_high = float(recent.iloc[:-1]["high"].max()) if len(recent) > 1 else recent_high
        last_close = float(recent["close"].iloc[-1])
        close_recent = float(recent["close"].iloc[-3]) if len(recent) >= 3 else last_close
        near_extreme = price >= recent_high * (1.0 - near_extreme_pct)
        no_follow_through = (
            float(recent["high"].iloc[-1]) <= prev_high * 1.002
            and last_close <= close_recent + 0.25 * atr
        )
        weak_flow = volume_ratio < weak_volume_ratio
        return near_extreme and no_follow_through and weak_flow

    recent_low = float(recent["low"].min())
    prev_low = float(recent.iloc[:-1]["low"].min()) if len(recent) > 1 else recent_low
    last_close = float(recent["close"].iloc[-1])
    close_recent = float(recent["close"].iloc[-3]) if len(recent) >= 3 else last_close
    near_extreme = price <= recent_low * (1.0 + near_extreme_pct)
    no_follow_through = (
        float(recent["low"].iloc[-1]) >= prev_low * 0.998
        and last_close >= close_recent - 0.25 * atr
    )
    weak_flow = volume_ratio < weak_volume_ratio
    return near_extreme and no_follow_through and weak_flow


def evaluate_entry_quality_gate(
    side: str,
    price: float,
    atr: float,
    volume_ratio: float,
    score: int,
    df: pd.DataFrame | None = None,
    min_rr: float = MIN_NET_REWARD_RISK,
    min_volume_ratio: float = KELTNER_MIN_VOLUME_RATIO,
):
    """進場品質檢查：只攔截真正高風險、低價值的進場型態，而不是一刀切封死所有交易。

    目標是保留正常趨勢/高品質交易，同時排除以下高虧損潛力的情況：
      - 尾段反彈、接近極值
      - 量能弱
      - 盈虧比低
      - 高分值但無真動能
    """
    side = str(side).upper()
    if side not in {"LONG", "SHORT"}:
        return {"blocked": False, "reason": "side invalid"}

    price = float(price or 0.0)
    atr = float(atr or 0.0)
    if price <= 0 or atr <= 0:
        return {"blocked": False, "reason": "price/atr invalid", "kind": "skip"}

    volume_ratio = float(volume_ratio or 0.0)
    if volume_ratio < float(min_volume_ratio):
        # 低量能不一定全都該擋，但當它連同尾端反彈、RR 低等條件同時出現時，
        # 才判定為高風險進場；否則讓正常趨勢進場仍可存在。
        if score >= 80 and df is not None and is_tail_end_rebound_guard(
            df=df, side=side, price=price, atr=atr, volume_ratio=volume_ratio
        ):
            return {
                "blocked": True,
                "reason": f"量能不足且接近尾端反彈：{volume_ratio:.2f}x < {float(min_volume_ratio):.2f}x，拒絕開倉（分數 {score}）",
                "kind": "volume_tailend",
            }
        return {"blocked": False, "reason": "weak volume but not tail-end risk", "kind": "volume_soft_skip"}

    sl_distance = max(atr * STOP_LOSS_MULTIPLIER, price * MIN_SL_DISTANCE_PCT)
    tp_distance = max(atr * TAKE_PROFIT_MULTIPLIER, sl_distance * min_rr)
    sl_price = price - sl_distance if side == "LONG" else price + sl_distance
    reward_pct = tp_distance / price
    net_rr, _, _ = compute_net_reward_risk(price, sl_price, reward_pct)
    if net_rr < float(min_rr):
        # 低 RR 只在分數高且進場風險明確的情況下攔截；正常高品質價值交易不被一刀切。
        if score >= 80 and df is not None and is_tail_end_rebound_guard(
            df=df, side=side, price=price, atr=atr, volume_ratio=volume_ratio
        ):
            return {
                "blocked": True,
                "reason": f"盈虧比不足且接近尾端反彈：淨風報比 {net_rr:.2f}:1 < {float(min_rr):.2f}:1，拒絕開倉（分數 {score}）",
                "kind": "rr_tailend",
            }
        return {"blocked": False, "reason": "low RR but not tail-end risk", "kind": "rr_soft_skip"}

    return {"blocked": False, "reason": "quality ok", "kind": "pass"}


def compute_pullback_target(
    kc_edge: float, ema_20: float, atr: float, side: str, score: int
) -> tuple[float, float, bool]:
    """Return (target, pullback_distance, has_enough_room) using one shared rule."""
    depth = get_pullback_target_depth(score)
    span = abs(float(kc_edge) - float(ema_20))
    min_distance = max(0.0, float(atr) * PULLBACK_TARGET_MIN_ATR_MULT)
    if span + 1e-12 < min_distance:
        return float(kc_edge), span, False
    distance = min(span, max(span * depth, min_distance))
    target = (
        float(kc_edge) - distance
        if str(side).upper() == "LONG"
        else float(kc_edge) + distance
    )
    return target, distance, True


def classify_btc_regime(
    st_direction: int, btc_direction: int, flip_age: int, symbol: str = None,
    score_penalty: int = None,
) -> dict:
    """Return BTC alignment context and block contrary trades unless explicitly allowed."""
    context = {
        "mode": "UNKNOWN",
        "score_penalty": 0,
        "allocation_factor": 1.0,
        "hard_block": False,
    }
    if not BTC_REGIME_FILTER_ENABLED or btc_direction == 0:
        return context
    if flip_age < BTC_REGIME_FLIP_BUFFER_BARS:
        context.update(mode="JUST_FLIPPED", hard_block=True)
        return context
    if str(symbol or "").replace("/", "").upper() == "BTCUSDT":
        context["mode"] = "SELF"
        return context
    contrary = (st_direction == 1 and btc_direction == -1) or (
        st_direction == -1 and btc_direction == 1
    )
    if contrary:
        if not BTC_REGIME_ALLOW_CONTRARY:
            context.update(mode="CONTRARY", hard_block=True)
            return context
        context.update(
            mode="CONTRARY",
            score_penalty=(
                BTC_REGIME_SCORE_PENALTY if score_penalty is None else max(0, int(score_penalty))
            ),
            allocation_factor=BTC_REGIME_ALLOCATION_FACTOR,
        )
    else:
        context["mode"] = "ALIGNED"
    return context


def build_sl_tp_for_side(
    price: float,
    side: str,
    sl_distance: float,
    tp_distance: float = None,
) -> tuple[float, float]:
    """依方向計算真正的 SL/TP 價格，並強制保證：
    - LONG: SL < price < TP
    - SHORT: TP < price < SL
    - TP/SL 毛風報比不低於 MIN_REWARD_RISK_RATIO。
    """
    side = str(side).upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError(f"Unsupported side: {side}")

    sl_distance = float(abs(sl_distance or 0.0))
    tp_distance = float(abs(tp_distance if tp_distance is not None else sl_distance))
    tp_distance = max(tp_distance, sl_distance * MIN_REWARD_RISK_RATIO)

    if side == "LONG":
        sl, tp = price - sl_distance, price + tp_distance
    else:
        sl, tp = price + sl_distance, price - tp_distance
    validate_sl_tp_pair(price, side, sl, tp)
    return sl, tp


def validate_sl_tp_pair(
    price: float,
    side: str,
    sl: float,
    tp: float,
    *,
    allow_profit_lock: bool = False,
) -> None:
    """驗證保護價。

    初始訂單強制套用毛風報比下限；追蹤停損已越過成本價時沒有下行風險，
    呼叫端可用 ``allow_profit_lock=True`` 驗證價位順序而不套用初始 R:R。
    """
    side = str(side).upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError(f"Unsupported side: {side}")
    price = float(price)
    sl = float(sl)
    tp = float(tp)
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"Invalid price for SL/TP validation: {price!r}")

    if sl != 0.0:
        if side == "LONG" and not (sl < price):
            raise ValueError(f"LONG SL invalid: price={price}, sl={sl}")
        if side == "SHORT" and not (sl > price):
            raise ValueError(f"SHORT SL invalid: price={price}, sl={sl}")

    # tp=0 代表不設固定止盈，由 trailing 或動態指標出場。
    if tp == 0.0:
        return

    if not all(math.isfinite(value) for value in (sl, tp)):
        raise ValueError(f"Non-finite SL/TP: sl={sl!r}, tp={tp!r}")

    if allow_profit_lock:
        if side == "LONG" and not (sl < tp):
            raise ValueError(f"LONG trailing SL must remain below TP: sl={sl}, tp={tp}")
        if side == "SHORT" and not (tp < sl):
            raise ValueError(f"SHORT trailing SL must remain above TP: sl={sl}, tp={tp}")
        return

    if side == "LONG":
        if not (sl < price < tp):
            raise ValueError(f"LONG SL/TP invalid: price={price}, sl={sl}, tp={tp}")
        gross_rr = abs(tp - price) / max(abs(price - sl), 1e-12)
    else:
        if not (tp < price < sl):
            raise ValueError(f"SHORT SL/TP invalid: price={price}, sl={sl}, tp={tp}")
        gross_rr = abs(price - tp) / max(abs(sl - price), 1e-12)
    if gross_rr + 1e-12 < MIN_REWARD_RISK_RATIO:
        raise ValueError(
            f"{side} reward/risk {gross_rr:.3f}:1 below minimum "
            f"{MIN_REWARD_RISK_RATIO:.3f}:1"
        )


def compute_sl_tp_distance(price: float, atr: float) -> tuple[float, float]:
    """算出止損/止盈距離，並套用 MIN_SL_DISTANCE_PCT 下限，避免低波動期間
    ATR 太小導致止損距離縮到容易被雜訊掃出的地步。回傳 (sl_distance, tp_distance)。

    止損可由 DISASTER_STOP_MULTIPLIER 放寬，但止盈會同步拉遠到「扣除
    進出場 taker fee 後」仍至少符合 MIN_NET_REWARD_RISK，避免表面 1:2、
    實際因放寬止損與手續費只剩約 1:1.3。公式使用較保守的較高出場名目
    金額估算手續費，因此多空方向都不會低於設定值。"""
    base_sl_distance = max(atr * STOP_LOSS_MULTIPLIER, price * MIN_SL_DISTANCE_PCT)
    sl_distance = base_sl_distance * DISASTER_STOP_MULTIPLIER
    # 上限止損距離，避免單筆止損過寬導致一次虧損吃掉整個獲利空間
    try:
        from core.config import MAX_SL_DISTANCE_PCT
        sl_distance = min(sl_distance, price * float(MAX_SL_DISTANCE_PCT))
    except Exception:
        pass
    configured_tp_distance = base_sl_distance * (
        TAKE_PROFIT_MULTIPLIER / max(STOP_LOSS_MULTIPLIER, 1e-9)
    )
    fee_rate = max(0.0, min(TAKER_FEE_RATE, 0.99))
    net_risk_per_unit = sl_distance * (1 + fee_rate) + 2 * price * fee_rate
    min_tp_distance = (
        MIN_NET_REWARD_RISK * net_risk_per_unit + 2 * price * fee_rate
    ) / max(1 - fee_rate, 1e-9)
    tp_distance = max(configured_tp_distance, min_tp_distance)
    # 即使環境誤把 ATR 倍數設反，仍維持初始毛風報比硬下限。
    tp_distance = max(tp_distance, sl_distance * MIN_REWARD_RISK_RATIO)
    return sl_distance, tp_distance


def compute_net_reward_risk(
    entry_price: float,
    sl_price: float,
    reward_pct: float,
    fee_rate: float = TAKER_FEE_RATE,
    slippage_pct: float = SLIPPAGE_PCT,
) -> tuple[float, float, float]:
    """回傳（淨風報比、淨獲利距離、淨風險距離）。

    BOUNCE 目標是以成交價百分比觸發；進場與出場手續費皆計入，另保留
    一次出場滑價。進場若是市價，呼叫端應傳實際或預估滑價後成交價。
    """
    entry = max(float(entry_price or 0.0), 0.0)
    stop_distance = abs(entry - float(sl_price or entry))
    gross_reward = entry * max(float(reward_pct or 0.0), 0.0)
    execution_cost = entry * (
        2.0 * max(float(fee_rate or 0.0), 0.0)
        + max(float(slippage_pct or 0.0), 0.0)
    )
    net_reward = max(0.0, gross_reward - execution_cost)
    net_risk = stop_distance + execution_cost
    ratio = net_reward / net_risk if net_risk > 0 else 0.0
    return ratio, net_reward, net_risk


def detect_ma7_reversal(
    df: pd.DataFrame, 
    side: str,
    ema_50_1h: float = None,
    st_direction_1h: int = None,
    btc_st_direction_1h: int = 0,
    btc_st_flip_age: int = 999,
    btc_1m_turn: str = None,
    symbol: str = None,
    parameter_overrides: dict = None,
    indicators_precomputed: bool = False,
    live_price: float = None,
    require_strict_v: bool = False,
) -> dict:
    """
    純粹 MA7/MA25 交叉 + MA7 樞軸轉折 (Stop and Reverse)
    - 多單：MA7 > MA25 且 MA7 形成 V 型谷底
    - 空單：MA7 < MA25 且 MA7 形成倒 V 型峰頂
    """
    if len(df) < 25:
        return {"detected": False, "reason": "K線資料不足25根"}

    # 確保指標已計算
    if 'ma7' not in df.columns:
        df['ma7'] = df['close'].rolling(window=7).mean()
    if 'ma25' not in df.columns:
        df['ma25'] = df['close'].rolling(window=25).mean()

    # 取值
    ma7_series = df['ma7'].dropna()
    if len(ma7_series) < 3:
        return {"detected": False, "reason": "MA7資料不足"}

    ma25_series = df['ma25'].dropna()
    if len(ma25_series) < 1:
        return {"detected": False, "reason": "MA25資料不足"}

    ma7_curr = float(ma7_series.iloc[-1])
    ma7_prev = float(ma7_series.iloc[-2])
    ma7_prev2 = float(ma7_series.iloc[-3])
    ma25_curr = float(ma25_series.iloc[-1])
    
    price = float(live_price) if live_price else float(df['close'].iloc[-1])
    atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns and not pd.isna(df['atr'].iloc[-1]) else price * 0.015

    def _no(reason: str) -> dict:
        return {"detected": False, "reason": reason, "side": side, "score": 0}

    # 進場條件：MA7 方向（只要往上就是多，往下就是空）
    # 出場由 compute_position_trigger 的 V 型確認負責
    is_trough = (ma7_curr > ma7_prev)   # MA7 往上 = 多頭
    is_peak   = (ma7_curr < ma7_prev)   # MA7 往下 = 空頭

    want_dir = 1 if str(side).upper() == "LONG" else -1

    if want_dir == 1:
        if not is_trough:
            return _no("MA7 尚未向上指")
        direction_note = "MA7向上 (LONG)"
    else:
        if not is_peak:
            return _no("MA7 尚未向下指")
        direction_note = "MA7向下 (SHORT)"

    # 完全符合，滿分通過
    score = 100
    
    # 乖離率 (作為排序優選的參考，引擎在處理分數相同時，可以依據這個乖離程度決定優先級)
    turn_sharpness = abs(ma7_curr - ma25_curr)

    return {
        "detected": True,
        "side": side,
        "score": score,
        "price": float(price),
        "atr": float(atr),
        "ma7_curr": ma7_curr,
        "ma7_prev": ma7_prev,
        "ma7_prev2": ma7_prev2,
        "ma25_curr": ma25_curr,
        "turn_sharpness": turn_sharpness,
        "early_projection": False,
        "fast_entry": False,
        "pullback_bottom_order": False,
        "entry_mode": "MA7_CROSS_PIVOT",
        "profit_profile": "TREND_EXTENSION",
        "action": "ENTER_MARKET",
        "target_price": None,
        "structural_sl": price * 0.9 if want_dir == 1 else price * 1.1,
        "is_contrarian_bottom_buy": False,
        "reason": f"Cross_Pivot_{side}｜{direction_note}｜score={score}"
    }


class SuperTrendKeltnerStrategy:
    """
    高精度量化引擎 - 回調狙擊版本 (Pullback Sniper Mode)
    核心邏輯：
    1. 底線防禦 (Mandatory)：大週期趨勢 (1h EMA50) 與 SuperTrend 方向必須一致。
    2. 動態評分 (Scoring)：Keltner 突破、量能、RSI、訊號新鮮度 進行加權評分。
    3. 90+ 使用現價 Post-Only Maker；其餘達標訊號按分數等待回踩與二次確認。
    KC 突破後動量可能已接近末段，不因分數高而在突破高點市價追入。
    """
    def __init__(self, atr_period=10, atr_multiplier=3.0, adx_period=ADX_PERIOD):
        self.atr_period = atr_period
        self.atr_multiplier = atr_multiplier
        self.adx_period = adx_period

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        high = df['high']
        low = df['low']

        # 防插針價格選擇
        if 'close_price_spike_filtered' in df.columns:
            close = df['close_price_spike_filtered'].fillna(df['close'])
        else:
            close = df['close']

        volume = df['volume']

        # ATR 計算
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=self.atr_period).mean()

        # EMAs
        df['ema_20'] = close.ewm(span=20, adjust=False).mean()
        df['ema_50'] = close.ewm(span=50, adjust=False).mean()

        # MA7
        df['ma7'] = close.rolling(window=7).mean()

        # 成交量均線
        df['vol_ma_20'] = volume.rolling(window=20).mean()

        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / (loss + 1e-9)
        df['rsi'] = 100 - (100 / (1 + rs))

        # 15m RSI 估算 (window = 14 * 3 = 42)
        gain_15m = (delta.where(delta > 0, 0)).rolling(window=42).mean()
        loss_15m = (-delta.where(delta < 0, 0)).rolling(window=42).mean()
        rs_15m = gain_15m / (loss_15m + 1e-9)
        df['rsi_15m'] = 100 - (100 / (1 + rs_15m))

        # MACD
        fast_ema = close.ewm(span=12, adjust=False).mean()
        slow_ema = close.ewm(span=26, adjust=False).mean()
        df['macd_line'] = fast_ema - slow_ema
        df['macd_signal'] = df['macd_line'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd_line'] - df['macd_signal']

        # ADX（趨勢強度濾網）：KC 突破配上低 ADX，是盤整期假突破的常見樣貌，
        # 用來在評分裡分辨「真的有趨勢動能撐著的突破」跟「雜訊型突破」。
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
        minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
        tr_smooth = tr.ewm(alpha=1 / self.adx_period, adjust=False).mean()
        plus_di = 100 * (plus_dm.ewm(alpha=1 / self.adx_period, adjust=False).mean() / (tr_smooth + 1e-9))
        minus_di = 100 * (minus_dm.ewm(alpha=1 / self.adx_period, adjust=False).mean() / (tr_smooth + 1e-9))
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
        df['adx'] = dx.ewm(alpha=1 / self.adx_period, adjust=False).mean()

        # Keltner Channels
        df['kc_upper'] = df['ema_20'] + (df['atr'] * KELTNER_ATR_MULTIPLIER)
        df['kc_lower'] = df['ema_20'] - (df['atr'] * KELTNER_ATR_MULTIPLIER)
        df['kc_width'] = df['kc_upper'] - df['kc_lower']

        # SuperTrend
        hl2 = (high + low) / 2
        basic_upper = hl2 + (self.atr_multiplier * df['atr'])
        basic_lower = hl2 - (self.atr_multiplier * df['atr'])

        final_upper = pd.Series(index=df.index, dtype=float)
        final_lower = pd.Series(index=df.index, dtype=float)
        supertrend = pd.Series(index=df.index, dtype=float)
        direction = pd.Series(index=df.index, dtype=int)

        # 遞迴起點必須是 ATR 第一個有效值的那根，不能是第 0 根：ATR 是
        # atr_period 期滾動平均，前面幾根一定是 NaN，basic_upper/lower 算出來
        # 也是 NaN。如果從第 0 根開始遞迴，下面的棘輪邏輯全部是「跟前一根
        # 比大小」，只要比較對象是 NaN，Python/pandas 的比較結果永遠是
        # False，會一路落入 else 分支維持前一根的 NaN，一路傳染到最後一根，
        # 導致 final_upper/final_lower 永遠是 NaN、方向永遠卡在初始值 1
        # （多頭），不管實際價格怎麼走都不會翻轉——這是先前空單訊號被完全
        # 堵死、新鮮度分數永遠拿不到的根本原因。
        first_valid = df['atr'].first_valid_index()
        start_pos = df.index.get_loc(first_valid) if first_valid is not None else len(df)

        for i in range(len(df)):
            if i < start_pos:
                direction.iloc[i] = 1
                continue
            if i == start_pos:
                final_upper.iloc[i] = basic_upper.iloc[i]
                final_lower.iloc[i] = basic_lower.iloc[i]
                direction.iloc[i] = 1
                supertrend.iloc[i] = final_lower.iloc[i]
                continue

            if basic_upper.iloc[i] < final_upper.iloc[i-1] or close.iloc[i-1] > final_upper.iloc[i-1]:
                final_upper.iloc[i] = basic_upper.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i-1]

            if basic_lower.iloc[i] > final_lower.iloc[i-1] or close.iloc[i-1] < final_lower.iloc[i-1]:
                final_lower.iloc[i] = basic_lower.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i-1]

            prev_dir = direction.iloc[i-1]
            if prev_dir == 1:
                if close.iloc[i] < final_lower.iloc[i]:
                    direction.iloc[i] = -1
                    supertrend.iloc[i] = final_upper.iloc[i]
                else:
                    direction.iloc[i] = 1
                    supertrend.iloc[i] = final_lower.iloc[i]
            else:
                if close.iloc[i] > final_upper.iloc[i]:
                    direction.iloc[i] = 1
                    supertrend.iloc[i] = final_lower.iloc[i]
                else:
                    direction.iloc[i] = -1
                    supertrend.iloc[i] = final_upper.iloc[i]

        df['supertrend'] = supertrend
        df['st_direction'] = direction
        return df

    def evaluate_structured_entry(
        self, df: pd.DataFrame, ema_50_1h: float = None,
        st_direction_1h: int = None, btc_st_direction_1h: int = 0,
        symbol: str = None, indicators_precomputed: bool = False,
        is_dca_check: bool = False,
    ) -> dict:
        """Three closed-bar entries without MA7: breakout, support pullback, momentum cross."""
        if len(df) < max(65, STRUCTURED_SWING_LOOKBACK + 2):
            return {"action": "HOLD", "reason": "5m K線資料不足"}
        if not indicators_precomputed:
            df = self.compute_indicators(df)
        curr, prev = df.iloc[-1], df.iloc[-2]
        
        from core.indicators import analyze_candle_pattern
        candle_pattern = analyze_candle_pattern(curr)
        
        direction = int(curr["st_direction"])
        side = "LONG" if direction == 1 else "SHORT"
        

        price = float(curr["close"])
        volume_ratio = float(curr["volume"] / curr["vol_ma_20"]) if float(curr["vol_ma_20"]) > 0 else 0.0
        quality_gate = evaluate_entry_quality_gate(
            side=side,
            price=price,
            atr=float(curr["atr"]),
            volume_ratio=volume_ratio,
            score=85,
            df=df,
        )
        if quality_gate["blocked"]:
            return {
                "action": "HOLD",
                "side": side,
                "score": 0,
                "reason": quality_gate["reason"],
                "btc_regime_mode": "ALIGNED",
                "btc_allocation_factor": 1.0,
                "volume_ratio": volume_ratio,
                "price": price,
            }
        atr = float(curr["atr"]) if not pd.isna(curr["atr"]) else price * 0.015
        volume = float(curr["volume"])
        volume_ma = float(curr["vol_ma_20"]) if not pd.isna(curr["vol_ma_20"]) else 0.0
        volume_ratio = volume / volume_ma if volume_ma > 0 else 0.0
        aligned = st_direction_1h in (None, direction)
        btc_contrary = bool(
            btc_st_direction_1h and int(btc_st_direction_1h) != direction
        )
        btc_score_penalty = BTC_REGIME_SCORE_PENALTY if btc_contrary else 0
        btc_allocation_factor = BTC_REGIME_ALLOCATION_FACTOR if btc_contrary else 1.0
        common = {
            "side": side, "price": price, "atr": atr,
            "signal_candle_low": float(curr["low"]),
            "signal_candle_high": float(curr["high"]),
            "volume_ratio": volume_ratio,
            "btc_regime_mode": "CONTRARY" if btc_contrary else "ALIGNED",
            "btc_score_penalty": btc_score_penalty,
            "btc_allocation_factor": btc_allocation_factor,
        }

        if side == "LONG":
            if "high" in df.columns and len(df) >= 25:
                recent_high = float(df.iloc[-25:-1]["high"].max())
                recent_high_distance_pct = abs(recent_high - price) / max(abs(recent_high), 1e-12)
            elif "high" in df.columns:
                recent_high = float(df["high"].max())
                recent_high_distance_pct = abs(recent_high - price) / max(abs(recent_high), 1e-12)
            else:
                recent_high_distance_pct = 1.0
            bearish_divergence = False
            if "macd_hist" in df.columns and "close" in df.columns:
                bearish_divergence = detect_macd_divergence(df, "SHORT")
            if recent_high_distance_pct <= 0.015 and bearish_divergence:
                # 拒絕在接近前高點時遇到 MACD 頂背離還繼續做多 (避免接頂)
                return {
                    "action": "HOLD",
                    "reason": f"Bearish_MACD_Divergence_Near_Recent_High(距離前高{recent_high_distance_pct:.2%}, MACD 背離顯示空頭仍強，拒絕做多)",
                    "side": side,
                    "score": 0,
                    "btc_regime_mode": "ALIGNED",
                    "btc_allocation_factor": 1.0,
                    "volume_ratio": volume_ratio,
                    "price": price,
                }
        elif side == "SHORT":
            if "high" in df.columns and len(df) >= 25:
                recent_high = float(df.iloc[-25:-1]["high"].max())
                recent_high_distance_pct = abs(recent_high - price) / max(abs(recent_high), 1e-12)
            elif "high" in df.columns:
                recent_high = float(df["high"].max())
                recent_high_distance_pct = abs(recent_high - price) / max(abs(recent_high), 1e-12)
            else:
                recent_high_distance_pct = 1.0
            recent_peak_near = recent_high_distance_pct <= 0.015
            prev_close = float(prev.get("close", price)) if prev is not None and not pd.isna(prev.get("close", price)) else price
            close_broke_prev = float(curr.get("close", price)) < prev_close
            reversal_confirmed = bool(
                close_broke_prev
                and float(curr.get("close", price)) < float(curr.get("open", price))
                and (
                    float(curr.get("rsi", 50.0)) < 50.0
                    or float(curr.get("macd_hist", 0.0)) < 0.0
                )
            )
            if recent_peak_near and not reversal_confirmed:
                curr = curr.copy()
                curr['eligibility_note'] = (
                    f"Near_Recent_High_No_Reversal_Confirmation(距離前高{recent_high_distance_pct:.2%}, 近期高點附近須先跌破前一根收盤與確認反轉)"
                )
            if "low" in df.columns and len(df) >= 25:
                recent_low = float(df.iloc[-25:-1]["low"].min())
            elif "low" in df.columns:
                recent_low = float(df["low"].min())
            else:
                recent_low = price
            recent_low_distance_pct = abs(price - recent_low) / max(abs(recent_low), 1e-12)
            bullish_divergence = False
            if "macd_hist" in df.columns and "close" in df.columns:
                bullish_divergence = detect_macd_divergence(df, "LONG")
            if recent_low_distance_pct <= 0.015 and bullish_divergence:
                # 拒絕在接近前低點時遇到 MACD 底背離還繼續做空 (避免接底)
                return {
                    "action": "HOLD",
                    "reason": f"Bullish_MACD_Divergence_Near_Recent_Low(距離近期低點{recent_low_distance_pct:.2%}, MACD 背離顯示多頭仍可反彈，拒絕空單)",
                    "side": side,
                    "score": 0,
                    "btc_regime_mode": "ALIGNED",
                    "btc_allocation_factor": 1.0,
                    "volume_ratio": volume_ratio,
                    "price": price,
                }

        # MomentumCross 只把交叉視為候選訊號。正式進場延後一根已收盤 K 棒，
        # 要求收盤價沿訊號方向續走且交叉仍有效，避免沒有延續便立即追價。
        pre_cross = df.iloc[-3]
        macd_hist_cross = (
            float(pre_cross["macd_hist"]) <= 0 < float(prev["macd_hist"])
            if side == "LONG" else float(pre_cross["macd_hist"]) >= 0 > float(prev["macd_hist"])
        )
        macd_line_cross = (
            float(pre_cross["macd_line"]) <= float(pre_cross["macd_signal"])
            and float(prev["macd_line"]) > float(prev["macd_signal"])
            and float(prev["macd_line"]) >= 0
            if side == "LONG" else
            float(pre_cross["macd_line"]) >= float(pre_cross["macd_signal"])
            and float(prev["macd_line"]) < float(prev["macd_signal"])
            and float(prev["macd_line"]) <= 0
        )
        rsi_cross = (
            float(pre_cross["rsi"]) < 50 and float(prev["rsi"]) >= STRUCTURED_RSI_LONG_TRIGGER
            if side == "LONG" else
            float(pre_cross["rsi"]) > 50 and float(prev["rsi"]) <= STRUCTURED_RSI_SHORT_TRIGGER
        )
        if ENABLE_MOMENTUM_CROSS_ENTRY and aligned and (macd_hist_cross or macd_line_cross or rsi_cross):
            trigger_still_valid = (
                (macd_hist_cross and (
                    float(curr["macd_hist"]) > 0 if side == "LONG"
                    else float(curr["macd_hist"]) < 0
                ))
                or (macd_line_cross and (
                    float(curr["macd_line"]) > float(curr["macd_signal"])
                    if side == "LONG" else
                    float(curr["macd_line"]) < float(curr["macd_signal"])
                ))
                or (rsi_cross and (
                    float(curr["rsi"]) >= STRUCTURED_RSI_LONG_TRIGGER
                    if side == "LONG" else
                    float(curr["rsi"]) <= STRUCTURED_RSI_SHORT_TRIGGER
                ))
            )
            continuation_confirmed = (
                price > float(prev["close"])
                and price > float(curr["open"])
                and trigger_still_valid
                if side == "LONG" else
                price < float(prev["close"])
                and price < float(curr["open"])
                and trigger_still_valid
            )
            if MOMENTUM_CROSS_REQUIRE_CONTINUATION and not continuation_confirmed:
                signal_close = float(prev["close"])
                direction_word = "高於" if side == "LONG" else "低於"
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "momentum_continuation_confirmed": False,
                    "reason": (
                        f"MomentumCross_{side} 等待價格延續：收盤 {price:.6g} 尚未"
                        f"{direction_word}訊號棒收盤 {signal_close:.6g}，或交叉已失效"
                    ),
                    **common,
                }

            momentum_swing = df.iloc[-(STRUCTURED_SWING_LOOKBACK + 1):-1]
            momentum_prior_high = float(momentum_swing["high"].max())
            momentum_prior_low = float(momentum_swing["low"].min())
            profit_room_pct = (
                max(0.0, (momentum_prior_high - price) / price)
                if side == "LONG" else
                max(0.0, (price - momentum_prior_low) / price)
            )
            if profit_room_pct < MOMENTUM_CROSS_MIN_PROFIT_ROOM_PCT:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "momentum_continuation_confirmed": continuation_confirmed,
                    "profit_room_pct": profit_room_pct,
                    "reason": (
                        f"MomentumCross_{side} 預估獲利空間不足：目前"
                        f"{profit_room_pct:.2%}<最低"
                        f"{MOMENTUM_CROSS_MIN_PROFIT_ROOM_PCT:.2%}，拒絕進場"
                    ),
                    **common,
                }

            triggers = []
            if macd_hist_cross or macd_line_cross:
                triggers.append("MACD交叉")
            if rsi_cross:
                triggers.append("RSI穿越50")
            trigger_text = "+".join(triggers)

            # --- K 線形態防護：過濾假突破 ---
            if side == "LONG" and candle_pattern.get("is_shooting_star"):
                return eligibility_hold(
                    f"MomentumCross_{side} 拒絕：出現流星線 (Shooting Star) 假突破"
                )
            if side == "SHORT" and candle_pattern.get("is_hammer"):
                return eligibility_hold(
                    f"MomentumCross_{side} 拒絕：出現錘頭線 (Hammer) 假突破"
                )

            return {
                "action": "ENTER_MARKET", "entry_mode": "MOMENTUM_CROSS",
                "score": 80 - btc_score_penalty,
                "momentum_continuation_confirmed": continuation_confirmed,
                "profit_room_pct": profit_room_pct,
                # MomentumCross follows an aligned 5m/1h trend. Treating it as
                # a BOUNCE position makes the short-window bounce guard close it
                # before the R-based trailing exit has a chance to run.
                "profit_profile": "TREND_EXTENSION",
                "reason": (
                    f"MomentumCross_{side}｜{trigger_text}｜價格延續確認｜"
                    f"預估空間{profit_room_pct:.2%}"
                ), **common,
            }


        # 1h EMA50 大週期趨勢過濾：開倉方向必須與 1h EMA50 大趨勢同向
        if ENABLE_1H_EMA50_FILTER and not is_dca_check and ema_50_1h is not None and ema_50_1h > 0:
            ema_lower_bound = ema_50_1h * (1.0 - STRUCTURED_1H_EMA50_TOLERANCE_PCT)
            ema_upper_bound = ema_50_1h * (1.0 + STRUCTURED_1H_EMA50_TOLERANCE_PCT)
            if side == "LONG" and price < ema_lower_bound:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "reason": f"價格低於 1h EMA50 容許區，逆勢做多拒絕開倉（價格 {price:.6g} < 下界 {ema_lower_bound:.6g}，容許{STRUCTURED_1H_EMA50_TOLERANCE_PCT:.2%}）", **common
                }
            elif side == "SHORT" and price > ema_upper_bound:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "reason": f"價格高於 1h EMA50 容許區，逆勢做空拒絕開倉（價格 {price:.6g} > 上界 {ema_upper_bound:.6g}，容許{STRUCTURED_1H_EMA50_TOLERANCE_PCT:.2%}）", **common
                }

        # 計算支撐與壓力區（基於最近 24 根已收盤 of 5m K棒）
        # PRICE_NEAR_SUPPORT_PCT=0 則停用此條件（與 evaluate_signal 保持一致）
        _sp_near_pct = float(getattr(_core_config, 'PRICE_NEAR_SUPPORT_PCT', PRICE_NEAR_SUPPORT_PCT))
        if not is_dca_check and _sp_near_pct > 0 and symbol is not None and 'low' in df.columns and 'high' in df.columns and len(df) >= 25:
            past_24_bars = df.iloc[-25:-1]
            support_level = float(past_24_bars['low'].min())
            resistance_level = float(past_24_bars['high'].max())

            # 做多：必須在支撐位 N% 內
            if side == "LONG" and price > support_level * (1.0 + _sp_near_pct):
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "reason": f"價格不在支撐區{_sp_near_pct:.0%}內（當前 {price:.6g} > 支撐 {support_level:.6g}*{1.0+_sp_near_pct:.3f}）", **common
                }

            # 做空：必須在壓力位 N% 內
            if side == "SHORT" and price < resistance_level * (1.0 - _sp_near_pct):
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "reason": f"價格不在壓力區{_sp_near_pct:.0%}內（當前 {price:.6g} < 壓力 {resistance_level:.6g}*{1.0-_sp_near_pct:.3f}）", **common
                }


        swing = df.iloc[-(STRUCTURED_SWING_LOOKBACK + 1):-1]
        prior_high = float(swing["high"].max())
        prior_low = float(swing["low"].min())
        kc_break = (
            price > float(curr["kc_upper"]) if side == "LONG"
            else price < float(curr["kc_lower"])
        )
        structure_break = price > prior_high if side == "LONG" else price < prior_low
        if ENABLE_BREAKOUT_ENTRY and aligned and (kc_break or structure_break) and volume_ratio >= STRUCTURED_VOLUME_MIN_RATIO:
            trigger = "KC上軌" if side == "LONG" and kc_break else "KC下軌" if side == "SHORT" and kc_break else "前高" if side == "LONG" else "前低"

            # 爆量只代表突破候選，不代表可直接追價。實績顯示「量能 >= 2x
            # 就給 95 分並市價進場」會在短線耗竭點取得最大倉位；所有突破
            # 統一等待 EMA20 附近回踩，以 Maker 限價單成交。
            ema20 = float(curr["ema_20"])
            from core.config import BREAKOUT_PULLBACK_ATR_MULT
            if side == "LONG":
                pullback_target = ema20 + BREAKOUT_PULLBACK_ATR_MULT * atr
                pullback_target = min(pullback_target, price * 0.9995)
            else:
                pullback_target = ema20 - BREAKOUT_PULLBACK_ATR_MULT * atr
                pullback_target = max(pullback_target, price * 1.0005)
            return {
                "action": "ENTER_LIMIT", "entry_mode": "BREAKOUT",
                "score": BREAKOUT_ENTRY_SCORE - btc_score_penalty,
                "target_price": pullback_target,
                "reason": f"Breakout_{side}｜突破{trigger}｜量能{volume_ratio:.2f}x｜爆量不追價｜等回踩@{pullback_target:.6g}",
                "prior_high": prior_high, "prior_low": prior_low, **common,
            }

        ema20 = float(curr["ema_20"])
        ema60_series = df["close"].ewm(span=60, adjust=False).mean()
        ema60 = float(ema60_series.iloc[-1])
        supports = [("5m EMA20", ema20), ("5m EMA60", ema60)]
        if ema_50_1h is not None:
            supports.append(("1h EMA50", float(ema_50_1h)))
        support_name, support_price = min(supports, key=lambda item: abs(price - item[1]))
        support_distance_atr = abs(price - support_price) / max(atr, 1e-12)
        location_memory = None
        for age in range(max(1, SUPPORT_PULLBACK_LOCATION_MEMORY_BARS)):
            row_pos = len(df) - 1 - age
            if row_pos < 0:
                break
            row = df.iloc[row_pos]
            if int(row["st_direction"]) != direction:
                continue
            row_price = float(row["close"])
            row_atr = float(row["atr"]) if not pd.isna(row["atr"]) else atr
            row_supports = [
                ("5m EMA20", float(row["ema_20"])),
                ("5m EMA60", float(ema60_series.iloc[row_pos])),
            ]
            if ema_50_1h is not None:
                row_supports.append(("1h EMA50", float(ema_50_1h)))
            row_name, row_support = min(
                row_supports, key=lambda item: abs(row_price - item[1])
            )
            row_distance = abs(row_price - row_support) / max(row_atr, 1e-12)
            if row_distance <= STRUCTURED_SUPPORT_NEAR_ATR:
                location_memory = {
                    "age": age, "name": row_name, "price": row_support,
                    "close": row_price, "distance": row_distance,
                }
                break
        near_support = location_memory is not None
        candle_open = float(curr["open"])
        candle_body_atr = abs(price - candle_open) / max(atr, 1e-12)
        macd_hist = float(curr["macd_hist"])
        prev_macd_hist = float(prev["macd_hist"])
        confirmation_memory = None
        for age in range(max(1, SUPPORT_PULLBACK_CONFIRM_MEMORY_BARS)):
            row_pos = len(df) - 1 - age
            prev_pos = row_pos - 1
            if prev_pos < 0:
                break
            row = df.iloc[row_pos]
            prior_row = df.iloc[prev_pos]
            if int(row["st_direction"]) != direction:
                continue
            row_price = float(row["close"])
            row_open = float(row["open"])
            row_high = float(row["high"])
            row_low = float(row["low"])
            row_atr = float(row["atr"]) if not pd.isna(row["atr"]) else atr
            row_range = max(row_high - row_low, 1e-12)
            row_close_location = (row_price - row_low) / row_range
            row_reversal = (
                row_price > row_open and row_close_location >= 0.60
                if side == "LONG"
                else row_price < row_open and row_close_location <= 0.40
            )
            row_hist = float(row["macd_hist"])
            prior_hist = float(prior_row["macd_hist"])
            row_macd_improving = (
                row_hist > prior_hist if side == "LONG" else row_hist < prior_hist
            )
            row_body_atr = abs(row_price - row_open) / max(row_atr, 1e-12)
            if (
                (row_reversal or row_macd_improving)
                and row_body_atr >= SUPPORT_PULLBACK_MIN_BODY_ATR_MULT
            ):
                confirmation_memory = {
                    "age": age, "reversal": row_reversal,
                    "body_atr": row_body_atr,
                }
                break
        confirmation_recent = confirmation_memory is not None
        confirmed_body_atr = (
            float(confirmation_memory["body_atr"])
            if confirmation_memory else candle_body_atr
        )
        volume_healthy = (
            volume_ma > 0
            and SUPPORT_PULLBACK_MIN_VOLUME_RATIO <= volume_ratio
            <= SUPPORT_PULLBACK_MAX_VOLUME_RATIO
        )
        rsi = float(curr["rsi"])
        previous_rsi = float(prev["rsi"])
        rsi_extreme = (
            rsi > SUPPORT_PULLBACK_RSI_LONG_MAX
            if side == "LONG" else rsi < SUPPORT_PULLBACK_RSI_SHORT_MIN
        )
        if rsi_extreme:
            boundary = (
                SUPPORT_PULLBACK_RSI_LONG_MAX
                if side == "LONG" else SUPPORT_PULLBACK_RSI_SHORT_MIN
            )
            label = "過熱" if side == "LONG" else "過冷"
            return {
                "action": "HOLD", "side": side, "score": 0,
                "reason": f"RSI{label}（目前{rsi:.1f}，界限{boundary:g}），拒絕追價進場",
                **common,
            }
        rsi_ok = (
            SUPPORT_PULLBACK_RSI_LONG_MIN <= rsi <= SUPPORT_PULLBACK_RSI_LONG_MAX and rsi > previous_rsi
            if side == "LONG"
            else SUPPORT_PULLBACK_RSI_SHORT_MIN <= rsi <= SUPPORT_PULLBACK_RSI_SHORT_MAX and rsi < previous_rsi
        )
        
        # 【改進方案】強化多頭過濾：LONG 交易勝率 (53.42%) 與止損率 (24.66%) 均顯著遜於 SHORT
        if side == "LONG":
            # 提高多頭的量能要求（防止虛假突破）
            min_volume_ratio_long = max(SUPPORT_PULLBACK_MIN_VOLUME_RATIO, SUPPORT_PULLBACK_MIN_VOLUME_RATIO_LONG)
            volume_healthy_long = volume_ma > 0 and volume_ratio >= min_volume_ratio_long
            if not volume_healthy_long:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "reason": f"多頭交易量能不足：{volume_ratio:.2f}x < {min_volume_ratio_long:.2f}x，避免虛假突破",
                    **common,
                }
            
            # 提高多頭的 RSI 進場門檻（防止追高）
            rsi_long_min_enhanced = max(SUPPORT_PULLBACK_RSI_LONG_MIN, SUPPORT_PULLBACK_RSI_LONG_MIN_ENHANCED)
            if rsi < rsi_long_min_enhanced:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "reason": f"多頭交易 RSI 門檻提高：{rsi:.1f} < {rsi_long_min_enhanced:.1f}，等待更強勢信號",
                    **common,
                }
            
            # 檢查 Keltner Channel 寬度（避免在通道極窄時進場）
            kc_width = float(curr["kc_upper"]) - float(curr["kc_lower"])
            kc_width_atr_mult = kc_width / max(atr, 1e-12)
            min_kc_width_long = KELTNER_MIN_WIDTH_ATR_MULT_LONG * atr
            if kc_width < min_kc_width_long:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "reason": f"多頭 Keltner 通道過窄保護：{kc_width_atr_mult:.2f}x ATR < {KELTNER_MIN_WIDTH_ATR_MULT_LONG:.2f}x，通道擴張才進場",
                    **common,
                }
        
        adx = float(curr["adx"]) if not pd.isna(curr["adx"]) else 0.0
        atr_pct = atr / price if price > 0 else 0.0
        quality_ok = ADX_QUALITY_MIN <= adx and MIN_ATR_PCT <= atr_pct <= MAX_ATR_PCT

        # 等待中的訊號提供「準備度」，但不冒充正式進場分數。位置與確認事件
        # 可在短期記憶視窗內組合；方向、量能、RSI與品質仍須以最新K棒通過。
        trend_points = 20 if aligned else 0
        effective_support_distance = (
            float(location_memory["distance"])
            if location_memory else support_distance_atr
        )
        location_progress = max(
            0.0,
            1.0 - max(0.0, effective_support_distance - STRUCTURED_SUPPORT_NEAR_ATR),
        )
        location_points = round(20 * location_progress)
        reversal_points = 20 if confirmation_recent else 0
        body_points = round(
            10 * min(
                confirmed_body_atr / max(SUPPORT_PULLBACK_MIN_BODY_ATR_MULT, 1e-12), 1.0
            )
        )
        if volume_ma <= 0:
            volume_progress = 0.0
        elif volume_ratio < SUPPORT_PULLBACK_MIN_VOLUME_RATIO:
            volume_progress = volume_ratio / max(SUPPORT_PULLBACK_MIN_VOLUME_RATIO, 1e-12)
        else:
            volume_progress = max(
                0.0,
                1.0 - max(0.0, volume_ratio - SUPPORT_PULLBACK_MAX_VOLUME_RATIO)
                / max(SUPPORT_PULLBACK_MAX_VOLUME_RATIO, 1e-12),
            )
        volume_points = round(10 * volume_progress)
        if not volume_healthy:
            volume_points = min(volume_points, 9)
        if side == "LONG":
            rsi_progress = min(max((rsi - (SUPPORT_PULLBACK_RSI_LONG_MIN - 10.0)) / 10.0, 0.0), 1.0)
            rsi_trending = rsi > previous_rsi
        else:
            rsi_progress = min(max(((SUPPORT_PULLBACK_RSI_SHORT_MAX + 10.0) - rsi) / 10.0, 0.0), 1.0)
            rsi_trending = rsi < previous_rsi
        rsi_points = min(10, round(7 * rsi_progress) + (3 if rsi_trending else 0))
        quality_points = (
            (5 if adx >= ADX_QUALITY_MIN else round(5 * max(adx, 0.0) / max(ADX_QUALITY_MIN, 1e-12)))
            + (5 if MIN_ATR_PCT <= atr_pct <= MAX_ATR_PCT else 0)
        )
        # 進度分可以接近滿分，但硬條件未通過時該項不得因 round() 進位
        # 成滿分，否則會出現「100/100 但尚缺量能／RSI」的矛盾。
        if not near_support:
            location_points = min(location_points, 19)
        if not rsi_ok:
            rsi_points = min(rsi_points, 9)
        if not quality_ok:
            quality_points = min(quality_points, 9)
        readiness_components = {
            "trend": trend_points, "location": location_points,
            "reversal": reversal_points, "body": body_points,
            "volume": volume_points, "rsi": rsi_points,
            "quality": quality_points,
        }
        readiness_score = int(sum(readiness_components.values()))
        prerequisites_ready = (
            aligned and near_support and confirmation_recent
            and volume_healthy and rsi_ok and quality_ok
        )
        if not prerequisites_ready:
            readiness_score = min(readiness_score, 99)

        if prerequisites_ready:
            # 【改進方案】高分值陷阱保護：95+ 分信號在極端波動時表現極差
            if readiness_score >= HIGH_SCORE_THRESHOLD:
                atr_pct = atr / price if price > 0 else 0.0
                if atr_pct > HIGH_SCORE_ATR_LIMIT_PCT:
                    return {
                        "action": "HOLD", "side": side, "score": 0,
                        "readiness_score": readiness_score,
                        "readiness_components": readiness_components,
                        "reason": (
                            f"高分值信號波動過大保護：準備度 {readiness_score}/100 但 ATR% "
                            f"{atr_pct:.4f} > 限制 {HIGH_SCORE_ATR_LIMIT_PCT:.4f}，"
                            f"避免在極端行情進場"
                        ),
                        **common
                    }
            
            if BOTTOM_FILTER_ENABLED:
                rsi_15m = float(curr["rsi_15m"]) if "rsi_15m" in curr and not pd.isna(curr["rsi_15m"]) else rsi
                macd_div = False
                if "macd_hist" in df.columns and "close" in df.columns:
                    macd_div = detect_macd_divergence(df, side)
                is_bottom_ok = False
                if side == "LONG":
                    is_bottom_ok = (rsi_15m <= BOTTOM_OVERSOLD_RSI_15M_LIMIT) or macd_div
                else:
                    is_bottom_ok = (rsi_15m >= BOTTOM_OVERBOUGHT_RSI_15M_LIMIT) or macd_div
                if not is_bottom_ok:
                    return {
                        "action": "HOLD", "side": side, "score": 0,
                        "readiness_score": readiness_score,
                        "readiness_components": readiness_components,
                        "reason": f"未滿足底部抄底條件：15m RSI({rsi_15m:.1f}) 未達限制，且無 MACD 背離偵測",
                        **common
                    }

            reversal_desc = (
                "收綠K反彈" if side == "LONG" else "收紅K反轉"
            ) if confirmation_memory["reversal"] else "MACD動能改善"
            memory_note = (
                f"位置{location_memory['age']}根內、確認{confirmation_memory['age']}根內"
                if location_memory["age"] or confirmation_memory["age"] else "即時確認"
            )
            entry_support_name = str(location_memory["name"])
            anchor_price = float(location_memory["close"])
            target_price = (
                min(price, anchor_price) - atr * SUPPORT_PULLBACK_MAKER_OFFSET_ATR_MULT
                if side == "LONG"
                else max(price, anchor_price) + atr * SUPPORT_PULLBACK_MAKER_OFFSET_ATR_MULT
            )
            body_score = round(min(confirmed_body_atr / 0.50, 1.0) * 4)
            support_score = round(
                max(0.0, 1.0 - effective_support_distance / max(STRUCTURED_SUPPORT_NEAR_ATR, 1e-12)) * 4
            )
            rsi_strength = (
                max(0.0, rsi - SUPPORT_PULLBACK_RSI_LONG_MIN)
                if side == "LONG"
                else max(0.0, SUPPORT_PULLBACK_RSI_SHORT_MAX - rsi)
            )
            rsi_score = round(min(rsi_strength / 8.0, 1.0) * 4)
            adx_score = round(min(max(adx - ADX_QUALITY_MIN, 0.0) / 18.0, 1.0) * 4)
            
            # --- K 線反轉形態加分 ---
            pattern_score = 0
            if side == "LONG" and candle_pattern.get("is_hammer"):
                pattern_score = 5
            elif side == "SHORT" and candle_pattern.get("is_shooting_star"):
                pattern_score = 5

            score = max(
                0,
                min(91, 75 + body_score + support_score + rsi_score + adx_score + pattern_score)
                - btc_score_penalty,
            )
            profit_room_pct = (
                max(0.0, (prior_high - target_price) / target_price)
                if side == "LONG"
                else max(0.0, (target_price - prior_low) / target_price)
            )
            # 所有進場一律要求完整獲利空間；不再讓 100/100 訊號以半倉
            # 繞過門檻，避免高準備度但只剩交易成本等級空間的低價值交易。
            # 低空間小倉例外已關閉：若獲利空間不足 1%，直接拒絕不開倉。
            candidate_capture_ratio = get_bounce_capture_ratio(score)
            high_readiness_low_room = False
            if profit_room_pct < MIN_ENTRY_PROFIT_ROOM_PCT:
                return {
                    "action": "HOLD", "side": side, "score": 0,
                    "readiness_score": readiness_score,
                    "readiness_components": readiness_components,
                    "wait_estimate": "等待前高/前低空間擴大後重新評估",
                    "profit_room_pct": profit_room_pct,
                    "reason": (
                        f"獲利空間不足：目前{profit_room_pct:.2%}<"
                        f"最低{MIN_ENTRY_PROFIT_ROOM_PCT:.2%}，拒絕低價值進場"
                    ),
                    **common,
                }
            prev_adx = float(prev["adx"]) if not pd.isna(prev["adx"]) else 0.0
            macd_expanding = (
                macd_hist > 0 and macd_hist > prev_macd_hist
                if side == "LONG"
                else macd_hist < 0 and macd_hist < prev_macd_hist
            )
            volume_recovering = (
                volume_ratio >= TREND_EXTENSION_MIN_VOLUME_RATIO
                and volume > float(prev["volume"])
            )
            is_trend_extension = (
                profit_room_pct >= TREND_EXTENSION_MIN_ROOM_PCT
                and adx > prev_adx
                and macd_expanding
                and volume_recovering
                and candle_body_atr >= TREND_EXTENSION_MIN_BODY_ATR_MULT
            )
            profit_profile = "TREND_EXTENSION" if is_trend_extension else "BOUNCE"
            profit_profile_label = "趨勢延伸" if is_trend_extension else "反彈單"
            is_bounce = profit_profile == "BOUNCE"
            bounce_capture_ratio = candidate_capture_ratio if is_bounce else 0.0
            bounce_target_pct = profit_room_pct * bounce_capture_ratio
            profit_exit_note = (
                f"預定收割{bounce_capture_ratio:.0%}於{bounce_target_pct:.2%}"
                if is_bounce else "採動態峰值停利"
            )
            rsi_arrow = "↑" if side == "LONG" else "↓"
            return {
                "action": "ENTER_LIMIT", "entry_mode": "SUPPORT_PULLBACK", "score": score,
                "readiness_score": 100, "readiness_components": readiness_components,
                "wait_estimate": "條件已完成，等待掛單成交",
                "target_price": target_price,
                "profit_profile": profit_profile,
                "profit_room_pct": profit_room_pct,
                "bounce_capture_ratio": bounce_capture_ratio,
                "bounce_target_pct": bounce_target_pct,
                "high_readiness_low_room": high_readiness_low_room,
                "reason": (
                    f"SupportPullback_{side}｜{entry_support_name}{reversal_desc}｜"
                    f"Maker@{target_price:.6g}｜量能{volume_ratio:.2f}x｜RSI={rsi:.1f}{rsi_arrow}｜"
                    f"{memory_note}｜"
                    f"{profit_profile_label}｜可用空間{profit_room_pct:.2%}｜"
                    f"{profit_exit_note}"
                    + (
                        "｜滿準備度低空間探索小倉"
                        if high_readiness_low_room else ""
                    )
                    + (
                        f"｜BTC反向扣{btc_score_penalty}分、半倉"
                        if btc_contrary else ""
                    )
                ),
                **common,
            }

        missing = []
        if not aligned:
            missing.append("5m/1h趨勢同向")
        if not near_support:
            missing.append(
                f"靠近{support_name}（目前{support_distance_atr:.2f}ATR，需≤{STRUCTURED_SUPPORT_NEAR_ATR:.2f}）"
            )
        if not confirmation_recent:
            missing.append(
                f"近{SUPPORT_PULLBACK_CONFIRM_MEMORY_BARS}根缺反轉K/MACD改善＋足夠實體"
            )
        if not volume_healthy:
            missing.append(
                f"量能（目前{volume_ratio:.3f}x，需{SUPPORT_PULLBACK_MIN_VOLUME_RATIO:.2f}–{SUPPORT_PULLBACK_MAX_VOLUME_RATIO:.2f}x）"
            )
        if not rsi_ok:
            rsi_target = (
                SUPPORT_PULLBACK_RSI_LONG_MIN
                if side == "LONG" else SUPPORT_PULLBACK_RSI_SHORT_MAX
            )
            rsi_arrow = "上升" if side == "LONG" else "下降"
            missing.append(f"RSI達{rsi_target:g}且{rsi_arrow}（目前{rsi:.1f}）")
        if not quality_ok:
            missing.append(f"ADX/ATR品質（ADX {adx:.1f}，ATR {atr_pct:.2%}）")

        if not aligned or not quality_ok:
            wait_estimate = "趨勢或品質未通過，暫時無法估時"
        else:
            distance_bars = math.ceil(
                max(0.0, support_distance_atr - STRUCTURED_SUPPORT_NEAR_ATR) / 0.25
            )
            estimate_bars = min(12, max(1, distance_bars))
            wait_estimate = (
                f"最快約{estimate_bars * 5}–{(estimate_bars + 1) * 5}分鐘"
                f"（至少{estimate_bars}根5m收盤，僅估計）"
            )
        missing_text = "、".join(missing[:4])
        if len(missing) > 4:
            missing_text += f"，另{len(missing) - 4}項"
        if btc_contrary:
            missing_text += (
                f"｜BTC方向相反僅扣{btc_score_penalty}分、"
                f"倉位×{btc_allocation_factor:.2f}，不阻擋"
            )

        return {
            "action": "HOLD", "side": side, "score": 0,
            "readiness_score": readiness_score,
            "readiness_components": readiness_components,
            "wait_estimate": wait_estimate,
            "reason": f"尚缺：{missing_text}｜{wait_estimate}", **common,
        }

    def evaluate_signal(
        self, df: pd.DataFrame,
        ema_50_1h: float = None,
        trend_1h_declining: bool = False,
        st_direction_1h: int = None,
        btc_st_direction_1h: int = 0,
        btc_st_flip_age: int = 999,
        symbol: str = None,
        parameter_overrides: dict = None,
        indicators_precomputed: bool = False,
    ) -> dict:
        if len(df) < 50:
            return {
                "action": "HOLD", "reason": "Not enough data",
                "eligible": False, "score_stage": "ELIGIBILITY",
            }

        if not indicators_precomputed:
            df = self.compute_indicators(df)
        curr = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else None
        
        from core.indicators import analyze_candle_pattern
        candle_pattern = analyze_candle_pattern(curr)
        pattern_name = candle_pattern.get("pattern_name", "None")
        
        overrides = dict(parameter_overrides or {})
        volume_min_ratio = float(overrides.get("volume_min_ratio", KELTNER_MIN_VOLUME_RATIO))
        volume_ratio = float(curr["volume"] / curr["vol_ma_20"]) if float(curr["vol_ma_20"]) > 0 else 0.0
        st_dir = int(curr['st_direction'])
        quality_gate = evaluate_entry_quality_gate(
            side="LONG" if st_dir == 1 else "SHORT",
            price=float(curr['close']),
            atr=float(curr['atr']),
            volume_ratio=volume_ratio,
            score=85,
            df=df,
            min_volume_ratio=volume_min_ratio,
        )
        if quality_gate["blocked"]:
            return {
                "action": "HOLD",
                "reason": quality_gate["reason"],
                "eligible": False,
                "score_stage": "ELIGIBILITY",
                "diagnostics": {
                    "price": float(curr['close']),
                    "atr": float(curr['atr']),
                    "atr_pct": float(curr['atr'] / curr['close']) if float(curr['close']) > 0 else 0.0,
                    "rsi": float(curr['rsi']),
                    "adx": float(curr['adx']) if not pd.isna(curr['adx']) else 0.0,
                    "volume_ratio": volume_ratio,
                    "candle_pattern": pattern_name,
                },
            }
        atr_min_pct = float(overrides.get("atr_min_pct", MIN_ATR_PCT))
        rsi_long_max = float(overrides.get("rsi_long_max", RSI_LONG_MAX))
        rsi_short_min = float(overrides.get("rsi_short_min", RSI_SHORT_MIN))
        btc_score_penalty = overrides.get("btc_score_penalty")

        # --- 基本數據提取 ---
        price = curr['close_price_spike_filtered'] if ('close_price_spike_filtered' in curr and not pd.isna(curr['close_price_spike_filtered'])) else curr['close']
        atr = curr['atr'] if not np.isnan(curr['atr']) else price * 0.015
        rsi = curr['rsi']
        adx = curr['adx'] if not np.isnan(curr['adx']) else 0.0
        vol = curr['volume']
        vol_ma_20 = curr['vol_ma_20'] if not np.isnan(curr['vol_ma_20']) else 0
        kc_upper = curr['kc_upper']
        kc_lower = curr['kc_lower']
        kc_width = curr['kc_width'] if not np.isnan(curr['kc_width']) else (price * 0.03)
        ema_20 = curr['ema_20'] if not np.isnan(curr['ema_20']) else price
        ema_50 = curr['ema_50'] if not np.isnan(curr['ema_50']) else price

        def signal_diagnostics() -> dict:
            return {
                "price": float(price),
                "atr": float(atr),
                "atr_pct": float(atr / price) if price > 0 else 0.0,
                "rsi": float(rsi),
                "adx": float(adx),
                "volume_ratio": float(vol / vol_ma_20) if vol_ma_20 > 0 else 0.0,
                "ema_20": float(ema_20),
                "ema_50_1h": float(ema_50_1h) if ema_50_1h is not None else None,
                "st_direction_5m": int(st_dir),
                "st_direction_1h": int(st_direction_1h) if st_direction_1h is not None else None,
                "btc_direction_1h": int(btc_st_direction_1h or 0),
                "btc_flip_age": int(btc_st_flip_age),
                "candle_pattern": pattern_name,
            }

        def eligibility_hold(reason: str) -> dict:
            return {
                "action": "HOLD", "reason": reason,
                "eligible": False, "score_stage": "ELIGIBILITY",
                "diagnostics": signal_diagnostics(),
            }

        # --- 1. 底線防禦 (Mandatory Filters) ---

        st_dir = curr['st_direction']

        # 計算支撐與壓力區（基於最近 24 根已收盤的 5m K棒）
        # PRICE_NEAR_SUPPORT_PCT：做多要求現價在支撐位 N% 以內；做空要求現價在壓力位 N% 以內。
        # 設為 0（或負值）則完全停用此條件。預設 8%，比原本 3% 寬鬆，避免趨勢延伸時永遠進不了場。
        _near_pct = float(getattr(_core_config, 'PRICE_NEAR_SUPPORT_PCT', PRICE_NEAR_SUPPORT_PCT))
        if _near_pct > 0 and symbol is not None and 'low' in df.columns and 'high' in df.columns and len(df) >= 25:
            past_24_bars = df.iloc[-25:-1]
            support_level = float(past_24_bars['low'].min())
            resistance_level = float(past_24_bars['high'].max())

            # 做多：必須在支撐位 N% 內
            if st_dir == 1 and price > support_level * (1.0 + _near_pct):
                return eligibility_hold(
                    f"Mandatory_Fail: Price_Not_Near_Support({price:.6g}>{support_level:.6g}*{1.0+_near_pct:.3f})"
                )

            # 做空：必須在壓力位 N% 內
            if st_dir == -1 and price < resistance_level * (1.0 - _near_pct):
                return eligibility_hold(
                    f"Mandatory_Fail: Price_Not_Near_Resistance({price:.6g}<{resistance_level:.6g}*{1.0-_near_pct:.3f})"
                )

        # 層 A：BTC 大盤風險調整。剛翻轉仍暫停；方向相反改為扣分與縮倉，
        # 讓真正相對強勢的個幣仍可在通過其餘品質與回踩確認後進場。
        btc_regime = classify_btc_regime(
            st_dir, btc_st_direction_1h, btc_st_flip_age, symbol=symbol,
            score_penalty=btc_score_penalty,
        )
        if btc_regime["hard_block"]:
            block_reason = (
                "BTC_1h_ST_Contrary"
                if btc_regime["mode"] == "CONTRARY"
                else f"BTC_1h_ST_JustFlipped({btc_st_flip_age}bars<{BTC_REGIME_FLIP_BUFFER_BARS})"
            )
            return eligibility_hold(f"Mandatory_Fail: {block_reason}")

        # 層 B：個幣自身 1h SuperTrend 方向對齊
        # 1h SuperTrend 翻轉需要較長時間，比 price vs EMA50 準確 3~5 倍。
        # 既然進場是以大方向作為風控，當 1h ST 與 5m 顯示相反方向時，
        # 需直接擋單，避免在高週期逆勢中執行 5m MomentumCross 追價。
        if st_direction_1h is not None and int(st_direction_1h) != 0 and st_dir != int(st_direction_1h):
            return eligibility_hold(
                f"Mandatory_Fail: MomentumCross_Not_Aligned(5m={st_dir},1h={int(st_direction_1h)})"
            )

        # 近期高/低點附近不允許直接反手開倉：若價格仍站在最近極值附近，
        # 但 MACD 仍顯示相反方向背離，直接拒絕開單，避免大幅反向追單。
        if st_dir == 1:
            if "high" in df.columns and len(df) >= 25:
                recent_high = float(df.iloc[-25:-1]["high"].max())
                recent_high_distance_pct = abs(recent_high - price) / max(abs(recent_high), 1e-12)
            elif "high" in df.columns:
                recent_high = float(df["high"].max())
                recent_high_distance_pct = abs(recent_high - price) / max(abs(recent_high), 1e-12)
            else:
                recent_high_distance_pct = 1.0
            bearish_divergence = False
            if "macd_hist" in df.columns and "close" in df.columns:
                bearish_divergence = detect_macd_divergence(df, "SHORT")
            if recent_high_distance_pct <= 0.015 and bearish_divergence:
                # 不再做硬性擋單，改為在分數/診斷中標記為品質下降，讓 scoring 決定是否開倉
                # 將理由加入診斷以便日誌觀察
                curr_reason = f"Bearish_MACD_Divergence_Near_Recent_High(距離前高{recent_high_distance_pct:.2%}, MACD 背離顯示空頭仍強，拒絕做多)"
                # attach to diagnostics via an ad-hoc key
                curr = curr.copy()
                curr['eligibility_note'] = curr_reason
        elif st_dir == -1:
            if "high" in df.columns and len(df) >= 25:
                recent_high = float(df.iloc[-25:-1]["high"].max())
                recent_high_distance_pct = abs(recent_high - price) / max(abs(recent_high), 1e-12)
            elif "high" in df.columns:
                recent_high = float(df["high"].max())
                recent_high_distance_pct = abs(recent_high - price) / max(abs(recent_high), 1e-12)
            else:
                recent_high_distance_pct = 1.0
            recent_peak_near = recent_high_distance_pct <= 0.015
            prev_close = float(prev.get("close", price)) if prev is not None and not pd.isna(prev.get("close", price)) else price
            close_broke_prev = float(curr.get("close", price)) < prev_close
            reversal_confirmed = bool(
                close_broke_prev
                and float(curr.get("close", price)) < float(curr.get("open", price))
                and (
                    float(curr.get("rsi", 50.0)) < 50.0
                    or float(curr.get("macd_hist", 0.0)) < 0.0
                )
            )
            if recent_peak_near and not reversal_confirmed:
                curr_reason = f"Near_Recent_High_No_Reversal_Confirmation(距離前高{recent_high_distance_pct:.2%}, 近期高點附近須先跌破前一根收盤與確認反轉)"
                curr = curr.copy()
                curr['eligibility_note'] = curr_reason
            if "low" in df.columns and len(df) >= 25:
                recent_low = float(df.iloc[-25:-1]["low"].min())
            elif "low" in df.columns:
                recent_low = float(df["low"].min())
            else:
                recent_low = price
            recent_low_distance_pct = abs(price - recent_low) / max(abs(recent_low), 1e-12)
            bullish_divergence = False
            if "macd_hist" in df.columns and "close" in df.columns:
                bullish_divergence = detect_macd_divergence(df, "LONG")
            if recent_low_distance_pct <= 0.015 and bullish_divergence:
                curr_reason = f"Bullish_MACD_Divergence_Near_Recent_Low(距離近期低點{recent_low_distance_pct:.2%}, MACD 背離顯示多頭仍可反彈，拒絕空單)"
                curr = curr.copy()
                curr['eligibility_note'] = curr_reason

        # 層 C：1h EMA50 輔助確認（第三道防線）
        # 1h SuperTrend 覆蓋不到的邊緣情況（如剛翻轉尚未展開），
        # 這一層確保價格需明顯站穩 EMA50 同側才允許。
        # 1h EMA50 方向檢查已禁用，允許逆勢進場

        # 防線 3：ADX 硬性最低門檻 — ADX 低於此值代表市場完全無趨勢動能，
        # 盤整期假突破發生率最高，直接 HOLD 不進入評分系統。
        # 注意：ADX 衰退擋單（D2，見下方）是另一種機制，兩者互補不衝突：
        # 這裡擋「太低」，D2 擋「方向沒變但動能在退潮」。
        if adx < ADX_MANDATORY_MIN:
            return eligibility_hold(f"Mandatory_Fail: ADX_Too_Low({adx:.1f}<{ADX_MANDATORY_MIN})")

        # 波動率過濾：ATR 佔價格比例太高或太低都不開倉。
        # 太高：SL/TP 用 ATR 倍數算出來的停損距離會被放大，同樣倉位金額下
        # 觸發止損時虧的錢遠大於移動止利能鎖住的獲利，是最大幾筆虧損的
        # 共同特徵。太低：市場太安靜時的「突破」更可能是盤整區間的假突破，
        # 沒有真實動能支撐，容易一進場就反轉（實測一批止損反推 ATR 只有
        # 0.07%~0.21%）。兩者一起框出一個波動適中的可交易區間。
        atr_pct = atr / price if price > 0 else 0
        if atr_pct > MAX_ATR_PCT:
            return eligibility_hold(f"Mandatory_Fail: ATR_Too_High({atr_pct:.2%})")
        if atr_pct < atr_min_pct:
            return eligibility_hold(f"Mandatory_Fail: ATR_Too_Low({atr_pct:.2%}<{atr_min_pct:.2%})")

        # 極端 RSI 代表行情已經過熱／過冷，不是更高品質的追價訊號。
        if st_dir == 1 and rsi > rsi_long_max:
            return eligibility_hold(f"Mandatory_Fail: RSI_Overbought({rsi:.1f}>{rsi_long_max:.1f})")
        if st_dir == -1 and rsi < rsi_short_min:
            return eligibility_hold(f"Mandatory_Fail: RSI_Oversold({rsi:.1f}<{rsi_short_min:.1f})")


        # --- 2. 動態評分系統 (Scoring System) ---
        score = 0
        score_details = []

        # A. Keltner 突破分數 (30分) + 收盤確認防假突破
        kc_breakout_buffer = kc_width * KELTNER_BREAKOUT_MARGIN_PCT
        # 防線 1：KC 突破收盤確認 — 取倒數第 2、3 根「已收盤」K 棒（iloc[-3:-1]）
        # 檢查收盤價是否仍在 KC 通道外，最新那根 iloc[-1] 可能尚未收盤不計入。
        # 這樣可過濾「影線剛碰到通道邊界但K棒尚未收出確認」的假突破訊號。
        past_slice = df.iloc[-3:-1]  # 前兩根已收盤K棒
        if len(past_slice) >= 1:
            if st_dir == 1:
                closed_confirmed = int((past_slice['close'] >= (past_slice['kc_upper'] + kc_breakout_buffer)).sum()) >= BREAKOUT_CONFIRM_BARS
            else:
                closed_confirmed = int((past_slice['close'] <= (past_slice['kc_lower'] - kc_breakout_buffer)).sum()) >= BREAKOUT_CONFIRM_BARS
        else:
            closed_confirmed = False  # 資料不足，保守處理視為未確認

        kc_breakout_passed = False
        if st_dir == 1 and price >= (kc_upper + kc_breakout_buffer) and closed_confirmed:
            score += 30
            kc_breakout_passed = True
            score_details.append("KC_Breakout_Pass")
        elif st_dir == -1 and price <= (kc_lower - kc_breakout_buffer) and closed_confirmed:
            score += 30
            kc_breakout_passed = True
            score_details.append("KC_Breakout_Pass")
        elif not closed_confirmed:
            score_details.append(f"KC_Breakout_NoClose({BREAKOUT_CONFIRM_BARS}bar_required)")
        else:
            score_details.append("KC_Breakout_Fail")

        # B. 量能確認分數 (20分)
        if vol_ma_20 > 0 and vol >= (vol_ma_20 * volume_min_ratio):
            score += 20
            score_details.append("Volume_Pass")
        else:
            score_details.append("Volume_Fail")

        # C. RSI 強勢分數 (20分)
        if st_dir == 1 and rsi >= RSI_LONG_THRESHOLD:
            score += 20
            score_details.append("RSI_Pass")
        elif st_dir == -1 and rsi <= RSI_SHORT_THRESHOLD:
            score += 20
            score_details.append("RSI_Pass")
        else:
            score_details.append("RSI_Fail")

        # D. 訊號新鮮度分數：初始突破只占 18/100，避免「方向尚未翻轉」
        # 跟真正 KC 突破同為 30 分，把老趨勢灌成不具預測力的 91+ 高分。
        # freshness_health_score 保留原本 30 分尺度，僅供老化硬門檻使用。
        st_flip_age = bars_since_supertrend_flip(df['st_direction'])
        freshness_ratio = max(0.0, 1.0 - st_flip_age / FRESHNESS_DECAY_BARS) if FRESHNESS_DECAY_BARS > 0 else 0.0
        freshness_health_score = round(freshness_ratio * 30)
        freshness_score = round(freshness_ratio * ENTRY_FRESHNESS_SCORE_MAX)
        score += freshness_score
        score_details.append(f"Freshness({st_flip_age}bars)+{freshness_score}")

        # D2. ADX 動能衰退檢查：SuperTrend 方向沒翻轉不代表動能沒有在退潮——
        # 實測 AAVE/USDT 進場前 8 根 5 分K，ADX 從 19.51 一路降到 14.67 才
        # 進場，方向沒變、新鮮度分數也還高，但這正是「末端趨勢」的典型樣貌：
        # 動能已經在衰退，只是方向還沒真的反轉。ADX 現在比 N 根K棒前低，
        # 且已經低於 WEAK_ENERGY_ADX_THRESHOLD，代表這不是「本來就安靜」
        # 而是「正在退潮」，直接擋單。絕對門檻原本用 ADX_QUALITY_MIN(15)，
        # 但實測 ONDO/USDT 這筆 ADX 從 36.3 衰退到 20 左右進場，衰退幅度
        # 很明顯卻因為還沒低於15分而沒被擋到，改用 WEAK_ENERGY_ADX_THRESHOLD
        # (22)，跟槓桿封頂共用同一套「能量」標準。
        adx_lookback_idx = len(df) - 1 - ADX_DECLINE_LOOKBACK_BARS
        adx_prior = df['adx'].iloc[adx_lookback_idx] if adx_lookback_idx >= 0 else np.nan
        adx_drop = (adx_prior - adx) if not pd.isna(adx_prior) else 0.0
        adx_declining = (
            not pd.isna(adx_prior)
            and adx_drop >= max(ADX_DECLINE_MIN_DROP, adx_prior * ADX_DECLINE_MIN_DROP_RATIO)
        )
        adx_declining_exhausted = adx_declining and adx < WEAK_ENERGY_ADX_THRESHOLD

        # D3. 價格乖離檢查：價格距離 EMA20 太遠（用 ATR 正規化衡量），代表
        # 這波已經漲/跌很多才追進場，均值回歸風險高，容易一進場就被拉回。
        # 跟 KC 突破（E4/A）是兩件事——KC 突破只要求價格超出通道邊界一點，
        # 這裡抓的是「超出太多」的極端情況。
        ema20_distance_atr = abs(price - ema_20) / atr if atr > 0 else 0.0
        price_overextended = ema20_distance_atr > EMA_EXTENSION_MAX_ATR_MULT

        # E. 品質細分加分（0~12分）：讓同樣達標 70/80 分的訊號能再分出優劣，
        # 用於同一輪多個候選訊號時挑選最優的下單，而不是隨機/先到先進場。
        # 四項各佔 0~3 分，數值越好加分越多，實測跟虧損大小/勝率相關：
        quality_bonus = 0
        # E1. 波動品質：越接近 [MIN_ATR_PCT, MAX_ATR_PCT] 區間的中點分數越高，
        # 越靠近任一邊界（太安靜或太劇烈）分數越低。不能只獎勵「越低越好」，
        # 因為太低的 ATR 反而是假突破風險（見上面的 Mandatory_Fail 說明）。
        atr_mid = (atr_min_pct + MAX_ATR_PCT) / 2.0
        atr_half_range = (MAX_ATR_PCT - atr_min_pct) / 2.0
        atr_quality = (
            max(0.0, 1.0 - abs(atr_pct - atr_mid) / atr_half_range)
            if atr_half_range > 0 else 0.0
        )
        quality_bonus += round(atr_quality * 3)
        # E2. RSI 品質：獎勵健康動能區中段，不再讓越極端的 RSI 得分越高。
        rsi_ideal = 60.0 if st_dir == 1 else 40.0
        rsi_half_width = max(
            rsi_ideal - RSI_LONG_THRESHOLD if st_dir == 1 else RSI_SHORT_THRESHOLD - rsi_ideal,
            1.0,
        )
        rsi_quality = max(0.0, 1.0 - abs(rsi - rsi_ideal) / rsi_half_width)
        quality_bonus += round(rsi_quality * 3)
        # E3. 量能強度：超過門檻越多代表確認度越高（多 1 倍視為滿分）
        vol_ratio = (vol / vol_ma_20) if vol_ma_20 > 0 else 0.0
        vol_margin = max(0.0, vol_ratio - volume_min_ratio)
        quality_bonus += round(min(vol_margin / 1.0, 1.0) * 3)
        # E4. 趨勢強度（ADX）：ADX 越高代表越像真的有趨勢動能撐著，越低越像
        # 盤整期雜訊——KC 突破配上低 ADX，正是假突破最常見的樣貌之一。
        # ADX_QUALITY_MIN 以下不加分，ADX_QUALITY_FULL 以上視為滿分。
        adx_ratio = (adx - ADX_QUALITY_MIN) / (ADX_QUALITY_FULL - ADX_QUALITY_MIN)
        quality_bonus += round(min(max(adx_ratio, 0.0), 1.0) * 3)
        # ADX 仍高於品質底線時，下降只代表趨勢強度轉弱，不能直接取消；
        # 輕扣 1 分品質，保留高分訊號進入回踩確認。低於底線才由硬性規則攔截。
        adx_decline_soft_penalty = 1 if adx_declining and not adx_declining_exhausted else 0
        if adx_decline_soft_penalty:
            quality_bonus = max(0, quality_bonus - adx_decline_soft_penalty)
            score_details.append(
                f"ADX_Declining_Soft-1({adx:.1f}<{adx_prior:.1f};floor={WEAK_ENERGY_ADX_THRESHOLD:.1f})"
            )

        score += quality_bonus
        if quality_bonus > 0:
            score_details.append(f"Quality+{quality_bonus}")

        raw_score_before_btc = min(100, score)
        score = raw_score_before_btc
        if btc_regime["score_penalty"] > 0:
            score = max(0, score - btc_regime["score_penalty"])
            score_details.append(
                f"BTC_Contrary-{btc_regime['score_penalty']}({raw_score_before_btc}→{score})"
            )
        btc_context = {
            "eligible": True,
            "score_stage": "INITIAL",
            "raw_score": raw_score_before_btc,
            "btc_adjusted_score": score,
            "score_components": {
                "kc": 30 if kc_breakout_passed else 0,
                "volume": 20 if "Volume_Pass" in score_details else 0,
                "rsi": 20 if "RSI_Pass" in score_details else 0,
                "freshness": freshness_score,
                "quality": quality_bonus,
            },
            "btc_regime_mode": btc_regime["mode"],
            "btc_direction_1h": int(btc_st_direction_1h or 0),
            "btc_score_penalty": btc_regime["score_penalty"],
            "btc_allocation_factor": btc_regime["allocation_factor"],
            "btc_pre_penalty_score": raw_score_before_btc,
            "diagnostics": signal_diagnostics(),
        }

        def scored_hold(reason: str) -> dict:
            return {"action": "HOLD", "score": score, "reason": reason, **btc_context}

        # --- 3. 回調狙擊最終決策 (Pullback Sniper Mode) ---
        # 修正核心：KC 突破是「訊號觸發」，等價格回踩 KC 軌道後才是「進場時機」
        # 進場門檻：總分 >= MIN_SCORE_THRESHOLD（預設 75 分）
        # 額外防線：新鮮度子分數太低（趨勢已經很舊）直接擋單，不管總分靠
        # 其他項目湊得多高——避免「已經開始老化、快要反轉的趨勢尾端，
        # 靠其他項目湊夠分數壓線擠進場」這種樣貌。
        if score >= MIN_SCORE_THRESHOLD and not kc_breakout_passed:
            return scored_hold(
                f"Mandatory_Fail: KC_Breakout_Unconfirmed | Score({score}) | {', '.join(score_details)}"
            )

        if score >= MIN_SCORE_THRESHOLD and quality_bonus < ENTRY_MIN_QUALITY_BONUS:
            return scored_hold(
                f"Mandatory_Fail: Entry_Quality_Too_Low"
                f"({quality_bonus}<{ENTRY_MIN_QUALITY_BONUS}) | Score({score}) | "
                f"{', '.join(score_details)}"
            )

        if score >= MIN_SCORE_THRESHOLD and freshness_health_score < MIN_FRESHNESS_SCORE:
            return scored_hold(
                f"Mandatory_Fail: Freshness_Too_Stale({st_flip_age}bars) | Score({score}) | {', '.join(score_details)}"
            )

        # 額外防線：只有 ADX 已跌破品質底線且持續衰退才硬擋。ADX 仍在
        # 品質底線以上時已於品質分輕扣 1 分，不取消仍具強度的趨勢訊號。
        if score >= MIN_SCORE_THRESHOLD and adx_declining_exhausted:
            return scored_hold(
                f"Mandatory_Fail: ADX_Declining_Exhaustion({adx:.1f}<{adx_prior:.1f}) | Score({score}) | {', '.join(score_details)}"
            )

        # 額外防線：價格已經乖離 EMA20 太遠（見上面 D3），代表這波已經漲/
        # 跌很多才追進場，均值回歸風險高，不管總分靠其他項目湊得多高。
        if score >= MIN_SCORE_THRESHOLD and price_overextended:
            return scored_hold(
                f"Mandatory_Fail: Price_Overextended({ema20_distance_atr:.1f}x_ATR) | Score({score}) | {', '.join(score_details)}"
            )

        # 額外防線：大週期（1h）本身的動能也在衰退（見 engine.py
        # update_1h_trend_cache 用同一批1h K線算的 ADX 衰退判斷），代表
        # 不只是5分K的小趨勢要提防，連大方向本身都已經在做頭/做底，
        # 這是5分K的新鮮度/ADX檢查看不到的更高層級末端訊號。
        if MIN_SCORE_THRESHOLD <= score < 90 and trend_1h_declining:
            return scored_hold(
                f"Mandatory_Fail: 1h_Trend_Declining | Score({score}) | {', '.join(score_details)}"
            )
        if score >= 90 and trend_1h_declining:
            # 90+ 現價 Maker 試行：高完整度突破不再被 1h ADX 衰退單獨否決。
            # 方向、ATR、RSI、KC、品質等前置資格仍已全部通過。
            score_details.append("1h_Trend_Declining_90Plus_Allowed")

        if score >= MIN_SCORE_THRESHOLD:
            pullback_depth = get_pullback_target_depth(score)
            kc_edge = kc_upper if st_dir == 1 else kc_lower
            side = "LONG" if st_dir == 1 else "SHORT"
            current_maker = score >= 90
            if current_maker:
                pullback_target = float(price)
                pullback_distance = 0.0
            else:
                pullback_target, pullback_distance, pullback_room_ok = compute_pullback_target(
                    kc_edge, ema_20, atr, side, score
                )
                if not pullback_room_ok:
                    return scored_hold(
                        f"Mandatory_Fail: Pullback_Range_Too_Narrow"
                        f"({pullback_distance / max(atr, 1e-12):.2f}ATR<"
                        f"{PULLBACK_TARGET_MIN_ATR_MULT:.2f}ATR) | Score({score}) | "
                        f"{', '.join(score_details)}"
                    )
            confirmation_reason = (
                f"ADX {adx:.1f}←{adx_prior:.1f}仍高於{WEAK_ENERGY_ADX_THRESHOLD:g}，品質-1，等待回調二次確認"
                if adx_decline_soft_penalty
                else "90+分現價Maker掛單" if current_maker
                else "等待回調至KC區後二次確認"
            )
            entry_mode = "CURRENT_MAKER" if current_maker else "PULLBACK"
            downgrade_note = " | CurrentPrice_PostOnly" if current_maker else " | MarketChase_Disabled"
            if st_dir == 1:
                dist = (price - kc_upper) / kc_upper
                return {
                    "action": "WAIT_PULLBACK", "side": "LONG",
                    "price": price, "atr": atr,
                    "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                    "target_zone": pullback_target, "ema_20": ema_20,
                    "pullback_depth": pullback_depth,
                    "pullback_distance_atr": pullback_distance / max(atr, 1e-12),
                    "entry_mode": entry_mode,
                    "confirmation_reason": confirmation_reason,
                    **btc_context,
                    "reason": f"Pullback_WAIT({score}) | dist={dist:.2%} | Target={pullback_target:.4f} | {', '.join(score_details)}{downgrade_note}"
                }
            else:  # SHORT
                dist = (kc_lower - price) / kc_lower
                return {
                    "action": "WAIT_PULLBACK", "side": "SHORT",
                    "price": price, "atr": atr,
                    "kc_upper": kc_upper, "kc_lower": kc_lower, "score": score,
                    "target_zone": pullback_target, "ema_20": ema_20,
                    "pullback_depth": pullback_depth,
                    "pullback_distance_atr": pullback_distance / max(atr, 1e-12),
                    "entry_mode": entry_mode,
                    "confirmation_reason": confirmation_reason,
                    **btc_context,
                    "reason": f"Pullback_WAIT({score}) | dist={dist:.2%} | Target={pullback_target:.4f} | {', '.join(score_details)}{downgrade_note}"
                }

        return scored_hold(f"Score_Low({score}) | {', '.join(score_details)}")

    def confirm_pullback_entry(
        self, df: pd.DataFrame, side: str, ema_1h: float = None, trend_1h_declining: bool = False,
        btc_st_direction_1h: int = 0, btc_st_flip_age: int = 999, symbol: str = None,
    ) -> dict:
        """回踩觸發當下的二次確認。

        訊號登記等待回踩時，可能已經是將近 PULLBACK_TIMEOUT_MINUTES 分鐘前
        （目前預設 20 秒）的舊資料；等價格真的回踩到目標區時，量能可能已經萎縮、RSI 可能已經
        轉弱、甚至大趨勢或 SuperTrend 方向已經反轉——這正是「假突破」最常見的
        樣貌：一開始的突破訊號成立，但等真正要進場時動能其實已經退潮。這裡用
        當下最新的一根 K 棒重新檢查核心條件是否還成立，任何一項不成立就直接
        取消這次回踩進場，而不是機械式地照著已經過期的舊訊號開倉。
        """
        if len(df) < 50:
            return {"status": "CANCEL", "reason": "K線資料不足"}

        df = self.compute_indicators(df)
        curr = df.iloc[-1]
        price = curr['close_price_spike_filtered'] if ('close_price_spike_filtered' in curr and not pd.isna(curr['close_price_spike_filtered'])) else curr['close']
        atr = curr['atr'] if not np.isnan(curr['atr']) else price * 0.015
        rsi = curr['rsi']
        vol = curr['volume']
        vol_ma_20 = curr['vol_ma_20'] if not np.isnan(curr['vol_ma_20']) else 0
        ema_20 = curr['ema_20'] if not pd.isna(curr['ema_20']) else price
        st_dir = curr['st_direction']
        want_dir = 1 if side == "LONG" else -1

        if st_dir != want_dir:
            return {
                "status": "CANCEL",
                "reason": f"SuperTrend 方向已反轉（現為{'多頭' if st_dir == 1 else '空頭'}）",
            }

        btc_regime = classify_btc_regime(
            want_dir, btc_st_direction_1h, btc_st_flip_age, symbol=symbol
        )
        if btc_regime["hard_block"]:
            reason = (
                "BTC 1h 方向背離，禁止逆大盤進場"
                if btc_regime["mode"] == "CONTRARY"
                else f"BTC 1h 剛翻轉，仍在 {btc_st_flip_age}/{BTC_REGIME_FLIP_BUFFER_BARS} 根緩衝期"
            )
            return {"status": "CANCEL", "reason": reason}

        if side == "LONG" and rsi > RSI_LONG_MAX:
            return {"status": "CANCEL", "reason": f"RSI過熱 RSI_Overbought({rsi:.1f}>{RSI_LONG_MAX:.1f})"}
        if side == "SHORT" and rsi < RSI_SHORT_MIN:
            return {"status": "CANCEL", "reason": f"RSI過冷 RSI_Oversold({rsi:.1f}<{RSI_SHORT_MIN:.1f})"}

        # 大週期（1h）本身動能衰退時，即使 5 分K條件仍成立也取消。
        if trend_1h_declining:
            return {
                "status": "CANCEL",
                "reason": "大週期(1h)動能已在衰退 1h_Trend_Declining",
            }

        # 價格乖離 EMA20 太遠：等回踩的這段時間裡價格可能又衝更遠，均值
        # 回歸風險比登記當下更高，一樣取消。
        ema20_distance_atr = abs(price - ema_20) / atr if atr > 0 else 0.0
        if ema20_distance_atr > EMA_EXTENSION_MAX_ATR_MULT:
            return {
                "status": "CANCEL",
                "reason": f"價格乖離EMA20過大 Price_Overextended({ema20_distance_atr:.1f}x_ATR)",
            }

        # 回踩跌破/突破 EMA20：健康的回調應該只是往 EMA20 靠近，不會真的
        # 穿越到對面——多單回踩時價格已經跌破 EMA20（或空單回踩時已經站
        # 上 EMA20），代表這已經不是「回調」，而是價格真的穿越均線在反轉。
        # 跟上面「乖離過大」是兩個不同方向的風險：那個抓「離 EMA20 太遠」
        # （不管在哪一側），這個抓「跑到 EMA20 錯的那一側」，兩種情況都
        # 可能發生、必須分開判斷，缺一不可。
        if side == "LONG" and price < ema_20:
            return {
                "status": "CANCEL",
                "reason": f"回踩跌破EMA20，疑似真反轉 EMA20_Breached(price={price:.6f}<ema20={ema_20:.6f})",
            }
        if side == "SHORT" and price > ema_20:
            return {
                "status": "CANCEL",
                "reason": f"回踩突破EMA20，疑似真反轉 EMA20_Breached(price={price:.6f}>ema20={ema_20:.6f})",
            }

        # 距離原始突破過了多久：方向沒反轉不代表這個突破還「新鮮」——等回踩
        # 的這段時間裡，行情可能只是在原地震盪消耗動能，SuperTrend 遲遲沒
        # 真的翻轉，但這個突破本身已經是強弩之末。跟 evaluate_signal() 的
        # 新鮮度用同一套連續淡化公式重新算一次，太舊直接取消，不是只看
        # 方向對不對。
        st_flip_age = bars_since_supertrend_flip(df['st_direction'])
        freshness_ratio = max(0.0, 1.0 - st_flip_age / FRESHNESS_DECAY_BARS) if FRESHNESS_DECAY_BARS > 0 else 0.0
        freshness_score = round(freshness_ratio * 30)
        if freshness_score < MIN_FRESHNESS_SCORE:
            return {
                "status": "CANCEL",
                "reason": f"距離原始突破已過太久 Freshness({st_flip_age}bars)={freshness_score}<{MIN_FRESHNESS_SCORE}",
            }

        # ADX 動能衰退檢查（跟 evaluate_signal() 同一套邏輯）：方向沒反轉、
        # 新鮮度也還夠，但 ADX 現在比 N 根K棒前低且已經低於
        # WEAK_ENERGY_ADX_THRESHOLD，代表動能在等待回踩的這段時間持續
        # 衰退，一樣是末端趨勢的樣貌。
        adx = curr['adx'] if not pd.isna(curr['adx']) else 0.0
        adx_lookback_idx = len(df) - 1 - ADX_DECLINE_LOOKBACK_BARS
        adx_prior = df['adx'].iloc[adx_lookback_idx] if adx_lookback_idx >= 0 else np.nan
        adx_drop = (adx_prior - adx) if not pd.isna(adx_prior) else 0.0
        adx_declining = (
            not pd.isna(adx_prior)
            and adx_drop >= max(ADX_DECLINE_MIN_DROP, adx_prior * ADX_DECLINE_MIN_DROP_RATIO)
        )
        adx_declining_exhausted = adx_declining and adx < WEAK_ENERGY_ADX_THRESHOLD
        if adx_declining_exhausted:
            return {
                "status": "CANCEL",
                "reason": (
                    f"ADX 動能持續衰退 {adx:.1f}<{adx_prior:.1f}"
                    f"（{ADX_DECLINE_LOOKBACK_BARS}根K棒前）且低於能量門檻 {WEAK_ENERGY_ADX_THRESHOLD:.1f}"
                ),
            }

        if ema_1h is not None:
            if side == "LONG" and price < ema_1h:
                return {"status": "CANCEL", "reason": "1h 大趨勢已轉空"}
            if side == "SHORT" and price > ema_1h:
                return {"status": "CANCEL", "reason": "1h 大趨勢已轉多"}

        atr_pct = atr / price if price > 0 else 0
        if atr_pct > MAX_ATR_PCT:
            return {"status": "CANCEL", "reason": f"波動率轉為過高 ATR_Too_High({atr_pct:.2%})"}
        if atr_pct < MIN_ATR_PCT:
            return {"status": "CANCEL", "reason": f"波動率轉為過低 ATR_Too_Low({atr_pct:.2%})"}

        # 回踩縮量使用多空合計成交量，無法辨識是順勢量或逆勢量；尤其
        # 回踩時縮量常屬正常型態，因此只讓量能項記 0 分，不再硬取消。
        volume_faded = False
        recent_vol_avg = None
        min_sustain_vol = None
        if vol_ma_20 > 0 and len(df) >= 3:
            recent_vol_avg = float(df['volume'].iloc[-3:-1].mean())
            min_sustain_vol = float(vol_ma_20 * POST_BREAKOUT_VOL_SUSTAIN_RATIO)
            volume_faded = recent_vol_avg < min_sustain_vol

        # 逆向爆量先觀察、不硬擋：多單的大陰K或空單的大陽K若達均量1.8倍，
        # 回傳旗標讓引擎記錄，日後可用真實結果決定是否升級為硬性撤單。
        candle_open = float(curr.get('open', curr['close']))
        candle_close = float(curr['close'])
        volume_ratio = (vol / vol_ma_20) if vol_ma_20 > 0 else 0.0
        adverse_candle = (
            (side == "LONG" and candle_close < candle_open)
            or (side == "SHORT" and candle_close > candle_open)
        )
        adverse_volume_spike = (
            adverse_candle
            and abs(candle_close - candle_open) >= atr * ADVERSE_PULLBACK_BODY_MIN_ATR_MULT
            and volume_ratio >= ADVERSE_PULLBACK_VOLUME_SPIKE_RATIO
        )

        # 回調總分（B量能+C RSI+D新鮮度+E品質加分，滿分79，跟 evaluate_signal()
        # 同一套加權）：量能/RSI 不再各自當硬性關卡、任一項不過就整筆取消，
        # 改成允許互相補償——量能爆量成長可以補足 RSI 差一點點的缺口，更貼近
        # 真實交易判斷。上面的方向反轉/大趨勢衰退/新鮮度太舊/ADX動能衰退/
        # 價格乖離過大/ATR%範圍 是絕對紅線，不受總分補償影響，維持硬性取消。
        score_b = (
            20
            if not volume_faded
            and vol_ma_20 > 0
            and vol >= vol_ma_20 * KELTNER_MIN_VOLUME_RATIO
            else 0
        )
        score_c = 20 if (
            (side == "LONG" and rsi >= RSI_LONG_THRESHOLD)
            or (side == "SHORT" and rsi <= RSI_SHORT_THRESHOLD)
        ) else 0
        score_d = freshness_score

        atr_mid = (MIN_ATR_PCT + MAX_ATR_PCT) / 2.0
        atr_half_range = (MAX_ATR_PCT - MIN_ATR_PCT) / 2.0
        atr_quality = (
            max(0.0, 1.0 - abs(atr_pct - atr_mid) / atr_half_range)
            if atr_half_range > 0 else 0.0
        )
        rsi_ideal = 60.0 if side == "LONG" else 40.0
        rsi_half_width = max(
            rsi_ideal - RSI_LONG_THRESHOLD if side == "LONG" else RSI_SHORT_THRESHOLD - rsi_ideal,
            1.0,
        )
        rsi_quality = max(0.0, 1.0 - abs(rsi - rsi_ideal) / rsi_half_width)
        vol_ratio = (vol / vol_ma_20) if vol_ma_20 > 0 else 0.0
        vol_margin = max(0.0, vol_ratio - KELTNER_MIN_VOLUME_RATIO)
        adx_ratio = (adx - ADX_QUALITY_MIN) / (ADX_QUALITY_FULL - ADX_QUALITY_MIN)
        score_e = (
            round(atr_quality * 3)
            + round(rsi_quality * 3)
            + round(min(vol_margin / 1.0, 1.0) * 3)
            + round(min(max(adx_ratio, 0.0), 1.0) * 3)
        )
        adx_decline_soft_penalty = 1 if adx_declining and not adx_declining_exhausted else 0
        if adx_decline_soft_penalty:
            score_e = max(0, score_e - adx_decline_soft_penalty)

        if score_e < ENTRY_MIN_QUALITY_BONUS:
            return {
                "status": "CANCEL",
                "reason": f"回調品質不足 Quality_Too_Low({score_e}<{ENTRY_MIN_QUALITY_BONUS})",
                "adverse_volume_spike": adverse_volume_spike,
                "adverse_volume_ratio": volume_ratio,
            }

        raw_pullback_score = score_b + score_c + score_d + score_e
        pullback_score = max(0, raw_pullback_score - btc_regime["score_penalty"])
        if pullback_score < PULLBACK_SCORE_THRESHOLD:
            return {
                "status": "CANCEL",
                "raw_pullback_score": raw_pullback_score,
                "pullback_score": pullback_score,
                "volume_faded": volume_faded,
                "recent_volume_avg": recent_vol_avg,
                "min_sustain_volume": min_sustain_vol,
                "adverse_volume_spike": adverse_volume_spike,
                "adverse_volume_ratio": volume_ratio,
                "reason": (
                    f"回調總分不足 Pullback_Score({pullback_score}<{PULLBACK_SCORE_THRESHOLD}) | "
                    f"Volume+{score_b} RSI+{score_c} Freshness+{score_d} Quality+{score_e} "
                    f"BTC-{btc_regime['score_penalty']}"
                ),
            }

        return {
            "status": "PASS",
            "reason": (
                f"二次確認通過 Pullback_Score({pullback_score})"
                + (
                    f" | ADX {adx:.1f}←{adx_prior:.1f}仍高於{WEAK_ENERGY_ADX_THRESHOLD:g}，品質-1"
                    if adx_decline_soft_penalty else ""
                )
            ),
            "raw_pullback_score": raw_pullback_score,
            "pullback_score": pullback_score,
            "volume_faded": volume_faded,
            "recent_volume_avg": recent_vol_avg,
            "min_sustain_volume": min_sustain_vol,
            "adverse_volume_spike": adverse_volume_spike,
            "adverse_volume_ratio": volume_ratio,
            "btc_regime_mode": btc_regime["mode"],
            "btc_direction_1h": int(btc_st_direction_1h or 0),
            "btc_score_penalty": btc_regime["score_penalty"],
            "btc_allocation_factor": btc_regime["allocation_factor"],
        }


def detect_simple_ma7_signal(df: pd.DataFrame, live_price: float = None) -> dict:
    """
    Simple MA7 strategy open conditions:
    - Long: MA7 valley (ma7[-3] > ma7[-2] < ma7[-1]), Green Candle (close > open).
    - Short: MA7 peak (ma7[-3] < ma7[-2] > ma7[-1]), Red Candle (close < open).
    - Common: ATR14 >= 0.05%, MA7 change >= 35% ATR14, amplitude <= 3x ATR14, close change <= 3x ATR14, deviation <= 0.5%.
    """
    if len(df) < 14:
        return {"detected": False, "reason": "Data too short"}

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    # Calculate ATR14
    high = df['high']
    low = df['low']
    close = df['close']
    open_ = df['open']
    
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr14 = float(tr.rolling(window=14).mean().iloc[-1])

    # Calculate MA7
    ma7 = close.rolling(window=7).mean()
    if len(ma7) < 3 or pd.isna(ma7.iloc[-1]) or pd.isna(ma7.iloc[-2]) or pd.isna(ma7.iloc[-3]):
        return {"detected": False, "reason": "MA7 not ready"}
        
    ma7_curr = float(ma7.iloc[-1])
    ma7_prev = float(ma7.iloc[-2])
    ma7_prev2 = float(ma7.iloc[-3])

    price = float(live_price) if live_price is not None and float(live_price) > 0 else float(curr['close'])
    if price <= 0:
        return {"detected": False, "reason": "Invalid price"}

    # Volatility / Candle checks
    from core.config import (
        MA7_MIN_ATR_PCT, MA7_ENTRY_ATR_CHANGE_MIN_RATIO, MA7_EXIT_ATR_CHANGE_MIN_RATIO, FIXED_STOP_LOSS_PCT, MA7_MAX_CANDLE_AMPLITUDE_MULT,
        MA7_MAX_CLOSE_CHANGE_MULT, MA7_MARK_PRICE_DEV_PCT
    )

    if atr14 < price * MA7_MIN_ATR_PCT:
        return {"detected": False, "reason": f"Low ATR14 ({atr14:.4f} < {price * MA7_MIN_ATR_PCT:.4f})"}
        
    body_size = abs(float(curr['close']) - float(curr['open']))
    candle_range = float(curr['high']) - float(curr['low'])
    if candle_range > 0 and (body_size / candle_range) < 0.5:
        return {"detected": False, "reason": f"K-line body too small ({body_size / candle_range:.1%} < 50%)"}
        
    amplitude = float(curr['high'] - curr['low'])
    if amplitude > MA7_MAX_CANDLE_AMPLITUDE_MULT * atr14:
        return {"detected": False, "reason": f"Amplitude too large ({amplitude:.4f} > {MA7_MAX_CANDLE_AMPLITUDE_MULT * atr14:.4f})"}
        
    close_change = abs(float(curr['close']) - float(prev['close']))
    if close_change > MA7_MAX_CLOSE_CHANGE_MULT * atr14:
        return {"detected": False, "reason": f"Close change too large ({close_change:.4f} > {MA7_MAX_CLOSE_CHANGE_MULT * atr14:.4f})"}

    dev_pct = abs(price - float(curr['close'])) / float(curr['close'])
    if dev_pct > MA7_MARK_PRICE_DEV_PCT:
        return {"detected": False, "reason": f"Mark price deviation too large ({dev_pct:.4%} > {MA7_MARK_PRICE_DEV_PCT:.4%})"}

    # Valley/Peak check
    is_green = float(curr['close']) > float(curr['open'])
    is_red = float(curr['close']) < float(curr['open'])
    
    is_valley = (ma7_prev2 > ma7_prev) and (ma7_curr > ma7_prev)
    is_peak = (ma7_prev2 < ma7_prev) and (ma7_curr < ma7_prev)

    body_proportion = (body_size / candle_range) if candle_range > 0 else 0
    signal_score = int(body_proportion * 100)
    
    if is_valley and is_green:
        return {
            "detected": True,
            "side": "LONG",
            "score": signal_score,
            "price": price,
            "atr": atr14,
            "reason": f"MA7 Valley + Green Candle (Body {signal_score}%)",
        }
    elif is_peak and is_red:
        return {
            "detected": True,
            "side": "SHORT",
            "score": signal_score,
            "price": price,
            "atr": atr14,
            "reason": f"MA7 Peak + Red Candle (Body {signal_score}%)",
        }

    return {"detected": False, "reason": "No MA7 valley/peak or color mismatch"}


def check_simple_ma7_exit(df: pd.DataFrame, position: dict) -> dict:
    """
    Simple MA7 strategy exit conditions:
    Tracks the highest/lowest MA7 since entry.
    - Long: Exits if current MA7 <= highest_ma7 - (0.25 * ATR14)
    - Short: Exits if current MA7 >= lowest_ma7 + (0.25 * ATR14)
    """
    if len(df) < 14:
        return {"close": False, "reason": "Data too short"}

    # Calculate ATR14
    high = df['high']
    low = df['low']
    close = df['close']
    
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr14 = float(tr.rolling(window=14).mean().iloc[-1])

    # Calculate MA7
    ma7 = close.rolling(window=7).mean()
    if len(ma7) < 3 or pd.isna(ma7.iloc[-1]) or pd.isna(ma7.iloc[-2]) or pd.isna(ma7.iloc[-3]):
        return {"close": False, "reason": "MA7 not ready"}
        
    ma7_curr = float(ma7.iloc[-1])
    ma7_prev = float(ma7.iloc[-2])
    ma7_prev2 = float(ma7.iloc[-3])

    # 為了過濾 1 分鐘線的「小波動（微小V型反彈）」，必須等 MA7 確實偏離最高/最低點一定幅度才算「確定轉彎」
    from core.config import MA7_EXIT_ATR_CHANGE_MIN_RATIO

    side = position.get("side")

    if side == "LONG":
        highest_ma7 = position.get("highest_ma7", ma7_curr)
        if ma7_curr > highest_ma7:
            position["highest_ma7"] = ma7_curr
            highest_ma7 = ma7_curr
            
        if ma7_curr <= highest_ma7 - (MA7_EXIT_ATR_CHANGE_MIN_RATIO * atr14):
            return {"close": True, "reason": "MA7高點實質轉彎 (累積回落大於門檻)"}
            
    elif side == "SHORT":
        lowest_ma7 = position.get("lowest_ma7", ma7_curr)
        if ma7_curr < lowest_ma7:
            position["lowest_ma7"] = ma7_curr
            lowest_ma7 = ma7_curr
            
        if ma7_curr >= lowest_ma7 + (MA7_EXIT_ATR_CHANGE_MIN_RATIO * atr14):
            return {"close": True, "reason": "MA7谷底實質轉彎 (累積反彈大於門檻)"}

    return {"close": False, "reason": "No exit condition met"}
