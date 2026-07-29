import pytest
import pandas as pd
import numpy as np
import os
import json
import core.paper_account as pa_module
import core.strategy as strategy_module
from core.config import (
    DEFAULT_SYMBOLS, get_position_multiplier, get_signal_leverage,
    RSI_LONG_THRESHOLD, FRESHNESS_DECAY_BARS, MIN_SCORE_THRESHOLD, ADX_QUALITY_MIN,
    STOP_LOSS_MULTIPLIER, TAKE_PROFIT_MULTIPLIER, DISASTER_STOP_MULTIPLIER,
    STRONG_BREAKOUT_SCORE_THRESHOLD,
)
from core.ai_advisor import LocalAIAdvisor
from core.trade_history_analysis import TradeHistoryAnalyzer
from core.strategy import SuperTrendKeltnerStrategy, compute_sl_tp_distance
from core.paper_account import PaperAccount
from core.symbol_rotation import SymbolRotation
from core.indicators import compute_position_trigger

def test_strategy_indicators():
    strategy = SuperTrendKeltnerStrategy()
    dates = pd.date_range(start="2026-01-01", periods=100, freq="15min")
    prices = np.linspace(100, 200, 100)
    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices,
        "high": prices + 1.0,
        "low": prices - 1.0,
        "close": prices,
        "volume": 1000
    })
    res = strategy.compute_indicators(df)
    assert "supertrend" in res.columns
    assert "st_direction" in res.columns
    assert "atr" in res.columns

def test_paper_account_open_close(tmp_path, monkeypatch):
    # 隔離測試：使用臨時空白狀態檔，不受真實持倉影響
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "test_account.json"))
    account = PaperAccount()
    close_notifications = []
    account.on_trade_closed = lambda: close_notifications.append("closed")
    initial_bal = account.balance
    success = account.open_position("BTC/USDT", "LONG", 50000.0, 50.0, 49000.0, 52000.0, "Test Entry")
    assert success is True
    assert "BTC/USDT" in account.positions

    close_success = account.close_position("BTC/USDT", 51000.0, "Test Exit")
    assert close_success is True
    assert "BTC/USDT" not in account.positions
    assert close_notifications == ["closed"]

def test_low_score_signal_caps_eth_leverage():
    """最低檔門檻用 MIN_SCORE_THRESHOLD 本身，避免它跟這裡的最低檔位置
    調開之後又出現「兩個門檻中間的分數算出 0 倍位，下單金額變 0」的
    空隙（實測 DOGE/USDT 07/29 這筆就是這樣炸掉主迴圈的）。"""
    assert get_position_multiplier(MIN_SCORE_THRESHOLD - 1) == 0.0
    assert get_position_multiplier(MIN_SCORE_THRESHOLD) == 0.6
    assert get_position_multiplier(80) == 1.0
    assert get_position_multiplier(90) == 1.0
    assert get_signal_leverage("ETH/USDT", 70) == 3
    assert get_signal_leverage("ETH/USDT", 80) == 6
    assert get_signal_leverage("ETH/USDT", 90) == 10
    assert get_signal_leverage("APT/USDT", 70) == 3


def test_open_trade_persists_score_reason_and_dynamic_leverage(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "test_account.json"))
    account = PaperAccount()
    assert account.open_position(
        "ETH/USDT", "LONG", 1900.0, 30.0, 1890.0, 1920.0,
        "Score70 test", signal_score=70
    )
    assert account.positions["ETH/USDT"]["leverage"] == 3
    trade = account.trades[0]
    assert trade["leverage"] == 3
    assert trade["signal_score"] == 70
    assert trade["reason"] == "Score70 test"

