from core.engine import TradingEngine
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy
engine = TradingEngine(paper_trading=True)
exit_service = DualTrackExitStrategy()

positions = engine.account.positions
for sym, pos in positions.items():
    print(f"Checking {sym}")
    frame = engine.api.get_klines(sym, "1m", limit=30)
    frame = engine.strategy.compute_indicators(frame)
    price = engine.api.get_live_price(sym)
    reason = exit_service.evaluate_exit(pos, frame, price)
    print(f"Exit reason for {sym}: {reason}")
