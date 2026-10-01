"""Fail-closed shared entry verification immediately before opening exposure."""
import math
import time

from core.services.entry_contract import ENTRY_CODES, evaluate_entry_contract


class EntryFirewall:
    @classmethod
    def verify_can_open(cls, frame, side, code):
        """Only a supported, freshly validated contract authorizes automatic entry."""
        if code not in ENTRY_CODES:
            raise ValueError('[FORBIDDEN_ENTRY] 已停用的自動入口訊號')
        decision = evaluate_entry_contract(frame, code=code)
        if decision is None or decision['side'] != side:
            raise ValueError('[FORBIDDEN_ENTRY] 最新行情不符合入口訊號')
        return decision


def validate_entry_frame(frame, side, code):
    return EntryFirewall.verify_can_open(frame, side, code)

async def validate_account_entry(account, symbol, side, context):
    context = context if isinstance(context, dict) else {}
    
    is_manual = context.get('is_manual') in [True, 'true', 'TRUE'] or context.get('source') == 'MANUAL' or context.get('manual_entry') in [True, 'true', 'TRUE']
    if is_manual:
        return {'action': 'ENTER', 'side': side, 'reason': 'MANUAL_TEST'}
        
    code = context.get('entry_signal_code')
    if code not in ENTRY_CODES:
        raise ValueError('[FORBIDDEN_ENTRY] 缺少合法入口白名單訊號，禁止送單')
    provider = getattr(account, 'entry_frame_provider', None)
    if not callable(provider):
        raise ValueError('[FORBIDDEN_ENTRY] 缺少可重驗的行情來源')
    try:
        frame = await provider(symbol)
    except Exception as exc:
        raise ValueError("[FORBIDDEN_ENTRY] 無法取得最新行情") from exc
        
    if frame is None or not frame.attrs.get('entry_finality_verified'):
        raise ValueError('[FORBIDDEN_ENTRY] 收線資料尚未通過獨立取樣確認')
    decision = evaluate_entry_contract(frame, code=code, account=account, symbol=symbol)
    if decision is None or decision['side'] != side:
        raise ValueError('[FORBIDDEN_ENTRY] 冷卻或最新入口行情不符')
    stamp = float(decision['confirmation_bar_id'])
    age = time.time() * 1000 - (stamp if decision.get('intrabar') else stamp + 60000)
    if not math.isfinite(age) or not 0 <= age <= (60000 if decision.get('intrabar') else 90000):
        raise ValueError('[FORBIDDEN_ENTRY] 已收線訊號過期或來自未來')
    if context.get('channel_confirmation_bar_id') != stamp:
        raise ValueError('[FORBIDDEN_ENTRY] 下單確認 K 已改變')
    from core.services.candle_data import entry_frame_evidence
    snapshot = context.setdefault('entry_snapshot', {})
    snapshot.update(signal_code=decision['type'], closed_bar=stamp,
                    closed_price=decision['close_price'],
                    entry_phase=decision['entry_phase'],
                    breakout_bar_id=decision['breakout_bar_id'],
                    pair_confirmation_bar_id=decision['pair_confirmation_bar_id'],
                    finality_server_ms=frame.attrs.get('entry_finality_server_ms'),
                    evidence=entry_frame_evidence(frame))
    return decision
