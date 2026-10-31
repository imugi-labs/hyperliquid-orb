"""Streaming indicators: O(1) per bar, identical in backtest and live.

Each indicator is fed one closed bar at a time and returns None until it has
enough data. Because live trading uses the exact same objects, a backtest
cannot accidentally use information a live bot would not have had.
"""

from __future__ import annotations

from collections import deque


class EMA:
    def __init__(self, length: int):
        if length < 1:
            raise ValueError("length must be >= 1")
        self.length = length
        self.alpha = 2.0 / (length + 1)
        self._seed: list[float] = []
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self._seed.append(x)
            if len(self._seed) == self.length:
                self.value = sum(self._seed) / self.length  # SMA seed
                self._seed.clear()
            return self.value
        self.value = self.alpha * x + (1 - self.alpha) * self.value
        return self.value


class ATR:
    """Wilder's Average True Range."""

    def __init__(self, length: int):
        self.length = length
        self._prev_close: float | None = None
        self._trs: list[float] = []
        self.value: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close
        if self.value is None:
            self._trs.append(tr)
            if len(self._trs) == self.length:
                self.value = sum(self._trs) / self.length
                self._trs.clear()
            return self.value
        self.value = (self.value * (self.length - 1) + tr) / self.length
        return self.value


class Window:
    """Rolling max/min of the previous `length` values (current value excluded)."""

    def __init__(self, length: int):
        self.length = length
        self._buf: deque[float] = deque(maxlen=length)

    @property
    def ready(self) -> bool:
        return len(self._buf) == self.length

    def max(self) -> float | None:
        return max(self._buf) if self.ready else None

    def min(self) -> float | None:
        return min(self._buf) if self.ready else None

    def push(self, x: float) -> None:
        self._buf.append(x)
