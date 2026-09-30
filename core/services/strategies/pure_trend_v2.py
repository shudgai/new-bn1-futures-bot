import logging
import math
from typing import Dict, Any, Optional, Union

logger = logging.getLogger("PureTrendV2_Meme")

class PureTrendStrategyV2:
    """
    妖幣純淨趨勢追蹤引擎：
    1. 開倉：前根已收線同向實體站外，下一根盤中突破前根高低點；共用趨勢、整理與距離限制。
    2. 物理禁區：KC 中軌上方嚴禁開空！KC 中軌下方嚴禁開多！
    3. 唯一策略出口：1.5ATR或5%淨利啟動，峰值回退超過0.4ATR或淨利回吐達20%即時全平。
    4. 帳戶與初始硬止損獨立保留，無收線、中軌、MA3或分批策略出口。
    """
    def __init__(self):
        self.cooldown_tracker = {}
        self.entry_rejection = "WAIT_PURE_TREND_V2"

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

    @staticmethod
    def outside_continuation_side(closed: Any) -> Optional[str]:
        """Two adjacent closed directional outside candles establish continuation."""
        if closed is None or len(closed) < 2:
            return None
        try:
            before, previous = closed.iloc[-2], closed.iloc[-1]
            if float(previous['timestamp']) - float(before['timestamp']) != 60000:
                return None
            for side, edge, sign in (('LONG', 'kc_upper', 1), ('SHORT', 'kc_lower', -1)):
                valid = True
                for bar in (before, previous):
                    opening, close, rail = (float(bar[key]) for key in ('open', 'close', edge))
                    if not all(math.isfinite(value) and value > 0 for value in (opening, close, rail)):
                        valid = False
                        break
                    if sign * (close - opening) <= 0 or sign * (close - rail) <= 0:
                        valid = False
                        break
                if valid:
                    return side
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        return None

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
        self.entry_rejection = "WAIT_PURE_TREND_V2"
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
        kc_upper_curr = float(bar_curr['kc_upper'])
        kc_lower_curr = float(bar_curr['kc_lower'])

        # -------------------------------------------------------------
        # 嚴格鎖死 KC 外軌條件：只要現價介於 [KC 下軌, KC 上軌] 之間，絕對禁止開倉
        # -------------------------------------------------------------
        if kc_lower_curr <= current_price <= kc_upper_curr:
            self.entry_rejection = "現價在通道內部，拒絕開倉"
            return None

        # 均線張角與通道寬度過濾（防死魚震盪）
        spread_pct = abs(ma3 - ma15) / current_price * 100.0
        if spread_pct < 0.08:
            self.entry_rejection = "均線間距不足0.08%"
            return None  # 均線走平黏合，直接一票否決！

        kc_middle_prev = float(bar_prev.get('kc_middle', current_price))
        channel_width_pct = (kc_upper_prev - kc_lower_prev) / kc_middle_prev * 100.0
        if channel_width_pct < 0.20:
            self.entry_rejection = "通道寬度不足0.20%"
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
        # Fresh breakouts retain consolidation; adjacent outside bodies may continue.
        # -------------------------------------------------------------
        # Fresh breakouts retain consolidation; adjacent outside bodies may continue.
        continuation_side = self.outside_continuation_side(closed)

        # -------------------------------------------------------------
        # 門禁 2：(已移除) 地板空 / 天花板多過濾 (滾動 15 根絕對極值)
        # -------------------------------------------------------------

        atr = float(bar_prev.get('atr', 0))

        body_curr = abs(current_price - curr_open)
        min_body_threshold = 0.5 * atr

        # -------------------------------------------------------------
        # 多單做多開倉標準範例：
        # -------------------------------------------------------------
        # 新增 Doji 過濾 (實體小於總長度 20% 或是小於 0.5 ATR 視為十字星/變盤線)
        prev_range = float(bar_prev['high']) - float(bar_prev['low'])
        prev_body = abs(prev_close - prev_open)
        is_prev_doji = (prev_body < 0.20 * prev_range) or (prev_body < 0.5 * atr) if prev_range > 0 else True
        is_curr_doji = (body_curr < 0.20 * curr_range) or (body_curr < 0.5 * atr) if curr_range > 0 else True
        
        # 雙棒推進：第 1 根實質收在軌道外，第 2 根開盤與現價依然在軌道外，且必須是實體陽線，且不能是十字星
        bar1_long_valid = (prev_close > kc_upper_prev) and (prev_close > prev_open) and not is_prev_doji
        # 確保當下也是實體陽線 (current_price > curr_open) 並過濾十字星
        bar2_breaks_prev_high = (current_price > kc_upper_curr) and (curr_open > kc_upper_curr) and (current_price > curr_open) and not is_curr_doji
        
        # 3. 動能檢驗：實體需達標且當前棒不能是極長上影十字星
        upper_wick_curr = float(bar_curr['high']) - max(curr_open, current_price)
        long_momentum_valid = (body_curr >= min_body_threshold)
        long_wick_valid = (body_curr == 0) or (upper_wick_curr <= body_curr * 1.5)

        two_bar_long = bar1_long_valid and bar2_breaks_prev_high and long_momentum_valid and long_wick_valid
        
        # 【趨勢過濾】做多必須 CK 向上
        long_trend_valid = ck_is_up
            
        # 【乖離過濾】做多進場價與 MA15 的距離不得大於 ma15_dist_limit
        if dist_from_ma15_atr > ma15_dist_limit:
            long_trend_valid = False

        # -------------------------------------------------------------
        # 4. 強勢突破與趨勢延續例外規則 (Trend Continuation & Override)
        # -------------------------------------------------------------
        # 多單延續條件：價格持續在軌外 + 均線多頭排列
        is_ma_bullish = (ma3 > ma15)
        continuation_long = (
            (current_price > kc_upper_curr) and
            (prev_close > kc_upper_prev) and
            is_ma_bullish and
            (body_curr >= 0.3 * atr) and
            (current_price > curr_open) and
            not is_curr_doji
        )
        # 強勢單棒突破 (Override)：實體超過 1.2 ATR 且突破軌道
        massive_breakout_long = (
            (current_price > kc_upper_curr) and
            (current_price > curr_open) and
            (body_curr >= 1.2 * atr)
        )
        
        final_long_signal = (two_bar_long or continuation_long or massive_breakout_long) and long_trend_valid

        if final_long_signal:
            res = {"action": "ENTRY_LONG"}
            return res

        # -------------------------------------------------------------
        # 空單做空開倉標準範例：
        # -------------------------------------------------------------
        # 放寬雙棒推進：第 1 根實體跌破 KC 下軌，第 2 根維持收陰且破軌
        bar1_short_valid = (prev_close < kc_lower_prev) and (prev_close < prev_open)
        bar2_breaks_prev_low = (current_price < kc_lower_curr) and (current_price < curr_open)
        
        two_bar_short = bar1_short_valid and bar2_breaks_prev_low
        
        # 【趨勢過濾】做空必須 CK 向下 (嚴禁逆勢)
        short_trend_valid = ck_is_down
            
        # 【乖離過濾】嚴禁極度超賣追空：做空進場價與 MA15 的距離不得大於 ma15_dist_limit
        if dist_from_ma15_atr > ma15_dist_limit:
            short_trend_valid = False

        # 優化強勢延續開倉：價格持續在下軌外與 MA5/MA3 下方，無須嚴格實體門檻
        continuation_short = (
            (current_price < kc_lower_curr) and
            (prev_close < kc_lower_prev) and
            (current_price < ma3)
        )
        # 強勢單棒突破 (Override)：實體超過 1.2 ATR 且突破軌道
        massive_breakout_short = (
            (current_price < kc_lower_curr) and
            (current_price < curr_open) and
            (body_curr >= 1.2 * atr)
        )

        final_short_signal = (two_bar_short or continuation_short or massive_breakout_short) and short_trend_valid

        if final_short_signal:
            res = {"action": "ENTRY_SHORT"}
            return res

        # Report the first failed condition for the actual outside direction.
        if (current_price > kc_upper_curr and current_price > curr_open) or (prev_close > kc_upper_prev and prev_close > prev_open):
            checks = [(ck_is_up, "CK方向未向上"),
                      (dist_from_ma15_atr <= ma15_dist_limit, "距MA15超過乖離上限"),
                      (two_bar_long or continuation_long or massive_breakout_long, "不符合雙棒推進或強勢延續")]
        elif (current_price < kc_lower_curr and current_price < curr_open) or (prev_close < kc_lower_prev and prev_close < prev_open):
            checks = [(ck_is_down, "CK方向未向下"),
                      (dist_from_ma15_atr <= ma15_dist_limit, "距MA15超過乖離上限"),
                      (two_bar_short or continuation_short or massive_breakout_short, "不符合雙棒推進或強勢延續")]
        else:
            checks = [(False, "未形成任何多空破軌動能")]
        self.entry_rejection = next(reason for passed, reason in checks if not passed)
        return None

    @staticmethod
    def advancing_body_rejection(closed, current, side):
        """Compare the live body with the first outside candle of this episode."""
        prefix = 'REJECT_ENTRY: 推進棒動能衰竭'
        try:
            if closed is None or closed.empty or side not in ('LONG','SHORT'):
                return prefix + '(缺少起爆棒資料)'
            sign = 1 if side == 'LONG' else -1
            edge = 'kc_upper' if side == 'LONG' else 'kc_lower'
            ignition = None
            expected = float(current['timestamp']) - 60000
            for _, bar in closed.iloc[::-1].iterrows():
                stamp, close, rail = (float(bar[k]) for k in ('timestamp','close',edge))
                if not all(math.isfinite(v) and v > 0 for v in (stamp,close,rail)) or stamp != expected:
                    return prefix + '(起爆段資料無效或不相鄰)'
                if sign*(close-rail) <= 0:
                    break
                ignition = bar
                expected -= 60000
            else:
                return prefix + '(無法確認起爆棒起點)'
            if ignition is None:
                return prefix + '(缺少起爆棒)'
            base = abs(float(ignition['close'])-float(ignition['open']))
            opening,close,high,low = (float(current[k]) for k in ('open','close','high','low'))
            atr = float(closed.iloc[-1]['atr'])
            if not all(math.isfinite(v) and v > 0 for v in (opening,close,high,low,atr,base)) or not low <= min(opening,close) <= max(opening,close) <= high:
                return prefix + '(實體或ATR資料無效)'
            body = abs(close-opening)
            below = lambda a,b: a < b and not math.isclose(a,b,rel_tol=1e-12)
            above = lambda a,b: a > b and not math.isclose(a,b,rel_tol=1e-12)
            if below(body,.5*base):
                return prefix + '(實體萎縮：小於起爆棒50%)'
            if below(body,.5*atr):
                return prefix + '(弱實體：小於0.5 ATR)'
            wick = high-max(opening,close) if side=='LONG' else min(opening,close)-low
            if above(wick,1.5*body) or (side=='LONG' and above(wick,.4*(high-low))):
                return prefix + '(長上影線)' if side=='LONG' else prefix + '(長下影線)'
            return None
        except (KeyError,TypeError,ValueError,OverflowError,IndexError):
            return prefix + '(資料無效)'

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
                rejection = self.advancing_body_rejection(closed, bar_curr, side)
                if rejection:
                    self.entry_rejection = rejection
                    return None
                signal_dict = dict(side=side, type='SECOND_BAR_OUTSIDE_' + side, price=current_price,
                            reason='符合標準開倉範例')
                if isinstance(result, dict) and 'initial_sl' in result:
                    signal_dict['initial_sl'] = result['initial_sl']
                return signal_dict
            return None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def evaluate_anti_whipsaw_profit_lock(self, position, current_price, bar_curr, atr):
        """Compatibility adapter for the sole real-time peak exit policy."""
        from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT
        from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
        return evaluate_peak_trailing(position, current_price, bar_curr, atr,
                                      fee=TAKER_FEE_RATE, slippage=SLIPPAGE_PCT)

    def check_intraday_instant_exit(self, position, current_tick_price, bar_curr, atr):
        """Shared tick protection, independent of candle finality."""
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

    def evaluate_bar_closed_exit(self, position: Dict[str, Any], closed: Any) -> Optional[str]:
        """Retired compatibility API: candle closure never authorizes an exit."""
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


