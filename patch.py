import sys
import core.services.exits.realtime_profit_exit as rpe
old_enforce = rpe.enforce_realtime_profit_exit

async def debug_enforce(*args, **kwargs):
    print("ENFORCE ARGS:", args, kwargs)
    try:
        res = await old_enforce(*args, **kwargs)
        print("ENFORCE RES:", res)
        if not res:
            pos = args[0].account.positions.get(args[1])
            if pos:
                print("POS STATE:", pos.get('__PEAK_TRAILING__'))
        return res
    except Exception as e:
        print("ENFORCE EXCEPTION:", e)
        return False

rpe.enforce_realtime_profit_exit = debug_enforce
