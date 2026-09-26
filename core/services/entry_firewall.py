"""Fail-closed A-E verification immediately before opening exposure."""
import math
import time

from core.services.strategies.unified_entry_strategy import (
    RULE_CODES, confirmed, evaluate_closed_entry, ma3_entry_problem,
)


def validate_entry_frame(frame, side, code):
    if side not in ('LONG', 'SHORT') or code not in RULE_CODES:
        raise ValueError('[FORBIDDEN_ENTRY] 非法開倉方向或白名單訊號')
    closed = confirmed(frame)
    if closed is None:
        raise ValueError('[FORBIDDEN_ENTRY] 缺少有效已收線行情')
    problem = ma3_entry_problem(closed, side)
    if problem:
        raise ValueError('[FORBIDDEN_ENTRY] MA3 斜率或排列禁止開倉：' + problem)
    last_bar = closed.iloc[-1]
    opening, close = float(last_bar.open), float(last_bar.close)
    atr = float(last_bar.atr)
    middle = float(last_bar.kc_middle)
    upper = float(last_bar.kc_upper)
    lower = float(last_bar.kc_lower)
    prev_bar = closed.iloc[-2]
    prev_upper = float(prev_bar.kc_upper)
    prev_lower = float(prev_bar.kc_lower)

    if side == 'SHORT':
        if lower >= prev_lower:
            raise ValueError(f'[FATAL_REJECT] KC 下軌走平或收窄 ({lower:.6f} >= {prev_lower:.6f})，無向下擴張動能嚴禁開空！')
        if close >= opening:
            raise ValueError(f'[FATAL_REJECT] 陽線嚴禁開空！Close:{close} >= Open:{opening}')
        if close > lower and (opening - close) < 1.2 * atr:
            raise ValueError(f'[FATAL_REJECT] 軌道內小碎步橫盤禁開空！實體: {(opening - close):.8f} < 1.2 ATR: {1.2*atr:.8f}')
        distance_from_middle = abs(close - middle)
        if distance_from_middle > 2.2 * atr:
            raise ValueError(f'[FATAL_REJECT] 拒絕追空：價格距離 KC 中軌達 {distance_from_middle:.5f} (> 2.2 ATR)，極限超賣嚴禁地板追空！')
    else:
        if upper <= prev_upper:
            raise ValueError(f'[FATAL_REJECT] KC 上軌走平或收窄 ({upper:.6f} <= {prev_upper:.6f})，無向上擴張動能嚴禁開多！')
        if close <= opening:
            raise ValueError(f'[FATAL_REJECT] 陰線嚴禁開多！Close:{close} <= Open:{opening}')
        if close < upper and (close - opening) < 1.2 * atr:
            raise ValueError(f'[FATAL_REJECT] 軌道內小碎步橫盤禁開多！實體: {(close - opening):.8f} < 1.2 ATR: {1.2*atr:.8f}')
        distance_from_middle = abs(close - middle)
        if distance_from_middle > 2.2 * atr:
            raise ValueError(f'[FATAL_REJECT] 拒絕追多：價格距離 KC 中軌達 {distance_from_middle:.5f} (> 2.2 ATR)，極限超買嚴禁天花板追多！')

    if 'CLOSED_C' in code:
        prev_middle = float(closed.iloc[-2].kc_middle)
        if side == 'LONG' and middle <= prev_middle:
            raise ValueError('[FATAL_REJECT] 金叉開多，但 KC 中軌向下！嚴禁逆勢開多！')
        if side == 'SHORT' and middle >= prev_middle:
            raise ValueError('[FATAL_REJECT] 死叉開空，但 KC 中軌向上！嚴禁逆勢開空！')

    ok, actual, decision = evaluate_closed_entry(frame, side)
    if not ok or actual != code:
        raise ValueError('[FORBIDDEN_ENTRY] 最新行情不符合指定 A–E 規則')
    return decision


async def validate_account_entry(account, symbol, side, context):
    context = context if isinstance(context, dict) else {}
    code = context.get('entry_signal_code')
    if code not in RULE_CODES:
        raise ValueError('[FORBIDDEN_ENTRY] 缺少 A–E 白名單訊號，禁止送單')
    provider = getattr(account, 'entry_frame_provider', None)
    if not callable(provider):
        raise ValueError('[FORBIDDEN_ENTRY] 缺少可重驗的行情來源')
    try:
        frame = await provider(symbol)
    except Exception as exc:
        raise ValueError('[FORBIDDEN_ENTRY] 無法取得最新行情') from exc
    decision = validate_entry_frame(frame, side, code)
    stamp = float(decision['confirmation_bar_id'])
    age = time.time() * 1000 - (stamp + 60000)
    if not math.isfinite(age) or not 0 <= age <= 90000:
        raise ValueError('[FORBIDDEN_ENTRY] 已收線訊號過期或來自未來')
    if context.get('channel_confirmation_bar_id') != stamp:
        raise ValueError('[FORBIDDEN_ENTRY] 下單確認 K 已改變')
    last_close = getattr(account, 'last_closed_at', {}).get(symbol)
    if last_close and stamp <= float(last_close) * 1000 + 60000:
        raise ValueError('[FORBIDDEN_ENTRY] 剛觸發平倉，強制冷卻 2 根 K 棒！嚴禁追單！')
    return decision
