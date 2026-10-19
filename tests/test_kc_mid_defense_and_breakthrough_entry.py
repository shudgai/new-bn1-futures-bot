import pytest
import pandas as pd
from core.services.strategies.wild_swing_strategy import WildSwingStrategy
from core.gates.holding_protection_gate import HoldingProtectionExitGate
from core.gates.trend_riding_strategy import can_open_position


def test_long_kc_middle_defense_no_exit_on_pullback():
    """多單在 KC 中軌之上，無論收陰或利潤回撤，100% 絕對不平倉。"""
    strategy = WildSwingStrategy()
    position = {
        'symbol': 'BTC/USDT',
        'side': 'LONG',
        'entry_price': 0.0210,
        'amount': 1000.0,
    }
    # 收盤價 0.0212 穩穩踩在 KC 中軌 0.0205 之上，即使收陰 (open 0.0214 > close 0.0212)
    rows = [
        {'open': 0.0208, 'high': 0.0211, 'low': 0.0207, 'close': 0.0210, 'kc_middle': 0.0205, 'ma15': 0.0206, 'timestamp': 1000, 'is_closed': True},
        {'open': 0.0210, 'high': 0.0216, 'low': 0.0210, 'close': 0.0215, 'kc_middle': 0.0205, 'ma15': 0.0207, 'timestamp': 2000, 'is_closed': True},
        {'open': 0.0215, 'high': 0.0215, 'low': 0.0211, 'close': 0.0212, 'kc_middle': 0.0205, 'ma15': 0.0207, 'timestamp': 3000, 'is_closed': True},
    ]
    df = pd.DataFrame(rows)

    exit_sig = strategy.evaluate_exit('BTC/USDT', position, df, quote=0.0212)
    assert exit_sig is None, "處於 KC 中軌上方，多頭結構完整，嚴禁平倉！"

    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, df, quote=0.0212, candidate_reason='THREE_POINT_PIVOT'
    )
    assert allowed is False
    assert "HEALTHY_PULLBACK_ABOVE_MID" in reason


def test_short_kc_middle_defense_no_exit_on_bounce():
    """空單在 KC 中軌之下，無論收陽或小反彈，100% 絕對不平倉。"""
    strategy = WildSwingStrategy()
    position = {
        'symbol': 'BTC/USDT',
        'side': 'SHORT',
        'entry_price': 0.0210,
        'amount': 1000.0,
    }
    # 收盤價 0.0207 壓制在 KC 中軌 0.0215 之下，即使收陽小紅 (open 0.0205 < close 0.0207)
    rows = [
        {'open': 0.0212, 'high': 0.0213, 'low': 0.0209, 'close': 0.0210, 'kc_middle': 0.0215, 'ma15': 0.0213, 'timestamp': 1000, 'is_closed': True},
        {'open': 0.0210, 'high': 0.0210, 'low': 0.0204, 'close': 0.0205, 'kc_middle': 0.0215, 'ma15': 0.0212, 'timestamp': 2000, 'is_closed': True},
        {'open': 0.0205, 'high': 0.0208, 'low': 0.0205, 'close': 0.0207, 'kc_middle': 0.0215, 'ma15': 0.0212, 'timestamp': 3000, 'is_closed': True},
    ]
    df = pd.DataFrame(rows)

    exit_sig = strategy.evaluate_exit('BTC/USDT', position, df, quote=0.0207)
    assert exit_sig is None, "處於 KC 中軌下方，空頭結構完整，嚴禁平倉！"

    allowed, reason, _ = HoldingProtectionExitGate.validate_exit(
        position, df, quote=0.0207, candidate_reason='THREE_POINT_PIVOT'
    )
    assert allowed is False
    assert "HEALTHY_PULLBACK_BELOW_MID" in reason or "REJECT_EXIT" in reason


