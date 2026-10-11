"""Fail-closed shared entry verification immediately before opening exposure."""
import math
import time

from core.services.entry_contract import ENTRY_CODES, ENTRY_EVIDENCE_KEYS, evaluate_entry_contract


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
    from core.config import is_entry_disabled
    if is_entry_disabled(symbol):
        raise ValueError("[FORBIDDEN_ENTRY] ENTRY_DISABLED_SYMBOL: " + symbol)
    context = context if isinstance(context, dict) else {}
    
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
    grace = context.get('pipeline_ttl_grace')
    if grace is not None:
        from core.gates.pipeline import PIPELINE_ENTRY_TYPES
        try:
            elapsed = time.monotonic() - float(grace['authorized_at_monotonic'])
            authorized_price = float(grace['authorized_price'])
            atr = float(grace['entry_atr'])
            quote_timestamp = float(grace['quote_timestamp'])
            quote = float(frame.iloc[-1]['close'])
            current_bar = float(frame.iloc[-1]['timestamp'])
            authorized_decision = grace['decision']
            stamp = float(authorized_decision['confirmation_bar_id'])
            adverse_move = (
                max(0.0, authorized_price - quote)
                if side == 'LONG'
                else max(0.0, quote - authorized_price)
            )
            quote_age = time.time() - quote_timestamp
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            raise ValueError('[FORBIDDEN_ENTRY] TTL grace evidence is invalid')
        failures = []
        if code not in PIPELINE_ENTRY_TYPES:
            failures.append('UNSUPPORTED_ENTRY_TYPE')
        if (
            not isinstance(authorized_decision, dict)
            or not authorized_decision.get('_is_authorized')
            or authorized_decision.get('type') != code
            or authorized_decision.get('side') != side
        ):
            failures.append('AUTHORIZATION_MISMATCH')
        if (
            not isinstance(authorized_decision, dict)
            or authorized_decision.get('pending_signal_id') != grace.get('pending_signal_id')
            or grace.get('pending_signal_id') != context.get('signal_id')
        ):
            failures.append('SIGNAL_ID_MISMATCH')
        values = (
            elapsed, authorized_price, atr, quote_timestamp, quote,
            current_bar, stamp, quote_age, adverse_move,
        )
        if not all(math.isfinite(value) for value in values):
            failures.append('NONFINITE_EVIDENCE')
        if not math.isfinite(elapsed) or not 0 <= elapsed <= 3:
            failures.append('AUTHORIZATION_TTL_EXPIRED')
        if not math.isfinite(authorized_price) or authorized_price <= 0:
            failures.append('INVALID_AUTHORIZED_PRICE')
        if not math.isfinite(atr) or atr <= 0:
            failures.append('INVALID_ATR')
        if not math.isfinite(quote) or quote <= 0:
            failures.append('INVALID_QUOTE')
        if not math.isfinite(current_bar) or current_bar <= 0:
            failures.append('INVALID_CURRENT_BAR')
        if not math.isfinite(quote_age) or not 0 <= quote_age <= 30:
            failures.append('STALE_QUOTE')
        adverse_atr = adverse_move / atr if math.isfinite(atr) and atr > 0 else math.inf
        if not math.isfinite(adverse_atr) or adverse_atr > 0.8:
            failures.append('ADVERSE_SLIPPAGE_LIMIT')
        if failures:
            metrics = (
                f'elapsed_seconds={elapsed:.3f} quote_age_seconds={quote_age:.3f} '
                f'adverse_slippage_atr={adverse_atr:.3f} current_bar={current_bar} '
                f'authorized_bar={stamp}'
            )
            raise ValueError(
                '[FORBIDDEN_ENTRY] TTL grace rejected: '
                + ','.join(dict.fromkeys(failures))
                + f'; {metrics}'
            )
        decision = dict(authorized_decision)
        decision['price'] = quote
        decision['close_price'] = quote
    else:
        diagnostics = {}
        decision = evaluate_entry_contract(frame, code=code, account=account, symbol=symbol, diagnostics=diagnostics)
        if decision is None or decision['side'] != side:
            raise ValueError('[FORBIDDEN_ENTRY] 冷卻或最新入口行情不符: ' + diagnostics['reason'])
    expected_id = snapshot.get('pending_signal_id') if snapshot else None
    if expected_id is not None and expected_id != decision.get('pending_signal_id'):
        raise ValueError('[FORBIDDEN_ENTRY] 原始突破訊號已改變')
    stamp = float(decision['confirmation_bar_id'])
    if grace is None:
        age = time.time() * 1000 - (stamp if decision.get('intrabar') else stamp + 60000)
        if not math.isfinite(age) or not 0 <= age <= (60000 if decision.get('intrabar') else 90000):
            raise ValueError('[FORBIDDEN_ENTRY] 已收線訊號過期或來自未來')
    else:
        decision['intrabar'] = True
    if context.get('channel_confirmation_bar_id') != stamp:
        raise ValueError('[FORBIDDEN_ENTRY] 下單確認 K 已改變')
    from core.services.candle_data import entry_frame_evidence
    if snapshot:
        snapshot.update(signal_code=decision['type'], closed_bar=stamp,
                        quote_price=decision['price'],
                        closed_price=decision['close_price'],
                        entry_phase=decision['entry_phase'],
                        breakout_bar_id=decision['breakout_bar_id'],
                        pair_confirmation_bar_id=decision['pair_confirmation_bar_id'],
                        finality_server_ms=frame.attrs.get('entry_finality_server_ms'),
                        evidence=entry_frame_evidence(frame))
        snapshot.update({key: decision[key] for key in ENTRY_EVIDENCE_KEYS if key in decision})
    return decision
