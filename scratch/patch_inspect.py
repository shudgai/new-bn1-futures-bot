import sys

with open("core/services/manual_order_service.py", "r") as f:
    tcontent = f.read()

replacement = """        import logging
        import inspect
        logger = logging.getLogger("uvicorn.error")
        logger.info(f"INSPECTING open_position: {engine.account.open_position}")
        try:
            logger.info(f"Source file: {inspect.getsourcefile(engine.account.open_position)}")
        except Exception as e:
            logger.info(f"Cannot get source file: {e}")
        try:
            success = await engine.account.open_position(
"""
tcontent = tcontent.replace(
    '        try:\n            success = await engine.account.open_position(',
    replacement
)

with open("core/services/manual_order_service.py", "w") as f:
    f.write(tcontent)

