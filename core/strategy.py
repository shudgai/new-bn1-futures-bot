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
    MA5_EARLY_ENTRY_ENABLED, MA5_EARLY_MIN_ATR_MULT, MA5_REVERSAL_MIN_ATR_MULT,
    MA5_FAST_ENTRY_ENABLED, MA5_FAST_MIN_ATR_MULT, MA5_FAST_MAX_ATR_MULT,
    MA5_FAST_MIN_VOLUME_RATIO, MA5_DYNAMIC_ATR_FLOOR_PCT,
    MA5_BOTTOM_ENTRY_ENABLED, MA5_BOTTOM_OFFSET_ATR_MULT,
    MA2_CONFIRMATION_LOOKBACK_BARS,
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
    EXHAUSTION_SNIPER_LOOKBACK_BARS, EXHAUSTION_SNIPER_VOLUME_RATIO,
    EXHAUSTION_SNIPER_RSI_LONG_MAX, EXHAUSTION_SNIPER_RSI_SHORT_MIN,
    EXHAUSTION_SNIPER_STOP_LOSS_PCT,
)
import core.config as _core_config

# Ensure runtime config edits are respected when this module is reloaded during tests
STRUCTURED_SUPPORT_NEAR_ATR = getattr(_core_config, "STRUCTURED_SUPPORT_NEAR_ATR", STRUCTURED_SUPPORT_NEAR_ATR)
from core.services.technical_indicators import compute_technical_indicators
from core.indicators import (
    bars_since_supertrend_flip,
    evaluate_minimum_kc_wave,
    get_dynamic_adx_floor,
)
from core.config import (
    ADX_DECLINE_LOOKBACK_BARS, ADX_DECLINE_MIN_DROP, ADX_DECLINE_MIN_DROP_RATIO,
    KC_TOUCH_LOOKBACK_BARS,
    MAINSTREAM_SYMBOLS, VOLUME_DIVERGENCE_LOOKBACK_BARS, VOLUME_DIVERGENCE_MAX_RATIO,
    PRICE_NEAR_SUPPORT_PCT,
)


def check_ma3_trend(df: pd.DataFrame, lookback: int = 3) -> int:
    """檢查MA3的趨勢方向。
    返回值：
    - 1: MA3上升趨勢（適合LONG）
    - -1: MA3下降趨勢（適合SHORT）
    - 0: MA3平盤或資料不足
    """
    if df is None or len(df) < lookback + 1:
        return 0
    
    if "ma3" not in df.columns:
        return 0
    
    ma3_values = df["ma3"].tail(lookback + 1).values
    # 過濾NaN值
    ma3_values = ma3_values[~np.isnan(ma3_values)]
    
    if len(ma3_values) < 2:
        return 0
    
    # 檢查最近幾根是否持續上升或下降
    ma3_diffs = np.diff(ma3_values)
    
    # 計算上升和下降的根數
    uptrend_count = np.sum(ma3_diffs > 0)
    downtrend_count = np.sum(ma3_diffs < 0)
    
    # 如果上升根數明顯多於下降，認為是上升趨勢
    if uptrend_count > downtrend_count:
        return 1
    # 如果下降根數明顯多於上升，認為是下降趨勢
    elif downtrend_count > uptrend_count:
        return -1
    else:
        return 0


