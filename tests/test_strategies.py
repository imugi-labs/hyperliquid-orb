import pytest

from orb.models import Side
from orb.strategies import BarContext, build_strategy

from .conftest import bar, wave


def test_registry_and_unknown_params():
    assert build_strategy("breakout").name == "breakout"
    with pytest.raises(ValueError):
        build_strategy("nope")
    with pytest.raises(ValueError):
        build_strategy("ema_retest", {"typo_param": 1})


def test_no_signal_during_warmup():
    s = build_strategy("ema_retest", {"ema_len": 50})
    for b in wave(55):
        assert s.on_bar("X", b, BarContext()) is None


def test_ema_retest_signals_have_consistent_levels():
    s = build_strategy("ema_retest", {"ema_len": 50, "touch_atr": 1.0})
    sigs = [x for b in wave(1500) if (x := s.on_bar("X", b, BarContext()))]
    assert sigs, "expected retest signals on a trending wave"
    for g in sigs:
        if g.side is Side.LONG:
            assert g.stop < g.take_profit
        else:
            assert g.take_profit < g.stop


def test_funding_filter_blocks_crowded_longs():
    params = {"ema_len": 50, "touch_atr": 1.0, "max_funding_8h": 0.0005}
    hot, cold = build_strategy("ema_retest", params), build_strategy("ema_retest", params)
    longs_hot = longs_cold = 0
    for b in wave(1500):
        a = hot.on_bar("X", b, BarContext(funding_1h=0.001))  # 0.8% / 8h: crowded
        c = cold.on_bar("X", b, BarContext(funding_1h=0.00001))
        longs_hot += bool(a and a.side is Side.LONG)
        longs_cold += bool(c and c.side is Side.LONG)
    assert longs_cold > 0 and longs_hot == 0


def test_breakout_long_on_new_high():
    s = build_strategy("breakout", {"lookback": 5, "ema_len": 5, "atr_len": 3})
    sig = None
    for i in range(10):
        sig = s.on_bar("X", bar(i, 100, 101, 99, 100), BarContext())
    assert sig is None
    sig = s.on_bar("X", bar(10, 100, 106, 100, 105), BarContext())
    assert sig is not None and sig.side is Side.LONG and sig.stop < 105 < sig.take_profit


def test_strategy_state_is_per_coin():
    s = build_strategy("ema_retest", {"ema_len": 50})
    for b in wave(60):
        s.on_bar("A", b, BarContext())
    assert s.state("A").ema.value is not None
    assert s.state("B").ema.value is None
