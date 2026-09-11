from collections import deque

# Mock Engine class
class MockEngine:
    def __init__(self):
        self._btc_price_samples = deque(maxlen=200)
        self._btc_flash_crash_last_triggered_at = 0.0
        self.account = self
        self.positions = {"ADA/USDT": {"side": "LONG"}, "DOGE/USDT": {"side": "SHORT"}}
        self.tickers = {"ADA/USDT": 1.0, "DOGE/USDT": 0.5}
        self.closed_positions = []
        self.logs = []
        
    def log(self, msg, level):
        self.logs.append(msg)
        print(f"[{level}] {msg}")
        
    async def close_position(self, sym, price, reason, is_manual=False):
        self.closed_positions.append((sym, price, reason, is_manual))
        print(f"Closed {sym} at {price} due to {reason} (manual={is_manual})")

    def simulate_tick(self, now, btc_live):
        BTC_FLASH_CRASH_DROP_PCT = 0.5
        BTC_FLASH_CRASH_WINDOW_SEC = 5.0
        
        self._btc_price_samples.append((now, btc_live))
        cutoff = now - BTC_FLASH_CRASH_WINDOW_SEC
        ref_price = None
        for ts, px in self._btc_price_samples:
            if ts <= cutoff:
                ref_price = px
            else:
                break
        
        if ref_price and ref_price > 0:
            drop_pct = (ref_price - btc_live) / ref_price * 100.0
            cooldown_ok = now - self._btc_flash_crash_last_triggered_at > 60.0
            if drop_pct >= BTC_FLASH_CRASH_DROP_PCT and cooldown_ok:
                self._btc_flash_crash_last_triggered_at = now
                long_positions = [sym for sym, pos in self.positions.items() if str(pos.get("side", "")).upper() == "LONG"]
                if long_positions:
                    self.log(f"🚨 [BTC插針緊急平倉] BTC {BTC_FLASH_CRASH_WINDOW_SEC:.0f}秒內急跌 {drop_pct:.2f}% (≥{BTC_FLASH_CRASH_DROP_PCT}%)，立即市價平掉 {len(long_positions)} 個多單：" + ", ".join(long_positions), "DANGER")
                    import asyncio
                    for sym in list(long_positions):
                        close_price = self.tickers.get(sym, 0.0)
                        if close_price > 0:
                            asyncio.run(self.close_position(sym, close_price, f"BTC插針緊急平倉 ({drop_pct:.2f}%↓/{BTC_FLASH_CRASH_WINDOW_SEC:.0f}s)", is_manual=True))

engine = MockEngine()
# T=0: BTC at 60000
engine.simulate_tick(100.0, 60000)
# T=2: BTC at 59900 (drop is 100/60000 = 0.16%)
engine.simulate_tick(102.0, 59900)
# T=4: BTC at 59600 (drop is 400/60000 = 0.66% from T=0)
engine.simulate_tick(104.0, 59600)
print("Finished test 1. Number of closed positions:", len(engine.closed_positions))

# T=10: BTC drops again to 58000, shouldn't trigger because of cooldown
engine.simulate_tick(110.0, 58000)
print("Finished test 2. Number of closed positions:", len(engine.closed_positions))

