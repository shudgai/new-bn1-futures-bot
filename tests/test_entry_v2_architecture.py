"""Entry v2 Phase 1 — isolated strategy + canonical 5M context tests.

Every test exercises production code in
``core/services/context_5m.py`` and
``core/services/strategies/entry_v2_strategy.py``.
No engine / entry_contract wiring is tested here (Phase 1 only).
"""
import math
import os

import pandas as pd
import pytest

from core.services import context_5m as c5
from core.services.context_5m import (
    LONG,
    SHORT,
    Closed5mContext,
    build_closed_5m_table,
    get_closed_5m_context,
    is_opposite_slope_veto,
)
from core.services.strategies import entry_v2_strategy as ev2
from core.services.strategies.entry_v2_strategy import (
    ENTRY_MODE,
    SEARCH,
    VETO_WAIT,
    WAIT_FRESH_CONFIRM,
    ClosedBar1m,
    EntryV2Candidate,
    EntryV2Strategy,
    compute_closed_1m_bars,
)

MIN = 60_000
FIVE = 300_000
SYM = "LOBSTER/USDT"


def ts(hhmmss: str) -> int:
    return int(pd.Timestamp(f"2026-10-03 {hhmmss}", tz="UTC").value // 1_000_000)


# ==========================================================================
# 5M canonical context
# ==========================================================================
def _one_minute_frame(start="08:00:00", end="10:14:00"):
    start_ms, end_ms = ts(start), ts(end)
    rows = []
    for k, t in enumerate(range(start_ms, end_ms + MIN, MIN)):
        c = 1.0 + 0.002 * math.sin(k / 3.0) + 0.0001 * k
        rows.append({"timestamp": t, "open": c - 0.0005, "high": c + 0.001,
                     "low": c - 0.001, "close": c, "volume": 10.0})
    return pd.DataFrame(rows)


def _expected_5m(frame, decision):
    """Independent (loop-based) recomputation of the visible closed 5M bar."""
    closes = {}
    for t, c in zip(frame["timestamp"], frame["close"]):
        if t + MIN <= decision:
            closes.setdefault((t // FIVE) * FIVE, []).append((t, c))
    buckets = sorted(b for b in closes if b + FIVE <= decision)
    series = [sorted(closes[b])[-1][1] for b in buckets]
    ma = lambda s, n: sum(s[-n:]) / n
    return {
        "open": buckets[-1],
        "close": buckets[-1] + FIVE,
        "ma5": ma(series, 5),
        "ma15": ma(series, 15),
        "prev_ma5": ma(series[:-1], 5),
        "count": len(closes[buckets[-1]]),
    }


BOUNDARIES = [
    ("10:04:59", "09:55:00", "10:00:00"),
    ("10:05:00", "10:00:00", "10:05:00"),
    ("10:05:01", "10:00:00", "10:05:00"),
    ("10:09:59", "10:00:00", "10:05:00"),
    ("10:10:00", "10:05:00", "10:10:00"),
]


@pytest.mark.parametrize("decision,exp_open,exp_close", BOUNDARIES)
def test_5m_boundary_visible_closed_candle(decision, exp_open, exp_close):
    frame = _one_minute_frame()
    d = ts(decision)
    ctx = get_closed_5m_context(frame, d)
    assert ctx.open_timestamp == ts(exp_open)
    assert ctx.close_timestamp == ts(exp_close)
    assert ctx.close_timestamp == ctx.open_timestamp + FIVE
    assert ctx.close_timestamp <= d
    assert ctx.decision_timestamp == d
    exp = _expected_5m(frame, d)
    assert ctx.bar_count == 5 == exp["count"]
    assert ctx.ma5 == pytest.approx(exp["ma5"], abs=1e-12)
    assert ctx.ma15 == pytest.approx(exp["ma15"], abs=1e-12)
    assert ctx.prev_ma5 == pytest.approx(exp["prev_ma5"], abs=1e-12)
    assert ctx.prev_close_timestamp == ctx.open_timestamp
    assert ctx.usable is True


def test_5m_forming_data_does_not_contaminate():
    frame = _one_minute_frame()
    d = ts("10:07:30")
    base = get_closed_5m_context(frame, d)
    poisoned = frame.copy()
    forming = poisoned["timestamp"] >= ts("10:05:00")
    poisoned.loc[forming, ["open", "high", "low", "close"]] = 999.0
    after = get_closed_5m_context(poisoned, d)
    assert after == base
    assert base.open_timestamp == ts("10:00:00")


def test_5m_incomplete_bucket_is_not_usable_and_veto_fails_closed():
    frame = _one_minute_frame()
    frame = frame[frame["timestamp"] != ts("10:02:00")]
    ctx = get_closed_5m_context(frame, ts("10:05:00"))
    assert ctx.open_timestamp == ts("10:00:00")
    assert ctx.bar_count == 4
    assert ctx.usable is False
    assert is_opposite_slope_veto(LONG, ctx) is None
    assert is_opposite_slope_veto(SHORT, ctx) is None


def test_5m_table_cannot_select_after_build_time():
    table = build_closed_5m_table(_one_minute_frame(), ts("10:05:00"))
    with pytest.raises(ValueError):
        c5.select_closed_5m(table, ts("10:10:00"))


def _ctx(ma5, ma15, prev_ma5, close_ts=ts("10:05:00")):
    return Closed5mContext(
        decision_timestamp=close_ts, open_timestamp=close_ts - FIVE,
        close_timestamp=close_ts, open=1.0, high=1.0, low=1.0, close=1.0,
        bar_count=5, ma5=ma5, ma15=ma15, prev_open_timestamp=close_ts - 2 * FIVE,
        prev_close_timestamp=close_ts - FIVE, prev_ma5=prev_ma5)


@pytest.mark.parametrize("ma5,ma15,prev,expected", [
    (0.90, 1.00, 0.95, True),    # below MA15 and falling -> veto
    (0.90, 1.00, 0.85, False),   # below MA15 but rising
    (1.10, 1.00, 1.20, False),   # falling but above MA15
    (0.90, 1.00, 0.90, False),   # flat slope is not "<"
    (1.00, 1.00, 1.05, False),   # equal to MA15 is not "<"
])
def test_veto_long_exact_rule(ma5, ma15, prev, expected):
    assert is_opposite_slope_veto(LONG, _ctx(ma5, ma15, prev)) is expected


@pytest.mark.parametrize("ma5,ma15,prev,expected", [
    (1.10, 1.00, 1.05, True),    # above MA15 and rising -> veto
    (1.10, 1.00, 1.15, False),   # above MA15 but falling
    (0.90, 1.00, 0.80, False),   # rising but below MA15
    (1.10, 1.00, 1.10, False),   # flat slope is not ">"
    (1.00, 1.00, 0.95, False),   # equal to MA15 is not ">"
])
def test_veto_short_exact_rule(ma5, ma15, prev, expected):
    assert is_opposite_slope_veto(SHORT, _ctx(ma5, ma15, prev)) is expected


def test_veto_missing_context_returns_none():
    assert is_opposite_slope_veto(LONG, None) is None
    nan_ctx = _ctx(float("nan"), 1.0, 1.0)
    assert is_opposite_slope_veto(SHORT, nan_ctx) is None


# ==========================================================================
# State machine — LONG scenarios written once, SHORT = exact price mirror
# ==========================================================================
BASE = ts("12:00:00")


def _m(x, side):
    return x if side == LONG else 2.0 - x


class Script:
    """Builds deterministic closed 1M bars; SHORT mirrors prices around 1.0."""

    def __init__(self, side, strategy=None):
        self.side = side
        self.s = strategy or EntryV2Strategy()
        wm_frame = pd.DataFrame([{"timestamp": BASE, "open": 1, "high": 1,
                                  "low": 1, "close": 1, "volume": 1}])
        self.wm = self.s.initialize_symbol(SYM, wm_frame, BASE + MIN)
        self.i = 0

    def _bar(self, close, high, low, ma5, prev_ma5, ma15=1.0, prev_ma15=1.0, kc=1.0):
        self.i += 1
        m = lambda x: _m(x, self.side)
        hi, lo = (high, low) if self.side == LONG else (2.0 - low, 2.0 - high)
        return ClosedBar1m(BASE + self.i * MIN, m(close), hi, lo, m(close),
                           m(ma5), m(ma15), m(prev_ma5), m(prev_ma15), m(kc), 1.0, 0.5)

    def _ctx(self, bar, veto):
        a, b, p = (0.90, 1.00, 0.95) if veto else (1.10, 1.00, 1.05)
        m = lambda x: _m(x, self.side)
        return _ctx(m(a), m(b), m(p), close_ts=bar.close_timestamp)

    def feed(self, bar, veto=False, ctx="auto"):
        c = self._ctx(bar, veto) if ctx == "auto" else ctx
        return bar, self.s.on_closed_bar(SYM, bar, c)

    # --- bar vocabulary (LONG terms) ---
    def cross(self, **kw):
        return self.feed(self._bar(1.02, 1.025, 1.012, ma5=1.01, prev_ma5=0.99, prev_ma15=1.0), **kw)

    def pullback(self, **kw):
        return self.feed(self._bar(1.03, 1.04, 1.015, ma5=1.02, prev_ma5=1.01), **kw)

    def neutral(self, **kw):
        return self.feed(self._bar(1.035, 1.038, 1.032, ma5=1.03, prev_ma5=1.02), **kw)

    def resume(self, **kw):
        return self.feed(self._bar(1.05, 1.055, 1.045, ma5=1.04, prev_ma5=1.03), **kw)

    def structure_break(self, **kw):
        # prev MA5 < prev MA15: invalidates structure without an opposite cross
        return self.feed(self._bar(0.99, 1.0, 0.985, ma5=0.995, prev_ma5=0.999), **kw)

    def opposite_cross(self, **kw):
        # prev MA5 >= prev MA15 and MA5 < MA15: invalidation AND opposite cross
        return self.feed(self._bar(0.99, 1.0, 0.985, ma5=0.995, prev_ma5=1.0), **kw)

    def pullback_break(self, **kw):
        return self.feed(self._bar(1.03, 1.035, 1.010, ma5=1.02, prev_ma5=1.01), **kw)

    @property
    def state(self):
        return self.s.get_state(SYM)

    @property
    def snap(self):
        return self.s.snapshot(SYM)


SIDES = [LONG, SHORT]


def _setup_vetoed(side):
    sc = Script(side)
    cross, _ = sc.cross()
    pb, _ = sc.pullback()
    origin, cand = sc.resume(veto=True)
    assert cand is None
    return sc, cross, pb, origin


@pytest.mark.parametrize("side", SIDES)
def test_c2_veto_false_emits_candidate(side):
    sc = Script(side)
    cross, r1 = sc.cross()
    pb, r2 = sc.pullback()
    nb, r3 = sc.neutral()
    rs, cand = sc.resume(veto=False)
    assert (r1, r2, r3) == (None, None, None)
    assert isinstance(cand, EntryV2Candidate)
    assert cand.side == side
    assert cand.entry_mode == ENTRY_MODE == "ENTRY_V2_C2"
    assert cand.symbol == SYM
    assert cand.trend_context_bar_id == cross.bar_id
    assert cand.pullback_bar_id == pb.bar_id
    assert cand.resume_bar_id == rs.bar_id == cand.candidate_created_bar_id
    assert cand.decision_timestamp == rs.bar_id + MIN
    assert cand.veto_5m_close_timestamp <= cand.decision_timestamp
    assert cand.veto_release_bar_id is None and cand.recovery_origin_bar_id is None
    assert cand.is_recovered is False
    assert sc.state == SEARCH
    assert sc.s.counters["CANDIDATES"] == 1


@pytest.mark.parametrize("side", SIDES)
def test_c2_veto_true_destroys_candidate_and_enters_veto_wait(side):
    sc, cross, pb, origin = _setup_vetoed(side)
    assert sc.state == VETO_WAIT
    rec = sc.snap["recovery"]
    assert rec["origin_bar_id"] == origin.bar_id
    assert rec["origin_pullback_bar_id"] == pb.bar_id
    assert rec["veto_release_bar_id"] is None
    assert sc.s.counters["ORIGINAL_VETOED"] == 1
    assert sc.s.counters["CANDIDATES"] == 0


@pytest.mark.parametrize("side", SIDES)
def test_recovery_state_holds_no_executable_ticket(side):
    sc, *_ = _setup_vetoed(side)
    rec = sc.snap["recovery"]
    assert set(rec) == {"origin_bar_id", "origin_pullback_bar_id",
                        "origin_pullback_extreme", "veto_release_bar_id"}
    assert not any(isinstance(v, EntryV2Candidate) for v in rec.values())
    assert set(sc.snap["formation"]) == {"side", "trend_context_bar_id", "pullback_bar_id",
                                         "pullback_high", "pullback_low"}


@pytest.mark.parametrize("side", SIDES)
def test_veto_release_gives_no_immediate_candidate(side):
    sc, *_ = _setup_vetoed(side)
    rel, cand = sc.resume(veto=False)  # qualifies as resume, but is release bar
    assert cand is None
    assert sc.state == WAIT_FRESH_CONFIRM
    assert sc.snap["recovery"]["veto_release_bar_id"] == rel.bar_id
    assert sc.s.counters["CANDIDATES"] == 0


@pytest.mark.parametrize("side", SIDES)
def test_confirmation_while_veto_active_cannot_be_reused(side):
    sc, *_ = _setup_vetoed(side)
    during, c1 = sc.resume(veto=True)
    assert c1 is None and sc.state == VETO_WAIT
    assert sc.s.counters["CONFIRM_DISCARDED_WHILE_VETO"] == 1
    rel, c2 = sc.neutral(veto=False)
    assert c2 is None and sc.state == WAIT_FRESH_CONFIRM
    quiet, c3 = sc.neutral(veto=False)  # no new resume -> nothing replays
    assert c3 is None and sc.state == WAIT_FRESH_CONFIRM
    fresh, cand = sc.resume(veto=False)
    assert cand.resume_bar_id == fresh.bar_id
    assert cand.resume_bar_id != during.bar_id


@pytest.mark.parametrize("side", SIDES)
def test_fresh_confirmation_after_release_emits_new_candidate(side):
    sc, cross, pb, origin = _setup_vetoed(side)
    rel, _ = sc.neutral(veto=False)
    fresh, cand = sc.resume(veto=False)
    assert isinstance(cand, EntryV2Candidate)
    assert cand.side == side
    assert cand.is_recovered is True
    assert cand.recovery_origin_bar_id == origin.bar_id
    assert cand.veto_release_bar_id == rel.bar_id
    assert cand.resume_bar_id == fresh.bar_id
    assert cand.resume_bar_id > cand.veto_release_bar_id
    assert cand.resume_bar_id != cand.recovery_origin_bar_id
    assert cand.trend_context_bar_id == cross.bar_id
    assert sc.state == SEARCH and sc.snap["recovery"] is None
    assert sc.s.counters["RECOVERED_ENTRIES"] == 1


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("phase", [VETO_WAIT, WAIT_FRESH_CONFIRM])
def test_structure_invalidation_cancels_to_search(side, phase):
    sc, *_ = _setup_vetoed(side)
    if phase == WAIT_FRESH_CONFIRM:
        sc.neutral(veto=False)
    assert sc.state == phase
    _, cand = sc.structure_break(veto=False)
    assert cand is None
    assert sc.state == SEARCH
    assert sc.snap["formation"] is None and sc.snap["recovery"] is None
    assert sc.s.counters["CANCELLED_STRUCTURE_BREAK"] == 1
    _, again = sc.resume(veto=False)  # no formation -> cannot enter
    assert again is None


@pytest.mark.parametrize("side", SIDES)
def test_opposite_cross_cancels_recovery_and_starts_fresh_opposite_context(side):
    sc, *_ = _setup_vetoed(side)
    oc, cand = sc.opposite_cross(veto=False)
    assert cand is None
    assert sc.state == SEARCH
    assert sc.snap["recovery"] is None
    f = sc.snap["formation"]
    assert f["side"] == (SHORT if side == LONG else LONG)
    assert f["trend_context_bar_id"] == oc.bar_id
    assert f["pullback_bar_id"] is None
    assert sc.s.counters["CANCELLED_STRUCTURE_BREAK"] == 1


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("phase", [VETO_WAIT, WAIT_FRESH_CONFIRM])
def test_pullback_invalidation_cancels_to_search(side, phase):
    sc, *_ = _setup_vetoed(side)
    if phase == WAIT_FRESH_CONFIRM:
        sc.neutral(veto=False)
    _, cand = sc.pullback_break(veto=False)
    assert cand is None
    assert sc.state == SEARCH
    assert sc.snap["formation"] is None and sc.snap["recovery"] is None
    assert sc.s.counters["CANCELLED_PULLBACK_BREAK"] == 1
    _, again = sc.resume(veto=False)
    assert again is None


@pytest.mark.parametrize("side", SIDES)
def test_veto_reactivation_returns_to_veto_wait(side):
    sc, cross, pb, origin = _setup_vetoed(side)
    rel1, _ = sc.neutral(veto=False)
    assert sc.state == WAIT_FRESH_CONFIRM
    react, c = sc.resume(veto=True)
    assert c is None
    assert sc.state == VETO_WAIT
    assert sc.snap["recovery"]["veto_release_bar_id"] is None
    assert sc.s.counters["VETO_REACTIVATED"] == 1
    rel2, _ = sc.neutral(veto=False)
    fresh, cand = sc.resume(veto=False)
    assert cand.veto_release_bar_id == rel2.bar_id != rel1.bar_id
    assert cand.resume_bar_id > rel2.bar_id
    assert cand.recovery_origin_bar_id == origin.bar_id


@pytest.mark.parametrize("side", SIDES)
def test_unavailable_5m_context_fails_closed(side):
    sc = Script(side)
    sc.cross()
    sc.pullback()
    _, cand = sc.resume(ctx=None)
    assert cand is None
    assert sc.state == VETO_WAIT


def test_future_5m_context_rejected():
    sc = Script(LONG)
    bar = sc._bar(1.02, 1.025, 1.012, ma5=1.01, prev_ma5=0.99)
    future = _ctx(1.1, 1.0, 1.05, close_ts=bar.close_timestamp + FIVE)
    with pytest.raises(ValueError):
        sc.s.on_closed_bar(SYM, bar, future)


def test_candidate_schema_enforces_invariants():
    good = dict(symbol=SYM, side=LONG, entry_mode=ENTRY_MODE,
                trend_context_bar_id=1 * MIN, pullback_bar_id=2 * MIN,
                resume_bar_id=5 * MIN, candidate_created_bar_id=5 * MIN,
                decision_timestamp=6 * MIN, resume_close=1.0, pullback_high=1.0,
                pullback_low=1.0, veto_5m_open_timestamp=0, veto_5m_close_timestamp=FIVE,
                veto_release_bar_id=4 * MIN, recovery_origin_bar_id=3 * MIN)
    assert EntryV2Candidate(**good).is_recovered is True
    for bad in (
        {"recovery_origin_bar_id": 5 * MIN},             # reuse of origin
        {"veto_release_bar_id": 5 * MIN},                # resume not after release
        {"pullback_bar_id": 1 * MIN},                    # causal order
        {"candidate_created_bar_id": 6 * MIN},           # created off resume bar
        {"veto_5m_close_timestamp": 7 * MIN},            # 5M not closed
        {"veto_release_bar_id": None},                   # half recovery fields
        {"entry_mode": "CHANNEL_SWING"},
    ):
        with pytest.raises(ValueError):
            EntryV2Candidate(**{**good, **bad})
    with pytest.raises(Exception):
        EntryV2Candidate(**good).side = SHORT  # frozen


# ==========================================================================
# Restart policy (重整不開倉) and post-exit freshness
# ==========================================================================
def test_startup_watermark_is_last_closed_1m_open_timestamp():
    frame = _one_minute_frame(end="10:14:00")
    s = EntryV2Strategy()
    startup = ts("10:10:30")  # 10:10 bar is forming
    assert s.evaluate(SYM, frame, startup) is None
    wm = s.startup_watermark(SYM)
    assert isinstance(wm, int)
    assert wm == ts("10:09:00")              # open id of last CLOSED bar
    assert wm + MIN <= startup
    assert wm % MIN == 0
    assert s.get_state(SYM) == SEARCH


def test_new_instance_has_no_state():
    s = EntryV2Strategy()
    assert s.get_state(SYM) == SEARCH
    assert s.snapshot(SYM) == {"state": SEARCH, "formation": None, "recovery": None}
    with pytest.raises(RuntimeError):
        s.on_closed_bar(SYM, ClosedBar1m(BASE, *([1.0] * 11)), None)


@pytest.mark.parametrize("side", SIDES)
def test_restart_discards_recovery_state(side):
    sc, *_ = _setup_vetoed(side)
    assert sc.state == VETO_WAIT
    restarted = Script(side)  # new process == new instance
    assert restarted.state == SEARCH
    assert restarted.snap["formation"] is None and restarted.snap["recovery"] is None
    _, c1 = restarted.neutral(veto=False)
    _, c2 = restarted.resume(veto=False)
    assert (c1, c2) == (None, None)


@pytest.mark.parametrize("side", SIDES)
def test_restart_old_cross_new_pullback_new_resume_blocked(side):
    sc = Script(side)  # pre-startup cross is history; no post-startup cross
    _, a = sc.pullback()
    _, b = sc.neutral()
    _, c = sc.resume(veto=False)
    assert (a, b, c) == (None, None, None)
    assert sc.snap["formation"] is None


def test_restart_cross_on_watermark_bar_not_fresh():
    sc = Script(LONG)
    bar = ClosedBar1m(sc.wm, 1.02, 1.025, 1.012, 1.02, 1.01, 1.0, 0.99, 1.0, 1.0, 1.0, 1.0)
    assert sc.s.on_closed_bar(SYM, bar, None) is None
    assert sc.snap["formation"] is None


def test_restart_historical_full_formation_never_enters():
    """Replay real candles: everything loaded at startup is context only."""
    frame = _one_minute_frame(end="10:14:00")
    s = EntryV2Strategy()
    assert s.evaluate(SYM, frame, ts("10:10:00")) is None   # startup call
    wm = s.startup_watermark(SYM)
    assert wm == ts("10:09:00")
    # 130+ historical bars were loaded, yet no formation exists at startup.
    assert s.snapshot(SYM)["formation"] is None
    assert s.snapshot(SYM)["recovery"] is None
    for t in range(ts("10:10:00") + 1000, ts("10:15:00") + 1, 1000):
        cand = s.evaluate(SYM, frame, t)
        f = s.snapshot(SYM)["formation"]
        if f is not None:
            assert f["trend_context_bar_id"] > wm
        if cand is not None:
            assert cand.trend_context_bar_id > wm
    assert not any(e[1] == "TREND_CONTEXT" and e[0] <= wm for e in s.events(SYM))


@pytest.mark.parametrize("side", SIDES)
def test_pre_exit_formation_post_exit_resume_blocked(side):
    sc = Script(side)
    sc.cross()
    pb, _ = sc.pullback()
    sc.s.on_exit(SYM, pb.bar_id + MIN + 5_000)
    _, cand = sc.resume(veto=False)
    assert cand is None
    assert sc.snap["formation"] is None
    assert sc.snap["last_exit_bar_id"] == pb.bar_id + MIN


@pytest.mark.parametrize("side", SIDES)
def test_exit_during_recovery_destroys_state(side):
    sc, *_ = _setup_vetoed(side)
    sc.s.on_exit(SYM, BASE + (sc.i + 1) * MIN)
    assert sc.state == SEARCH and sc.snap["recovery"] is None
    sc.neutral(veto=False)
    _, cand = sc.resume(veto=False)
    assert cand is None


@pytest.mark.parametrize("side", SIDES)
def test_cross_on_exit_bar_is_not_fresh(side):
    sc = Script(side)
    sc.s.on_exit(SYM, BASE + 1 * MIN + 30_000)  # inside the next bar
    cross, _ = sc.cross()
    assert cross.bar_id == sc.snap["last_exit_bar_id"]
    assert sc.snap["formation"] is None


@pytest.mark.parametrize("side", SIDES)
def test_new_post_exit_formation_can_enter(side):
    sc = Script(side)
    sc.s.on_exit(SYM, BASE + 1 * MIN + 30_000)
    sc.neutral()  # exit bar itself
    cross, _ = sc.cross()
    pb, _ = sc.pullback()
    rs, cand = sc.resume(veto=False)
    assert cand.trend_context_bar_id == cross.bar_id > sc.snap["last_exit_bar_id"]
    assert cand.resume_bar_id > cand.pullback_bar_id > cand.trend_context_bar_id
    assert cand.side == side


# ==========================================================================
# End-to-end on real candles (frame -> indicators -> 5M -> state machine)
# ==========================================================================
CSV = os.path.join(os.path.dirname(__file__), "..", "scratch", "lobster_real_1m_history.csv")


@pytest.mark.skipif(not os.path.exists(CSV), reason="LOBSTER 1m history not present")
def test_real_candle_replay_candidates_satisfy_frozen_rules():
    full = pd.read_csv(CSV).iloc[:4000].reset_index(drop=True)
    window = 400
    s = EntryV2Strategy()
    s.evaluate(SYM, full.iloc[:window], int(full.loc[window - 1, "timestamp"]) + MIN)
    cands = []
    for k in range(window, len(full)):
        frame = full.iloc[k - window + 1:k + 1]
        c = s.evaluate(SYM, frame, int(full.loc[k, "timestamp"]) + MIN)
        if c is not None:
            cands.append(c)

    assert len(cands) > 0
    bars = {b.bar_id: b for b in compute_closed_1m_bars(full, int(full["timestamp"].iloc[-1]) + MIN)}
    wm = s.startup_watermark(SYM)
    for c in cands:
        ctx_bar, pb, rs = bars[c.trend_context_bar_id], bars[c.pullback_bar_id], bars[c.resume_bar_id]
        assert c.trend_context_bar_id > wm
        assert ev2._is_cross(c.side, ctx_bar)
        assert ev2._is_pullback(c.side, pb)
        if c.side == LONG:
            assert rs.close > pb.high
        else:
            assert rs.close < pb.low
        ctx = get_closed_5m_context(full, c.decision_timestamp)
        assert ctx.close_timestamp <= c.decision_timestamp
        assert (ctx.open_timestamp, ctx.close_timestamp) == (c.veto_5m_open_timestamp, c.veto_5m_close_timestamp)
        assert is_opposite_slope_veto(c.side, ctx) is False
        if c.is_recovered:
            assert c.resume_bar_id > c.veto_release_bar_id > c.recovery_origin_bar_id
    assert s.counters["CANDIDATES"] == len(cands)