def evaluate_v2_frame(frame, price=None, code=None, *, account=None, symbol='', diagnostics=None):
    """Recompute V2 at every boundary; return a complete execution contract."""
    if diagnostics is not None:
        diagnostics.clear()
        diagnostics["reason"] = "入口資料、棒次或訊號不符"
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
        # Cooldown check deferred to later so we can allow continuations
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
        if diagnostics is not None:
            diagnostics["reason"] = strategy.entry_rejection
        return None
    continuation = strategy.outside_continuation_side(closed) == decision['side']
    # Handle continuation states and cooldowns
    is_reentry = False
    if ticket or continuation:
        is_reentry = True
    else:
        # Prevent opening a brand new initial breakout if we are already outside
        before = closed.iloc[-2]
        edge = 'kc_upper' if decision['side'] == 'LONG' else 'kc_lower'
        before_close, before_edge = float(before['close']), float(before[edge])
        if all(math.isfinite(value) and value > 0 for value in (before_close, before_edge)):
            if float(previous['timestamp']) - float(before['timestamp']) == 60000:
                if before_close > before_edge if decision['side'] == 'LONG' else before_close < before_edge:
                    if diagnostics is not None:
                        diagnostics['reason'] = '前段已在軌外，但尚未形成同向延續'
                    return None

    if ticket and not is_reentry and bar_index - ticket['exit_bar_index'] < 5:
        if diagnostics is not None:
            diagnostics['reason'] = 'WAIT_POST_EXIT_5_BAR_COOLDOWN'
        return None

    reason_str = "符合標準開倉範例"
    if is_reentry:
        reason_str = "順勢延續開倉(Re-entry)"
        # 延續單縮緊硬停損：前一根 K 棒收盤價或軌道邊緣
    atr = float(closed.iloc[-1]['atr'])
    if not math.isfinite(atr) or atr <= 0:
        return None
    if diagnostics is not None:
        diagnostics["reason"] = reason_str
    
    # Overwrite the reason in the decision dictionary
    decision['reason'] = reason_str
    return dict(decision, type=code or decision['type'], price=quote, entry_atr=atr,
                confirmation_bar_id=stamp, breakout_bar_id=float(previous['timestamp']),
                close_price=float(previous['close']), intrabar=True,
                entry_phase=('CONTINUATION_REENTRY' if ticket else
                             'OUTSIDE_CONTINUATION' if continuation else 'INITIAL_BREAKOUT'),
                exit_bar_id=ticket['exit_bar_index']*60000 if ticket else None)