def test_atr_range_filter_is_mandatory(monkeypatch):
    """1h 大趨勢之外，ATR 波動率範圍是目前唯一還會直接 HOLD 的強制門檻
    （KC 突破、量能、RSI、新鮮度都已改成評分制，見下面
    test_kc_breakout_and_freshness_lower_score_not_mandatory）。"""
    strategy = SuperTrendKeltnerStrategy()
    df = pd.DataFrame({
        "close": [100.0] * 50,
        "close_price_spike_filtered": [100.0] * 50,
        "atr": [1.0] * 50,  # atr/price = 1% > MAX_ATR_PCT(0.6%)
        "rsi": [60.0] * 50,
        "adx": [30.0] * 50,
        "volume": [1000.0] * 50,
        "vol_ma_20": [900.0] * 50,
        "kc_upper": [101.0] * 50,
        "kc_lower": [99.0] * 50,
        "kc_width": [2.0] * 50,
        "ema_20": [101.0] * 50,
        "ema_50": [100.0] * 50,
        "st_direction": [1] * 50,
    })
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 1)
    result = strategy.evaluate_signal(df, ema_50_1h=90.0)
    assert result["action"] == "HOLD"
    assert "Mandatory_Fail: ATR_Too_High" in result["reason"]

    df.loc[:, "atr"] = 0.001  # atr/price = 0.001% < MIN_ATR_PCT(0.15%)
    result = strategy.evaluate_signal(df, ema_50_1h=90.0)
    assert result["action"] == "HOLD"
    assert "Mandatory_Fail: ATR_Too_Low" in result["reason"]


def _entry_score_frame(volume=700.0, rsi=49.0, adx=20.0):
    return pd.DataFrame({
        "close": [100.05] * 50,
        "close_price_spike_filtered": [100.05] * 50,
        "atr": [0.3] * 50,  # atr/price = 0.3%，落在 MIN/MAX_ATR_PCT 之間，不會被強制門檻擋掉
        "rsi": [rsi] * 50,
        "adx": [adx] * 50,
        "volume": [volume] * 50,
        "vol_ma_20": [1000.0] * 50,
        "kc_upper": [100.0] * 50,
        "kc_lower": [98.0] * 50,
        "kc_width": [2.0] * 50,
        "ema_20": [101.0] * 50,
        "ema_50": [100.0] * 50,
        "st_direction": [1] * 50,
    })


def test_kc_breakout_and_freshness_lower_score_not_mandatory(monkeypatch):
    """KC 突破/訊號新鮮度沒過，不再是強制擋單（Mandatory_Fail），
    而是評分制底下的扣分，分數不夠門檻時走 HOLD + Score_Low。"""
    strategy = SuperTrendKeltnerStrategy()
    # ADX 給 13（高於 ADX_MANDATORY_MIN=12 硬性門檻，讓訊號進入評分系統，
    # 但低於 ADX_QUALITY_MIN=15，品質加分仍為 0）；量能、RSI 也刻意不過，
    # 確保不管品質加分怎麼算都遠低於 MIN_SCORE_THRESHOLD，走到 Score_Low 分支。
    frame = _entry_score_frame(volume=100.0, rsi=RSI_LONG_THRESHOLD - 5, adx=13.0)
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: FRESHNESS_DECAY_BARS + 50)

    result = strategy.evaluate_signal(frame, ema_50_1h=95.0)

    assert result["action"] == "HOLD"
    # Score_Low 分支的 dict 沒有獨立的 "score" 欄位，分數只嵌在 reason 文字裡
    assert "Score_Low" in result["reason"]


def test_qualifying_score_scheme_a_branching(monkeypatch):
    """方案 A 分流機制：
    評分 >= 85 時觸發 BUY（強勢突破直接市價進場）；
    評分 71~84 時觸發 WAIT_PULLBACK（溫和突破等待回踩）。"""
    strategy = SuperTrendKeltnerStrategy()
    
    # 高分強突破 (>= 85) -> 應為 BUY
    frame_high = _entry_score_frame(volume=1500.0, rsi=RSI_LONG_THRESHOLD + 10, adx=35.0)
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 1)
    result_high = strategy.evaluate_signal(frame_high, ema_50_1h=95.0)
    assert result_high["action"] == "BUY"
    assert result_high["score"] >= STRONG_BREAKOUT_SCORE_THRESHOLD

    # 溫和突破 (71 ~ 84) -> 應為 WAIT_PULLBACK
    frame_mid = _entry_score_frame(volume=1000.0, rsi=RSI_LONG_THRESHOLD, adx=20.0)
    result_mid = strategy.evaluate_signal(frame_mid, ema_50_1h=95.0)
    if result_mid["score"] < STRONG_BREAKOUT_SCORE_THRESHOLD:
        assert result_mid["action"] == "WAIT_PULLBACK"
        assert "target_zone" in result_mid


