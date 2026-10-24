import time
import pytest
from core.strategies.kc_trend_strategy import Bar, KCTrendStrategy, StrategyConfig

def test_bar_counter_monotonic_growth():
    """驗證長時間運行時，計數器超越 maxlen=500 仍持續單調遞增"""
    strat = KCTrendStrategy(StrategyConfig(min_bars_for_indicators=20))
    now = time.time() - 40000
    for i in range(550):
        bar = Bar(now + i * 60, 100.0, 101.0, 99.0, 100.0, 10.0)
        strat.on_bar_close(bar)
    
    assert len(strat.bars) == 500
    assert strat.bar_index == 550

def test_two_bar_breakout_strict_color_and_ma():
    """驗證第2根非同色不進場；第2根同色且站穩 MA3/MA15 才准進場"""
    cfg = StrategyConfig(min_bars_for_indicators=20, require_ma15_direction=False)
    strat = KCTrendStrategy(cfg)
    now = time.time() - 3600

    # 建立基準平穩行情
    for i in range(25):
        strat.on_bar_close(Bar(now + i * 60, 100.0, 100.5, 99.5, 100.0, 50.0))

    snap = strat._indicator_snapshot()
    kc_up = snap["kc_upper"]
    t = time.time()

    # 1. Bar1 突破上軌
    strat.on_bar_close(Bar(t - 120, 100.0, kc_up + 1.0, 99.8, kc_up + 0.5, 100.0))
    
    # 2. Bar2 站上軌但為陰線 (Close < Open) -> 兩根破軌確認必須拒絕
    strat.on_bar_close(Bar(t - 60, kc_up + 1.2, kc_up + 1.5, kc_up + 0.2, kc_up + 0.3, 100.0))
    bar2_reasons = []
    while True:
        s = strat.pop_pending_entry()
        if not s:
            break
        bar2_reasons.append(s.reason)
    assert "two_bar_kc_upper_breakout_confirmed" not in bar2_reasons

    # 3. Bar1 再次突破上軌
    curr_kc_up = strat._last_closed_snapshot["kc_upper"]
    strat.on_bar_close(Bar(t - 40, 100.0, curr_kc_up + 2.0, 99.8, curr_kc_up + 1.5, 100.0))
    
    # 4. Bar2 為陽線 (Close > Open) 且高於 KC/MA -> 順利進場
    curr_kc_up2 = strat._last_closed_snapshot["kc_upper"]
    strat.on_bar_close(Bar(t - 10, curr_kc_up2 + 0.2, curr_kc_up2 + 2.0, curr_kc_up2 + 0.1, curr_kc_up2 + 1.5, 100.0))
    bar4_signals = []
    while True:
        s = strat.pop_pending_entry()
        if not s:
            break
        bar4_signals.append(s)
    sig = next((s for s in bar4_signals if s.reason == "two_bar_kc_upper_breakout_confirmed"), None)
    assert sig is not None
    assert sig.side == "long"

def test_pending_entries_fifo_queue():
    """驗證多個訊號不會被覆蓋，依序以 FIFO 取出"""
    strat = KCTrendStrategy(StrategyConfig(min_bars_for_indicators=5, require_ma15_direction=False))
    # 手動注入訊號至佇列
    from core.strategies.kc_trend_strategy import EntrySignal
    s1 = EntrySignal("long", "reason_1", time.time(), 100.0)
    s2 = EntrySignal("short", "reason_2", time.time(), 95.0)
    strat._pending_entries.append(s1)
    strat._pending_entries.append(s2)

    assert strat.pop_pending_entry().reason == "reason_1"
    assert strat.pop_pending_entry().reason == "reason_2"
    assert strat.pop_pending_entry() is None

def test_independent_cooldown_by_side():
    """驗證做多平倉的冷卻鎖不會阻礙做空訊號"""
    strat = KCTrendStrategy(StrategyConfig(cooldown_bars_after_exit=2))
    strat._bar_counter = 10
    strat.notify_position_closed("long")

    assert strat.bar_index - strat.last_exit_bar_index["long"] <= 2
    # 做空應不受影響 (初始為 -10^9)
    assert strat.bar_index - strat.last_exit_bar_index["short"] > 2

def test_continuation_hard_reset_on_rebound_into_channel():
    """驗證價格一旦回縮到下軌以內，立即清空 breakout_side 與 continuation_count，不准再開延續空單"""
    cfg = StrategyConfig(min_bars_for_indicators=20, require_ma15_direction=False)
    strat = KCTrendStrategy(cfg)
    now = time.time() - 3600
    for i in range(25):
        strat.on_bar_close(Bar(now + i * 60, 100.0, 100.5, 99.5, 100.0, 50.0))

    # 模擬空頭破軌
    strat.breakout_side = "short"
    strat.continuation_count = 1
    snap = strat._indicator_snapshot()
    kc_lower = snap["kc_lower"]

    # 1. 價格反彈回下軌內側（price >= kc_lower）
    strat.current_bar = Bar(time.time(), 100.0, 100.5, 99.5, 100.0, 10.0)
    rebound_price = kc_lower + 0.1
    sig = strat._continuation_signal(rebound_price, time.time())

    assert sig is None
    # 狀態機必須硬性重置為 None 與 0
    assert strat.breakout_side is None
    assert strat.continuation_count == 0

