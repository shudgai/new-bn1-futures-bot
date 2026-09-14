from core.services.exits.fading_exit_service import next_breakout_ready
"""Read-only entry diagnostics; viewing a chart never observes or consumes a turn."""
import math
from core.guards.abnormal_guard import opposite_entry_releases
from core.services.strategies.outer_strategy import (
    entry_trend_direction, aligned_entry, live_adverse_entry_safe,
    breakout_confirmation_pending, channel_middle_is_flat, ck_direction,
    live_body_breakout_side, LIVE_BREAKOUT_BODY_ATR,
    CHOP_BREAKOUT_CODES, CONFIRMED_OUTER_CODES,
)
from core.services.strategies.pivot_strategy import pivot_entry


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
        outer = engine._channel_entry_action(symbol, frame, price)
        pivot = pivot_entry(frame, price)
        pivot_ready = pivot.get("action") == "ENTER" and outer.get("action") == "ENTER"
        side = outer.get("side") or entry_trend_direction(frame)
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
        extra = dict(side=side, price=price, quote_fresh=fresh, pivot_ready=pivot_ready,
                 outer_signal=outer.get('reason'), profit_room=None,
                     special_k_side=special_side, live_body_atr=live_body_atr,
                     special_k_threshold=LIVE_BREAKOUT_BODY_ATR)
        if engine._channel_candle_entry_blocked(symbol, now, side=side, frame=frame, price=price):
            return result('KC_ONE_ENTRY_PER_CANDLE', '本根K已有成交，等待下一根', '平倉後不反手；每根限次保留。', **extra)
        if not fresh:
            return result('KC_ENTRY_QUOTE_WAIT', '等待有效即時報價', '報價缺失或超過5秒，不送單。', **extra)
        if outer.get("reason") == "KC_CHOP_WAIT":
            return result('KC_CHOP_WAIT', '盤整中，等待即時外軌突破',
                          '當根由軌內穿上軌開多、穿下軌開空，不等收線；軌內峰谷仍不開。', **extra)
        if side and engine._channel_peak_exit_reentry_blocked(
            "ENTER", False, side, frame, engine._channel_post_peak_exit_info(symbol),
            symbol, live_price=price,
        ):
            return result('KC_POST_PEAK_BREAKOUT_WAIT', '原趨勢未持續，等待新進場確認',
                          '同向趨勢持續可同根重開；峰頂下破須一根實體收線，轉向做多須兩根實體破軌確認。', **extra)
        if not side:
            return result('WAIT_MA15_PRICE_PIVOT', '等待MA3三點峰谷與右側收線確認',
                          '谷底轉上且右側K收漲評估多單；峰頂轉下且右側K收跌評估空單。峰谷不等待CK或1H轉向，盤整不開。', **extra)
        if not live_adverse_entry_safe(frame, price, side):
            return result('KC_LIVE_ADVERSE_ENTRY_WAIT', '當根反向異常，暫不開倉', '沿用原開盤價及已收線ATR門檻。', **extra)
        ticket = getattr(engine.account, 'channel_profit_reentries', {}).get(symbol)
        if ticket and ticket.get('mode') in ('direct_reverse', 'ck_reverse', 'ma3_turn_wait'):
            ticket = None
        if ticket and ticket.get('mode') == 'next_breakout' and outer.get('reason') not in {'KC_OUTSIDE_CONTINUATION_LONG', 'KC_OUTSIDE_CONTINUATION_SHORT'}:
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
        if (not ticket and outer.get('reason') not in CONFIRMED_OUTER_CODES | CHOP_BREAKOUT_CODES
                and (symbol, side, candidate) in getattr(engine, '_channel_invalid_entry_candidates', set())):
            return result('KC_CANDIDATE_INVALIDATED', '此候選訊號已失效', '等待下一個有效候選，再重新評估進場。', **extra)
        if outer.get('action') == 'ENTER' and outer.get('reason') in CHOP_BREAKOUT_CODES:
            return result(
                'KC_ENTRY_READY', '盤整即時破軌成立，等待送單風控',
                '當根由軌內穿出外軌，不等收線或MA3/KC轉向；縮回軌內取消，帳戶風控與每根限次照常。',
                **extra,
            )
        outer_candidate = (
            (math.isfinite(float(frame.iloc[-1].get('kc_upper', float('nan'))))
             and price > float(frame.iloc[-1]['kc_upper']))
            or
            (math.isfinite(float(frame.iloc[-1].get('kc_lower', float('nan'))))
             and price < float(frame.iloc[-1]['kc_lower']))
        )
        if outer.get('action') != 'ENTER' and not pivot_ready and outer_candidate and (outer.get('reason') == 'KC_SECOND_BODY_WAIT' or breakout_confirmation_pending(frame, side)):
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
                'KC_ENTRY_SIGNAL_WAIT', '等待MA3三點峰谷確認',
                '一般破軌須兩根已收線同向實體K，MA3與KC同向；只有確認的MA3峰谷可用一根右側同向收線K。',
                **extra,
            )
        terminal_side = side if outer_candidate else None
        continuation_signal = str(outer.get('reason') or '').startswith(
            'KC_OUTSIDE_CONTINUATION_'
        )
        if continuation_signal and outer.get('action') == 'ENTER':
            return result(
                'KC_ENTRY_READY', '平倉後原趨勢持續，可立即重開',
                '平倉已確認，MA3與KC仍同向；可同根重開，不等獲利冷卻，送單風控照常。',
                **extra,
            )
        if (engine._channel_terminal_market(frame) and outer_candidate
                and terminal_side and near_terminal_extreme(terminal_side)
                and not str(outer.get('reason') or '').startswith(
                    ('KC_LIVE_BODY_BREAKOUT_', 'KC_LONG_BODY_'))):
            return result('KC_TREND_END_WAIT', '末端弱量期間多空暫停開倉', '等趨勢重新明朗後重驗。', **extra)
        if outer.get('reason') == 'KC_BREAKOUT_REJECTION_WAIT':
            return result('KC_BREAKOUT_REJECTION_WAIT', '破軌已確認，等待影線與突破幅度檢查',
                          '沿用一般破軌防假突破：現價須超過外軌0.25 ATR，拒絕方向的影線不得超過實體80%。', **extra)
        if outer.get('reason') == 'KC_SECOND_BODY_WAIT':
            return result(
                'KC_SECOND_BODY_WAIT', '外軌已破，等待第二根有效同色實體K',
                '須實體破軌根＋同向實體確認根，兩根均已收線、實體占全長至少20%，收盤與現價均在同側軌外。', **extra)
        if outer.get('reason') == 'KC_LIVE_BODY_BREAKOUT_WAIT':
            return result(
                'KC_SPECIAL_BODY_WAIT', '等待順向實體達 ATR 特例K門檻',
                '特例K只看順向實體與 ATR，不要求外軌位置或通道內開盤。', **extra)
        if pivot_ready and outer.get('reason') == pivot.get('reason'):
            return result(
                'KC_ENTRY_READY', 'MA3峰谷入口已成立，等待送單風控',
                '三點MA3與右側同向K均已收線，不等待CK或1H轉向且未處於盤整；送單前重驗報價、峰谷條件與帳戶風控。',
                **extra,
            )
        if outer.get('action') == 'ENTER':
            return result(
                'KC_ENTRY_READY', 'KC入口已成立，掃描將送單',
                '不計算利潤空間；送單前只重驗可用餘額、兩槽上限、有效報價、每根限次、停損冷卻與異常行情。',
                **extra,
            )
        return result('KC_ENTRY_SIGNAL_WAIT', '等待MA3三點峰谷確認',
                  '一般入口等待兩根已收線實體破軌；確認MA3峰谷才可用一根同向收線K，峰谷不等待CK轉向，盤整不開。', **extra)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        return result('KC_ENTRY_DATA_WAIT', '等待有效行情資料', '資料不足或無效，不推測進場方向。')
