import time
import pytest
import pandas as pd

from core.services.exits.dual_track_exit_service import (
    DualTrackExitStrategy,
    is_early_lifecycle_protected,
    is_ma5_slope_shield_active,
    is_candle_closed_ma5_break,
    detect_extreme_bar_emergency_exit,
)
from core.services.entry_contract import _post_close_long_opportunity
from core.gates.pipeline import EntryGatePipeline


def test_early_lifecycle_protection_under_90s():
    """持倉未滿 90 秒，只要未跌破 1.2 ATR，強制保護不平倉。"""
    now = time.time()
    position = {
        'symbol': '龙虾/USDT',
        'side': 'LONG',
        'entry_price': 0.0430,
        'open_timestamp': now - 45.0,  # 45 秒前開倉
    }
    atr = 0.0002
    # 價格微跌 0.5 ATR (0.0001) 至 0.0429
    assert is_early_lifecycle_protected(position, 0.0429, atr) is True

    # 價格暴跌 1.5 ATR (0.0003) 至 0.0427 -> 擊穿 1.2 ATR，解除保護允許硬停損
    assert is_early_lifecycle_protected(position, 0.0427, atr) is False

    # 超過 90 秒 -> 解除早期保護
    position['open_timestamp'] = now - 95.0
    assert is_early_lifecycle_protected(position, 0.0429, atr) is False


def test_ma5_slope_shield():
    """強趨勢斜率盾牌：多單 MA5 斜率 > 0.03 ATR 向上，強制禁止平倉。"""
    # 測試從使用者要求的 core.services.dual_track_exit_service 直接匯入
    from core.services.dual_track_exit_service import is_ma5_slope_shield_active as alias_shield
    atr = 0.0002

    # MA5 升 0.000007 (+0.035 ATR > 0.03 ATR) -> 觸發保護
    strong_up_frame = pd.DataFrame([
        {'ma5': 0.042993},
        {'ma5': 0.043000},
    ])
    assert alias_shield(strong_up_frame, 'LONG', atr) is True

    # MA5 升 0.000004 (+0.02 ATR <= 0.03 ATR) -> 未達 0.03 ATR 門檻，不觸發盾牌
    mild_up_frame = pd.DataFrame([
        {'ma5': 0.042996},
        {'ma5': 0.043000},
    ])
    assert alias_shield(mild_up_frame, 'LONG', atr) is False

    # 平緩或走平 (斜率 0)
    flat_frame = pd.DataFrame([
        {'ma5': 0.0430},
        {'ma5': 0.0430},
    ])
    assert alias_shield(flat_frame, 'LONG', atr) is False


def test_candle_closed_only_ma5_break():
    """平倉必須收線確認：盤中跳價不平，收盤確認且跌破 MA5 才離場。"""
    frame = pd.DataFrame([
        {'close': 0.0428, 'ma5': 0.0430},
    ])
    # 盤中未收線 (is_closed=False) -> 不平倉
    assert is_candle_closed_ma5_break(frame, 'LONG', is_closed=False) is False

    # 收線確認 (is_closed=True) 且 close < ma5 -> 離場
    assert is_candle_closed_ma5_break(frame, 'LONG', is_closed=True) is True


def test_extreme_bar_emergency_exit():
    """極端大陰棒反殺緊急避險：實體 >= 0.8 ATR 且摜破 MA5 允許即時砍倉。"""
    atr = 0.0002
    # 陰棒 open 0.0430, quote 0.0428 (實體 0.0002 = 1.0 ATR >= 0.8 ATR), ma5 0.0429
    frame = pd.DataFrame([{
        'open': 0.0430,
        'high': 0.0430,
        'low': 0.0427,
        'close': 0.0428,
        'ma5': 0.0429,
    }])
    assert detect_extreme_bar_emergency_exit(frame, 0.0428, 'LONG', atr) is True

    # 普通小陰棒 (實體 0.00005 = 0.25 ATR < 0.8 ATR) -> 不砍倉
    frame_small = pd.DataFrame([{
        'open': 0.0430,
        'high': 0.0430,
        'low': 0.0429,
        'close': 0.04295,
        'ma5': 0.04298,
    }])
    assert detect_extreme_bar_emergency_exit(frame_small, 0.04295, 'LONG', atr) is False


