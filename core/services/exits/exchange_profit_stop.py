"""Create replacement reduce-only stops before cancelling prior protection."""
import asyncio
import hashlib
import time


async def sync_exchange_profit_stop(account, symbol, price):
    locks = getattr(account, '_moving_profit_locks', None)
    if locks is None:
        locks = account._moving_profit_locks = {}
    async with locks.setdefault(symbol, asyncio.Lock()):
        pos = account.positions.get(symbol)
        if not pos or symbol in account.closing_lock:
            return
        if str(pos.get('entry_mode', '')).upper() == 'CHANNEL_SWING':
            order = pos.get('moving_profit_order') or account.position_meta.get(symbol, {}).get('moving_profit_order') or {}
            if not order or time.time() < order.get('retry_after', 0):
                return
            try:
                ids = set(order.get('retired_ids') or [])
                if order.get('algo_id'):
                    ids.add(order['algo_id'])
                if order.get('pending'):
                    try:
                        response = await account.exchange.request('algoOrder', 'fapiPrivate', 'GET',
                            {'clientAlgoId': order['pending']['clientAlgoId']})
                        if response.get('algoId'):
                            ids.add(response['algoId'])
                    except Exception as exc:
                        if '-2013' not in str(exc):
                            raise
                for algo_id in ids:
                    try:
                        await account.exchange.request('algoOrder', 'fapiPrivate', 'DELETE', {'algoId': algo_id})
                    except Exception as exc:
                        if '-2011' not in str(exc) and '-2013' not in str(exc):
                            raise
                pos.pop('moving_profit_order', None)
                account.position_meta.setdefault(symbol, {}).pop('moving_profit_order', None)
            except Exception as exc:
                order['retry_after'] = time.time() + 5
                pos['moving_profit_order'] = dict(order)
                account.position_meta.setdefault(symbol, {})['moving_profit_order'] = dict(order)
                account.log(f'{symbol} 舊鎖利掛單撤銷未確認，保留識別碼重試：{exc}', 'WARNING')
            account.save_state()
            return
        pos['exchange_managed'] = True
        state = pos.get('peak_trailing_state') or {}
        line = state.get('profit_stop_price')
        if not line or state.get('profit_stop_triggered'):
            return
        order = pos.setdefault('moving_profit_order', dict(account.position_meta.get(symbol, {}).get('moving_profit_order') or {}))
        if time.time() < order.get('retry_after', 0):
            return
        sign = 1 if pos['side']=='LONG' else -1
        target = float(account.exchange.price_to_precision(symbol,line))
        qty = account.exchange.amount_to_precision(symbol,pos['qty'])
        if sign*(price-target)<=0:
            return
        try:
            # Persist uncertain requests; query by deterministic client ID before retry.
            pending = order.get('pending')
            if pending:
                try:
                    response = await account.exchange.request('algoOrder','fapiPrivate','GET',{'clientAlgoId':pending['clientAlgoId']})
                except Exception as exc:
                    if '-2013' not in str(exc):
                        raise
                    response = await account.exchange.request('algoOrder','fapiPrivate','POST',pending)
            elif order.get('algo_id') and target==order.get('price') and qty==order.get('qty'):
                response = None
            else:
                if order.get('price') and sign*(target-order['price'])<0:
                    return
                key = f"{symbol}:{pos['open_timestamp']}:{target}:{qty}"
                pending = dict(algoType='CONDITIONAL',symbol=account._raw_symbol(symbol),
                               side='SELL' if sign==1 else 'BUY',type='STOP_MARKET',quantity=qty,
                               triggerPrice=str(target),reduceOnly='true',workingType='CONTRACT_PRICE',
                               clientAlgoId='profit_'+hashlib.sha256(key.encode()).hexdigest()[:24])
                order['pending']=pending
                account.save_state()
                response = await account.exchange.request('algoOrder','fapiPrivate','POST',pending)
            if response is not None:
                if not response.get('algoId') or response.get('algoStatus')!='NEW':
                    raise ValueError('Profit stop is not confirmed open')
                old = order.get('algo_id')
                order.update(algo_id=response['algoId'],price=float(response.get('triggerPrice') or pending['triggerPrice']),
                             qty=pending['quantity'])
                order.pop('pending',None)
                if old and old!=order['algo_id']:
                    order.setdefault('retired_ids',[]).append(old)
                account.position_meta.setdefault(symbol,{})['moving_profit_order']=dict(order)
                account.save_state()
            for old in list(order.get('retired_ids',[])):
                await account.exchange.request('algoOrder','fapiPrivate','DELETE',{'algoId':old})
                order['retired_ids'].remove(old)
            order.pop('retry_after',None)
            account.position_meta.setdefault(symbol,{})['moving_profit_order']=dict(order)
            account.save_state()
        except Exception as exc:
            order['retry_after']=time.time()+5
            account.position_meta.setdefault(symbol,{})['moving_profit_order']=dict(order)
            account.save_state()
            account.log(f'{symbol} 移動鎖利掛單更新未確認，保留舊單及本地保護：{exc}', 'WARNING')
