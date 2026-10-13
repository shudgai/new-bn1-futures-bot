"""Fail-closed shared entry verification immediately before opening exposure."""
import math
import time

from core.gates.meme_coin_gate import MEME_ENTRY_CODES
from core.services.entry_contract import (
    ENTRY_CODES, ENTRY_EVIDENCE_KEYS, KC_PENDING_CODES,
    evaluate_entry_contract,
)


class EntryFirewall:
    @classmethod
    def verify_can_open(cls, frame, side, code, symbol=None):
        """Only a supported, freshly validated contract authorizes automatic entry."""
        if code not in ENTRY_CODES:
            raise ValueError('[FORBIDDEN_ENTRY] 已停用的自動入口訊號')
        decision = evaluate_entry_contract(frame, code=code, symbol=symbol)
        if decision is None or decision['side'] != side:
            raise ValueError('[FORBIDDEN_ENTRY] 最新行情不符合入口訊號')
        return decision


def validate_entry_frame(frame, side, code, symbol=None):
    return EntryFirewall.verify_can_open(frame, side, code, symbol=symbol)

async def validate_account_entry(account, symbol, side, context):
    from core.config import is_entry_disabled
    if is_entry_disabled(symbol):
        raise ValueError("[FORBIDDEN_ENTRY] ENTRY_DISABLED_SYMBOL: " + symbol)
    context = context if isinstance(context, dict) else {}
    
    code = context.get('entry_signal_code')
    if code not in ENTRY_CODES:
        from core.gates.pipeline import pipeline
        regime_problem = pipeline.market_regime_problem(symbol)
        if regime_problem:
            raise ValueError("[FORBIDDEN_ENTRY] " + regime_problem)
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
    quote = float(frame.iloc[-1]['close'])
    from core.services.entry_contract import (
        evaluate_death_cross_fast_lane,
        evaluate_golden_cross_fast_lane,
    )
    verified_fast_cross = (
        evaluate_golden_cross_fast_lane(frame, quote, symbol)
        if code == 'KC_GOLDEN_CROSS_FAST_LONG' and side == 'LONG'
        else evaluate_death_cross_fast_lane(frame, quote, symbol)
        if code == 'KC_DEATH_CROSS_FAST_SHORT' and side == 'SHORT'
        else None
    )
    from core.gates.pipeline import (
        DEATH_CROSS_FAST_SHORT_CODE, GOLDEN_CROSS_FAST_LONG_CODE,
        MOMENTUM_BURST_CODES, entry_strategy_type,
        fast_cross_evidence, fast_cross_ma15_slope_problem,
        is_meme_breakout_symbol, ma5_ma15_cross_chop_problem,
        strict_kc_entry_problem,
    )
    if code in (GOLDEN_CROSS_FAST_LONG_CODE, DEATH_CROSS_FAST_SHORT_CODE):
        slope_problem = fast_cross_ma15_slope_problem(
            frame, quote, side, symbol=symbol,
        )
        if slope_problem:
            raise ValueError('[FORBIDDEN_ENTRY] ' + slope_problem)
        if fast_cross_evidence(frame, quote, side, symbol=symbol) is None:
            raise ValueError(
                '[FORBIDDEN_ENTRY] BLOCKED_FAST_CROSS_MA15_SLOPE_OR_EVIDENCE'
            )
    meme_golden_cross = (
        code == GOLDEN_CROSS_FAST_LONG_CODE and side == 'LONG'
        and is_meme_breakout_symbol(symbol)
    )
    cross_chop_problem = (
        None if meme_golden_cross or code in KC_PENDING_CODES
        or code in MEME_ENTRY_CODES
        or code in (
            'BEARISH_INSTANT_BREAKOUT', 'BULLISH_INSTANT_BREAKOUT',
            'KC_LIVE_BODY_BREAKOUT_LONG', 'KC_LIVE_BODY_BREAKOUT_SHORT',
            'KC_LIVE_PATTERN_BREAKOUT_LONG', 'KC_LIVE_PATTERN_BREAKOUT_SHORT',
            'KC_REALTIME_RAIL_BREACH_SHORT',
        ) else
        ma5_ma15_cross_chop_problem(
            frame, quote, symbol=symbol, log_result=True,
            side=side,
        )
    )
    if cross_chop_problem:
        raise ValueError('[FORBIDDEN_ENTRY] ' + cross_chop_problem)
    kc_problem = strict_kc_entry_problem(
        frame, quote, side,
        strategy_type=(
            'BREAKOUT' if code in KC_PENDING_CODES or code in MEME_ENTRY_CODES or code in (
                'KC_LIVE_PATTERN_BREAKOUT_LONG', 'KC_LIVE_PATTERN_BREAKOUT_SHORT',
            'KC_REALTIME_RAIL_BREACH_SHORT',
            )
            else entry_strategy_type(context.get('entry_phase'))
        ),
    )
    if kc_problem:
        raise ValueError('[FORBIDDEN_ENTRY] ' + kc_problem)
    from core.services.entry_contract import (
        opposite_side_close_cooldown_problem, same_bar_close_entry_problem,
        same_side_anti_churn_problem,
    )
    churn_problem = same_side_anti_churn_problem(
        account, symbol, frame, side, quote,
    )
    if churn_problem:
        raise ValueError('[FORBIDDEN_ENTRY] ' + churn_problem)
    same_bar_problem = same_bar_close_entry_problem(
        account, symbol, frame, side,
    )
    if same_bar_problem:
        raise ValueError('[FORBIDDEN_ENTRY] ' + same_bar_problem)
    opposite_cooldown_problem = (
        None if code in ('BEARISH_INSTANT_BREAKOUT', 'BULLISH_INSTANT_BREAKOUT')
        else opposite_side_close_cooldown_problem(
            account, symbol, frame, side,
        )
    )
    if opposite_cooldown_problem:
        raise ValueError('[FORBIDDEN_ENTRY] ' + opposite_cooldown_problem)
    from core.gates.pipeline import long_large_bearish_candle_problem
    if side == 'LONG':
        bearish_candle_problem = long_large_bearish_candle_problem(frame, quote)
        if bearish_candle_problem:
            raise ValueError('[FORBIDDEN_ENTRY] ' + bearish_candle_problem)
    if (side == 'SHORT'
            and code in (
                'AUTHORIZED_BY_PEAK_FLIP_SHORT',
                'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
                'AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT',
                'TOP_WATERFALL_FLIP',
                'AUTHORIZED_TOP_REVERSAL_SHORT',
                'CLIMAX_REVERSAL_FLIP',
            )
            and str(getattr(account, 'last_closed_side', {}).get(symbol, '')).upper() == 'LONG'):
        from core.services.strategies.outer_strategy import kc_bearish_midline_turn
        if not kc_bearish_midline_turn(frame, quote):
            raise ValueError(
                '[FORBIDDEN_ENTRY] BLOCKED_SHORT_FLIP_WITHOUT_BEARISH_KC_TURN'
            )
    from core.gates.pipeline import FLAT_LINE_BREAKOUT_TYPES, PIPELINE_ENTRY_TYPES, pipeline
    is_outer_rail_breakout = (
        code in FLAT_LINE_BREAKOUT_TYPES
        or pipeline.detect_realtime_breakout(frame, quote, side=side) is not None
    )
    midline_detector = (
        pipeline.detect_momentum_burst
        if code in MOMENTUM_BURST_CODES
        else pipeline.detect_midline_break_long if side == 'LONG'
        else pipeline.detect_midline_break_short
    )
    verified_midline_entry = midline_detector(
        frame, quote, side=side,
    )
    verified_midline_entry = bool(
        verified_midline_entry
        and verified_midline_entry.get('type') == code
        and verified_midline_entry.get('side') == side
    )
    is_flip_or_shadow = (
        code in ('AUTHORIZED_BY_TOP_WATERFALL_FLIP_SHORT', 'TOP_WATERFALL_FLIP',
                 'CLIMAX_REVERSAL_FLIP', 'AUTHORIZED_SHADOW_RETEST_ENTRY',
                 'SHADOW_RETEST_ENTRY',
                 'AUTHORIZED_BY_VALLEY_REVERSAL_FLIP_LONG',
                 'AUTHORIZED_BY_PEAK_FLIP_SHORT',
                 'AUTHORIZED_BY_PEAK_REVERSAL_FLIP_SHORT',
                 'AUTHORIZED_TOP_REVERSAL_SHORT')
        or code in (
            'BEARISH_INSTANT_BREAKOUT', 'BULLISH_INSTANT_BREAKOUT',
            'KC_LIVE_BODY_BREAKOUT_LONG', 'KC_LIVE_BODY_BREAKOUT_SHORT',
            'KC_REALTIME_RAIL_BREACH_SHORT',
        )
    )
    is_verified_live_breakout = (
        pipeline.has_strong_bullish_breakout(frame, quote) if side == 'LONG'
        else pipeline.has_strong_bearish_breakout(frame, quote)
    )
    is_exempt_from_regime = bool(
        code in KC_PENDING_CODES or is_flip_or_shadow
        or is_verified_live_breakout or verified_midline_entry
        or verified_fast_cross is not None
        or code in MEME_ENTRY_CODES
    )
    regime_problem = pipeline.market_regime_problem(symbol, frame, quote)
    if regime_problem and not is_exempt_from_regime:
        raise ValueError("[FORBIDDEN_ENTRY] " + regime_problem)
    if (code not in KC_PENDING_CODES and not is_flip_or_shadow and not verified_midline_entry
            and verified_fast_cross is None and code not in MEME_ENTRY_CODES):
        try:
            chop_problem = pipeline.chop_lockout_problem(
                frame, quote, allow_flat_lines=is_outer_rail_breakout,
                signal_code=code,
                allow_ma_tangle=verified_fast_cross is not None,
            )
        except TypeError:
            chop_problem = pipeline.chop_lockout_problem(frame, quote)
        if chop_problem:
            raise ValueError('[FORBIDDEN_ENTRY] ' + chop_problem)
    grace = context.get('pipeline_ttl_grace')
    # The two remaining entry authorities are always recomputed from the fresh
    # frame. A cached pipeline TTL decision must never bypass their contract.
    if grace is not None and code not in ENTRY_CODES:
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
            if (
                elapsed > 3
                and code in PIPELINE_ENTRY_TYPES
                and isinstance(authorized_decision, dict)
                and authorized_decision.get('_is_authorized')
                and authorized_decision.get('type') == code
                and authorized_decision.get('side') == side
                and authorized_decision.get('pending_signal_id') == grace.get('pending_signal_id')
                and grace.get('pending_signal_id') == context.get('signal_id')
                and current_bar == stamp
                and all(math.isfinite(value) for value in (
                    authorized_price, atr, quote, current_bar, stamp,
                    quote_age, adverse_move,
                ))
                and authorized_price > 0
                and atr > 0
                and quote > 0
                and 0 <= quote_age <= 30
                and adverse_move / atr <= 0.8
            ):
                from core.gates.pipeline import pipeline
                refreshed_at = time.monotonic()
                refreshed = pipeline.authorize(
                    authorized_decision, frame, quote, symbol=symbol,
                    requested_side=side,
                )
                if (
                    refreshed is not None
                    and refreshed.get('_is_authorized')
                    and refreshed.get('type') == code
                    and refreshed.get('side') == side
                    and refreshed.get('pending_signal_id') == grace.get('pending_signal_id')
                    and refreshed.get('confirmation_bar_id') == stamp
                ):
                    authorized_decision = refreshed
                    authorized_price = quote
                    atr = float(refreshed['entry_atr'])
                    stamp = float(refreshed['confirmation_bar_id'])
                    elapsed = time.monotonic() - refreshed_at
                    adverse_move = 0.0
                    import logging
                    logging.getLogger('uvicorn.error').info(
                        '[ENTRY_FIREWALL] Refreshed same-bar pipeline authorization '
                        f'for {symbol} {side}: {code}'
                    )
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
    from core.gates.pipeline import entry_market_structure_problem
    market_structure_problem = entry_market_structure_problem(
        frame, float(frame.iloc[-1]['close']), side,
        entry_phase=decision.get('entry_phase'),
    )
    if market_structure_problem:
        raise ValueError('[FORBIDDEN_ENTRY] ' + market_structure_problem)
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
