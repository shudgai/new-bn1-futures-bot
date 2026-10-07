"""Short-lived, process-bound evidence required at exposure commit boundaries."""
import hashlib
import hmac
import json
import math
import secrets
import time

VERSION = 'entry-gate-20261007-v50-shared-ten-bar-pivot-live-expiry'
_SECRET = secrets.token_bytes(32)
MAX_AGE_MS = 5000


def is_manual(context):
    return any(context.get(k) in (True,'true','TRUE') for k in ('is_manual','manual_entry')) or context.get('source')=='MANUAL'


def _signature(proof):
    payload={k:v for k,v in proof.items() if k!='signature'}
    return hmac.new(_SECRET,json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode(),hashlib.sha256).hexdigest()


def issue_proof(context,symbol,side,decision,frame):
    from core.services.candle_data import entry_frame_evidence
    snapshot=context.setdefault('entry_snapshot',{})
    snapshot.update(symbol=symbol,side=side,signal_code=decision['type'],
                    signal_id=context.get('signal_id'),candidate_bar_id=context.get('candidate_bar_id'),
                    closed_bar=decision['confirmation_bar_id'],quote_price=decision['price'],
                    entry_phase=decision['entry_phase'],
                    breakout_bar_id=decision['breakout_bar_id'],
                    pair_confirmation_bar_id=decision['pair_confirmation_bar_id'],
                    pending_signal_id=decision.get('pending_signal_id'),gate_version=VERSION,
                    evidence=entry_frame_evidence(frame))
    from core.services.entry_contract import ENTRY_EVIDENCE_KEYS
    snapshot.update({key:decision[key] for key in ENTRY_EVIDENCE_KEYS if key in decision})
    proof=dict(version=VERSION,symbol=symbol,side=side,code=decision['type'],
               bar=decision['confirmation_bar_id'],quote=decision['price'],
               signal_id=snapshot.get('signal_id'),candidate_bar_id=snapshot.get('candidate_bar_id'),
               pending_signal_id=snapshot.get('pending_signal_id'),
               pair_confirmation_bar_id=snapshot.get('pair_confirmation_bar_id'),
               cap_origin_id=snapshot.get('cap_origin_id'),
               entry_phase=snapshot['entry_phase'],
               same_bar_entry_bar_ms=snapshot.get('same_bar_entry_bar_ms'),
               same_bar_exit_deadline_ms=snapshot.get('same_bar_exit_deadline_ms'),
               issued_ms=time.time()*1000,
               evidence_hash=hashlib.sha256(json.dumps(snapshot['evidence'],sort_keys=True,default=str).encode()).hexdigest())
    proof['signature']=_signature(proof)
    snapshot['gate_proof']=proof
    return proof


def assert_commit_proof(account,symbol,side,context):
    context=context or {}
    if is_manual(context):
        return
    if getattr(account,'position_meta',{}).get('_entry_gate_halts',{}).get(symbol):
        raise ValueError('[FORBIDDEN_ENTRY] GATE_INTEGRITY_HALT: '+symbol)
    try:
        snapshot=context['entry_snapshot'];proof=snapshot['gate_proof']
        age=time.time()*1000-float(proof['issued_ms'])
        if not math.isfinite(age) or not 0<=age<=MAX_AGE_MS:
            raise ValueError('EXPIRED_GATE_PROOF')
        deadline = proof.get('same_bar_exit_deadline_ms')
        if deadline is not None and time.time()*1000 >= float(deadline):
            raise ValueError('EXPIRED_SAME_BAR_ENTRY')
        if (proof['version']!=VERSION or proof['symbol']!=symbol or proof['side']!=side
                or proof['code']!=context.get('entry_signal_code')
                or proof['bar']!=context.get('channel_confirmation_bar_id')
                or proof['signal_id']!=context.get('signal_id') or proof['signal_id']!=snapshot.get('signal_id')
                or proof['candidate_bar_id']!=context.get('candidate_bar_id') or proof['candidate_bar_id']!=snapshot.get('candidate_bar_id')
                or proof['pending_signal_id']!=snapshot.get('pending_signal_id')
                or proof['pair_confirmation_bar_id']!=snapshot.get('pair_confirmation_bar_id')
                or proof['cap_origin_id']!=snapshot.get('cap_origin_id')
                or snapshot.get('symbol')!=symbol or snapshot.get('side')!=side
                or snapshot.get('signal_code')!=proof['code'] or snapshot.get('closed_bar')!=proof['bar']
                or snapshot.get('gate_version')!=VERSION
                or proof['quote']!=snapshot.get('quote_price')
                or proof['entry_phase']!=snapshot.get('entry_phase')
                or proof['same_bar_entry_bar_ms']!=snapshot.get('same_bar_entry_bar_ms')
                or proof['same_bar_exit_deadline_ms']!=snapshot.get('same_bar_exit_deadline_ms')
                or proof['evidence_hash']!=hashlib.sha256(json.dumps(snapshot['evidence'],sort_keys=True,default=str).encode()).hexdigest()
                or not hmac.compare_digest(proof['signature'],_signature(proof))):
            raise ValueError('INVALID_GATE_PROOF')
    except (KeyError,TypeError,ValueError,OverflowError) as exc:
        meta=getattr(account,'position_meta',None)
        if isinstance(meta,dict):
            meta.setdefault('_entry_gate_halts',{})[symbol]={'reason':str(exc),'time':time.time(),'version':VERSION}
            account.save_state()
        raise ValueError('[FORBIDDEN_ENTRY] GATE_INTEGRITY_HALT: '+symbol+' '+str(exc)) from exc
    from core.services.cap_breakout_entry import SYMBOL as CAP_SYMBOL, STATE_KEY, CODES
    if (proof['code'] in ('KC_LIVE_BODY_BREAKOUT_LONG', 'KC_LIVE_BODY_BREAKOUT_SHORT')
            and float(proof['bar']) != math.floor(time.time()/60)*60000):
        raise ValueError('[FORBIDDEN_ENTRY] LIVE_BREAKOUT_CANDLE_EXPIRED')
    if symbol == CAP_SYMBOL and proof['code'] in (CODES | {'KC_2BAR_CONFIRM_LONG', 'KC_2BAR_CONFIRM_SHORT'}):
        state = getattr(account, 'position_meta', {}).get(STATE_KEY, {}).get(symbol, {})
        second = snapshot.get('pair_confirmation_bar_id')
        if second is None or float(second) <= state.get('cancelled_second_ms', 0):
            raise ValueError('[FORBIDDEN_ENTRY] CAP_ORIGIN_CANCELLED')
        if proof['code'] in CODES:
            origin = state.get('origin') or {}
            if (origin.get('id') != snapshot.get('cap_origin_id')
                    or origin.get('side') != side
                    or state.get('session') != getattr(account, '_cap_breakout_session', None)
                    or not 0 <= time.time()*1000-state.get('last_quote_ms', 0) <= MAX_AGE_MS):
                raise ValueError('[FORBIDDEN_ENTRY] CAP_ORIGIN_NOT_CURRENT')
