import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.services.exits.authorized_position_exit import (
    automated_position_exit_allowed,
)
from core.services.exits.strict_true_pivot import store_confirmed_pivot_exit


def position(side="LONG"):
    return {
        "side": side,
        "open_timestamp": 60.0,
        "entry_price": 100.0,
        "qty": 1.0,
    }


def test_blocks_ordinary_and_loss_exits_for_lobster_and_cap():
    active_position = position()
    for symbol in ("龙虾/USDT", "CAP/USDT"):
        for reason in (
            "觸發止損 (Stop-Loss)",
            "急速逆向閃崩觸發平倉",
            "Channel Swing WATERFALL_DROP",
            "EXIT_BY_SAME_BAR_PROFIT",
            "TAKE_PROFIT_50PCT",
            "碰觸 KC 上軌平倉",
            "MA5_PULLBACK_EXIT",
        ):
            assert not automated_position_exit_allowed(
                symbol, active_position, {}, reason, 99.0,
            )


def test_allows_positive_net_profit_only_with_armed_profit_lock():
    active_position = position()
    metadata = {"profit_lock_usdt_armed": True}

    assert automated_position_exit_allowed(
        "CAP/USDT", active_position, metadata,
        "NET_ROE_THREE_TIER_GIVEBACK", 102.0,
    )
    assert not automated_position_exit_allowed(
        "CAP/USDT", active_position, metadata,
        "NET_ROE_THREE_TIER_GIVEBACK", 99.0,
    )
    assert not automated_position_exit_allowed(
        "CAP/USDT", active_position, {},
        "NET_ROE_THREE_TIER_GIVEBACK", 102.0,
    )


def test_allows_wild_swing_trailing_exit_after_strategy_arms_lock():
    active_position = {
        **position("SHORT"),
        "entry_mode": "WILD_SWING",
        "profit_floor_armed": True,
    }

    assert automated_position_exit_allowed(
        "LOBSTER/USDT", active_position, {},
        "Wild Swing EXIT_BY_TRAILING_PROFIT_LOCK", 99.0,
    )


@pytest.mark.parametrize("entry_mode", ["WILD_SWING", "CHANNEL_SWING"])
def test_short_true_valley_authority_accepts_only_the_two_confirmed_causes(entry_mode):
    from core.services.exits.kc_true_extreme_exit import STATE_KEY

    active_position = {
        **position("SHORT"),
        "entry_mode": entry_mode,
        STATE_KEY: {
            "version": 1,
            "identity": ["SHORT", 60.0, 100.0, 1.0],
            "armed": True,
            "pending": {
                "reason": "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL",
                "details": {"cause": "THREE_POINT_CONFIRMED_PIVOT"},
            },
        },
    }
    assert automated_position_exit_allowed(
        "LOBSTER/USDT", active_position, {},
        "Wild Swing EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL", 99.0,
    )

    for cause in (
        "SHORT_LOWER_WICK", "BULLISH_ENGULFING",
        "KC_MIDLINE_TOUCH", "MA5_PULLBACK_EXIT",
    ):
        active_position[STATE_KEY]["pending"]["details"]["cause"] = cause
        assert not automated_position_exit_allowed(
            "LOBSTER/USDT", active_position, {},
            "Wild Swing EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL", 99.0,
        )


@pytest.mark.parametrize("reason", [
    "Wild Swing EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL",
    "EXIT_BY_KC_MIDLINE_RETRACE",
    "EXIT_SHORT_ON_KC_UPPER_BREACH",
    "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT",
    "EXIT_BY_REVERSE_CANDLE",
    "REALTIME_SHORT_PULLBACK_EXIT",
    "Channel Swing CHANNEL_SHORT_OVERSOLD_RATCHET_EXIT",
    "DualTF EXIT_BY_REVERSAL_CANDLE",
])
def test_short_exit_boundary_rejects_non_lock_reasons_without_valid_valley_ticket(reason):
    active_position = {**position("SHORT"), "entry_mode": "WILD_SWING"}
    assert not automated_position_exit_allowed(
        "LOBSTER/USDT", active_position, {}, reason, 99.0,
        is_manual=True,
    )


def test_short_breakeven_exit_requires_armed_lock_and_positive_net_pnl():
    active_position = {
        **position("SHORT"),
        "entry_mode": "WILD_SWING",
        "breakeven_locked": True,
    }
    assert automated_position_exit_allowed(
        "LOBSTER/USDT", active_position, {},
        "Wild Swing EXIT_BY_BREAKEVEN", 99.0,
    )
    assert not automated_position_exit_allowed(
        "LOBSTER/USDT", active_position, {},
        "Wild Swing EXIT_BY_BREAKEVEN", 101.0,
    )
    active_position["breakeven_locked"] = False
    assert not automated_position_exit_allowed(
        "LOBSTER/USDT", active_position, {},
        "Wild Swing EXIT_BY_BREAKEVEN", 99.0,
    )


@pytest.mark.parametrize("symbol", ["LOBSTER/USDT", "CAP/USDT"])
@pytest.mark.parametrize(
    "reason",
    ["Wild Swing EXIT_BY_HARD_STOP_LOSS", "CIRCUIT_BREAKER_HARD_SL"],
)
def test_allows_safety_exit_for_restricted_symbols(symbol, reason):
    active_position = {
        **position("SHORT"),
        "entry_mode": "WILD_SWING",
    }

    assert automated_position_exit_allowed(
        symbol, active_position, {}, reason, 101.0,
    )


@pytest.mark.skip(reason="Deprecated ticket format; unified pipeline ExitGate owns pivot validation")
def test_retired_confirmed_pivot_ticket_no_longer_authorizes_exit():
    active_position = position()
    assert store_confirmed_pivot_exit(
        active_position,
        (
            "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT",
            {"pivot_bar_id": 120_000, "confirm_bar_id": 180_000},
        ),
    )

    assert not automated_position_exit_allowed(
        "龙虾/USDT", active_position, {},
        "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT", 100.0,
    )
    changed_position = {**active_position, "qty": 2.0}
    assert not automated_position_exit_allowed(
        "龙虾/USDT", changed_position, {},
        "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT", 100.0,
    )


