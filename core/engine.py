import asyncio
import time
import ccxt.async_support as ccxt
import pandas as pd
from typing import Dict, List
from core.config import DEFAULT_SYMBOLS, MAX_SLOTS, TRADE_AMOUNT_USDT, TREND_FILTER_EMA_PERIOD
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
        self.ema_200_1h_cache: Dict[str, float] = {}
        self.last_1h_cache_time: float = 0.0

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

    async def update_1h_trend_cache(self):
        """10 分鐘才抓取一次 1h 大週期數據，避免頻繁調用 API Rate Limit"""
        now = time.time()
        if now - self.last_1h_cache_time < 600 and self.ema_200_1h_cache:
            return

        for symbol in DEFAULT_SYMBOLS:
            df_1h = await self.fetch_klines(symbol, timeframe="1h", limit=100)
            if not df_1h.empty and len(df_1h) >= 30:
                ema_val = df_1h['close'].ewm(span=min(len(df_1h), TREND_FILTER_EMA_PERIOD), adjust=False).mean().iloc[-1]
                self.ema_200_1h_cache[symbol] = float(ema_val)
            await asyncio.sleep(0.1)
        self.last_1h_cache_time = now

    async def _main_loop(self):
        while self.is_running:
            try:
                # 1. 更新實時價格
                await self.update_market_prices()

                # 2. 更新與執行持倉部位（包含動態追蹤止利 0.8x/1.5x ATR 與 SL/TP 觸發）
                self.account.update_positions(self.tickers)

                # 3. 10分鐘定時刷新 1h EMA200 快取 (防止 API Rate Limit 封鎖)
                await self.update_1h_trend_cache()

                # 4. 開倉訊號檢查
                if len(self.account.positions) < MAX_SLOTS:
                    for symbol in DEFAULT_SYMBOLS:
                        if symbol in self.account.positions:
                            continue

                        # 4.1 低流動性過濾
                        vol_24h = self.ticker_volumes.get(symbol, 0.0)
                        if vol_24h > 0 and vol_24h < 500000.0:
                            continue

                        df = await self.fetch_klines(symbol, timeframe="5m", limit=100)
                        if df.empty or len(df) < 50:
                            continue

                        # 取出 1h 快取值
                        ema_200_1h = self.ema_200_1h_cache.get(symbol)

                        # 防插針價格選擇 (SpikeFilter_L2)
                        if 'close_price_spike_filtered' in df.columns and not pd.isna(df.iloc[-1]['close_price_spike_filtered']):
                            price = float(df.iloc[-1]['close_price_spike_filtered'])
                        else:
                            price = float(df.iloc[-1]['close'])

                        self.tickers[symbol] = price

                        # 4.2 計算真實動態 ATR (非固定 1.5%)
                        high = df['high']
                        low = df['low']
                        close = df['close']
                        tr1 = high - low
                        tr2 = (high - close.shift(1)).abs()
                        tr3 = (low - close.shift(1)).abs()
                        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                        real_atr = tr.rolling(window=10).mean().iloc[-1]
                        if pd.isna(real_atr) or real_atr <= 0:
                            real_atr = price * 0.015

                        # 4.3 防插針檢查 (5x 真實 ATR)
                        recent_high = df.iloc[-1]['high']
                        recent_low = df.iloc[-1]['low']
                        candle_spread = recent_high - recent_low

                        if candle_spread > (real_atr * 5.0):
                            self.account.log(f"🛡️ [防插針觸發] {symbol} 最新 K 線振幅過大 ({candle_spread:.4f} > 5x 真實ATR)，過濾潛在假突破訊號", "WARNING")
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
                                atr=sig.get("atr", real_atr)
                            )

                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                self.account.log(f"🌐 網路連線暫時中斷，正在自動重試... ({type(e).__name__})", "WARNING")
                await asyncio.sleep(5)
            except ccxt.ExchangeError as e:
                self.account.log(f"⚠️ 交易所 API 權限或請求異常: {str(e)}", "WARNING")
                await asyncio.sleep(5)
            except Exception as e:
                self.account.log(f"⚠️ 引擎運作異常: {str(e)}", "WARNING")
                await asyncio.sleep(5)

# Singleton global instance
engine = TradingEngine()

