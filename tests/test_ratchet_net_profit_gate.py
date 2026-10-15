"""
單元測試：RatchetNetProfitManager 與限價防滑鎖利 Gate (5%起跑、回吐3%、每階3%推進)
徹底驗證：
1. 淨利精準計算（扣除 0.1% 雙邊手續費與累計資金費）
2. 鎖利階梯推進：
   - 達 5% 淨利 -> 鎖 2% (Tier 1)
   - 達 8% 淨利 -> 鎖 5% (Tier 2)
   - 達 11% 淨利 -> 鎖 8% (Tier 3, 每 3% 推進 3%)
3. 單向棘輪保底（只升不降，價格回踩不降級）
4. 回吐觸發限價防滑平倉（帶 0.25% offset 死守底線）
5. 3 秒逾時 Watchdog 追單機制
6. 貫通整合：TrendHoldingAndExitManager 與 Account 平倉防滑驗證
"""
import pytest
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock
import pandas as pd

from core.gates.ratchet_net_profit_gate import RatchetNetProfitManager, get_ratchet_manager
from core.gates.trend_holding_immunity_and_exit_gate import TrendHoldingAndExitManager
from core.paper_account import PaperAccount


def test_calculate_net_pnl_long_and_short():
    manager = RatchetNetProfitManager(fee_rate=0.001, slippage_offset=0.0025)

    # Long: Entry 100, Current 105.1 -> 毛利 5.1%, 扣 0.1% 手續費 -> 淨利 5.0%
    net_pnl_long = manager.calculate_net_pnl('long', 100.0, 105.1, accumulated_funding_pct=0.0)
    assert pytest.approx(net_pnl_long, rel=1e-4) == 0.050

    # Long with funding fee 0.0002 -> 淨利 4.98%
    net_pnl_funding = manager.calculate_net_pnl('long', 100.0, 105.1, accumulated_funding_pct=0.0002)
    assert pytest.approx(net_pnl_funding, rel=1e-4) == 0.0498

    # Short: Entry 100, Current 94.9 -> 毛利 5.1%, 扣 0.1% 手續費 -> 淨利 5.0%
    net_pnl_short = manager.calculate_net_pnl('short', 100.0, 94.9, accumulated_funding_pct=0.0)
    assert pytest.approx(net_pnl_short, rel=1e-4) == 0.050


def test_ratchet_tier_progression_and_monotonicity():
    manager = RatchetNetProfitManager(symbol='TEST/USDT', fee_rate=0.001, slippage_offset=0.0025)

    # 1. 浮盈 4% (未達 5% 起跑門檻)
    # Entry 100, Current 104.1 -> 淨利 4.0%
    triggered, details = manager.evaluate_and_update('long', 100.0, 104.1)
    assert triggered is False
    assert manager.locked_net_pnl == 0.0
    assert details['armed'] is False

    # 2. 浮盈達 5.1% -> 扣手續費淨利 5.0% (Tier 1: 鎖 2% 淨利)
    triggered, details = manager.evaluate_and_update('long', 100.0, 105.1)
    assert triggered is False
    assert details['armed'] is True
    assert pytest.approx(manager.locked_net_pnl, rel=1e-4) == 0.02
    assert manager.tier == 1

    # 3. 浮盈續衝至 8.1% -> 扣手續費淨利 8.0% (Tier 2: 鎖 5% 淨利)
    triggered, details = manager.evaluate_and_update('long', 100.0, 108.1)
    assert triggered is False
    assert pytest.approx(manager.locked_net_pnl, rel=1e-4) == 0.05
    assert manager.tier == 2

    # 4. 浮盈暴衝至 14.1% -> 扣手續費淨利 14.0%
    # (14% - 5%) // 3% = 3 階 -> target_lock = 2% + 3*3% = 11% (Tier 4)
    triggered, details = manager.evaluate_and_update('long', 100.0, 114.1)
    assert triggered is False
    assert pytest.approx(manager.locked_net_pnl, rel=1e-4) == 0.11
    assert manager.tier == 4

    # 5. 價格回調至 113.0 (淨利 12.9%)，單向棘輪鎖定淨利線絕不回退，死守 11%
    triggered, details = manager.evaluate_and_update('long', 100.0, 113.0)
    assert triggered is False
    assert pytest.approx(manager.locked_net_pnl, rel=1e-4) == 0.11


