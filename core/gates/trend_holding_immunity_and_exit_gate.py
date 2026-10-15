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

        # 新單保護期 (Grace Period)：開倉 120 秒內嚴禁被跨週期靜態指標秒殺平倉
        open_time = float(position.get('open_time') or position.get('timestamp') or 0.0)
        if open_time > 1e11:
            open_time /= 1000.0
        is_new_position = bool(open_time > 0 and (time.time() - open_time < 120.0))
        
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

        # ──【第一道出場：高檔利潤動態回撤止盈 (Dynamic Ratchet Profit Lock)】──
        # 規格：
        # 1. 若 Peak Profit >= 2.5 ATR (或 ROE >= 25%)：允許最大回撤 25% (保留 75% 峰值利潤)。
        # 2. 若 Peak Profit >= 1.5 ATR (或 ROE >= 12%)：允許最大回撤 35% (保留 65% 峰值利潤)。
        # 3. 若 Peak Profit >= 1.0 ATR (或 ROE >= 8%)：利潤保本移損，鎖定 entry_price ± 0.05%。
        leverage = float(position.get('leverage') or 5.0)
        margin_roe_pct = peak_roe_pct * leverage # 槓桿 ROE
        curr_margin_roe_pct = curr_roe_pct * leverage

        has_tier2_ratchet = (peak_profit_atr >= 2.5 or margin_roe_pct >= 0.25)
        has_tier1_ratchet = (peak_profit_atr >= 1.5 or margin_roe_pct >= 0.12)
        has_breakeven_lock = (peak_profit_atr >= 1.0 or margin_roe_pct >= 0.08)

        qty = float(position.get('qty', 0.0))

        if has_tier2_ratchet:
            max_allowed_giveback = 0.25
            floor_profit_atr = peak_profit_atr * (1.0 - max_allowed_giveback)
            lock_price = (entry_p + floor_profit_atr * atr_5m) if side == 'LONG' else (entry_p - floor_profit_atr * atr_5m)
            if peak_roe_pct >= 0.10:
                lock_price_roe = entry_p * (1.0 + peak_roe_pct * 0.8) if side == 'LONG' else entry_p * (1.0 - peak_roe_pct * 0.8)
                lock_price = max(lock_price, lock_price_roe) if side == 'LONG' else min(lock_price, lock_price_roe)
            lock_price = round(lock_price, 8)
            locked_net = abs(lock_price - entry_p) * qty
            giveback_ratio = (peak_profit_atr - curr_profit_atr) / peak_profit_atr if peak_profit_atr > 0 else 0.0

            position['active_stop_price'] = lock_price
            position['breakeven_price'] = lock_price
            position['locked_profit_usdt'] = round(locked_net, 4)
            position['locked_phase'] = 2
            position['channel_profit_protection'] = {
                'locked_net': round(locked_net, 4),
                'breakeven_price': lock_price,
                'armed': True,
                'status': 'ARMED',
                'peak_profit_atr': round(peak_profit_atr, 2),
                'curr_profit_atr': round(curr_profit_atr, 2),
                'giveback_ratio': round(giveback_ratio, 4),
            }
            if (side == 'LONG' and p <= lock_price) or (side == 'SHORT' and p >= lock_price):
                return True, "EXIT_BY_RATCHET_PROFIT_LOCK", {
                    'tier': 'TIER2_PEAK_2.5ATR',
                    'peak_profit_atr': peak_profit_atr,
                    'curr_profit_atr': curr_profit_atr,
                    'giveback_ratio': giveback_ratio,
                    'lock_price': lock_price,
                    'trigger_price': p,
                }

        elif has_tier1_ratchet:
            max_allowed_giveback = 0.35
            floor_profit_atr = peak_profit_atr * (1.0 - max_allowed_giveback)
            lock_price = (entry_p + floor_profit_atr * atr_5m) if side == 'LONG' else (entry_p - floor_profit_atr * atr_5m)
            lock_price = round(lock_price, 8)
            locked_net = abs(lock_price - entry_p) * qty
            giveback_ratio = (peak_profit_atr - curr_profit_atr) / peak_profit_atr if peak_profit_atr > 0 else 0.0

            position['active_stop_price'] = lock_price
            position['breakeven_price'] = lock_price
            position['locked_profit_usdt'] = round(locked_net, 4)
            position['locked_phase'] = 1
            position['channel_profit_protection'] = {
                'locked_net': round(locked_net, 4),
                'breakeven_price': lock_price,
                'armed': True,
                'status': 'ARMED_TIER1_35PCT_GIVEBACK',
                'peak_profit_atr': round(peak_profit_atr, 2),
                'curr_profit_atr': round(curr_profit_atr, 2),
                'giveback_ratio': round(giveback_ratio, 4),
            }
            if (side == 'LONG' and p <= lock_price) or (side == 'SHORT' and p >= lock_price):
                return True, "EXIT_BY_RATCHET_PROFIT_LOCK", {
                    'tier': 'TIER1_PEAK_1.5ATR',
                    'peak_profit_atr': peak_profit_atr,
                    'curr_profit_atr': curr_profit_atr,
                    'giveback_ratio': giveback_ratio,
                    'lock_price': lock_price,
                    'trigger_price': p,
                }

        elif has_breakeven_lock:
            # 確保保本僅在價格真正回抽至開倉成本線 (Entry Price ± 0.05%) 時才觸發
            be_price = (entry_p * 1.0005) if side == 'LONG' else (entry_p * 0.9995)
            be_price = round(be_price, 8)
            position['active_stop_price'] = be_price
            position['breakeven_price'] = be_price
            position['locked_profit_usdt'] = round(abs(be_price - entry_p) * qty, 4)
            position['locked_phase'] = 0
            position['channel_profit_protection'] = {
                'locked_net': 0.0,
                'breakeven_price': be_price,
                'armed': True,
                'status': 'ARMED_BREAKEVEN_ENTRY_PRICE',
                'peak_profit_atr': round(peak_profit_atr, 2),
            }
            if (side == 'LONG' and p <= be_price) or (side == 'SHORT' and p >= be_price):
                return True, "EXIT_BY_BREAKEVEN_LOCK", {
                    'entry_price': entry_p,
                    'trigger_price': p,
                    'be_price': be_price,
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
                'target_profit_atr': 1.5,
            }

        # 取得 15M 指標
        if df_15m is not None and len(df_15m) >= 2:
            m15 = DualTimeframeProvider.get_15m_trend(df_15m)
        else:
            from core.gates.meme_shared_protection_gate import MemeSharedProtectionGate
            m15 = MemeSharedProtectionGate._resample_or_derive_15m(df_5m) if df_5m is not None else {}

        # ──【第二道出場：避雷針收割、均線高檔死叉與真結構破位】──
        # 嚴格取已收盤 5M K 棒 (df_5m.iloc[-2] 或已收定型之 bar) 判定結構破位：
        # 盤中任何插針、走動中的影線一律無視，嚴禁對走動中的 iloc[-1] 進行 MA15 破位判定！
        last_closed_bar = None
        if df_5m is not None and len(df_5m) >= 2:
            if 'is_closed' in df_5m.columns:
                closed_bars = df_5m[df_5m['is_closed'] == True]
                last_closed_bar = closed_bars.iloc[-1] if len(closed_bars) >= 1 else df_5m.iloc[-2]
            else:
                last_closed_bar = df_5m.iloc[-2] if len(df_5m) >= 3 else df_5m.iloc[-1]

        if last_closed_bar is not None:
            c_5m = float(last_closed_bar.get('close', p))
            o_5m = float(last_closed_bar.get('open', p))
            h_5m = float(last_closed_bar.get('high', p))
            l_5m = float(last_closed_bar.get('low', p))
            ma5_5m = float(last_closed_bar.get('ma5', p))
            ma15_5m = float(last_closed_bar.get('ma15', p))
        else:
            c_5m = m5.get('5m_close', p)
            o_5m = m5.get('5m_open', p)
            h_5m = m5.get('5m_high', p)
            l_5m = m5.get('5m_low', p)
            ma5_5m = m5.get('5m_ma5', p)
            ma15_5m = m5.get('5m_ma15', p)

        cross_5m = m5.get('ma_cross', 'NONE')
        body_5m = abs(c_5m - o_5m)
        range_5m = max(h_5m - l_5m, 1e-9)

        kc_trend_15m = m15.get('15m_kc_trend', 'RANGE')
        mid_15m = m15.get('15m_kc_middle', 0.0)
        c_15m = m15.get('15m_close', p)

        if side == 'LONG':
            # 1. 高檔避雷針/長上影線主動收割（Exhaustion Upper Wick Exit）
            # 多單持倉中，若處於顯著浮盈（Peak Profit >= 1.2 ATR 或 margin_roe >= 10%）：
            # 當根 K 線出現巨幅上影線（上影線 >= 實體 1.5 倍，或上影線佔整根振幅 >= 60%）且收盤實體回落
            upper_wick = max(0.0, h_5m - max(c_5m, o_5m))
            has_wick_exhaustion = (upper_wick >= 0.5 * atr_5m) and (
                (body_5m > 0 and upper_wick >= 1.5 * body_5m) or (range_5m > 0 and upper_wick >= 0.60 * range_5m)
            )
            if (peak_profit_atr >= 1.2 or margin_roe_pct >= 0.10) and has_wick_exhaustion:
                return True, "EXIT_BY_EXHAUSTION_UPPER_WICK", {
                    'peak_profit_atr': peak_profit_atr,
                    'upper_wick': upper_wick,
                    'body_5m': body_5m,
                    'range_5m': range_5m,
                    'upper_wick_ratio': upper_wick / range_5m if range_5m > 0 else 0.0,
                }

            # 2. 均線高檔高位死叉保護：若浮盈 >= 1.0 ATR，且 5M MA5 已經死叉 MA15
            if (peak_profit_atr >= 1.0 or margin_roe_pct >= 0.08) and (cross_5m == 'DEATH' or (ma5_5m < ma15_5m and cross_5m != 'GOLDEN')):
                return True, "EXIT_BY_HIGH_PROFIT_MA_CROSS_REVERSAL", {
                    'peak_profit_atr': peak_profit_atr,
                    'ma5_5m': ma5_5m,
                    'ma15_5m': ma15_5m,
                    'cross': cross_5m,
                }

            # 3. 5M 實體陰線摜破 5M MA15 且 MA5 死叉 MA15
            is_red_5m = (c_5m < o_5m)
            pierced_ma15_5m = (c_5m < ma15_5m)
            death_crossed = (cross_5m == 'DEATH') or (ma5_5m < ma15_5m and o_5m >= ma15_5m)
            if is_red_5m and pierced_ma15_5m and death_crossed:
                return True, "EXIT_BY_5M_STRUCTURE_BREAK_RED_PIERCE_MA15", {'detail': '5m red pierce ma15 and death cross'}

            # 4. 15M 通道明確逆向反轉且 5M 結構破位 (新單保護期內嚴禁觸發)
            if not is_new_position:
                if (kc_trend_15m == 'DOWN' or (mid_15m > 0 and c_15m < mid_15m)) and (c_5m < ma15_5m):
                    return True, "EXIT_BY_15M_TREND_REVERSED_OR_KC_MID_PIERCED", {'15m_kc_trend': kc_trend_15m, '15m_mid': mid_15m}

        elif side == 'SHORT':
            # ──【空頭趨勢鎖倉防禦規則 (Bear Trend-Riding Short Exit Guard)】──
            # 只要處於空頭趨勢（5M 收在 MA15 下方或 KC 中軌下行），嚴格遵守平倉禁令：
            # 1. 禁用微幅反彈鎖利：嚴禁 2%~3% 敏感反抽平空，盤中小綠棒/下影線視為下跌中繼正常呼吸，強制 HOLD！
            # 2. 允許通道內弱勢反抽：即使從 KC 下軌反抽回通道內，只要實體收盤未站上基準線 (c_5m <= ma15_5m)，一律不准平空！
            # 3. 短均線未拐頭不平倉：短均線壓制且方向向下，堅決持倉讓利潤奔跑！
            #
            # ──【唯二合法平倉條件】──
            # 條件 1：結構被破 (Trend Invalidation)
            # 5M K 棒收盤價實體突破並站穩 5M MA15 或 KC 中軌
            kc_mid_5m = m5.get('5m_kc_middle', ma15_5m)
            exit_basis = max(ma15_5m, kc_mid_5m)
            if c_5m > exit_basis:
                return True, "EXIT_BY_SHORT_STRUCTURE_BROKEN_CLOSE_ABOVE_BASIS", {
                    'close_5m': c_5m,
                    'exit_basis': exit_basis,
                    'ma15_5m': ma15_5m,
                    'kc_mid_5m': kc_mid_5m,
                    'desc': '5M 收盤價實體突破站穩基準線，空頭動能衰竭離場',
                }

            # 條件 2：極端恐慌天量插針 (Panic Climax Bottom)
            # ① 價格跌破 KC 下軌 (超賣負乖離過大)
            # ② 當根爆出 2.5 倍以上巨量 (確認針尖天量換手)
            # ③ 留下超過實體 2 倍長度的超長下影線 (針尖放量主力吃單)
            kc_lower_5m = m5.get('5m_kc_lower', 0.0)
            curr_vol = float(last_closed_bar.get('volume', 0.0))
            avg_vol = float(df_5m['volume'].tail(15).mean()) if df_5m is not None and 'volume' in df_5m.columns and len(df_5m) >= 10 else 0.0
            is_climax_volume = (avg_vol > 0 and curr_vol >= 2.5 * avg_vol)
            lower_wick = max(0.0, min(c_5m, o_5m) - l_5m)
            is_climax_wick = (lower_wick >= 2.0 * max(body_5m, 0.1 * atr_5m)) and (lower_wick / range_5m >= 0.50)
            is_severe_oversold = (kc_lower_5m > 0 and l_5m <= kc_lower_5m) or ((exit_basis - l_5m) / atr_5m >= 1.5)

            if is_severe_oversold and is_climax_volume and is_climax_wick:
                return True, "EXIT_BY_PANIC_CLIMAX_BOTTOM_2_5X_VOL", {
                    'peak_profit_atr': peak_profit_atr,
                    'lower_wick': lower_wick,
                    'body_5m': body_5m,
                    'volume_ratio': curr_vol / avg_vol if avg_vol > 0 else 0.0,
                    'desc': 'KC下軌外超賣 + 爆2.5倍天量 + 超長下影線>=實體2倍，恐慌針尖換手獲利離場',
                }

            # 只要仍在空頭結構內（c_5m <= exit_basis），任何一般下影線、小綠棒反抽或短線雜訊，一律強制 HOLD！
            return False, "REJECT_EXIT: BEAR_TREND_LOCK_INTACT", {
                'close_5m': c_5m,
                'exit_basis': exit_basis,
                'desc': '空頭主跌浪趨勢鎖定中，未破中軌且無天量插針，強制 HOLD 嚴禁中途被反彈洗下車',
            }

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
