import re

with open('core/engine.py', 'r') as f:
    content = f.read()

# Replace the first await self.account.open_position block (around line 2843)
old_block_1 = """                                        await self.account.open_position(
                                            symbol=symbol,
                                            side=cr_signal,
                                            price=live_price,
                                            amount_usdt=self.account.get_wallet_balance() / max(MAX_SLOTS, 1) if MAX_SLOTS > 0 else TRADE_AMOUNT_USDT,
                                            sl=sl,
                                            tp=tp,
                                            reason=cr_info.get("reason", cr_entry_type),
                                            atr=atr,
                                            leverage=get_leverage(symbol),
                                            signal_score=100
                                        )"""

new_block_1 = """                                        total_usdt = self.account.get_wallet_balance() / max(MAX_SLOTS, 1) if MAX_SLOTS > 0 else TRADE_AMOUNT_USDT
                                        amount_usdt_market = total_usdt * 0.5
                                        amount_usdt_limit = total_usdt * 0.5
                                        limit_target_price = live_price - (atr * 0.5) if cr_signal == "LONG" else live_price + (atr * 0.5)

                                        # 首倉 50%：立刻市價進場
                                        await self.account.open_position(
                                            symbol=symbol,
                                            side=cr_signal,
                                            price=live_price,
                                            amount_usdt=amount_usdt_market,
                                            sl=sl,
                                            tp=tp,
                                            reason=f"{cr_info.get('reason', cr_entry_type)} (首倉)",
                                            atr=atr,
                                            leverage=get_leverage(symbol),
                                            signal_score=100
                                        )
                                        # 補倉 50%：掛限價單等回踩
                                        await self.account.place_limit_entry(
                                            symbol=symbol,
                                            side=cr_signal,
                                            target_price=limit_target_price,
                                            amount_usdt=amount_usdt_limit,
                                            sl=sl,
                                            tp=tp,
                                            reason=f"{cr_info.get('reason', cr_entry_type)} (補倉限價)",
                                            atr=atr,
                                            leverage=get_leverage(symbol),
                                            signal_score=100,
                                            timeframe="1m"
                                        )"""

# Replace the second await self.account.open_position block (around line 2880)
old_block_2 = """                                        await self.account.open_position(
                                            symbol=symbol,
                                            side=cr_signal,
                                            price=live_price,
                                            amount_usdt=self.account.get_wallet_balance() / max(MAX_SLOTS, 1) if MAX_SLOTS > 0 else TRADE_AMOUNT_USDT,
                                            sl=sl,
                                            tp=tp,
                                            reason=cr_info.get("reason", cr_entry_type),
                                            atr=atr,
                                            leverage=get_leverage(symbol),
                                            signal_score=85
                                        )"""

new_block_2 = """                                        total_usdt = self.account.get_wallet_balance() / max(MAX_SLOTS, 1) if MAX_SLOTS > 0 else TRADE_AMOUNT_USDT
                                        amount_usdt_market = total_usdt * 0.5
                                        amount_usdt_limit = total_usdt * 0.5
                                        limit_target_price = live_price - (atr * 0.5) if cr_signal == "LONG" else live_price + (atr * 0.5)

                                        # 首倉 50%：立刻市價進場
                                        await self.account.open_position(
                                            symbol=symbol,
                                            side=cr_signal,
                                            price=live_price,
                                            amount_usdt=amount_usdt_market,
                                            sl=sl,
                                            tp=tp,
                                            reason=f"{cr_info.get('reason', cr_entry_type)} (首倉)",
                                            atr=atr,
                                            leverage=get_leverage(symbol),
                                            signal_score=85
                                        )
                                        # 補倉 50%：掛限價單等回踩
                                        await self.account.place_limit_entry(
                                            symbol=symbol,
                                            side=cr_signal,
                                            target_price=limit_target_price,
                                            amount_usdt=amount_usdt_limit,
                                            sl=sl,
                                            tp=tp,
                                            reason=f"{cr_info.get('reason', cr_entry_type)} (補倉限價)",
                                            atr=atr,
                                            leverage=get_leverage(symbol),
                                            signal_score=85,
                                            timeframe="1m"
                                        )"""

content = content.replace(old_block_1, new_block_1)
content = content.replace(old_block_2, new_block_2)

with open('core/engine.py', 'w') as f:
    f.write(content)

