"""Plain data types shared by every component."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


@dataclass(frozen=True)
class Candle:
    t: int  # open time, ms since epoch (UTC)
    o: float
    h: float
    l: float  # noqa: E741
    c: float
    v: float


@dataclass(frozen=True)
class Signal:
    coin: str
    side: Side
    stop: float
    take_profit: float
    reason: str
    bar_time: int


@dataclass
class Position:
    coin: str
    side: Side
    size: float
    entry: float
    stop: float
    take_profit: float
    opened_at: int
    fees: float = 0.0
    funding: float = 0.0
    oid: str = ""

    def unrealized(self, price: float) -> float:
        return self.side.sign * (price - self.entry) * self.size


@dataclass
class Trade:
    coin: str
    side: str
    size: float
    entry: float
    exit: float
    opened_at: int
    closed_at: int
    pnl: float  # net of fees and funding
    fees: float
    funding: float
    exit_reason: str
    r_multiple: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EquityPoint:
    t: int
    equity: float


@dataclass
class RunResult:
    trades: list[Trade] = field(default_factory=list)
    equity: list[EquityPoint] = field(default_factory=list)
    starting_equity: float = 0.0
    events: list[str] = field(default_factory=list)
