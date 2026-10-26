"""
單元與整合測試：雙槽位 (2-Slots) 架構、資金分配與 1000PEPE 特殊精度換算
Path: tests/test_dual_slot_architecture.py
"""

import pytest
import numpy as np
import pandas as pd
from decimal import Decimal

from core.config import DEFAULT_SYMBOLS, MAX_SLOTS, SLOT_1_SYMBOL, SLOT_2_SYMBOL, SLOT_CONFIG
from core.symbol_aliases import to_canonical_display_symbol, to_binance_usdm_symbol, CANONICAL_LOBSTER_SYMBOL, CANONICAL_PEPE_SYMBOL
from core.services.order_sizing import calculate_target_slot_capital, raw_order_qty, positive
from core.services.dual_slot_chart_generator import (
    setup_chinese_font,
    compute_slot_indicators,
    generate_dual_slot_chart,
)


def test_dual_slot_configuration():
    """驗證雙槽位標的與常數定義嚴格吻合"""
    assert MAX_SLOTS == 2
    assert SLOT_1_SYMBOL == "龙虾/USDT"
    assert SLOT_2_SYMBOL == "1000PEPE/USDT"
    assert SLOT_CONFIG["SLOT_1"] == "龙虾/USDT"
    assert SLOT_CONFIG["SLOT_2"] == "1000PEPE/USDT"
    assert DEFAULT_SYMBOLS == ["龙虾/USDT", "1000PEPE/USDT"]
    
    # 確保嚴格使用簡體字「龙虾/USDT」，不被轉為英文
    assert to_canonical_display_symbol("龙虾/USDT") == "龙虾/USDT"
    assert to_canonical_display_symbol("龍蝦/USDT") == "龙虾/USDT"
    assert to_canonical_display_symbol("LOBSTER/USDT") == "龙虾/USDT"
    
    # 確保 1000PEPE 映射正確
    assert to_canonical_display_symbol("1000PEPE/USDT") == "1000PEPE/USDT"
    assert to_canonical_display_symbol("PEPE/USDT") == "1000PEPE/USDT"


def test_dynamic_capital_allocation_half_balance():
    """驗證動態資金分配：Target_Capital = Account_Balance / 2"""
    # 餘額 100 U -> 每槽 50 U
    assert calculate_target_slot_capital(100.0, slot_count=2) == 50.0
    # 餘額 150 U -> 每槽 75 U
    assert calculate_target_slot_capital(150.0, slot_count=2) == 75.0
    # 餘額 333.33 U -> 每槽 166.665 U (四捨五入)
    assert calculate_target_slot_capital(333.33, slot_count=2) == 166.665
    # 餘額 0 U 或負數
    assert calculate_target_slot_capital(0.0, slot_count=2) == 0.0
    assert calculate_target_slot_capital(-50.0, slot_count=2) == 0.0


def test_1000pepe_quantity_and_notional_math():
    """
    驗證 1000PEPE 特殊單位換算：
    合約乘數 1 單位 = 1000 PEPE，報價即為 1000 PEPE 的價格。
    保證金 = Notional / Leverage，數量不可被額外除以或乘以 1000！
    """
    account_balance = 200.0
    target_capital = calculate_target_slot_capital(account_balance, slot_count=2)  # 100.0 U
    leverage = 5
    pepe_price = 0.0095  # 0.0095 USDT / 1000 PEPE

    raw_qty = raw_order_qty(target_capital, leverage, pepe_price, symbol="1000PEPE/USDT")
    notional = float(raw_qty) * pepe_price
    actual_margin = notional / leverage

    # 斷言名義價值 = 100 * 5 = 500 U
    assert pytest.approx(notional, 0.0001) == 500.0
    # 斷言實際保證金精準等於 Target_Capital (100.0 U)
    assert pytest.approx(actual_margin, 0.0001) == 100.0
    # 數量約為 52631.5789 手 (每手 1000 PEPE)
    assert pytest.approx(float(raw_qty), 0.01) == 52631.58


def test_dual_slot_chart_generator_with_chinese_font():
    """驗證雙圖繪圖模組：Slot 1 龙虾/USDT 與 Slot 2 1000PEPE/USDT 同步生成且中文字型正常"""
    font = setup_chinese_font()
    assert font is not None  # 確保抓取到中文字型

    n = 30
    df1 = pd.DataFrame({
        "open": 0.019 + np.linspace(0, 0.001, n),
        "high": 0.0192 + np.linspace(0, 0.001, n),
        "low": 0.0188 + np.linspace(0, 0.001, n),
        "close": 0.0191 + np.linspace(0, 0.001, n),
        "volume": [1000.0] * n,
    })
    df2 = pd.DataFrame({
        "open": 0.0095 - np.linspace(0, 0.0005, n),
        "high": 0.0097 - np.linspace(0, 0.0005, n),
        "low": 0.0093 - np.linspace(0, 0.0005, n),
        "close": 0.0094 - np.linspace(0, 0.0005, n),
        "volume": [50000.0] * n,
    })

    # 驗證指標計算獨立性
    df1_calc = compute_slot_indicators(df1)
    df2_calc = compute_slot_indicators(df2)
    assert "ema_fast" in df1_calc.columns and "kc_upper" in df1_calc.columns and "rsi" in df1_calc.columns
    assert "ema_fast" in df2_calc.columns and "kc_upper" in df2_calc.columns and "rsi" in df2_calc.columns
    assert df1_calc["close"].iloc[-1] != df2_calc["close"].iloc[-1]

    # 模擬持倉
    pos1 = {"side": "LONG", "entry_price": 0.0190, "sl": 0.0187, "tp": 0.0196, "margin": 50.0, "leverage": 5}
    pos2 = {"side": "SHORT", "entry_price": 0.0096, "sl": 0.0098, "tp": 0.0092, "margin": 50.0, "leverage": 5}

    img_bytes = generate_dual_slot_chart(
        slot1_df=df1,
        slot2_df=df2,
        slot1_pos=pos1,
        slot2_pos=pos2,
        output_filepath=None,
    )
    assert isinstance(img_bytes, bytes)
    assert len(img_bytes) > 50000  # 確保產出真實清晰的高解析度圖檔
