"""TrendHoldingAndExitManager
實現 5M（操作級別）+ 15M（大趨勢級別）持倉護航與兩道出場機制：
a. 第一道出場（保本移損）：浮盈峰值曾達 >= 1.2 ATR（或淨 ROE >= 8.0%）後，回撤碰觸 entry_price ± 0.05% 即刻市價保本出場。
b. 第二道出場（真結構破位）：
   - 多單：5M 實體陰線摜破 5M MA15 且 MA5 死叉 MA15；或 15M 通道轉 DOWN/摜破 15M KC 中軌；或高檔浮盈 >= 1.5 ATR 且上影線 >= 50% 避雷針收割。
   - 空單：5M 實體陽線突破 5M MA15 且 MA5 金叉 MA15；或 15M 通道轉 UP/突破 15M KC 中軌；或低檔浮盈 >= 1.5 ATR 且下影線 >= 50% 地板針收割。
c. 趨勢全程護航（一票否決）：未觸發上述結構破位前，多單在 5M MA15 之上、空單在 5M MA15 之下，一律 100% 駁回常規平倉 (REJECT_EXIT: TREND_INTACT_IMMUNITY)。
"""
from typing import Dict, Any, Optional, Tuple
import pandas as pd
from core.indicators.dual_timeframe_provider import DualTimeframeProvider


