import asyncio
import pandas as pd
from core.paper_account import PaperAccount
import core.services.entry_firewall

# mock firewall
async def mock_validate(*args, **kwargs):
    pass
core.services.entry_firewall.validate_account_entry = mock_validate

class MockAccount(PaperAccount):
    def __init__(self):
        super().__init__()
        self.balance = 1000.0
        self.positions = {}
        self.closing_lock = set()
        self.pending_limit_orders = {}
        df = pd.DataFrame([
            {'timestamp': 1000, 'open': 1.0, 'high': 1.1, 'low': 0.9, 'close': 1.0, 'atr': 0.1, 'ma3': 1.0, 'ma15': 1.0, 'kc_upper': 1.2, 'kc_lower': 0.8, 'kc_middle': 1.0},
            {'timestamp': 61000, 'open': 1.0, 'high': 1.1, 'low': 0.9, 'close': 1.0, 'atr': 0.1, 'ma3': 1.0, 'ma15': 1.0, 'kc_upper': 1.2, 'kc_lower': 0.8, 'kc_middle': 1.0},
            {'timestamp': 121000, 'open': 1.05, 'high': 1.1, 'low': 0.9, 'close': 0.95, 'atr': 0.1, 'ma3': 0.95, 'ma15': 1.02, 'kc_upper': 1.2, 'kc_lower': 0.8, 'kc_middle': 1.0},
            {'timestamp': 181000, 'open': 0.95, 'high': 0.96, 'low': 0.85, 'close': 0.86, 'atr': 0.1, 'ma3': 0.88, 'ma15': 1.01, 'kc_upper': 1.2, 'kc_lower': 0.8, 'kc_middle': 1.0},
            {'timestamp': 241000, 'open': 0.86, 'high': 0.9, 'low': 0.8, 'close': 0.85, 'atr': 0.1, 'ma3': 0.8, 'ma15': 1.0, 'kc_upper': 1.2, 'kc_lower': 0.8, 'kc_middle': 1.0}
        ])
        df.attrs['timeframe_ms'] = 60000
        self.test_df = df
    
    async def entry_frame_provider(self, symbol):
        return self.test_df
        
    def log(self, message, level="INFO"):
        print(f"[{level}] {message}")

async def main():
    acc = MockAccount()
    print(">>> 測試觸發開多，但資料為大陰線且在軌道內 (模擬 17:47 情況)")
    result = await acc.open_position(
        symbol="LOBSTERUSDT",
        side="LONG",
        price=0.86,
        amount_usdt=100.0,
        sl=0.8,
        tp=1.0,
        reason="MOCK_TEST",
        entry_context={}
    )
    print(f">>> 執行結果: {result} (預期為 False)")

if __name__ == '__main__':
    asyncio.run(main())
