"""Read-only entry diagnostics; viewing a chart never observes or consumes a turn."""
import math
from core.channel_outer_entry import ck_direction, aligned_entry, live_adverse_entry_safe, live_ma3_direction_ready


def entry_diagnostics(engine, symbol, frame, price, now):
    def result(reason, message, detail, **extra):
        return dict(reason=reason, message=message, detail=detail, **extra)
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
            return result('KC_ENTRY_FRAME_WAIT', '等待本根行情資料', '不沿用上一根的盤中峰谷。')
        side = ck_direction(frame)
        outer = aligned_entry(frame, price)
        quoted = float(getattr(engine, '_channel_entry_quote_times', {}).get(symbol, float('nan')))
        fresh = math.isfinite(quoted) and 0 <= now - quoted <= 5
        tracker = getattr(engine, '_channel_live_pivots', None)
        state = tracker.states.get(symbol, {}) if tracker is not None else {}
        pivot_ready = bool(side and fresh and state.get('identity') == (bar / 1000, side)
                           and state.get('at') == quoted and state.get('ready')
                           and state.get('last') == (price if side == 'LONG' else -price))
        room = None
        extra = dict(side=side, price=price, quote_fresh=fresh, pivot_ready=pivot_ready,
                     outer_signal=outer.get('reason'), profit_room=room)
        if engine._channel_candle_entry_blocked(symbol, now):
            # The diagnostic does not grant the CK reverse exception; order validation does.
            ticket = getattr(engine.account, 'channel_profit_reentries', {}).get(symbol, {})
            if ticket.get('mode') != 'ck_reverse':
                return result('KC_ONE_ENTRY_PER_CANDLE', '本根K已有成交，等待下一根', '每根限次保留，必要平倉不受限制。', **extra)
        if not fresh:
            return result('KC_ENTRY_QUOTE_WAIT', '等待有效即時報價', '報價缺失或超過5秒，不送單。', **extra)
        if not side:
            key = 'kc_middle' if 'kc_middle' in frame else 'ema_20'
            a, b = frame.iloc[-3], frame.iloc[-2]
            detail = (f"已收線CK中軌 {float(a[key]):.10g} → {float(b[key]):.10g}；"
                      f"上軌 {float(a['kc_upper']):.10g} → {float(b['kc_upper']):.10g}；"
                      f"下軌 {float(a['kc_lower']):.10g} → {float(b['kc_lower']):.10g}。"
                      '中軌須嚴格順向且持倉側外軌不得逆向。')
            return result('KC_DIRECTION_WAIT', 'CK方向條件尚未一致', detail, **extra)
        if not live_ma3_direction_ready(frame, price, side):
            return result('KC_LIVE_MA3_DIRECTION_WAIT', '即時MA3未順向，暫不開倉', '多單須MA3上升、空單須下降；反向、持平或無效都不開。', **extra)
        if not live_adverse_entry_safe(frame, price, side):
            return result('KC_LIVE_ADVERSE_ENTRY_WAIT', '當根反向異常，暫不開倉', '沿用原開盤價及已收線ATR門檻。', **extra)
        ticket = getattr(engine.account, 'channel_profit_reentries', {}).get(symbol)
        if ticket:
            if ticket.get('phase') != 'closed':
                return result('KC_CLOSE_CONFIRMATION_WAIT', '等待舊倉平倉確認', '平倉成功前不重開或反手。', **extra)
            if ticket.get('requires_pullback', True) and ticket.get('mode') != 'ck_reverse':
                return result('KC_POST_CLOSE_PULLBACK_WAIT', '異常平倉後須重驗回踩', '後續K須先回到CK內，再順向站回原側外軌。', **extra)
            if ticket.get('side') != side:
                return result('KC_REENTRY_DIRECTION_WAIT', '預定重開方向與CK方向不同', '等待新的同向重開條件成立。', **extra)
        candidate = 'live:' + str(bar) if pivot_ready else engine._channel_candidate_bar_id(frame)
        if not ticket and (symbol, side, candidate) in getattr(engine, '_channel_invalid_entry_candidates', set()):
            return result('KC_CANDIDATE_INVALIDATED', '此候選訊號已失效', '等待下一個有效候選，再重新評估進場。', **extra)
        if pivot_ready or outer.get('action') == 'ENTER':
            return result('KC_ENTRY_READY', '已有入口訊號，等待送單風控', '仍須重驗帳戶、行情、票據與每根限次；不代表保證成交。', **extra)
        return result('KC_ENTRY_SIGNAL_WAIT', '等待盤中峰谷或有效外軌訊號',
                      '多單需實際報價先跌後升，空單先升後跌；順CK且MA3同向站在同側外軌外可即時評估，不等兩根K。', **extra)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        return result('KC_ENTRY_DATA_WAIT', '等待有效行情資料', '資料不足或無效，不推測進場方向。')
