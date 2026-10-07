"""One-way netting reversal: one order, one durable transaction, no flat/reopen gap."""
import copy
import math
import time

from core.services.auto_reverse import KEY, entry_halted
from core.services.exits.peak_trailing_exit import position_identity

ACTIVE = {'prepared', 'submitting'}


def authority(account, symbol, side, context):
    from core.services.auto_reverse import AUTO_REVERSE_ENABLED
    if not AUTO_REVERSE_ENABLED:
        raise ValueError('AUTO_REVERSE_DISABLED')
    ticket = account.position_meta.get(KEY, {}).get(symbol)
    held = account.positions.get(symbol)
    if (not ticket or ticket.get('mode') != 'direct_netting_v1'
            or ticket.get('phase') not in ACTIVE or not held
            or context.get('direct_reverse_token') != ticket.get('token')
            or ticket['side'] != side or side == held['side']
            or ticket['identity'] != position_identity(held)
            or ticket['bar'] != math.floor(time.time()/60)*60000):
        raise ValueError('DIRECT_REVERSE_AUTHORITY_INVALID')
    return ticket


def risk_plan(account, engine, position, decision, price):
    from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT, MAX_POSITION_MARGIN_LOSS_RATIO
    from core.services.structure_risk_sizing import structure_risk_plan
    qty, leverage = float(position['qty']), int(position['leverage'])
    sign = 1 if position['side'] == 'LONG' else -1
    raw = sign*(price-float(position['entry_price']))*qty
    released = float(position.get('margin') or 0)+raw-price*qty*TAKER_FEE_RATE
    # Exchange cross-margin availability already includes marked unrealized PnL.
    included_pnl = float(position.get('unrealized_pnl') or 0) if hasattr(account, 'exchange') else 0.
    available = float(account.get_available_balance())+released-included_pnl
    wallet = float(account.get_wallet_balance())+raw-price*qty*TAKER_FEE_RATE
    budget = engine._half_wallet_entry_margin(wallet, available, leverage)
    plan = structure_risk_plan(price, decision['side'], decision['entry_atr'],
                               decision['structure_risk_stop'], budget, leverage,
                               MAX_POSITION_MARGIN_LOSS_RATIO, TAKER_FEE_RATE, SLIPPAGE_PCT)
    if (not math.isfinite(qty) or qty <= 0
            or qty*price/leverage > plan['amount']+1e-12
            or qty*price/leverage+qty*price*TAKER_FEE_RATE > available+1e-12):
        raise ValueError('DIRECT_REVERSE_SAME_QTY_EXCEEDS_RISK_BUDGET')
    return plan


def context_for(symbol, ticket, decision):
    stamp = decision['confirmation_bar_id']
    signal_id = decision.get('pending_signal_id') or f'{symbol}_REVERSE_{int(stamp)}_{decision["side"]}'
    return dict(entry_mode='CHANNEL_SWING', entry_signal_code=decision['type'],
                signal_id=signal_id, candidate_bar_id=stamp,
                channel_confirmation_bar_id=stamp, direct_reverse_token=ticket['token'],
                entry_snapshot=dict(symbol=symbol, side=decision['side'],
                                    signal_code=decision['type'], signal_id=signal_id,
                                    candidate_bar_id=stamp, closed_bar=stamp))


