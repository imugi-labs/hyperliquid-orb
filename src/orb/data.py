"""Hyperliquid market data: candles, funding, mids, metadata.

Uses the public /info endpoint (no keys). Handles pagination, retries with
exponential backoff on 429/5xx, and a CSV cache so repeated backtests are
offline and reproducible.
"""

from __future__ import annotations

import csv
import logging
import time
from bisect import bisect_left
from pathlib import Path
from typing import Any

import requests

from orb.config import INTERVALS
from orb.models import Candle

log = logging.getLogger(__name__)

CANDLE_PAGE = 5000
FUNDING_PAGE = 500


class DataError(RuntimeError):
    pass


class HyperliquidData:
    def __init__(
        self,
        base_url: str = "https://api.hyperliquid.xyz",
        cache_dir: str | Path | None = "data",
        session: requests.Session | None = None,
        timeout: float = 15.0,
        max_retries: int = 5,
    ):
        self.url = base_url.rstrip("/") + "/info"
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = time.sleep  # injectable for tests

    # ------------------------------------------------------------------ http
    def _post(self, payload: dict[str, Any]) -> Any:
        delay = 0.5
        for attempt in range(1, self.max_retries + 1):
            try:
                r = self.session.post(self.url, json=payload, timeout=self.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    raise DataError(f"HTTP {r.status_code}")
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, DataError, ValueError) as e:
                if attempt == self.max_retries:
                    raise DataError(f"{payload.get('type')} failed after {attempt} tries: {e}") from e
                log.warning("info %s failed (%s), retry %d in %.1fs", payload.get("type"), e, attempt, delay)
                self._sleep(delay)
                delay = min(delay * 2, 8.0)
        raise DataError("unreachable")  # pragma: no cover

    # --------------------------------------------------------------- candles
    def candles(self, coin: str, interval: str, start_ms: int, end_ms: int) -> list[Candle]:
        """All candles with open time in [start_ms, end_ms), oldest first.

        Hyperliquid serves at most the most recent 5000 bars per interval.
        """
        step = INTERVALS[interval] * 1000
        out: dict[int, Candle] = {}
        cursor = start_ms
        while cursor < end_ms:
            page_end = min(end_ms, cursor + step * CANDLE_PAGE)
            raw = self._post(
                {
                    "type": "candleSnapshot",
                    "req": {"coin": coin, "interval": interval, "startTime": cursor, "endTime": page_end},
                }
            )
            for k in raw or []:
                c = Candle(int(k["t"]), float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"]), float(k["v"]))
                if start_ms <= c.t < end_ms:
                    out[c.t] = c
            cursor = page_end
        return [out[t] for t in sorted(out)]

    def closed_candles(self, coin: str, interval: str, bars: int, now_ms: int | None = None) -> list[Candle]:
        """The last `bars` fully closed candles (the in-progress bar is dropped)."""
        now_ms = now_ms or int(time.time() * 1000)
        step = INTERVALS[interval] * 1000
        current_open = now_ms - now_ms % step
        return self.candles(coin, interval, current_open - step * bars, current_open)

    # --------------------------------------------------------------- funding
    def funding(self, coin: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
        """Hourly funding prints (time_ms, rate) in [start_ms, end_ms)."""
        out: dict[int, float] = {}
        cursor = start_ms
        while cursor < end_ms:
            raw = self._post({"type": "fundingHistory", "coin": coin, "startTime": cursor, "endTime": end_ms})
            if not raw:
                break
            for f in raw:
                t = int(f["time"])
                if start_ms <= t < end_ms:
                    out[t] = float(f["fundingRate"])
            last = int(raw[-1]["time"])
            if len(raw) < FUNDING_PAGE or last + 1 <= cursor:
                break
            cursor = last + 1
        return sorted(out.items())

    # ------------------------------------------------------------------ misc
    def mids(self) -> dict[str, float]:
        return {k: float(v) for k, v in self._post({"type": "allMids"}).items()}

    def size_decimals(self) -> dict[str, int]:
        meta = self._post({"type": "meta"})
        return {a["name"]: int(a["szDecimals"]) for a in meta["universe"]}

    # ----------------------------------------------------------------- cache
    def _cpath(self, coin: str, interval: str) -> Path:
        assert self.cache_dir is not None
        return self.cache_dir / f"{coin}_{interval}_candles.csv"

    def _fpath(self, coin: str) -> Path:
        assert self.cache_dir is not None
        return self.cache_dir / f"{coin}_funding.csv"

    def load_cached(self, coin: str, interval: str) -> tuple[list[Candle], list[tuple[int, float]]]:
        if not self.cache_dir:
            return [], []
        candles: list[Candle] = []
        funding: list[tuple[int, float]] = []
        cp, fp = self._cpath(coin, interval), self._fpath(coin)
        if cp.exists():
            with cp.open() as f:
                for r in csv.DictReader(f):
                    candles.append(
                        Candle(
                            int(r["t"]),
                            float(r["o"]),
                            float(r["h"]),
                            float(r["l"]),
                            float(r["c"]),
                            float(r["v"]),
                        )
                    )
        if fp.exists():
            with fp.open() as f:
                funding = [(int(r["t"]), float(r["rate"])) for r in csv.DictReader(f)]
        return candles, funding

    def sync(self, coin: str, interval: str, days: int, now_ms: int | None = None):
        """Return the last `days` of closed candles + funding.

        Only the missing pieces are downloaded: history older than the cache (backfill)
        and bars newer than it. The cache keeps everything it has ever seen, so asking
        for a shorter window never deletes older data.
        """
        now_ms = now_ms or int(time.time() * 1000)
        step = INTERVALS[interval] * 1000
        end = now_ms - now_ms % step
        start = end - days * 86_400_000
        old_c, old_f = self.load_cached(coin, interval)
        c_map = {c.t: c for c in old_c}
        f_map = dict(old_f)

        if c_map:
            ranges = [(start, min(min(c_map), end)), (max(max(c_map) + step, start), end)]
        else:
            ranges = [(start, end)]
        for a, b in ranges:
            if a < b:
                for c in self.candles(coin, interval, a, b):
                    c_map[c.t] = c

        if f_map:
            f_ranges = [(start, min(min(f_map), end)), (max(max(f_map) + 1, start), end)]
        else:
            f_ranges = [(start, end)]
        for a, b in f_ranges:
            if a < b:
                for t, r in self.funding(coin, a, b):
                    f_map[t] = r

        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with self._cpath(coin, interval).open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["t", "o", "h", "l", "c", "v"])
                w.writerows(
                    [c_map[t].t, c_map[t].o, c_map[t].h, c_map[t].l, c_map[t].c, c_map[t].v] for t in sorted(c_map)
                )
            with self._fpath(coin).open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["t", "rate"])
                w.writerows((t, f_map[t]) for t in sorted(f_map))
        candles = [c_map[t] for t in sorted(c_map) if start <= t < end]
        funding = [(t, f_map[t]) for t in sorted(f_map) if start <= t < end]
        return candles, funding


def funding_in_window(funding: list[tuple[int, float]], start: int, end: int) -> list[tuple[int, float]]:
    """Funding prints with start <= t < end (funding must be sorted by time)."""
    i = bisect_left(funding, (start, float("-inf")))
    out = []
    while i < len(funding) and funding[i][0] < end:
        out.append(funding[i])
        i += 1
    return out
