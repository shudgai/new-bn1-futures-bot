"""離線出口政策回放的計算正確性（工具本身不改交易）。"""
import pytest

from tools.channel_exit_policy_replay import net_pnl, price_for_net, summarise


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("target", [0.0, 2.0, -8.4])
def test_price_for_net_round_trip(side, target):
    entry, qty = 100.0, 3.75
    price = price_for_net(side, entry, qty, target)
    assert net_pnl(side, entry, price, qty) == pytest.approx(target, abs=1e-6)


def test_summarise_reports_profit_factor_and_drawdown():
    stats = summarise([5.0, -2.0, 3.0, -4.0])
    assert stats["n"] == 4
    assert stats["net"] == pytest.approx(2.0)
    assert stats["win"] == pytest.approx(50.0)
    assert stats["pf"] == pytest.approx(8.0 / 6.0, abs=1e-3)
    assert stats["dd"] == pytest.approx(-4.0)


def test_summarise_handles_no_trades():
    assert summarise([]) == {"n": 0, "net": 0.0, "win": 0.0, "pf": 0.0, "dd": 0.0}