def test_reentry_single_bar_pullback_ma5_support():
    """多單平倉後，回踩 MA5 獲得支撐並收陽線，成功觸發順勢延續開倉。"""
    close_trade_id = 1791862000000.0  # 平倉時間
    # closed bars with >= 3 bars
    closed = pd.DataFrame([
        {
            'timestamp': 1791861940000.0,
            'open': 0.0420, 'high': 0.0426, 'low': 0.0419, 'close': 0.0425,
            'ma5': 0.0423, 'ma15': 0.0418, 'atr': 0.0002,
        },
        {
            'timestamp': 1791862000000.0,
            'open': 0.0425, 'high': 0.0430, 'low': 0.0424, 'close': 0.0428,
            'ma5': 0.0426, 'ma15': 0.0420, 'atr': 0.0002,
        },
        {
            'timestamp': 1791862060000.0,
            'open': 0.0427, 'high': 0.0432, 'low': 0.0426, 'close': 0.0431,  # 回踩 0.0426 (<= ma5 0.0427) 收陽 0.0431
            'ma5': 0.0427, 'ma15': 0.0421, 'atr': 0.0002,
        },
    ])
    opportunity = _post_close_long_opportunity(closed, close_trade_id)
    assert opportunity is not None
    assert opportunity['kind'] == 'PULLBACK_SUPPORT'


def test_momentum_burst_short_blocked_when_kc_up_and_above_mid():
    """當 KC 為向上環境且價格在中軌之上時，禁止逆勢快車道開空。"""
    # 建立 11 根 K 線，KC 中軌向上，價格在 KC 中軌之上
    rows = []
    for i in range(11):
        c = 0.0950 + i * 0.0002
        rows.append({
            'timestamp': 1791860000000.0 + i * 60000,
            'open': c - 0.0001,
            'high': c + 0.0003,
            'low': c - 0.0003,
            'close': c,
            'kc_upper': 0.0965,
            'kc_middle': 0.0945,  # KC 中軌
            'kc_lower': 0.0925,
            'channel_state': 'KC向上',
            'ma5': c - 0.0001,
            'ma15': c - 0.0003,
            'atr': 0.0005,
            'is_closed': i < 10,
        })
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    # 報價 0.0948 在中軌 0.0945 之上，嘗試開空
    burst = EntryGatePipeline.detect_momentum_burst(df, 0.0948, side='SHORT')
    assert burst is None


def test_momentum_burst_short_blocked_when_ma15_sloping_up():
    """當 MA15 仍維持向上斜率時，禁止執行空單快車道進場。"""
    rows = []
    for i in range(11):
        c = 0.0940 - i * 0.0001
        rows.append({
            'timestamp': 1791860000000.0 + i * 60000,
            'open': c + 0.0002,
            'high': c + 0.0003,
            'low': c - 0.0003,
            'close': c,
            'kc_upper': 0.0965,
            'kc_middle': 0.0945,
            'kc_lower': 0.0925,
            'channel_state': 'KC走平',
            'ma5': 0.0940,
            'ma15': 0.0930 + i * 0.0001,  # MA15 向上斜率 (0.0930 -> 0.0940)
            'atr': 0.0005,
            'is_closed': i < 10,
        })
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    burst = EntryGatePipeline.detect_momentum_burst(df, 0.0935, side='SHORT')
    assert burst is None


def test_holding_protection_veto_by_ma5_slope_shield():
    """HoldingProtectionExitGate 驗證：若 MA5 斜率強勢順向，一票否決回踩平多。"""
    from core.gates.holding_protection_gate import HoldingProtectionExitGate
    position = {'symbol': '龙虾/USDT', 'side': 'LONG', 'entry_price': 0.0420}
    atr = 0.0002
    frame = pd.DataFrame([
        {'ma5': 0.0428, 'atr': atr, 'kc_middle': 0.0435, 'close': 0.0428},
        {'ma5': 0.0430, 'atr': atr, 'kc_middle': 0.0435, 'close': 0.0429},  # ma5 斜率 +0.0002 = 1.0 ATR > 0.03 ATR
    ])
    is_allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, frame, quote=0.0429, candidate_reason='EXIT_LONG_ON_SELLING_PRESSURE',
    )
    assert is_allowed is False
    assert reason == "VETO_MA5_SLOPE_SHIELD_ACTIVE"


