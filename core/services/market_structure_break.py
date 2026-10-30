"""
Market Structure Break (大週期結構轉折與結束判定引擎)
======================================================
依據 2026-10-30 授權規範：
1. 大週期鎖定保護 (防護罩生效):
   - 只要大結構的「關鍵高低點 (Pivot HL / LH)」未被實體突破，1m/3m 盤中的急跌或急拉一律定義為「牛市回調」或「熊市反彈」，
     絕不能輕易將大週期標籤翻轉。
2. 大週期【牛市結束】判定:
   - 僅當 K 棒實體收盤價 強勢跌破大結構前波起漲關鍵低點 (Higher Low, HL)，且大週期收盤價跌破 HTF MA15 時，
     雙重確認結構打破，方可宣告牛市結束 (MACRO_BULLISH_ENDED)，轉為「大週期熊市 (順勢做空)」。
3. 大週期【熊市結束】判定:
   - 僅當 K 棒實體收盤價 強勢突破大結構前波反彈關鍵高點 (Lower High, LH)，且大週期收盤價站上 HTF MA15 時，
     雙重確認結構打破，方可宣告熊市結束 (MACRO_BEARISH_ENDED)，轉為「大週期牛市 (順勢做多)」。
"""
from typing import Any, Dict, List, Optional, Union
import pandas as pd


def extract_latest_close(htf_candles: Any) -> float:
    """從 DataFrame 或 list 中獲取最新一根 K 棒收盤價"""
    if isinstance(htf_candles, pd.DataFrame):
        if not htf_candles.empty and "close" in htf_candles.columns:
            return float(htf_candles["close"].iloc[-1])
        return 0.0
    elif isinstance(htf_candles, (list, tuple)) and len(htf_candles) > 0:
        last = htf_candles[-1]
        if isinstance(last, dict):
            return float(last.get("close", 0.0))
        return float(getattr(last, "close", 0.0))
    return 0.0


def calculate_htf_ma15(htf_candles: Any) -> float:
    """計算大週期 MA15 當前數值"""
    if isinstance(htf_candles, pd.DataFrame):
        if not htf_candles.empty and "close" in htf_candles.columns:
            s_close = htf_candles["close"].astype(float)
            lookback = min(len(s_close), 15)
            if lookback > 0:
                return float(s_close.iloc[-lookback:].mean())
        return 0.0
    elif isinstance(htf_candles, (list, tuple)) and len(htf_candles) > 0:
        closes = []
        for c in htf_candles[-15:]:
            if isinstance(c, dict):
                closes.append(float(c.get("close", 0.0)))
            else:
                closes.append(float(getattr(c, "close", 0.0)))
        return float(sum(closes) / max(1, len(closes)))
    return 0.0


def find_htf_key_pivot(htf_candles: Any, trend: str) -> float:
    """
    從大週期 K 棒中尋找關鍵結構高低點 (Key Pivot Price)：
    - 牛市 (BULLISH)：尋找前波大結構關鍵起漲低點 (Higher Low, HL)
    - 熊市 (BEARISH)：尋找前波大結構關鍵反彈高點 (Lower High, LH)
    """
    trend_norm = str(trend or "").upper()
    if isinstance(htf_candles, pd.DataFrame):
        if htf_candles.empty:
            return 0.0
        df = htf_candles
        n = len(df)
        if trend_norm == "BULLISH":
            # 尋找前波回踩低點 (HL)：倒序尋找近 30 根內三點局部谷底 (Low[i] <= Low[i-1] and Low[i] <= Low[i+1])
            if n >= 5 and "low" in df.columns:
                lows = df["low"].astype(float).values
                for i in range(n - 2, max(0, n - 35), -1):
                    if lows[i] <= lows[i - 1] and lows[i] <= lows[i + 1]:
                        return float(lows[i])
                # 若無明顯三點分形，取前波最低點 (排除當前根)
                lookback = min(n - 1, 20)
                return float(df["low"].iloc[-lookback:-1].min()) if lookback > 1 else float(df["low"].iloc[0])
            return float(df["low"].min()) if "low" in df.columns else 0.0
        else:
            # 尋找前波反彈高點 (LH)：倒序尋找近 30 根內三點局部峰頂 (High[i] >= High[i-1] and High[i] >= High[i+1])
            if n >= 5 and "high" in df.columns:
                highs = df["high"].astype(float).values
                for i in range(n - 2, max(0, n - 35), -1):
                    if highs[i] >= highs[i - 1] and highs[i] >= highs[i + 1]:
                        return float(highs[i])
                lookback = min(n - 1, 20)
                return float(df["high"].iloc[-lookback:-1].max()) if lookback > 1 else float(df["high"].iloc[0])
            return float(df["high"].max()) if "high" in df.columns else 0.0

    elif isinstance(htf_candles, (list, tuple)) and len(htf_candles) > 0:
        n = len(htf_candles)
        if trend_norm == "BULLISH":
            for i in range(n - 2, max(0, n - 35), -1):
                prev_l = float(htf_candles[i - 1].get("low", 0.0) if isinstance(htf_candles[i - 1], dict) else getattr(htf_candles[i - 1], "low", 0.0))
                curr_l = float(htf_candles[i].get("low", 0.0) if isinstance(htf_candles[i], dict) else getattr(htf_candles[i], "low", 0.0))
                next_l = float(htf_candles[i + 1].get("low", 0.0) if isinstance(htf_candles[i + 1], dict) else getattr(htf_candles[i + 1], "low", 0.0))
                if curr_l <= prev_l and curr_l <= next_l:
                    return curr_l
            lookback = min(n - 1, 20)
            sub = htf_candles[-lookback:-1] if lookback > 1 else htf_candles[:-1]
            if sub:
                return min(float(c.get("low", 0.0) if isinstance(c, dict) else getattr(c, "low", 0.0)) for c in sub)
        else:
            for i in range(n - 2, max(0, n - 35), -1):
                prev_h = float(htf_candles[i - 1].get("high", 0.0) if isinstance(htf_candles[i - 1], dict) else getattr(htf_candles[i - 1], "high", 0.0))
                curr_h = float(htf_candles[i].get("high", 0.0) if isinstance(htf_candles[i], dict) else getattr(htf_candles[i], "high", 0.0))
                next_h = float(htf_candles[i + 1].get("high", 0.0) if isinstance(htf_candles[i + 1], dict) else getattr(htf_candles[i + 1], "high", 0.0))
                if curr_h >= prev_h and curr_h >= next_h:
                    return curr_h
            lookback = min(n - 1, 20)
            sub = htf_candles[-lookback:-1] if lookback > 1 else htf_candles[:-1]
            if sub:
                return max(float(c.get("high", 0.0) if isinstance(c, dict) else getattr(c, "high", 0.0)) for c in sub)

    return 0.0


