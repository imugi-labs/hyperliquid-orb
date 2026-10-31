import pytest

from orb.indicators import ATR, EMA, Window


def test_ema_seeds_with_sma_then_smooths():
    e = EMA(3)
    assert e.update(1) is None and e.update(2) is None
    assert e.update(3) == pytest.approx(2.0)
    assert e.update(6) == pytest.approx(0.5 * 6 + 0.5 * 2.0)


def test_ema_rejects_bad_length():
    with pytest.raises(ValueError):
        EMA(0)


def test_atr_wilder():
    a = ATR(2)
    assert a.update(10, 8, 9) is None  # tr 2
    assert a.update(12, 9, 11) == pytest.approx((2 + 3) / 2)  # tr max(3, 3, 0) = 3
    assert a.update(11, 10, 10.5) == pytest.approx((2.5 * 1 + 1) / 2)  # tr max(1, 0, 1) = 1


def test_window_excludes_current_value():
    w = Window(3)
    for x in [1, 5, 2]:
        assert w.max() is None
        w.push(x)
    assert w.max() == 5 and w.min() == 1
    w.push(0)
    assert w.max() == 5 and w.min() == 0