def test_fresh_death_cross_gap_exemption():
    """初生死叉（MA5 剛下穿 MA15 且斜率向下）時，Gap 豁免，不報 BLOCKED_MA5_MA15_GAP_TOO_NARROW。"""
    from core.gates.pipeline import ma5_ma15_cross_chop_problem
    rows = []
    for i in range(15):
        rows.append({
            'timestamp': 1791860000000.0 + i * 60000,
            'open': 0.0500, 'high': 0.0505, 'low': 0.0495, 'close': 0.0500,
            'ma5': 0.0502 if i < 14 else 0.0498,  # 前根 0.0502 >= ma15 0.0500，最新根 0.0498 < ma15 0.0499 (死叉向下)
            'ma15': 0.0500 if i < 14 else 0.0499,
            'is_closed': i < 14,
        })
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    problem = ma5_ma15_cross_chop_problem(df, quote=0.0498, side='SHORT')
    assert problem != 'BLOCKED_MA5_MA15_GAP_TOO_NARROW'


def test_short_kc_live_body_breakout_space_exemption():
    """當實體陰線跌破 KC 下軌且 KC_TREND_DOWN 時，豁免 25% 下軌空間限制。"""
    from core.services.entry_contract import evaluate_entry_contract
    # 建立 20 根 K 線，通道向下
    rows = []
    for i in range(21):
        mid = 0.0500 - i * 0.0002
        c = mid - 0.0005 if i == 20 else mid
        rows.append({
            'timestamp': 1791860000000.0 + i * 60000,
            'open': c + 0.0003,
            'high': c + 0.0004,
            'low': c - 0.0001,
            'close': c,
            'kc_upper': mid + 0.0005,
            'kc_middle': mid,
            'kc_lower': mid - 0.0005,
            'channel_state': 'KC向下',
            'ma3': c - 0.0001,
            'ma5': c + 0.0001,
            'ma15': mid + 0.0001,
            'atr': 0.0004,
            'is_closed': i < 20,
        })
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    # 報價 0.0450 跌破 kc_lower (約 0.0455)，實體跌破下軌
    diagnostics = {}
    entry = evaluate_entry_contract(df, 0.0450, code='KC_LIVE_BODY_BREAKOUT_SHORT', diagnostics=diagnostics)
    # 驗證未被 BLOCKED_STRICT_MA_SPACING 或 BLOCKED_STRICT_CHANNEL_CONVERGENCE 攔截
    assert diagnostics.get('reason') not in ('BLOCKED_STRICT_MA_SPACING', 'BLOCKED_STRICT_CHANNEL_CONVERGENCE')


def test_momentum_stalling_exit():
    """衝高滯漲主動止盈/平倉：連續無法刷新高、上影線 >= 實體 0.8 倍且 MA5 走平，主動平多。"""
    from core.services.exits.dual_track_exit_service import detect_momentum_stalling_exit
    position = {
        'symbol': 'CAP/USDT',
        'side': 'LONG',
        'entry_price': 0.0940,
        'highest_price': 0.0950,
    }
    atr = 0.0005
    # 當前 K 線 open 0.0945, quote 0.0946, high 0.0949 (實體 0.0001, 上影線 0.0003 >= 0.8*body)
    # MA5 從 0.09440 到 0.094405 (|ΔMA5| = 0.000005 / 0.0005 = 0.01 ATR < 0.02 ATR 走平)
    frame = pd.DataFrame([
        {'ma5': 0.09440, 'open': 0.0942, 'high': 0.0947, 'low': 0.0941, 'close': 0.0945},
        {'ma5': 0.094405, 'open': 0.0945, 'high': 0.0949, 'low': 0.0944, 'close': 0.0946},
    ])
    assert detect_momentum_stalling_exit(position, frame, 0.0946, 'LONG', atr) is True


def test_candle_closed_dump_exit():
    """大實體破位果斷砍倉：實體陰線收盤確認跌破 MA5，當根收線立即平多。"""
    from core.services.exits.dual_track_exit_service import is_candle_closed_dump_exit
    # 實體陰線 open 0.0946, close 0.0940 < open 且 close 0.0940 < ma5 0.0944
    frame = pd.DataFrame([
        {'open': 0.0946, 'close': 0.0940, 'ma5': 0.0944},
    ])
    # 未收線 -> False
    assert is_candle_closed_dump_exit(frame, 'LONG', is_closed=False) is False
    # 收線確認 -> True
    assert is_candle_closed_dump_exit(frame, 'LONG', is_closed=True) is True


