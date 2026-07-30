import asyncio
import re
import time
import ccxt.async_support as ccxt
import pandas as pd
from typing import Dict, List
from core.config import (
    DEFAULT_SYMBOLS, MAX_SLOTS, TRADE_AMOUNT_USDT, TREND_FILTER_EMA_PERIOD,
    PULLBACK_TIMEOUT_MINUTES, ENTRY_LIMIT_TIMEOUT_SEC,
    PULLBACK_TARGET_MAX_DRIFT_ATR, PULLBACK_RECLAIM_MIN_ATR,
    PULLBACK_RETRY_COOLDOWN_SEC, PULLBACK_TARGET_DEPTH,
    SYMBOL_ROTATION_INTERVAL_SEC,
    UNHEALTHY_SYMBOL_CHECK_INTERVAL_SEC,
    BINANCE_API_KEY, BINANCE_SECRET, get_position_multiplier, MIN_TRADE_USDT,
    MIN_SCORE_THRESHOLD, USE_TESTNET,
    ADX_QUALITY_MIN, ADX_DECLINE_LOOKBACK_BARS_1H, TEST_BUDGET_CAP_USDT,
    ENTRY_DISABLED_SYMBOLS,
)
from core.strategy import SuperTrendKeltnerStrategy, compute_sl_tp_distance
from core.testnet_account import BinanceTestnetAccount
from core.symbol_rotation import SymbolRotation
from core.indicators import drop_unclosed_candle, compute_position_trigger

