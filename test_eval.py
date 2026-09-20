import asyncio
from core.engine import engine
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
from core.services.strategies.outer_strategy import ck_direction

async def main():
    await engine.initialize()
    for symbol, pos in engine.account.positions.items():
        if symbol not in engine.market_data: continue
        df = engine.market_data[symbol]
        print(f"--- {symbol} ---")
        strategy = DualTrackExitStrategy()
        # Test my logic directly
        try:
            prev_1 = df.iloc[-2]
            prev_2 = df.iloc[-3]
            kc_mid_prev1 = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0.0)))
            kc_mid_prev2 = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0.0)))
            kc_mid_prev3 = float(df.iloc[-4].get("kc_middle", df.iloc[-4].get("ema_20", 0.0))) if len(df) >= 4 else 0.0
            
            ui_ck_trend = None
            if kc_mid_prev1 > kc_mid_prev2 and kc_mid_prev2 > kc_mid_prev3:
                ui_ck_trend = "LONG"
            elif kc_mid_prev1 < kc_mid_prev2 and kc_mid_prev2 < kc_mid_prev3:
                ui_ck_trend = "SHORT"
            print(f"ui_ck_trend: {ui_ck_trend}")
            
            exit_reason = strategy.evaluate_exit(pos, df, df.iloc[-1]["close"])
            print(f"evaluate_exit returned: {exit_reason}")
            print(f"bar_id stored: {pos.get('last_evaluated_closed_bar_id')}")
            bar_id = prev_1.name if hasattr(prev_1, "name") else str(prev_1.to_dict())
            print(f"current bar_id: {bar_id}")
        except Exception as e:
            print(f"Error: {e}")

asyncio.run(main())