def test_anti_top_chase_pinbar():
    """高檔避雷針防追高：價格高出 MA5 超過 0.6 ATR 且拉出上影線 >= 實體 0.8 倍，禁止追多。"""
    from core.services.entry_contract import pre_flight_safety_check
    atr = 0.0005
    # quote 0.0950, ma5 0.0946 (差距 0.0004 = 0.8 ATR > 0.6 ATR)
    # open 0.0947, high 0.0954 (實體 0.0003, 上影線 0.0004 >= 0.8*body)
    frame = pd.DataFrame([
        {'open': 0.0944, 'close': 0.0948, 'high': 0.0949, 'low': 0.0943, 'ma5': 0.0945, 'ma15': 0.0940,
         'atr': atr, 'kc_upper': 0.0949, 'kc_middle': 0.0945, 'kc_lower': 0.0930, 'is_closed': True},
        {'open': 0.0947, 'close': 0.0950, 'high': 0.0954, 'low': 0.0946, 'ma5': 0.0946, 'ma15': 0.0940,
         'atr': atr, 'kc_upper': 0.0949, 'kc_middle': 0.0945, 'kc_lower': 0.0930, 'is_closed': False},
    ])
    passed, reason = pre_flight_safety_check(
        frame, quote=0.0950, side='LONG',
        decision_type='AUTHORIZED_BY_TREND_CONTINUATION_LONG',
        entry_phase='POST_CLOSE_CONTINUATION_ENTRY',
    )
    assert passed is False
    assert reason == "BLOCKED_ANTI_TOP_CHASE_PINBAR"


def test_v_reversal_momentum_burst_long():
    """V 轉底部動能爆發搶跑：實體 >= 0.7 ATR 站回 KC 中軌上方立即搶跑進場。"""
    from core.gates.pipeline import EntryGatePipeline
    rows = []
    for i in range(14):
        mid = 0.0500 - i * 0.0002
        c = mid - 0.0003
        rows.append({
            'timestamp': 1791860000000.0 + i * 60000,
            'open': c - 0.0002,
            'high': c + 0.0002,
            'low': c - 0.0004,
            'close': c,
            'kc_upper': mid + 0.0005,
            'kc_middle': mid,
            'kc_lower': mid - 0.0005,
            'channel_state': 'KC向下',
            'ma5': 0.0469 if i == 13 else c,
            'ma15': mid,
            'atr': 0.0004,
            'is_closed': True,
        })
    # 第 15 根 (即時 Forming Bar)：V 轉大陽線，open 0.0465 (中軌 0.0470 下方)，quote 0.0474 (中軌上方)
    # 實體 0.0474 - 0.0465 = 0.0009 >= 0.7 * 0.0004 (0.00028)
    rows.append({
        'timestamp': 1791860000000.0 + 14 * 60000,
        'open': 0.0465,
        'high': 0.0475,
        'low': 0.0464,
        'close': 0.0474,
        'kc_upper': 0.0475,
        'kc_middle': 0.0470,
        'kc_lower': 0.0465,
        'channel_state': 'KC向下',
        'ma5': 0.0471,
        'ma15': 0.0470,
        'atr': 0.0004,
        'is_closed': False,
    })
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    burst = EntryGatePipeline.detect_momentum_burst(df, quote=0.0474, side='LONG')
    assert burst is not None
    assert burst['type'] == 'KC_MOMENTUM_BURST_LONG'


