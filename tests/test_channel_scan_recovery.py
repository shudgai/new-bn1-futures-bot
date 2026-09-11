"""Regression contracts for the scan methods lost during extraction."""
import ast
import inspect

import pytest

from core.engine import TradingEngine


def test_engine_self_calls_have_callable_targets():
    tree = ast.parse(inspect.getsource(TradingEngine))
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"}
    assert not {name for name in calls if not callable(getattr(TradingEngine, name, None))}


@pytest.mark.parametrize("allowed,positions,pending,expected", [
    (True, {}, {}, ["1000PEPE/USDT", "龙虾/USDT"]),
    (False, {"HELD": {}}, {}, ["HELD"]),
    (True, {}, {"PENDING": {}}, ["PENDING"]),
])
def test_scan_pool_keeps_account_guards(allowed, positions, pending, expected):
    symbols = ["1000PEPE/USDT", "龙虾/USDT"]
    assert TradingEngine._entry_scan_symbol_snapshot(
        symbols, symbols, positions, pending, allowed, 2) == expected


def test_empty_capacity_refreshes_candidates_and_full_account_waits():
    assert TradingEngine._candidate_board_refresh_needed(False, 0, 0, 2, 15)
    assert not TradingEngine._candidate_board_refresh_needed(False, 2, 0, 2, 15)