def test_ratchet_trigger_pullback_exit_and_protected_limit():
    manager = RatchetNetProfitManager(symbol='TEST/USDT', fee_rate=0.001, slippage_offset=0.0025)

    # 衝到 11.1% 淨利 -> 觸發 Tier 3，鎖定 8% 淨利
    manager.evaluate_and_update('long', 100.0, 111.2)
    assert pytest.approx(manager.locked_net_pnl, rel=1e-4) == 0.08

    # 計算保底限價：
    # base_price = 100 * (1 + 0.08 + 0.001) = 108.1
    # limit_price = 108.1 * (1 - 0.0025) = 107.82975
    limit_p = manager.calculate_protected_limit_price('long', 100.0)
    assert pytest.approx(limit_p, rel=1e-4) == 107.82975

    # 價格回吐跌破 8% 淨利線 (現價 107.5 -> 淨利 7.4% <= 8.0%)
    triggered, details = manager.evaluate_and_update('long', 100.0, 107.5)
    assert triggered is True
    assert details['is_limit'] is True
    assert pytest.approx(details['limit_price'], rel=1e-4) == 107.82975


@pytest.mark.anyio
async def test_watchdog_timeout_cancel_and_reorder():
    """驗證 3 秒 Watchdog：掛出限價單若未成交，自動撤單並改追盤口買一/賣一家"""
    mock_exchange = MagicMock()
    mock_exchange.price_to_precision = lambda s, p: f"{p:.4f}"
    mock_exchange.amount_to_precision = lambda s, a: f"{a:.2f}"

    # 模擬 3 秒內 fetch_order 返回 open，3 秒後未成交
    mock_exchange.fetch_order = MagicMock(return_value={'status': 'open', 'remaining': 10.0})
    mock_exchange.cancel_order = MagicMock()
    mock_exchange.fetch_order_book = MagicMock(return_value={
        'bids': [[107.5, 50.0]],
        'asks': [[107.8, 40.0]],
    })
    mock_exchange.create_order = MagicMock(return_value={'id': 'order_new_123'})

    manager = RatchetNetProfitManager(exchange=mock_exchange, symbol='TEST/USDT', slippage_offset=0.0025)
    manager.locked_net_pnl = 0.08

    # 測試短逾時 (0.2 秒以加速測試)
    await manager._ensure_order_filled_async('order_old_999', 'sell', 10.0, 107.8, timeout_sec=0.2)

    # 斷言：原單被撤銷，並以盤口最佳買一價 (107.5) 重新掛 limit 單追單
    mock_exchange.cancel_order.assert_called_once_with('order_old_999', 'TEST/USDT')
    mock_exchange.create_order.assert_called_once_with(
        symbol='TEST/USDT',
        type='limit',
        side='sell',
        amount=10.0,
        price=107.5,
        params={'reduceOnly': True}
    )


@pytest.mark.anyio
async def test_paper_account_limit_close_no_slippage_catastrophe():
    """驗證 PaperAccount 平倉時，帶 is_limit/limit_price 絕不遭受市價滑價砸盤（徹底終結 14 滑到 11）"""
    acc = PaperAccount()
    acc.balance = 1000.0
    acc.realized_pnl = 0.0
    acc.positions['LOBSTER/USDT'] = {
        'symbol': 'LOBSTER/USDT',
        'side': 'LONG',
        'entry_price': 10.0,
        'qty': 100.0,
        'margin': 200.0,
    }

    # 一般市價平倉會扣 SLIPPAGE_PCT (0.15% 或更多)
    # 但若為鎖利限價平倉，必須死守限價底線 (例如 11.20)
    limit_price = 11.20
    closed = await acc.close_position(
        'LOBSTER/USDT',
        current_price=11.25,
        close_reason="DualTF EXIT_BY_RATCHET_PROFIT_LOCK",
        is_manual=True,
        is_limit=True,
        limit_price=limit_price,
    )

    assert closed is True
    assert 'LOBSTER/USDT' not in acc.positions
    # 斷言獲利結算時，平倉價精準使用 limit_price 11.20，絕不受市價單砸盤滑到底部
    # raw_pnl = (11.20 - 10.0) * 100 = 120.0
    # open_fee = 100 * 10 * 0.0005 = 0.5
    # close_fee = 100 * 11.20 * 0.0005 = 0.56
    # net_pnl = 120.0 - 1.06 = 118.94
    assert pytest.approx(acc.realized_pnl, rel=1e-3) == 118.94


