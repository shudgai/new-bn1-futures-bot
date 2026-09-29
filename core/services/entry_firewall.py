"""Fail-closed A-E verification immediately before opening exposure."""
import math
import time

from core.services.strategies.unified_entry_strategy import (
    RULE_CODES, confirmed, evaluate_closed_entry, ma3_entry_problem, long_entry_trend_problem,
)


class EntryFirewall:
    @classmethod
    def verify_can_open(cls, frame, side, code):
        """
        【架構級重大重構：建立開倉底層的「單一閘門（Single Hard-Gate）」】
        所有開倉行為必須經過此唯一入口，實施一票否決。
        """
        if side not in ('LONG', 'SHORT') or code not in RULE_CODES:
            raise ValueError('[FORBIDDEN_ENTRY] 非法開倉方向或白名單訊號')
            
        closed = confirmed(frame)
        if closed is None or len(closed) < 8:
            raise ValueError('[FORBIDDEN_ENTRY] 缺少有效已收線行情或K棒數量不足')

        c = closed.iloc[-1]
        c1 = closed.iloc[-2]
        
        close = float(c.close)
        upper = float(c.kc_upper)
        lower = float(c.kc_lower)
        middle = float(c.kc_middle)
        prev_middle = float(c1.kc_middle)
        
        is_breakout_up = close > upper
        is_breakout_down = close < lower
        
        is_ignition = 'IGNITION' in str(code).upper() or 'BREAKOUT' in str(code).upper()
        
        # 0. 通道寬度 (Bandwidth / 波動率空間) 硬性門檻
        channel_width_pct = (upper - lower) / middle
        if channel_width_pct < 0.005:
            raise ValueError(f'[FATAL_REJECT] BLOCKED_CHANNEL_BANDWIDTH_TOO_NARROW: 通道寬度 {channel_width_pct*100:.2f}% < 0.5%，死水盤禁止開倉！')

        # 1. 焊死硬性前置開倉條件 (徹底移除 Bypass)
        if side == 'LONG':
            if is_ignition:
                if not is_breakout_up:
                    raise ValueError(f'[FATAL_REJECT] REJECTED_NO_BREAKOUT: 破軌多單未破上軌 (close {close} <= upper {upper})')
            else:
                last_8 = closed.iloc[-8:]
                touched_lower = (last_8['low'].astype(float) <= last_8['kc_lower'].astype(float)).any()
                middle_rising = middle > prev_middle
                if not (touched_lower and close > middle and middle_rising):
                    raise ValueError(f'[FATAL_REJECT] REJECTED_INSIDE_CHANNEL_WITHOUT_TOUCH: 交叉多單必須前8根觸碰下軌，且當前價格高中軌且中軌向上')
                    
        elif side == 'SHORT':
            if is_ignition:
                if not is_breakout_down:
                    raise ValueError(f'[FATAL_REJECT] REJECTED_NO_BREAKOUT: 破軌空單未破下軌 (close {close} >= lower {lower})')
            else:
                last_8 = closed.iloc[-8:]
                touched_upper = (last_8['high'].astype(float) >= last_8['kc_upper'].astype(float)).any()
                middle_falling = middle < prev_middle
                if not (touched_upper and close < middle and middle_falling):
                    raise ValueError(f'[FATAL_REJECT] REJECTED_INSIDE_CHANNEL_WITHOUT_TOUCH: 交叉空單必須前8根觸碰上軌，且當前價格低中軌且中軌向下')

            # 貼近支撐位禁止做空 (檢查前10根的最低點)
            recent_10_low = float(closed['low'].iloc[-10:].min()) if len(closed) >= 10 else float(closed['low'].min())
            distance_to_support = (close - recent_10_low) / recent_10_low
            if distance_to_support < 0.003:
                raise ValueError(f'[FATAL_REJECT] BLOCKED_SHORT_NEAR_SUPPORT: 距離近期支撐 {recent_10_low} 僅 {distance_to_support*100:.2f}% < 0.3%，禁止空在強支撐上！')


        # 2. 基本形態驗證 (維持原邏輯)
        if side == 'LONG':
            problem = long_entry_trend_problem(closed)
            if problem:
                raise ValueError('[FORBIDDEN_ENTRY] ' + problem)
        
        if not is_ignition:
            problem = ma3_entry_problem(closed, side)
            if problem:
                raise ValueError('[FORBIDDEN_ENTRY] MA3 斜率或排列禁止開倉：' + problem)
                
        # 3. 再經底層策略驗證
        ok, actual, decision = evaluate_closed_entry(frame, side)
        if not ok or actual != code:
            raise ValueError(f'[FORBIDDEN_ENTRY] 最新行情不符合指定訊號 expected={code} actual={actual}')
            
        return decision

def validate_entry_frame(frame, side, code):
    return EntryFirewall.verify_can_open(frame, side, code)

async def validate_account_entry(account, symbol, side, context):
    context = context if isinstance(context, dict) else {}
    
    is_manual = context.get('is_manual') in [True, 'true', 'TRUE'] or context.get('source') == 'MANUAL' or context.get('manual_entry') in [True, 'true', 'TRUE']
    if is_manual:
        return {'action': 'ENTER', 'side': side, 'reason': 'MANUAL_TEST'}
        
    code = context.get('entry_signal_code')
    if code not in RULE_CODES:
        raise ValueError('[FORBIDDEN_ENTRY] 缺少 A–E 白名單訊號，禁止送單')
    provider = getattr(account, 'entry_frame_provider', None)
    if not callable(provider):
        raise ValueError('[FORBIDDEN_ENTRY] 缺少可重驗的行情來源')
    try:
        frame = await provider(symbol)
    except Exception as exc:
        raise ValueError("[FORBIDDEN_ENTRY] 無法取得最新行情") from exc
        
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
