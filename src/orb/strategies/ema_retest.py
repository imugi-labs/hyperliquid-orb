"""EMA trend retest: buy pullbacks to a rising long EMA, sell rallies to a falling one.

Rules (long; short is the mirror image), evaluated on a closed bar:
  1. Trend: the previous 3 closes are above the EMA and the EMA is above its value 5 bars ago.
  2. Retest: the bar's low comes within `touch_atr` ATRs of the EMA.
  3. Rejection: the bar closes back above the EMA and above its open.
  4. Crowding filter: skip if 8h-equivalent funding > `max_funding_8h`.
Stop = bar low - `stop_atr` x ATR.  Take-profit = close + `reward_r` x (close - stop).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from orb.indicators import ATR, EMA
from orb.models import Candle, Side, Signal
from orb.strategies.base import BarContext, Strategy


@dataclass
class _S:
    ema: EMA
    atr: ATR
    closes: deque = field(default_factory=lambda: deque(maxlen=4))
    emas: deque = field(default_factory=lambda: deque(maxlen=6))


class EmaRetest(Strategy):
    name = "ema_retest"
    defaults = {
        "ema_len": 200,
        "atr_len": 14,
        "touch_atr": 0.5,
        "stop_atr": 1.5,
        "reward_r": 2.0,
        "max_funding_8h": 0.0005,
    }

    def new_state(self) -> _S:
        return _S(EMA(int(self.p["ema_len"])), ATR(int(self.p["atr_len"])))

    def warmup_bars(self) -> int:
        return int(self.p["ema_len"]) + 6

    def on_bar(self, coin: str, bar: Candle, ctx: BarContext) -> Signal | None:
        s = self.state(coin)
        ema = s.ema.update(bar.c)
        atr = s.atr.update(bar.h, bar.l, bar.c)
        prev_closes = list(s.closes)
        prev_emas = list(s.emas)
        s.closes.append(bar.c)
        if ema is not None:
            s.emas.append(ema)
        if ema is None or atr is None or len(prev_emas) < 5 or len(prev_closes) < 3 or atr <= 0:
            return None

        f8 = ctx.funding_1h * 8 if ctx.funding_1h is not None else None
        cap = float(self.p["max_funding_8h"])
        touch = float(self.p["touch_atr"]) * atr
        r = float(self.p["reward_r"])
        stop_pad = float(self.p["stop_atr"]) * atr
        ema_5ago = prev_emas[-5]
        last3 = prev_closes[-3:]
        last3_emas = prev_emas[-3:]

        uptrend = all(c > e for c, e in zip(last3, last3_emas)) and ema > ema_5ago
        if uptrend and bar.l <= ema + touch and bar.c > ema and bar.c > bar.o:
            if f8 is not None and f8 > cap:
                return None
            stop = bar.l - stop_pad
            return Signal(coin, Side.LONG, stop, bar.c + r * (bar.c - stop), "ema_retest_long", bar.t)

        downtrend = all(c < e for c, e in zip(last3, last3_emas)) and ema < ema_5ago
        if downtrend and bar.h >= ema - touch and bar.c < ema and bar.c < bar.o:
            if f8 is not None and f8 < -cap:
                return None
            stop = bar.h + stop_pad
            return Signal(coin, Side.SHORT, stop, bar.c - r * (stop - bar.c), "ema_retest_short", bar.t)
        return None