def test_live_breakout_long_strict_filters():
    """驗證即時破軌開多的四大硬性過濾條件：
    1. KC 趨勢強制對齊 (kc_trend != 'DOWN')
    2. MA15 斜率不得為負 (ΔMA15 >= 0)
    3. 破軌實體必須 >= 0.6 ATR
    4. 排除假突破上影線阻力 (上影線 < 0.7 * 實體)
    """
    from core.services.entry_contract import pre_flight_safety_check

    atr = 0.0005

    def make_frame(channel_state='KC向上', ma15_prev=0.0450, ma15_curr=0.0451,
                   open_p=0.0460, high_p=0.0465, quote=0.0464, kc_upper=0.0462):
        rows = [
            {
                'timestamp': 1000, 'open': 0.0455, 'high': 0.0460, 'low': 0.0454, 'close': 0.0458,
                'ma5': 0.0455, 'ma15': ma15_prev, 'atr': atr,
                'kc_upper': kc_upper, 'kc_middle': 0.0455, 'kc_lower': 0.0448,
                'channel_state': channel_state, 'is_closed': True,
            },
            {
                'timestamp': 2000, 'open': open_p, 'high': high_p, 'low': 0.0459, 'close': quote,
                'ma5': 0.0460, 'ma15': ma15_curr, 'atr': atr,
                'kc_upper': kc_upper, 'kc_middle': 0.0456, 'kc_lower': 0.0448,
                'channel_state': channel_state, 'is_closed': False,
            },
        ]
        df = pd.DataFrame(rows)
        df.attrs['timeframe_ms'] = 60000
        return df

    # 1. KC 向下時禁止開多
    df_kc_down = make_frame(channel_state='KC向下', quote=0.0464, open_p=0.0460, high_p=0.0465)
    passed, reason = pre_flight_safety_check(
        df_kc_down, 0.0464, 'LONG', decision_type='KC_LIVE_BODY_BREAKOUT_LONG',
        entry_phase='KC_LIVE_OUTER_BREAKOUT',
    )
    assert passed is False
    assert reason == 'BLOCKED_BY_KC_TREND_DOWN'

    # 2. MA15 斜率向下時禁止開多
    df_ma15_down = make_frame(channel_state='KC向上', ma15_prev=0.0455, ma15_curr=0.0450, quote=0.0464)
    passed, reason = pre_flight_safety_check(
        df_ma15_down, 0.0464, 'LONG', decision_type='KC_LIVE_BODY_BREAKOUT_LONG',
        entry_phase='KC_LIVE_OUTER_BREAKOUT',
    )
    assert passed is False
    assert reason == 'BLOCKED_BY_MA15_SLOPE_DOWN'

    # 3. 實體不足 0.6 ATR (0.0003) 禁止開多：例如 kc_upper 0.0461, open 0.0460, quote 0.0462 > kc_upper (實體 0.0002 < 0.0003)
    df_small_body = make_frame(channel_state='KC向上', open_p=0.0460, quote=0.0462, high_p=0.04625, kc_upper=0.0461)
    passed, reason = pre_flight_safety_check(
        df_small_body, 0.0462, 'LONG', decision_type='KC_LIVE_BODY_BREAKOUT_LONG',
        entry_phase='KC_LIVE_OUTER_BREAKOUT',
    )
    assert passed is False
    assert reason == 'BLOCKED_INSUFFICIENT_BREAKOUT_BODY'

    # 4. 上影線 >= 0.7 * 實體（假突破碰軌受阻）：例如 open 0.0460, quote 0.0464 (實體 0.0004), high 0.0468 (上影線 0.0004 >= 0.7*body)
    df_upper_wick = make_frame(channel_state='KC向上', open_p=0.0460, quote=0.0464, high_p=0.0468)
    passed, reason = pre_flight_safety_check(
        df_upper_wick, 0.0464, 'LONG', decision_type='KC_LIVE_BODY_BREAKOUT_LONG',
        entry_phase='KC_LIVE_OUTER_BREAKOUT',
    )
    assert passed is False
    assert reason == 'BLOCKED_UPPER_WICK_RESISTANCE'

    # 5. 全條件滿足時成功通過審核：KC 向上、MA15 走平/向上、實體 0.0004 >= 0.6 ATR、上影線 0.0001 < 0.7*body
    df_perfect = make_frame(channel_state='KC向上', open_p=0.0460, quote=0.0464, high_p=0.0465)
    passed, reason = pre_flight_safety_check(
        df_perfect, 0.0464, 'LONG', decision_type='KC_LIVE_BODY_BREAKOUT_LONG',
        entry_phase='KC_LIVE_OUTER_BREAKOUT',
    )
    assert passed is True
    assert reason is None


