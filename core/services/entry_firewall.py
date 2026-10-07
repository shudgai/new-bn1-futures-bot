"""Fail-closed shared entry verification immediately before opening exposure."""
import math
import time

from core.services.entry_contract import ENTRY_CODES, ENTRY_EVIDENCE_KEYS, evaluate_entry_contract


class EntryFirewall:
    @classmethod
    def verify_can_open(cls, frame, side, code, symbol=''):
        """Only a supported, freshly validated contract authorizes automatic entry."""
        if code not in ENTRY_CODES:
            raise ValueError('[FORBIDDEN_ENTRY] 已停用的自動入口訊號')
        decision = evaluate_entry_contract(frame, code=code, symbol=symbol)
        if decision is None or decision['side'] != side:
            raise ValueError('[FORBIDDEN_ENTRY] 最新行情不符合入口訊號')
        return decision


def validate_entry_frame(frame, side, code, symbol=''):
    return EntryFirewall.verify_can_open(frame, side, code, symbol)

async def _validate_account_entry(account, symbol, side, context):
    from core.config import is_entry_disabled
    if is_entry_disabled(symbol):
        raise ValueError("[FORBIDDEN_ENTRY] ENTRY_DISABLED_SYMBOL: " + symbol)
    context = context if isinstance(context, dict) else {}
    from core.services.auto_reverse import AUTO_REVERSE_ENABLED
    if not AUTO_REVERSE_ENABLED and any(context.get(key) for key in ('direct_reverse_token', 'auto_reverse_token')):
        raise ValueError('[FORBIDDEN_ENTRY] AUTO_REVERSE_DISABLED')
    direct_ticket = None
    if context.get('direct_reverse_token'):
        from core.services.direct_reverse import authority
        direct_ticket = authority(account, symbol, side, context)
        if any(context.get(k) for k in ('is_manual', 'manual_entry', 'auto_reverse_token')) or context.get('source') == 'MANUAL':
            raise ValueError('[FORBIDDEN_ENTRY] 反向不得使用手動豁免')
    from core.services.auto_reverse import matched_ticket
    reverse_state = getattr(account, 'position_meta', {}).get('_auto_reverse_tickets', {}).get(symbol)
    if reverse_state and reverse_state.get('mode') == 'direct_netting_v1' and reverse_state.get('phase') == 'partial':
        raise ValueError('[FORBIDDEN_ENTRY] 反向部分成交，禁止自動補單')
    if (reverse_state and reverse_state.get('mode') == 'direct_netting_v1'
            and reverse_state.get('phase') in ('submitting', 'unknown')
            and (not context.get('direct_reverse_token')
                 or context.get('direct_reverse_token') != reverse_state.get('token'))):
        raise ValueError('[FORBIDDEN_ENTRY] 反向成交狀態未確認，禁止新增訂單')
    if (AUTO_REVERSE_ENABLED and reverse_state and reverse_state.get('phase') in ('closing', 'closed', 'prepared', 'submitting', 'unknown')
            and reverse_state.get('bar') == math.floor(time.time()/60)*60000
            and context.get('auto_reverse_token') != reverse_state.get('token')
            and context.get('direct_reverse_token') != reverse_state.get('token')):
        raise ValueError('[FORBIDDEN_ENTRY] 自動反向處理中，禁止其他入口搶單')
    if context.get('auto_reverse_token'):
        reverse_ticket = matched_ticket(account, symbol, side)
        if not reverse_ticket or reverse_ticket['token'] != context['auto_reverse_token']:
            raise ValueError('[FORBIDDEN_ENTRY] 未匹配成功自動反向平倉')
    
    is_manual = context.get('is_manual') in [True, 'true', 'TRUE'] or context.get('source') == 'MANUAL' or context.get('manual_entry') in [True, 'true', 'TRUE']
    from core.services.wait_authority import CODES as WAIT_CODES
    wait_entry = context.get('entry_signal_code') in WAIT_CODES
    if wait_entry:
        submit_lock = getattr(account, '_wait_submit_lock', None)
        if is_manual or submit_lock is None or not submit_lock.locked():
            raise ValueError('[FORBIDDEN_ENTRY] WAIT_REQUIRES_SHARED_SUBMIT_LOCK')
    if is_manual:
        return {'action': 'ENTER', 'side': side, 'reason': 'MANUAL_TEST'}
        
    from core.services.entry_gate_integrity import VERSION
    if getattr(account, 'position_meta', {}).get('_entry_gate_halts', {}).get(symbol):
        raise ValueError('[FORBIDDEN_ENTRY] GATE_INTEGRITY_HALT: ' + symbol)
    snapshot_version = (context.get('entry_snapshot') or {}).get('gate_version')
    if snapshot_version is not None and snapshot_version != VERSION:
        raise ValueError('[FORBIDDEN_ENTRY] 原始訊號規則版本已過期')

    code = context.get('entry_signal_code')
    if code not in ENTRY_CODES:
        raise ValueError('[FORBIDDEN_ENTRY] 缺少合法入口白名單訊號，禁止送單')
        
    # Retain identity checks, but a forming candle must be revalidated.
    snapshot = context.get('entry_snapshot')
    if snapshot:
        # Check snapshot identity
        if snapshot.get('symbol') != symbol:
            raise ValueError('[FORBIDDEN_ENTRY] ENTRY_FIREWALL_REJECT: wrong symbol')
        if snapshot.get('side') != side:
            raise ValueError('[FORBIDDEN_ENTRY] ENTRY_FIREWALL_REJECT: wrong side')
        if snapshot.get('signal_code') != code:
            raise ValueError('[FORBIDDEN_ENTRY] ENTRY_FIREWALL_REJECT: wrong signal_code')
        if snapshot.get('signal_id') != context.get('signal_id'):
            raise ValueError('[FORBIDDEN_ENTRY] ENTRY_FIREWALL_REJECT: wrong signal_id')
        if snapshot.get('candidate_bar_id') != context.get('candidate_bar_id'):
            raise ValueError('[FORBIDDEN_ENTRY] ENTRY_FIREWALL_REJECT: wrong candidate_bar_id')
        if snapshot.get('closed_bar') != context.get('channel_confirmation_bar_id'):
            raise ValueError('[FORBIDDEN_ENTRY] ENTRY_FIREWALL_REJECT: wrong closed_bar_id')
            
        # Continue through fresh market validation; cached color is not permission.
        
    provider = getattr(account, 'entry_frame_provider', None)
    if not callable(provider):
        raise ValueError('[FORBIDDEN_ENTRY] 缺少可重驗的行情來源')
    try:
        frame = await provider(symbol)
    except Exception as exc:
        raise ValueError("[FORBIDDEN_ENTRY] 無法取得最新行情") from exc
        
    if frame is None or not frame.attrs.get('entry_finality_verified'):
        raise ValueError('[FORBIDDEN_ENTRY] 收線資料尚未通過獨立取樣確認')
    now_ms=time.time()*1000
    for key in ('entry_finality_server_ms','entry_quote_ms'):
        if frame.attrs.get(key) is not None:
            age=now_ms-float(frame.attrs[key])
            if not math.isfinite(age) or not 0<=age<=5000:
                raise ValueError('[FORBIDDEN_ENTRY] 最後行情取樣過期或來自未來: '+key)
    diagnostics = {}
    from core.services.cap_breakout_entry import observe_cap_breakout
    observe_cap_breakout(account, symbol, frame, float(frame.iloc[-1].close),
                        frame.attrs.get('entry_quote_ms', frame.attrs.get('entry_finality_server_ms')))
    decision = evaluate_entry_contract(frame, code=code, account=account, symbol=symbol, diagnostics=diagnostics,
                                      evaluate_held=direct_ticket is not None)
    if decision is None or decision['side'] != side:
        raise ValueError('[FORBIDDEN_ENTRY] 冷卻或最新入口行情不符: ' + diagnostics.get('reason', 'INVALID_ENTRY_DATA'))
    ticket = None if wait_entry else getattr(account, 'channel_profit_reentries', {}).get(symbol)
    if ticket:
        from core.services.closed_breakout_entry import matched_reentry_close
        from core.services.strategies.outer_strategy import abnormal_pullback_ready
        import copy
        filled_at = matched_reentry_close(account, symbol, ticket)
        if (context.get('profit_reentry_token') != ticket.get('token')
                or ticket.get('phase') != 'closed' or ticket.get('side') != side
                or not filled_at
                or float(frame.iloc[-1].timestamp) < math.floor(filled_at/60000)*60000):
            raise ValueError('[FORBIDDEN_ENTRY] 未匹配成功平倉及重開票據')
        abnormal = any(k in str(ticket.get('close_reason') or '') for k in ('ADVERSE','ABNORMAL','WATERFALL'))
        if abnormal and not abnormal_pullback_ready(copy.deepcopy(ticket), frame, float(frame.iloc[-1].close)):
            raise ValueError('[FORBIDDEN_ENTRY] 異常平倉回踩尚未完成')
    deadline = decision.get('same_bar_exit_deadline_ms')
    if deadline is not None and time.time()*1000 >= float(deadline):
        raise ValueError('[FORBIDDEN_ENTRY] 當根入口已到期')
    expected_id = snapshot.get('pending_signal_id') if snapshot else None
    if expected_id is not None and expected_id != decision.get('pending_signal_id'):
        raise ValueError('[FORBIDDEN_ENTRY] 原始突破訊號已改變')
    stamp = float(decision['confirmation_bar_id'])
    age = time.time() * 1000 - (stamp if decision.get('intrabar') else stamp + 60000)
    if not math.isfinite(age) or not 0 <= age <= (60000 if decision.get('intrabar') else 90000):
        raise ValueError('[FORBIDDEN_ENTRY] 已收線訊號過期或來自未來')
    if context.get('channel_confirmation_bar_id') != stamp:
        raise ValueError('[FORBIDDEN_ENTRY] 下單確認 K 已改變')
    from core.services.candle_data import entry_frame_evidence
    if snapshot:
        snapshot.update(signal_code=decision['type'], closed_bar=stamp,
                        closed_price=decision['close_price'],
                        entry_phase=decision['entry_phase'],
                        breakout_bar_id=decision['breakout_bar_id'],
                        pair_confirmation_bar_id=decision['pair_confirmation_bar_id'],
                        finality_server_ms=frame.attrs.get('entry_finality_server_ms'),
                        evidence=entry_frame_evidence(frame))
        snapshot.update({key: decision[key] for key in ENTRY_EVIDENCE_KEYS if key in decision})
    from core.services.entry_gate_integrity import issue_proof
    issue_proof(context, symbol, side, decision, frame)
    if direct_ticket is not None:
        import copy
        from core.services.direct_reverse import authority, risk_plan
        from core.services.auto_reverse import entry_halted
        authority(account, symbol, side, context)
        engine = getattr(account, '_structure_close_engine', None)
        if engine is None or entry_halted(engine, symbol):
            raise ValueError('[FORBIDDEN_ENTRY] 反向引擎或風控失效')
        plan = risk_plan(account, engine, account.positions[symbol], decision, float(decision['price']))
        direct_ticket.update(decision=copy.deepcopy(decision), plan=plan, context=copy.deepcopy(context))
    return decision


async def validate_account_entry(account, symbol, side, context):
    import json
    try:
        decision = await _validate_account_entry(account, symbol, side, context)
    except Exception as exc:
        if callable(getattr(account,'log',None)):
            account.log('ENTRY_GATE_AUDIT '+json.dumps(dict(symbol=symbol,side=side,result='REJECT',reason=str(exc)),ensure_ascii=False),'WARNING')
        raise
    if callable(getattr(account,'log',None)):
        proof=((context or {}).get('entry_snapshot') or {}).get('gate_proof')
        account.log('ENTRY_GATE_AUDIT '+json.dumps(dict(symbol=symbol,side=side,result='PASS',proof=proof),ensure_ascii=False),'INFO')
    return decision
