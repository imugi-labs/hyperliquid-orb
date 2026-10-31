"""Donchian breakout with an EMA trend filter.

Long when the close breaks above the highest high of the previous `lookback` bars
while above the EMA; short is the mirror. Stop = close -/+ `stop_atr` x ATR,
take-profit at `reward_r` x risk.
"""

from __future__ import annotations

from dataclasses import dataclass

from orb.indicators import ATR, EMA, Window
from orb.models import Candle, Side, Signal
from orb.strategies.base import BarContext, Strategy


@dataclass
class _S:
    ema: EMA
    atr: ATR
    highs: Window
    lows: Window


class Breakout(Strategy):
    name = "breakout"
    defaults = {"lookback": 48, "ema_len": 200, "atr_len": 14, "stop_atr": 2.0, "reward_r": 2.5}

    def new_state(self) -> _S:
        n = int(self.p["lookback"])
        return _S(EMA(int(self.p["ema_len"])), ATR(int(self.p["atr_len"])), Window(n), Window(n))

    def warmup_bars(self) -> int:
        return max(int(self.p["ema_len"]), int(self.p["lookback"])) + 1

    def on_bar(self, coin: str, bar: Candle, ctx: BarContext) -> Signal | None:
        s = self.state(coin)
        ema = s.ema.update(bar.c)
        atr = s.atr.update(bar.h, bar.l, bar.c)
        hi, lo = s.highs.max(), s.lows.min()
        s.highs.push(bar.h)
        s.lows.push(bar.l)
        if ema is None or atr is None or hi is None or lo is None or atr <= 0:
            return None
        pad = float(self.p["stop_atr"]) * atr
        r = float(self.p["reward_r"])
        if bar.c > hi and bar.c > ema:
            stop = bar.c - pad
            return Signal(coin, Side.LONG, stop, bar.c + r * pad, "breakout_long", bar.t)
        if bar.c < lo and bar.c < ema:
            stop = bar.c + pad
            return Signal(coin, Side.SHORT, stop, bar.c - r * pad, "breakout_short", bar.t)
        return None