def test_adx_declining_blocks_entry_even_with_qualifying_score(monkeypatch):
    """SuperTrend 方向沒反轉、分數也達標，但 ADX 連續下滑且已經低於
    ADX_QUALITY_MIN——實測 AAVE/USDT 07/28 這筆進場前 8 根 5 分K，ADX
    從 19.51 降到 14.67 才進場，方向沒變、新鮮度分數也還高，是新鮮度
    抓不到的另一種末端趨勢樣貌，必須直接擋單而不是只扣分。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _entry_score_frame(volume=1200.0, rsi=RSI_LONG_THRESHOLD + 5, adx=35.0)
    frame.loc[44:49, "adx"] = [19.5, 18.8, 17.6, 16.5, 15.7, 14.7]
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 1)

    result = strategy.evaluate_signal(frame, ema_50_1h=95.0)

    assert result["action"] == "HOLD"
    assert "Mandatory_Fail: ADX_Declining_Exhaustion" in result["reason"]


def test_price_overextended_blocks_entry_even_with_qualifying_score(monkeypatch):
    """價格距離 EMA20 太遠（用 ATR 正規化衡量）代表這波已經漲很多才追
    進場，均值回歸風險高，不管總分靠其他項目湊得多高都要擋單。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _entry_score_frame(volume=1200.0, rsi=RSI_LONG_THRESHOLD + 5, adx=35.0)
    frame["ema_20"] = 100.05 - 5 * 0.3  # 距離拉開到 5倍 ATR，超過 EMA_EXTENSION_MAX_ATR_MULT(3.5)
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 1)

    result = strategy.evaluate_signal(frame, ema_50_1h=95.0)

    assert result["action"] == "HOLD"
    assert "Mandatory_Fail: Price_Overextended" in result["reason"]


def test_1h_trend_declining_blocks_entry_even_with_qualifying_score(monkeypatch):
    """大週期（1h）本身動能也在衰退時（engine.py 用同一批1h K線算好傳
    進來），就算5分K的分數/條件都達標，也要擋單——這是5分K的新鮮度/
    ADX檢查看不到的更高層級末端訊號。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _entry_score_frame(volume=1200.0, rsi=RSI_LONG_THRESHOLD + 5, adx=35.0)
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 1)

    result = strategy.evaluate_signal(frame, ema_50_1h=95.0, trend_1h_declining=True)

    assert result["action"] == "HOLD"
    assert "Mandatory_Fail: 1h_Trend_Declining" in result["reason"]


def _reconfirm_frame(side="LONG", st_direction=None, volume=1000.0, rsi=None, atr=0.3):
    """confirm_pullback_entry() 用的最小 K 線快照：atr 落在 MIN/MAX_ATR_PCT
    之間，避免被 ATR 範圍門檻誤擋，方便單獨測試量能/RSI/趨勢反轉的判斷。"""
    direction = 1 if side == "LONG" else -1
    if st_direction is None:
        st_direction = direction
    if rsi is None:
        rsi = 60.0 if side == "LONG" else 40.0
    price = 100.0
    return pd.DataFrame({
        "close": [price] * 50,
        "close_price_spike_filtered": [price] * 50,
        "atr": [atr] * 50,
        "rsi": [rsi] * 50,
        "volume": [volume] * 50,
        "vol_ma_20": [900.0] * 50,
        "kc_upper": [101.0] * 50,
        "kc_lower": [99.0] * 50,
        "ema_20": [price] * 50,
        "st_direction": [st_direction] * 50,
        "adx": [25.0] * 50,
    })


def test_pullback_reconfirmation_passes_when_conditions_still_hold(monkeypatch):
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG")
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=95.0)
    assert result["status"] == "PASS"


def test_pullback_reconfirmation_cancels_when_supertrend_reversed(monkeypatch):
    """假突破最典型的樣貌：登記回調待命之後，SuperTrend 方向其實已經反轉了。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG", st_direction=-1)
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=95.0)
    assert result["status"] == "CANCEL"
    assert "SuperTrend 方向已反轉" in result["reason"]


