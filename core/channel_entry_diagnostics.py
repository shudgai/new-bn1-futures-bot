from core.channel_fading_exit import next_breakout_ready
"""Read-only entry diagnostics; viewing a chart never observes or consumes a turn."""
import math
from core.channel_abnormal_release import opposite_entry_releases
from core.channel_outer_entry import ck_direction, aligned_entry, live_adverse_entry_safe, live_ma3_direction_ready, ck_entry_momentum_ready


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
            return result('KC_ENTRY_FRAME_WAIT', '等待本根行情資料', '送單前須以本根有效行情重驗破軌入口。')
        side = ck_direction(frame)
        outer = aligned_entry(frame, price)
        quoted = float(getattr(engine, '_channel_entry_quote_times', {}).get(symbol, float('nan')))
        fresh = math.isfinite(quoted) and 0 <= now - quoted <= 5
        room = None
        extra = dict(side=side, price=price, quote_fresh=fresh, pivot_ready=False,
                     outer_signal=outer.get('reason'), profit_room=room)
        if engine._channel_candle_entry_blocked(symbol, now):
            return result('KC_ONE_ENTRY_PER_CANDLE', '本根K已有成交，等待下一根', '平倉後不反手；每根限次保留。', **extra)
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
        if not ck_entry_momentum_ready(frame, side):
            return result('KC_MOMENTUM_FADE_WAIT', 'CK動能衰退或資料不足，暫停新倉',
                          '最近4根已收線中軌的3次順向位移連續縮小時暫停；下一根重新評估，不影響持倉出口。', **extra)
        if not live_ma3_direction_ready(frame, price, side):
            return result('KC_LIVE_MA3_DIRECTION_WAIT', '即時MA3未順向，暫不開倉', '多單須MA3上升、空單須下降；反向、持平或無效都不開。', **extra)
        if not live_adverse_entry_safe(frame, price, side):
            return result('KC_LIVE_ADVERSE_ENTRY_WAIT', '當根反向異常，暫不開倉', '沿用原開盤價及已收線ATR門檻。', **extra)
        ticket = getattr(engine.account, 'channel_profit_reentries', {}).get(symbol)
        if ticket and ticket.get('mode') in ('direct_reverse', 'ck_reverse', 'ma3_turn_wait'):
            ticket = None
        if ticket and ticket.get('mode') == 'next_breakout':
            if not next_breakout_ready(engine.account, symbol, frame, price):
                return result('KC_NEXT_BREAKOUT_WAIT', '等待平倉後新的兩根破軌確認', '不反手；第一根破軌須在實際平倉K之後，多空皆須重新驗證。', **extra)
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
        if outer.get('action') == 'ENTER':
            return result('KC_ENTRY_READY', '已有入口訊號，等待送單風控', '仍須重驗帳戶、行情、票據與每根限次；不代表保證成交。', **extra)
        return result('KC_ENTRY_SIGNAL_WAIT', '等待上下外軌破軌確認',
                      '第一根已收線同向實體穿出外軌，第二根同色有效實體確認；最新價仍須在同側外軌外。', **extra)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        return result('KC_ENTRY_DATA_WAIT', '等待有效行情資料', '資料不足或無效，不推測進場方向。')
