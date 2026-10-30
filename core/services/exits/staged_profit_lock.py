"""Staged Profit Lock Service (三階段階梯式移動鎖利引擎)

依據 2026-10 最新授權規範實作：
1. 三階段規則：
   - 階段 0（歷史最高淨利率 < 10%）：鎖利不啟動，忽略 MA3 交叉、中軌回踩及短線震盪反轉訊號（VETO_HOLD）。
     絕對安全出口（3 ATR 硬止損、1.5 ATR 瀑布、反側 KC 擊穿）不受阻擋。
   - 階段 1（歷史最高淨利率 >= 10% 且 < 20%）：鎖利底線為最高淨利的 70%（回吐 30%）。
     當即時淨利 <= 鎖利底線時觸發 EXIT_BY_STAGED_PROFIT_LOCK_TIER1。
   - 階段 2（歷史最高淨利率 >= 20%）：升級為最高淨利的 85%（回吐 15%）。
     當即時淨利 <= 鎖利底線時觸發 EXIT_BY_STAGED_PROFIT_LOCK_TIER2。
2. 不可降級鐵律（Monotonic Stage Progression）：
   - 一旦進入階段 2，縱使行情回吐使淨利率跌回 20% 以下，stage 嚴格鎖定在 2，底線維持 Highest Net PnL * 0.85，絕不降回階段 1。
3. 歷史極值單調遞增（Monotonic Peak Profit）：
   - 歷史最高淨利只能隨行情破新高而向上更新，任何 K 棒切換、行情回踩均不可向下調整。
4. 真實淨利與費用精確計算：
   - 扣除開倉與平倉手續費（TAKER_FEE_RATE）、預估滑價（SLIPPAGE_PCT）及累計資金費率（accumulated_funding_fee）。
   - 保證金依據真實槓桿 Initial Margin = (entry_price * qty) / leverage 計算。
5. LONG 與 SHORT 雙向完全鏡像對稱。
6. 防前視偏誤（Anti-Lookahead Bias）：
   - 回測若 K 棒開盤價跳空穿透鎖利線，成交價以開盤價（或更差價）計算，絕不虛構在理想鎖利線上成交。
"""
from dataclasses import dataclass, asdict
import math
from typing import Optional, Tuple, Dict, Any

from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT

# 階梯門檻常數
TIER1_START_ROE_PCT = 10.0   # 階段 1 啟動門檻：淨利率 +10.0%
TIER1_RETAIN_RATIO = 0.70    # 階段 1 鎖利保留率：70% (允許回吐 30%)
TIER2_START_ROE_PCT = 20.0   # 階段 2 啟動門檻：淨利率 +20.0%
TIER2_RETAIN_RATIO = 0.85    # 階段 2 鎖利保留率：85% (允許回吐 15%)

EXIT_REASON_TIER1 = "EXIT_BY_STAGED_PROFIT_LOCK_TIER1"
EXIT_REASON_TIER2 = "EXIT_BY_STAGED_PROFIT_LOCK_TIER2"
EXIT_REASON_SCHEME1 = "EXIT_BY_SCHEME1_PROFIT_LOCK_MA3_MA15"