def test_anti_chop_breakthrough_entry():
    """平倉防刷保護：同一根 K 棒禁止秒開；價格未突破上次平倉價禁止開；有新突破立即開倉。"""
    rows = [
        {'open': 0.0200, 'high': 0.0210, 'low': 0.0199, 'close': 0.0208, 'kc_trend': 'UP', 'is_closed': True, 'timestamp': 1700000000000.0, 'ma5': 0.0207, 'ma15': 0.0202},
        {'open': 0.0208, 'high': 0.0215, 'low': 0.0207, 'close': 0.0214, 'kc_trend': 'UP', 'is_closed': True, 'timestamp': 1700000060000.0, 'ma5': 0.0212, 'ma15': 0.0205},
    ]
    df = pd.DataFrame(rows)

    # 1. 同一根 K 棒剛平多，禁止立即秒開
    can_open, reason, _ = can_open_position(
        df=df,
        side='LONG',
        quote=0.0215,
        last_closed_side='LONG',
        last_closed_ts=1700000060000.0,  # 同一根 K 棒
        last_closed_price=0.0214,
    )
    assert can_open is False
    assert reason == 'BLOCKED_SAME_CANDLE_ANTI_CHOP'

    # 2. 跨入新 K 棒，但價格 0.0213 <= 上次平倉價 0.0214，未創新高禁止追多
    can_open_sub, reason_sub, _ = can_open_position(
        df=df,
        side='LONG',
        quote=0.0213,
        last_closed_side='LONG',
        last_closed_ts=1700000000000.0,  # 前一根 K 棒
        last_closed_price=0.0214,
    )
    assert can_open_sub is False
    assert reason_sub == 'BLOCKED_REQUIRES_NEW_HIGH_ABOVE_LAST_EXIT'

    # 3. 跨入新 K 棒，且價格 0.0218 > 上次平倉價 0.0214，形成新突破，解除冷卻立即放行開多！
    can_open_break, reason_break, _ = can_open_position(
        df=df,
        side='LONG',
        quote=0.0218,
        last_closed_side='LONG',
        last_closed_ts=1700000000000.0,  # 前一根 K 棒
        last_closed_price=0.0214,
    )
    assert can_open_break is True


def test_mode_a_golden_and_death_cross_entry():
    """模式 A：MA5/MA15 交叉 + 中軌位置確認 + 安全區防護。"""
    from core.gates.breakout_continuation_entry_gate import BreakoutContinuationEntryGate, MA_CROSSOVER

    # 金叉開多：MA5 由下往上穿過 MA15，收在 KC 中軌之上且未過度超出上軌
    df_long = pd.DataFrame([
        {'open': 0.0200, 'high': 0.0205, 'low': 0.0198, 'close': 0.0202, 'kc_upper': 0.0215, 'kc_lower': 0.0195, 'kc_middle': 0.0200, 'ma5': 0.0201, '_wild_ma5': 0.0201, 'ma15': 0.0202, 'is_closed': True},
        {'open': 0.0202, 'high': 0.0208, 'low': 0.0201, 'close': 0.0206, 'kc_upper': 0.0216, 'kc_lower': 0.0194, 'kc_middle': 0.0201, 'ma5': 0.0204, '_wild_ma5': 0.0204, 'ma15': 0.0203, 'is_closed': True},
    ])
    cand_long = BreakoutContinuationEntryGate.evaluate_pure_math(
        df_long.to_dict('records'),
        df_long['kc_upper'].tolist(),
        df_long['kc_lower'].tolist(),
        df_long['kc_middle'].tolist(),
        df_long['_wild_ma5'].tolist(),
        df_long['ma15'].tolist(),
    )
    assert cand_long is not None
    assert cand_long['action'] == 'OPEN_LONG'
    assert cand_long['type'] == MA_CROSSOVER

    # 死叉開空：MA5 由上往下穿過 MA15，收在 KC 中軌之下且未過度跌破下軌
    df_short = pd.DataFrame([
        {'open': 0.0206, 'high': 0.0208, 'low': 0.0201, 'close': 0.0204, 'kc_upper': 0.0215, 'kc_lower': 0.0195, 'kc_middle': 0.0205, 'ma5': 0.0205, '_wild_ma5': 0.0205, 'ma15': 0.0204, 'is_closed': True},
        {'open': 0.0204, 'high': 0.0205, 'low': 0.0198, 'close': 0.0200, 'kc_upper': 0.0216, 'kc_lower': 0.0194, 'kc_middle': 0.0204, 'ma5': 0.0202, '_wild_ma5': 0.0202, 'ma15': 0.0203, 'is_closed': True},
    ])
    cand_short = BreakoutContinuationEntryGate.evaluate_pure_math(
        df_short.to_dict('records'),
        df_short['kc_upper'].tolist(),
        df_short['kc_lower'].tolist(),
        df_short['kc_middle'].tolist(),
        df_short['_wild_ma5'].tolist(),
        df_short['ma15'].tolist(),
    )
    assert cand_short is not None
    assert cand_short['action'] == 'OPEN_SHORT'
    assert cand_short['type'] == MA_CROSSOVER