def test_manual_close_is_explicit_and_other_symbols_keep_existing_policy():
    assert automated_position_exit_allowed(
        "CAP/USDT", position(), {}, "手動平倉", 99.0, is_manual=True,
    )
    assert not automated_position_exit_allowed(
        "CAP/USDT", position(), {}, "人工例外", 99.0, is_manual=True,
    )
    assert automated_position_exit_allowed(
        "DOGE/USDT", position(), {}, "觸發止損", 99.0,
    )


@pytest.mark.parametrize("account_module,class_name", [
    ("core.paper_account", "PaperAccount"),
    ("core.testnet_account", "BinanceTestnetAccount"),
])
def test_account_close_boundary_blocks_unauthorized_exit(account_module, class_name):
    import importlib

    account_type = getattr(importlib.import_module(account_module), class_name)
    account = object.__new__(account_type)
    account.positions = {"CAP/USDT": position()}
    account.position_meta = {}
    account.closing_lock = set()
    account.log = Mock()
    account.pending_limit_orders = {}
    account.last_closed_at = {}
    account.last_closed_side = {}
    account.last_closed_price = {}
    account._close_retry_after = {}
    account._auto_close_reject_logged_at = {}

    assert not asyncio.run(account.close_position(
        "CAP/USDT",
        99.0,
        "UNAUTHORIZED_PULLBACK_EXIT",
    ))
    account.log.assert_called_once()


@pytest.mark.parametrize(
    "exchange_symbol,internal_symbol",
    [("CAPUSDT", "CAP/USDT"), ("LOBSTERUSDT", "LOBSTER/USDT")],
)
def test_account_circuit_breaker_does_not_software_close_restricted_symbols(
    exchange_symbol, internal_symbol,
):
    from core.testnet_account import BinanceTestnetAccount

    account = object.__new__(BinanceTestnetAccount)
    account._circuit_breaker_running = False
    account.exchange = SimpleNamespace(
        fapiPrivateV2GetPositionRisk=AsyncMock(return_value=[{
            "symbol": exchange_symbol,
            "positionAmt": "1",
        }]),
        fetch_open_orders=AsyncMock(return_value=[]),
    )
    account._raw_create_order = AsyncMock()
    account.log = Mock()
    account.save_state = Mock()

    report = asyncio.run(account._run_circuit_breaker_flatten())

    assert report["close_attempts"] == [{
        "symbol": internal_symbol,
        "qty": "1",
        "ok": False,
        "blocked": "SOFTWARE_EXIT_POLICY_PROFIT_LOCK_OR_CONFIRMED_PIVOT",
    }]
    account._raw_create_order.assert_not_awaited()


def test_sequential_time_series_pullback_vs_true_peak_exit():
    """
    [第二階段：時序功能/整合測試 —— 驗證狀態機防抖與逐根推進]
    模擬開多後的時序推進 (Bar-by-Bar Sequential Playback)：
    開多點: entry_price = 100.0, 隨後價格上衝至 110.0。
    1. Step 1 (第一根小黑 K 回踩):
       價格從 110.0 拉回到 108.0 (小陰棒)，但守在均線與中軌 (104.0) 之上。
       驗證：should_exit is False，系統輸出 VETO_HOLD，維持續抱。
    2. Step 2 (第二根小黑 K 縮量回踩):
       價格在 107.0 震盪，未跌破前低，均線仍向上，未形成三點頂部確認。
       驗證：should_exit is False，系統輸出 VETO_HOLD，維持續抱。
    3. Step 3 (第三根長黑摜破回踩低點收線):
       價格收於 104.0 (跌破中間棒實體中點且確認前棒為頂部樞紐)。
       三點頂部確定性樞紐確立！
       驗證：收線瞬間系統輸出 should_exit is True，觸發 TRUE_PEAK 授權平倉！
    """
    import pandas as pd
    from core.gates.unified_pipeline_gates import ExitGate

    pos = {
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "peak_roe": 0.08,
    }

    # 基礎上升趨勢歷史數據
    history = [
        {"open": 98.0 + i * 0.5, "high": 98.5 + i * 0.5, "low": 97.8 + i * 0.5, "close": 98.3 + i * 0.5}
        for i in range(25)
    ]
    # 第 25 根：多頭大陽線衝高至 110.0
    bar_peak = {"open": 105.0, "high": 110.0, "low": 104.8, "close": 109.5}

    # ── Step 1: 第一根小黑 K 回踩 (109.5 -> 108.2，守在中軌 104 之上) ──
    bar_step1 = {"open": 109.0, "high": 109.2, "low": 107.5, "close": 108.2}
    df_step1 = pd.DataFrame([*history, bar_peak, bar_step1])
    m_data_step1 = {
        "current_price": 108.2,
        "atr": 2.0,
        "kc_middle": 104.0,
        "kc_middle_slope": 0.2,
        "ema_slow": 104.0,
        "raw_df": df_step1,
    }
    should_exit1, reason1, _ = ExitGate.evaluate(pos, m_data_step1, current_price=108.2)
    assert should_exit1 is False, "第一根小黑K回踩時必須拒絕平倉，維持續抱"
    assert "VETO_HOLD" in reason1

    # ── Step 2: 第二根小黑 K 縮量回踩 (高點 108.5，收 107.8，未破前低 107.5) ──
    bar_step2 = {"open": 108.2, "high": 108.5, "low": 107.6, "close": 107.8}
    df_step2 = pd.DataFrame([*history, bar_peak, bar_step1, bar_step2])
    m_data_step2 = {
        "current_price": 107.8,
        "atr": 2.0,
        "kc_middle": 104.2,
        "kc_middle_slope": 0.2,
        "ema_slow": 104.0,
        "raw_df": df_step2,
    }
    should_exit2, reason2, _ = ExitGate.evaluate(pos, m_data_step2, current_price=107.8)
    assert should_exit2 is False, "第二根小黑K縮量回踩且未破前低時必須拒絕平倉，維持續抱"
    assert "VETO_HOLD" in reason2

    # ── Step 3: 第三根長黑摜破回踩低點收線 (高點 108.0，長黑實體貫穿至 104.0) ──
    bar_step3 = {"open": 107.8, "high": 108.0, "low": 103.5, "close": 104.0}
    df_step3 = pd.DataFrame([*history, bar_peak, bar_step1, bar_step2, bar_step3])
    m_data_step3 = {
        "current_price": 104.0,
        "atr": 2.0,
        "kc_middle": 104.0,
        "kc_middle_slope": 0.1,
        "ema_slow": 104.0,
        "raw_df": df_step3,
    }
    should_exit3, reason3, meta3 = ExitGate.evaluate(pos, m_data_step3, current_price=104.0)
    assert should_exit3 is True, "第三根長黑破位、確立三點真峰頂樞紐時，必須授權平倉！"
    assert reason3 in ("TRUE_PEAK", "EARLY_PIVOT_PEAK_EXIT")
    assert meta3.get("cause") is not None


