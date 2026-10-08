import asyncio
import time

import pytest

from core.services.exits import trend_pivot_exit as policy
from test_live_ma5_owner_exit import market


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_three_doji_never_close_and_old_pending_revoked(symbol, side, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path/"retired.json"))
    p, f, stamp, _ = market(side)
    p["open_timestamp"] = (float(f.iloc[0].timestamp)+100)/1000
    for index in range(6):
        f.loc[index, ["open", "high", "low", "close", "ma5", "kc_middle", "ma15"]] = [
            100., 101., 99., 100., 100., 100., 100.]
    p[policy.STATE_KEY] = dict(policy="kc_reverse_ma5_peak_or_three_closed_doji_v10",
                              identity=policy.position_identity(p), reference_atr=1.,
                              closed_doji=dict(count=3), pending=policy.DOJI_REASON,
                              evidence=dict(reason=policy.DOJI_REASON))
    monkeypatch.setattr(time, "time", lambda:stamp/1000)
    a = PaperAccount()
    a.positions = {symbol:p}
    a.position_meta = {}
    assert not asyncio.run(policy.enforce(a, symbol, 100., f, stamp))
    assert "pending" not in p[policy.STATE_KEY]
    assert "closed_doji" not in p[policy.STATE_KEY]
    assert not policy.close_allowed(p, {}, "Channel Swing "+policy.DOJI_REASON, True)
    assert not a.trades and symbol in a.positions
    assert symbol in PaperAccount().positions