def test_healthy_ma15_pullback_protection():
    """趨勢拉升中途（回踩保護）：價格回踩 MA5 甚至貼近 MA15，只要 1m 收盤未實體摜破 MA15，一律視為健康回測，禁止平倉。"""
    from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
    strategy = DualTrackExitStrategy()
    position = {
        'symbol': 'LOBSTER/USDT',
        'side': 'LONG',
        'entry_price': 0.0450,
        'entry_atr': 0.0005,
        'highest_price': 0.0460,
    }
    # 價格回踩跌破 MA5 (0.0454)，但收盤價 0.0453 依然高於 MA15 (0.0450)，實體 0.0002 = 0.4 ATR
    frame = pd.DataFrame([
        {'open': 0.0455, 'close': 0.0453, 'high': 0.0456, 'low': 0.0452,
         'ma5': 0.0454, 'ma15': 0.0450, 'atr': 0.0005, 'is_closed': True},
    ])
    exit_decision = strategy.evaluate_exit(position, frame=frame, current_price=0.0453, is_closed=True)
    # 驗證健康回踩保護生效，未觸發 MA5 平倉
    assert exit_decision is None


def test_ratchet_breakeven_profit_lock():
    """階梯利潤鎖定（Ratchet）：浮盈達 1.5 ATR 後，硬止損自動上推至保本位以上，鎖定贏面，避免獲利回吐變虧損。"""
    from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
    strategy = DualTrackExitStrategy()
    atr = 0.0010
    entry_p = 0.0500
    position = {
        'symbol': 'LOBSTER/USDT',
        'side': 'LONG',
        'entry_price': entry_p,
        'entry_atr': atr,
        'highest_price': entry_p + 1.8 * atr,  # 浮盈曾達 1.8 ATR (> 1.5 ATR)
    }
    frame = pd.DataFrame([
        {'open': 0.0505, 'close': 0.0501, 'high': 0.0506, 'low': 0.0500,
         'ma5': 0.0503, 'ma15': 0.0498, 'atr': atr, 'is_closed': True},
    ])
    # 當價格回落到保本位 (entry + 0.1 ATR = 0.0501)
    exit_decision = strategy.evaluate_exit(position, frame=frame, current_price=0.0501, is_closed=True)
    assert exit_decision == 'EXIT_BY_RATCHET_PROFIT_LOCK'


def test_fast_cross_agile_entry():
    """進場：MA5/MA15 交叉敏捷開倉，當 MA5 實質金叉 MA15 且當根收線確認 (close > ma5 且 close > open) 時立即觸發。"""
    from core.gates.pipeline import EntryGatePipeline
    atr = 0.0005
    rows = []
    # 構造 14 根歷史 K 線
    for i in range(14):
        rows.append({
            'timestamp': 1000 + i * 60000,
            'open': 0.0450, 'high': 0.0452, 'low': 0.0448, 'close': 0.0450,
            'ma5': 0.0449 if i < 13 else 0.04495,  # 前根 ma5 <= ma15
            'ma15': 0.0450,
            'atr': atr, 'kc_upper': 0.0460, 'kc_middle': 0.0450, 'kc_lower': 0.0440,
            'is_closed': True,
        })
    # 第 15 根即時 K 線：實質金叉，open 0.0449, quote 0.0453 (close > open 且 close > ma5 0.0451 > ma15 0.0450)
    rows.append({
        'timestamp': 1000 + 14 * 60000,
        'open': 0.0449, 'high': 0.0454, 'low': 0.0448, 'close': 0.0453,
        'ma5': 0.0451, 'ma15': 0.0450,
        'atr': atr, 'kc_upper': 0.0460, 'kc_middle': 0.0450, 'kc_lower': 0.0440,
        'is_closed': False,
    })
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    decision = EntryGatePipeline.detect_fast_cross(df, quote=0.0453, side='LONG', symbol='LOBSTER/USDT')
    assert decision is not None
    assert decision['type'] == 'KC_GOLDEN_CROSS_FAST_LONG'
    assert decision['side'] == 'LONG'