def test_bearish_trend_short_pullback_hold_integration_test():
    """
    [空頭主浪回踩防洗時序整合測試]
    防範風險：大趨勢走空 (KC中軌向下) 時，盤中常有 1~2 根短暫紅 K 反抽洗盤。
    若在此時提前平空甚至反手開多，會立即被後續跌浪洗爆。
    驗證：
    1. 空單持倉 (entry_price=0.0700)。
    2. KC 中軌斜率向下 (kc_middle_slope = -0.0003 < 0)。
    3. 盤中出現連續 2 根小紅 K 反抽均線 (價格從 0.0665 反彈至 0.0678，仍在 KC 上軌之下)。
    4. ExitGate.evaluate 必須嚴格輸出 should_exit is False，reason 為 VETO_HOLD，
       堅定維持續抱，嚴禁平空！
    """
    import pandas as pd
    from core.gates.unified_pipeline_gates import ExitGate

    pos_short = {
        "side": "SHORT",
        "entry_price": 0.0700,
        "qty": 1000.0,
        "peak_roe": 0.05,
    }

    # 模擬 25 根連續下跌的空頭主浪 (KC 向下)
    history = [
        {
            "open": 0.0700 - i * 0.0002,
            "high": 0.0701 - i * 0.0002,
            "low": 0.0697 - i * 0.0002,
            "close": 0.0698 - i * 0.0002,
        }
        for i in range(25)
    ]
    # 反彈第 1 根紅 K (0.0650 -> 0.0665)
    bounce_bar1 = {"open": 0.0650, "high": 0.0668, "low": 0.0649, "close": 0.0665}
    # 反彈第 2 根紅 K (0.0665 -> 0.0678，觸及均線但受壓在 KC 上軌 0.0690 之下)
    bounce_bar2 = {"open": 0.0665, "high": 0.0680, "low": 0.0663, "close": 0.0678}

    df_bounce = pd.DataFrame([*history, bounce_bar1, bounce_bar2])
    market_data = {
        "current_price": 0.0678,
        "atr": 0.0005,
        "kc_upper": 0.0690,
        "kc_middle": 0.0685,  # 中軌在 0.0685，反抽價 0.0678 受壓在中軌之下
        "kc_lower": 0.0655,
        "kc_middle_slope": -0.0003,  # KC 中軌明確向下 (空頭主格局)
        "ema_slow": 0.0688,
        "raw_df": df_bounce,
    }

    should_exit, reason, meta = ExitGate.evaluate(pos_short, market_data, current_price=0.0678)
    assert should_exit is False, "KC 向下空頭大格局中，短線小紅K反彈受壓於中軌下，必須嚴禁平空，強制續抱！"
    assert "VETO_HOLD" in reason
    assert "kc_middle_slope" in meta.get("cause", "") or "依然向下" in meta.get("cause", "")


def test_exit_short_on_kc_mid_band_crossover():
    """
    [空單大週期順勢死抱防反覆平倉測試]
    驗證空單持倉時，若大週期趨勢依然向下 (ema_fast <= ema_slow 且中軌向下)，
    即使最新 K 棒收盤價實體微幅站上或回測 KC 中軌 (close > kc_middle)，
    只要未出現真谷底力竭或均線金叉反轉，中途嚴禁平空！
    ExitGate.evaluate 必須回傳 should_exit == False, reason == "VETO_HOLD"，
    確保空單抱牢主跌浪，徹底消除反覆平倉開倉的震盪損耗！
    """
    import pandas as pd
    from core.gates.unified_pipeline_gates import ExitGate

    pos_short = {
        "side": "SHORT",
        "entry_price": 100.0,
        "qty": 1.0,
        "peak_roe": 0.04,
    }
    bars = [
        {"open": 98.0, "high": 98.5, "low": 97.0, "close": 97.2, "ma15": 98.5},
        {"open": 97.2, "high": 98.0, "low": 97.1, "close": 97.8, "ma15": 98.2},
        {"open": 97.8, "high": 99.2, "low": 97.7, "close": 99.0, "ma15": 98.1},  # 實體微幅站上中軌 (99.0 > 98.5)
    ]
    df = pd.DataFrame(bars)
    market_data = {
        "current_price": 99.0,
        "atr": 1.5,
        "kc_upper": 102.0,
        "kc_middle": 98.5,
        "kc_lower": 95.0,
        "kc_middle_slope": -0.05,  # 中軌斜率依然向下
        "ema_fast": 98.1,
        "ema_slow": 99.5,          # 大週期均線空頭排列
        "raw_df": df,
    }

    should_exit, reason, meta = ExitGate.evaluate(pos_short, market_data, current_price=99.0)
    assert should_exit is False, "大週期空頭未變且無真谷底衰退時，實體觸及中軌屬於健康反抽，必須死抱嚴禁平空！"
    assert reason == "VETO_HOLD"
    assert "依然向下" in meta.get("cause", "") or "維持續抱" in meta.get("cause", "")


