"""Post-entry MA3 turning exit gated by confirmed CK momentum fading.
Implements IExitStrategy interface.
"""
import math
from statistics import median
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy
from core.config import CHANNEL_FADING_MA3_EXIT_ENABLED
from core.services.swing_service import significant_ma3_turn
from core.services.strategies.outer_strategy import ck_momentum_fading, aligned_entry

STATE_KEY = 'channel_fading_ma3_turn'
EXIT_REASON = 'CK_FADING_MA3_TURN_EXIT'
IMMEDIATE_EXIT_REASON = 'MA3_IMMEDIATE_TURN_EXIT'

NARROW_LOOKBACK = 20
NARROW_RATIO = 0.75


def immediate_ma3_turn(position, frame, price):
    """Retired tick-to-tick exit; historical pending state cannot close a trade."""
    position.pop('channel_immediate_ma3_turn', None)
    return False


def ck_channel_narrow(frame):
    """Latest closed relative width <= 75% of preceding 20-bar median."""
    try:
        if frame is None or len(frame) < NARROW_LOOKBACK + 2:
            return False
        rows = frame.iloc[-(NARROW_LOOKBACK + 2):-1]
        widths = []
        for _, row in rows.iterrows():
            upper, lower = float(row['kc_upper']), float(row['kc_lower'])
            middle = float(row['kc_middle'] if 'kc_middle' in rows.columns else row['ema_20'])
            if not all(math.isfinite(v) for v in (upper, lower, middle)) or not 0 < lower < middle < upper:
                return False
            widths.append((upper - lower) / middle)
        return widths[-1] <= median(widths[:-1]) * NARROW_RATIO
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def ma3_outside_rail_turning_back(frame, price, side):
    """多單：已收線 MA3 仍在持倉側外軌（上軌）之外，但即時 MA3 已轉向往回；空單鏡像。

    2026-09-13 使用者：只有「漲勢末端、MA3 還在上軌外卻已轉向往回」才是轉彎；
    正向轉彎（MA3 還在往上、或在軌內往上）不可以平倉。
    """
    if side not in ("LONG", "SHORT") or frame is None or len(frame) < 3:
        return False
    rail_key = "kc_upper" if side == "LONG" else "kc_lower"
    if "ma3" not in frame.columns or rail_key not in frame.columns:
        return False
    try:
        closes = [float(v) for v in frame["close"].iloc[-4:-1]]
        closed_ma3 = float(frame.iloc[-2]["ma3"])
        rail = float(frame.iloc[-2][rail_key])
        price = float(price)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return False
    if (len(closes) != 3 or not all(math.isfinite(v) and v > 0 for v in (closed_ma3, rail, price, *closes))):
        return False
    live_ma3 = (closes[-2] + closes[-1] + price) / 3.0
    if side == "LONG":
        return closed_ma3 > rail and live_ma3 < closed_ma3
    return closed_ma3 < rail and live_ma3 > closed_ma3


def fading_ma3_turn(position, frame, price):
    identity = [position.get('side'), position.get('open_timestamp'), position.get('entry_price')]
    state = position.get(STATE_KEY)
    if state and state.get('identity') != identity:
        position.pop(STATE_KEY, None)
        state = None
    if not CHANNEL_FADING_MA3_EXIT_ENABLED:
        return False
    # 2026-09-13 使用者：只有「漲勢末端 MA3 還在外軌外卻已轉向往回」才是轉彎平倉；
    # 正向轉彎（MA3 還在往上、或已回到軌內往上）不可以平。
    if (state and state.get('version') == 3 and state.get('pending')
            and ma3_outside_rail_turning_back(frame, price, position.get('side'))):
        return True
    if ck_momentum_fading(frame, position.get('side')) is None:
        position.pop(STATE_KEY, None)
        return False
    observed = {k: position.get(k) for k in ('side', 'open_timestamp', 'entry_price')}
    if state:
        observed['channel_significant_ma3_turn'] = state
    turned = significant_ma3_turn(observed, frame, price)
    state = observed.get('channel_significant_ma3_turn')
    if state is None:
        position.pop(STATE_KEY, None)
        return False
    # 幅度門檻沿用 significant_ma3_turn 的 0.10 ATR 峰谷反向；
    # 2026-09-13 使用者：還必須是「真量能衰退」＋MA3 在持倉側外軌外轉向往回才平，
    # 通道寬、量沒衰退的場合不可以平（先前只看 MA3 轉彎是錯的）。
    position[STATE_KEY] = state
    if not turned:
        return False
    if not ma3_outside_rail_turning_back(frame, price, position.get('side')):
        return False
    from core.strategy import has_real_volume_decay
    side = position.get('side')
    return bool(has_real_volume_decay(frame, -1 if side == 'LONG' else 1))


def next_breakout_ready(account, symbol, frame, price):
    """A matched successful close must precede the live MA3 continuation candle."""
    ticket = getattr(account, 'channel_profit_reentries', {}).get(symbol, {})
    if symbol in account.positions or ticket.get('mode') != 'next_breakout':
        return False
    try:
        if ticket.get('close_reason') != 'Channel Swing ' + EXIT_REASON:
            return False
        requested = float(ticket['close_requested_at_ms'])
        fills = [float(t['id']) for t in account.trades
                 if t.get('symbol') == symbol and t.get('action') == 'CLOSE_' + ticket['side']
                 and t.get('reason') == ticket['close_reason'] and float(t['id']) >= requested]
        if not fills or not math.isfinite(requested):
            return False
        closed = max(fills)
        first = float(frame.iloc[-1]['timestamp'])
        return (math.isfinite(closed) and math.isfinite(first)
                and first > math.floor(closed / 60000) * 60000
                and aligned_entry(frame, price).get('action') == 'ENTER')
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        return False


class FadingExitStrategy(IExitStrategy):
    """OOP Strategy class implementing IExitStrategy for fading MA3 turn exit evaluation."""

    def evaluate_exit(
        self,
        position: Dict[str, Any],
        frame: pd.DataFrame,
        price: float,
        **kwargs: Any
    ) -> Optional[str]:
        if fading_ma3_turn(position, frame, price):
            return EXIT_REASON
        return None