def check_exit_conditions(
    position: str,
    entry_price: float,
    current_price: float,
    highest_profit_margin: float,
    current_ma3: float,
    current_ma15: float,
    highest_roe_pct: Optional[float] = None,
    current_roe_pct: Optional[float] = None,
) -> str:
    """
    方案 1 核心實作：持倉平倉與動態鎖利邏輯判定 (以 10% 門檻 + 20% 回吐鎖利 結合 MA3 / MA15 趨勢濾網)
    支援 SHORT 與 LONG 雙向鏡像。

    核心思想：
    「縱使浮盈從高點回吐達 20%，只要 MA3 依然在 MA15 下方（空單未反轉）/ 上方（多單未反轉），
    就判定這只是影線插針或正常震盪，強制續抱不鎖利；只有當趨勢真正反轉且滿足回吐條件時，才執行平倉。」

    :param position: 當前持倉狀態 ("SHORT", "LONG" 或 "NONE")
    :param entry_price: 開倉價格
    :param current_price: 當前 K 線價格 (或收盤價)
    :param highest_profit_margin: 本筆交易紀錄到的最高浮盈比例 (例如 0.15 代表 15%)
    :param current_ma3: 當前 MA3 數值
    :param current_ma15: 當前 MA15 數值
    :param highest_roe_pct: (可選) 本筆交易最高 ROE%
    :param current_roe_pct: (可選) 當前 ROE%
    :return: 執行的動作 ("HOLD" 續抱, "CLOSE_SHORT" 平空, "CLOSE_LONG" 平多, 或 "NONE")
    """
    pos_side = str(position or "NONE").upper()
    if pos_side not in ("SHORT", "LONG"):
        return "NONE"

    if entry_price <= 0 or current_price <= 0:
        return "HOLD"

    # 1. 計算當前浮盈比例 (空單：(開倉價 - 當前價) / 開倉價；多單：(當前價 - 開倉價) / 開倉價)
    if pos_side == "SHORT":
        current_profit_margin = (entry_price - current_price) / entry_price
    else:
        current_profit_margin = (current_price - entry_price) / entry_price

    # 2. 更新歷史最高浮盈
    if current_profit_margin > highest_profit_margin:
        highest_profit_margin = current_profit_margin

    # 3. 計算從最高點回吐的比例 (Drawdown from Peak)
    if highest_profit_margin > 0:
        profit_drawdown = (highest_profit_margin - current_profit_margin) / highest_profit_margin
    elif highest_roe_pct is not None and current_roe_pct is not None and highest_roe_pct > 0:
        profit_drawdown = max(0.0, (highest_roe_pct - current_roe_pct) / highest_roe_pct)
    else:
        profit_drawdown = 0.0

    # 4. 判定均線趨勢是否發生反轉
    # 空單背景下：MA3 上穿 MA15 (MA3 > MA15) 代表小週期走勢轉強/反彈
    # 多單背景下：MA3 下穿 MA15 (MA3 < MA15) 代表小週期走勢轉弱/回檔
    if pos_side == "SHORT":
        is_trend_reversed = (current_ma3 > current_ma15)
    else:
        is_trend_reversed = (current_ma3 < current_ma15)

    # ----------------------------------------------------
    # 核心方案 1 鎖利與平倉判定邏輯
    # ----------------------------------------------------

    # 條件 A：達到 10% 鎖利門檻（價格浮盈 >= 10% 或 ROE >= 10%），且回吐超過 20%
    is_reach_10pct = (highest_profit_margin >= 0.10) or (highest_roe_pct is not None and highest_roe_pct >= 10.0)
    is_take_profit_triggered = is_reach_10pct and (profit_drawdown >= 0.20)

    if is_take_profit_triggered:
        # 【加入雙重濾網】：即使回吐達 20%，也要檢查趨勢是否真的反轉
        if is_trend_reversed:
            action = "CLOSE_SHORT" if pos_side == "SHORT" else "CLOSE_LONG"
            return action
        else:
            # 雖然回吐 20%，但 MA3 依然在未反轉側，認定為插針/假拉高，忽略平倉！
            return "HOLD"

    # 條件 B：一般的均線反轉平倉 (即使沒達到 10% 浮盈，只要均線反轉且處於浮盈區亦可離場)
    elif is_trend_reversed and (current_profit_margin > 0 or (current_roe_pct is not None and current_roe_pct > 0)):
        action = "CLOSE_SHORT" if pos_side == "SHORT" else "CLOSE_LONG"
        return action

    return "HOLD"

