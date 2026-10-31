"""Simulated broker used by both the backtester and paper trading.

Fill model (deliberately conservative):
  * A signal from bar N fills at bar N+1's OPEN, plus slippage. No same-bar fills.
  * Size is computed at the fill price, so gaps change size, not risk.
  * If the open gaps past the stop or the target, the entry is cancelled.
  * Stop and target are checked on every bar, including the entry bar.
    If both are touched in one bar, the STOP is assumed to fill first.
  * A gap through the stop fills at the open (worse than the stop).
  * Taker fee on entry and exit notional; hourly funding charged on notional.
"""

from __future__ import annotations

from dataclasses import asdict

from orb.broker.base import Broker
from orb.config import ExecutionCfg
from orb.models import Candle, Position, Side, Signal, Trade
from orb.risk import Budget, RiskManager


class PaperBroker(Broker):
    def __init__(self, cfg: ExecutionCfg, starting_cash: float, min_notional: float = 10.0):
        super().__init__()
        self.cfg = cfg
        self.cash = starting_cash
        self.min_notional = min_notional
        self.pending: dict[str, tuple[Signal, Budget]] = {}

    # ----------------------------------------------------------------- utils
    def _slip(self, price: float, side: Side, entering: bool) -> float:
        s = self.cfg.slippage_bps / 10_000
        worse_up = (side is Side.LONG) == entering  # buying pays up, selling gets less
        return price * (1 + s) if worse_up else price * (1 - s)

    def open_coins(self) -> set[str]:
        return set(self.positions) | set(self.pending)

    # --------------------------------------------------------------- broker
    def submit(self, signal: Signal, budget: Budget) -> str:
        self.pending[signal.coin] = (signal, budget)
        lv = f"stop {signal.stop:.6g}, tp {signal.take_profit:.6g}"
        return f"queued {signal.side.value} {signal.coin} for next open ({lv})"

    def process_bar(self, coin: str, bar: Candle, funding: list[tuple[int, float]]) -> list[Trade]:
        closed: list[Trade] = []
        self._fill_pending(coin, bar)
        pos = self.positions.get(coin)
        if pos is None:
            return closed
        for t, rate in funding:
            if t >= pos.opened_at:
                # positive rate: longs pay shorts
                pay = pos.side.sign * rate * pos.size * bar.c
                pos.funding += pay
                self.cash -= pay
        exit_px, reason = self._exit_price(pos, bar)
        if exit_px is not None:
            closed.append(self._close(pos, exit_px, bar.t, reason))
        return closed

    def _fill_pending(self, coin: str, bar: Candle) -> None:
        item = self.pending.pop(coin, None)
        if item is None:
            return
        sig, budget = item
        fill = self._slip(bar.o, sig.side, entering=True)
        valid = (sig.stop < fill < sig.take_profit) if sig.side is Side.LONG else (sig.take_profit < fill < sig.stop)
        if not valid:
            return  # gapped through stop or target: skip
        size = RiskManager.size(fill, sig.stop, budget)
        if size * fill < self.min_notional:
            return
        fee = size * fill * self.cfg.taker_fee
        self.cash -= fee
        self.positions[coin] = Position(coin, sig.side, size, fill, sig.stop, sig.take_profit, bar.t, fees=fee)

    @staticmethod
    def _exit_price(pos: Position, bar: Candle) -> tuple[float | None, str]:
        if pos.side is Side.LONG:
            if bar.l <= pos.stop:
                return min(pos.stop, bar.o), "stop"
            if bar.h >= pos.take_profit:
                return max(pos.take_profit, bar.o), "take_profit"
        else:
            if bar.h >= pos.stop:
                return max(pos.stop, bar.o), "stop"
            if bar.l <= pos.take_profit:
                return min(pos.take_profit, bar.o), "take_profit"
        return None, ""

    def _close(self, pos: Position, raw_px: float, t: int, reason: str) -> Trade:
        px = self._slip(raw_px, pos.side, entering=False) if reason != "take_profit" else raw_px
        gross = pos.side.sign * (px - pos.entry) * pos.size
        fee = pos.size * px * self.cfg.taker_fee
        self.cash += gross - fee
        fees = pos.fees + fee
        pnl = gross - fees - pos.funding
        risk = abs(pos.entry - pos.stop) * pos.size
        del self.positions[pos.coin]
        return Trade(
            pos.coin,
            pos.side.value,
            pos.size,
            pos.entry,
            px,
            pos.opened_at,
            t,
            pnl,
            fees,
            pos.funding,
            reason,
            r_multiple=pnl / risk if risk > 0 else 0.0,
        )

    def equity(self, marks: dict[str, float]) -> float:
        return self.cash + sum(p.unrealized(marks.get(c, p.entry)) for c, p in self.positions.items())

    def close_all(self, marks: dict[str, float], t: int, reason: str) -> list[Trade]:
        self.pending.clear()
        return [self._close(p, marks.get(c, p.entry), t, reason) for c, p in list(self.positions.items())]

    # ---------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {
            "cash": self.cash,
            "positions": {c: {**asdict(p), "side": p.side.value} for c, p in self.positions.items()},
            "pending": {
                c: {"signal": {**asdict(s), "side": s.side.value}, "budget": asdict(b)}
                for c, (s, b) in self.pending.items()
            },
        }

    def load_dict(self, d: dict) -> None:
        self.cash = float(d.get("cash", self.cash))
        self.positions = {c: Position(**{**p, "side": Side(p["side"])}) for c, p in d.get("positions", {}).items()}
        self.pending = {
            c: (Signal(**{**v["signal"], "side": Side(v["signal"]["side"])}), Budget(**v["budget"]))
            for c, v in d.get("pending", {}).items()
        }