def settle(account, symbol, ticket, execution_price, filled, order_id=None, paper=False):
    """Record the two accounting legs of a single fill without exposing an intermediate FLAT."""
    from core.config import TAKER_FEE_RATE
    from core.paper_account import ENTRY_CONTEXT_KEYS, get_taipei_now_str
    from core.services.exits.entry_atr_protection import initialize_atr_protection
    if any(t.get('reverse_id') == ticket['token'] for t in account.trades):
        return  # A recovered acknowledgement cannot settle the same fill twice.
    old = ticket['original']
    qty = float(old['qty'])
    filled = float(filled)
    if not math.isfinite(filled) or not 0 < filled <= 2*qty+qty*1e-10:
        raise ValueError('INVALID_REVERSE_FILL')
    if not math.isfinite(execution_price) or execution_price <= 0:
        raise ValueError('INVALID_REVERSE_PRICE')
    closed = min(qty, filled)
    opened = max(0., filled-qty)
    sign = 1 if old['side'] == 'LONG' else -1
    raw = sign*(execution_price-old['entry_price'])*closed
    close_fee = execution_price*closed*TAKER_FEE_RATE
    net = raw-close_fee-old['entry_price']*closed*TAKER_FEE_RATE
    now = time.time()
    common = dict(symbol=symbol, price=execution_price, time=get_taipei_now_str('%m/%d %H:%M:%S'),
                  reverse_id=ticket['token'], execution_action='REVERSE', exchange_order_id=order_id,
                  reverse_completed=math.isclose(filled,2*qty,rel_tol=1e-10))
    close_trade = dict(common, id=int(now*1000), action='CLOSE_'+old['side'], side=old['side'],
                       qty=closed, amount=old.get('margin', 0)*closed/qty,
                       fee=close_fee+old['entry_price']*closed*TAKER_FEE_RATE,
                       pnl=net, status='CLOSED', reason='直接反向 '+ticket['token'],
                       **{k:copy.deepcopy(old.get(k)) for k in ENTRY_CONTEXT_KEYS})
    trades = [close_trade]
    plan = ticket['plan']
    if opened:
        new = dict(symbol=symbol, side=ticket['side'], entry_price=execution_price,
                   qty=opened, margin=opened*execution_price/old['leverage'], leverage=old['leverage'],
                   entry_mode='CHANNEL_SWING', signal_score=100, open_timestamp=now,
                   open_time=get_taipei_now_str(), reason='直接反向 '+ticket['token'],
                   mark_price=execution_price, unrealized_pnl=0., peak_pnl_pct=0.,
                   atr=ticket['decision']['entry_atr'], tp=0., reverse_id=ticket['token'],
                   entry_snapshot=copy.deepcopy(ticket['context']['entry_snapshot']),
                   channel_confirmation_bar_id=ticket['bar'],
                   **{k:v for k,v in plan.items() if k.startswith('structure_')})
        sign_new = 1 if new['side'] == 'LONG' else -1
        stop = (min if sign_new == 1 else max)(plan['stop'], execution_price-sign_new*1.5*new['atr'])
        initialize_atr_protection(new, execution_price, new['side'], new['atr'], initial_stop=stop)
        open_fee = opened*execution_price*TAKER_FEE_RATE
        trades.insert(0, dict(common, id=int(now*1000)+1, action='OPEN_'+new['side'], side=new['side'],
                              qty=opened, amount=new['margin'], leverage=new['leverage'], fee=open_fee,
                              pnl=0., status='OPEN', reason=new['reason'],
                              **{k:copy.deepcopy(new.get(k)) for k in ENTRY_CONTEXT_KEYS}))
        account.positions[symbol] = new
        account.position_meta[symbol] = copy.deepcopy(new)
    elif closed < qty:
        new = copy.deepcopy(old)
        new['qty'] = qty-closed
        new['margin'] = old.get('margin',0)*(qty-closed)/qty
        account.positions[symbol] = new
        open_fee = 0.
    else:
        account.positions.pop(symbol, None)
        account.position_meta.pop(symbol, None)
        open_fee = 0.
    if paper:
        account.balance += old.get('margin',0)*closed/qty+raw-close_fee
        if opened:
            account.balance -= account.positions[symbol]['margin']+open_fee
    account.realized_pnl += net
    account.last_closed_at[symbol] = now
    account.trades[0:0] = trades
    ticket.update(phase='consumed' if math.isclose(filled,2*qty,rel_tol=1e-10) else 'partial',
                  execution_price=execution_price, filled=filled, exchange_order_id=order_id)
    if ticket['phase'] == 'partial':
        account.position_meta.setdefault('_entry_gate_halts', {})[symbol] = dict(
            reason='DIRECT_REVERSE_PARTIAL_FILL', time=now)
    generation = getattr(account, '_close_generation', None)
    if generation is not None:
        generation[symbol] = generation.get(symbol,0)+1
    account.save_state()
    callback = getattr(account, 'on_trade_closed', None)
    if callback:
        try:
            callback()
        except Exception:
            pass
    account.log(f'直接反向 {symbol} {old["side"]} → {ticket["side"]} '
                f'成交量={filled} 新倉數量={opened} 狀態={ticket["phase"]}', 'SUCCESS')


async def reconcile(account, symbol, ticket):
    """An ambiguous submit is queried by its durable client ID, never resubmitted."""
    if time.time() < float(ticket.get('next_query_at') or 0):
        return False
    ticket['next_query_at'] = time.time()+2.
    try:
        order = await account.exchange.fapiPrivateGetOrder(dict(
            symbol=account._raw_symbol(symbol), origClientOrderId=ticket['client_order_id']))
        status = order.get('status')
        filled = float(order.get('executedQty') or 0)
        if status in ('FILLED','CANCELED','EXPIRED','REJECTED'):
            if filled > 0:
                settle(account, symbol, ticket, float(order.get('avgPrice') or 0),
                       filled, order.get('orderId'))
            else:
                ticket['phase'] = 'rejected'
                account.save_state()
            return True
    except Exception as exc:
        account.log(f'REVERSE_QUERY_WAIT {symbol} {type(exc).__name__}', 'WARNING')
    return False