class TrendHoldingAndExitManager:
    @classmethod
    def evaluate_exit(
        cls,
        position: Dict[str, Any],
        df_5m: pd.DataFrame,
        df_15m: Optional[pd.DataFrame] = None,
        quote: Optional[float] = None,
        candidate_reason: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """審查當前持倉是否授權平倉。
        
        Returns:
            (can_exit, reason, details)
            can_exit == True: 授權出場
            can_exit == False: 一票否決 (VETO/REJECT_EXIT)
        """
        if not position:
            return False, "REJECT_EXIT: NO_POSITION", None

        side = str(position.get('side', '')).upper()
        if side not in ('LONG', 'SHORT'):
            return False, "REJECT_EXIT: INVALID_POSITION_SIDE", None

        entry_p = float(position.get('entry_price', 0.0))
        if entry_p <= 0:
            return False, "REJECT_EXIT: INVALID_ENTRY_PRICE", None

        p = float(quote) if quote is not None else entry_p
        
        # 取得 5M 指標
        m5 = DualTimeframeProvider.get_5m_metrics(df_5m) if df_5m is not None else {}
        atr_5m = float(m5.get('5m_atr', position.get('atr', entry_p * 0.01)))
        if atr_5m <= 0:
            atr_5m = entry_p * 0.01

        # 計算當前浮盈與歷史峰值浮盈
        curr_profit_usd = (p - entry_p) if side == 'LONG' else (entry_p - p)
        curr_profit_atr = curr_profit_usd / atr_5m
        curr_roe_pct = curr_profit_usd / entry_p

        peak_profit_atr = float(position.get('peak_profit_atr', 0.0))
        peak_roe_pct = float(position.get('peak_roe_pct', 0.0))
        highest_p = float(position.get('highest_price', entry_p))
        lowest_p = float(position.get('lowest_price', entry_p))

        if side == 'LONG':
            if p > highest_p:
                highest_p = p
            profit_from_peak_usd = (highest_p - entry_p)
        else:
            if p < lowest_p:
                lowest_p = p
            profit_from_peak_usd = (entry_p - lowest_p)

        calc_peak_atr = profit_from_peak_usd / atr_5m
        calc_peak_roe = profit_from_peak_usd / entry_p

        peak_profit_atr = max(peak_profit_atr, calc_peak_atr, curr_profit_atr)
        peak_roe_pct = max(peak_roe_pct, calc_peak_roe, curr_roe_pct)

        position['peak_profit_atr'] = round(peak_profit_atr, 4)
        position['peak_roe_pct'] = round(peak_roe_pct, 4)
        position['highest_price'] = highest_p
        position['lowest_price'] = lowest_p

        # ──【第一道出場：保本移損 (Hard Breakeven Exit)】──
        # 浮盈峰值曾達 >= 1.2 ATR（或淨 ROE >= 8.0%）後，回撤碰觸 entry_price ± 0.05% 即刻市價保本出場
        has_reached_breakeven_arm = (peak_profit_atr >= 1.2) or (peak_roe_pct >= 0.08)
        be_threshold = 0.0005 # 0.05%
        be_price = entry_p * (1.0 + be_threshold) if side == 'LONG' else entry_p * (1.0 - be_threshold)
        qty = float(position.get('qty', 0.0))
        locked_net = abs(be_price - entry_p) * qty

        if has_reached_breakeven_arm:
            position['active_stop_price'] = be_price
            position['breakeven_price'] = be_price
            position['locked_profit_usdt'] = round(locked_net, 4)
            position['locked_phase'] = 1
            position['channel_profit_protection'] = {
                'locked_net': round(locked_net, 4),
                'breakeven_price': be_price,
                'armed': True,
                'status': 'ARMED',
                'peak_profit_atr': round(peak_profit_atr, 2),
                'peak_roe_pct': round(peak_roe_pct * 100, 2),
            }
            if side == 'LONG' and p <= be_price:
                return True, "EXIT_BY_HARD_BREAKEVEN_PROTECTION", {
                    'peak_profit_atr': peak_profit_atr,
                    'peak_roe_pct': peak_roe_pct,
                    'entry_price': entry_p,
                    'trigger_price': p,
                }
            elif side == 'SHORT' and p >= be_price:
                return True, "EXIT_BY_HARD_BREAKEVEN_PROTECTION", {
                    'peak_profit_atr': peak_profit_atr,
                    'peak_roe_pct': peak_roe_pct,
                    'entry_price': entry_p,
                    'trigger_price': p,
                }
        else:
            position['locked_profit_usdt'] = 0.0
            position['locked_phase'] = 0
            position['channel_profit_protection'] = {
                'locked_net': 0.0,
                'breakeven_price': None,
                'armed': False,
                'status': 'WAIT_TRIGGER',
                'peak_profit_atr': round(peak_profit_atr, 2),
                'peak_roe_pct': round(peak_roe_pct * 100, 2),
            }

        # 取得 15M 指標
        if df_15m is not None and len(df_15m) >= 2:
            m15 = DualTimeframeProvider.get_15m_trend(df_15m)
        else:
            from core.gates.meme_shared_protection_gate import MemeSharedProtectionGate
            m15 = MemeSharedProtectionGate._resample_or_derive_15m(df_5m) if df_5m is not None else {}

        # ──【第二道出場：真結構破位 (True Structural Exit)】──
        c_5m = m5.get('5m_close', p)
        o_5m = m5.get('5m_open', p)
        h_5m = m5.get('5m_high', p)
        l_5m = m5.get('5m_low', p)
        ma5_5m = m5.get('5m_ma5', p)
        ma15_5m = m5.get('5m_ma15', p)
        cross_5m = m5.get('ma_cross', 'NONE')
        body_5m = m5.get('5m_body', abs(c_5m - o_5m))
        range_5m = max(h_5m - l_5m, 1e-9)

        kc_trend_15m = m15.get('15m_kc_trend', 'RANGE')
        mid_15m = m15.get('15m_kc_middle', 0.0)
        c_15m = m15.get('15m_close', p)

        if side == 'LONG':
            # 1. 5M 實體陰線摜破 5M MA15 且 MA5 死叉 MA15
            is_red_5m = (c_5m < o_5m)
            pierced_ma15_5m = (c_5m < ma15_5m)
            death_crossed = (cross_5m == 'DEATH') or (ma5_5m < ma15_5m and o_5m >= ma15_5m)
            if is_red_5m and pierced_ma15_5m and death_crossed:
                return True, "EXIT_BY_5M_STRUCTURE_BREAK_RED_PIERCE_MA15", {'detail': '5m red pierce ma15 and death cross'}

            # 2. 15M 通道轉 DOWN 或 摜破 15M KC 中軌
            if (kc_trend_15m == 'DOWN') or (mid_15m > 0 and c_15m < mid_15m):
                return True, "EXIT_BY_15M_TREND_REVERSED_OR_KC_MID_PIERCED", {'15m_kc_trend': kc_trend_15m, '15m_mid': mid_15m}

            # 3. 高檔浮盈 >= 1.5 ATR 且上影線 >= 50% 避雷針收割
            upper_wick = h_5m - max(c_5m, o_5m)
            if curr_profit_atr >= 1.5 and upper_wick >= 0.50 * range_5m:
                return True, "EXIT_BY_HIGH_PEAK_LIGHTNING_ROD_HARVEST", {'profit_atr': curr_profit_atr, 'upper_wick_ratio': upper_wick / range_5m}

        elif side == 'SHORT':
            # 1. 5M 實體陽線突破 5M MA15 且 MA5 金叉 MA15
            is_green_5m = (c_5m > o_5m)
            pierced_ma15_5m = (c_5m > ma15_5m)
            golden_crossed = (cross_5m == 'GOLDEN') or (ma5_5m > ma15_5m and o_5m <= ma15_5m)
            if is_green_5m and pierced_ma15_5m and golden_crossed:
                return True, "EXIT_BY_5M_STRUCTURE_BREAK_GREEN_PIERCE_MA15", {'detail': '5m green pierce ma15 and golden cross'}

            # 2. 15M 通道轉 UP 或 突破 15M KC 中軌
            if (kc_trend_15m == 'UP') or (mid_15m > 0 and c_15m > mid_15m):
                return True, "EXIT_BY_15M_TREND_REVERSED_OR_KC_MID_PIERCED", {'15m_kc_trend': kc_trend_15m, '15m_mid': mid_15m}

            # 3. 低檔浮盈 >= 1.5 ATR 且下影線 >= 50% 地板針收割
            lower_wick = min(c_5m, o_5m) - l_5m
            if curr_profit_atr >= 1.5 and lower_wick >= 0.50 * range_5m:
                return True, "EXIT_BY_LOW_VALLEY_FLOOR_PIN_HARVEST", {'profit_atr': curr_profit_atr, 'lower_wick_ratio': lower_wick / range_5m}

        # ──【第三道：趨勢全程護航（一票否決）】──
        # 未觸發上述結構破位前，多單在 5M MA15 之上、空單在 5M MA15 之下，一律 100% 駁回常規平倉
        if side == 'LONG':
            if c_5m >= ma15_5m:
                return False, "REJECT_EXIT: TREND_INTACT_IMMUNITY (LONG_ABOVE_5M_MA15)", {
                    'close_5m': c_5m, 'ma15_5m': ma15_5m,
                }
        elif side == 'SHORT':
            if c_5m <= ma15_5m:
                return False, "REJECT_EXIT: TREND_INTACT_IMMUNITY (SHORT_BELOW_5M_MA15)", {
                    'close_5m': c_5m, 'ma15_5m': ma15_5m,
                }

        # 跌破 MA15 且非護航狀態，若是特定出場候選放行
        if candidate_reason:
            return True, f"PASSED_BY_CANDIDATE: {candidate_reason}", None

        return False, "REJECT_EXIT: TREND_INTACT_IMMUNITY", None
