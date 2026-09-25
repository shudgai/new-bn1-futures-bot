import sys
with open("core/services/symbol_runner.py", "r") as f:
    content = f.read()

old_block = """async def process_single_symbol_runner(
    engine: Any, symbol: str, now_time: float, btc_1m_turn: str | None, daily_halt: bool,
    exit_frame: pd.DataFrame | None = None, exit_quote: float | None = None, exit_only: bool = False
) -> Tuple[str, List[Dict[str, Any]]]:"""

new_block = """async def process_single_symbol_runner(
    engine: Any, symbol: str, now_time: float, btc_1m_turn: str | None, daily_halt: bool,
    exit_frame: pd.DataFrame | None = None, exit_quote: float | None = None, exit_only: bool = False
) -> Tuple[str, List[Dict[str, Any]]]:
    with open("data/symbol_runner_debug.log", "a") as dbgf:
        dbgf.write(f"process_single_symbol_runner called for {symbol}, exit_only={exit_only}\\n")"""

content = content.replace(old_block, new_block)

with open("core/services/symbol_runner.py", "w") as f:
    f.write(content)
