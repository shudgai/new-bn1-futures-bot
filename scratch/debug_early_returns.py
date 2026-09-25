with open("core/services/exits/profit_protection_service.py", "r") as f:
    content = f.read()

old_block = """        from core.services.exits.staged_risk_service import staged_enabled
        if staged_enabled(position):
            return None  # The staged dispatcher is the sole strategy owner.

        # 1. 優先檢查異常 K 線與大瀑布
        from core.guards.abnormal_guard import channel_adverse_exit_reason
        if frame is not None and not frame.empty and 'atr' in frame.iloc[-2]:
            atr = float(frame.iloc[-2]['atr'])
            adverse_reason = channel_adverse_exit_reason(frame, position.get('side', ''), price, atr)
            if adverse_reason:
                return f"PROFIT_PROTECTION_ABNORMAL_EXIT {adverse_reason}" """

new_block = """        from core.services.exits.staged_risk_service import staged_enabled
        with open("/tmp/eval_early.log", "a") as dbgf:
            dbgf.write(f"evaluate_exit started for {position.get('symbol')}, staged={staged_enabled(position)}\\n")
        if staged_enabled(position):
            return None  # The staged dispatcher is the sole strategy owner.

        # 1. 優先檢查異常 K 線與大瀑布
        from core.guards.abnormal_guard import channel_adverse_exit_reason
        if frame is not None and not frame.empty and 'atr' in frame.iloc[-2]:
            atr = float(frame.iloc[-2]['atr'])
            adverse_reason = channel_adverse_exit_reason(frame, position.get('side', ''), price, atr)
            with open("/tmp/eval_early.log", "a") as dbgf:
                dbgf.write(f"adverse_reason={adverse_reason}\\n")
            if adverse_reason:
                return f"PROFIT_PROTECTION_ABNORMAL_EXIT {adverse_reason}" """

content = content.replace(old_block, new_block)

with open("core/services/exits/profit_protection_service.py", "w") as f:
    f.write(content)