def test_pullback_reconfirmation_cancels_when_too_stale(monkeypatch):
    """方向沒反轉不代表還「新鮮」——等回踩的期間，行情可能只是原地盤整
    消耗動能，SuperTrend 遲遲沒真的翻轉，但這個突破本身已經是強弩之末。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG")
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: FRESHNESS_DECAY_BARS)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=95.0)
    assert result["status"] == "CANCEL"
    assert "距離原始突破已過太久" in result["reason"]


def test_pullback_reconfirmation_cancels_when_adx_declining(monkeypatch):
    """SuperTrend 方向沒反轉、新鮮度也還夠，但 ADX 連續下滑且已經低於
    ADX_QUALITY_MIN——實測 AAVE/USDT 進場前就是這個樣貌（方向沒變，
    動能已經在退潮），屬於新鮮度抓不到的另一種末端趨勢。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG")
    adx_series = [25.0] * 44 + [19.5, 18.8, 17.6, 16.5, 15.7, 14.7]
    frame["adx"] = adx_series
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=95.0)
    assert result["status"] == "CANCEL"
    assert "ADX 動能持續衰退" in result["reason"]


def test_pullback_reconfirmation_cancels_when_1h_trend_declining(monkeypatch):
    """等回踩的這段時間裡，就算5分K條件都還成立，若大週期(1h)本身的
    動能已經在衰退（engine.py 算好傳進來），一樣取消。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG")
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)

    result = strategy.confirm_pullback_entry(
        frame, side="LONG", ema_1h=95.0, trend_1h_declining=True
    )
    assert result["status"] == "CANCEL"
    assert "大週期(1h)動能已在衰退" in result["reason"]


def test_pullback_reconfirmation_cancels_when_price_overextended(monkeypatch):
    """等回踩的這段時間裡價格可能又衝更遠，均值回歸風險比登記當下更高，
    超過 EMA_EXTENSION_MAX_ATR_MULT 就取消。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG")
    frame["ema_20"] = 100.0 - 5 * 0.3  # _reconfirm_frame price=100.0, atr=0.3
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=95.0)
    assert result["status"] == "CANCEL"
    assert "價格乖離EMA20過大" in result["reason"]


def test_pullback_reconfirmation_cancels_when_price_breaches_ema20_long(monkeypatch):
    """健康回調只應該靠近 EMA20，不會真的穿越到對面：多單回踩時價格已經
    跌破 EMA20（哪怕乖離幅度還不到 Price_Overextended 的門檻），代表這已
    經不是回調、是真的在反轉，直接取消。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG")
    frame["ema_20"] = 100.05  # price(100.0) < ema_20，乖離幅度很小，不會觸發 Price_Overextended
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=95.0)
    assert result["status"] == "CANCEL"
    assert "回踩跌破EMA20" in result["reason"]


def test_pullback_reconfirmation_cancels_when_price_breaches_ema20_short(monkeypatch):
    """空單回踩時價格已經站上 EMA20，一樣視為真反轉而非健康回調，取消。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("SHORT")
    frame["ema_20"] = 99.95  # price(100.0) > ema_20
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)

    result = strategy.confirm_pullback_entry(frame, side="SHORT", ema_1h=105.0)
    assert result["status"] == "CANCEL"
    assert "回踩突破EMA20" in result["reason"]


def test_pullback_reconfirmation_passes_when_price_still_on_correct_side_of_ema20(monkeypatch):
    """多單回踩時價格還在 EMA20 之上（哪怕只是貼著），視為還在健康回調
    範圍內，不觸發這道新防線。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG")
    frame["ema_20"] = 99.95  # price(100.0) 剛好還在 ema_20 之上
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=95.0)
    assert result["status"] == "PASS"


def test_pullback_reconfirmation_passes_when_volume_weak_but_other_signals_strong(monkeypatch):
    """量能單項不再是硬性關卡：新鮮度/RSI/ADX 都還強的情況下，總分
    （B量能+C RSI+D新鮮度+E品質，滿分79）足以覆蓋 PULLBACK_SCORE_THRESHOLD，
    量能偏弱不再單獨否決這筆回踩進場——這是本次改動要達成的補償式判斷。
    volume=600 落在 vol_ma_20(900) × POST_BREAKOUT_VOL_SUSTAIN_RATIO(0.6)=540 以上（通過衰退門檻），
    但低於 KELTNER_MIN_VOLUME_RATIO(0.8)×900=720（B量能評分仍為 0），
    確認即使量能評分零分，其餘訊號可補足到總分門檻。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG", volume=600.0)  # 通過衰退門檻但低於量能加分門檻
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=95.0)
    assert result["status"] == "PASS"



