# -*- coding: utf-8 -*-
"""
Demon Coin Defense Mechanisms Module (短線妖幣 5 大專用防護機制)

專門針對高波動、極速洗盤的「妖幣」（如 1m ~ 15m 週期），提供五項核心防護：
1. 訊號冷卻期機制 (Cooldown Period)
   - 空單平倉後，強制進入 N 根 K 棒的「開多凍結期」，嚴禁開多。
   - 多單平倉後，強制進入 N 根 K 棒的「開空凍結期」，嚴禁開空。
   - 徹底消滅低位/高位擺盪區塊內「平倉後秒被洗反手」的 Whipsaw 損耗。

2. 多/空門檻不對稱與量能確認 (Asymmetric Thresholds & Volume Confirmation)
   - 開多條件 (Strict Long): 成交量必須 >= 過去 M 根均量的 K 倍 (預設 1.5 倍)。縮量弱反彈僅能平空，絕不開多。
   - 開空條件 (Fast Short): 妖幣雪崩極快，無需高量能門檻，保持敏捷。

3. 爆量插針/超買過熱過濾 (ATR Extreme Filter)
   - 當前 K 棒高低差或實體漲幅 >= X * ATR (預設 2.5 倍)，判定為情緒過熱/爆量插針，一票否決追多。
   - 唯有拉回至均線或通道內才允許重新建倉。

4. 主動式動態追蹤止盈 (ATR Trailing Stop)
   - 獲利展開達門檻 (預設 1.5 ATR 或 ROE >= 8%) 啟動追蹤止盈。
   - 從最高點 (多) 或最低點 (空) 回撤達 Y * ATR (預設 1.0 ATR) 時市價平倉鎖利。

5. 時間停損與無效盤整平倉 (Time-Based Exit)
   - 建倉經過 P 根 K 棒 (預設 8 根)，若價格仍卡在成本區 (+- 0.3 ATR) 或未創新高/新低，主動平倉觀望。
"""

import time
import math
import logging
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger("DemonCoinDefense")


@dataclass
class DemonCoinDefenseConfig:
    """可調參數配置 (可於配置檔或實時覆蓋調整，便於回測優化)"""
    # 模組獨立啟用開關 (預設為 False 保持系統向上向下完全相容，可依標的或回測啟用)
    cooldown_enabled: bool = False
    volume_confirmation_enabled: bool = False
    atr_extreme_filter_enabled: bool = False
    trailing_stop_enabled: bool = False
    time_exit_enabled: bool = False

    # 1. 冷卻期參數
    cooldown_bars: int = 4                # 平倉後凍結 K 棒數 (預設 4 根，1m 週期下約 240 秒)
    cooldown_seconds: float = 240.0       # 時間維度冷卻秒數保底

    # 2. 多空門檻不對稱與量能確認
    vol_ma_period: int = 20               # 成交量均線週期
    long_vol_ratio: float = 1.5           # 開多成交量放大倍數 (curr_vol >= 1.5 * vol_ma20)
    fast_short_enabled: bool = True       # 開空是否保持敏捷無需高量能限制

    # 3. 爆量插針 / 超買過熱過濾
    atr_extreme_mult: float = 2.5         # 單棒高低差或漲幅超過 2.5 * ATR 視為過熱插針

    # 4. 主動式動態追蹤止盈 (ATR Trailing Stop)
    trailing_arm_atr: float = 1.5         # 浮動獲利達 1.5 * ATR 時啟動追蹤
    trailing_pullback_atr: float = 1.0    # 從波段極端點回撤 1.0 * ATR 時即刻市價止盈

    # 5. 時間停損與無效盤整平倉 (Time-Based Exit)
    max_hold_bars: int = 8                # 持倉超時 K 棒數上限 (8 根 K 棒)
    consolidation_atr_threshold: float = 0.3  # 成本區無效盤整浮動範圍 (+- 0.3 * ATR)