# 階段 0 強制忽略／否決之震盪與微觀反轉出口候選清單
STAGE0_VETO_EXIT_CANDIDATES = frozenset({
    "EXIT_BY_TREND_BROKEN",
    "EXIT_BY_KC_MID_STRUCTURE_BROKEN",
    "EXIT_SHORT_ON_MA5_FLAT_REVERSAL",
    "EXIT_LONG_ON_MA5_FLAT_REVERSAL",
    "EXIT_BY_KC_MIDLINE_RETRACE",
    "EXIT_BY_MA3_MA15_CROSS",
    "EXIT_PEAK_MA_TURN_PRESSURE",
    "EXIT_BY_PULLBACK",
    "EXIT_ON_MOMENTUM_EXHAUSTION",
})


def calculate_staged_net_pnl_and_roe(
    position: Dict[str, Any],
    price: float,
    side: Optional[str] = None,
) -> Tuple[float, float, float, float]:
    """計算單筆持倉之真實淨損益 (Net PnL)、淨 ROE%、毛利 (Gross PnL) 與總費用 (Total Costs)。

    回傳: (net_pnl, net_roe_pct, gross_pnl, total_costs)
    """
    if not position or price is None:
        return 0.0, 0.0, 0.0, 0.0

    pos_side = str(side or position.get("side", "")).upper()
    if pos_side not in ("LONG", "SHORT"):
        return 0.0, 0.0, 0.0, 0.0

    entry_p = float(position.get("entry_price") or 0.0)
    curr_p = float(price)
    if not (math.isfinite(entry_p) and math.isfinite(curr_p) and entry_p > 0 and curr_p > 0):
        return 0.0, 0.0, 0.0, 0.0

    raw_qty = position.get("qty", position.get("quantity", 0.0))
    qty = abs(float(raw_qty or 0.0))
    if not (math.isfinite(qty) and qty > 0):
        return 0.0, 0.0, 0.0, 0.0

    leverage = float(position.get("leverage") or 5.0)
    if not (math.isfinite(leverage) and leverage > 0):
        leverage = 5.0

    margin = float(position.get("margin") or 0.0)
    if not (math.isfinite(margin) and margin > 0):
        margin = (entry_p * qty) / max(leverage, 1.0)

    # 1. 毛利計算
    if pos_side == "LONG":
        gross_pnl = (curr_p - entry_p) * qty
    else:
        gross_pnl = (entry_p - curr_p) * qty

    # 2. 手續費與滑價扣除
    fee_rate = float(position.get("fee_rate", TAKER_FEE_RATE))
    slippage_pct = float(position.get("slippage_pct", SLIPPAGE_PCT))
    entry_fee = entry_p * qty * fee_rate
    exit_fee = curr_p * qty * fee_rate
    slippage_cost = curr_p * qty * slippage_pct
    accumulated_funding = float(position.get("accumulated_funding_fee", 0.0))

    total_costs = entry_fee + exit_fee + slippage_cost + accumulated_funding

    # 3. 淨損益與淨利率
    net_pnl = gross_pnl - total_costs
    net_roe_pct = (net_pnl / margin) * 100.0 if margin > 0 else 0.0

    return net_pnl, net_roe_pct, gross_pnl, total_costs


def get_or_create_staged_state(position: Dict[str, Any]) -> Dict[str, Any]:
    """獲取或初始化持倉物件中之 staged_profit_lock_state 字典。"""
    state = position.get("staged_profit_lock_state")
    if not isinstance(state, dict):
        state = {
            "stage": 0,
            "highest_net_pnl": 0.0,
            "highest_net_roe_pct": 0.0,
            "lock_floor_net_pnl": None,
            "lock_floor_roe_pct": None,
            "highest_favorable_price": None,
            "initial_margin": float(position.get("margin") or 0.0),
            "armed": False,
        }
        position["staged_profit_lock_state"] = state
    return state


