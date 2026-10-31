"""Risk manager: decides IF a trade may open and HOW MUCH it may risk.

Hard rules, enforced before any order reaches a broker:
  * fixed-fractional risk per trade (loss at stop = risk_per_trade x equity)
  * notional cap = max_leverage x equity
  * max concurrent positions, one position per coin
  * daily loss limit (UTC day): no new entries once breached
  * drawdown kill switch: flatten and halt until a human resets state
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from orb.config import RiskCfg

DAY_MS = 86_400_000


@dataclass
class RiskState:
    peak_equity: float = 0.0
    day: int = -1
    day_start_equity: float = 0.0
    halted: bool = False
    halt_reason: str = ""


@dataclass(frozen=True)
class Budget:
    risk_amount: float  # $ lost if the stop is hit
    max_notional: float  # $ notional cap for the position


class RiskManager:
    def __init__(self, cfg: RiskCfg, state: RiskState | None = None):
        self.cfg = cfg
        self.s = state or RiskState()

    def update(self, t_ms: int, equity: float) -> str | None:
        """Feed the latest equity. Returns a halt reason the first time the kill switch fires."""
        day = t_ms // DAY_MS
        if day != self.s.day:
            self.s.day = day
            self.s.day_start_equity = equity
        self.s.peak_equity = max(self.s.peak_equity, equity)
        if not self.s.halted and self.s.peak_equity > 0:
            dd = 1 - equity / self.s.peak_equity
            if dd >= self.cfg.max_drawdown_kill:
                self.s.halted = True
                self.s.halt_reason = f"drawdown {dd:.1%} >= kill {self.cfg.max_drawdown_kill:.0%}"
                return self.s.halt_reason
        return None

    def daily_limit_hit(self, equity: float) -> bool:
        if self.s.day_start_equity <= 0:
            return False
        return equity <= self.s.day_start_equity * (1 - self.cfg.daily_loss_limit)

    def check(self, coin: str, open_coins: set[str], equity: float) -> str | None:
        """None if a new position may open, else the reason it may not."""
        if self.s.halted:
            return f"halted: {self.s.halt_reason}"
        if equity <= 0:
            return "no equity"
        if coin in open_coins:
            return "already in position"
        if len(open_coins) >= self.cfg.max_open_positions:
            return "max open positions"
        if self.daily_limit_hit(equity):
            return "daily loss limit"
        return None

    def budget(self, equity: float) -> Budget:
        return Budget(equity * self.cfg.risk_per_trade, equity * self.cfg.max_leverage)

    @staticmethod
    def size(entry: float, stop: float, budget: Budget) -> float:
        dist = abs(entry - stop)
        if dist <= 0 or entry <= 0:
            return 0.0
        return max(0.0, min(budget.risk_amount / dist, budget.max_notional / entry))

    def to_dict(self) -> dict:
        return asdict(self.s)
