import re

with open('core/paper_account.py', 'r') as f:
    content = f.read()

start_marker = "            # ----------------------------------------------------------------\n            # 固定 USDT 金額鎖利（Profit Lock in USDT）"
end_marker = "            # ----------------------------------------------------------------\n            # 固定百分比鎖利（Fixed Profit Lock by Unlevered %）"

start_idx = content.find(start_marker)
end_idx = content.find(end_marker, start_idx)

new_block = """            # ----------------------------------------------------------------
            # 動態階梯鎖利 (Dynamic Tiered Profit Lock)
            # 自動計算手續費2倍為起點，依照本金級距遞增步距
            # ----------------------------------------------------------------
            if ENABLE_PROFIT_LOCK_USDT:
                qty = float(pos.get("qty") or meta.get("qty") or 0.0)
                leverage = float(pos.get("leverage") or meta.get("leverage") or 1.0)
                # 帳面未實現利潤（USDT，已含槓桿）
                unrealized_usdt = float(pos.get("unrealized_pnl") or 0.0)
                # 峰值利潤（USDT）：持續追蹤歷史最高值
                peak_usdt_key = "profit_lock_peak_usdt"
                prev_peak_usdt = float(meta.get(peak_usdt_key) or 0.0)
                peak_usdt = max(prev_peak_usdt, unrealized_usdt)
                if peak_usdt > prev_peak_usdt:
                    meta[peak_usdt_key] = peak_usdt

                # 1. 自動計算手續費 (幣安 Taker 費率單程約 0.05%，來回 0.1%)
                notional_value = qty * entry_p
                margin_used = notional_value / leverage
                round_trip_fee = notional_value * 0.001
                
                # 2. 鎖利起點 = 手續費的 2 倍
                base_trigger = round_trip_fee * 2.0
                dynamic_trigger = base_trigger
                dynamic_floor = base_trigger
                
                # 3. 階梯步距 = 依本金級距遞增
                import math
                if margin_used <= 200:
                    dynamic_step = 1.0
                else:
                    dynamic_step = 1.0 + math.ceil((margin_used - 200.0) / 100.0)

                if peak_usdt + 1e-9 >= dynamic_trigger and qty > 0 and entry_p > 0:
                    # ── 階梯地板：從起始點開始，每超過一階步距，地板推升 ──
                    step_floor_usdt = dynamic_floor + int((peak_usdt - dynamic_trigger) / dynamic_step) * dynamic_step
                    notional_units = qty  # qty 已為合約張數
                    floor_price_move = step_floor_usdt / max(notional_units, 1e-12)
                    if side == "LONG":
                        floor_sl = entry_p + floor_price_move
                    else:
                        floor_sl = entry_p - floor_price_move

                    current_sl = float(pos.get("sl") or meta.get("sl") or 0.0)
                    improves_usdt = (
                        floor_sl > current_sl + entry_p * 0.00001 if side == "LONG" 
                        else current_sl <= 0 or floor_sl < current_sl - entry_p * 0.00001
                    )
                    
                    if improves_usdt:
                        pos["sl"] = floor_sl
                        meta["sl"] = floor_sl
                        pos["is_breakeven_moved"] = True
                        meta["is_breakeven_moved"] = True
                        pos["profit_lock_usdt_armed"] = True
                        meta["profit_lock_usdt_armed"] = True
                        
                        self.log(
                            f"🔐 [階梯鎖利] {symbol} 峰值 {peak_usdt:.2f}U "
                            f"(本金 {margin_used:.0f}U，步距 {dynamic_step}U)，"
                            f"鎖定 {step_floor_usdt:.2f}U，保護線 {floor_sl:.6g}",
                            "SUCCESS",
                        )

"""

new_content = content[:start_idx] + new_block + content[end_idx:]

with open('core/paper_account.py', 'w') as f:
    f.write(new_content)