def test_mode_b_five_line_pullback_and_bounce_continuation():
    """模式 B：五線共振回踩/反彈有守次級發動延續開倉。"""
    from core.gates.breakout_continuation_entry_gate import BreakoutContinuationEntryGate, FIVE_LINE_CONTINUATION

    # 多單延續：五線向上，前一根回踩收陰，本根轉陽站回 MA5
    df_long = pd.DataFrame([
        {'open': 0.0205, 'high': 0.0212, 'low': 0.0204, 'close': 0.0209, 'kc_upper': 0.0218, 'kc_lower': 0.0198, 'kc_middle': 0.0204, 'ma5': 0.0208, '_wild_ma5': 0.0208, 'ma15': 0.0203, 'is_closed': True},
        {'open': 0.0209, 'high': 0.0210, 'low': 0.0205, 'close': 0.0206, 'kc_upper': 0.0220, 'kc_lower': 0.0196, 'kc_middle': 0.0206, 'ma5': 0.0207, '_wild_ma5': 0.0207, 'ma15': 0.0204, 'is_closed': True}, # 回踩收陰
        {'open': 0.0206, 'high': 0.0215, 'low': 0.0206, 'close': 0.0214, 'kc_upper': 0.0222, 'kc_lower': 0.0195, 'kc_middle': 0.0208, 'ma5': 0.0210, '_wild_ma5': 0.0210, 'ma15': 0.0205, 'is_closed': True}, # 轉陽站回MA5
    ])
    cand_long = BreakoutContinuationEntryGate.evaluate_pure_math(
        df_long.to_dict('records'),
        df_long['kc_upper'].tolist(),
        df_long['kc_lower'].tolist(),
        df_long['kc_middle'].tolist(),
        df_long['_wild_ma5'].tolist(),
        df_long['ma15'].tolist(),
    )
    assert cand_long is not None
    assert cand_long['action'] == 'OPEN_LONG'
    assert cand_long['type'] == FIVE_LINE_CONTINUATION

    # 空單延續：五線向下，前一根反彈收陽，本根轉陰跌破 MA5
    df_short = pd.DataFrame([
        {'open': 0.0205, 'high': 0.0206, 'low': 0.0198, 'close': 0.0200, 'kc_upper': 0.0212, 'kc_lower': 0.0192, 'kc_middle': 0.0206, 'ma5': 0.0201, '_wild_ma5': 0.0201, 'ma15': 0.0207, 'is_closed': True},
        {'open': 0.0200, 'high': 0.0205, 'low': 0.0199, 'close': 0.0203, 'kc_upper': 0.0210, 'kc_lower': 0.0190, 'kc_middle': 0.0204, 'ma5': 0.0202, '_wild_ma5': 0.0202, 'ma15': 0.0206, 'is_closed': True}, # 反彈收陽
        {'open': 0.0203, 'high': 0.0203, 'low': 0.0195, 'close': 0.0196, 'kc_upper': 0.0208, 'kc_lower': 0.0188, 'kc_middle': 0.0202, 'ma5': 0.0199, '_wild_ma5': 0.0199, 'ma15': 0.0205, 'is_closed': True}, # 轉陰跌破MA5
    ])
    cand_short = BreakoutContinuationEntryGate.evaluate_pure_math(
        df_short.to_dict('records'),
        df_short['kc_upper'].tolist(),
        df_short['kc_lower'].tolist(),
        df_short['kc_middle'].tolist(),
        df_short['_wild_ma5'].tolist(),
        df_short['ma15'].tolist(),
    )
    assert cand_short is not None
    assert cand_short['action'] == 'OPEN_SHORT'
    assert cand_short['type'] == FIVE_LINE_CONTINUATION


