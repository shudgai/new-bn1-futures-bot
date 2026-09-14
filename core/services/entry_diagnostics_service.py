from core.services.exits.fading_exit_service import next_breakout_ready
"""Read-only entry diagnostics; viewing a chart never observes or consumes a turn."""
import math
from core.guards.abnormal_guard import opposite_entry_releases
from core.services.strategies.outer_strategy import (
    entry_trend_direction, aligned_entry, live_adverse_entry_safe,
    breakout_confirmation_pending, live_body_breakout_side, LIVE_BREAKOUT_BODY_ATR,
)


def entry_diagnostics(engine, symbol, frame, price, now):
    def result(reason, message, detail, **extra):
        return dict(reason=reason, message=message, detail=detail, **extra)

    def near_terminal_extreme(direction: str) -> bool:
        """Only label terminal when price is near the recent structural extreme."""
        try:
            if direction not in ("LONG", "SHORT") or len(frame) < 8:
                return False
            atr = float(frame.iloc[-2]["atr"])
            recent = frame.iloc[-8:-1]
            price_value = float(price)
            if not math.isfinite(atr) or atr <= 0 or not math.isfinite(price_value):
                return False
            if direction == "LONG":
                extreme = float(recent["high"].max())
                return price_value >= extreme - atr * 0.5
            extreme = float(recent["low"].min())
            return price_value <= extreme + atr * 0.5
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            return False
    if symbol in engine.account.positions:
        return result('KC_POSITION_HELD', '已有持倉，由出口邏輯管理', '不另外加倉。')
    if not engine.is_running:
        return result('KC_BOT_STOPPED', '機器人尚未運行', '等待啟動後重新驗證行情。')
    try:
        price = float(price)
        if frame is None or len(frame) < 4 or not math.isfinite(price) or price <= 0:
            raise ValueError('missing frame')
        bar = float(frame.iloc[-1]['timestamp'])
        if not math.isfinite(bar) or int(now // 60) != int(bar // 60000):
            return result('KC_ENTRY_FRAME_WAIT', '等待本根行情資料', '送單前須以本根有效行情重驗趨勢或即時實體突破入口。')
        outer = aligned_entry(frame, price)
        side = outer.get('side') or entry_trend_direction(frame)
        quoted = float(getattr(engine, '_channel_entry_quote_times', {}).get(symbol, float('nan')))
        fresh = math.isfinite(quoted) and 0 <= now - quoted <= 5
        live_body_atr = None
        live_atr = None
        special_side = live_body_breakout_side(frame, price)
        try:
            live_body = abs(float(price) - float(frame.iloc[-1]["open"]))
            live_atr = float(frame.iloc[-2]["atr"])
            if live_atr > 0:
                live_body_atr = live_body / live_atr
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            pass
        special_room = engine._channel_profit_room(frame, price, special_side) if special_side else None
        extra = dict(side=side, price=price, quote_fresh=fresh, pivot_ready=False,
                 outer_signal=outer.get('reason'), profit_room=special_room,
                     special_k_side=special_side, live_body_atr=live_body_atr,
                     special_k_threshold=LIVE_BREAKOUT_BODY_ATR)
        if engine._channel_candle_entry_blocked(symbol, now):
            return result('KC_ONE_ENTRY_PER_CANDLE', '本根K已有成交，等待下一根', '平倉後不反手；每根限次保留。', **extra)
        if not fresh:
            return result('KC_ENTRY_QUOTE_WAIT', '等待有效即時報價', '報價缺失或超過5秒，不送單。', **extra)
        if not side:
            key = 'kc_middle' if 'kc_middle' in frame else 'ema_20'
            a, b = frame.iloc[-3], frame.iloc[-2]
            detail = (f"已收線CK中軌 {float(a[key]):.10g} → {float(b[key]):.10g}。"
                      '中軌上升評估多單、下降評估空單；持平或無效不開，不要求上下軌位置。')
            return result('KC_DIRECTION_WAIT', 'CK趨勢尚未明確', detail, **extra)
        trend_1h = getattr(engine, 'st_direction_1h_cache', {}).get(symbol)
        if trend_1h in (1, -1) and (
            (side == 'LONG' and trend_1h == -1)
            or (side == 'SHORT' and trend_1h == 1)
        ):
            return result(
                'KC_1H_DIRECTION_WAIT', '1H方向不同，暫不開倉',
                f"1H SuperTrend 為 {'多頭' if trend_1h == 1 else '空頭'}，目前 {side} 訊號需等待同向。",
                **extra,
            )
        if not live_adverse_entry_safe(frame, price, side):
            return result('KC_LIVE_ADVERSE_ENTRY_WAIT', '當根反向異常，暫不開倉', '沿用原開盤價及已收線ATR門檻。', **extra)
        ticket = getattr(engine.account, 'channel_profit_reentries', {}).get(symbol)
        if ticket and ticket.get('mode') in ('direct_reverse', 'ck_reverse', 'ma3_turn_wait'):
            ticket = None
        if ticket and ticket.get('mode') == 'next_breakout':
            if not next_breakout_ready(engine.account, symbol, frame, price):
                return result('KC_NEXT_BREAKOUT_WAIT', '等待下一根有效趨勢入口', '平倉當根不開 (除非發生強勢破軌)；下一根起依已收線CK中軌方向與利潤空間重新評估。', **extra)
            ticket = None
        if ticket and opposite_entry_releases(engine.account, symbol, frame, price):
            # Preview order validation without mutating persisted state.
            ticket = None
            extra['abnormal_ticket_release_ready'] = True
        if ticket:
            if ticket.get('phase') != 'closed':
                return result('KC_CLOSE_CONFIRMATION_WAIT', '等待舊倉平倉確認', '平倉成功前不重開或反手。', **extra)
            if ticket.get('requires_pullback', True) and ticket.get('mode') != 'ck_reverse':
                return result('KC_POST_CLOSE_PULLBACK_WAIT', '異常平倉後須重驗回踩', '後續K須先回到CK內，再順向站回原側外軌。', **extra)
            if ticket.get('side') != side:
                return result('KC_REENTRY_DIRECTION_WAIT', '預定重開方向與CK方向不同', '等待新的同向重開條件成立。', **extra)
        candidate = engine._channel_candidate_bar_id(frame)
        if not ticket and (symbol, side, candidate) in getattr(engine, '_channel_invalid_entry_candidates', set()):
            return result('KC_CANDIDATE_INVALIDATED', '此候選訊號已失效', '等待下一個有效候選，再重新評估進場。', **extra)
        outer_candidate = (
            (math.isfinite(float(frame.iloc[-1].get('kc_upper', float('nan'))))
             and price > float(frame.iloc[-1]['kc_upper']))
            or
            (math.isfinite(float(frame.iloc[-1].get('kc_lower', float('nan'))))
             and price < float(frame.iloc[-1]['kc_lower']))
        )
        if outer_candidate and breakout_confirmation_pending(frame, side):
            return result(
                'KC_SECOND_BODY_WAIT', '外軌已破，等待第二根有效同色實體K',
                '第一根有效實體已突破外軌；中間同色弱體可順延，直到第一根同色有效實體收線確認。',
                **extra,
            )
        if outer.get('action') != 'ENTER' and not outer_candidate:
            if outer.get('reason') in {
                'KC_MA3_TURN_WAIT', 'KC_MIDDLE_OPPOSITE_WAIT',
                'KC_LOW_VOLATILITY_WAIT', 'KC_ENTRY_BODY_OVERHEAT_WAIT',
                'KC_ENTRY_PREV_BODY_WAIT', 'KC_MOMENTUM_FADING_WAIT',
            }:
                return result(
                    outer['reason'], '延續條件已重置，等待新的外軌破軌',
                    '價格已回到通道內或 MA3 已轉弱；下一次需重新完成兩根有效同色實體破軌確認。',
                    **extra,
                )
            return result(
                'KC_ENTRY_SIGNAL_WAIT', '目前沒有有效外軌入口',
                '目前價格未站在持倉側外軌外；中軌趨勢不開倉，等待新的外軌破軌或當前K達 ATR 特例K。',
                **extra,
            )
        terminal_side = side if outer_candidate else None
        continuation_signal = str(outer.get('reason') or '').startswith(
            'KC_OUTSIDE_CONTINUATION_'
        )
        if special_room and not special_room['allowed']:
            return result(
                special_room['reason'], '特例K利潤空間不足，暫不開倉', special_room['detail'], **extra,
            )
        if continuation_signal and outer.get('action') == 'ENTER':
            return result(
                'KC_ENTRY_READY', '外軌延續可開倉，等待送單風控',
                '已完成破軌且同方向K仍在外軌外；延續入口不再因前方目標距離逐根失效。',
                **extra,
            )
        if (engine._channel_terminal_market(frame) and outer_candidate
                and terminal_side and near_terminal_extreme(terminal_side)
                and not str(outer.get('reason') or '').startswith(
                    ('KC_LIVE_BODY_BREAKOUT_', 'KC_LONG_BODY_'))):
            return result('KC_TREND_END_WAIT', '末端弱量期間多空暫停開倉', '等趨勢重新明朗後重驗。', **extra)
        if outer.get('reason') == 'KC_SECOND_BODY_WAIT':
            return result(
                'KC_SECOND_BODY_WAIT', '外軌已破，等待第二根有效同色實體K',
                '第一根破軌已成立；第二根必須收線且實體比例達20%。', **extra)
        if outer.get('reason') == 'KC_LIVE_BODY_BREAKOUT_WAIT':
            return result(
                'KC_SPECIAL_BODY_WAIT', '等待順向實體達 ATR 特例K門檻',
                '特例K只看順向實體與 ATR，不要求外軌位置或通道內開盤。', **extra)
        if outer.get('action') == 'ENTER':
            return result('KC_ENTRY_READY', '已有入口訊號，等待送單風控', '仍須重驗帳戶、行情、票據與每根限次；不代表保證成交。', **extra)
        return result('KC_ENTRY_SIGNAL_WAIT', '等待外軌突破或 ATR 特例K',
                  '中軌趨勢不開倉；一般入口需外軌破軌與第二根確認，特例K只看順向實體 ATR。', **extra)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        return result('KC_ENTRY_DATA_WAIT', '等待有效行情資料', '資料不足或無效，不推測進場方向。')
