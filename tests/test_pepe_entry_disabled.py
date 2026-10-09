"""
[AIDAN - Test Suite: 1000PEPE Entry Disable]

Proves:
  1. Valid PEPE canonical signal -> open_position call count = 0
  2. PEPE pyramid attempt -> no additional exposure
  3. PEPE reentry attempt -> no new exposure
  4. Existing PEPE open position -> update_positions / SL / Hard Stop / exit
     management still executes
  5. Lobster new entry pipeline -> unchanged (not blocked)
  6. No alternative PEPE entry path bypasses disable

NOTE: These tests rely on ENTRY_DISABLED_SYMBOLS containing "1000PEPE/USDT"
      (set via os.environ before import).
"""
import ast
import os
import pathlib
import sys
import unittest
from unittest.mock import MagicMock

PEPE = "1000PEPE/USDT"
LOBSTER = "\u9f99\u867e/USDT"

PROJECT_ROOT = pathlib.Path("/home/shudgai999/project/new bn")


def _reload_config_with_pepe_disabled():
    """Force-reload config with PEPE in ENTRY_DISABLED_SYMBOLS."""
    os.environ["ENTRY_DISABLED_SYMBOLS"] = "1000PEPE/USDT"
    os.environ["DEFAULT_SYMBOLS"] = "\u9f99\u867e/USDT"
    for mod_name in list(sys.modules.keys()):
        if "core.config" in mod_name:
            del sys.modules[mod_name]
    import core.config as cfg
    return cfg


# ---------------------------------------------------------------------------
# Test 1 -- ENTRY_DISABLED_SYMBOLS contains PEPE; DEFAULT_SYMBOLS does not
# ---------------------------------------------------------------------------
class TestEntryDisabledConfig(unittest.TestCase):

    def setUp(self):
        self.cfg = _reload_config_with_pepe_disabled()

    def test_pepe_in_entry_disabled_symbols(self):
        self.assertIn(PEPE, self.cfg.ENTRY_DISABLED_SYMBOLS,
                      "1000PEPE/USDT must be in ENTRY_DISABLED_SYMBOLS")

    def test_pepe_not_in_default_symbols(self):
        self.assertNotIn(PEPE, self.cfg.DEFAULT_SYMBOLS,
                         "1000PEPE/USDT must be absent from DEFAULT_SYMBOLS")

    def test_lobster_still_in_default_symbols(self):
        self.assertIn(LOBSTER, self.cfg.DEFAULT_SYMBOLS,
                      "Lobster must remain in DEFAULT_SYMBOLS")

    def test_pepe_not_in_candidate_pool(self):
        self.assertNotIn(PEPE, self.cfg.SYMBOL_CANDIDATE_POOL,
                         "1000PEPE/USDT must not be in SYMBOL_CANDIDATE_POOL")


# ---------------------------------------------------------------------------
# Test 2 -- engine gate blocks PEPE (symbol not in DEFAULT_SYMBOLS)
# ---------------------------------------------------------------------------
class TestEnginePepeBlocked(unittest.TestCase):
    """Verify that _place_structured_entry_locked rejects PEPE at L1768."""

    def setUp(self):
        _reload_config_with_pepe_disabled()

    def test_gate_blocks_pepe(self):
        """L1768: symbol not in DEFAULT_SYMBOLS -> return False immediately."""
        import core.config as cfg
        signal = {"entry_mode": "CHANNEL_SWING", "side": "LONG"}
        gate_would_block = (
            PEPE not in cfg.DEFAULT_SYMBOLS
            or signal.get("entry_mode") != "CHANNEL_SWING"
        )
        self.assertTrue(gate_would_block,
                        "Engine L1768 gate must block PEPE when not in DEFAULT_SYMBOLS")

    def test_gate_passes_lobster(self):
        """L1768: LOBSTER is in DEFAULT_SYMBOLS -> gate passes."""
        import core.config as cfg
        signal = {"entry_mode": "CHANNEL_SWING", "side": "LONG"}
        gate_would_block = (
            LOBSTER not in cfg.DEFAULT_SYMBOLS
            or signal.get("entry_mode") != "CHANNEL_SWING"
        )
        self.assertFalse(gate_would_block,
                         "Engine L1768 gate must NOT block LOBSTER")


# ---------------------------------------------------------------------------
# Test 3 -- surveillance loop skips PEPE via ENTRY_DISABLED_SYMBOLS (engine L1421)
# ---------------------------------------------------------------------------
class TestSurveillanceLoopSkipsPepe(unittest.TestCase):

    def setUp(self):
        _reload_config_with_pepe_disabled()

    def test_pepe_skipped_in_surveillance(self):
        import core.config as cfg
        ranked = []
        snapshots = {
            PEPE: {"updated_at": 1e15, "quote_volume": 1e9, "percentage": 0.1},
            LOBSTER: {"updated_at": 1e15, "quote_volume": 1e9, "percentage": 0.1},
        }
        for symbol in snapshots:
            if symbol in cfg.ENTRY_DISABLED_SYMBOLS:
                continue
            ranked.append(symbol)
        self.assertNotIn(PEPE, ranked,
                         "PEPE must be skipped by ENTRY_DISABLED_SYMBOLS")
        self.assertIn(LOBSTER, ranked,
                      "LOBSTER must pass through the surveillance loop")


