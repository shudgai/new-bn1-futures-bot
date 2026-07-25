import asyncio
import ccxt.async_support as ccxt
import pandas as pd
from typing import Dict, List
from core.config import DEFAULT_SYMBOLS, MAX_SLOTS, TRADE_AMOUNT_USDT
from core.strategy import SuperTrendKeltnerStrategy
from core.paper_account import PaperAccount

class TradingEngine:
    def __init__(self):
        self.exchange = ccxt.binanceusdm({"enableRateLimit": True})
        self.strategy = SuperTrendKeltnerStrategy()
        self.account = PaperAccount()
        self.is_running = False
        self.task: asyncio.Task = None
        self.tickers: Dict[str, float] = {}
        self.ticker_volumes: Dict[str, float] = {} # 24小時成交量 (USDT)
        self.cooldowns: Dict[str, float] = {}

    async def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.account.log("▶️ 量化交易機器人啟動 (台北時間模式 / 防插針防重複平倉防低流動性啟用)")
        self.task = asyncio.create_task(self._main_loop())

    async def stop(self):
        if not self.is_running:
            return
        self.is_running = False
        if self.task:
            self.task.cancel()
        await self.exchange.close()
        self.account.log("⏹️ 量化交易機器人已停止")

    async def fetch_klines(self, symbol: str, timeframe: str = "5m", limit: int = 100) -> pd.DataFrame:
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            return df
        except Exception as e:
            return pd.DataFrame()

    async def update_market_prices(self):
        try:
            tickers = await self.exchange.fetch_tickers(DEFAULT_SYMBOLS)
            for sym, t in tickers.items():
                if 'last' in t and t['last'] is not None:
                    self.tickers[sym] = float(t['last'])
                if 'quoteVolume' in t and t['quoteVolume'] is not None:
                    self.ticker_volumes[sym] = float(t['quoteVolume'])
        except Exception as e:
            pass

    async def _main_loop(self):
        while self.is_running:
            try:
                await self.update_market_prices()
                self.account.update_positions(self.tickers)

                # Check entry signals if current active slots < MAX_SLOTS
                if len(self.account.positions) < MAX_SLOTS:
                    for symbol in DEFAULT_SYMBOLS:
                        if symbol in self.account.positions:
                            continue

                        # 1. 低流動性過濾 (避免低流動性山寨幣出現極大滑點或假報價)
                        vol_24h = self.ticker_volumes.get(symbol, 0.0)
                        if vol_24h > 0 and vol_24h < 500000.0: # 24h交易量低於 50萬 USDT 跳過
                            continue

                        df = await self.fetch_klines(symbol, timeframe="5m", limit=100)
                        if df.empty or len(df) < 50:
                            continue

                        df_1h = await self.fetch_klines(symbol, timeframe="1h", limit=100)
                        ema_200_1h = None
                        if not df_1h.empty and len(df_1h) >= 50:
                            ema_200_1h = df_1h['close'].ewm(span=min(len(df_1h), 200), adjust=False).mean().iloc[-1]

                        # 1. 防插針價格選擇 (SpikeFilter_L2 優先選用 close_price_spike_filtered)
                        if 'close_price_spike_filtered' in df.columns and not pd.isna(df.iloc[-1]['close_price_spike_filtered']):
                            price = float(df.iloc[-1]['close_price_spike_filtered'])
                        else:
                            price = float(df.iloc[-1]['close'])

                        self.tickers[symbol] = price

                        # 2. 防插針檢查 (Anti-Spike Filter)
                        recent_high = df.iloc[-1]['high']
                        recent_low = df.iloc[-1]['low']
                        atr = df.iloc[-1]['close'] * 0.015
                        candle_spread = recent_high - recent_low

                        if candle_spread > (atr * 5.0):
                            self.account.log(f"🛡️ [防插針觸發] {symbol} 最新 K 線振幅過大 ({candle_spread:.4f} > 5x ATR)，過濾潛在假突破訊號", "WARNING")
                            continue

                        sig = self.strategy.evaluate_signal(df, ema_200_1h=ema_200_1h)
                        if sig["action"] in ["BUY", "SELL"]:
                            side = sig["side"]
                            sl = sig["sl"]
                            tp = sig["tp"]
                            reason = sig["reason"]

                            self.account.open_position(
                                symbol=symbol,
                                side=side,
                                price=price,
                                amount_usdt=TRADE_AMOUNT_USDT,
                                sl=sl,
                                tp=tp,
                                reason=reason,
                                atr=sig.get("atr", 0.0)
                            )

                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.account.log(f"⚠️ 引擎異常: {str(e)}", "WARNING")
                await asyncio.sleep(5)

# Singleton global instance
engine = TradingEngine()

