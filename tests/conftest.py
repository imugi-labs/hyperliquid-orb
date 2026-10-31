from __future__ import annotations

import math

import pytest

from orb.config import Config
from orb.models import Candle

H = 3_600_000
T0 = 1_760_000_400_000 - (1_760_000_400_000 % H)


def bar(i: int, o: float, h: float, l: float, c: float, v: float = 1.0) -> Candle:  # noqa: E741
    return Candle(T0 + i * H, o, h, l, c, v)


def wave(n: int, base: float = 100.0, amp: float = 8.0, period: int = 120, drift: float = 0.02) -> list[Candle]:
    """Deterministic trending sine wave: guaranteed pullbacks to a long EMA."""
    out, prev = [], base
    for i in range(n):
        c = base + drift * i + amp * math.sin(2 * math.pi * i / period)
        o = prev
        out.append(bar(i, o, max(o, c) + 0.4, min(o, c) - 0.4, c))
        prev = c
    return out


@pytest.fixture
def cfg() -> Config:
    c = Config()
    c.market.coins = ["TEST"]
    c.strategy.params = {"ema_len": 50, "atr_len": 14}
    return c