def test_instant_burst_breakout_mode_c():
    from core.gates.breakout_continuation_entry_gate import (
        BreakoutContinuationEntryGate,
        INSTANT_BURST_BREAKOUT,
    )
    from core.gates.trend_riding_strategy import can_open_position

    # 1. 驗證 evaluate_pure_math 在盤中即時爆破 KC 上軌時觸發 INSTANT_BURST_BREAKOUT
    closed_klines = [
        {"open": 100.0, "high": 100.5, "low": 99.8, "close": 100.2, "timestamp": 1000},
        {"open": 100.2, "high": 100.4, "low": 99.7, "close": 99.9, "timestamp": 2000},  # 小紅回踩
        {"open": 99.9, "high": 101.0, "low": 99.9, "close": 100.8, "timestamp": 3000},  # 轉陽
        {"open": 100.8, "high": 101.5, "low": 100.7, "close": 101.2, "timestamp": 4000}, # 續陽
    ]
    kc_upper = [102.0, 102.0, 102.0, 102.0]
    kc_lower = [98.0, 98.0, 98.0, 98.0]
    kc_basis = [100.0, 100.0, 100.0, 100.0]
    ma5_series = [100.0, 100.1, 100.4, 100.8]
    ma15_series = [100.5, 100.4, 100.3, 100.2]  # MA15 滯後仍微下傾

    # 當前盤中 K 棒：開盤 101.2，最新價暴拉至 102.5 (穿破上軌 102.0)
    live_candle = {
        "open": 101.2, "high": 102.5, "low": 101.2, "close": 102.5,
        "kc_upper": 102.0, "kc_lower": 98.0, "kc_middle": 100.0,
        "ma15": 100.2, "timestamp": 5000, "is_closed": False,
    }
    cand_burst = BreakoutContinuationEntryGate.evaluate_pure_math(
        closed_klines,
        kc_upper,
        kc_lower,
        kc_basis,
        ma5_series,
        ma15_series,
        tick_price=102.5,
        live_candle=live_candle,
    )
    assert cand_burst is not None
    assert cand_burst["action"] == "OPEN_LONG"
    assert cand_burst["type"] in {INSTANT_BURST_BREAKOUT, "INSTANT_BREAKOUT_INSIDE"}

    # 2. 驗證 trend_riding_strategy.can_open_position 在 V 轉中軌滯後向下 (DOWN) 時，
    # 針對 INSTANT_BURST_BREAKOUT 豁免 kc_trend != 'UP' 阻斷，成功放行！
    import pandas as pd
    df = pd.DataFrame([
        {"open": 100.0, "high": 100.5, "low": 99.8, "close": 100.2, "kc_trend": "DOWN", "channel_state": "DOWN", "timestamp": 1000},
        {"open": 100.2, "high": 100.4, "low": 99.7, "close": 99.9, "kc_trend": "DOWN", "channel_state": "DOWN", "timestamp": 2000},
        {"open": 99.9, "high": 101.0, "low": 99.9, "close": 100.8, "kc_trend": "DOWN", "channel_state": "DOWN", "timestamp": 3000},
        {"open": 100.8, "high": 101.5, "low": 100.7, "close": 101.2, "kc_trend": "DOWN", "channel_state": "DOWN", "timestamp": 4000},
        {"open": 101.2, "high": 102.5, "low": 101.2, "close": 102.5, "kc_upper": 102.0, "kc_lower": 98.0, "kc_middle": 100.0, "ma15": 100.2, "timestamp": 5000},
    ])
    df["is_closed"] = [True, True, True, True, False]
    df["ma5"] = [100.0, 100.1, 100.4, 100.8, 101.5]
    df["ma15"] = [100.5, 100.4, 100.3, 100.2, 100.2]
    df["kc_upper"] = [102.0, 102.0, 102.0, 102.0, 102.0]
    df["kc_lower"] = [98.0, 98.0, 98.0, 98.0, 98.0]
    df["kc_middle"] = [100.0, 100.0, 100.0, 100.0, 100.0]

    allowed, reason, details = can_open_position(
        df,
        side="LONG",
        symbol="LOBSTERUSDT",
        quote=102.5,
        entry_confirmation=INSTANT_BURST_BREAKOUT,
    )
    assert allowed is True, f"應成功豁免滯後KC中軌開多，但被攔截: {reason}"