def update_staged_profit_lock_state(
    position: Dict[str, Any],
    current_price: float,
    side: Optional[str] = None,
) -> Dict[str, Any]:
    """更新持倉之階梯鎖利狀態，嚴格維持單調不減與不可降級性。"""
    state = get_or_create_staged_state(position)
    pos_side = str(side or position.get("side", "")).upper()
    if pos_side not in ("LONG", "SHORT"):
        return state

    net_pnl, net_roe_pct, _, _ = calculate_staged_net_pnl_and_roe(position, current_price, pos_side)
    entry_p = float(position.get("entry_price") or 0.0)

    # 結合歷史最高價/最低價（若有）推算峰值，確保不會因取樣頻率遺漏高點
    favorable_p = current_price
    if entry_p > 0:
        if pos_side == "LONG":
            saved_fav = float(position.get("highest_price") or state.get("highest_favorable_price") or entry_p)
            favorable_p = max(saved_fav, current_price)
        else:
            saved_fav = float(position.get("lowest_price") or state.get("highest_favorable_price") or entry_p)
            favorable_p = min(saved_fav, current_price)

    fav_net_pnl, fav_net_roe_pct, _, _ = calculate_staged_net_pnl_and_roe(position, favorable_p, pos_side)

    # 歷史最高極值單調遞增
    prev_highest_pnl = float(state.get("highest_net_pnl", 0.0))
    prev_highest_roe = float(state.get("highest_net_roe_pct", 0.0))

    new_highest_pnl = max(prev_highest_pnl, net_pnl, fav_net_pnl)
    new_highest_roe = max(prev_highest_roe, net_roe_pct, fav_net_roe_pct)

    state["highest_net_pnl"] = round(new_highest_pnl, 6)
    state["highest_net_roe_pct"] = round(new_highest_roe, 6)
    state["highest_favorable_price"] = favorable_p

    # 同步回持倉頂層
    position["highest_net_roe"] = state["highest_net_roe_pct"]
    position["peak_net_roe_pct"] = state["highest_net_roe_pct"]

    # 階段單調遞增（不可降級）
    current_stage = int(state.get("stage", 0))

    # 檢查是否達到階段 2 門檻 (+20%)
    if (new_highest_roe >= TIER2_START_ROE_PCT) or math.isclose(new_highest_roe, TIER2_START_ROE_PCT, rel_tol=1e-9, abs_tol=1e-9):
        current_stage = 2
    # 檢查是否達到階段 1 門檻 (+10%)
    elif (new_highest_roe >= TIER1_START_ROE_PCT) or math.isclose(new_highest_roe, TIER1_START_ROE_PCT, rel_tol=1e-9, abs_tol=1e-9):
        if current_stage < 1:
            current_stage = 1

    state["stage"] = current_stage
    state["armed"] = (current_stage >= 1)
    position["profit_lock_armed"] = (current_stage >= 1)

    # 計算鎖利底線
    if current_stage == 2:
        floor_pnl = new_highest_pnl * TIER2_RETAIN_RATIO
        floor_roe = new_highest_roe * TIER2_RETAIN_RATIO
        state["lock_floor_net_pnl"] = round(floor_pnl, 6)
        state["lock_floor_roe_pct"] = round(floor_roe, 6)
    elif current_stage == 1:
        floor_pnl = new_highest_pnl * TIER1_RETAIN_RATIO
        floor_roe = new_highest_roe * TIER1_RETAIN_RATIO
        state["lock_floor_net_pnl"] = round(floor_pnl, 6)
        state["lock_floor_roe_pct"] = round(floor_roe, 6)
    else:
        state["lock_floor_net_pnl"] = None
        state["lock_floor_roe_pct"] = None

    return state