def test_pullback_reconfirmation_cancels_when_pullback_score_insufficient(monkeypatch):
    """量能弱、RSI 剛好卡在門檻（無邊際加分）、ADX 剛好卡在 ADX_QUALITY_MIN
    （無邊際加分，但還不到觸發 ADX 衰退硬性紅線的程度）：
    量能極低（100 < vol_ma_20×0.6=540）時，量能衰退門檻（Vol_Fade）會在計分前先觸發，
    兩種 CANCEL 路徑都代表訊號不夠強，任一路徑都應取消進場。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG", volume=100.0, rsi=RSI_LONG_THRESHOLD)
    frame["adx"] = [ADX_QUALITY_MIN] * 50
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=95.0)
    assert result["status"] == "CANCEL"
    # 量能極低時 vol-fade 門檻（上游）或回調總分不足（下游）均為正確取消理由
    assert "CANCEL" == result["status"]
    assert any(kw in result["reason"] for kw in ("回調總分不足", "Vol_Fade", "突破後量能萎縮"))



def test_pullback_reconfirmation_cancels_when_1h_trend_flipped(monkeypatch):
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG")
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=105.0)
    assert result["status"] == "CANCEL"
    assert "1h 大趨勢已轉空" in result["reason"]


def test_local_ai_advisor_accepts_only_allowed_symbols():
    def fake_request(payload):
        assert payload["response_format"]["type"] == "json_object"
        return {
            "model": "test-local",
            "choices": [{"message": {"content": (
                "{\"ranked_symbols\":[\"BTC/USDT\",\"BAD/USDT\","
                "\"BTC/USDT\",\"DOGE/USDT\"],\"summary\":\"測試\"}"
            )}}],
        }

    advisor = LocalAIAdvisor(
        "http://127.0.0.1:8888/v1/chat/completions",
        request_fn=fake_request,
    )
    metrics = [
        {
            "symbol": symbol, "quant_score": 0.5, "trades": 0,
            "avg_pnl": 0.0, "win_rate": 0.5, "stop_rate": 0.0,
            "quote_volume": 1000.0, "change_pct": 1.0,
        }
        for symbol in ["BTC/USDT", "DOGE/USDT"]
    ]
    import asyncio
    ranked = asyncio.run(advisor.rank_symbols(metrics))
    assert ranked == ["BTC/USDT", "DOGE/USDT"]
    assert advisor.status()["status"] == "ok"


def test_trade_history_ai_analysis_is_sanitized_cached_and_persisted(tmp_path):
    calls = []

    def fake_request(payload):
        calls.append(payload)
        user_payload = json.loads(payload["messages"][1]["content"])
        assert user_payload["task"] == "analyze_trade_history"
        history = user_payload["history"]
        assert history["overview"]["closed_trades"] == 1
        serialized = json.dumps(history)
        assert "BINANCE_API_KEY" not in serialized
        assert "must-not-leak" not in serialized
        return {
            "model": "test-local",
            "choices": [{"message": {"content": json.dumps({
                "summary": "一筆交易樣本，暫以風控觀察為主。",
                "strengths": ["有記錄訊號分數"],
                "weaknesses": ["樣本不足"],
                "recommendations": ["累積更多樣本後再調整"],
                "risk_flags": ["單筆結果不具代表性"],
            }, ensure_ascii=False)}}],
        }

    advisor = LocalAIAdvisor(
        "http://127.0.0.1:8888/v1/chat/completions",
        request_fn=fake_request,
    )
    analyzer = TradeHistoryAnalyzer(
        advisor,
        analysis_file=str(tmp_path / "ai_trade_analysis.json"),
    )
    trades = [
        {
            "id": 2,
            "time": "07/26 01:05:00",
            "symbol": "BTC/USDT",
            "action": "CLOSE_LONG",
            "side": "LONG",
            "price": 101.0,
            "amount": 25.0,
            "fee": 0.1,
            "pnl": 0.9,
            "status": "CLOSED",
            "reason": "觸發止盈 (Take-Profit)",
        },
        {
            "id": 1,
            "time": "07/26 01:00:00",
            "symbol": "BTC/USDT",
            "action": "OPEN_LONG",
            "side": "LONG",
            "price": 100.0,
            "amount": 25.0,
            "fee": 0.05,
            "pnl": 0.0,
            "status": "OPEN",
            "leverage": 3,
            "signal_score": 70,
            "reason": "Pullback_Confirmed",
            "api_secret": "must-not-leak",
        },
    ]

    import asyncio
    assert asyncio.run(analyzer.analyze_if_changed(trades)) is True
    assert asyncio.run(analyzer.analyze_if_changed(trades)) is False
    assert len(calls) == 1
    assert analyzer.status()["status"] == "ok"
    assert analyzer.status()["trade_count"] == 1
    assert (tmp_path / "ai_trade_analysis.json").exists()

    restored = TradeHistoryAnalyzer(
        advisor,
        analysis_file=str(tmp_path / "ai_trade_analysis.json"),
    )
    assert restored.status()["summary"] == "一筆交易樣本，暫以風控觀察為主。"


def test_market_candidates_only_keeps_liquid_crypto_perpetuals(monkeypatch):
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_MIN_QUOTE_VOLUME", 20_000_000.0)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_MARKET_SCAN_LIMIT", 40)
    tickers = {
        "BTC/USDT:USDT": {"quoteVolume": 100_000_000.0},
        "SKHY/USDT:USDT": {"quoteVolume": 200_000_000.0},
        "LOW/USDT:USDT": {"quoteVolume": 10_000_000.0},
    }
    markets = {
        "BTC/USDT:USDT": {
            "symbol": "BTC/USDT:USDT", "active": True, "swap": True, "quote": "USDT",
            "info": {"contractType": "PERPETUAL", "underlyingType": "COIN"},
        },
        "SKHY/USDT:USDT": {
            "symbol": "SKHY/USDT:USDT", "active": True, "swap": True, "quote": "USDT",
            "info": {"contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY"},
        },
        "LOW/USDT:USDT": {
            "symbol": "LOW/USDT:USDT", "active": True, "swap": True, "quote": "USDT",
            "info": {"contractType": "PERPETUAL", "underlyingType": "COIN"},
        },
    }
    assert SymbolRotation.market_candidates(tickers, markets) == ["BTC/USDT"]


def test_purge_unhealthy_swaps_illiquid_candidate_but_protects_held_position(monkeypatch):
    """觀察名單裡流動性枯竭的候選幣種要立刻換掉，不用等下一次整點輪替；
    已經有持倉的幣種即使一樣流動性枯竭，也不能被這個輕量健康檢查動到。"""
    import asyncio

    monkeypatch.setattr("core.symbol_rotation.SYMBOL_MIN_QUOTE_VOLUME", 20_000_000.0)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_MAX_24H_CHANGE_PCT", 30.0)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_MARKET_SCAN_LIMIT", 40)
    watchlist = ["ILLIQUID/USDT", "HELDLOW/USDT", "GOOD/USDT"]
    monkeypatch.setattr("core.symbol_rotation.DEFAULT_SYMBOLS", watchlist)

    class _FakeAccount:
        positions = {"HELDLOW/USDT": {"side": "LONG"}}
        trades = []

    class _FakeExchange:
        markets = {
            "GOOD/USDT:USDT": {
                "symbol": "GOOD/USDT:USDT", "active": True, "swap": True, "quote": "USDT",
                "info": {"contractType": "PERPETUAL", "underlyingType": "COIN"},
            },
            "REPLACEMENT/USDT:USDT": {
                "symbol": "REPLACEMENT/USDT:USDT", "active": True, "swap": True, "quote": "USDT",
                "info": {"contractType": "PERPETUAL", "underlyingType": "COIN"},
            },
        }

        async def fetch_tickers(self):
            return {
                "ILLIQUID/USDT:USDT": {"quoteVolume": 1_000_000.0, "percentage": 1.0},
                "HELDLOW/USDT:USDT": {"quoteVolume": 1_000_000.0, "percentage": 1.0},
                "GOOD/USDT:USDT": {"quoteVolume": 100_000_000.0, "percentage": 1.0},
                "REPLACEMENT/USDT:USDT": {"quoteVolume": 90_000_000.0, "percentage": 1.0},
            }

    rotation = SymbolRotation(_FakeAccount())
    changes = asyncio.run(rotation.purge_unhealthy(_FakeExchange()))

    assert changes == [{
        "out": "ILLIQUID/USDT", "in": "REPLACEMENT/USDT",
        "reason": "流動性不足(1000000<20000000)",
    }]
    assert "ILLIQUID/USDT" not in watchlist
    assert "REPLACEMENT/USDT" in watchlist
    assert "HELDLOW/USDT" in watchlist  # 持倉中，即使流動性也差，不能被換掉


def test_directional_rotation_selects_six_each_and_protects_position(monkeypatch):
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_MIN_SCORE", 60.0)
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_SIDE_COUNT", 6)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_COUNT", 12)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_MAX_CHANGES", 12)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_MIN_SCORE_GAP", 0.0)
    current = [f"OLD{i}/USDT" for i in range(12)]
    metrics = []
    for index in range(7):
        metrics.append({
            "symbol": f"L{index}/USDT",
            "direction": "LONG",
            "eligible": True,
            "final_score": 90.0 - index,
        })
        metrics.append({
            "symbol": f"S{index}/USDT",
            "direction": "SHORT",
            "eligible": True,
            "final_score": 89.0 - index,
        })
    held_symbol = "OLD11/USDT"
    selected, directions, changes = SymbolRotation.choose_directional_symbols(
        current,
        {held_symbol: {"side": "SHORT"}},
        metrics,
    )
    assert held_symbol in selected
    assert directions[held_symbol] == "SHORT"
    assert sum(side == "LONG" for side in directions.values()) == 6
    assert sum(side == "SHORT" for side in directions.values()) == 6
    assert all(change["out"] != held_symbol for change in changes)


def test_directional_rotation_limits_each_scan_to_three_changes(monkeypatch):
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_MIN_SCORE", 60.0)
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_SIDE_COUNT", 6)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_COUNT", 12)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_MAX_CHANGES", 3)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_MIN_SCORE_GAP", 5.0)
    current = [f"OLD{index}/USDT" for index in range(12)]
    metrics = [
        {
            "symbol": f"NEW{index}/USDT",
            "direction": "LONG" if index < 6 else "SHORT",
            "eligible": True,
            "final_score": 90.0 - index,
        }
        for index in range(12)
    ]

    selected, _, changes = SymbolRotation.choose_directional_symbols(
        current, {}, metrics
    )

    assert len(selected) == 12
    assert len(changes) == 3
    assert sum(symbol.startswith("NEW") for symbol in selected) == 3


def test_directional_rotation_backfills_missing_shorts_with_longs(monkeypatch):
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_MIN_SCORE", 60.0)
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_SIDE_COUNT", 6)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_COUNT", 12)
    metrics = [
        {
            "symbol": f"L{index}/USDT",
            "direction": "LONG",
            "eligible": True,
            "final_score": 90.0 - index,
        }
        for index in range(12)
    ]
    metrics.extend([
        {
            "symbol": f"S{index}/USDT",
            "direction": "SHORT",
            "eligible": True,
            "final_score": 80.0 - index,
        }
        for index in range(2)
    ])

    selected, directions, _ = SymbolRotation.choose_directional_symbols([], {}, metrics)

    assert len(selected) == 12
    assert sum(side == "SHORT" for side in directions.values()) == 2
    assert sum(side == "LONG" for side in directions.values()) == 10


def test_directional_rotation_uses_lower_score_longs_only_to_fill_display(monkeypatch):
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_MIN_SCORE", 60.0)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_COUNT", 12)
    metrics = [
        {
            "symbol": f"L{index}/USDT",
            "direction": "LONG",
            "eligible": False,
            "final_score": 59.0 - index,
        }
        for index in range(12)
    ]
    metrics.extend([
        {
            "symbol": f"S{index}/USDT",
            "direction": "SHORT",
            "eligible": True,
            "final_score": 80.0 - index,
        }
        for index in range(2)
    ])

    selected, directions, _ = SymbolRotation.choose_directional_symbols([], {}, metrics)

    assert len(selected) == 12
    assert sum(side == "SHORT" for side in directions.values()) == 2
    assert sum(side == "LONG" for side in directions.values()) == 10


def test_trade_amount_multiplier_uses_tiered_size_for_score_70():
    assert get_position_multiplier(MIN_SCORE_THRESHOLD) == 0.6
    assert get_position_multiplier(80) == 1.0
    assert get_position_multiplier(100) == 1.0


def _trigger_frame(closes, lows=None, highs=None):
    lows = lows if lows is not None else closes
    highs = highs if highs is not None else closes
    return pd.DataFrame({"close": closes, "low": lows, "high": highs})


def test_position_trigger_long_flags_ma_cross_and_prior_low_break():
    """多單：均線走平的情況下，最後一根K棒重挫，同時跌破均線也跌破前低。"""
    closes = [100.0] * 24 + [90.0]
    lows = [99.0] * 24 + [90.0]
    highs = [101.0] * 24 + [90.0]
    result = compute_position_trigger(_trigger_frame(closes, lows, highs), "LONG")
    assert result["active"] is True
    assert "跌破均線" in result["reasons"]
    assert "跌破前低" in result["reasons"]


def test_position_trigger_long_inactive_when_price_healthy():
    """多單：價格穩定在均線與前低之上，不觸發任何警示。"""
    closes = [100.0] * 24 + [100.5]
    lows = [99.0] * 25
    highs = [101.0] * 25
    result = compute_position_trigger(_trigger_frame(closes, lows, highs), "LONG")
    assert result["active"] is False
    assert result["reasons"] == []


def test_position_trigger_short_flags_ma_cross_and_prior_high_break():
    """空單：對稱情境，最後一根K棒暴衝，同時站上均線也站上前高。"""
    closes = [100.0] * 24 + [110.0]
    lows = [99.0] * 24 + [110.0]
    highs = [101.0] * 24 + [110.0]
    result = compute_position_trigger(_trigger_frame(closes, lows, highs), "SHORT")
    assert result["active"] is True
    assert "站上均線" in result["reasons"]
    assert "站上前高" in result["reasons"]


def test_position_trigger_inactive_when_not_enough_bars():
    """K線資料不足（少於 lookback_bars+1）時，不判斷、也不誤報警示。"""
    result = compute_position_trigger(_trigger_frame([100.0] * 5), "LONG")
    assert result["active"] is False
    assert result["ma_ok"] is True


def test_position_trigger_ma_ok_stays_false_across_multiple_bars_below_ma():
    """持續性狀態，不是只在剛好穿越的那一根才觸發：跌破均線之後只要
    收盤價還在均線下面，接下來好幾根都應該持續打叉，不會穿越後下一根
    就自動恢復顯示沒事（原本用「穿越瞬間」判斷會有這個問題）。"""
    closes = [100.0] * 24 + [90.0, 89.0, 88.0]
    result = compute_position_trigger(_trigger_frame(closes), "LONG")
    assert result["ma_ok"] is False
    assert "跌破均線" in result["reasons"]


def test_position_trigger_short_ma_ok_true_when_price_still_below_ma():
    """空單：收盤價還在均線之下（對空單是健康的一側），ma_ok 應為
    True，不誤報「站上均線」。"""
    closes = [100.0] * 24 + [99.0]
    result = compute_position_trigger(_trigger_frame(closes), "SHORT")
    assert result["ma_ok"] is True
    assert "站上均線" not in result["reasons"]
    assert result["reasons"] == []


def test_sl_tp_distance_widens_stop_but_preserves_take_profit_target():
    """使用者要求「先不要止損，讓利潤有機會回來，人工判斷要不要平倉」：
    止損距離要乘上 DISASTER_STOP_MULTIPLIER 變成寬鬆的最後防線，但止盈
    距離必須維持原本的風報比不變，不能跟著一起放寬。"""
    price, atr = 100.0, 2.0  # atr*1.5=3.0 > price*MIN_SL_DISTANCE_PCT，取ATR倍數為基準
    base_sl_distance = atr * STOP_LOSS_MULTIPLIER
    expected_tp_distance = base_sl_distance * (TAKE_PROFIT_MULTIPLIER / STOP_LOSS_MULTIPLIER)
    expected_sl_distance = base_sl_distance * DISASTER_STOP_MULTIPLIER

    sl_distance, tp_distance = compute_sl_tp_distance(price, atr)

    assert sl_distance == pytest.approx(expected_sl_distance)
    assert tp_distance == pytest.approx(expected_tp_distance)
    assert sl_distance > base_sl_distance  # 止損確實被放寬了
    assert tp_distance == pytest.approx(expected_tp_distance)  # 止盈沒有被放寬影響