def test_kc_mid_slope_and_clearance_filters():
    """驗證 KC 中軌方向過濾、中軌安全緩衝與快速縮口過濾"""
    cfg = StrategyConfig(
        min_bars_for_indicators=20,
        require_kc_mid_direction=True,
        require_close_clear_of_kc_mid=True,
        close_kc_mid_buffer_atr=0.20,
        block_rapid_kc_contraction=True,
    )
    strat = KCTrendStrategy(cfg)

    # 模擬 KC 中軌下行時嘗試開多 -> 應被 blocked_kc_mid_not_rising 拒絕
    snap_falling = {
        "kc_mid_slope_atr": -0.05,
        "close": 105.0,
        "kc_mid": 100.0,
        "atr": 1.0,
        "kc_width_contraction_ratio": 0.01,
        "kc_width_atr": 3.0,
        "range_atr": 3.0,
        "ma3_slope_atr": 0.1,
        "ma15_slope_atr": 0.1,
        "ma3_slope_flips": 0.0,
        "ma3_price_crosses": 0.0,
    }
    assert strat._global_filter_reason("long", snap_falling) == "blocked_kc_mid_not_rising"

    # 模擬價格緊貼中軌未拉開緩衝 (close <= kc_mid + 0.2*atr) -> 應被 blocked_long_not_clear_of_kc_mid 拒絕
    snap_near_mid = {
        "kc_mid_slope_atr": 0.05,
        "close": 100.1,
        "kc_mid": 100.0,
        "atr": 1.0,
        "kc_width_contraction_ratio": 0.01,
        "kc_width_atr": 3.0,
        "range_atr": 3.0,
        "ma3_slope_atr": 0.1,
        "ma15_slope_atr": 0.1,
        "ma3_slope_flips": 0.0,
        "ma3_price_crosses": 0.0,
    }
    assert strat._global_filter_reason("long", snap_near_mid) == "blocked_long_not_clear_of_kc_mid"

    # 模擬通道急遽縮口 (contraction >= 5%) -> 應被 blocked_rapid_kc_contraction 拒絕
    snap_contracting = {
        "kc_mid_slope_atr": 0.05,
        "close": 102.0,
        "kc_mid": 100.0,
        "atr": 1.0,
        "kc_width_contraction_ratio": 0.08,
        "kc_width_atr": 3.0,
        "range_atr": 3.0,
        "ma3_slope_atr": 0.1,
        "ma15_slope_atr": 0.1,
        "ma3_slope_flips": 0.0,
        "ma3_price_crosses": 0.0,
    }
    assert strat._global_filter_reason("long", snap_contracting) == "blocked_rapid_kc_contraction"


def test_true_pivot_peak_immediate_exit_without_kc_middle():
    """
    驗證多單「真峰頂確立立即平倉」：
    持倉衝高獲利後，頂部三點樞紐確立 (b2 High鶴立雞群，b3 確認收陰跌破實體中點)，
    立即發出 true_pivot_peak_exit 平倉，完全不必等到跌回 KC 中軌！
    """
    cfg = StrategyConfig(min_bars_for_indicators=20, require_ma15_direction=False, emergency_single_bar_move_atr=5.0)
    strat = KCTrendStrategy(cfg)
    now = time.time() - 3600

    # 1. 建立 25 根基準上漲 K 棒
    for i in range(25):
        base = 100.0 + i * 0.5
        strat.on_bar_close(Bar(now + i * 60, base - 0.2, base + 0.4, base - 0.3, base + 0.2, 50.0))

    snap = strat._indicator_snapshot()
    strat.notify_position_opened("long", avg_price=snap["close"])
    kc_mid = snap["kc_mid"]  # 約在 107~108 附近

    # 2. 構造頂部三點樞紐 (b1, b2, b3)
    t = now + 25 * 60
    # b1: 112.2 -> 113.0
    strat.on_bar_close(Bar(t + 60, 112.2, 113.2, 112.0, 113.0, 50.0))
    # b2: 113.0 -> 114.5 (峰頂，High=114.8)
    strat.on_bar_close(Bar(t + 120, 113.0, 114.8, 112.9, 114.5, 60.0))
    # b3: 確認棒，收陰黑棒 114.5 -> 113.2 (跌破 b2 實體中點 113.75)，但此時遠高於 KC 中軌
    exits = strat.on_bar_close(Bar(t + 180, 114.5, 114.6, 113.0, 113.2, 50.0))

    peak_exits = [e for e in exits if "true_pivot_peak_exit" in e.reason]
    assert len(peak_exits) == 1
    assert peak_exits[0].side == "long"
    assert peak_exits[0].price == 113.2
    assert peak_exits[0].price > kc_mid  # 遠在中軌之上，鎖住頂部利潤！
    # 持倉已被標記清空，防重複發布
    assert strat.position_entry_price["long"] is None


