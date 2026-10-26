"""雙槽位 (2-Slots) K 線圖與繪圖模組
支援 Slot 1 (龙虾/USDT) 與 Slot 2 (1000PEPE/USDT) 獨立指標計算與繪圖，
內建中文字型自動探測與渲染防亂碼機制。
"""

import os
import io
import math
from typing import Optional, Dict, Any, Tuple, List
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")  # 確保無 GUI 伺服器環境相容
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm


# ─────────────────────────────────────────────────────────────────────────────
# 1. 中文字型配置：解決「龙虾」與中文標題方框亂碼問題
# ─────────────────────────────────────────────────────────────────────────────
def setup_chinese_font() -> Optional[str]:
    """自動探測並配置支援簡體中文的字型，避免 Matplotlib 出現豆腐塊 (方框) 亂碼。"""
    candidate_fonts = [
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "Source Han Sans CN",
        "SimHei",
        "Microsoft YaHei",
        "PingFang SC",
        "Heiti SC",
        "Droid Sans Fallback",
        "DejaVu Sans",
    ]
    
    # 搜尋系統已安裝之字型名稱
    available_font_names = {f.name for f in fm.fontManager.ttflist}
    selected_font = None
    for name in candidate_fonts:
        if name in available_font_names:
            selected_font = name
            break

    # 若未直接匹配名稱，搜尋專案內與系統 fonts 下的中文 ttf/ttc 檔案
    project_font = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../assets/fonts/wqy-microhei.ttc"))
    user_font = os.path.expanduser("~/.local/share/fonts/wqy-microhei.ttc")
    font_file_candidates = [
        project_font,
        user_font,
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    ]
    for font_path in font_file_candidates:
        if os.path.exists(font_path):
            try:
                fm.fontManager.addfont(font_path)
                prop = fm.FontProperties(fname=font_path)
                selected_font = prop.get_name()
                break
            except Exception:
                pass

    # 設定 Matplotlib 全域字型
    if selected_font:
        plt.rcParams["font.sans-serif"] = [selected_font] + candidate_fonts
    else:
        plt.rcParams["font.sans-serif"] = candidate_fonts

    plt.rcParams["axes.unicode_minus"] = False  # 正常顯示負號
    return selected_font


# 初始化字型設定
ACTIVE_CHINESE_FONT = setup_chinese_font()