class TradingEngine:
    def __init__(self):
        # 真實市場公開行情與8006獨立Testnet執行帳戶分流。
        self.exchange = ccxt.binanceusdm({"enableRateLimit": True})
        self.execution_exchange = ccxt.binanceusdm({
            "apiKey": BINANCE_API_KEY,
            "secret": BINANCE_SECRET,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        self.execution_exchange.set_sandbox_mode(USE_TESTNET)
        self.strategy = SuperTrendKeltnerStrategy()
        self.account = BinanceTestnetAccount(self.execution_exchange)
        self.symbol_rotation = SymbolRotation(self.account)
        self.is_running = False
        self.task: asyncio.Task = None
        self.rotation_task: asyncio.Task = None
        self.analysis_task: asyncio.Task = None
        self.analysis_event = asyncio.Event()
        self.account.on_trade_closed = self.request_trade_analysis
        self.tickers: Dict[str, float] = {}
        self.ticker_volumes: Dict[str, float] = {}  # 24小時成交量 (USDT)
        self.last_ticker_success_ts: float = time.time()
        self._last_stale_ticker_log: float = 0.0
        self.ema_50_1h_cache: Dict[str, float] = {}
        # 大週期（1h）本身動能是不是也在衰退，用同一批 update_1h_trend_
        # cache() 已經抓到的1h K線算 ADX，判斷「不只是5分K的小趨勢要提防，
        # 連大方向本身都已經在做頭/做底」——5分K的新鮮度/ADX檢查看不到
        # 這一層。
        self.adx_1h_declining_cache: Dict[str, bool] = {}
        # 個幣 1h SuperTrend 方向快取（1=多頭 / -1=空頭）
        self.st_direction_1h_cache: Dict[str, int] = {}
        # BTC 大盤方向：1h SuperTrend 方向 + 翻轉後已過幾根 1h K棒
        self.btc_1h_st_direction: int = 0      # 0=未知, 1=多頭, -1=空頭
        self.btc_1h_st_flip_age: int = 999     # 翻轉後已過幾根 1h K棒（999=尚未初始化）
        self.last_1h_cache_time: float = 0.0
        # 兩階段回踩：突破先進候選池，觸價後等待 1m 收盤反轉確認，才送
        # 短效 Post-Only 限價單。候選與交易所掛單分開追蹤。
        self.pending_pullback_candidates: Dict[str, dict] = {}
        self._pullback_retry_after: Dict[str, float] = {}
        self.last_signal_progress_log_at: float = 0.0
        # 持倉手動平倉參考指標（跌破/站上關鍵均線、跌破前低/站上前高）：
        # 純粹給使用者按「平倉」前參考用，不是自動出場條件，不影響
        # 止損/止利/24h時間過濾等既有的自動平倉邏輯。
        self.position_triggers: Dict[str, dict] = {}
        self.trigger_task: asyncio.Task = None
        # 歷史係數降分 log 節流：同一個 symbol 在績效數據沒變的情況下，
        # 每輪主迴圈都會重算出同樣的係數/分數，導致同一則訊息每 5~10 秒
        # 就重複印一次（實測 ZEC/USDT 這樣連續洗了好幾分鐘）。只記錄狀態
        # 有變化時才印，同樣的狀態只顯示一次。
        self._history_coeff_logged: Dict[str, tuple] = {}

    @staticmethod
    def _format_signal_progress(
        symbol: str,
        signal: dict,
        current_direction: str,
    ) -> str:
        """將策略結果壓縮成適合系統日誌的一行進度。"""
        score = signal.get("score")
        if score is None:
            match = re.search(r"Score\((\d+)\)", signal.get("reason", ""))
            score = int(match.group(1)) if match else 0
        direction_text = {"LONG": "多單", "SHORT": "空單"}.get(
            current_direction, "雙向"
        )
        action = signal.get("action", "HOLD")
        reason = signal.get("reason", "")
        if action in ("BUY", "SELL"):
            stage = "符合立即開倉"
        elif action == "WAIT_PULLBACK":
            stage = signal.get(
                "confirmation_reason", "等待回調至KC區後二次確認"
            )
        elif "BTC_1h_ST_JustFlipped" in reason:
            stage = "BTC剛翻轉緩衝期過濾"
        elif "BTC_Regime" in reason:
            stage = "BTC大盤方向不符"
        elif "Symbol_1h_ST" in reason:
            stage = "個幣1h趨勢不符"
        elif "ADX_Too_Low" in reason:
            stage = "ADX過低過濾"
        elif "KC_Breakout" in reason:
            stage = "待KC突破"
        elif "SuperTrend_Stale" in reason:
            stale = re.search(r"SuperTrend_Stale\((\d+)\)", reason)
            stage = f"SuperTrend過期{stale.group(1)}根" if stale else "SuperTrend過期"
        elif "ADX_Declining_Exhaustion" in reason:
            stage = "ADX動能衰退過濾"
        elif "Price_Overextended" in reason:
            overext_match = re.search(r"Price_Overextended\(([\d.]+x_ATR)\)", reason)
            stage = f"價格乖離過大{overext_match.group(1)}" if overext_match else "價格乖離過大"
        elif "1h_Trend_Declining" in reason:
            stage = "大週期動能衰退過濾"
        elif "EMA20" in reason or "1h_Trend" in reason:
            stage = "趨勢方向不符"
        elif "ATR_Too_High" in reason:
            atr_match = re.search(r"ATR_Too_High\(([\d.]+%)\)", reason)
            stage = f"波動過大{atr_match.group(1)}過濾" if atr_match else "波動過大過濾"
        elif "ATR_Too_Low" in reason:
            atr_match = re.search(r"ATR_Too_Low\(([\d.]+%)\)", reason)
            stage = f"波動過低{atr_match.group(1)}過濾" if atr_match else "波動過低過濾"
        elif "Volume" in reason:
            stage = "量能不足"
        elif "RSI" in reason:
            stage = "RSI方向不足"
        elif "Score_Low" in reason:
            stage = "分數不足"
        else:
            stage = "條件未完成"
        coin = symbol.replace("/USDT", "")
        return f"{coin} {direction_text} {int(score)}分,{stage}"


    @staticmethod
    def _history_adjusted_score(raw_score: int, performance: dict) -> tuple[int, float]:
        if performance["trades"] < 3:
            return int(raw_score), 1.0
        win_rate_mult = max(0.4, min(1.0, 0.3 + performance["win_rate"]))
        pnl_mult = 1.0 if performance["avg_pnl"] >= 0 else 0.85
        multiplier = win_rate_mult * pnl_mult
        return round(raw_score * multiplier), multiplier

    def _symbol_recent_performance(self, symbol: str, side: str) -> dict:
        """取這個幣種+方向最近 10 筆已平倉交易的勝率與平均損益，用來過濾歷史表現差的訊號。"""
        recent = [
            t for t in self.account.trades
            if t.get("symbol") == symbol
            and t.get("side") == side
            and str(t.get("action", "")).startswith("CLOSE")
        ][:10]
        if not recent:
            return {"trades": 0, "avg_pnl": 0.0, "win_rate": 1.0}
        pnls = [float(t.get("pnl") or 0.0) for t in recent]
        return {
            "trades": len(pnls),
            "avg_pnl": sum(pnls) / len(pnls),
            "win_rate": sum(p > 0 for p in pnls) / len(pnls),
        }

    def _log_signal_progress(
        self, entries: List[str], now_time: float, symbols_snapshot: List[str]
    ) -> None:
        if self.symbol_rotation.last_rotation_at <= 0:
            return
        if symbols_snapshot != list(DEFAULT_SYMBOLS):
            return
        if not entries or now_time - self.last_signal_progress_log_at < 60:
            return
        self.account.log("📊 [12幣訊號進度]\n" + "\n".join(f"• {entry}" for entry in entries), "INFO")
        self.last_signal_progress_log_at = now_time

    async def start(self):
        if self.is_running:
            return
        await self.account.initialize()
        self.is_running = True
        if USE_TESTNET:
            self.account.log("▶️ 8006 Binance Futures Testnet 機器人啟動（達標訊號全數回踩確認 / 12幣雙向交易）")
        else:
            self.account.log(
                "🔴🔴🔴 正式實盤模式已啟用（USE_TESTNET=false）：本次啟動將用真實資金下單！",
                "DANGER",
            )
        self.task = asyncio.create_task(self._main_loop())
        # 幣種輪替（含 AI 呼叫，最壞情況耗時數十秒）獨立成背景任務，
        # 避免跟主迴圈共用同一個 await 鏈，卡住停損停利檢查。
        self.rotation_task = asyncio.create_task(self._rotation_loop())
        # 歷史分析是第三條完全獨立的工作，不等待主交易或幣種輪替。
        self.analysis_task = asyncio.create_task(self._analysis_loop())
        # 持倉平倉參考指標同樣獨立成背景任務，抓K線失敗/變慢不影響主迴圈。
        self.trigger_task = asyncio.create_task(self._position_trigger_loop())
        # 啟動時檢查既有歷史；摘要未變時會由 digest 快取直接略過。
        self.request_trade_analysis()

    async def stop(self):
        if not self.is_running:
            return
        self.is_running = False
        if self.task:
            self.task.cancel()
        if self.rotation_task:
            self.rotation_task.cancel()
        if self.analysis_task:
            self.analysis_task.cancel()
        if self.trigger_task:
            self.trigger_task.cancel()
        await self.exchange.close()
        await self.execution_exchange.close()
        self.account.log("⏹️ 量化交易機器人已停止")

    def request_trade_analysis(self) -> None:
        """平倉事件只設旗標，絕不在平倉／風控路徑等待 AI。"""
        self.analysis_event.set()

    async def _analysis_loop(self):
        """事件式歷史分析；連續平倉會合併，失敗才按節流時間重試。"""
        retry_delay = None
        while self.is_running:
            try:
                if retry_delay is None:
                    await self.analysis_event.wait()
                else:
                    try:
                        await asyncio.wait_for(
                            self.analysis_event.wait(),
                            timeout=retry_delay,
                        )
                    except asyncio.TimeoutError:
                        pass
                self.analysis_event.clear()

                analysis_updated = await self.symbol_rotation.trade_analysis.analyze_if_changed(
                    self.account.trades
                )
                analysis_status = self.symbol_rotation.trade_analysis.status()
                if analysis_updated:
                    count = analysis_status.get("trade_count", 0)
                    self.account.log(f"🧠 [AI 歷史分析] 已記錄並分析 {count} 筆平倉交易", "INFO")

                retry_delay = (
                    self.symbol_rotation.trade_analysis.retry_after_sec
                    if analysis_status.get("status") == "fallback"
                    else None
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                retry_delay = self.symbol_rotation.trade_analysis.retry_after_sec
                self.account.log(
                    f"⚠️ [AI 歷史分析] 暫時失敗，交易與輪替不受影響："
                    f"{type(exc).__name__}: {exc}",
                    "WARNING",
                )

    async def _rotation_loop(self):
        """獨立於主交易迴圈之外定時執行幣種輪替，避免 AI 呼叫延遲停損停利判斷。"""
        last_unhealthy_check_at = 0.0
        while self.is_running:
            try:
                now_time = time.time()
                if now_time - self.symbol_rotation.last_rotation_at >= SYMBOL_ROTATION_INTERVAL_SEC:
                    changes = await self.symbol_rotation.rotate(self.exchange)
                    if changes:
                        change_text = "、".join(f"{item['out']}→{item['in']}" for item in changes)
                        self.account.log(f"🔄 [幣種輪替] {change_text}；{self.symbol_rotation.last_reason}", "INFO")
                    else:
                        self.account.log(f"✅ [幣種輪替] 目前 12 幣仍為較優組合；{self.symbol_rotation.last_reason}", "INFO")
                    last_unhealthy_check_at = now_time
                elif now_time - last_unhealthy_check_at >= UNHEALTHY_SYMBOL_CHECK_INTERVAL_SEC:
                    last_unhealthy_check_at = now_time
                    purge_changes = await self.symbol_rotation.purge_unhealthy(self.exchange)
                    if purge_changes:
                        change_text = "、".join(
                            f"{item['out']}→{item['in']}（{item['reason']}）" for item in purge_changes
                        )
                        self.account.log(f"🚨 [不健康幣種淘汰] {change_text}", "WARNING")
                await asyncio.sleep(30)  # 30 秒檢查一次是否到了下次輪替/健康檢查時間
            except asyncio.CancelledError:
                break
            except Exception as exc:
                # 輪替與 AI 都是輔助層，失敗時不能中斷持倉管理與主策略。
                self.symbol_rotation.last_rotation_at = time.time()
                self.symbol_rotation.last_reason = f"輪替失敗，保留原牌面：{type(exc).__name__}"
                self.account.log(f"⚠️ [幣種輪替] 暫時失敗，保留原牌面並繼續交易：{type(exc).__name__}: {exc}", "WARNING")
                await asyncio.sleep(30)

    async def _position_trigger_loop(self):
        """持倉手動平倉參考指標：用 EMA20（策略本身 Keltner 通道用的同一條
        基準線）跟近 20 根 5 分K的前低/前高，判斷「跌破均線」「跌破前低」
        （多單）或「站上均線」「站上前高」（空單）。純粹是給使用者按網頁
        「平倉」按鈕前參考用的視覺提示，不會觸發任何自動平倉，獨立成
        背景任務、抓K線失敗或變慢也不影響主迴圈的止損止利判斷。"""
        while self.is_running:
            try:
                for symbol, position in list(self.account.positions.items()):
                    df = await self.fetch_klines(symbol, timeframe="5m", limit=30)
                    trigger = compute_position_trigger(df, position.get("side"))
                    trigger["updated_at"] = time.time()
                    self.position_triggers[symbol] = trigger
                for symbol in set(self.position_triggers) - set(self.account.positions):
                    self.position_triggers.pop(symbol, None)
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.account.log(f"⚠️ [平倉參考指標] 暫時失敗：{type(exc).__name__}: {exc}", "WARNING")
                await asyncio.sleep(30)

    async def fetch_klines(self, symbol: str, timeframe: str = "5m", limit: int = 100) -> pd.DataFrame:
        try:
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            # 丟棄還沒收盤的最後一根 K 棒，只在這個共用入口做一次，
            # evaluate_signal/confirm_pullback_entry 等下游邏輯用 df.iloc[-1]
            # 時就天然拿到「最後一根已收盤」的資料，不用逐處修改。
            return drop_unclosed_candle(df, timeframe)
        except Exception as e:
            return pd.DataFrame()

    async def update_market_prices(self):
        try:
            # 牌面外的舊持倉仍必須取得報價，才能正常執行停損／停利；
            # 只有 DEFAULT_SYMBOLS 會進入下方的新開倉掃描。
            monitored_symbols = list(dict.fromkeys([
                *DEFAULT_SYMBOLS,
                *self.account.positions.keys(),
            ]))
            tickers = await self.exchange.fetch_tickers(monitored_symbols)
            for sym, t in tickers.items():
                if 'last' in t and t['last'] is not None:
                    price = float(t['last'])
                    # 統一存成 "SYMBOL/USDT" 格式（去掉 :USDT 後綴）
                    clean_sym = sym.replace(':USDT', '') if sym.endswith(':USDT') else sym
                    self.tickers[clean_sym] = price
                    self.tickers[sym] = price  # 同時保留原格式作為備援
                if 'quoteVolume' in t and t['quoteVolume'] is not None:
                    clean_sym = sym.replace(':USDT', '') if sym.endswith(':USDT') else sym
                    self.ticker_volumes[clean_sym] = float(t['quoteVolume'])
                    self.ticker_volumes[sym] = float(t['quoteVolume'])
            self.last_ticker_success_ts = time.time()
        except Exception as e:
            # 原本這裡整個吞掉例外，抓價失敗時 self.tickers 會停在上一次的
            # 舊報價，止損/止利判斷、訊號評分全部悄悄用過期價格繼續跑，
            # 不會有任何紀錄。改成量測「已經幾秒沒更新」並每 30 秒記一次
            # WARNING，讓抓價持續失敗這件事至少看得到，不是無聲無息。
            now = time.time()
            stale_sec = now - self.last_ticker_success_ts
            if now - self._last_stale_ticker_log >= 30:
                self._last_stale_ticker_log = now
                self.account.log(
                    f"⚠️ 抓取即時報價失敗（{type(e).__name__}: {e}），"
                    f"報價已 {stale_sec:.0f} 秒未更新",
                    "WARNING",
                )

    async def update_1h_trend_cache(self):
        """10 分鐘才抓取一次 1h 大週期數據，避免頻繁調用 API Rate Limit"""
        now = time.time()
        if now - self.last_1h_cache_time < 600 and self.ema_50_1h_cache:
            return

        monitored_symbols = list(dict.fromkeys([
            *DEFAULT_SYMBOLS,
            *self.account.positions.keys(),
        ]))

        # 確保 BTC/USDT 一定在監控列表裡（守門員需要它）
        btc_symbol = "BTC/USDT"
        all_symbols = list(dict.fromkeys([btc_symbol, *monitored_symbols]))

        for symbol in all_symbols:
            df_1h = await self.fetch_klines(symbol, timeframe="1h", limit=150)
            if not df_1h.empty and len(df_1h) >= 30:
                ema_val = df_1h['close'].ewm(span=min(len(df_1h), TREND_FILTER_EMA_PERIOD), adjust=False).mean().iloc[-1]
                self.ema_50_1h_cache[symbol] = float(ema_val)
                # 計算 1h 指標（SuperTrend + ADX）
                computed_1h = self.strategy.compute_indicators(df_1h)

                # 個幣 1h SuperTrend 方向快取
                st_dir_1h = int(computed_1h['st_direction'].iloc[-1])
                self.st_direction_1h_cache[symbol] = st_dir_1h

                # BTC 守門員：記錄方向 + 翻轉後幾根 K棒
                if symbol == btc_symbol:
                    from core.indicators import bars_since_supertrend_flip
                    self.btc_1h_st_direction = st_dir_1h
                    self.btc_1h_st_flip_age = int(bars_since_supertrend_flip(computed_1h['st_direction']))

                # ADX 衰退快取
                adx_1h = computed_1h['adx'].iloc[-1]
                lookback_idx = len(computed_1h) - 1 - ADX_DECLINE_LOOKBACK_BARS_1H
                adx_1h_prior = computed_1h['adx'].iloc[lookback_idx] if lookback_idx >= 0 else float('nan')
                if not pd.isna(adx_1h) and not pd.isna(adx_1h_prior):
                    self.adx_1h_declining_cache[symbol] = bool(
                        adx_1h < ADX_QUALITY_MIN and adx_1h < adx_1h_prior
                    )
            await asyncio.sleep(0.1)
        self.last_1h_cache_time = now

    @staticmethod
    def _quality_bonus(reason: str) -> int:
        match = re.search(r"Quality\+(\d+)", reason or "")
        return int(match.group(1)) if match else 0

    @staticmethod
    def _pullback_reversal_confirmed(candidate: dict, candles_1m: pd.DataFrame) -> bool:
        """觸價後必須等包含觸價時刻的 1m K 棒真正收盤，且收回目標外側。"""
        if candles_1m is None or candles_1m.empty:
            return False
        candle = candles_1m.iloc[-1]
        close_time_ms = float(candle["timestamp"]) + 60_000
        if close_time_ms <= float(candidate.get("touched_at", 0.0)) * 1000:
            return False
        target = float(candidate["target_price"])
        atr = max(float(candidate.get("atr") or 0.0), target * 1e-6)
        reclaim = atr * PULLBACK_RECLAIM_MIN_ATR
        open_price = float(candle["open"])
        close_price = float(candle["close"])
        if candidate["side"] == "LONG":
            return (
                close_price > open_price
                and close_price >= target + reclaim
            )
        return (
            close_price < open_price
            and close_price <= target - reclaim
        )

    def _fresh_pullback_target(self, df: pd.DataFrame, side: str) -> tuple[float, float]:
        computed = self.strategy.compute_indicators(df)
        curr = computed.iloc[-1]
        atr = float(curr["atr"])
        ema_20 = float(curr["ema_20"])
        if side == "LONG":
            kc_edge = float(curr["kc_upper"])
            target = kc_edge - (kc_edge - ema_20) * PULLBACK_TARGET_DEPTH
        else:
            kc_edge = float(curr["kc_lower"])
            target = kc_edge + (ema_20 - kc_edge) * PULLBACK_TARGET_DEPTH
        return target, atr

    def _drop_pullback_candidate(
        self, symbol: str, reason: str, now: float, cooldown: bool = True
    ) -> None:
        candidate = self.pending_pullback_candidates.pop(symbol, None)
        if not candidate:
            return
        if cooldown:
            self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
        self.account.log(f"↩️ [回踩候選取消] {symbol}：{reason}", "INFO")

    def _admit_pullback_candidates(
        self, signals: list, available_balance: float, now: float
    ) -> None:
        """把舊候選與本輪新訊號一起排序，只保留分數/品質最佳的可用槽位。"""
        pool = {
            symbol: info
            for symbol, info in self.pending_pullback_candidates.items()
            if symbol not in self.account.positions
            and symbol not in self.account.pending_limit_orders
            and symbol in DEFAULT_SYMBOLS
            and symbol not in ENTRY_DISABLED_SYMBOLS
            and now - info["created_at"] <= PULLBACK_TIMEOUT_MINUTES * 60
        }
        for score, symbol, sig, _price, real_atr in signals:
            if symbol in pool or self._pullback_retry_after.get(symbol, 0.0) > now:
                continue
            base_amount = min(
                max(TRADE_AMOUNT_USDT * get_position_multiplier(score), MIN_TRADE_USDT),
                TRADE_AMOUNT_USDT,
            )
            allocation_factor = float(sig.get("btc_allocation_factor", 1.0) or 1.0)
            amount = base_amount * allocation_factor
            pool[symbol] = {
                "symbol": symbol,
                "side": sig["side"],
                "score": score,
                "quality": self._quality_bonus(sig.get("reason", "")),
                "target_price": float(sig["target_zone"]),
                "atr": float(sig.get("atr") or real_atr),
                "reason": sig.get("reason", ""),
                "amount_usdt": amount,
                "base_amount_usdt": base_amount,
                "btc_regime_mode": sig.get("btc_regime_mode", "UNKNOWN"),
                "btc_direction_1h": sig.get("btc_direction_1h", 0),
                "btc_score_penalty": sig.get("btc_score_penalty", 0),
                "btc_allocation_factor": allocation_factor,
                "btc_pre_penalty_score": sig.get("btc_pre_penalty_score", score),
                "leverage": self.symbol_rotation.get_dynamic_leverage(symbol, score),
                "created_at": now,
                "touched_at": None,
            }

        capacity = (
            max(0, MAX_SLOTS - len(self.account.positions) - len(self.account.pending_limit_orders))
            if MAX_SLOTS > 0 else len(pool)
        )
        ranked = sorted(
            pool.values(),
            key=lambda item: (item["score"], item["quality"], -item["created_at"]),
            reverse=True,
        )
        selected = {}
        budget_used = 0.0
        for item in ranked:
            if len(selected) >= capacity:
                break
            if budget_used + item["amount_usdt"] > available_balance:
                continue
            selected[item["symbol"]] = item
            budget_used += item["amount_usdt"]

        old_symbols = set(self.pending_pullback_candidates)
        self.pending_pullback_candidates = selected
        for symbol in old_symbols - set(selected):
            self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
        for symbol in set(selected) - old_symbols:
            item = selected[symbol]
            self.account.log(
                f"🎯 [回踩候選] {symbol} {item['side']} {item['score']}分/品質{item['quality']} "
                f"等待觸價 @ {item['target_price']:.8g}"
                f"（BTC={item['btc_regime_mode']}，倉位×{item['btc_allocation_factor']:.2f}）",
                "INFO",
            )

    async def _monitor_pullback_candidates(self, now: float) -> None:
        daily_halt, _ = self.account.daily_loss_limit_hit()
        if daily_halt:
            for symbol in list(self.pending_pullback_candidates):
                self._drop_pullback_candidate(symbol, "每日虧損熔斷，停止新倉", now)
            return

        for symbol, candidate in list(self.pending_pullback_candidates.items()):
            if symbol in ENTRY_DISABLED_SYMBOLS or symbol not in DEFAULT_SYMBOLS:
                self._drop_pullback_candidate(symbol, "幣種已停止新倉或移出牌面", now)
                continue
            if symbol in self.account.positions or symbol in self.account.pending_limit_orders:
                self.pending_pullback_candidates.pop(symbol, None)
                continue
            if now - candidate["created_at"] > PULLBACK_TIMEOUT_MINUTES * 60:
                self._drop_pullback_candidate(symbol, "等待回踩/反轉確認逾時", now)
                continue

            live_price = self.tickers.get(symbol)
            if not live_price:
                continue
            target = candidate["target_price"]
            touched = live_price <= target if candidate["side"] == "LONG" else live_price >= target
            if candidate.get("touched_at") is None:
                if not touched:
                    continue
                candidate["touched_at"] = now
                self.account.log(
                    f"👀 [回踩觸價] {symbol} {candidate['side']} @ {live_price:.8g}，"
                    "等待 1m 收盤反轉確認",
                    "INFO",
                )

            confirm_df = await self.fetch_klines(symbol, timeframe="5m", limit=100)
            if confirm_df.empty or len(confirm_df) < 50:
                continue
            confirm = self.strategy.confirm_pullback_entry(
                confirm_df, candidate["side"], ema_1h=self.ema_50_1h_cache.get(symbol),
                trend_1h_declining=self.adx_1h_declining_cache.get(symbol, False),
                btc_st_direction_1h=getattr(self, "btc_1h_st_direction", 0),
                btc_st_flip_age=getattr(self, "btc_1h_st_flip_age", 999), symbol=symbol,
            )
            if confirm["status"] != "PASS":
                self._drop_pullback_candidate(symbol, f"條件已變差：{confirm['reason']}", now)
                continue

            final_btc_factor = float(confirm.get("btc_allocation_factor", 1.0) or 1.0)
            candidate["amount_usdt"] = min(
                candidate["amount_usdt"], candidate["base_amount_usdt"] * final_btc_factor
            )
            candidate["btc_regime_mode"] = confirm.get("btc_regime_mode", "UNKNOWN")
            candidate["btc_direction_1h"] = confirm.get("btc_direction_1h", 0)
            candidate["btc_score_penalty"] = confirm.get("btc_score_penalty", 0)
            candidate["btc_allocation_factor"] = final_btc_factor

            fresh_target, fresh_atr = self._fresh_pullback_target(confirm_df, candidate["side"])
            drift = abs(fresh_target - target)
            if drift > max(candidate["atr"], fresh_atr) * PULLBACK_TARGET_MAX_DRIFT_ATR:
                self._drop_pullback_candidate(symbol, f"目標漂移 {drift / max(fresh_atr, 1e-12):.2f} ATR", now)
                continue

            candles_1m = await self.fetch_klines(symbol, timeframe="1m", limit=5)
            candidate_for_check = dict(candidate, target_price=fresh_target, atr=fresh_atr)
            if not self._pullback_reversal_confirmed(candidate_for_check, candles_1m):
                continue
            live_price = self.tickers.get(symbol, live_price)
            reclaimed = live_price >= fresh_target if candidate["side"] == "LONG" else live_price <= fresh_target
            if not reclaimed:
                self._drop_pullback_candidate(symbol, "反轉確認後又跌回/漲回目標錯側", now)
                continue
            committed = len(self.account.positions) + len(self.account.pending_limit_orders)
            if MAX_SLOTS > 0 and committed >= MAX_SLOTS:
                self._drop_pullback_candidate(symbol, "可用持倉槽位已滿", now, cooldown=False)
                continue
            if self.account.get_available_balance() < candidate["amount_usdt"]:
                self._drop_pullback_candidate(symbol, "可用保證金不足", now, cooldown=False)
                continue

            sl_distance, tp_distance = compute_sl_tp_distance(fresh_target, fresh_atr)
            if candidate["side"] == "LONG":
                sl, tp = fresh_target - sl_distance, fresh_target + tp_distance
            else:
                sl, tp = fresh_target + sl_distance, fresh_target - tp_distance
            placed = await self.account.place_limit_entry(
                symbol=symbol, side=candidate["side"], target_price=fresh_target,
                amount_usdt=candidate["amount_usdt"], sl=sl, tp=tp,
                reason=f"Pullback_Confirmed_Limit | {candidate['reason']}", atr=fresh_atr,
                leverage=candidate["leverage"], signal_score=candidate["score"],
                post_only=True, entry_context={
                    "btc_regime_at_entry": candidate["btc_regime_mode"],
                    "btc_direction_1h_at_entry": candidate["btc_direction_1h"],
                    "btc_score_penalty": candidate["btc_score_penalty"],
                    "btc_allocation_factor": candidate["btc_allocation_factor"],
                    "btc_pre_penalty_score": candidate["btc_pre_penalty_score"],
                },
            )
            if placed:
                self.pending_pullback_candidates.pop(symbol, None)

    async def _validate_pending_limit_orders(self, now: float) -> None:
        """先驗證再查成交；最大限度縮小失效訊號被舊掛單接住的時間窗。"""
        daily_halt, _ = self.account.daily_loss_limit_hit()
        if daily_halt:
            for symbol in list(self.account.pending_limit_orders):
                await self.account.cancel_pending_limit(symbol, "每日虧損熔斷，停止新倉")
                self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
            await self.account.check_pending_limit_orders()
            return

        for symbol, info in list(self.account.pending_limit_orders.items()):
            if symbol in ENTRY_DISABLED_SYMBOLS or symbol not in DEFAULT_SYMBOLS:
                await self.account.cancel_pending_limit(symbol, "幣種已停止新倉或移出牌面")
                continue
            if now - info["placed_at"] > ENTRY_LIMIT_TIMEOUT_SEC:
                await self.account.cancel_pending_limit(
                    symbol, f"短效 Maker 掛單 {ENTRY_LIMIT_TIMEOUT_SEC:.0f} 秒未成交"
                )
                self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
                continue
            confirm_df = await self.fetch_klines(symbol, timeframe="5m", limit=100)
            if confirm_df.empty or len(confirm_df) < 50:
                continue
            confirm = self.strategy.confirm_pullback_entry(
                confirm_df, info["side"], ema_1h=self.ema_50_1h_cache.get(symbol),
                trend_1h_declining=self.adx_1h_declining_cache.get(symbol, False),
                btc_st_direction_1h=getattr(self, "btc_1h_st_direction", 0),
                btc_st_flip_age=getattr(self, "btc_1h_st_flip_age", 999), symbol=symbol,
            )
            if confirm["status"] != "PASS":
                await self.account.cancel_pending_limit(symbol, f"條件已變差：{confirm['reason']}")
                self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC
                continue
            current_factor = float(confirm.get("btc_allocation_factor", 1.0) or 1.0)
            placed_factor = float((info.get("entry_context") or {}).get("btc_allocation_factor", 1.0) or 1.0)
            if current_factor < placed_factor:
                await self.account.cancel_pending_limit(
                    symbol, "BTC方向轉為背離，撤銷原倉位並按50%重新建立候選"
                )
                self._pullback_retry_after[symbol] = now
                continue
            fresh_target, fresh_atr = self._fresh_pullback_target(confirm_df, info["side"])
            drift_atr = abs(fresh_target - info["target_price"]) / max(fresh_atr, 1e-12)
            if drift_atr > PULLBACK_TARGET_MAX_DRIFT_ATR:
                await self.account.cancel_pending_limit(
                    symbol, f"掛單目標已漂移 {drift_atr:.2f} ATR"
                )
                self._pullback_retry_after[symbol] = now + PULLBACK_RETRY_COOLDOWN_SEC

        await self.account.check_pending_limit_orders()

    async def _main_loop(self):
        while self.is_running:
            try:
                # 1. 更新實時價格
                await self.update_market_prices()

                # 幣種輪替已移到獨立的 _rotation_loop() 背景任務執行，
                # 不再佔用這個迴圈的 await 鏈，停損停利不會被 AI 呼叫延遲。

                # 2. 更新與執行持倉部位
                await self.account.update_positions(self.tickers)
                # 冷卻時間唯一資料來源是 self.account.last_closed_at（見
                # testnet_account.py），不管平倉是這裡的主迴圈觸發，還是
                # /api/prices、/api/status 這些跟主迴圈不同步的網頁輪詢
                # 呼叫觸發，都會準確記錄，不會像原本這裡自己拿前後快照
                # 判斷那樣，漏掉別的呼叫者觸發的平倉。

                # 3. 10分鐘定時刷新 1h EMA200 快取 (防止 API Rate Limit 封鎖)
                await self.update_1h_trend_cache()

                # 4. 先等觸價與 1m 反轉確認，再驗證短效掛單，最後才查成交。
                now_time = time.time()
                await self._monitor_pullback_candidates(now_time)
                await self._validate_pending_limit_orders(now_time)



                # 5. 開倉訊號檢查 — 依可用餘額填充預算，用完為止
                # 每日虧損熔斷：觸發時只跳過本段（不開新倉），上面的持倉管理
                # （止損/止利/移動止利/分批止盈）完全不受影響。
                daily_halt, _daily_loss_pct = self.account.daily_loss_limit_hit()
                available_balance = self.account.get_available_balance()
                if TEST_BUDGET_CAP_USDT > 0:
                    available_balance = min(available_balance, TEST_BUDGET_CAP_USDT)
                if not daily_halt and available_balance >= MIN_TRADE_USDT:
                    candidate_signals = []  # [(score, symbol, sig, price, atr)]
                    pullback_signals = []
                    signal_progress = []

                    now_time = time.time()
                    # 幣種輪替現在跑在獨立背景任務，可能在這個迴圈 await 期間改動 DEFAULT_SYMBOLS，
                    # 用 list(...) 先拍一份快照，避免邊跑邊被換牌造成跳過或重複掃描。
                    symbols_snapshot = list(DEFAULT_SYMBOLS)
                    for symbol in symbols_snapshot:
                        direction_text = "雙向"
                        coin = symbol.replace("/USDT", "")
                        if symbol in self.account.positions:
                            position = self.account.positions[symbol]
                            position_score = position.get("signal_score") or 0
                            position_direction = "多單" if position.get("side") == "LONG" else "空單"
                            signal_progress.append(
                                f"{coin} {position_direction} {position_score}分,持倉中"
                            )
                            continue

                        if symbol in self.pending_pullback_candidates:
                            pending = self.pending_pullback_candidates[symbol]
                            stage = (
                                "已觸價，等待1m收盤反轉確認"
                                if pending.get("touched_at")
                                else f"等待回踩觸價 @ {pending.get('target_price'):.8g}"
                            )
                            signal_progress.append(
                                f"{coin} {pending['side']} {pending['score']}分,{stage}"
                            )
                            continue

                        # 如果已經掛著限價單，跳過訊號偵測（避免重複掛單）
                        if symbol in self.account.pending_limit_orders:
                            pending = self.account.pending_limit_orders[symbol]
                            signal_progress.append(self._format_signal_progress(
                                symbol,
                                {
                                    "action": "WAIT_PULLBACK",
                                    "score": pending.get("signal_score", 0),
                                    "confirmation_reason": f"限價單等待成交 @ {pending.get('target_price')}",
                                },
                                pending.get("side"),
                            ))
                            continue

                        retry_after = self._pullback_retry_after.get(symbol, 0.0)
                        if retry_after > now_time:
                            signal_progress.append(
                                f"{coin} {direction_text} 0分,回踩失效冷卻{int(retry_after - now_time)}秒"
                            )
                            continue

                        # 冷卻時間檢查 (剛平倉 15 分鐘內禁止重複進場)

                        if symbol in ENTRY_DISABLED_SYMBOLS:
                            signal_progress.append(
                                f"{coin} {direction_text} 0分,暫停新倉"
                            )
                            continue
                        last_closed = self.account.last_closed_at.get(symbol)
                        if last_closed is not None and (now_time - last_closed) < 900:
                            remaining = max(0, int((900 - (now_time - last_closed)) / 60) + 1)
                            signal_progress.append(
                                f"{coin} {direction_text} 0分,冷卻剩{remaining}分鐘"
                            )
                            continue

                        # 4.1 低流動性過濾
                        vol_24h = self.ticker_volumes.get(symbol, 0.0)
                        if vol_24h > 0 and vol_24h < 500000.0:
                            signal_progress.append(
                                f"{coin} {direction_text} 0分,24h流動性不足"
                            )
                            continue

                        df = await self.fetch_klines(symbol, timeframe="5m", limit=100)
                        if df.empty or len(df) < 50:
                            signal_progress.append(
                                f"{coin} {direction_text} 0分,K線資料不足"
                            )
                            continue

                        # 取出 1h 快取值
                        ema_50_1h = self.ema_50_1h_cache.get(symbol)

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
                            signal_progress.append(
                                f"{coin} {direction_text} 0分,防插針過濾"
                            )
                            continue

                        # 計算指標以取得 rsi 與 kc 通道等欄位
                        df = self.strategy.compute_indicators(df)
                        sig = self.strategy.evaluate_signal(
                            df,
                            ema_50_1h=ema_50_1h,
                            trend_1h_declining=self.adx_1h_declining_cache.get(symbol, False),
                            st_direction_1h=self.st_direction_1h_cache.get(symbol),
                            btc_st_direction_1h=self.btc_1h_st_direction,
                            btc_st_flip_age=self.btc_1h_st_flip_age,
                            symbol=symbol,
                        )
                        current_direction = (
                            "LONG" if int(df.iloc[-1]["st_direction"]) == 1 else "SHORT"
                        )
                        signal_progress.append(self._format_signal_progress(
                            symbol, sig, current_direction
                        ))
                        if sig["action"] in ["BUY", "SELL", "WAIT_PULLBACK"]:
                            perf = self._symbol_recent_performance(symbol, sig["side"])
                            adjusted_score, history_mult = self._history_adjusted_score(
                                sig.get("score", 0), perf
                            )
                            if history_mult < 0.99:
                                log_key = (
                                    sig["side"], perf["trades"], round(perf["win_rate"], 2),
                                    round(perf["avg_pnl"], 3), round(history_mult, 2),
                                    sig.get("score", 0), adjusted_score,
                                )
                                if self._history_coeff_logged.get(symbol) != log_key:
                                    self._history_coeff_logged[symbol] = log_key
                                    self.account.log(
                                        f"📉 [歷史係數] {symbol} {sig['side']} 近期 {perf['trades']} 筆"
                                        f"勝率 {perf['win_rate']:.0%}、平均損益 {perf['avg_pnl']:+.3f} → "
                                        f"係數 x{history_mult:.2f}，分數 {sig.get('score', 0)}→{adjusted_score}",
                                        "INFO",
                                    )
                            sig["score"] = adjusted_score
                            if adjusted_score < MIN_SCORE_THRESHOLD:
                                signal_progress.append(
                                    f"{coin} {sig['side']} {adjusted_score}分,歷史績效降分後取消"
                                )
                                continue

                        if sig["action"] in ["BUY", "SELL"]:
                            candidate_signals.append((adjusted_score, symbol, sig, price, real_atr))

                        elif sig["action"] == "WAIT_PULLBACK":
                            pullback_signals.append(
                                (adjusted_score, symbol, sig, price, real_atr)
                            )

                    self._admit_pullback_candidates(pullback_signals, available_balance, now_time)

                    self._log_signal_progress(signal_progress, now_time, symbols_snapshot)

                    # 按評分排序，逐個填充，直到預算用完或同時持倉數達到 MAX_SLOTS
                    # 上限為止——避免行情同時觸發多個高度相關的訊號時（同一波
                    # 市場方向）一次開一堆單，一旦反轉就一次全部停損。訊號一次
                    # 多於剩餘槽位時，依評分排序只挑最優的填滿槽位。
                    candidate_signals.sort(key=lambda x: x[0], reverse=True)
                    top_signals = []
                    budget_used = 0.0
                    open_slots = (
                        max(
                            0, MAX_SLOTS - len(self.account.positions)
                            - len(self.account.pending_limit_orders)
                            - len(self.pending_pullback_candidates),
                        )
                        if MAX_SLOTS > 0 else None
                    )
                    for sc, sym, sig, pr, atr_val in candidate_signals:
                        if open_slots is not None and len(top_signals) >= open_slots:
                            break
                        slot_amount = min(
                            TRADE_AMOUNT_USDT * get_position_multiplier(sig.get("score", sc)),
                            TRADE_AMOUNT_USDT
                        )
                        slot_amount = max(slot_amount, MIN_TRADE_USDT)
                        if budget_used + slot_amount > available_balance:
                            break
                        top_signals.append((sc, sym, sig, pr, atr_val, slot_amount))
                        budget_used += slot_amount

                    if len(candidate_signals) > len(top_signals):
                        skipped = [s[1] for s in candidate_signals[len(top_signals):]]
                        slot_note = (
                            f"，同時持倉上限 {MAX_SLOTS} 槽（目前持倉 {len(self.account.positions)}）"
                            if MAX_SLOTS > 0 else ""
                        )
                        self.account.log(
                            f"🏆 [訊號篩選] 本輪 {len(candidate_signals)} 個訊號，預算 {available_balance:.0f}U 入場 {len(top_signals)} 個（{budget_used:.0f}U）{slot_note}，跳過: {', '.join(skipped)}",
                            "INFO",
                        )

                    for score, symbol, sig, price, real_atr, amount_usdt in top_signals:
                        await self.account.open_position(
                            symbol=symbol,
                            side=sig["side"],
                            price=price,
                            amount_usdt=amount_usdt,
                            sl=sig["sl"],
                            tp=sig["tp"],
                            reason=sig["reason"],
                            atr=sig.get("atr", real_atr),
                            signal_score=sig.get("score"),
                            leverage=self.symbol_rotation.get_dynamic_leverage(symbol, sig.get("score")),
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