async def execute(account, engine, symbol, price, decision, ticket, *, paper):
    from core.services.auto_reverse import AUTO_REVERSE_ENABLED
    if not AUTO_REVERSE_ENABLED:
        account.log(f'AUTO_REVERSE_DISABLED symbol={symbol}', 'WARNING')
        return False
    from core.services.entry_firewall import validate_account_entry
    from core.services.entry_gate_integrity import assert_commit_proof
    from core.config import SLIPPAGE_PCT
    old = account.positions.get(symbol)
    context = context_for(symbol, ticket, decision)
    account.closing_lock.add(symbol)
    try:
        if not paper:
            mode = await account.exchange.fapiPrivateGetPositionSideDual()
            if mode.get('dualSidePosition') not in (False, 'false'):
                raise ValueError('DIRECT_REVERSE_REQUIRES_ONE_WAY_MODE')
            rows = await account.exchange.fapiPrivateV2GetPositionRisk(dict(symbol=account._raw_symbol(symbol)))
            row = next((r for r in rows if r.get('symbol') == account._raw_symbol(symbol)
                        and r.get('positionSide') == 'BOTH'), None)
            signed = float((row or {}).get('positionAmt') or 0)
            expected = old['qty']*(1 if old['side'] == 'LONG' else -1)
            if not math.isclose(signed, expected, rel_tol=1e-10, abs_tol=1e-12):
                raise ValueError('DIRECT_REVERSE_EXCHANGE_POSITION_MISMATCH')
            order_qty = float(account.exchange.amount_to_precision(symbol, 2*old['qty']))
            if not math.isclose(order_qty, 2*old['qty'], rel_tol=1e-12):
                raise ValueError('DIRECT_REVERSE_QUANTITY_PRECISION_MISMATCH')
            await account._cancel_all_orders(symbol)
        fresh = await validate_account_entry(account, symbol, ticket['side'], context)
        authority(account, symbol, ticket['side'], context)
        if entry_halted(engine, symbol):
            raise ValueError('DIRECT_REVERSE_ENTRY_HALTED')
        price = float(fresh['price'])
        execution_price = price*(1+SLIPPAGE_PCT if ticket['side']=='LONG' else 1-SLIPPAGE_PCT) if paper else price
        plan = risk_plan(account, engine, old, fresh, execution_price)
        # Validate initialization before any exposure changes.
        from core.services.exits.entry_atr_protection import initialize_atr_protection
        initialize_atr_protection({}, execution_price, ticket['side'], fresh['entry_atr'], initial_stop=plan['stop'])
        ticket.update(decision=copy.deepcopy(fresh), plan=plan, context=context,
                      original=copy.deepcopy(old), phase='submitting')
        assert_commit_proof(account, symbol, ticket['side'], context)
        account.save_state()
        if paper:
            settle(account, symbol, ticket, execution_price, 2*old['qty'], paper=True)
            return True
        order = await account._send_order(symbol, 'market', 'buy' if ticket['side']=='LONG' else 'sell',
                        order_qty, None, dict(positionSide='BOTH', reduceOnly=False,
                        newClientOrderId=ticket['client_order_id'], newOrderRespType='RESULT'),
                        entry_context=context)
        if order.get('status') == 'closed' and float(order.get('filled') or 0)>0:
            settle(account, symbol, ticket, float(order.get('average') or 0), float(order['filled']), order.get('id'))
        else:
            ticket['phase'] = 'unknown'
            account.save_state()
            await reconcile(account, symbol, ticket)
        return True
    except Exception as exc:
        import ccxt
        definite = isinstance(exc, (ValueError, ccxt.InvalidOrder, ccxt.InsufficientFunds, ccxt.AuthenticationError))
        ticket['phase'] = 'unknown' if ticket.get('phase') == 'submitting' and not definite else 'rejected'
        account.save_state()
        account.log(f'DIRECT_REVERSE_WAIT {symbol} {type(exc).__name__}: {exc}', 'WARNING')
        return ticket['phase'] == 'unknown'
    finally:
        account.closing_lock.discard(symbol)


async def pending_close(account, symbol):
    ticket = account.position_meta.get(KEY, {}).get(symbol)
    if not ticket or ticket.get('mode') != 'direct_netting_v1' or ticket.get('phase') not in ('submitting', 'unknown'):
        return False
    if symbol not in account.closing_lock:
        account.closing_lock.add(symbol)
        try:
            await reconcile(account, symbol, ticket)
        finally:
            account.closing_lock.discard(symbol)
    # The caller evaluated the old position; re-evaluate protection on the next tick.
    return True
