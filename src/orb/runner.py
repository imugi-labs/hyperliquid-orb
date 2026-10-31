"""Real-time loop for paper and live trading.

On start: restore state, warm the strategy on recent history (no trading), then
poll for newly CLOSED bars and push each through the engine exactly as the
backtester does. State is saved after every tick, so a crash or restart resumes
without re-trading old bars.
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable

from orb.config import Config
from orb.data import HyperliquidData, funding_in_window
from orb.engine import Engine
from orb.notify import Notifier
from orb.risk import RiskState
from orb.state import StateStore

log = logging.getLogger(__name__)


class Runner:
    def __init__(
        self,
        cfg: Config,
        data: HyperliquidData,
        engine: Engine,
        store: StateStore,
        notifier: Notifier,
        mode: str,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cfg, self.data, self.engine, self.store, self.notifier = cfg, data, engine, store, notifier
        self.mode = mode
        self.now_ms, self.sleep = now_ms, sleep
        self.last_bar: dict[str, int] = {}
        self._stop = False
        self.step_ms = cfg.market.interval_seconds * 1000
        # Long warm-up so recursive indicators (EMA/ATR) converge to the same values a
        # backtest over long history would have; Hyperliquid serves at most 5000 bars.
        self.warm_bars = min(5000, max(3 * engine.strategy.warmup_bars(), 500))

    # ----------------------------------------------------------------- state
    def _restore(self) -> None:
        st = self.store.load()
        if not st:
            return
        if st.get("mode") != self.mode:
            raise RuntimeError(f"state file belongs to mode {st.get('mode')!r}; use a different storage.state_file")
        self.last_bar = {k: int(v) for k, v in st.get("last_bar", {}).items()}
        if hasattr(self.engine.broker, "load_dict"):
            self.engine.broker.load_dict(st.get("broker", {}))
        self.engine.risk.s = RiskState(**st.get("risk", {})) if st.get("risk") else self.engine.risk.s
        log.info("restored state: last bars %s", self.last_bar)

    def save(self) -> None:
        b = self.engine.broker
        self.store.save(
            {
                "mode": self.mode,
                "saved_at": self.now_ms(),
                "last_bar": self.last_bar,
                "broker": b.to_dict() if hasattr(b, "to_dict") else {},
                "risk": self.engine.risk.to_dict(),
                "equity": b.equity(self.engine.marks),
                "recent_events": self.engine.events[-50:],
            }
        )

    # --------------------------------------------------------------- startup
    def start(self) -> None:
        self._restore()
        warm = self.warm_bars
        now = self.now_ms()
        for coin in self.cfg.market.coins:
            bars = self.data.closed_candles(coin, self.cfg.market.interval, warm, now)
            funding = self.data.funding(coin, now - 8 * 3_600_000, now)
            last = self.last_bar.get(coin)
            for bar in bars:
                if last is None or bar.t <= last:
                    self.engine.warm(coin, bar, funding_in_window(funding, bar.t, bar.t + self.step_ms))
            if last is None and bars:
                self.last_bar[coin] = bars[-1].t  # never trade bars that closed before we started
        self.save()
        msg = (
            f"{self.mode.upper()} started: {', '.join(self.cfg.market.coins)} {self.cfg.market.interval}, "
            f"strategy {self.cfg.strategy.name}, equity ${self.engine.broker.equity(self.engine.marks):,.2f}"
        )
        log.info(msg)
        self.notifier.send(msg)

    # ------------------------------------------------------------------ loop
    def tick(self) -> int:
        """Process every newly closed bar. Returns how many bars were processed."""
        n = 0
        now = self.now_ms()
        for coin in self.cfg.market.coins:
            bars = self.data.closed_candles(coin, self.cfg.market.interval, 5, now)
            new = [b for b in bars if b.t > self.last_bar.get(coin, -1)]
            if not new:
                continue
            funding = self.data.funding(coin, new[0].t, new[-1].t + self.step_ms)
            for bar in new:
                before = len(self.engine.events)
                self.engine.step(coin, bar, funding_in_window(funding, bar.t, bar.t + self.step_ms))
                for ev in self.engine.events[before:]:
                    self.notifier.send(ev)
                self.last_bar[coin] = bar.t
                n += 1
        if n:
            self.save()
        return n

    def run(self, once: bool = False) -> None:
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)
        self.start()
        backoff = self.cfg.market.poll_seconds
        last_err = ""
        while not self._stop:
            try:
                n = self.tick()
                if n:
                    log.info("processed %d bar(s); equity $%.2f", n, self.engine.broker.equity(self.engine.marks))
                backoff = self.cfg.market.poll_seconds
                last_err = ""
            except Exception as e:  # keep the bot alive through transient failures
                err = f"{type(e).__name__}: {e}"
                log.exception("tick failed")
                if err != last_err:
                    self.notifier.send(f"error: {err}")
                last_err = err
                backoff = min(backoff * 2, 600)
            if once:
                break
            self.sleep(backoff)
        self.save()
        log.info("stopped")

    def _handle_stop(self, *_):
        log.info("stop requested; finishing current tick")
        self._stop = True