def test_cap_waterfall_continuation_and_breakout_entry():
    from core.gates.breakout_continuation_entry_gate import (
        BreakoutContinuationEntryGate,
        CONTINUATION_OUTSIDE_KC,
        INSTANT_BURST_BREAKOUT,
    )
    from core.gates.trend_riding_strategy import can_open_position
    import pandas as pd

    # 模擬 CAP 瀑布殺跌：
    # 前一根長陰砸穿下軌（收在 97.0，下軌 98.0，最低曾殺到 96.5）
    # 當前盤中在 96.8（仍深在下軌外，MA5 97.5 < 中軌 100.0，且在 MA5 之下，但尚未跌破上一根影線最低點 96.5）
    closed_klines = [
        {"open": 100.2, "high": 100.5, "low": 99.8, "close": 100.0, "timestamp": 1000, "kc_upper": 102.0, "kc_lower": 98.0, "kc_middle": 100.0, "ma5": 100.2, "ma15": 100.5},
        {"open": 100.0, "high": 100.3, "low": 99.6, "close": 99.8, "timestamp": 2000, "kc_upper": 102.0, "kc_lower": 98.0, "kc_middle": 100.0, "ma5": 100.0, "ma15": 100.4},
        {"open": 99.8, "high": 100.1, "low": 99.5, "close": 99.7, "timestamp": 3000, "kc_upper": 102.0, "kc_lower": 98.0, "kc_middle": 100.0, "ma5": 99.8, "ma15": 100.3},
        {"open": 99.7, "high": 99.8, "low": 96.5, "close": 97.0, "timestamp": 4000, "kc_upper": 102.0, "kc_lower": 98.0, "kc_middle": 100.0, "ma5": 99.0, "ma15": 100.0},
    ]
    kc_upper = [102.0, 102.0, 102.0, 102.0]
    kc_lower = [98.0, 98.0, 98.0, 98.0]
    kc_basis = [100.0, 100.0, 100.0, 100.0]
    ma5_series = [100.2, 100.0, 99.8, 99.0]
    ma15_series = [100.5, 100.4, 100.3, 100.0]

    live_candle = {
        "open": 97.0, "high": 97.1, "low": 96.7, "close": 96.8,
        "kc_upper": 102.0, "kc_lower": 98.0, "kc_middle": 100.0,
        "ma15": 100.0, "timestamp": 5000, "is_closed": False,
    }

    # 1. 驗證 evaluate_pure_math 成功識別延續開空 CONTINUATION_OUTSIDE_KC
    cand = BreakoutContinuationEntryGate.evaluate_pure_math(
        closed_klines,
        kc_upper,
        kc_lower,
        kc_basis,
        ma5_series,
        ma15_series,
        tick_price=96.8,
        live_candle=live_candle,
    )
    assert cand is not None
    assert cand["action"] == "OPEN_SHORT"
    assert cand["type"] in {CONTINUATION_OUTSIDE_KC, INSTANT_BURST_BREAKOUT}

    # 2. 驗證 validate_closed_outer_entry 不會因過苛影線破前低或標籤互搏而失敗
    val = BreakoutContinuationEntryGate.validate_closed_outer_entry(
        closed_klines,
        "SHORT",
        CONTINUATION_OUTSIDE_KC,
        tick_price=96.8,
        live_candle=live_candle,
    )
    assert val is not None, "validate_closed_outer_entry 不得回傳 None"
    assert val["action"] == "OPEN_SHORT"

    # 3. 驗證 can_open_position 允許放行，不再拋出 BLOCKED_WILD_OUTER_ENTRY_CONFIRMATION_INVALID
    df = pd.DataFrame([
        {"open": 100.2, "high": 100.5, "low": 99.8, "close": 100.0, "timestamp": 1000},
        {"open": 100.0, "high": 100.3, "low": 99.6, "close": 99.8, "timestamp": 2000},
        {"open": 99.8, "high": 100.1, "low": 99.5, "close": 99.7, "timestamp": 3000},
        {"open": 99.7, "high": 99.8, "low": 96.5, "close": 97.0, "timestamp": 4000},
        {"open": 97.0, "high": 97.1, "low": 96.7, "close": 96.8, "kc_upper": 102.0, "kc_lower": 98.0, "kc_middle": 100.0, "ma15": 100.0, "timestamp": 5000},
    ])
    df["is_closed"] = [True, True, True, True, False]
    df["kc_trend"] = ["DOWN"] * 5
    df["channel_state"] = ["DOWN"] * 5
    df["ma5"] = [100.2, 100.0, 99.8, 99.0, 97.5]
    df["ma15"] = [100.5, 100.4, 100.3, 100.0, 99.8]
    df["kc_upper"] = [102.0] * 5
    df["kc_lower"] = [98.0] * 5
    df["kc_middle"] = [100.0] * 5

    allowed, reason, details = can_open_position(
        df,
        side="SHORT",
        symbol="CAPUSDT",
        quote=96.8,
        entry_confirmation=CONTINUATION_OUTSIDE_KC,
    )
    assert allowed is True, f"CAP 瀑布跌破延續開空應允許放行，但被攔截: {reason}"


