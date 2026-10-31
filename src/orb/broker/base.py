from __future__ import annotations

from abc import ABC, abstractmethod

from orb.models import Candle, Position, Signal, Trade
from orb.risk import Budget


class Broker(ABC):
    def __init__(self) -> None:
        self.positions: dict[str, Position] = {}

    def open_coins(self) -> set[str]:
        return set(self.positions)

    @abstractmethod
    def submit(self, signal: Signal, budget: Budget) -> str:
        """Act on a signal. Returns a human-readable event line."""

    @abstractmethod
    def process_bar(self, coin: str, bar: Candle, funding: list[tuple[int, float]]) -> list[Trade]:
        """Advance one closed bar for `coin`: fills, funding, stops/targets. Returns closed trades."""

    @abstractmethod
    def equity(self, marks: dict[str, float]) -> float: ...

    @abstractmethod
    def close_all(self, marks: dict[str, float], t: int, reason: str) -> list[Trade]: ...