# ─────────────────────────────────────────────────────────────────────────────
# 2. 獨立指標計算模組 (EMA, KC 通道, ATR, RSI)
# ─────────────────────────────────────────────────────────────────────────────
def compute_slot_indicators(
    df: pd.DataFrame,
    ema_fast_span: int = 15,
    ema_slow_span: int = 40,
    kc_span: int = 20,
    atr_period: int = 14,
    rsi_period: int = 14,
    kc_mult: float = 1.5,
) -> pd.DataFrame:
    """為個別 Slot 獨立計算所有技術指標，杜絕前視偏差與跨標的資料混淆。"""
    df = df.copy()

    # 確保數值型別
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 1. 均線系統 (EMA15, EMA40, MA5)
    df["ma5"] = df["close"].rolling(window=5, min_periods=1).mean()
    df["ema_fast"] = df["close"].ewm(span=ema_fast_span, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=ema_slow_span, adjust=False).mean()

    # 2. ATR (True Range 週期平均)
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.rolling(window=atr_period, min_periods=1).mean()

    # 3. Keltner Channels (KC 通道)
    df["kc_middle"] = df["close"].rolling(window=kc_span, min_periods=1).mean()
    df["kc_upper"] = df["kc_middle"] + kc_mult * df["atr"]
    df["kc_lower"] = df["kc_middle"] - kc_mult * df["atr"]

    # 4. RSI (相對強弱指標)
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=rsi_period, min_periods=1).mean()
    avg_loss = loss.rolling(window=rsi_period, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100.0 - (100.0 / (1.0 + rs))
    df["rsi"] = df["rsi"].fillna(50.0)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# 3. 單一槽位子圖繪製邏輯
# ─────────────────────────────────────────────────────────────────────────────
def draw_slot_subplot(
    ax: plt.Axes,
    ax_rsi: Optional[plt.Axes],
    df: pd.DataFrame,
    slot_label: str,
    symbol: str,
    position: Optional[Dict[str, Any]] = None,
    signals: Optional[List[Dict[str, Any]]] = None,
):
    """在指定的 Matplotlib Axes 上繪製該 Slot 的 K 線、均線、KC 通道與進出場線。"""
    x_indices = np.arange(len(df))
    up = df["close"] >= df["open"]
    down = df["close"] < df["open"]

    col_up = "#00c076"    # 綠色陽線 (Binance 主流配色)
    col_down = "#ff3b30"  # 紅色陰線
    bar_width = 0.6

    # 1. 繪製 K 線影線與實體柱
    ax.vlines(x_indices[up], df["low"][up], df["high"][up], color=col_up, linewidth=1.0, alpha=0.9)
    ax.vlines(x_indices[down], df["low"][down], df["high"][down], color=col_down, linewidth=1.0, alpha=0.9)
    ax.bar(x_indices[up], df["close"][up] - df["open"][up], bottom=df["open"][up], color=col_up, width=bar_width, alpha=0.9)
    ax.bar(x_indices[down], df["open"][down] - df["close"][down], bottom=df["close"][down], color=col_down, width=bar_width, alpha=0.9)

    # 2. 繪製技術指標曲線
    ax.plot(x_indices, df["ma5"], color="#3b82f6", linewidth=1.2, linestyle="--", label="MA5")
    ax.plot(x_indices, df["ema_fast"], color="#00e5ff", linewidth=1.4, label="EMA15 (Fast)")
    ax.plot(x_indices, df["ema_slow"], color="#ff9100", linewidth=1.6, label="EMA40 (Slow)")
    ax.plot(x_indices, df["kc_upper"], color="#787b86", linewidth=1.0, linestyle="--", label="KC Upper")
    ax.plot(x_indices, df["kc_middle"], color="#94a3b8", linewidth=1.0, linestyle=":", label="KC Middle")
    ax.plot(x_indices, df["kc_lower"], color="#787b86", linewidth=1.0, linestyle="--", label="KC Lower")
    ax.fill_between(x_indices, df["kc_upper"], df["kc_lower"], color="#06b6d4", alpha=0.04)

    # 3. 標記當前持倉狀態、進場價、止損線與止盈線
    if position:
        side = str(position.get("side", "")).upper()
        entry_price = float(position.get("entry_price", 0.0))
        sl_price = float(position.get("sl", 0.0))
        tp_price = float(position.get("tp", 0.0))
        margin = float(position.get("margin", 0.0))
        leverage = position.get("leverage", 1)

        pos_info = f"【持倉: {side} {leverage}x | 本金: {margin:.2f}U】"
        ax.set_title(f"[{slot_label}] {symbol} {pos_info}", fontsize=12, fontweight="bold", pad=8, color="#0f172a")

        if entry_price > 0:
            ax.axhline(entry_price, color="#3b82f6", linestyle="-", linewidth=1.5, alpha=0.85, label=f"Entry: {entry_price:.6g}")
        if sl_price > 0:
            ax.axhline(sl_price, color="#ef4444", linestyle="--", linewidth=1.5, alpha=0.9, label=f"Stop-Loss: {sl_price:.6g}")
        if tp_price > 0:
            ax.axhline(tp_price, color="#10b981", linestyle="--", linewidth=1.5, alpha=0.9, label=f"Take-Profit: {tp_price:.6g}")
    else:
        ax.set_title(f"[{slot_label}] {symbol} 【空槽待機 (Empty Slot)】", fontsize=12, fontweight="bold", pad=8, color="#475569")

    # 4. 歷史進出場信號標記 (若有傳入 signals)
    if signals:
        for sig in signals:
            idx = sig.get("index")
            sig_type = sig.get("type", "").upper()
            price = sig.get("price", df["close"].iloc[idx] if idx is not None else 0.0)
            if idx is not None and 0 <= idx < len(df):
                if sig_type in ("BUY", "LONG"):
                    ax.scatter(idx, price * 0.998, marker="^", color="#10b981", s=110, zorder=6)
                elif sig_type in ("SELL", "SHORT"):
                    ax.scatter(idx, price * 1.002, marker="v", color="#ef4444", s=110, zorder=6)

    ax.set_ylabel("Price (USDT)", fontsize=10)
    ax.grid(True, linestyle=":", alpha=0.4)
    ax.legend(loc="upper left", framealpha=0.85, fontsize=8)

    # 5. 下方 RSI 指標子圖 (若存在)
    if ax_rsi is not None and "rsi" in df.columns:
        ax_rsi.plot(x_indices, df["rsi"], color="#a855f7", linewidth=1.3, label="RSI(14)")
        ax_rsi.axhline(70, color="#ef4444", linestyle=":", linewidth=0.9, alpha=0.7)
        ax_rsi.axhline(30, color="#10b981", linestyle=":", linewidth=0.9, alpha=0.7)
        ax_rsi.fill_between(x_indices, 30, 70, color="#9333ea", alpha=0.05)
        ax_rsi.set_ylabel("RSI", fontsize=9)
        ax_rsi.set_ylim(0, 100)
        ax_rsi.grid(True, linestyle=":", alpha=0.3)
        ax_rsi.legend(loc="upper left", fontsize=7)


# ─────────────────────────────────────────────────────────────────────────────
# 4. 雙槽位整合圖表生成器 (Dual-Slot Chart Export)
# ─────────────────────────────────────────────────────────────────────────────
def generate_dual_slot_chart(
    slot1_df: pd.DataFrame,
    slot2_df: pd.DataFrame,
    slot1_symbol: str = "龙虾/USDT",
    slot2_symbol: str = "1000PEPE/USDT",
    slot1_pos: Optional[Dict[str, Any]] = None,
    slot2_pos: Optional[Dict[str, Any]] = None,
    slot1_signals: Optional[List[Dict[str, Any]]] = None,
    slot2_signals: Optional[List[Dict[str, Any]]] = None,
    output_filepath: Optional[str] = "dual_slot_chart.png",
    figsize: Tuple[int, int] = (16, 12),
) -> bytes:
    """生成 Slot 1 與 Slot 2 雙槽位獨立圖表並輸出為 PNG 圖片。
    
    :param slot1_df: Slot 1 歷史 K 線 (含 open, high, low, close, volume)
    :param slot2_df: Slot 2 歷史 K 線
    :param slot1_symbol: Slot 1 標的名稱，嚴格使用簡體字「龙虾/USDT」
    :param slot2_symbol: Slot 2 標的名稱「1000PEPE/USDT」
    :param slot1_pos: Slot 1 當前持倉資訊 (可選)
    :param slot2_pos: Slot 2 當前持倉資訊 (可選)
    :param output_filepath: 檔案儲存路徑 (若為 None 則僅回傳 bytes)
    :return: PNG 圖片的二進位 bytes
    """
    setup_chinese_font()

    # 1. 獨立計算兩套指標
    df1 = compute_slot_indicators(slot1_df)
    df2 = compute_slot_indicators(slot2_df)

    # 2. 建立 4 列子圖 (Slot 1 Price, Slot 1 RSI, Slot 2 Price, Slot 2 RSI)
    fig, axes = plt.subplots(
        4, 1, figsize=figsize,
        gridspec_kw={"height_ratios": [3, 1, 3, 1]},
        sharex=False,
    )
    ax1_price, ax1_rsi, ax2_price, ax2_rsi = axes

    # 3. 繪製 Slot 1
    draw_slot_subplot(
        ax=ax1_price,
        ax_rsi=ax1_rsi,
        df=df1,
        slot_label="Slot 1",
        symbol=slot1_symbol,
        position=slot1_pos,
        signals=slot1_signals,
    )

    # 4. 繪製 Slot 2
    draw_slot_subplot(
        ax=ax2_price,
        ax_rsi=ax2_rsi,
        df=df2,
        slot_label="Slot 2",
        symbol=slot2_symbol,
        position=slot2_pos,
        signals=slot2_signals,
    )

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    img_bytes = buf.getvalue()

    if output_filepath:
        with open(output_filepath, "wb") as f:
            f.write(img_bytes)

    plt.close(fig)
    return img_bytes