class DemonCoinDefenseController:
    """短線妖幣防護控制器 (單例與狀態管理)"""

    _last_exit_by_symbol: Dict[str, Dict[str, Any]] = {}
    config: DemonCoinDefenseConfig = DemonCoinDefenseConfig()

    @classmethod
    def reset_state(cls, symbol: Optional[str] = None):
        """重置狀態 (主要用於測試隔離)"""
        if symbol:
            cls._last_exit_by_symbol.pop(symbol, None)
        else:
            cls._last_exit_by_symbol.clear()

    @classmethod
    def enable_all(cls):
        """一鍵啟用妖幣全部 5 項防護機制"""
        cls.config.cooldown_enabled = True
        cls.config.volume_confirmation_enabled = True
        cls.config.atr_extreme_filter_enabled = True
        cls.config.trailing_stop_enabled = True
        cls.config.time_exit_enabled = True
        logger.info("🛡️ [DEMON_DEFENSE] 妖幣 5 大防護機制已全面啟動！")

    @classmethod
    def disable_all(cls):
        """一鍵停用妖幣 5 項防護機制 (恢復基底常規行為)"""
        cls.config.cooldown_enabled = False
        cls.config.volume_confirmation_enabled = False
        cls.config.atr_extreme_filter_enabled = False
        cls.config.trailing_stop_enabled = False
        cls.config.time_exit_enabled = False
        logger.info("🛡️ [DEMON_DEFENSE] 妖幣防護機制已恢復停用狀態。")

    @classmethod
    def record_exit(
        cls,
        symbol: str,
        closed_side: str,
        exit_price: float,
        timestamp: Optional[float] = None,
        bar_timestamp: Optional[int] = None,
        reason: str = "",
    ):
        """
        記錄平倉事件，供冷卻期與反手防護審查
        """
        now = timestamp or time.time()
        side_norm = str(closed_side).upper()
        record = {
            "side": side_norm,
            "exit_price": float(exit_price),
            "timestamp": now,
            "bar_timestamp": bar_timestamp,
            "reason": reason,
        }
        cls._last_exit_by_symbol[symbol] = record
        logger.info(
            f"🛡️ [DEMON_DEFENSE_EXIT_RECORDED] {symbol} 平倉記錄: side={side_norm}, "
            f"price={exit_price:.6f}, reason={reason}"
        )

    # =========================================================================
    # 1. 訊號冷卻期機制 (Cooldown Period)
    # =========================================================================
    @classmethod
    def check_entry_cooldown(
        cls,
        symbol: str,
        target_side: str,
        current_time: Optional[float] = None,
        current_bar_ts: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """
        審查開倉冷卻期：
        - 空單平倉後：強制進入 N 根 K 棒的「開多凍結期」，嚴禁開多。
        - 多單平倉後：強制進入 N 根 K 棒的「開空凍結期」，嚴禁開空。
        回傳: (passed, reason)
        """
        if not cls.config.cooldown_enabled:
            return True, "COOLDOWN_DISABLED"

        record = cls._last_exit_by_symbol.get(symbol)
        if not record:
            return True, "COOLDOWN_PASS_NO_PRIOR_EXIT"

        last_side = record.get("side", "")
        last_exit_ts = float(record.get("timestamp", 0.0))
        last_bar_ts = record.get("bar_timestamp")

        now = current_time or time.time()
        elapsed_sec = now - last_exit_ts

        target_side_norm = str(target_side).upper()

        # 檢查時間差 (秒數)
        in_cooldown = (elapsed_sec < cls.config.cooldown_seconds)

        # 若有 K 棒 timestamp，檢查 K 棒差數 (1m 棒為 60000ms 或 60s)
        if current_bar_ts is not None and last_bar_ts is not None:
            bar_diff_ms = abs(current_bar_ts - last_bar_ts)
            # 若為毫秒時間戳
            if bar_diff_ms > 1000000:
                bars_passed = bar_diff_ms / 60000.0
            else:
                bars_passed = bar_diff_ms / 60.0
            if bars_passed >= cls.config.cooldown_bars:
                in_cooldown = False

        if not in_cooldown:
            return True, "COOLDOWN_EXPIRED"

        # 🔴 空單平倉 (last_side == SHORT) -> 開多凍結 (target_side == LONG)
        if last_side == "SHORT" and target_side_norm in ("LONG", "BUY"):
            remaining = cls.config.cooldown_seconds - elapsed_sec
            msg = (
                f"VETO_COOLDOWN_LONG_FROZEN (空單剛平倉，開多凍結中: "
                f"剩餘 {max(0.0, remaining):.1f}s / {cls.config.cooldown_bars} 根 K 棒，嚴防低位洗盤反手陷阱)"
            )
            return False, msg

        # 🟢 多單平倉 (last_side == LONG) -> 開空凍結 (target_side == SHORT)
        if last_side == "LONG" and target_side_norm in ("SHORT", "SELL"):
            remaining = cls.config.cooldown_seconds - elapsed_sec
            msg = (
                f"VETO_COOLDOWN_SHORT_FROZEN (多單剛平倉，開空凍結中: "
                f"剩餘 {max(0.0, remaining):.1f}s / {cls.config.cooldown_bars} 根 K 棒，嚴防高位洗盤反手陷阱)"
            )
            return False, msg

        return True, "COOLDOWN_SIDE_ALIGNED"

    # =========================================================================
    # 2. 多/空門檻不對稱與量能確認 (Asymmetric Thresholds & Volume Confirmation)
    # =========================================================================
    @classmethod
    def check_volume_confirmation(
        cls,
        side: str,
        curr_volume: float,
        volume_ma20: float,
    ) -> Tuple[bool, str]:
        """
        量能過濾審查：
        - 開多：必須滿足 curr_volume >= long_vol_ratio * volume_ma20。縮量弱反彈嚴禁開多。
        - 開空：妖幣崩盤極速，保持敏捷，無需高量能限制。
        """
        if not cls.config.volume_confirmation_enabled:
            return True, "VOLUME_CONFIRMATION_DISABLED"

        side_norm = str(side).upper()
        if side_norm in ("LONG", "BUY"):
            if volume_ma20 > 0:
                required_vol = volume_ma20 * cls.config.long_vol_ratio
                if curr_volume < required_vol:
                    return False, (
                        f"VETO_LONG_INSUFFICIENT_VOLUME (開多成交量不足: "
                        f"curr_vol={curr_volume:.1f} < {cls.config.long_vol_ratio}x MA20={required_vol:.1f})"
                    )
        # 開空保持敏捷
        return True, "VOLUME_CONFIRMATION_PASS"

    # =========================================================================
    # 3. 爆量插針/超買過熱過濾 (ATR Extreme Filter)
    # =========================================================================
    @classmethod
    def check_atr_extreme_overheat(
        cls,
        side: str,
        curr_k: Dict[str, Any],
        current_atr: float,
        ma5_curr: Optional[float] = None,
        kc_upper: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        防追高超買過熱過濾：
        當單棒高低振幅或實體漲幅 >= atr_extreme_mult * ATR (預設 2.5 倍)，
        判定為短線情緒過熱/爆量插針，暫停追多，僅允許拉回均線或通道內時才考慮建倉。
        """
        if not cls.config.atr_extreme_filter_enabled:
            return True, "ATR_EXTREME_DISABLED"

        side_norm = str(side).upper()
        if side_norm not in ("LONG", "BUY"):
            return True, "ATR_EXTREME_PASS_SHORT"

        atr_val = float(current_atr or 0.0)
        if atr_val <= 0:
            return True, "ATR_EXTREME_PASS_ZERO_ATR"

        high = float(curr_k.get("high", 0.0))
        low = float(curr_k.get("low", 0.0))
        close = float(curr_k.get("close", 0.0))
        open_p = float(curr_k.get("open", close))

        bar_range = high - low
        body_rise = max(0.0, close - open_p)

        threshold = cls.config.atr_extreme_mult * atr_val

        # 若單棒高低價差或單棒漲幅超過門檻
        is_extreme_spike = (bar_range >= threshold) or (body_rise >= threshold)

        if is_extreme_spike:
            # 檢查是否已拉回至均線 (Close <= MA5) 或回到 KC 通道內部 (Close <= KC_Upper)
            is_pulled_back = False
            if ma5_curr is not None and close <= float(ma5_curr):
                is_pulled_back = True
            elif kc_upper is not None and close <= float(kc_upper):
                is_pulled_back = True

            if not is_pulled_back:
                return False, (
                    f"VETO_ATR_EXTREME_OVERHEATED (單棒爆量插針過熱: "
                    f"range={bar_range:.6f} >= {cls.config.atr_extreme_mult}x ATR={threshold:.6f}，"
                    f"尚未回踩 MA5/通道內部，一票否決追多開倉防天花板接盤)"
                )

        return True, "ATR_EXTREME_PASS"

    # =========================================================================
    # 4. 主動式動態追蹤止盈 (ATR Trailing Stop)
    # =========================================================================
    @classmethod
    def check_atr_trailing_stop(
        cls,
        pos: Dict[str, Any],
        current_quote: float,
        current_atr: float,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        主動式動態追蹤止盈審查：
        - 多單：最高浮盈達到 trailing_arm_atr * ATR 後啟動追蹤，從最高點回撤 trailing_pullback_atr * ATR 立即平多。
        - 空單：最低浮盈達到 trailing_arm_atr * ATR 後啟動追蹤，從最低點回彈 trailing_pullback_atr * ATR 立即平空。
        回傳: (should_exit, reason, meta)
        """
        if not cls.config.trailing_stop_enabled:
            return False, "", {}

        atr_val = float(current_atr or 0.0)
        if atr_val <= 0 or not pos:
            return False, "", {}

        entry_p = float(pos.get("entry_price") or 0.0)
        if entry_p <= 0:
            return False, "", {}

        side = str(pos.get("side") or "").upper()
        high_seen = float(pos.get("highest_price") or pos.get("peak_price") or entry_p)
        low_seen = float(pos.get("lowest_price") or pos.get("trough_price") or entry_p)

        arm_threshold = cls.config.trailing_arm_atr * atr_val
        pullback_threshold = cls.config.trailing_pullback_atr * atr_val

        if side == "LONG":
            max_gain = high_seen - entry_p
            # 獲利達到門檻，啟動追蹤止盈
            if max_gain >= arm_threshold:
                drawdown = high_seen - current_quote
                if drawdown >= pullback_threshold:
                    reason = "EXIT_BY_ATR_TRAILING_STOP"
                    meta = {
                        "trigger": "LONG_ATR_TRAILING_STOP",
                        "high_seen": high_seen,
                        "entry_price": entry_p,
                        "drawdown": drawdown,
                        "pullback_threshold": pullback_threshold,
                    }
                    return True, reason, meta

        elif side == "SHORT":
            max_gain = entry_p - low_seen
            # 獲利達到門檻，啟動追蹤止盈
            if max_gain >= arm_threshold:
                bounce = current_quote - low_seen
                if bounce >= pullback_threshold:
                    reason = "EXIT_BY_ATR_TRAILING_STOP"
                    meta = {
                        "trigger": "SHORT_ATR_TRAILING_STOP",
                        "low_seen": low_seen,
                        "entry_price": entry_p,
                        "bounce": bounce,
                        "pullback_threshold": pullback_threshold,
                    }
                    return True, reason, meta

        return False, "", {}

    # =========================================================================
    # 5. 時間停損與無效盤整平倉 (Time-Based Exit)
    # =========================================================================
    @classmethod
    def check_time_based_exit(
        cls,
        pos: Dict[str, Any],
        current_quote: float,
        current_atr: float,
        bars_held: int,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        時間停損與無效盤整平倉審查：
        建倉經過 max_hold_bars 根 K 棒後，價格仍未脫離成本區 (+- consolidation_atr_threshold * ATR)，
        或未創出有效新高/新低，判定動能喪失，市價離場觀望。
        """
        if not cls.config.time_exit_enabled:
            return False, "", {}

        if not pos or bars_held < cls.config.max_hold_bars:
            return False, "", {}

        entry_p = float(pos.get("entry_price") or 0.0)
        atr_val = float(current_atr or 0.0)
        if entry_p <= 0 or atr_val <= 0:
            return False, "", {}

        price_delta = abs(current_quote - entry_p)
        consolidation_range = cls.config.consolidation_atr_threshold * atr_val

        # 價格仍在成本區間附近盤整 (無效死水)
        if price_delta <= consolidation_range:
            reason = "EXIT_BY_TIME_BASED_CONSOLIDATION"
            meta = {
                "trigger": "TIME_BASED_CONSOLIDATION",
                "bars_held": bars_held,
                "price_delta": price_delta,
                "consolidation_range": consolidation_range,
                "max_hold_bars": cls.config.max_hold_bars,
            }
            return True, reason, meta

        return False, "", {}