def test_true_pivot_valley_immediate_exit_without_kc_middle():
    """
    驗證空單「真谷底確立立即平倉」：
    持倉急跌獲利後，底部三點樞紐確立 (b2 Low探底，b3 確認收陽突破實體中點)，
    立即發出 true_pivot_valley_exit 平倉，完全不必等到漲回 KC 中軌！
    """
    cfg = StrategyConfig(min_bars_for_indicators=20, require_ma15_direction=False, emergency_single_bar_move_atr=5.0)
    strat = KCTrendStrategy(cfg)
    now = time.time() - 3600

    # 1. 建立 25 根基準下跌 K 棒
    for i in range(25):
        base = 120.0 - i * 0.5
        strat.on_bar_close(Bar(now + i * 60, base + 0.2, base + 0.3, base - 0.4, base - 0.2, 50.0))

    snap = strat._indicator_snapshot()
    strat.notify_position_opened("short", avg_price=snap["close"])
    kc_mid = snap["kc_mid"]

    # 2. 構造底部三點樞紐 (b1, b2, b3)
    t = now + 25 * 60
    # b1: 107.8 -> 107.0
    strat.on_bar_close(Bar(t + 60, 107.8, 108.0, 106.8, 107.0, 50.0))
    # b2: 107.0 -> 105.5 (谷底，Low=105.2)
    strat.on_bar_close(Bar(t + 120, 107.0, 107.1, 105.2, 105.5, 60.0))
    # b3: 確認棒，收陽紅棒 105.5 -> 106.8 (突破 b2 實體中點 106.25)，但此時遠低於 KC 中軌
    exits = strat.on_bar_close(Bar(t + 180, 105.5, 107.0, 105.4, 106.8, 50.0))

    valley_exits = [e for e in exits if "true_pivot_valley_exit" in e.reason]
    assert len(valley_exits) == 1
    assert valley_exits[0].side == "short"
    assert valley_exits[0].price == 106.8
    assert valley_exits[0].price < kc_mid  # 遠在中軌之下，鎖住底部利潤！
    # 持倉已被標記清空，防重複發布
    assert strat.position_entry_price["short"] is None


def test_normal_pullback_holds_position():
    """
    驗證「正常微幅回調繼續持有」：
    上漲途中出現單根小陰棒或短暫回調，未形成真峰頂反轉結構，系統維持續抱，絕不提前被洗出場！
    """
    cfg = StrategyConfig(min_bars_for_indicators=20, require_ma15_direction=False, emergency_single_bar_move_atr=5.0)
    strat = KCTrendStrategy(cfg)
    now = time.time() - 3600

    for i in range(25):
        base = 100.0 + i * 0.5
        strat.on_bar_close(Bar(now + i * 60, base - 0.2, base + 0.4, base - 0.3, base + 0.2, 50.0))

    strat.notify_position_opened("long", avg_price=112.0)
    t = now + 25 * 60

    # 連續兩根正常推進
    strat.on_bar_close(Bar(t + 60, 112.5, 113.2, 112.3, 113.0, 50.0))
    strat.on_bar_close(Bar(t + 120, 113.0, 114.0, 112.8, 113.8, 50.0))

    # 出現一根小陰線微幅回檔 (113.8 -> 113.5，High 創新高達 114.2，未形成被包夾三點樞紐)
    exits = strat.on_bar_close(Bar(t + 180, 113.8, 114.2, 113.3, 113.5, 30.0))

    # 沒有真峰頂，不發出任何出場訊號，多單穩穩續抱
    assert len(exits) == 0
    assert strat.position_entry_price["long"] == 112.0


def test_fallback_to_kc_middle_when_no_pivot():
    """
    驗證「真峰谷未確認時，保留原有 KC 中軌出場防線」：
    若行情橫盤或緩慢滑落未觸發真峰頂，但實體收盤跌破 KC 中軌，依然由 KC 中軌防線執行破位出場。
    """
    cfg = StrategyConfig(
        min_bars_for_indicators=20,
        require_ma15_direction=False,
        enable_true_pivot_exit=False,
        emergency_single_bar_move_atr=5.0
    )
    strat = KCTrendStrategy(cfg)
    now = time.time() - 3600

    for i in range(25):
        base = 100.0 + i * 0.2
        strat.on_bar_close(Bar(now + i * 60, base - 0.1, base + 0.2, base - 0.2, base + 0.1, 50.0))

    snap = strat._indicator_snapshot()
    strat.notify_position_opened("long", avg_price=snap["close"])
    kc_mid = snap["kc_mid"]

    t = now + 25 * 60
    # 價格連續跌破 KC 中軌
    strat.on_bar_close(Bar(t + 60, 104.9, 105.0, 103.5, 103.8, 50.0))
    exits = strat.on_bar_close(Bar(t + 120, 103.8, 103.9, 101.8, 102.0, 50.0))
    mid_exits = [e for e in exits if e.reason == "kc_middle_band_trend_invalidation"]
    assert len(mid_exits) == 1
    assert mid_exits[0].side == "long"

