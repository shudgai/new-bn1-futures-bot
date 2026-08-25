with open("core/engine.py", "r") as f:
    content = f.read()

content = content.replace('close_reason="谷底長影線防禦 (不反手)"', 'close_reason="峰頂長影線防禦 (不反手)"', 1)

dead_code = """                                        atr = cr_info.get("atr", live_price * 0.015)
                                        sl_dist, tp_dist = compute_sl_tp_distance(live_price, atr)
                                        sl, tp = build_sl_tp_for_side(live_price, cr_signal, sl_dist, tp_dist)
                                        opened = await self.account.open_position(
                                            symbol=symbol,
                                            side=cr_signal,
                                            price=live_price,
                                            amount_usdt=TRADE_AMOUNT_USDT,
                                            sl=sl,
                                            tp=tp,
                                            reason=cr_info.get("reason", cr_entry_type),
                                            atr=atr,
                                            leverage=get_leverage(symbol),
                                            signal_score=85
                                        )
                                        if opened:
                                            self._continuous_last_entry_bar[symbol] = (cr_signal, entry_bar_id)"""

content = content.replace(dead_code, "")

with open("core/engine.py", "w") as f:
    f.write(content)
