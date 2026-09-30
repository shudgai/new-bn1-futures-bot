import logging
import math
from typing import Dict, Any, Optional, Union

logger = logging.getLogger("PureTrendV2_Meme")

class PureTrendStrategyV2:
    """
    妖幣純淨趨勢追蹤引擎：
    1. 開倉：前根已收線同向實體站外，下一根盤中站外且 MA3／MA15 同向；當根同向實體至少全長25%，末端影線不超過實體。
    2. 物理禁區：KC 中軌上方嚴禁開空！KC 中軌下方嚴禁開多！
    3. 盤中策略出口：嚴格穿越KC中軌，或曾達10U/3ATR後回吐超過25%；不等收線。
    4. 常規平倉：只在1M收線嚴格突破最近已確認峰谷時全平；無峰谷續抱。
    5. 未觸發平倉前，嚴格抱牢波段，一股都不賣！
    """
    def __init__(self):
        self.cooldown_tracker = {}

    def record_exit(self, symbol: str, side: str, current_bar_index: int):
        self.cooldown_tracker[symbol] = dict(exit_bar_index=int(current_bar_index), side=side)

    def strict_entry_preflight_check(
        self,
        side: str,
        bar_prev: Dict[str, Any],       # 第 1 根（已收盤 1M K 棒）
        bar_curr: Dict[str, Any],       # 當前盤中 1M K 棒
        current_price: float,
        indicators: Dict[str, Any]
    ) -> tuple[bool, str]:
        """
        開倉前最終硬性審查（一票否決制）：
        必須 100% 同時滿足所有規則，任一項不符立即拒絕！
        """
        kc_upper = float(indicators['kc_upper'])
        kc_lower = float(indicators['kc_lower'])
        ma3 = float(indicators['ma3'])
        ma15 = float(indicators['ma15'])
        c_open = float(bar_curr['open'])
        c_high = float(bar_curr['high'])
        c_low = float(bar_curr['low'])
        body = abs(current_price - c_open)
        bar_range = c_high - c_low

        # ---------------------------------------------------------
        # 門禁 1：第 1 根（上一根已收盤）必須實質以收盤價破軌 (嚴禁影線摸軌)
        # ---------------------------------------------------------
        prev_close = float(bar_prev['close'])
        prev_kc_upper = float(bar_prev['kc_upper'])
        prev_kc_lower = float(bar_prev['kc_lower'])

        if side == 'LONG':
            if prev_close <= prev_kc_upper:
                return False, f"前根收盤 ({prev_close}) 未實質站上 KC 上軌 ({prev_kc_upper})，拒絕開多！"
        elif side == 'SHORT':
            if prev_close >= prev_kc_lower:
                return False, f"前根收盤 ({prev_close}) 未實質跌破 KC 下軌 ({prev_kc_lower})，拒絕開空！"

        # ---------------------------------------------------------
        # 門禁 2：當前盤中必須為同向推進，嚴禁反向 K 或十字星接盤
        # ---------------------------------------------------------
        if side == 'LONG':
            # 嚴禁紅陰線開多
            if current_price <= c_open:
                return False, f"當前為紅陰線 (現價 {current_price} <= 開盤 {c_open})，拒絕開多！"
            # 嚴禁十字星 (實體小於全波幅 25%)
            if bar_range > 0 and body < 0.25 * bar_range:
                return False, "當前為無動能十字星，拒絕開多！"
            # 嚴禁長上影線 (上影線大於實體 1.0 倍)
            if (c_high - current_price) > body * 1.0:
                return False, "當前帶顯著長上影線拋壓，拒絕開多！"

        elif side == 'SHORT':
            # 嚴禁綠陽線開空
            if current_price >= c_open:
                return False, f"當前為綠陽線 (現價 {current_price} >= 開盤 {c_open})，拒絕開空！"
            # 嚴禁十字星
            if bar_range > 0 and body < 0.25 * bar_range:
                return False, "當前為無動能十字星，拒絕開空！"
            # 嚴禁長下影線 (下影線大於實體 1.0 倍)
            if (current_price - c_low) > body * 1.0:
                return False, "當前帶顯著長下影線抵抗，拒絕開空！"

        # ---------------------------------------------------------
        # 門禁 3：均線張角防橫盤死魚 (MA3 與 MA15 差值必須張開)
        # ---------------------------------------------------------
        spread_pct = abs(ma3 - ma15) / current_price * 100
        if spread_pct < 0.04:
            return False, f"MA3 與 MA15 黏合 (張角僅 {spread_pct:.4f}% < 0.04%)，橫盤死魚拒絕開單！"

        return True, "驗證通過"

    def is_valid_directional_entry_bar(self, bar_curr, side):
        """Strict live color, >=25% body, and adverse-side wick <= body."""
        try:
            opened, close, high, low = (float(bar_curr[k]) for k in ('open', 'close', 'high', 'low'))
            if side not in ('LONG', 'SHORT') or not all(
                    math.isfinite(v) and v > 0 for v in (opened, close, high, low)):
                return False
            if not low <= min(opened, close) <= max(opened, close) <= high or high <= low:
                return False
            signed_body = (close-opened) * (1 if side == 'LONG' else -1)
            if signed_body <= 0:
                return False
            minimum = .25*(high-low)
            if signed_body < minimum and not math.isclose(signed_body, minimum, rel_tol=1e-12):
                return False
            wick = high-close if side == 'LONG' else close-low
            return wick <= signed_body or math.isclose(wick, signed_body, rel_tol=1e-12)
        except (KeyError, TypeError, ValueError, OverflowError):
            return False

    def check_standard_example_breakout_entry(
        self,
        bar_prev: Dict[str, Any],     # 第 1 根（剛收盤的破軌確認 K）
        bar_curr: Dict[str, Any],     # 第 2 根（當前盤中推進 K）
        current_price: float,
        indicators: Dict[str, Any],
        closed: Any = None,
        symbol: str = ""
    ) -> Optional[Union[str, Dict[str, Any]]]:
        """
        100% 依據使用者給定範例開倉：
        必須【全部條件同時為 True】，任一條件不符直接回傳 None（嚴禁開倉）！
        """
        kc_upper_prev = float(bar_prev['kc_upper'])
        kc_lower_prev = float(bar_prev['kc_lower'])
        prev_close = float(bar_prev['close'])
        prev_open = float(bar_prev['open'])
        
        curr_open = float(bar_curr['open'])
        curr_high = float(bar_curr['high'])
        curr_low = float(bar_curr['low'])
        curr_range = curr_high - curr_low
        curr_body = abs(current_price - curr_open)

        ma3 = float(indicators['ma3'])
        ma15 = float(indicators['ma15'])

        # 均線張角與通道寬度過濾（防死魚震盪）
        spread_pct = abs(ma3 - ma15) / current_price * 100.0
        if spread_pct < 0.08:
            return None  # 均線走平黏合，直接一票否決！

        kc_middle_prev = float(bar_prev.get('kc_middle', current_price))
        channel_width_pct = (kc_upper_prev - kc_lower_prev) / kc_middle_prev * 100.0
        if channel_width_pct < 0.20:
            return None  # 通道極度壓縮，波動率過低，拒絕開倉

        # -------------------------------------------------------------
        # 門禁 0：趨勢一致性與超買/超賣過濾 (乖離上限)
        # -------------------------------------------------------------
        if closed is not None and len(closed) >= 4:
            kc_middle_3_ago = float(closed.iloc[-4]['kc_middle'])
            ck_is_down = kc_middle_prev < kc_middle_3_ago
            ck_is_up = kc_middle_prev > kc_middle_3_ago
        else:
            ck_is_down = True
            ck_is_up = True
            
        atr = float(bar_prev.get('atr', 0.0001))
        # 解決 ATR 過度壓縮失真問題：使用 max(atr, atr_50)
        if closed is not None and len(closed) >= 50:
            tr = closed['high'].astype(float) - closed['low'].astype(float)
            atr_50 = float(tr.rolling(50).mean().iloc[-1])
            atr = max(atr, atr_50)

        # 針對妖幣/迷因幣放寬乖離上限 (直接豁免乖離限制)
        is_meme = any(meme in symbol for meme in ["PEPE", "DOGE", "WIF", "FLOKI", "NEIRO", "龙虾", "龍蝦", "LOBSTER", "LOKA", "TURBO", "1000", "MEME"])
        ma15_dist_limit = float('inf') if is_meme else 1.6
        dist_from_ma15_atr = abs(current_price - ma15) / atr if atr > 0 else 0.0

        # -------------------------------------------------------------
        # 門禁 1：起爆新鮮度過濾（必須經過通道內「充分整理」）
        # -------------------------------------------------------------
        # 破軌前 2 到前 5 根 (共 4 根)，至少有 3 根收在 KC 軌道之內
        if closed is not None and len(closed) >= 6:
            prev_4_bars = closed.iloc[-5:-1]
            inside_count = 0
            for _, row in prev_4_bars.iterrows():
                if row['kc_lower'] <= row['close'] <= row['kc_upper']:
                    inside_count += 1
            if inside_count < 3:
                return None  # 整理不充分，視為過期趨勢或連續單邊
        else:
            return None # 資料不足

        # -------------------------------------------------------------
        # 門禁 2：地板空 / 天花板多過濾 (滾動 15 根絕對極值)
        # -------------------------------------------------------------
        highest_15 = None
        lowest_15 = None
        if closed is not None and len(closed) >= 15:
            last_15 = closed.iloc[-15:]
            highest_15 = float(last_15['high'].max())
            lowest_15 = float(last_15['low'].min())
        elif closed is not None and len(closed) > 0:
            highest_15 = float(closed['high'].max())
            lowest_15 = float(closed['low'].min())

        atr = float(bar_prev.get('atr', 0))

        # -------------------------------------------------------------
        # 多單做多開倉標準範例：
        # -------------------------------------------------------------
        # 1. 第 1 根必須以收盤價實質收在 KC 上軌外側，且為同向陽線
        bar1_long_valid = (prev_close > kc_upper_prev) and (prev_close > prev_open)
        
        prev_high = float(bar_prev['high'])
        prev_low = float(bar_prev['low'])
        
        # 2. 第 2 根只要突破第 1 根長紅的最高價，即代表動能延續，直接開多 (不再要求同色或實體)
        bar2_breaks_prev_high = (current_price > prev_high)

        # 【天花板多過濾】開多位置離前高必須至少 2.0 ATR 空間，否則視為撞天花板
        ceiling_blocked = False
        if highest_15 is not None and atr > 0:
            if highest_15 > current_price and (highest_15 - current_price) < 2.0 * atr:
                ceiling_blocked = True

        # 【趨勢過濾】做多必須 CK 向上
        if not ck_is_up:
            bar1_long_valid = False
            
        # 【乖離過濾】做多進場價與 MA15 的距離不得大於 ma15_dist_limit，且超出上軌不得大於 0.8 ATR
        if dist_from_ma15_atr > ma15_dist_limit or (current_price > kc_upper_prev and (current_price - kc_upper_prev) > 0.8 * atr):
            bar1_long_valid = False

        if bar1_long_valid and bar2_breaks_prev_high and not ceiling_blocked:
            res = {"action": "ENTRY_LONG"}
            # 高乖離進場安全保護 (1.2 ~ 1.6/1.8 ATR 之間)，強制止損設為突破K最低點
            if dist_from_ma15_atr >= 1.2:
                res["initial_sl"] = prev_open if prev_open < prev_close else prev_close # 簡單防守點
            return res

        # -------------------------------------------------------------
        # 空單做空開倉標準範例：
        # -------------------------------------------------------------
        # 1. 第 1 根必須以收盤價實質收在 KC 下軌外側，且為同向陰線
        bar1_short_valid = (prev_close < kc_lower_prev) and (prev_close < prev_open)
        
        # 2. 第 2 根只要跌破第 1 根大長黑的最低價，即代表動能延續，直接開空 (不再要求同色或實體)
        bar2_breaks_prev_low = (current_price < prev_low)

        # 【地板空過濾】開空位置離前低必須至少 2.0 ATR 空間，否則視為死在地板上
        floor_blocked = False
        if lowest_15 is not None and atr > 0:
            if current_price > lowest_15 and (current_price - lowest_15) < 2.0 * atr:
                floor_blocked = True

        # 【趨勢過濾】做空必須 CK 向下 (嚴禁逆勢)
        if not ck_is_down:
            bar1_short_valid = False
            
        # 【乖離過濾】嚴禁極度超賣追空：做空進場價與 MA15 的距離不得大於 ma15_dist_limit，且跌破下軌不得大於 0.8 ATR
        if dist_from_ma15_atr > ma15_dist_limit or (current_price < kc_lower_prev and (kc_lower_prev - current_price) > 0.8 * atr):
            bar1_short_valid = False

        if bar1_short_valid and bar2_breaks_prev_low and not floor_blocked:
            res = {"action": "ENTRY_SHORT"}
            # 高乖離進場安全保護
            if dist_from_ma15_atr >= 1.2:
                res["initial_sl"] = prev_open if prev_open > prev_close else prev_close
            return res

        return None

    def evaluate_second_bar_outside_entry(
        self, symbol: str, bar_curr: Dict[str, Any], bar_prev: Dict[str, Any], closed: Any = None
    ) -> Optional[Dict[str, Any]]:
        """One closed directional outside body followed by its live next bar.

        Evaluation is read-only. The engine locks successful fills, not signals.
        """
        import numpy as np

        previous_closed = bar_prev.get('is_closed')
        current_closed = bar_curr.get('is_closed')
        if (not isinstance(previous_closed, (bool, np.bool_)) or not previous_closed
                or not isinstance(current_closed, (bool, np.bool_)) or current_closed):
            return None
        try:
            # Check timestamps to ensure they are consecutive
            p_stamp, c_stamp = float(bar_prev['timestamp']), float(bar_curr['timestamp'])
            if c_stamp - p_stamp != 60000:
                return None
                
            current_price = float(bar_curr['close'])
            
            # Delegate entirely to the standard example breakout entry
            result = self.check_standard_example_breakout_entry(
                bar_prev, bar_curr, current_price, bar_curr, closed, symbol
            )
            if result:
                action = result if isinstance(result, str) else result.get("action")
                side = action.replace('ENTRY_', '')
                signal_dict = dict(side=side, type='SECOND_BAR_OUTSIDE_' + side, price=current_price,
                            reason='符合標準開倉範例')
                if isinstance(result, dict) and 'initial_sl' in result:
                    signal_dict['initial_sl'] = result['initial_sl']
                return signal_dict
            return None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def evaluate_anti_whipsaw_profit_lock(self, position, current_price, bar_curr, atr):
        """Tick protection; no breakeven or fixed-profit tier exits."""
        try:
            price = float(current_price)
            entry = float(position['entry_price'])
            qty = abs(float(position.get('qty', position.get('quantity', 0.))))
            stamp, opened = float(bar_curr['quote_ms']), float(position['open_timestamp'])
            side = position['side']
            if side not in ('LONG', 'SHORT') or not all(
                    math.isfinite(v) and v > 0 for v in (price, entry, qty, stamp, opened)):
                return None
            if stamp < opened*1000:
                return None
            identity = [side, opened, entry, qty]
            state = position.get('instant_exit_state') or {}
            if state.get('identity') != identity:
                state = dict(identity=identity, peak=0.)
            if stamp < state.get('last_ms', 0):
                return None
            # Removed exits cannot survive a restart as pending closes.
            retired = {'EXIT_PROFIT_TIER2_LOCK', 'EXIT_BREAKEVEN_LOCK',
                       'EXIT_INTRADAY_PROFIT_DRAWDOWN_20PCT', 'EXIT_INTRADAY_ANOMALY_SPIKE',
                       'EMERGENCY_FLASH_CRASH_LONG', 'EMERGENCY_FLASH_SURGE_SHORT',
                       'EMERGENCY_GIANT_REVERSE_CANDLE', 'EMERGENCY_BTC_CRASH'}
            if state.get('pending') not in {None, 'EXIT_KC_MID_BREACH',
                                            'EXIT_INTRADAY_KC_MID_BREACH', 'EXIT_PEAK_DRAWDOWN_25PCT'}:
                state.pop('pending', None)
            if (position.get('closed_exit_state') or {}).get('reason') in retired:
                position['closed_exit_state'] = {}
            state.pop('breakeven_line', None)
            state.pop('tier2_line', None)
            if state.get('pending'):
                return dict(action='CLOSE_POSITION', type=state['pending'], price=price,
                            reason=state['pending'])
            sign = 1 if side == 'LONG' else -1
            pnl = sign*(price-entry)*qty
            state.update(last_ms=stamp, peak=max(float(state.get('peak', 0.)), pnl),
                         version=3)
            scale = float(atr or 0.)
            peak_gain = float(state.get('peak_gain_atr', 0.))
            if math.isfinite(scale) and scale > 0:
                peak_gain = max(peak_gain, sign*(price-entry)/scale)
            state['peak_gain_atr'] = peak_gain
            reached = lambda value, limit: value >= limit or math.isclose(value, limit, rel_tol=1e-12)
            position['instant_exit_state'] = state
            position['peak_pnl_usd'] = state['peak']
            position['peak_gain_atr'] = peak_gain
            position['peak_unrealized_profit_usd'] = state['peak']
            position['current_unrealized_pnl_usd'] = pnl
            reason = None
            mid = float(bar_curr.get('kc_middle') or 0.)
            if math.isfinite(mid) and mid > 0 and sign*(price-mid) < 0:
                reason = 'EXIT_KC_MID_BREACH'
            threshold = state['peak']*.75
            if reason is None and (reached(state['peak'], 10.) or reached(peak_gain, 3.)) and (
                    pnl < threshold and not math.isclose(pnl, threshold, rel_tol=1e-12)):
                reason = 'EXIT_PEAK_DRAWDOWN_25PCT'
            if reason:
                from core.services.exits.dual_track_exit_service import POLICY
                state['pending'] = reason
                position['closed_exit_state'] = dict(policy=POLICY, pending=True, reason=reason)
                return dict(action='CLOSE_POSITION', type=reason, price=price, reason=reason)
            return None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def check_intraday_instant_exit(self, position, current_tick_price, bar_curr, atr):
        """Compatibility adapter; only midpoint and large-profit drawdown remain."""
        decision = self.evaluate_anti_whipsaw_profit_lock(position, current_tick_price, bar_curr, atr)
        return decision['type'] if decision else None

    def evaluate_entry(self, symbol, bar_curr, bar_prev1, bar_prev2=None):
        """Compatibility entry point; all callers use the second-bar rule."""
        return self.evaluate_second_bar_outside_entry(symbol, bar_curr, bar_prev1)

    # =================================================================
    # 二、 盤中即時極端熔斷（每一秒檢查，不看收盤，立刻秒平逃命）
    # =================================================================
    def check_intra_bar_emergency_exit(self, position: Dict[str, Any], current_price: float, bar_snapshot: Dict[str, Any], btc_status: Dict[str, Any]) -> Optional[str]:
        # Legacy candle-body/BTC strategy exits are retired. Live exits use
        # observed ticks in evaluate_anti_whipsaw_profit_lock instead.
        return None

    # =================================================================
    # 三、 收盤平倉三部曲（若盤中無極端熔斷，抱到收線；滿足任一標準也必須平倉！）
    # =================================================================
    def evaluate_bar_closed_exit(self, position: Dict[str, Any], closed: Any) -> Optional[str]:
        from core.services.candle_data import closed_entry_candles
        if closed is None or 'is_closed' not in closed:
            return None
        closed = closed_entry_candles(closed)
        if len(closed) < 4 or position.get('side') not in ('LONG', 'SHORT'):
            return None
        try:
            rows = closed[['timestamp','high','low','close']].astype(float)
            if not all(math.isfinite(v) and v > 0 for v in rows.to_numpy().flat):
                return None
            if not (rows.timestamp.diff().dropna() == 60000).all():
                return None
            if not ((rows.low <= rows.close) & (rows.close <= rows.high)).all():
                return None
            current = rows.iloc[-1]
            opened = float(position.get('open_timestamp') or 0) * 1000
            if not math.isfinite(opened) or current.timestamp < opened:
                return None
            # The pivot and its right-hand confirmation precede the break bar.
            key = 'low' if position['side'] == 'LONG' else 'high'
            values = rows[key].tolist()
            # 至少要 5 根 K 棒來形成一個顯著的 Swing (左右各 2 根不低於/不高於它)
            for i in range(len(values)-3, 1, -1):
                pivot = values[i]
                if key == 'low':
                    found = (pivot < values[i-1] and pivot < values[i-2] and pivot < values[i+1] and pivot < values[i+2])
                else:
                    found = (pivot > values[i-1] and pivot > values[i-2] and pivot > values[i+1] and pivot > values[i+2])
                if found:
                    broken = current.close < pivot if key == 'low' else current.close > pivot
                    return ('EXIT_SWING_LOW_BREAK_CLOSED' if key == 'low' else 'EXIT_SWING_HIGH_BREAK_CLOSED') if broken else None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        return None

    def evaluate_third_bar_open_entry(self, symbol: str, current_bar: Dict[str, Any], bar_prev1: Dict[str, Any], bar_prev2: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.evaluate_entry(symbol, current_bar, bar_prev1, bar_prev2)


V2_ENTRY_CODES = frozenset(
    f"{rule}_{side}"
    for rule in ("SECOND_BAR_OUTSIDE",)
    for side in ("LONG", "SHORT")
)


def successful_exit_ticket(account, symbol):
    """Rebuild one-use re-entry permission from persisted successful fills."""
    if account is None:
        return None
    events = []
    for trade in getattr(account, 'trades', []):
        if trade.get('symbol') != symbol or trade.get('action') not in ('OPEN_LONG','OPEN_SHORT','CLOSE_LONG','CLOSE_SHORT'):
            continue
        try:
            stamp = float(trade.get('id'))
            if math.isfinite(stamp) and stamp > 0:
                events.append((stamp, trade))
        except (TypeError,ValueError):
            continue
    if not events:
        return None
    stamp, latest = max(events, key=lambda item:(item[0], item[1]['action'].startswith('OPEN_')))
    if not latest['action'].startswith('CLOSE_'):
        return None
    return dict(exit_bar_index=int(stamp // 60000), side=latest['action'][6:])


def evaluate_v2_frame(frame, price=None, code=None, *, account=None, symbol=''):
    """Recompute V2 at every boundary; return a complete execution contract."""
    from core.services.strategies.unified_entry_strategy import confirmed
    closed = confirmed(frame)
    if closed is None:
        return None
    import numpy as np
    row = frame.iloc[-1].to_dict()
    flag = row.get('is_closed')
    if not isinstance(flag, (bool, np.bool_)) or flag:
        return None  # Never turn a closed-only snapshot into a live second bar.
    try:
        quote = float(price if price is not None else row['close'])
        original_price = float(row['close'])
        stamp = float(row['timestamp'])
        if not all(math.isfinite(v) and v > 0 for v in (quote, original_price, stamp)):
            return None
        if stamp != float(closed.iloc[-1]['timestamp']) + 60000:
            return None
        # Keep live simple moving averages consistent with the latest quote.
        for key, period in (('ma3', 3), ('ma15', 15)):
            row[key] = float(row[key]) + (quote - original_price) / period
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    strategy = PureTrendStrategyV2()
    ticket = successful_exit_ticket(account, symbol)
    bar_index = int(float(row['timestamp']) // 60000)
    if ticket:
        strategy.record_exit(symbol, ticket['side'], ticket['exit_bar_index'])
        if bar_index - ticket['exit_bar_index'] < 5:
            return None
    if code is not None and code not in V2_ENTRY_CODES:
        return None
    # New ticks extend the live extremes without mutating the source frame.
    try:
        row['high'] = max(float(row['high']), quote)
        row['low'] = min(float(row['low']), quote)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    row['close'] = quote
    previous = closed.iloc[-1].to_dict()
    # The first bar of a re-entry pair must start after the successful close bar.
    if ticket and float(previous['timestamp']) // 60000 <= ticket['exit_bar_index']:
        return None
    decision = strategy.evaluate_second_bar_outside_entry(symbol, row, previous, closed)
    if not decision or (code is not None and decision['type'] != code):
        return None
    if not ticket:
        # The prior closed candle must be the first outside close of this episode.
        # Already-outside third/fourth bars cannot relabel themselves as bar two.
        try:
            before = closed.iloc[-2]
            edge = 'kc_upper' if decision['side'] == 'LONG' else 'kc_lower'
            before_close, before_edge = float(before['close']), float(before[edge])
            if not all(math.isfinite(v) and v > 0 for v in (before_close, before_edge)):
                return None
            if float(previous['timestamp']) - float(before['timestamp']) != 60000:
                return None
            if (before_close > before_edge if decision['side'] == 'LONG'
                    else before_close < before_edge):
                return None
        except (IndexError, KeyError, TypeError, ValueError, OverflowError):
            return None
    atr = float(closed.iloc[-1]['atr'])
    if not math.isfinite(atr) or atr <= 0:
        return None
    return dict(decision, type=code or decision['type'], price=quote, entry_atr=atr,
                confirmation_bar_id=stamp, breakout_bar_id=float(previous['timestamp']),
                close_price=float(previous['close']), intrabar=True,
                entry_phase='CONTINUATION_REENTRY' if ticket else 'INITIAL_BREAKOUT',
                exit_bar_id=ticket['exit_bar_index']*60000 if ticket else None)
