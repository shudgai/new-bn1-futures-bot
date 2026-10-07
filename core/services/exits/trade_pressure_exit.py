"""Observed aggressive trade imbalance with position-bound price reversal."""
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
import math
from uuid import uuid4

from core.services.exits.peak_trailing_exit import position_identity

REASON = 'EXIT_AGGRESSIVE_TRADE_PRESSURE'
RULE_VERSION = 1
WINDOW_MS = 10000
MIN_TRADES = 10
MAX_AGE_MS = 5000
MAX_SAMPLES = 20000
IMBALANCE = Decimal('0.70')
REVERSAL_ATR = Decimal('0.15')


@dataclass
class TradeWindow:
    identity: list
    started_ms: float
    received_ms: float
    last_ms: float
    last_id: int
    samples: deque = field(default_factory=deque)
    buy_notional: Decimal = Decimal(0)
    sell_notional: Decimal = Decimal(0)
    extreme: float = 0.

    def trim(self, stamp):
        while self.samples and self.samples[0][0] < stamp-WINDOW_MS:
            _, _, cost, side = self.samples.popleft()
            if side == 'buy':
                self.buy_notional -= cost
            else:
                self.sell_notional -= cost


class TradePressureFeed:
    def __init__(self, log):
        self.log = log
        self.session = str(uuid4())
        self.windows = {}
        self.statuses = {}

    def suspend(self, symbol, reason):
        self.windows.pop(symbol, None)
        if self.statuses.get(symbol) != reason:
            self.log(f'TRADE_PRESSURE_SUSPENDED symbol={symbol} reason={reason}', 'WARNING')
            self.statuses[symbol] = reason

    def suspend_all(self, reason):
        for symbol in list(self.windows):
            self.suspend(symbol, reason)

    def retain_positions(self, positions):
        for symbol in list(self.windows):
            if symbol not in positions:
                self.windows.pop(symbol, None)
                self.statuses.pop(symbol, None)

    def observe(self, symbol, position, trade, now_ms):
        if str(position.get('entry_mode', '')).upper() != 'CHANNEL_SWING':
            self.windows.pop(symbol, None)
            return
        try:
            identity = position_identity(position)
            raw = trade['info']
            stamp, price, amount = map(float, (trade['timestamp'], trade['price'], trade['amount']))
            trade_id = int(raw['a'])
            maker = raw['m']
            side = 'sell' if maker else 'buy'
            if (raw.get('e') != 'aggTrade' or not isinstance(maker, bool)
                    or str(trade['id']) != str(trade_id) or trade_id < 0
                    or trade['side'] != side or trade['symbol'].replace(':USDT', '') != symbol
                    or float(raw['T']) != stamp or float(raw['p']) != price
                    or float(raw['q']) != amount
                    or not all(math.isfinite(v) and v > 0 for v in (stamp, price, amount, now_ms))
                    or not 0 <= now_ms-stamp <= MAX_AGE_MS):
                raise ValueError('INVALID_AGG_TRADE')
            if stamp < identity[1]*1000:
                self.suspend(symbol, 'PRE_ENTRY_TRADE')
                return
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            self.suspend(symbol, f'INVALID_TRADE:{type(exc).__name__}')
            return
        window = self.windows.get(symbol)
        if window and window.identity != identity:
            self.suspend(symbol, 'POSITION_CHANGED')
            window = None
        if window:
            if trade_id == window.last_id and stamp == window.last_ms:
                return
            if trade_id <= window.last_id or stamp < window.last_ms:
                self.suspend(symbol, 'OUT_OF_ORDER')
                return
            if (trade_id != window.last_id+1 or now_ms-window.received_ms > MAX_AGE_MS
                    or stamp-window.last_ms > MAX_AGE_MS):
                self.suspend(symbol, 'TRADE_GAP')
                window = None
        if window is None:
            window = TradeWindow(identity, now_ms, now_ms, stamp, trade_id)
            self.windows[symbol] = window
        window.last_ms, window.last_id, window.received_ms = stamp, trade_id, now_ms
        cost = Decimal(str(price))*Decimal(str(amount))
        window.samples.append((stamp, price, cost, side))
        if side == 'buy':
            window.buy_notional += cost
        else:
            window.sell_notional += cost
        extreme = max if position['side'] == 'LONG' else min
        window.extreme = extreme(window.extreme, price) if window.extreme else price
        window.trim(stamp)
        if len(window.samples) > MAX_SAMPLES:
            self.suspend(symbol, 'WINDOW_CAPACITY')
            return
        if self.statuses.get(symbol) != 'OBSERVING':
            self.log(f'TRADE_PRESSURE_OBSERVING symbol={symbol} warmup_ms={WINDOW_MS}', 'INFO')
            self.statuses[symbol] = 'OBSERVING'

    def evidence(self, symbol, position, state, snapshot, now_ms):
        window = self.windows.get(symbol)
        if not window or window.identity != position_identity(position):
            return None
        stamp = float(snapshot['quote_ms'])
        if (not 0 <= now_ms-window.last_ms <= MAX_AGE_MS
                or not 0 <= now_ms-window.received_ms <= MAX_AGE_MS):
            self.suspend(symbol, 'STALE_TRADES')
            return None
        if stamp < window.last_ms:
            return None
        observed = state.get('trade_pressure_observation') or {}
        if observed.get('identity') != window.identity or observed.get('rule_version') != RULE_VERSION:
            observed = dict(identity=window.identity, rule_version=RULE_VERSION)
        scale = snapshot.get('atr')
        if not observed.get('atr'):
            if (snapshot.get('reason') or snapshot.get('fallback_used')
                    or snapshot.get('closed_bar_ms') != math.floor(stamp/60000)*60000-60000
                    or snapshot.get('live_bar_ms') != math.floor(stamp/60000)*60000
                    or not isinstance(scale, (int, float)) or not math.isfinite(scale) or scale <= 0):
                return None
            observed['atr'] = float(scale)
        extreme = max if position['side'] == 'LONG' else min
        observed['extreme'] = extreme(window.extreme, observed.get('extreme', window.extreme))
        observed.update(session=self.session, last_trade_ms=window.last_ms)
        state['trade_pressure_observation'] = observed
        window.trim(stamp)
        return dict(rule_version=RULE_VERSION, identity=window.identity, symbol=symbol,
                    quote_ms=stamp, last_trade_ms=window.last_ms,
                    coverage_ms=stamp-window.started_ms, count=len(window.samples),
                    buy_notional=str(window.buy_notional), sell_notional=str(window.sell_notional),
                    atr=observed['atr'], extreme=observed.get('extreme'))


