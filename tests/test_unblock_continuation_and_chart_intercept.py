import pandas as pd
import pytest
from core.gates.meme_coin_gate import MemeCoinGate
from core.services.entry_contract import evaluate_entry_contract
from services.api import parse_intercept_info, map_block_reason


def make_frame(rows):
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 60000
    df.attrs['entry_finality_verified'] = True
    return df


class TestUnblockContinuationAndChartIntercept:
    """驗證無論龍蝦還是 CAP，均解除延續開倉限制，且圖表攔截標記正確解析。"""

    def test_cap_and_lobster_forced_continuation_unblocked_long(self):
        """驗證 CAP 與龍蝦在已破軌、位於外軌強勢區且 MA5 向上時，順暢觸發延續開倉（強制上車）。"""
        for sym in ('CAP/USDT', '龍蝦/USDT', 'LOBSTER/USDT'):
            df = make_frame([
                {'open': 10.0, 'close': 10.0, 'high': 10.2, 'low': 9.8,
                 'ma5': 10.0, 'ma15': 9.8, 'kc_middle': 10.0, 'kc_upper': 10.5, 'kc_lower': 9.5,
                 'atr': 1.0, 'timestamp': 1000, 'is_closed': True},
                {'open': 10.8, 'close': 11.2, 'high': 11.5, 'low': 10.7,
                 'ma5': 10.4, 'ma15': 9.9, 'kc_middle': 10.1, 'kc_upper': 10.6, 'kc_lower': 9.4,
                 'atr': 1.0, 'timestamp': 61000, 'is_closed': False},
            ])
            passed, reason, meta = MemeCoinGate.evaluate_meme_entry(
                df, quote=11.2, side='LONG', symbol=sym
            )
            assert passed is True, f"{sym} should pass meme continuation"
            assert reason == 'MEME_FORCED_CONTINUATION_ENTRY_LONG'
            assert meta['entry_type'] == 'MEME_FORCED_CONTINUATION'

    def test_cap_and_lobster_forced_continuation_unblocked_short(self):
        """驗證 CAP 與龍蝦在已摜破下軌、位於下軌強勢區且 MA5 向下時，順暢觸發空單延續開倉（強制上車）。"""
        for sym in ('CAP/USDT', '龍蝦/USDT', 'LOBSTER/USDT'):
            df = make_frame([
                {'open': 10.0, 'close': 10.0, 'high': 10.2, 'low': 9.8,
                 'ma5': 10.0, 'ma15': 10.1, 'kc_middle': 10.0, 'kc_upper': 10.6, 'kc_lower': 9.4,
                 'atr': 1.0, 'timestamp': 1000, 'is_closed': True},
                {'open': 9.2, 'close': 8.8, 'high': 9.3, 'low': 8.7,
                 'ma5': 9.6, 'ma15': 10.0, 'kc_middle': 9.9, 'kc_upper': 10.5, 'kc_lower': 9.3,
                 'atr': 1.0, 'timestamp': 61000, 'is_closed': False},
            ])
            passed, reason, meta = MemeCoinGate.evaluate_meme_entry(
                df, quote=8.8, side='SHORT', symbol=sym
            )
            assert passed is True, f"{sym} should pass meme short continuation"
            assert reason == 'MEME_FORCED_CONTINUATION_ENTRY_SHORT'
            assert meta['entry_type'] == 'MEME_FORCED_CONTINUATION'

    def test_evaluate_entry_contract_with_general_continuation_code(self):
        """驗證 evaluate_entry_contract 以 TRIGGER_C_CONTINUATION 或 KC_2BAR 查詢時，CAP 能順利以 MEME 延續開倉授權。"""
        rows = [
            {'open': 10.0, 'close': 10.0, 'high': 10.2, 'low': 9.8,
             'ma5': 10.0, 'ma15': 9.8, 'kc_middle': 10.0, 'kc_upper': 10.5, 'kc_lower': 9.5,
             'atr': 1.0, 'timestamp': 1000 + i * 60000, 'is_closed': True}
            for i in range(12)
        ]
        rows.append({
            'open': 10.8, 'close': 11.2, 'high': 11.5, 'low': 10.7,
            'ma5': 10.4, 'ma15': 9.9, 'kc_middle': 10.1, 'kc_upper': 10.6, 'kc_lower': 9.4,
            'atr': 1.0, 'timestamp': 1000 + 12 * 60000, 'is_closed': True,
        })
        df = make_frame(rows)
        diagnostics = {}
        decision = evaluate_entry_contract(
            df, price=11.2, code='TRIGGER_C_CONTINUATION', symbol='CAP/USDT', diagnostics=diagnostics
        )
        print("DIAGNOSTICS:", diagnostics)
        assert decision is not None
        assert decision['action'] == 'ENTER'
        assert decision['type'] == 'MEME_FORCED_CONTINUATION_ENTRY_LONG'

    def test_parse_intercept_info_translations(self):
        """驗證 K 線圖攔截原因轉譯功能正確提供繁體中文與詳細提示。"""
        sample_cases = [
            ('BLOCKED_LONG_ON_BEARISH_TREND', '空頭趨勢禁開多'),
            ('BLOCKED_SHORT_ON_BULLISH_TREND', '多頭趨勢禁開空'),
            ('WAIT_NEW_KC_BREAKOUT', '等待有效破軌'),
            ('BLOCKED_BY_MA5_DOWNWARD_SLOPE', 'MA5下彎熄火'),
            ('BLOCKED_BY_MA5_UPWARD_SLOPE', 'MA5上勾衰退'),
            ('BLOCKED_BY_MA5_SLOPE_FLAT_FOR_LONG', 'MA5走平未上翹'),
            ('VETO_CANNOT_LONG_WHEN_KC_DOWN', 'KC通道向下禁多'),
            ('VETO_CANNOT_SHORT_WHEN_KC_UP', 'KC通道向上禁空'),
        ]
        for raw_code, expected_short in sample_cases:
            short_reason, detail = parse_intercept_info(raw_code)
            assert short_reason == expected_short, f"Mismatch for {raw_code}"
            assert len(detail) > 0