@pytest.mark.anyio
async def test_cap_long_trend_shield_pullback_no_exit():
    """驗證 CAP 開多後，若中軌向上且價格守在 KC 中軌與 MA15 之上，盤中回踩 100% 被趨勢保護鎖攔截不平倉。"""
    from core.services.exits.entry_atr_protection import enforce_atr_protection

    class MockAccount:
        def __init__(self):
            self.position_meta = {}
            self.logs = []
            self.closed = []

        def log(self, msg, level="INFO"):
            self.logs.append((msg, level))

        def save_state(self):
            pass

        async def close_position(self, symbol, price, reason, is_manual=False):
            self.closed.append((symbol, price, reason))
            return True

    account = MockAccount()
    symbol = "CAP/USDT"
    position = {
        "symbol": symbol,
        "side": "LONG",
        "entry_price": 0.07044,
        "entry_mode": "WILD_SWING",
        "qty": 5000.0,
        "open_timestamp": 1792403820.0,
    }

    # 構造 17:59 時的走勢：KC 中軌向上，收盤價 0.07044 遠高於 KC 中軌 0.07035 與 MA15 0.07038
    # 曾創出高點 0.07048，回踩到 0.07044
    df = pd.DataFrame([
        {"timestamp": 1792403760000, "open": 0.07035, "high": 0.07040, "low": 0.07034, "close": 0.07038, "kc_middle": 0.07032, "kc_upper": 0.07050, "kc_lower": 0.07014, "ma15": 0.07034, "atr": 0.00005, "is_closed": True},
        {"timestamp": 1792403820000, "open": 0.07038, "high": 0.07048, "low": 0.07038, "close": 0.07046, "kc_middle": 0.07035, "kc_upper": 0.07052, "kc_lower": 0.07018, "ma15": 0.07036, "atr": 0.00005, "is_closed": True},
        {"timestamp": 1792403880000, "open": 0.07046, "high": 0.07056, "low": 0.07042, "close": 0.07044, "kc_middle": 0.07037, "kc_upper": 0.07053, "kc_lower": 0.07021, "ma15": 0.07038, "atr": 0.00005, "is_closed": False},
    ])
    df.attrs["timeframe_ms"] = 60_000
    df.attrs["snapshot_ms"] = 1792403882000
    account.positions = {symbol: position}
    account.channel_exit_frame_provider = lambda s: df

    # 先模擬在 0.07056 衝破上軌衝高（觸發 armed）
    await enforce_atr_protection(account, symbol, 0.07056)
    # 再模擬回踩到 0.07044（回撤超過 0.4 ATR）
    handled = await enforce_atr_protection(account, symbol, 0.07044)

    assert handled is False, "大趨勢向上且價格在 KC 中軌之上，中途回踩必須被 TREND_SHIELD 攔截，不得平倉！"
    assert len(account.closed) == 0, "不應調用 close_position"



