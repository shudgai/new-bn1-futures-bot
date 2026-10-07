"""Reverse on a complete opposite entry using one durable netting transaction."""
import asyncio
import math
import time
import uuid

KEY = '_auto_reverse_tickets'
CLOSE_PREFIX = '自動反向平倉 '


def tickets(account):
    return account.position_meta.setdefault(KEY, {})


def matched_ticket(account, symbol, side=None, bar=None):
    ticket = getattr(account, 'position_meta', {}).get(KEY, {}).get(symbol)
    if not ticket or ticket.get('phase') not in ('closing', 'closed'):
        return None
    if side is not None and ticket.get('side') != side:
        return None
    if bar is not None and float(bar) != ticket.get('bar'):
        return None
    if math.floor(time.time()/60)*60000 != ticket.get('bar'):
        return None
    closes = [t for t in getattr(account, 'trades', [])
              if t.get('symbol') == symbol and t.get('action') == 'CLOSE_'+ticket['old_side']
              and t.get('reason') == CLOSE_PREFIX+ticket['token']
              and t.get('status') == 'CLOSED'
              and ticket['requested_ms'] <= float(t.get('id') or 0) < ticket['bar']+60000]
    if not closes or symbol in getattr(account, 'positions', {}):
        return None
    close_id = max(float(t['id']) for t in closes)
    if any(t.get('symbol') == symbol and t.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
           and float(t.get('id') or 0) >= close_id for t in account.trades):
        return None
    return ticket


def reverse_quantity(account, symbol, side, context, max_qty):
    """Never resize silently or grant a quantity override without a verified close."""
    token = (context or {}).get('auto_reverse_token')
    if not token:
        return None
    ticket = matched_ticket(account, symbol, side)
    if not ticket or ticket['token'] != token:
        raise ValueError('AUTO_REVERSE_UNVERIFIED_CLOSE')
    qty = float(ticket['qty'])
    if not math.isfinite(qty) or qty <= 0 or (qty > max_qty and not math.isclose(qty, max_qty, rel_tol=1e-10)):
        raise ValueError('AUTO_REVERSE_SAME_QTY_EXCEEDS_RISK_BUDGET')
    return qty


def entry_halted(engine, symbol):
    from core.config import DEFAULT_SYMBOLS, is_entry_disabled
    account = engine.account
    daily = getattr(account, 'daily_loss_limit_hit', None)
    return (not getattr(engine, 'is_running', False) or symbol not in DEFAULT_SYMBOLS
            or is_entry_disabled(symbol) or bool(daily and daily()[0])
            or bool(getattr(account, 'circuit_breaker_latched', False))
            or bool(getattr(account, 'position_meta', {}).get('_entry_gate_halts', {}).get(symbol))
            or getattr(engine, '_market_crash_entries_paused', lambda _: False)(time.time())
            or symbol in getattr(account, 'pending_limit_orders', {}))


async def try_auto_reverse(engine, symbol, frame, price):
    from core.services.entry_contract import evaluate_entry_contract
    from core.services.exits.hard_stop_service import enforce_hard_stop
    from core.services.exits.peak_trailing_exit import position_identity
    from core.services.direct_reverse import reconcile, risk_plan
    account = engine.account
    locks = getattr(engine, '_auto_reverse_locks', None)
    if locks is None:
        locks = engine._auto_reverse_locks = {}
    lock = locks.setdefault(symbol, asyncio.Lock())
    if lock.locked():
        return True
    async with lock:
        ticket = tickets(account).get(symbol)
        if ticket and ticket.get('mode') == 'direct_netting_v1' and ticket.get('phase') == 'partial':
            return False  # Partial execution needs reconciliation, never an automatic top-up.
        # Resolve an already-submitted order even after restart/bar rollover.
        if ticket and ticket.get('mode') == 'direct_netting_v1' and ticket.get('phase') in ('submitting', 'unknown'):
            if symbol in account.closing_lock:
                return True
            account.closing_lock.add(symbol)
            try:
                await reconcile(account, symbol, ticket)
            finally:
                account.closing_lock.discard(symbol)
            return True
        if entry_halted(engine, symbol):
            return False
        original = account.positions.get(symbol)
        if not original or str(original.get('entry_mode', '')).upper() != 'CHANNEL_SWING':
            return False
        if symbol in account.closing_lock:
            return False
        if await enforce_hard_stop(account, symbol, price):
            return True
        bar = math.floor(time.time()/60)*60000
        if ticket and ticket.get('bar') == bar and ticket.get('phase') in ('consumed', 'partial', 'rejected'):
            return False
        preliminary = evaluate_entry_contract(frame, price, account=account, symbol=symbol, evaluate_held=True)
        if not preliminary or preliminary['side'] == original['side']:
            return False
        identity = position_identity(original)
        fresh = await engine._entry_boundary_frame(symbol)
        if fresh is None or fresh.empty:
            return False
        quote = float(fresh.iloc[-1].close)
        decision = evaluate_entry_contract(fresh, quote, account=account, symbol=symbol, evaluate_held=True)
        if not decision or decision['side'] == original['side'] or entry_halted(engine, symbol):
            return False
        if not await engine._execution_price_is_safe(symbol, decision['side']):
            return False
        if (account.positions.get(symbol) is not original or position_identity(original) != identity
                or math.floor(time.time()/60)*60000 != bar):
            return False
        try:
            risk_plan(account, engine, original, decision, quote)
        except ValueError as exc:
            account.log(f'DIRECT_REVERSE_BLOCK {symbol} {exc}', 'WARNING')
            return False
        token = uuid.uuid4().hex
        ticket = dict(token=token, mode='direct_netting_v1', phase='prepared', bar=bar,
                      client_order_id='rev_'+token, side=decision['side'], old_side=original['side'],
                      qty=float(original['qty']), leverage=int(original['leverage']),
                      identity=identity, code=decision['type'], requested_ms=int(time.time()*1000))
        tickets(account)[symbol] = ticket
        account.save_state()
        return await account.reverse_position(engine, symbol, quote, decision, ticket)
