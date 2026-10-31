"""Strategy interface.

A strategy sees one *closed* candle at a time per coin and may return a Signal.
It never sees the next bar, never places orders and never sizes positions:
risk and execution live elsewhere, so strategies stay small and testable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from orb.models import Candle, Signal


@dataclass(frozen=True)
class BarContext:
    funding_1h: float | None = None  # latest hourly funding rate known at bar close


class Strategy(ABC):
    name: str = "base"
    defaults: dict[str, Any] = {}

    def __init__(self, params: dict[str, Any] | None = None):
        unknown = set(params or {}) - set(self.defaults)
        if unknown:
            raise ValueError(f"{self.name}: unknown params {sorted(unknown)}")
        self.p: dict[str, Any] = {**self.defaults, **(params or {})}
        self._state: dict[str, Any] = {}

    def state(self, coin: str) -> Any:
        if coin not in self._state:
            self._state[coin] = self.new_state()
        return self._state[coin]

    @abstractmethod
    def new_state(self) -> Any:
        """Per-coin indicator state."""

    @abstractmethod
    def warmup_bars(self) -> int:
        """Closed bars needed before signals are valid."""

    @abstractmethod
    def on_bar(self, coin: str, bar: Candle, ctx: BarContext) -> Signal | None:
        """Consume one closed bar; optionally return a signal for the next bar's open."""