def confirmed_trade_pressure(position, state, snapshot, price):
    evidence = snapshot.get('trade_pressure')
    state['trade_pressure_status'] = 'WAIT_VALID_TRADE_WINDOW'
    if not evidence:
        return None
    try:
        if (evidence['rule_version'] != RULE_VERSION
                or evidence['identity'] != position_identity(position)
                or evidence['quote_ms'] != snapshot['quote_ms']
                or not 0 <= evidence['quote_ms']-evidence['last_trade_ms'] <= MAX_AGE_MS
                or evidence['coverage_ms'] < WINDOW_MS or evidence['count'] < MIN_TRADES):
            return None
        buy, sell, scale, extreme, quoted = map(Decimal, (
            evidence['buy_notional'], evidence['sell_notional'],
            str(evidence['atr']), str(evidence['extreme']), str(price)))
        if (not all(v.is_finite() for v in (buy, sell, scale, extreme, quoted))
                or buy < 0 or sell < 0 or buy+sell <= 0
                or min(scale, extreme, quoted) <= 0):
            return None
        adverse = sell if position['side'] == 'LONG' else buy
        reversal = extreme-quoted if position['side'] == 'LONG' else quoted-extreme
        if adverse < IMBALANCE*(buy+sell):
            state['trade_pressure_status'] = 'WAIT_ADVERSE_IMBALANCE'
            return None
        if reversal < REVERSAL_ATR*scale:
            state['trade_pressure_status'] = 'WAIT_PRICE_REVERSAL'
            return None
        state['trade_pressure_status'] = 'CONFIRMED'
        return dict(evidence, adverse_ratio=str(adverse/(buy+sell)),
                    reversal=str(reversal), reversal_atr=str(REVERSAL_ATR))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        state['trade_pressure_status'] = 'INVALID_PRESSURE_EVIDENCE'
        return None