def check_macro_trend_end(
    current_macro_trend: str,
    htf_candles: Any,
    htf_ma15: Optional[float] = None,
    key_pivot_price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    大週期趨勢結束與轉折判定 (Market Structure Break)
    
    參數：
    - current_macro_trend: 當前大週期標籤 ("BULLISH" 或 "BEARISH")
    - htf_candles: 大週期 (如 15m/1H) K 線數據列表或 DataFrame
    - htf_ma15: 大週期 15MA 當前數值 (可選，若為 None 自動從 htf_candles 計算)
    - key_pivot_price: 前波大結構關鍵 Pivot 低點(HL) 或 高點(LH) (可選，若為 None 自動尋找)
    """
    latest_close = extract_latest_close(htf_candles)
    if htf_ma15 is None or htf_ma15 <= 0:
        htf_ma15 = calculate_htf_ma15(htf_candles)
    if key_pivot_price is None or key_pivot_price <= 0:
        key_pivot_price = find_htf_key_pivot(htf_candles, current_macro_trend)

    curr_trend_norm = str(current_macro_trend or "").upper()

    # -------------------------------------------------------------
    # 1. 大週期牛市 (BULLISH) 結束條件
    # -------------------------------------------------------------
    if curr_trend_norm == "BULLISH":
        # 條件 A：收盤價實體跌破大結構前波起漲低點 (Higher Low, HL 結構打破)
        is_structure_broken = bool(key_pivot_price > 0 and latest_close < key_pivot_price)
        
        # 條件 B：大週期收盤價跌破大週期 MA15 均線 (動能扭轉)
        is_ma_broken = bool(htf_ma15 > 0 and latest_close < htf_ma15)
        
        # 雙重確認：結構打破且跌破大週期 MA15 -> 宣告牛市結束！
        if is_structure_broken and is_ma_broken:
            return {
                "macro_trend": "BEARISH", # 或 "SIDEWAYS" (震盪)
                "status_label": "大週期熊市 (順勢做空)",
                "event": "MACRO_BULLISH_ENDED",
                "reason": f"大週期牛市結束：收盤價 {latest_close} 跌破大結構關鍵 HL 支撐 ({key_pivot_price}) 與 HTF MA15 ({htf_ma15:.6f})。",
                "latest_close": latest_close,
                "key_pivot_price": key_pivot_price,
                "htf_ma15": htf_ma15,
            }

    # -------------------------------------------------------------
    # 2. 大週期熊市 (BEARISH) 結束條件
    # -------------------------------------------------------------
    elif curr_trend_norm == "BEARISH":
        # 條件 A：收盤價實體突破大結構前波反彈高點 (Lower High, LH 結構打破)
        is_structure_broken = bool(key_pivot_price > 0 and latest_close > key_pivot_price)
        
        # 條件 B：大週期收盤價突破大週期 MA15 均線 (動能扭轉)
        is_ma_broken = bool(htf_ma15 > 0 and latest_close > htf_ma15)
        
        # 雙重確認：結構打破且站上大週期 MA15 -> 宣告熊市結束！
        if is_structure_broken and is_ma_broken:
            return {
                "macro_trend": "BULLISH",
                "status_label": "大週期牛市 (順勢做多)",
                "event": "MACRO_BEARISH_ENDED",
                "reason": f"大週期熊市結束：收盤價 {latest_close} 強勢突破大結構關鍵 LH 壓力 ({key_pivot_price}) 與 HTF MA15 ({htf_ma15:.6f})。",
                "latest_close": latest_close,
                "key_pivot_price": key_pivot_price,
                "htf_ma15": htf_ma15,
            }

    # 大週期趨勢未被打破，繼續維持原本方向 (保護防護罩，不被 1m 雜訊干擾)
    return {
        "macro_trend": current_macro_trend,
        "status_label": f"大週期{'牛市 (順勢做多)' if curr_trend_norm == 'BULLISH' else '熊市 (順勢做空)'}",
        "event": "MACRO_TREND_CONTINUES",
        "reason": "大週期結構完好，1m/3m 回調未打破大結構支撐/壓力。",
        "latest_close": latest_close,
        "key_pivot_price": key_pivot_price,
        "htf_ma15": htf_ma15,
    }

