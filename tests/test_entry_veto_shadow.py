import unittest
import pandas as pd
import numpy as np
import os
import json
from core.services.entry_veto_shadow_logger import log_entry_veto_shadow, _LOG_PATH

class TestEntryVetoShadow(unittest.TestCase):
    def setUp(self):
        if os.path.exists(_LOG_PATH):
            os.remove(_LOG_PATH)

    def tearDown(self):
        if os.path.exists(_LOG_PATH):
            os.remove(_LOG_PATH)

    def test_log_entry_veto_shadow(self):
        df = pd.DataFrame([
            {'timestamp': 100000, 'open': 100, 'high': 105, 'low': 95, 'close': 102, 'atr': 5, 'ma3': 90, 'ma5': 90, 'ma15': 90, 'kc_upper': 110, 'kc_lower': 80, 'kc_middle': 85, 'is_closed': True},
            {'timestamp': 160000, 'open': 102, 'high': 110, 'low': 101, 'close': 108, 'atr': 5, 'ma3': 92, 'ma5': 92, 'ma15': 90, 'kc_upper': 112, 'kc_lower': 82, 'kc_middle': 87, 'is_closed': True},
            {'timestamp': 220000, 'open': 108, 'high': 108, 'low': 100, 'close': 105, 'atr': 5, 'ma3': 95, 'ma5': 95, 'ma15': 90, 'kc_upper': 115, 'kc_lower': 85, 'kc_middle': 90, 'is_closed': True},
            {'timestamp': 280000, 'open': 105, 'high': 106, 'low': 104, 'close': 105, 'atr': 5, 'ma3': 96, 'ma5': 96, 'ma15': 91, 'kc_upper': 116, 'kc_lower': 86, 'kc_middle': 91, 'is_closed': False}
        ])
        df.attrs['timeframe_ms'] = 60000
        
        # 105 open, current price 102 => adverse body 3 for LONG
        decision = {'type': 'SECOND_BAR_OUTSIDE_LONG', 'reason': 'INITIAL_BREAKOUT'}
        log_entry_veto_shadow(df, 102.0, 'LONG', decision, '龙虾/USDT')

        self.assertTrue(os.path.exists(_LOG_PATH))
        with open(_LOG_PATH, 'r') as f:
            lines = f.readlines()
            self.assertEqual(len(lines), 1)
            event = json.loads(lines[0])
            self.assertEqual(event['symbol'], '龙虾/USDT')
            self.assertEqual(event['side'], 'LONG')
            self.assertEqual(event['current_price'], 102.0)
            self.assertEqual(event['adverse_body'], 3.0)
            self.assertEqual(event['adverse_body_atr'], 3.0 / 5.0)
            
            filters = event['candidate_filter_results']
            self.assertTrue(filters['ANY_COLOR_BLOCK'])
            self.assertTrue(filters['ATR_0_50_BLOCK'])
            self.assertFalse(filters['ATR_1_00_BLOCK'])
            # closed_ma5 = 95, current_price = 102 (not < 95)
            # closed_kc_middle = 90, current_price = 102 (not < 90)
            self.assertFalse(filters['S3_BLOCK'])
            self.assertFalse(filters['COMPOSITE_0_50_BLOCK'])

if __name__ == '__main__':
    unittest.main()