def test_exit_short_immediately_on_ema_golden_cross():
    """
    [EMA15 金叉 EMA40 即刻平空單元測試]
    驗證空單持倉時，只要紅色快線 EMA15 向上金叉慢線 EMA40 (ema_fast > ema_slow)，
    ExitGate.evaluate 立即回傳 should_exit == True，
    平倉原因標記為 SHORT_EXIT_ON_EMA_CROSS，不再死等觸碰上軌或極端形態！
    """
    import pandas as pd
    from core.gates.unified_pipeline_gates import ExitGate

    pos_short = {
        "side": "SHORT",
        "entry_price": 100.0,
        "qty": 1.0,
        "peak_roe": 0.05,
    }
    bars = [
        {"open": 97.0, "high": 97.5, "low": 96.8, "close": 97.2, "ma15": 97.0},
        {"open": 97.2, "high": 98.0, "low": 97.1, "close": 97.8, "ma15": 97.5},
        {"open": 97.8, "high": 98.5, "low": 97.6, "close": 98.2, "ma15": 98.2},
    ]
    df = pd.DataFrame(bars)
    market_data = {
        "current_price": 98.2,
        "atr": 1.5,
        "kc_upper": 102.0,
        "kc_middle": 98.5,
        "kc_lower": 95.0,
        "kc_middle_slope": -0.01,
        "ema_fast": 98.2,  # 快線金叉慢線 (98.2 > 98.0)
        "ema_slow": 98.0,
        "raw_df": df,
    }

    should_exit, reason, meta = ExitGate.evaluate(pos_short, market_data, current_price=98.2)
    assert should_exit is True, "EMA15 金叉 EMA40 時波段反轉，必須立即授權平空！"
    assert reason == "SHORT_EXIT_ON_EMA_CROSS"
    assert "EMA15向上金叉EMA40" in meta.get("cause", "")


def test_exit_short_on_early_valley_before_reaching_mid_band():
    """
    [提前平倉避開中軌依賴的閘門測試]
    驗證空單盈利中，價格打出 3 棒確認谷底樞紐：
    雖然當前收盤價仍在 KC 中軌與下軌之間 (97.5 < kc_middle 99.0)，離中軌還有一大段距離，
    ExitGate.evaluate 必須立即回傳 should_exit == True，
    原因為 EARLY_PIVOT_VALLEY_EXIT，絕不被 VETO_HOLD 或中軌依賴阻斷！
    """
    import pandas as pd
    from core.gates.unified_pipeline_gates import ExitGate

    pos_short = {
        "side": "SHORT",
        "entry_price": 100.0,
        "qty": 1.0,
        "peak_roe": 0.06,
    }
    bars = [
        {"open": 98.5, "high": 98.8, "low": 97.5, "close": 97.8},  # 破底
        {"open": 97.8, "high": 98.0, "low": 96.0, "close": 96.6},  # 最低點 96.0
        {"open": 96.6, "high": 97.8, "low": 96.5, "close": 97.5},  # 衝過中點 (97.5 > (97.8+96.6)/2=97.2)
    ]
    df = pd.DataFrame(bars)
    market_data = {
        "current_price": 97.5,
        "atr": 1.5,
        "kc_upper": 102.5,
        "kc_middle": 99.0,   # 中軌在 99.0，離當前價 97.5 還很遠！
        "kc_lower": 95.5,
        "kc_middle_slope": -0.05,  # 空頭趨勢中軌依然向下
        "ema_fast": 97.8,
        "ema_slow": 99.2,
        "raw_df": df,
    }

    should_exit, reason, meta = ExitGate.evaluate(pos_short, market_data, current_price=97.5)
    assert should_exit is True, "確立早期谷底形態時，必須直接平空落袋，不必等價格摸到中軌！"
    assert reason == "EARLY_PIVOT_VALLEY_EXIT"
    assert "早期真谷底樞紐確立" in meta.get("cause", "")