def test_candle_close_only_blocks_intrabar_tick_profit_lock():
    """防線一驗證：未收盤的盤中毛刺回落 100% 駁回平倉，讓子彈飛"""
    position = {
        'symbol': 'LOBSTER/USDT',
        'side': 'LONG',
        'entry_price': 100.0,
        'qty': 10.0,
        'atr': 1.0,
        'peak_price': 106.0, # 曾衝高到 106.0，淨利達 5.9% 啟動鎖利 (鎖 2.0%)
        'peak_roe_pct': 0.06,
    }

    # 該根 K 棒尚未收盤 (is_closed: False)
    # 盤中從 106.0 回落到 101.5 (淨利 1.4% <= 鎖利線 2.0%)
    df_5m = pd.DataFrame([
        {'open': 100.0, 'close': 101.5, 'high': 106.0, 'low': 99.5, 'ma3': 102.0, 'ema3': 102.0, 'ma5': 101.0, 'ma15': 100.0, 'kc_upper': 103.0, 'is_closed': False},
    ])

    can_exit, reason, details = TrendHoldingAndExitManager.evaluate_exit(
        position=position, df_5m=df_5m, quote=101.5
    )

    # 必須一票否決平倉，拒絕被即時毛刺洗下車！
    assert can_exit is False
    assert "CANDLE_NOT_CLOSED" in reason


def test_ma_filter_blocks_exit_when_above_ema3_and_kc_upper():
    """防線二驗證：即使滿足回吐條件，只要收盤價依然穩穩踩在極短期攻擊線 (3 EMA) 之上且在 KC 上軌外，強制 HOLD 絕不離場！"""
    position = {
        'symbol': 'LOBSTER/USDT',
        'side': 'LONG',
        'entry_price': 100.0,
        'qty': 10.0,
        'atr': 1.0,
        'peak_price': 106.0, # 曾衝高到 106.0，鎖定 2.0% (102.0)
        'peak_roe_pct': 0.06,
    }

    # 該根 K 棒已收盤 (is_closed: True)，收在 101.5 (淨利 1.4% <= 鎖利線 2.0%)
    # 攻擊均線 ema3 為 101.0，KC 上軌為 100.8
    # 收盤價 101.5 > ema3 (101.0) 且 > kc_upper (100.8)
    df_5m = pd.DataFrame([
        {'open': 99.0, 'close': 102.0, 'high': 103.0, 'low': 98.5, 'ma3': 100.0, 'ema3': 100.0, 'ma5': 99.5, 'ma15': 99.0, 'kc_upper': 100.5, 'kc_lower': 98.0, 'is_closed': True},
        {'open': 102.0, 'close': 101.5, 'high': 106.0, 'low': 101.0, 'ma3': 101.0, 'ema3': 101.0, 'ma5': 100.5, 'ma15': 99.5, 'kc_upper': 100.8, 'kc_lower': 98.0, 'is_closed': True},
    ])

    can_exit, reason, details = TrendHoldingAndExitManager.evaluate_exit(
        position=position, df_5m=df_5m, quote=101.5
    )

    # 必須觸發 MA Filter 保護，強制 HOLD 絕不離場！
    assert can_exit is False
    assert "STRONG_TREND_ABOVE_EMA3_AND_KC_UPPER" in reason


def test_ma_filter_blocks_short_exit_when_below_ema3_and_kc_lower():
    """防線二空單驗證：空單收盤價壓制在 3 EMA 之下且在 KC 下軌外，強制 HOLD 絕不平空！"""
    position = {
        'symbol': 'LOBSTER/USDT',
        'side': 'SHORT',
        'entry_price': 100.0,
        'qty': 10.0,
        'atr': 1.0,
        'lowest_price': 94.0, # 曾跌到 94.0，鎖定 2.0% (98.0)
        'peak_roe_pct': 0.06,
    }

    # 該根 K 棒已收盤 (is_closed: True)，收在 98.5 (淨利 1.4% <= 鎖利線 2.0%)
    # 攻擊均線 ema3 為 99.0，KC 下軌為 99.2
    # 收盤價 98.5 < ema3 (99.0) 且 < kc_lower (99.2)
    df_5m = pd.DataFrame([
        {'open': 101.0, 'close': 98.0, 'high': 101.5, 'low': 97.0, 'ma3': 100.0, 'ema3': 100.0, 'ma5': 100.5, 'ma15': 101.0, 'kc_upper': 102.0, 'kc_lower': 99.5, 'is_closed': True},
        {'open': 98.0, 'close': 98.5, 'high': 99.0, 'low': 94.0, 'ma3': 99.0, 'ema3': 99.0, 'ma5': 99.5, 'ma15': 100.5, 'kc_upper': 102.0, 'kc_lower': 99.2, 'is_closed': True},
    ])

    can_exit, reason, details = TrendHoldingAndExitManager.evaluate_exit(
        position=position, df_5m=df_5m, quote=98.5
    )

    # 必須觸發 MA Filter 保護，強制 HOLD 絕不平空！
    assert can_exit is False
    assert "STRONG_TREND_BELOW_EMA3_AND_KC_LOWER" in reason

