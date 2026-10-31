"""Strategy registry. Add a strategy by subclassing Strategy and listing it here."""

from __future__ import annotations

from typing import Any

from orb.strategies.base import BarContext, Strategy
from orb.strategies.breakout import Breakout
from orb.strategies.ema_retest import EmaRetest

REGISTRY: dict[str, type[Strategy]] = {EmaRetest.name: EmaRetest, Breakout.name: Breakout}


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise ValueError(f"unknown strategy {name!r}; available: {sorted(REGISTRY)}") from None
    return cls(params)


__all__ = ["BarContext", "Strategy", "build_strategy", "REGISTRY"]