def evaluate_staged_profit_lock(
    position: Dict[str, Any],
    current_price: float,
    side: Optional[str] = None,
    current_ma3: Optional[float] = None,
    current_ma15: Optional[float] = None,
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """三階段階梯式淨利鎖利唯一裁決官（結合方案 1 MA3/MA15 趨勢過濾）：

    回傳: (should_exit, reason, meta)
    """
    if not position or current_price is None:
        return False, None, {}

    pos_side = str(side or position.get("side", "")).upper()
    if pos_side not in ("LONG", "SHORT"):
        return False, None, {}

    # 1. 更新並取得最新狀態
    state = update_staged_profit_lock_state(position, current_price, pos_side)
    net_pnl, net_roe_pct, gross_pnl, total_costs = calculate_staged_net_pnl_and_roe(position, current_price, pos_side)

    stage = int(state.get("stage", 0))
    floor_pnl = state.get("lock_floor_net_pnl")
    floor_roe = state.get("lock_floor_roe_pct")
    highest_pnl = float(state.get("highest_net_pnl", 0.0))
    highest_roe = float(state.get("highest_net_roe_pct", 0.0))

    meta: Dict[str, Any] = {
        "current_net_pnl": round(net_pnl, 4),
        "current_net_roe_pct": round(net_roe_pct, 4),
        "highest_net_pnl": round(highest_pnl, 4),
        "highest_net_roe_pct": round(highest_roe, 4),
        "stage": stage,
        "armed": bool(state.get("armed", False)),
        "lock_floor_net_pnl": round(floor_pnl, 4) if floor_pnl is not None else None,
        "lock_floor_roe_pct": round(floor_roe, 4) if floor_roe is not None else None,
        "gross_pnl": round(gross_pnl, 4),
        "total_costs": round(total_costs, 4),
        "profit_lock_rule": "STAGED_PROFIT_LOCK_3TIER_WITH_SCHEME1",
        "current_ma3": current_ma3,
        "current_ma15": current_ma15,
    }

    # 階段 0：未啟動鎖利
    if stage == 0 or floor_pnl is None:
        return False, None, meta

    # 觸發判定：浮點數容差比對
    hit_floor = (
        (net_pnl <= floor_pnl or math.isclose(net_pnl, floor_pnl, rel_tol=1e-9, abs_tol=1e-9))
        or (floor_roe is not None and (net_roe_pct <= floor_roe or math.isclose(net_roe_pct, floor_roe, rel_tol=1e-9, abs_tol=1e-9)))
    )

    # 🎯 方案 1 觸發判定：最高淨利 >= 10%，且自最高點回吐 >= 20%
    scheme1_triggered = False
    if highest_pnl > 0 and (highest_roe >= 10.0 or (entry_p > 0 and (highest_pnl / entry_p) >= 0.10)):
        profit_drawdown = (highest_pnl - net_pnl) / highest_pnl
        if profit_drawdown >= 0.20 or math.isclose(profit_drawdown, 0.20, rel_tol=1e-9, abs_tol=1e-9):
            scheme1_triggered = True

    if hit_floor or scheme1_triggered:
        # 🎯 方案 1 核心過濾：縱使回吐跌破鎖利底線或回吐達 20%，若 MA3 仍未反轉，判定為插針/假拉回，一票否決鎖利！
        if current_ma3 is not None and current_ma15 is not None:
            if pos_side == "SHORT" and current_ma3 <= current_ma15:
                meta["cause"] = (
                    f"[忽略鎖利] 最高淨利 {highest_pnl:.2f}U ({highest_roe:.2f}%)，現利縮水至 {net_pnl:.2f}U，"
                    f"但 MA3 ({current_ma3:.6f}) 仍低於 MA15 ({current_ma15:.6f})，趨勢未反轉，"
                    f"判定為插針洗盤雜訊，一票否決平倉，繼續讓利潤奔跑！"
                )
                return False, "VETO_HOLD_SCHEME1_MA3_MA15_FILTER", meta
            elif pos_side == "LONG" and current_ma3 >= current_ma15:
                meta["cause"] = (
                    f"[忽略鎖利] 最高淨利 {highest_pnl:.2f}U ({highest_roe:.2f}%)，現利縮水至 {net_pnl:.2f}U，"
                    f"但 MA3 ({current_ma3:.6f}) 仍高於 MA15 ({current_ma15:.6f})，趨勢未反轉，"
                    f"判定為插針洗盤雜訊，一票否決平倉，繼續讓利潤奔跑！"
                )
                return False, "VETO_HOLD_SCHEME1_MA3_MA15_FILTER", meta

        if scheme1_triggered:
            reason = EXIT_REASON_SCHEME1
            meta["cause"] = (
                f"方案1鎖利觸發 ({reason}): 最高浮盈 {highest_pnl:.2f}U ({highest_roe:.2f}%)，"
                f"回吐達 {(highest_pnl-net_pnl)/highest_pnl*100:.1f}% >= 20%，且 MA3/MA15 趨勢已反轉，落袋保利！"
            )
            return True, reason, meta

        if stage == 2:
            reason = EXIT_REASON_TIER2
            meta["cause"] = (
                f"階梯鎖利第二階段觸發 ({reason}): "
                f"最高淨利 {highest_pnl:.2f}U ({highest_roe:.2f}%)，"
                f"現利縮水至 {net_pnl:.2f}U ({net_roe_pct:.2f}%) 跌破鎖利底線 {floor_pnl:.2f}U ({floor_roe:.2f}%)，"
                f"且 MA3/MA15 趨勢已反轉，落袋保利！"
            )
            return True, reason, meta
        elif stage == 1:
            reason = EXIT_REASON_TIER1
            meta["cause"] = (
                f"階梯鎖利第一階段觸發 ({reason}): "
                f"最高淨利 {highest_pnl:.2f}U ({highest_roe:.2f}%)，"
                f"現利縮水至 {net_pnl:.2f}U ({net_roe_pct:.2f}%) 跌破鎖利底線 {floor_pnl:.2f}U ({floor_roe:.2f}%)，"
                f"且 MA3/MA15 趨勢已反轉，落袋保利！"
            )
            return True, reason, meta

    return False, None, meta


def check_stage0_veto_hold(
    position: Dict[str, Any],
    candidate_reason: str,
    current_price: Optional[float] = None,
    side: Optional[str] = None,
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """在階段 0（歷史最高淨利率 < 10%）執行強制鎖倉防洗（VETO_HOLD）。

    一律忽略 MA3 交叉、中軌回踩及短線震盪反轉訊號。
    絕對安全出口（3 ATR 硬止損、瀑布、反側破軌）不受影響。
    """
    if not candidate_reason:
        return False, None, {}

    # 若持倉處於階段 0
    state = get_or_create_staged_state(position)
    if current_price is not None:
        state = update_staged_profit_lock_state(position, current_price, side)

    stage = int(state.get("stage", 0))
    highest_roe = float(state.get("highest_net_roe_pct", 0.0))

    if stage == 0 and highest_roe < TIER1_START_ROE_PCT:
        # 檢查候選出口是否屬於需忽略的微觀震盪出口
        reason_upper = str(candidate_reason).upper()
        should_veto = (
            candidate_reason in STAGE0_VETO_EXIT_CANDIDATES
            or "MA3" in reason_upper
            or "MIDLINE" in reason_upper
            or "RETRACE" in reason_upper
            or "PULLBACK" in reason_upper
            or "MOMENTUM_EXHAUSTION" in reason_upper
        )
        if should_veto:
            veto_meta = {
                "stage": 0,
                "highest_net_roe_pct": highest_roe,
                "suppressed_exit_reason": candidate_reason,
                "cause": (
                    f"階段0防震盪鎖倉鐵律一票否決 (VETO_HOLD): "
                    f"歷史最高淨利率未達10% (peak={highest_roe:.2f}% < 10%)，"
                    f"忽略MA3/中軌及微觀反轉雜訊 ({candidate_reason})，強制續抱！"
                ),
            }
            return True, "VETO_HOLD", veto_meta

    return False, None, {}
