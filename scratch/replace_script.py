import re

with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

# Add the swing functions
swing_funcs = """
def is_confirmed_swing_high(klines: pd.DataFrame) -> bool:
    if len(klines) < 3:
        return False
    is_pivot = float(klines.iloc[-2]['high']) > float(klines.iloc[-3]['high']) and float(klines.iloc[-2]['high']) > float(klines.iloc[-1]['high'])
    break_low = float(klines.iloc[-1]['close']) < min(float(klines.iloc[-2]['open']), float(klines.iloc[-2]['close']))
    return is_pivot and break_low

def is_confirmed_swing_low(klines: pd.DataFrame) -> bool:
    if len(klines) < 3:
        return False
    is_pivot = float(klines.iloc[-2]['low']) < float(klines.iloc[-3]['low']) and float(klines.iloc[-2]['low']) < float(klines.iloc[-1]['low'])
    break_high = float(klines.iloc[-1]['close']) > max(float(klines.iloc[-2]['open']), float(klines.iloc[-2]['close']))
    return is_pivot and break_high

class DualTrackExitStrategy(IExitStrategy):
"""
content = content.replace("class DualTrackExitStrategy(IExitStrategy):", swing_funcs)

# Modify evaluate_exit to pass frame
content = content.replace("return self.check_exit_and_manage_tp(position, current_kline, indicators)", "return self.check_exit_and_manage_tp(position, current_kline, indicators, frame)")
content = content.replace("def check_exit_and_manage_tp(self, position, current_kline, indicators):", "def check_exit_and_manage_tp(self, position, current_kline, indicators, frame):")

# Remove Binance replace limit order (to allow Python side blocking)
old_replace = """        if need_update_tp and self.order_manager:
            # 僅在 K 線收盤更新一張 Reduce-Only 限價止盈單
            self.order_manager.replace_reduce_only_limit_tp(
                symbol=position['symbol'],
                side="BUY" if side == "SHORT" else "SELL",
                quantity=position['size'],
                price=position['current_tp_price']
            )
            logger.info(f"[TP_RATCHET_UPDATED] {side} {position.get('symbol', 'UNKNOWN')}: 鎖利線單向推進至 {position['current_tp_price']:.4f}")"""

new_replace = """        if need_update_tp:
            # 將鎖利單改為本地觸發，以配合「真峰谷確認」的守門員機制
            logger.info(f"[TP_RATCHET_UPDATED] {side} {position.get('symbol', 'UNKNOWN')}: 本地鎖利防線推進至 {position['current_tp_price']:.4f}")"""
content = content.replace(old_replace, new_replace)

# Modify the exit logic
old_exit = """        # ==================== 3. KC 轉向平倉判定 ====================
        # 只要收盤價依然在軌道外側，絕對不准平倉，沿路死抱到底！
        if is_outside_band:
            return None

        # 收回軌內後，若 KC 通道實質反向轉向，波段結束市價平倉
        if side == "SHORT" and kc_trend == "UP":
            logger.warning(f"[EXIT_KC_REVERSAL] SHORT {position.get('symbol', 'UNKNOWN')}: 跌勢竭盡且 KC 正式向上轉向，波段市價平倉了結！")
            return "EXIT_KC_REVERSAL"

        if side == "LONG" and kc_trend == "DOWN":
            logger.warning(f"[EXIT_KC_REVERSAL] LONG {position.get('symbol', 'UNKNOWN')}: 漲勢竭盡且 KC 正式向下轉向，波段市價平倉了結！")
            return "EXIT_KC_REVERSAL"

        return None"""

new_exit = """        # ==================== 3. 真峰谷與轉向/破線平倉 (守門員機制) ====================
        # 在外軌外側時，無論是否出現峰谷，皆優先維持外軌護盾死抱！
        if is_outside_band:
            position['has_confirmed_swing'] = False
            return None

        # 收回軌內後，判斷是否走出真峰頂/真谷底
        if side == "LONG":
            if is_confirmed_swing_high(frame):
                position['has_confirmed_swing'] = True
            # 若未形成真峰頂結構，一律不觸發鎖利平倉，維持死抱
            if not position.get('has_confirmed_swing', False):
                return None
        elif side == "SHORT":
            if is_confirmed_swing_low(frame):
                position['has_confirmed_swing'] = True
            # 若未形成真谷底結構，一律不觸發鎖利平倉，維持死抱
            if not position.get('has_confirmed_swing', False):
                return None

        # --- 滿足「真峰谷確認」後，才允許觸發平倉 ---
        if side == "LONG":
            if current_tp_price and eval_price <= current_tp_price:
                logger.warning(f"[EXIT_TRAILING_STOP] LONG {position.get('symbol', 'UNKNOWN')}: 跌破 1.5 ATR 本地防守線，波段獲利了結！")
                return "EXIT_TRAILING_STOP"
            if kc_trend == "DOWN":
                logger.warning(f"[EXIT_KC_REVERSAL] LONG {position.get('symbol', 'UNKNOWN')}: 漲勢竭盡且 KC 正式向下轉向，波段市價平倉了結！")
                return "EXIT_KC_REVERSAL"

        elif side == "SHORT":
            if current_tp_price and eval_price >= current_tp_price:
                logger.warning(f"[EXIT_TRAILING_STOP] SHORT {position.get('symbol', 'UNKNOWN')}: 突破 1.5 ATR 本地防守線，波段獲利了結！")
                return "EXIT_TRAILING_STOP"
            if kc_trend == "UP":
                logger.warning(f"[EXIT_KC_REVERSAL] SHORT {position.get('symbol', 'UNKNOWN')}: 跌勢竭盡且 KC 正式向上轉向，波段市價平倉了結！")
                return "EXIT_KC_REVERSAL"

        return None"""
content = content.replace(old_exit, new_exit)

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(content)

