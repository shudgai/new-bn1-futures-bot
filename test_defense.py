from core.services.exits.tiered_exit_manager import TieredExitManager, PositionDefenseState

manager = TieredExitManager()

# EXPLOSIVE test
state1 = PositionDefenseState(entry_price=100.0, qty=1.0, side="LONG", snapshot_atr=2.0, mode="EXPLOSIVE")
print("EXPLOSIVE Mode Test")
print("Init state:", state1.current_sl_price)
res = manager.update_tick(state1, 100.0 + 0.75 * 2.0) # exactly 0.75 ATR
print("At 0.75 ATR:", res)
res = manager.update_tick(state1, 100.0 + 1.5 * 2.0) # exactly 1.5 ATR
print("At 1.5 ATR:", res)
res = manager.update_tick(state1, 100.0 + 3.0 * 2.0) # exactly 3.0 ATR
print("At 3.0 ATR:", res)
res = manager.update_tick(state1, 100.0 + 3.0 * 2.0 * 0.84) # drop 16% from peak (peak pct = 6%, current = 5.04%, giveback = 16%)
print("Drop 16% from peak:", res)

print("\nTREND Mode Test")
state2 = PositionDefenseState(entry_price=100.0, qty=1.0, side="LONG", snapshot_atr=2.0, mode="TREND")
res = manager.update_tick(state2, 100.0 + 1.5 * 2.0) # 1.5 ATR
print("At 1.5 ATR (TREND):", res)