def test_doji_two_bar_confirmation_exit():
    """十字星出場必須「雙棒確認」：單根十字星絕不平倉，僅內部標記；次根實體陰線跌破十字星最低點才平倉；次根收陽則作廢警示繼續抱緊。"""
    from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
    strategy = DualTrackExitStrategy()
    atr = 0.0010
    entry_p = 0.0450
    position = {
        'symbol': 'LOBSTER/USDT',
        'side': 'LONG',
        'entry_price': entry_p,
        'entry_atr': atr,
        'highest_price': 0.0470,
    }
    # 棒 1：頂部收綠十字星（open 0.0470, close 0.0471, high 0.0475, low 0.0469）
    doji_bar = {
        'open': 0.0470, 'close': 0.0471, 'high': 0.0475, 'low': 0.0469,
        'ma5': 0.0470, 'ma15': 0.0460, 'atr': atr, 'is_closed': True,
    }
    prev_bar = {
        'open': 0.0465, 'close': 0.0470, 'high': 0.0471, 'low': 0.0464,
        'ma5': 0.0468, 'ma15': 0.0458, 'atr': atr, 'is_closed': True,
    }
    frame_doji = pd.DataFrame([prev_bar, doji_bar])

    # 1. 第一根十字星收線：嚴禁平倉，只標記 awaiting_doji_confirmation = True
    exit_decision = strategy.evaluate_exit(position, frame=frame_doji, current_price=0.0471, is_closed=True)
    assert exit_decision is None
    assert position.get('awaiting_doji_confirmation') is True
    assert position.get('doji_low') == 0.0469

    # 2. 次根若繼續收陽（如 12:31 龍蝦繼續衝高）：作廢清除標記，繼續抱緊！
    bullish_next_bar = {
        'open': 0.0471, 'close': 0.0476, 'high': 0.0478, 'low': 0.0470,
        'ma5': 0.0473, 'ma15': 0.0462, 'atr': atr, 'is_closed': True,
    }
    frame_bull = pd.DataFrame([doji_bar, bullish_next_bar])
    exit_decision = strategy.evaluate_exit(position, frame=frame_bull, current_price=0.0476, is_closed=True)
    assert exit_decision is None
    assert position.get('awaiting_doji_confirmation') is None  # 已作廢清除

    # 3. 重新標記十字星，若次根為「明確實體陰線且跌破 doji_low (0.0469)」：確認雙棒離場！
    strategy.evaluate_exit(position, frame=frame_doji, current_price=0.0471, is_closed=True)
    assert position.get('awaiting_doji_confirmation') is True

    bearish_confirm_bar = {
        'open': 0.0471, 'close': 0.0467, 'high': 0.0472, 'low': 0.0466,
        'ma5': 0.0469, 'ma15': 0.0460, 'atr': atr, 'is_closed': True,
    }
    frame_bear = pd.DataFrame([doji_bar, bearish_confirm_bar])
    exit_decision = strategy.evaluate_exit(position, frame=frame_bear, current_price=0.0467, is_closed=True)
    assert exit_decision == 'TWO_BAR_DOJI_REVERSAL_EXIT'


def test_emergency_dump_exit_intrabar():
    """突發大賣壓緊急避險（Emergency Dump Exit）：盤中即刻秒平，不等收線！
    條件 A: 跌幅 >= 0.7 ATR 且 吞沒前陽 70%
    條件 B: 巨陰貫穿 MA5 且跌幅 >= 0.8 ATR
    條件 C: 天地針上影線 >= 1.5 倍實體
    """
    from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
    strategy = DualTrackExitStrategy()
    atr = 0.0010
    position = {
        'symbol': 'LOBSTER/USDT',
        'side': 'LONG',
        'entry_price': 0.0450,
        'entry_atr': atr,
        'highest_price': 0.0480,
    }
    # 前一根陽線實體 0.0008 (0.8 ATR)
    prev_bar = {
        'open': 0.0466, 'close': 0.0474, 'high': 0.0475, 'low': 0.0465,
        'ma5': 0.0470, 'ma15': 0.0460, 'atr': atr, 'is_closed': True,
    }

    # 條件 A：盤中大反殺（Open 0.0474, Live Price 0.0466 -> drop 0.0008 = 0.8 ATR >= 0.7 ATR，吞沒 100% 前陽棒）
    curr_dump_bar = {
        'open': 0.0474, 'close': 0.0466, 'high': 0.0475, 'low': 0.0465,
        'ma5': 0.0470, 'ma15': 0.0460, 'atr': atr, 'is_closed': False,  # 盤中未收線！
    }
    frame_dump = pd.DataFrame([prev_bar, curr_dump_bar])
    exit_decision = strategy.evaluate_exit(position, frame=frame_dump, current_price=0.0466, is_closed=False)
    assert exit_decision == 'EMERGENCY_DUMP_EXIT'
