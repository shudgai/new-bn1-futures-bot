import asyncio
import re
import time
import ccxt.async_support as ccxt
import pandas as pd
from typing import Dict, List
from core.config import (
    DEFAULT_SYMBOLS, MAX_SLOTS, TRADE_AMOUNT_USDT, TREND_FILTER_EMA_PERIOD,
    PULLBACK_TIMEOUT_MINUTES, PULLBACK_ZONE_PCT, SYMBOL_ROTATION_INTERVAL_SEC,
    BINANCE_API_KEY, BINANCE_SECRET, get_position_multiplier, MIN_TRADE_USDT,
    MIN_SCORE_THRESHOLD, USE_TESTNET, POSITION_TREND_CHECK_INTERVAL_SEC,
)
from core.strategy import SuperTrendKeltnerStrategy, compute_sl_tp_distance
from core.testnet_account import BinanceTestnetAccount
from core.symbol_rotation import SymbolRotation
from core.indicators import drop_unclosed_candle

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
        self.ema_200_1h_cache: Dict[str, float] = {}
        self.last_1h_cache_time: float = 0.0
        # 回調待命狀態機（Pullback State Machine）
        # 格式: { symbol: {"side": "LONG"/"SHORT", "target_zone": float, "atr": float,
        #                      "sl": float, "tp": float, "timestamp": float, "reason": str} }
        self.pending_pullbacks: Dict[str, dict] = {}
        self.last_signal_progress_log_at: float = 0.0
        # 持倉幣種的 SuperTrend 方向快取，用來偵測趨勢反轉、餵給移動止損當
        # 候選收緊價（見 update_position_trends）。不用跟主迴圈一樣每 5 秒
        # 重算，5 分K本身要 5 分鐘才會真的變化。
        self.position_trend_cache: Dict[str, int] = {}
        # 同一次重算順便更新的即時 ATR，取代原本「整個持倉期間都用進場那
        # 一刻存的 ATR」——讓移動止損/趨勢反轉的距離反映當下波動度，不是
        # 進場當下的舊波動度。
        self.position_atr_cache: Dict[str, float] = {}
        self.last_position_trend_check: float = 0.0

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
        elif "KC_Breakout" in reason:
            stage = "待KC突破"
        elif "SuperTrend_Stale" in reason:
            stale = re.search(r"SuperTrend_Stale\((\d+)\)", reason)
            stage = f"SuperTrend過期{stale.group(1)}根" if stale else "SuperTrend過期"
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
            self.account.log("▶️ 8006 Binance Futures Testnet 機器人啟動（70分回調＋80分立即 / 12幣雙向交易）")
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
                await asyncio.sleep(30)  # 30 秒檢查一次是否到了下次輪替時間
            except asyncio.CancelledError:
                break
            except Exception as exc:
                # 輪替與 AI 都是輔助層，失敗時不能中斷持倉管理與主策略。
                self.symbol_rotation.last_rotation_at = time.time()
                self.symbol_rotation.last_reason = f"輪替失敗，保留原牌面：{type(exc).__name__}"
                self.account.log(f"⚠️ [幣種輪替] 暫時失敗，保留原牌面並繼續交易：{type(exc).__name__}: {exc}", "WARNING")
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
        except Exception as e:
            pass

    async def update_1h_trend_cache(self):
        """10 分鐘才抓取一次 1h 大週期數據，避免頻繁調用 API Rate Limit"""
        now = time.time()
        if now - self.last_1h_cache_time < 600 and self.ema_200_1h_cache:
            return

        monitored_symbols = list(dict.fromkeys([
            *DEFAULT_SYMBOLS,
            *self.account.positions.keys(),
        ]))
        for symbol in monitored_symbols:
            df_1h = await self.fetch_klines(symbol, timeframe="1h", limit=100)
            if not df_1h.empty and len(df_1h) >= 30:
                ema_val = df_1h['close'].ewm(span=min(len(df_1h), TREND_FILTER_EMA_PERIOD), adjust=False).mean().iloc[-1]
                self.ema_200_1h_cache[symbol] = float(ema_val)
            await asyncio.sleep(0.1)
        self.last_1h_cache_time = now

    async def update_position_trends(self):
        """定期（非每5秒）幫目前持倉的幣種重新計算 SuperTrend 方向與 ATR，
        用來偵測趨勢反轉、更新移動止損距離。只覆蓋持倉，不是全部牌面，
        數量少、頻率也低，對 API 負擔很小。方向結果只當成移動止損的候選
        收緊價之一，不會直接觸發平倉，避免變成第二套跟移動止損互搶出場
        的邏輯。"""
        now = time.time()
        if now - self.last_position_trend_check < POSITION_TREND_CHECK_INTERVAL_SEC:
            return
        self.last_position_trend_check = now

        held_symbols = list(self.account.positions.keys())
        for symbol in held_symbols:
            df = await self.fetch_klines(symbol, timeframe="5m", limit=100)
            if df.empty or len(df) < 50:
                continue
            computed = self.strategy.compute_indicators(df)
            curr = computed.iloc[-1]
            self.position_trend_cache[symbol] = int(curr["st_direction"])
            if not pd.isna(curr["atr"]) and curr["atr"] > 0:
                self.position_atr_cache[symbol] = float(curr["atr"])
            await asyncio.sleep(0.1)

    async def _main_loop(self):
        while self.is_running:
            try:
                # 1. 更新實時價格
                await self.update_market_prices()

                # 幣種輪替已移到獨立的 _rotation_loop() 背景任務執行，
                # 不再佔用這個迴圈的 await 鏈，停損停利不會被 AI 呼叫延遲。

                # 2. 更新與執行持倉部位
                await self.update_position_trends()
                await self.account.update_positions(
                    self.tickers,
                    trend_directions=self.position_trend_cache,
                    atr_overrides=self.position_atr_cache,
                )
                # 冷卻時間唯一資料來源是 self.account.last_closed_at（見
                # testnet_account.py），不管平倉是這裡的主迴圈觸發，還是
                # /api/prices、/api/status 這些跟主迴圈不同步的網頁輪詢
                # 呼叫觸發，都會準確記錄，不會像原本這裡自己拿前後快照
                # 判斷那樣，漏掉別的呼叫者觸發的平倉。

                # 3. 10分鐘定時刷新 1h EMA200 快取 (防止 API Rate Limit 封鎖)
                await self.update_1h_trend_cache()

                # 4. 回調待命狀態機處理 (檢查所有待命訊號是否回調到位)
                now_time = time.time()
                for pb_symbol, pb_info in list(self.pending_pullbacks.items()):
                    if pb_symbol not in DEFAULT_SYMBOLS:
                        del self.pending_pullbacks[pb_symbol]
                        self.account.log(
                            f"🔄 [回調取消] {pb_symbol} 已不在目前12幣名單",
                            "INFO",
                        )
                        continue

                    # 4a. 超時檢查：超過 PULLBACK_TIMEOUT_MINUTES 就放棄
                    elapsed_min = (now_time - pb_info["timestamp"]) / 60.0
                    if elapsed_min > PULLBACK_TIMEOUT_MINUTES:
                        del self.pending_pullbacks[pb_symbol]
                        self.account.log(
                            f"⏰ [回調超時] {pb_symbol} 待命 {elapsed_min:.1f} 分鐘未回調，放棄本次進場機會", "WARNING"
                        )
                        continue

                    # 4b. 探索實時價格
                    curr_p = self.tickers.get(pb_symbol) or self.tickers.get(f"{pb_symbol}:USDT")
                    if not curr_p:
                        continue

                    # 已有持倉則移除待命記錄
                    if pb_symbol in self.account.positions:
                        del self.pending_pullbacks[pb_symbol]
                        continue

                    # 冷卻時間檢查：跟第 5 步的新訊號掃描用同一套規則。原本這裡沒檢查，
                    # 導致剛平倉的幣種如果剛好有更早登記的回調訊號在排隊，價格一回踩
                    # 到目標區就會立刻重新開倉——進場價跟剛平倉的價位幾乎一樣，等於
                    # 白白繳一次手續費又立刻承擔新的止損風險。冷卻期間先跳過這一輪，
                    # 不刪除待命記錄，冷卻結束後（且尚未超時）還是會繼續檢查回踩。
                    last_closed = self.account.last_closed_at.get(pb_symbol)
                    if last_closed is not None and (now_time - last_closed) < 900:
                        continue

                    # 同時持倉上限檢查：跟第 5 步的 MAX_SLOTS 用同一套規則，
                    # 避免這條獨立的回調觸發路徑繞過槽位上限。先跳過這一輪，
                    # 不刪除待命記錄，等有槽位空出來（且尚未超時）再繼續檢查。
                    if MAX_SLOTS > 0 and len(self.account.positions) >= MAX_SLOTS:
                        continue

                    target = pb_info["target_zone"]
                    zone_low  = target * (1.0 - PULLBACK_ZONE_PCT)
                    zone_high = target * (1.0 + PULLBACK_ZONE_PCT)

                    # 4c. 价格回調到目標區間內 → 先做二次確認，通過才觸發進場
                    if pb_info["side"] in ("LONG", "SHORT") and zone_low <= curr_p <= zone_high:
                        side = pb_info["side"]
                        # 二次確認：訊號登記時的量能/RSI/趨勢資料可能已經是幾分鐘甚至
                        # 快 25 分鐘前的舊資料，真正回踩到位這一刻，用最新 K 線重新檢查
                        # 核心條件是否還成立——避免「突破當下訊號成立，等回踩進場時
                        # 動能其實已經退潮」這種假突破最典型的樣貌。
                        confirm_df = await self.fetch_klines(pb_symbol, timeframe="5m", limit=100)
                        if confirm_df.empty or len(confirm_df) < 50:
                            continue
                        confirm = self.strategy.confirm_pullback_entry(
                            confirm_df, side, ema_1h=self.ema_200_1h_cache.get(pb_symbol)
                        )
                        if confirm["status"] != "PASS":
                            del self.pending_pullbacks[pb_symbol]
                            self.account.log(
                                f"🛡️ [回調二次確認未通過] {pb_symbol} {side} 取消本次進場：{confirm['reason']}",
                                "WARNING",
                            )
                            continue

                        atr = pb_info["atr"]
                        # 以回調進場價重新計算 SL/TP，統一用 compute_sl_tp_distance()
                        # 讀取 config 的 STOP_LOSS_MULTIPLIER/TAKE_PROFIT_MULTIPLIER，
                        # 並套用 MIN_SL_DISTANCE_PCT 下限（原本這裡寫死 2.0/3.0，
                        # 完全沒有跟到後續的止損止盈調整）。
                        sl_distance, tp_distance = compute_sl_tp_distance(curr_p, atr)
                        if side == "LONG":
                            sl = curr_p - sl_distance
                            tp = curr_p + tp_distance
                        else:
                            sl = curr_p + sl_distance
                            tp = curr_p - tp_distance
                        pb_amount = TRADE_AMOUNT_USDT * get_position_multiplier(pb_info.get("score", 0))
                        await self.account.open_position(
                            symbol=pb_symbol, side=side, price=curr_p,
                            amount_usdt=pb_amount, sl=sl, tp=tp,
                            reason=f"Pullback_Entry | {pb_info['reason']}", atr=atr,
                            signal_score=pb_info.get("score"),
                            leverage=self.symbol_rotation.get_dynamic_leverage(pb_symbol, pb_info.get("score", 0)),
                        )
                        self.account.log(
                            f"🎯 [回調進場] {pb_symbol} {side} 回調至目標區 ({curr_p:.4f}) 二次確認通過，觸發開倉！ SL={sl:.4f} TP={tp:.4f}", "SUCCESS"
                        )
                        del self.pending_pullbacks[pb_symbol]

                # 5. 開倉訊號檢查 — 依可用餘額填充預算，用完為止
                # 每日虧損熔斷：觸發時只跳過本段（不開新倉），上面的持倉管理
                # （止損/止利/移動止利/分批止盈）完全不受影響。
                daily_halt, _daily_loss_pct = self.account.daily_loss_limit_hit()
                available_balance = self.account.get_available_balance()
                if not daily_halt and available_balance >= MIN_TRADE_USDT:
                    candidate_signals = []  # [(score, symbol, sig, price, atr)]
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

                        # 如果已在回調待命中，跳過訊號偵測（避免重複登記）
                        if symbol in self.pending_pullbacks:
                            pending = self.pending_pullbacks[symbol]
                            signal_progress.append(self._format_signal_progress(
                                symbol,
                                {
                                    "action": "WAIT_PULLBACK",
                                    "score": pending.get("score", 0),
                                    "confirmation_reason": pending.get(
                                        "confirmation_reason",
                                        "等待回調至KC區後二次確認",
                                    ),
                                },
                                pending.get("side"),
                            ))
                            continue

                        # 冷卻時間檢查 (剛平倉 15 分鐘內禁止重複進場)
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
                            signal_progress.append(
                                f"{coin} {direction_text} 0分,防插針過濾"
                            )
                            continue

                        # 計算指標以取得 rsi 與 kc 通道等欄位
                        df = self.strategy.compute_indicators(df)
                        sig = self.strategy.evaluate_signal(df, ema_200_1h=ema_200_1h)
                        current_direction = (
                            "LONG" if int(df.iloc[-1]["st_direction"]) == 1 else "SHORT"
                        )
                        signal_progress.append(self._format_signal_progress(
                            symbol, sig, current_direction
                        ))
                        if sig["action"] in ["BUY", "SELL"]:
                            # 歷史勝率乘數化：不再是「一票否決」，改成用這個幣種+方向
                            # 近期的勝率/平均損益算一個係數，直接修正分數本身——讓歷史
                            # 數據跟即時訊號合併成同一套邏輯，分數越貼近真實期望值。
                            # 樣本數 < 3 時不修正（資料不夠可信），係數維持 1.0。
                            perf = self._symbol_recent_performance(symbol, sig["side"])
                            if perf["trades"] >= 3:
                                # 勝率 30% → 約 0.6x；勝率 80% → 約 1.0x（封頂），樣本不足外一律套用。
                                win_rate_mult = max(0.4, min(1.0, 0.3 + perf["win_rate"]))
                                pnl_mult = 1.0 if perf["avg_pnl"] >= 0 else 0.85
                                history_mult = win_rate_mult * pnl_mult
                            else:
                                history_mult = 1.0
                            # 分數本來就已經通過 MIN_SCORE_THRESHOLD 才准開倉，乘數只用來
                            # 反映「這個訊號在同批候選裡排序該往後、倉位該縮小」，不應該把
                            # 一個本來合格的訊號懲罰到連最低分級的槓桿/倉位都拿不到。
                            adjusted_score = max(
                                MIN_SCORE_THRESHOLD,
                                round(sig.get("score", 0) * history_mult),
                            )
                            if history_mult < 0.99:
                                self.account.log(
                                    f"📉 [歷史係數] {symbol} {sig['side']} 近期 {perf['trades']} 筆"
                                    f"勝率 {perf['win_rate']:.0%}、平均損益 {perf['avg_pnl']:+.3f} → "
                                    f"係數 x{history_mult:.2f}，分數 {sig.get('score', 0)}→{adjusted_score}",
                                    "INFO",
                                )
                            sig["score"] = adjusted_score
                            # 候選訊號排序分數直接沿用修正後的評分，同一輪出現多個候選時
                            # 優先選分數最高的下單，倉位大小/槓桿也會跟著這個分數走。
                            candidate_signals.append((adjusted_score, symbol, sig, price, real_atr))

                        elif sig["action"] == "WAIT_PULLBACK":
                            # ── 回調待命登記 ───────────────────────────────
                            self.pending_pullbacks[symbol] = {
                                "side": sig["side"],
                                "target_zone": sig["target_zone"],
                                "atr": sig.get("atr", real_atr),
                                "kc_upper": sig.get("kc_upper", 0),
                                "kc_lower": sig.get("kc_lower", 0),
                                "score": sig.get("score", 0),
                                "timestamp": time.time(),
                                "reason": sig["reason"]
                            }
                            self.account.log(
                                f"⏳ [回調待命] {symbol} {sig['side']} 登記，目標區: {sig['target_zone']:.4f} ±{PULLBACK_ZONE_PCT:.1%} | {sig['reason']}",
                                "INFO"
                            )

                    self._log_signal_progress(signal_progress, now_time, symbols_snapshot)

                    # 按評分排序，逐個填充，直到預算用完或同時持倉數達到 MAX_SLOTS
                    # 上限為止——避免行情同時觸發多個高度相關的訊號時（同一波
                    # 市場方向）一次開一堆單，一旦反轉就一次全部停損。訊號一次
                    # 多於剩餘槽位時，依評分排序只挑最優的填滿槽位。
                    candidate_signals.sort(key=lambda x: x[0], reverse=True)
                    top_signals = []
                    budget_used = 0.0
                    open_slots = (
                        max(0, MAX_SLOTS - len(self.account.positions))
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

