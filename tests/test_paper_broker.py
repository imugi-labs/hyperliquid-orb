import pytest

from orb.broker.paper import PaperBroker
from orb.config import ExecutionCfg
from orb.models import Side, Signal
from orb.risk import Budget

from .conftest import T0, H, bar

NOFEE = ExecutionCfg(taker_fee=0.0, slippage_bps=0.0)


def sig(side=Side.LONG, stop=95.0, tp=110.0):
    return Signal("X", side, stop, tp, "test", T0)


def test_signal_fills_at_next_open_not_same_bar():
    b = PaperBroker(NOFEE, 1000)
    b.submit(sig(), Budget(10, 10_000))
    assert not b.positions and "X" in b.open_coins()
    b.process_bar("X", bar(1, 100, 101, 99, 100.5), [])
    p = b.positions["X"]
    assert p.entry == 100 and p.size == pytest.approx(2.0)  # $10 risk / $5 stop distance


def test_take_profit_and_r_multiple():
    b = PaperBroker(NOFEE, 1000)
    b.submit(sig(), Budget(10, 10_000))
    b.process_bar("X", bar(1, 100, 101, 99, 100), [])
    (t,) = b.process_bar("X", bar(2, 100, 111, 99.5, 109), [])
    assert t.exit_reason == "take_profit" and t.exit == 110
    assert t.pnl == pytest.approx(20.0) and t.r_multiple == pytest.approx(2.0)
    assert b.cash == pytest.approx(1020.0)


def test_stop_wins_when_both_hit_in_one_bar():
    b = PaperBroker(NOFEE, 1000)
    b.submit(sig(), Budget(10, 10_000))
    b.process_bar("X", bar(1, 100, 101, 99, 100), [])
    (t,) = b.process_bar("X", bar(2, 100, 115, 90, 100), [])
    assert t.exit_reason == "stop" and t.exit == 95


def test_gap_through_stop_fills_at_open():
    b = PaperBroker(NOFEE, 1000)
    b.submit(sig(), Budget(10, 10_000))
    b.process_bar("X", bar(1, 100, 101, 99, 100), [])
    (t,) = b.process_bar("X", bar(2, 92, 93, 90, 91), [])
    assert t.exit == 92 and t.r_multiple == pytest.approx(-1.6)


def test_entry_cancelled_if_open_gaps_past_stop():
    b = PaperBroker(NOFEE, 1000)
    b.submit(sig(), Budget(10, 10_000))
    b.process_bar("X", bar(1, 94, 96, 93, 95), [])
    assert not b.positions and not b.pending


def test_short_side_and_fees_and_slippage():
    cfg = ExecutionCfg(taker_fee=0.001, slippage_bps=10)
    b = PaperBroker(cfg, 1000)
    b.submit(sig(Side.SHORT, stop=105, tp=90), Budget(10, 10_000))
    b.process_bar("X", bar(1, 100, 101, 99, 100), [])
    p = b.positions["X"]
    assert p.entry == pytest.approx(99.9)  # selling gets 10 bps less
    (t,) = b.process_bar("X", bar(2, 99, 99.5, 89, 90), [])
    assert t.exit == 90 and t.fees == pytest.approx(p.size * (99.9 + 90) * 0.001)
    assert t.pnl == pytest.approx((99.9 - 90) * p.size - t.fees)


def test_funding_charged_to_longs_when_positive():
    b = PaperBroker(NOFEE, 1000)
    b.submit(sig(), Budget(10, 10_000))
    b.process_bar("X", bar(1, 100, 101, 99, 100), [(T0 + H, 0.001)])
    assert b.positions["X"].funding == pytest.approx(2.0 * 100 * 0.001)
    assert b.cash == pytest.approx(1000 - 0.2)


def test_min_notional_and_equity_and_state_roundtrip():
    b = PaperBroker(NOFEE, 1000)
    b.submit(sig(), Budget(0.01, 10_000))  # tiny risk -> below $10 notional
    b.process_bar("X", bar(1, 100, 101, 99, 100), [])
    assert not b.positions
    b.submit(sig(), Budget(10, 10_000))
    d = b.to_dict()
    b2 = PaperBroker(NOFEE, 0)
    b2.load_dict(d)
    assert b2.pending["X"][0].side is Side.LONG and b2.cash == 1000
    b2.process_bar("X", bar(2, 100, 101, 99, 100), [])
    assert b2.equity({"X": 103}) == pytest.approx(1006.0)
    (t,) = b2.close_all({"X": 101}, T0, "manual")
    assert t.exit_reason == "manual" and not b2.positions