class TestDualSidePullbackHold:
    """多空雙向健康回踩防洗時序整合測試"""

    def test_long_position_must_hold_during_healthy_pullback(self):
        """[多單回踩防洗] 上升主浪中回踩 EMA15/中軌上方，必須全程 VETO_HOLD 續抱"""
        import numpy as np
        import pandas as pd
        from core.services.data_pipeline import DataPipeline
        from core.gates.unified_pipeline_gates import ExitGate

        n_bars = 35
        dates = pd.date_range("2026-10-21 00:00", periods=n_bars, freq="1min")
        closes = np.linspace(100.0, 106.0, n_bars)
        opens = closes - 0.2
        highs = closes + 0.3
        lows = opens - 0.1

        df = pd.DataFrame({
            "timestamp": [int(d.timestamp() * 1000) for d in dates],
            "open": opens, "high": highs, "low": lows, "close": closes,
            "volume": np.ones(n_bars) * 1000.0
        })

        pos = {"symbol": "TEST/USDT", "side": "LONG", "entry_price": 100.0, "peak_roe": 0.06, "qty": 10.0}

        # 構造 2 根小陰線健康回踩
        pullback_bars = [
            (106.0, 106.2, 104.8, 105.1),
            (105.1, 105.4, 104.6, 104.9),
        ]

        curr_df = df.copy()
        for i, (op, hi, lo, cl) in enumerate(pullback_bars):
            next_ts = curr_df["timestamp"].iloc[-1] + 60000
            new_bar = pd.DataFrame([{"timestamp": next_ts, "open": op, "high": hi, "low": lo, "close": cl, "volume": 800.0}])
            curr_df = pd.concat([curr_df, new_bar], ignore_index=True)

            snapshot = DataPipeline.calculate_snapshot("TEST/USDT", curr_df, current_price=cl)
            should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=cl)

            assert should_exit is False, f"多單第 {i+1} 根回踩棒誤觸平倉！原因: {reason}, Meta: {meta}"
            assert "HOLD" in reason, f"多單預期維持續抱，實際原因: {reason}"

    def test_short_position_must_hold_during_healthy_pullback(self):
        """[空單反抽防洗] 下跌主浪中小陽線反抽測試均線，必須全程 VETO_HOLD 續抱"""
        import numpy as np
        import pandas as pd
        from core.services.data_pipeline import DataPipeline
        from core.gates.unified_pipeline_gates import ExitGate

        n_bars = 35
        dates = pd.date_range("2026-10-21 00:00", periods=n_bars, freq="1min")
        closes = np.linspace(100.0, 94.0, n_bars)
        opens = closes + 0.2
        highs = opens + 0.1
        lows = closes - 0.3

        df = pd.DataFrame({
            "timestamp": [int(d.timestamp() * 1000) for d in dates],
            "open": opens, "high": highs, "low": lows, "close": closes,
            "volume": np.ones(n_bars) * 1000.0
        })

        pos = {"symbol": "TEST/USDT", "side": "SHORT", "entry_price": 100.0, "peak_roe": 0.06, "qty": 10.0}

        # 構造 2 根小陽線健康反抽
        pullback_bars = [
            (94.0, 95.2, 93.8, 95.0),
            (95.0, 95.4, 94.6, 95.1),
        ]

        curr_df = df.copy()
        for i, (op, hi, lo, cl) in enumerate(pullback_bars):
            next_ts = curr_df["timestamp"].iloc[-1] + 60000
            new_bar = pd.DataFrame([{"timestamp": next_ts, "open": op, "high": hi, "low": lo, "close": cl, "volume": 800.0}])
            curr_df = pd.concat([curr_df, new_bar], ignore_index=True)

            snapshot = DataPipeline.calculate_snapshot("TEST/USDT", curr_df, current_price=cl)
            should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, current_price=cl)

            assert should_exit is False, f"空單第 {i+1} 根反抽棒誤觸平倉！原因: {reason}, Meta: {meta}"
            assert "HOLD" in reason, f"空單預期維持續抱，實際原因: {reason}"

    def test_lobster_short_position_must_hold_during_mid_band_pullback(self):
        """
        [龍蝦/妖幣賣空回踩防洗測試]
        情境重現：龍蝦 (龙虾/USDT) 開空後，盤中出現小陽線反抽測試 KC 中軌或微幅破中軌。
        此時大趨勢 KC 中軌向下 (slope <= 0) 且 EMA15 < EMA40，未達 3% 獲利保護線 (peak_roe < 0.03)，
        且未連續兩根收破 EMA40 慢線。
        ExitGate.evaluate 必須嚴禁平空 (should_exit == False)，回傳 VETO_HOLD，
        徹底解決「龍蝦賣空一直在回踩就平倉」的實盤痛點！
        """
        import pandas as pd
        from core.gates.unified_pipeline_gates import ExitGate

        pos_lobster = {
            "symbol": "龙虾/USDT",
            "side": "SHORT",
            "entry_price": 0.02165,
            "qty": 3000.0,
            "peak_roe": 0.005,  # 剛開倉微浮盈，未達 3% 趨勢利潤保護
        }

        # 構造 1m K 棒歷史與反抽棒
        bars = [
            {"open": 0.02180, "high": 0.02182, "low": 0.02170, "close": 0.02172},
            {"open": 0.02172, "high": 0.02174, "low": 0.02162, "close": 0.02164},
            {"open": 0.02164, "high": 0.02169, "low": 0.02163, "close": 0.02167},  # 回踩微幅穿過中軌 (0.02167 > 0.02165)
        ]
        df = pd.DataFrame(bars)

        market_data = {
            "symbol": "龙虾/USDT",
            "current_price": 0.02167,
            "atr": 0.00015,
            "kc_upper": 0.02185,
            "kc_middle": 0.02165,  # KC 中軌
            "kc_lower": 0.02145,
            "kc_middle_slope": -0.00002,  # 大趨勢依然向下
            "ema_fast": 0.02166,
            "ema_slow": 0.02175,  # 均線依然空頭排列 (ema_fast < ema_slow)
            "raw_df": df,
        }

        should_exit, reason, meta = ExitGate.evaluate(pos_lobster, market_data, current_price=0.02167)
        assert should_exit is False, f"龍蝦賣空在回踩中軌時誤觸平倉！原因: {reason}, Meta: {meta}"
        assert "HOLD" in reason, f"預期維持 VETO_HOLD 續抱，實際原因: {reason}"
        assert "健康反抽" in meta.get("cause", "") or "依然向下" in meta.get("cause", "") or "慢線" in meta.get("cause", "")

    def test_macro_trend_prevents_rapid_churn_between_long_and_short(self):
        """
        [大週期順勢鐵律測試：嚴防一下子開多一下子開空 (Anti-Churn Test)]
        1. 當市場大週期為空頭 (slope < 0 且 ema_fast < ema_slow)：
           哪怕盤中出現大陽線甚至衝破 KC 上軌，EntryGate 必須 100% 阻斷開多 (BLOCKED_LONG_ON_BEARISH_TREND)。
        2. 當市場大週期為多頭 (slope > 0 且 ema_fast > ema_slow)：
           哪怕盤中出現大陰線甚至跌破 KC 下軌，EntryGate 必須 100% 阻斷開空 (BLOCKED_SHORT_ON_BULLISH_TREND)。
        """
        import pandas as pd
        from core.gates.unified_pipeline_gates import EntryGate

        # 情境 1：大週期空頭中，短線反彈衝高，嚴禁開多
        bearish_snapshot = {
            "symbol": "CAP/USDT",
            "current_price": 0.06800,
            "atr": 0.0003,
            "kc_upper": 0.06790,  # 價格高於上軌
            "kc_middle": 0.06740,
            "kc_lower": 0.06690,
            "kc_middle_slope": -0.00005,  # 大週期空頭
            "ema_fast": 0.06730,
            "ema_slow": 0.06770,          # 空頭排列 (ema_fast < ema_slow)
            "ma_spread_pct": 0.06,
            "curr_k": {"open": 0.06750, "high": 0.06810, "low": 0.06740, "close": 0.06800},
            "prev_k": {"open": 0.06730, "high": 0.06760, "low": 0.06720, "close": 0.06750},
        }
        allowed, reason, meta = EntryGate.evaluate("BREAKOUT_LONG", bearish_snapshot)
        assert allowed is False, "大週期為空頭時，嚴禁開多接飛刀！"
        assert reason == "BLOCKED_LONG_ON_BEARISH_TREND"

        # 情境 2：大週期多頭中，短線急殺破底，嚴禁開空
        bullish_snapshot = {
            "symbol": "CAP/USDT",
            "current_price": 0.06650,
            "atr": 0.0003,
            "kc_upper": 0.06800,
            "kc_middle": 0.06740,
            "kc_lower": 0.06680,  # 價格跌破下軌
            "kc_middle_slope": 0.00005,  # 大週期多頭
            "ema_fast": 0.06770,
            "ema_slow": 0.06730,         # 多頭排列 (ema_fast > ema_slow)
            "ma_spread_pct": 0.06,
            "curr_k": {"open": 0.06700, "high": 0.06710, "low": 0.06640, "close": 0.06650},
            "prev_k": {"open": 0.06740, "high": 0.06750, "low": 0.06690, "close": 0.06700},
        }
        allowed, reason, meta = EntryGate.evaluate("BREAKDOWN_SHORT", bullish_snapshot)
        assert allowed is False, "大週期為多頭時，嚴禁摸頂開空被軋！"
        assert reason == "BLOCKED_SHORT_ON_BULLISH_TREND"

    def test_position_must_hold_while_macro_trend_intact_until_exhaustion(self):
        """
        [趨勢不變死抱、衰退才平倉測試 (Trend Invariant Holding & Exhaustion Exit)]
        驗證：
        1. 多單持倉時，只要大週期多頭未變 (ema_fast >= ema_slow 且 slope >= 0)，
           盤中小陰線回測中軌/均線，全程 VETO_HOLD 死抱，不被洗下車！
        2. 當且僅當出現「確定性三點真峰頂包夾力竭」時，ExitGate 立即授權平多落袋！
        3. 空單持倉時，只要大週期空頭未變 (ema_fast <= ema_slow 且 slope <= 0)，
           盤中小陽線反抽中軌/均線，全程 VETO_HOLD 死抱，不被洗下車！
        4. 當且僅當出現「確定性三點真谷底包夾力竭」時，ExitGate 立即授權平空落袋！
        """
        import pandas as pd
        from core.gates.unified_pipeline_gates import ExitGate

        # ── A. 多單持倉測試 ──
        pos_long = {"side": "LONG", "entry_price": 100.0, "qty": 1.0, "peak_roe": 0.04}
        # 回調小陰線 (未形成真峰頂)
        pullback_bars_long = [
            {"open": 102.0, "high": 102.5, "low": 101.8, "close": 102.3},
            {"open": 102.3, "high": 102.6, "low": 101.5, "close": 102.0},
            {"open": 102.0, "high": 102.2, "low": 101.2, "close": 101.5},  # 小陰線回踩中軌
        ]
        df_long_hold = pd.DataFrame(pullback_bars_long)
        market_data_long_hold = {
            "current_price": 101.5,
            "atr": 1.0,
            "kc_upper": 103.5,
            "kc_middle": 101.0,
            "kc_lower": 98.5,
            "kc_middle_slope": 0.05,  # 大週期多頭
            "ema_fast": 102.0,
            "ema_slow": 100.5,        # 多頭排列
            "raw_df": df_long_hold,
        }
        should_exit, reason, meta = ExitGate.evaluate(pos_long, market_data_long_hold, current_price=101.5)
        assert should_exit is False, "多單大週期未變且無真峰頂衰退時，必須死抱嚴禁平多！"
        assert reason == "VETO_HOLD"

        # 隨後出現確定性三點真峰頂力竭 (Peak: high 104.0 > 102.5 and > 103.0, 第三棒收黑跌破中點)
        peak_bars_long = [
            {"open": 102.0, "high": 102.5, "low": 101.8, "close": 102.3},
            {"open": 102.3, "high": 104.0, "low": 102.0, "close": 103.8},  # 頂部峰棒 High=104.0
            {"open": 103.8, "high": 103.8, "low": 102.5, "close": 102.6},  # 確認棒跌破前棒實體中點 ((102.3+103.8)/2 = 103.05)
        ]
        df_long_exit = pd.DataFrame(peak_bars_long)
        market_data_long_exit = dict(market_data_long_hold, raw_df=df_long_exit, current_price=102.6)
        should_exit, reason, meta = ExitGate.evaluate(pos_long, market_data_long_exit, current_price=102.6)
        assert should_exit is True, "確立真峰頂力竭時，必須果斷平多落袋！"
        assert reason == "EARLY_PIVOT_PEAK_EXIT"

        # ── B. 空單持倉測試 ──
        pos_short = {"side": "SHORT", "entry_price": 100.0, "qty": 1.0, "peak_roe": 0.04}
        # 反彈小陽線 (未形成真谷底)
        pullback_bars_short = [
            {"open": 98.0, "high": 98.2, "low": 97.5, "close": 97.7},
            {"open": 97.7, "high": 98.5, "low": 97.4, "close": 98.0},
            {"open": 98.0, "high": 98.8, "low": 97.8, "close": 98.5},  # 小陽線反抽中軌
        ]
        df_short_hold = pd.DataFrame(pullback_bars_short)
        market_data_short_hold = {
            "current_price": 98.5,
            "atr": 1.0,
            "kc_upper": 101.5,
            "kc_middle": 99.0,
            "kc_lower": 96.5,
            "kc_middle_slope": -0.05,  # 大週期空頭
            "ema_fast": 98.0,
            "ema_slow": 99.5,          # 空頭排列
            "raw_df": df_short_hold,
        }
        should_exit, reason, meta = ExitGate.evaluate(pos_short, market_data_short_hold, current_price=98.5)
        assert should_exit is False, "空單大週期未變且無真谷底衰退時，必須死抱嚴禁平空！"
        assert reason == "VETO_HOLD"

        # 隨後出現確定性三點真谷底力竭 (Valley: low 96.0 < 97.5 and < 96.5, 第三棒收紅衝破中點)
        valley_bars_short = [
            {"open": 98.5, "high": 98.8, "low": 97.5, "close": 97.8},
            {"open": 97.8, "high": 98.0, "low": 96.0, "close": 96.6},  # 谷底棒 Low=96.0
            {"open": 96.6, "high": 97.8, "low": 96.5, "close": 97.5},  # 確認棒衝破前棒實體中點 ((97.8+96.6)/2 = 97.2)
        ]
        df_short_exit = pd.DataFrame(valley_bars_short)
        market_data_short_exit = dict(market_data_short_hold, raw_df=df_short_exit, current_price=97.5)
        should_exit, reason, meta = ExitGate.evaluate(pos_short, market_data_short_exit, current_price=97.5)
        assert should_exit is True, "確立真谷底力竭時，必須果斷平空落袋！"
        assert reason == "EARLY_PIVOT_VALLEY_EXIT"

    def test_lobster_long_position_must_hold_at_1517_while_trend_intact(self):
        """
        [實盤 15:17 龍蝦多單回踩防洗回放測試]
        情境重現：
        2026-10-21 15:17 (07:17 UTC)，龍蝦 (龙虾/USDT) 持多單 (entry=0.02187)。
        大週期均線多頭排列 (ema_fast=0.021927 > ema_slow=0.021892)，
        盤中出現微幅回踩毛刺 (quote=0.02188781)，短暫觸及慢線附近。
        此時多頭趨勢根本未結束，未見真峰頂力竭，未出現死叉反轉。
        ExitGate.evaluate 必須嚴禁平多 (should_exit == False)，回傳 VETO_HOLD，
        確保主升浪一單到底，絕不提前割肉被洗下車！
        """
        import pandas as pd
        from core.gates.unified_pipeline_gates import ExitGate

        pos_lobster = {
            "symbol": "龙虾/USDT",
            "side": "LONG",
            "entry_price": 0.02187,
            "qty": 3000.0,
            "peak_roe": 0.015,
        }

        # 構造 15:15~15:17 的實時 K 棒序列
        bars = [
            {"open": 0.02194, "high": 0.02194, "low": 0.02190, "close": 0.02191},
            {"open": 0.02191, "high": 0.02193, "low": 0.02189, "close": 0.02189},
            {"open": 0.02189, "high": 0.02192, "low": 0.02188, "close": 0.02188781},  # 盤中跳動毛刺
        ]
        df = pd.DataFrame(bars)

        market_data = {
            "symbol": "龙虾/USDT",
            "current_price": 0.02188781,
            "atr": 0.00008,
            "kc_upper": 0.021998,
            "kc_middle": 0.021942,
            "kc_lower": 0.021886,
            "kc_middle_slope": -0.000001,  # 短線微回調之斜率微負
            "ema_fast": 0.021927,          # EMA15
            "ema_slow": 0.021892,          # EMA40 -> 多頭排列 (ema_fast > ema_slow)
            "raw_df": df,
        }

        should_exit, reason, meta = ExitGate.evaluate(pos_lobster, market_data, current_price=0.02188781)
        assert should_exit is False, f"龍蝦多單在 15:17 趨勢未結束時被誤平倉！reason: {reason}, meta: {meta}"
        assert reason == "VETO_HOLD"
        assert "依然向上" in meta.get("cause", "") or "維持續抱" in meta.get("cause", "")

    def test_trend_terminated_slow_break_requires_both_ma_cross_and_consecutive_bars(self):
        """
        [趨勢終結判定嚴格性測試]
        驗證：
        1. 只有當大週期均線真正死叉 (ema_fast < ema_slow) 且連續兩根跌破慢線，多單才授權平倉。
        2. 只要均線依然多頭 (ema_fast >= ema_slow)，哪怕兩根收盤低於慢線，也一律 VETO_HOLD！
        """
        import pandas as pd
        from core.gates.unified_pipeline_gates import ExitGate

        pos_long = {"side": "LONG", "entry_price": 100.0, "qty": 1.0, "peak_roe": 0.02}

        bars_below_slow = [
            {"open": 99.8, "high": 99.9, "low": 99.4, "close": 99.5},
            {"open": 99.5, "high": 99.6, "low": 99.1, "close": 99.2},
        ]
        df = pd.DataFrame(bars_below_slow)

        # 情況 A：價格低於慢線 100.0，但 EMA15 (100.2) > EMA40 (100.0)，均線多頭未死叉 -> 嚴禁平多
        bullish_ma_data = {
            "current_price": 99.2,
            "atr": 1.0,
            "kc_upper": 102.0,
            "kc_middle": 100.1,
            "kc_lower": 98.2,
            "kc_middle_slope": -0.01,
            "ema_fast": 100.2,
            "ema_slow": 100.0,  # 多頭排列未破
            "raw_df": df,
        }
        should_exit, reason, meta = ExitGate.evaluate(pos_long, bullish_ma_data, current_price=99.2)
        assert should_exit is False, "均線未死叉前，嚴禁判定 TREND_TERMINATED 平多！"
        assert reason == "VETO_HOLD"

        # 情況 B：EMA15 (99.8) < EMA40 (100.0) 已形成死叉，且連續兩根跌破慢線 -> 授權 TREND_TERMINATED_SLOW_BREAK
        bearish_ma_data = dict(bullish_ma_data, ema_fast=99.8, ema_slow=100.0)
        should_exit, reason, meta = ExitGate.evaluate(pos_long, bearish_ma_data, current_price=99.2)
        assert should_exit is True, "均線已死叉且連續收破慢線，確認趨勢終結離場！"
        assert reason == "TREND_TERMINATED_SLOW_BREAK"

    def test_lobster_short_holds_through_healthy_mid_band_pullbacks_without_churning(self):
        """
        [實盤 16:55 龍蝦空單中途回抽中軌防洗回放測試]
        情境重現：
        2026-10-21 16:52 (08:52 UTC)，龍蝦 (龙虾/USDT) 開空 (entry=0.02182782)。
        16:55:49 價格短暫反抽，收盤在 0.02186219，微幅觸及 KC 中軌 (kc_mid=0.021860)。
        此時大週期均線依然為空頭排列 (ema_fast=0.021820 <= ema_slow=0.021880)，
        既未出現均線金叉反轉，也未出現三點真谷底力竭。
        系統必須嚴格判定為 VETO_HOLD，嚴禁中途平空！
        確保空單抱牢主跌浪，徹底消除「平空 -> 15秒後又開空」之反覆平倉損耗！
        """
        import pandas as pd
        from core.gates.unified_pipeline_gates import ExitGate

        pos_lobster_short = {
            "symbol": "龙虾/USDT",
            "side": "SHORT",
            "entry_price": 0.02182782,
            "qty": 13461.0,
            "peak_roe": 0.02,
        }

        # 構造 16:53~16:55 實時小陽線反抽序列 (中軌附近震盪，但未形成真谷底)
        bars_pullback = [
            {"open": 0.021810, "high": 0.021830, "low": 0.021800, "close": 0.021815, "ma15": 0.021830},
            {"open": 0.021815, "high": 0.021845, "low": 0.021810, "close": 0.021840, "ma15": 0.021825},
            {"open": 0.021840, "high": 0.021865, "low": 0.021835, "close": 0.02186219, "ma15": 0.021820},
        ]
        df_pullback = pd.DataFrame(bars_pullback)

        market_data_hold = {
            "symbol": "龙虾/USDT",
            "current_price": 0.02186219,
            "atr": 0.000049,
            "kc_upper": 0.021950,
            "kc_middle": 0.021860,
            "kc_lower": 0.021770,
            "kc_middle_slope": 0.00001,  # 短線微幅反抽使斜率微正
            "ema_fast": 0.021820,         # EMA15
            "ema_slow": 0.021880,         # EMA40 -> 空頭排列 (ema_fast <= ema_slow)
            "raw_df": df_pullback,
        }

        should_exit, reason, meta = ExitGate.evaluate(pos_lobster_short, market_data_hold, current_price=0.02186219)
        assert should_exit is False, f"空單大週期未變卻在 16:55 被中途平倉！reason={reason}, meta={meta}"
        assert reason == "VETO_HOLD"
        assert "依然向下" in meta.get("cause", "") or "維持續抱" in meta.get("cause", "")

        # 隨後行情真正衰退反轉：EMA15 向上金叉 EMA40 (0.021890 > 0.021880)
        market_data_reversal = dict(market_data_hold, ema_fast=0.021890)
        should_exit, reason, meta = ExitGate.evaluate(pos_lobster_short, market_data_reversal, current_price=0.021890)
        assert should_exit is True, "均線實質金叉趨勢反轉時，必須果斷授權平空！"
        assert reason == "SHORT_EXIT_ON_EMA_CROSS"

    def test_lobster_short_does_not_exit_at_1719_when_consolidation_above_ema15_before_trend_end(self):
        """
        [實盤 17:19 龍蝦空單通道底部橫盤防提前平倉回放測試]
        情境重現：
        2026-10-21 17:15~17:19，龍蝦 (龙虾/USDT) 持有空單 (entry=0.02182782)。
        價格在通道底部小幅橫盤整理，2 根小 K 棒收盤價微幅高於 EMA15，但依然遠低於中軌與 EMA40：
        EMA15=0.021750，EMA40=0.021840 (大週期空頭排列牢不可破)。
        此時根本尚未走到空頭趨勢末端，後續在 17:20 又迅速跌破開空！
        鐵律：既然大週期依然是空頭且後續還要開空，中途就絕對嚴禁提早平倉！
        ExitGate.evaluate 必須回傳 should_exit == False, reason == "VETO_HOLD"，
        徹底避免「17:19平空 ➔ 17:20追空」的低賣高買割肉損耗！
        """
        import pandas as pd
        from core.gates.unified_pipeline_gates import ExitGate

        pos_lobster = {
            "symbol": "龙虾/USDT",
            "side": "SHORT",
            "entry_price": 0.02182782,
            "qty": 13376.0,
            "peak_roe": 0.035,
        }

        # 構造 17:17~17:19 實時底部小紅小綠橫盤整理 K 棒
        bars = [
            {"open": 0.021720, "high": 0.021740, "low": 0.021715, "close": 0.021725, "ma15": 0.021740},
            {"open": 0.021725, "high": 0.021760, "low": 0.021720, "close": 0.021755, "ma15": 0.021745},  # 收盤微高於 EMA15
            {"open": 0.021755, "high": 0.021765, "low": 0.021730, "close": 0.021740, "ma15": 0.021748},  # 第二根微高於 EMA15
        ]
        df = pd.DataFrame(bars)

        market_data = {
            "symbol": "龙虾/USDT",
            "current_price": 0.021740,
            "atr": 0.000045,
            "kc_upper": 0.021920,
            "kc_middle": 0.021830,
            "kc_lower": 0.021740,
            "kc_middle_slope": -0.00003, # 中軌強烈向下
            "ema_fast": 0.021750,        # EMA15
            "ema_slow": 0.021840,        # EMA40 -> 牢固空頭排列 (ema_fast <= ema_slow)
            "raw_df": df,
        }

        should_exit, reason, meta = ExitGate.evaluate(pos_lobster, market_data, current_price=0.021740)
        assert should_exit is False, f"未達空頭趨勢末端卻在 17:19 被錯誤平空！reason={reason}, meta={meta}"
        assert reason == "VETO_HOLD"
        assert "未到空頭趨勢末端" in meta.get("cause", "") or "維持死抱" in meta.get("cause", "")