def detect_strong_green_candle_burst(df: pd.DataFrame) -> dict:
    """檢測強勢多單訊號：綠K（多K）從中軌衝到外軌外。
    
    返回值：
    {
        "detected": bool,  # 是否偵測到強勢多單
        "side": "LONG",    # 訊號方向（恆為LONG）
        "price": float,    # 當前價格
        "kc_upper": float, # 上軌價格
        "kc_middle": float,# 中軌價格
        "reason": str,     # 詳細原因
    }
    """
    result = {
        "detected": False,
        "side": None,
        "price": None,
        "kc_upper": None,
        "kc_middle": None,
        "in_outer_rail": False,
        "reason": "未偵測到強勢多單",
    }
    
    if df is None or len(df) < 2:
        return result
    
    # 檢查必要欄位
    required = {"open", "close", "high", "kc_upper", "ema_20"}
    if not required.issubset(df.columns):
        return result
    
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    candle_open = float(curr.get("open", 0))
    candle_close = float(curr.get("close", 0))
    candle_high = float(curr.get("high", 0))
    kc_upper = float(curr.get("kc_upper", 0))
    kc_middle = float(curr.get("ema_20", 0))  # 中軌 = EMA20
    
    # 檢查是否是綠K（收盤 > 開盤）
    is_green_candle = candle_close > candle_open
    if not is_green_candle:
        return result
    
    # 檢查前一根K是否在中軌附近或下方
    prev_close = float(prev.get("close", 0))
    prev_below_middle = prev_close <= kc_middle * 1.01  # 允許1%誤差範圍
    
    # 檢查當前K的高點是否衝到外軌上方
    broke_upper = candle_high > kc_upper
    
    # 檢查是否在外軌以上（用於多單持有決策）
    is_in_outer_rail = candle_close > kc_upper
    
    if is_green_candle and broke_upper:
        # 確認是從中軌附近衝出來的
        if prev_below_middle or candle_open <= kc_middle * 1.01:
            result.update({
                "detected": True,
                "side": "LONG",
                "price": candle_close,
                "kc_upper": kc_upper,
                "kc_middle": kc_middle,
                "in_outer_rail": is_in_outer_rail,
                "reason": f"強勢多單信號：綠K從中軌衝到外軌外 (high={candle_high:.4f}>${kc_upper:.4f})",
            })
    
    return result


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
    - 啟用固定 TP 時，TP 嚴格使用設定百分比；否則維持最低風報比。
    """
    side = str(side).upper()
    if side not in {"LONG", "SHORT"}:
        raise ValueError(f"Unsupported side: {side}")

    sl_distance = float(abs(sl_distance or 0.0))
    fixed_tp_pct = float(getattr(_core_config, "FIXED_TAKE_PROFIT_PCT", 0.0))
    tp_distance = (
        price * fixed_tp_pct if fixed_tp_pct > 0
        else max(float(abs(tp_distance if tp_distance is not None else sl_distance)),
                 sl_distance * MIN_REWARD_RISK_RATIO)
    )

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

    # allow_profit_lock=True 代表追蹤停損可能已經越過成本價（例如鎖利），
    # 此時 sl 在 LONG 高於現價、SHORT 低於現價都是正常狀態，不能套用
    # 「初始下單」才成立的 sl 必須在價格不利側的檢查，否則這個參數形同虛設。
    if sl != 0.0 and not allow_profit_lock:
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
    fixed_tp_pct = float(getattr(_core_config, "FIXED_TAKE_PROFIT_PCT", 0.0))
    if fixed_tp_pct <= 0 and gross_rr + 1e-12 < MIN_REWARD_RISK_RATIO:
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
    fixed_tp_pct = float(getattr(_core_config, "FIXED_TAKE_PROFIT_PCT", 0.0))
    if fixed_tp_pct > 0:
        return sl_distance, price * fixed_tp_pct
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


def check_exhaustion_entry_filters(df: pd.DataFrame, side: str) -> dict:
    """最近三根已收盤K中，同一根須同時通過KC、RSI與1.5倍量能。"""
    wanted_side = str(side).upper()
    required = {"high", "low", "volume", "vol_ma_20", "kc_upper", "kc_lower", "rsi"}
    if df is None or len(df) < EXHAUSTION_SNIPER_LOOKBACK_BARS or not required.issubset(df.columns):
        return {"passed": False, "reason": "KC／RSI／量能資料不足"}
    recent = df.iloc[-EXHAUSTION_SNIPER_LOOKBACK_BARS:]
    edge_seen = False
    rsi_seen = False
    for age, (_, candle) in enumerate(recent.iloc[::-1].iterrows()):
        edge = (
            float(candle["low"]) <= float(candle["kc_lower"])
            if wanted_side == "LONG"
            else float(candle["high"]) >= float(candle["kc_upper"])
        )
        if not edge:
            continue
        edge_seen = True
        rsi = float(candle["rsi"])
        rsi_ok = (
            rsi < EXHAUSTION_SNIPER_RSI_LONG_MAX
            if wanted_side == "LONG"
            else rsi > EXHAUSTION_SNIPER_RSI_SHORT_MIN
        )
        if not rsi_ok:
            continue
        rsi_seen = True
        volume_ma = float(candle["vol_ma_20"]) if not pd.isna(candle["vol_ma_20"]) else 0.0
        volume_ratio = float(candle["volume"]) / volume_ma if volume_ma > 0 else 0.0
        if volume_ratio <= EXHAUSTION_SNIPER_VOLUME_RATIO:
            continue
        return {
            "passed": True, "reason": "KC＋RSI＋1.5倍量能通過",
            "extreme_age_bars": age, "extreme_rsi": rsi,
            "extreme_volume_ratio": volume_ratio,
        }
    if not edge_seen:
        reason = f"最近{EXHAUSTION_SNIPER_LOOKBACK_BARS}根未觸及KC極端"
    elif not rsi_seen:
        reason = "KC極端K的RSI未達門檻"
    else:
        reason = f"同一根極端K量能未大於{EXHAUSTION_SNIPER_VOLUME_RATIO:g}x"
    return {"passed": False, "reason": reason}


def detect_ma5_reversal(
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
    """偵測 1m Exhaustion Sniper；傳入資料必須只包含已收盤 K。"""
    wanted_side = str(side).upper()

    def _no(reason: str) -> dict:
        return {"detected": False, "reason": reason, "side": wanted_side, "score": 0}

    required = {"open", "high", "low", "close", "volume", "kc_upper", "kc_lower", "rsi"}
    if len(df) < 20 or not required.issubset(df.columns):
        return _no("Exhaustion Sniper 指標資料不足")

    work = df.copy()
    if "ma3" not in work.columns:
        work["ma3"] = work["close"].rolling(window=3).mean()
    if "vol_ma_20" not in work.columns:
        work["vol_ma_20"] = work["volume"].rolling(window=20).mean()
    if len(work["ma3"].dropna()) < 3:
        return _no("MA3資料不足")

    ma3_curr = float(work["ma3"].iloc[-1])
    ma3_prev = float(work["ma3"].iloc[-2])
    ma3_prev2 = float(work["ma3"].iloc[-3])
    is_long = wanted_side == "LONG"
    strict_turn = (
        ma3_prev2 > ma3_prev and ma3_curr > ma3_prev
        if is_long
        else ma3_prev2 < ma3_prev and ma3_curr < ma3_prev
    )
    if not strict_turn:
        return _no("MA3 尚未形成嚴格V型反轉" if is_long else "MA3 尚未形成嚴格倒V型反轉")

    recent = work.iloc[-EXHAUSTION_SNIPER_LOOKBACK_BARS:]
    edge_seen = False
    rsi_seen = False
    event = None
    event_age = None
    for age, (_, candle) in enumerate(recent.iloc[::-1].iterrows()):
        edge = (
            float(candle["low"]) <= float(candle["kc_lower"])
            if is_long
            else float(candle["high"]) >= float(candle["kc_upper"])
        )
        if not edge:
            continue
        edge_seen = True
        rsi = float(candle["rsi"])
        rsi_ok = rsi < EXHAUSTION_SNIPER_RSI_LONG_MAX if is_long else rsi > EXHAUSTION_SNIPER_RSI_SHORT_MIN
        if not rsi_ok:
            continue
        rsi_seen = True
        volume_ma = float(candle["vol_ma_20"]) if not pd.isna(candle["vol_ma_20"]) else 0.0
        volume_ratio = float(candle["volume"]) / volume_ma if volume_ma > 0 else 0.0
        if volume_ratio <= EXHAUSTION_SNIPER_VOLUME_RATIO:
            continue
        event = candle
        event_age = age
        break

    if event is None:
        if not edge_seen:
            return _no(f"最近{EXHAUSTION_SNIPER_LOOKBACK_BARS}根未觸及Keltner極端邊界")
        if not rsi_seen:
            threshold = EXHAUSTION_SNIPER_RSI_LONG_MAX if is_long else EXHAUSTION_SNIPER_RSI_SHORT_MIN
            operator = "<" if is_long else ">"
            return _no(f"Keltner極端K的RSI未達{operator}{threshold:g}")
        return _no(f"同一根極端K的量能未大於{EXHAUSTION_SNIPER_VOLUME_RATIO:g}x")

    price = float(live_price) if live_price and live_price > 0 else float(work["close"].iloc[-1])
    atr = float(work["atr"].iloc[-1]) if "atr" in work.columns and not pd.isna(work["atr"].iloc[-1]) else price * 0.015
    event_vol_ma = float(event["vol_ma_20"])
    event_volume_ratio = float(event["volume"]) / event_vol_ma
    stop = price * (1.0 - EXHAUSTION_SNIPER_STOP_LOSS_PCT if is_long else 1.0 + EXHAUSTION_SNIPER_STOP_LOSS_PCT)
    return {
        "detected": True,
        "side": wanted_side,
        "score": 100,
        "price": price,
        "atr": atr,
        "entry_mode": "EXHAUSTION_SNIPER",
        "profit_profile": "TREND_EXTENSION",
        "action": "ENTER_MARKET",
        "target_price": None,
        "structural_sl": stop,
        "signal_candle_low": float(work["low"].iloc[-1]),
        "signal_candle_high": float(work["high"].iloc[-1]),
        "extreme_age_bars": event_age,
        "extreme_rsi": float(event["rsi"]),
        "extreme_volume_ratio": event_volume_ratio,
        "is_contrarian_bottom_buy": is_long,
        "reason": (
            f"Exhaustion_Sniper_{wanted_side}｜KC極端+RSI={float(event["rsi"]):.1f}"
            f"+量能={event_volume_ratio:.2f}x｜MA3嚴格V轉"
        ),
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
        """Compatibility facade for the pure indicator calculation service."""
        return compute_technical_indicators(
            df,
            atr_period=self.atr_period,
            atr_multiplier=self.atr_multiplier,
            adx_period=self.adx_period,
            keltner_atr_multiplier=KELTNER_ATR_MULTIPLIER,
        )

    def evaluate_structured_entry(
        self, df: pd.DataFrame, ema_50_1h: float = None,
        st_direction_1h: int = None, btc_st_direction_1h: int = 0,
        symbol: str = None, indicators_precomputed: bool = False,
        is_dca_check: bool = False,
    ) -> dict:
        from core import strategy as rules
        from core.services.strategies.structured_entry_evaluator import evaluate_structured_entry
        return evaluate_structured_entry(
            self, rules, df, ema_50_1h, st_direction_1h,
            btc_st_direction_1h, symbol, indicators_precomputed, is_dca_check,
        )


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
        from core import strategy as rules
        from core.services.strategies.signal_selection import evaluate_signal
        return evaluate_signal(
            self, rules, df, ema_50_1h, trend_1h_declining,
            st_direction_1h, btc_st_direction_1h, btc_st_flip_age,
            symbol, parameter_overrides, indicators_precomputed,
        )


    def confirm_pullback_entry(
        self, df: pd.DataFrame, side: str, ema_1h: float = None,
        trend_1h_declining: bool = False, btc_st_direction_1h: int = 0,
        btc_st_flip_age: int = 999, symbol: str = None,
    ) -> dict:
        from core import strategy as rules
        from core.services.strategies.pullback_confirmation import confirm_pullback_entry
        return confirm_pullback_entry(
            self, rules, df, side, ema_1h, trend_1h_declining,
            btc_st_direction_1h, btc_st_flip_age, symbol,
        )




def detect_simple_ma5_signal(df: pd.DataFrame, live_price: float = None) -> dict:
    from core import strategy as rules
    from core.services.strategies.turn_evaluator import detect_turn_entry
    return detect_turn_entry(rules, df, live_price)


def check_simple_ma5_exit(df: pd.DataFrame, position: dict) -> dict:
    from core import strategy as rules
    from core.services.strategies.turn_evaluator import evaluate_turn_exit
    return evaluate_turn_exit(rules, df, position)
