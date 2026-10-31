"""The engine: one code path for backtest, paper and live.

    closed bar --> broker.process_bar  (fills, funding, stops/targets)
              --> risk.update          (daily limit, kill switch)
              --> strategy.on_bar      (signal for the NEXT bar)
              --> risk.check/budget    (may we trade? how much?)
              --> broker.submit

Backtests replay history through this; the live runner feeds it bars as they close.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from orb.broker.base import Broker
from orb.models import Candle, Trade
from orb.risk import RiskManager
from orb.strategies.base import BarContext, Strategy

log = logging.getLogger(__name__)


def fmt_t(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


class Engine:
    def __init__(
        self,
        strategy: Strategy,
        broker: Broker,
        risk: RiskManager,
        on_event: Callable[[str], None] | None = None,
    ):
        self.strategy = strategy
        self.broker = broker
        self.risk = risk
        self.marks: dict[str, float] = {}
        self.last_funding: dict[str, float] = {}
        self.trades: list[Trade] = []
        self.events: list[str] = []
        self._on_event = on_event

    def _emit(self, msg: str) -> None:
        self.events.append(msg)
        log.info(msg)
        if self._on_event:
            self._on_event(msg)

    def warm(self, coin: str, bar: Candle, funding: list[tuple[int, float]] = ()) -> None:
        """Feed history to the strategy without trading (used on live start-up)."""
        for _, r in funding:
            self.last_funding[coin] = r
        self.marks[coin] = bar.c
        self.strategy.on_bar(coin, bar, BarContext(self.last_funding.get(coin)))

    def step(self, coin: str, bar: Candle, funding: list[tuple[int, float]] = ()) -> list[Trade]:
        closed = self.broker.process_bar(coin, bar, list(funding))
        for tr in closed:
            self.trades.append(tr)
            self._emit(
                f"{fmt_t(bar.t)} CLOSE {tr.side} {tr.coin} {tr.exit_reason} @ {tr.exit:.6g} "
                f"pnl {tr.pnl:+.2f} ({tr.r_multiple:+.2f}R)"
            )
        for _, r in funding:
            self.last_funding[coin] = r
        self.marks[coin] = bar.c
        equity = self.broker.equity(self.marks)

        halt = self.risk.update(bar.t, equity)
        if halt:
            self._emit(f"{fmt_t(bar.t)} KILL SWITCH: {halt}; flattening and halting")
            for tr in self.broker.close_all(self.marks, bar.t, "kill_switch"):
                self.trades.append(tr)
                closed.append(tr)

        sig = self.strategy.on_bar(coin, bar, BarContext(self.last_funding.get(coin)))
        if sig is None:
            return closed
        blocked = self.risk.check(coin, self.broker.open_coins(), equity)
        if blocked:
            log.debug("signal %s %s blocked: %s", sig.side.value, coin, blocked)
            return closed
        self._emit(
            f"{fmt_t(bar.t)} SIGNAL {sig.reason} {coin} close {bar.c:.6g} -> "
            + self.broker.submit(sig, self.risk.budget(equity))
        )
        return closed