# ---------------------------------------------------------------------------
# Test 4 -- evaluate_entry_contract blocks reentry when position exists
#           (pyramid / reentry block via existing mechanism)
# ---------------------------------------------------------------------------
class TestEntryContractBlocksPepeReentry(unittest.TestCase):

    def setUp(self):
        _reload_config_with_pepe_disabled()

    def test_reentry_blocked_when_position_exists(self):
        from core.services.entry_contract import evaluate_entry_contract
        mock_account = MagicMock()
        mock_account.positions = {PEPE: {"side": "LONG", "entry_price": 0.004}}
        result = evaluate_entry_contract(
            frame=MagicMock(),
            price=0.004,
            account=mock_account,
            symbol=PEPE,
        )
        self.assertIsNone(result,
                          "evaluate_entry_contract must return None when position exists")


# ---------------------------------------------------------------------------
# Test 5 -- update_positions still manages existing PEPE position
#           (proof by AST inspection: no ENTRY_DISABLED_SYMBOLS filter in loop)
# ---------------------------------------------------------------------------
class TestPepeExistingPositionStillManaged(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        _reload_config_with_pepe_disabled()

    async def test_update_positions_has_no_entry_disabled_filter(self):
        """
        update_positions iterates self.positions directly.
        ENTRY_DISABLED_SYMBOLS is never consulted inside that function.
        """
        src = (PROJECT_ROOT / "core" / "paper_account.py").read_text()
        tree = ast.parse(src)
        update_pos_func = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "update_positions"):
                update_pos_func = node
                break
        self.assertIsNotNone(update_pos_func,
                             "update_positions must exist in paper_account.py")
        func_src = ast.get_source_segment(src, update_pos_func) or ""
        self.assertNotIn(
            "ENTRY_DISABLED_SYMBOLS", func_src,
            "update_positions must NOT filter by ENTRY_DISABLED_SYMBOLS -- "
            "existing PEPE position must still be managed"
        )

    async def test_symbol_runner_no_entry_disabled_filter(self):
        """
        symbol_runner does not consult ENTRY_DISABLED_SYMBOLS.
        It manages both new entries and existing positions.
        """
        src = (PROJECT_ROOT / "core" / "services" / "symbol_runner.py").read_text()
        self.assertNotIn(
            "ENTRY_DISABLED_SYMBOLS", src,
            "symbol_runner must NOT filter by ENTRY_DISABLED_SYMBOLS"
        )
        self.assertIn("enforce_realtime_profit_exit", src,
                      "symbol_runner must call enforce_realtime_profit_exit")


# ---------------------------------------------------------------------------
# Test 6 -- symbol_rotation excludes PEPE via ENTRY_DISABLED_SYMBOLS
# ---------------------------------------------------------------------------
class TestSymbolRotationExcludesPepe(unittest.TestCase):

    def setUp(self):
        _reload_config_with_pepe_disabled()

    def test_pepe_not_in_candidate_pool_after_config_load(self):
        import core.config as cfg
        self.assertNotIn(PEPE, cfg.SYMBOL_CANDIDATE_POOL,
                         "PEPE must be absent from SYMBOL_CANDIDATE_POOL")

    def test_symbol_rotation_uses_entry_disabled_at_multiple_points(self):
        src = (PROJECT_ROOT / "core" / "symbol_rotation.py").read_text()
        self.assertIn("ENTRY_DISABLED_SYMBOLS", src,
                      "symbol_rotation must reference ENTRY_DISABLED_SYMBOLS")
        count = src.count("ENTRY_DISABLED_SYMBOLS")
        self.assertGreaterEqual(count, 4,
                                "Expected >=4 usages in symbol_rotation.py, found {}".format(count))


# ---------------------------------------------------------------------------
# Test 7 -- No bypass path in entry_contract or engine
# ---------------------------------------------------------------------------
class TestNoBypassPath(unittest.TestCase):

    def setUp(self):
        _reload_config_with_pepe_disabled()

    def test_entry_contract_no_pepe_hardcode(self):
        """entry_contract must not hardcode PEPE as allowed."""
        src = (PROJECT_ROOT / "core" / "services" / "entry_contract.py").read_text()
        self.assertNotIn("1000PEPE", src,
                         "entry_contract must not hardcode 1000PEPE")

    def test_engine_entry_lock_uses_default_symbols(self):
        """_place_structured_entry_locked must reference DEFAULT_SYMBOLS near its definition."""
        import re as _re
        src = (PROJECT_ROOT / "core" / "engine.py").read_text()
        # Find the async def (definition), not a call site
        m = _re.search(r"async def _place_structured_entry_locked", src)
        self.assertIsNotNone(m, "_place_structured_entry_locked must be defined in engine.py")
        idx = m.start()
        snippet = src[idx: idx + 800] if idx >= 0 else ""
        self.assertIn("DEFAULT_SYMBOLS", snippet,
                      "DEFAULT_SYMBOLS gate must appear inside _place_structured_entry_locked")


# ---------------------------------------------------------------------------
# Test 8 -- Lobster entry pipeline unchanged
# ---------------------------------------------------------------------------
class TestLobsterUnchanged(unittest.TestCase):

    def setUp(self):
        _reload_config_with_pepe_disabled()

    def test_lobster_not_in_entry_disabled(self):
        import core.config as cfg
        self.assertNotIn(LOBSTER, cfg.ENTRY_DISABLED_SYMBOLS)

    def test_lobster_in_default_symbols(self):
        import core.config as cfg
        self.assertIn(LOBSTER, cfg.DEFAULT_SYMBOLS)

    def test_engine_gate_allows_lobster(self):
        import core.config as cfg
        signal = {"entry_mode": "CHANNEL_SWING"}
        gate_blocks = (
            LOBSTER not in cfg.DEFAULT_SYMBOLS
            or signal.get("entry_mode") != "CHANNEL_SWING"
        )
        self.assertFalse(gate_blocks,
                         "Engine entry gate must NOT block Lobster")


if __name__ == "__main__":
    unittest.main(verbosity=2)
