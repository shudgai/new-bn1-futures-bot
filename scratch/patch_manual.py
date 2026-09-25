with open("core/services/manual_order_service.py", "r") as f:
    content = f.read()

content = content.replace(
'''        success = await engine.account.open_position(
            symbol=symbol,
            side=side,
            price=exec_price,''',
'''        import logging
        logger = logging.getLogger("uvicorn.error")
        logger.info(f"engine.account type: {type(engine.account)}")
        try:
            success = await engine.account.open_position(
                symbol=symbol,
                side=side,
                price=exec_price,''')

content = content.replace(
'''            entry_context={
                "entry_mode": "CHANNEL_SWING",
                "manual_entry": True,
            },
            entry_validator=lambda: not manual_entry_guard(symbol, side, frame)
        )''',
'''            entry_context={
                "entry_mode": "CHANNEL_SWING",
                "manual_entry": True,
            },
            entry_validator=lambda: not manual_entry_guard(symbol, side, frame)
        )
        logger.info(f"open_position returned: {success}")
        except Exception as e:
            logger.info(f"open_position raised exception: {e}")
            raise''')

with open("core/services/manual_order_service.py", "w") as f:
    f.write(content)
