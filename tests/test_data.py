import pytest
import requests

from orb.data import DataError, HyperliquidData, funding_in_window

H = 3_600_000


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def post(self, url, json, timeout):
        self.calls.append(json)
        return self.handler(json, len(self.calls))


def candle(t, c=100.0):
    return {"t": t, "o": str(c), "h": str(c + 1), "l": str(c - 1), "c": str(c), "v": "5"}


def test_candles_parsed_sorted_and_bounded(tmp_path):
    def h(p, n):
        s, e = p["req"]["startTime"], p["req"]["endTime"]
        return Resp(200, [candle(t) for t in range(s - H, e + H, H)][::-1])

    d = HyperliquidData(cache_dir=tmp_path, session=FakeSession(h))
    out = d.candles("BTC", "1h", 10 * H, 20 * H)
    assert [c.t for c in out] == list(range(10 * H, 20 * H, H))
    assert out[0].h == 101.0


def test_closed_candles_drops_in_progress_bar(tmp_path):
    d = HyperliquidData(
        cache_dir=tmp_path,
        session=FakeSession(lambda p, n: Resp(200, [candle(t) for t in range(0, 100 * H, H)])),
    )
    out = d.closed_candles("BTC", "1h", 3, now_ms=50 * H + 123)
    assert [c.t for c in out] == [47 * H, 48 * H, 49 * H]


def test_retries_then_succeeds_and_then_gives_up(tmp_path):
    flaky = FakeSession(lambda p, n: Resp(429, {}) if n < 3 else Resp(200, {"BTC": "1"}))
    d = HyperliquidData(cache_dir=tmp_path, session=flaky)
    d._sleep = lambda s: None
    assert d.mids() == {"BTC": 1.0} and len(flaky.calls) == 3
    dead = HyperliquidData(cache_dir=tmp_path, session=FakeSession(lambda p, n: Resp(500, {})), max_retries=2)
    dead._sleep = lambda s: None
    with pytest.raises(DataError):
        dead.mids()


def test_funding_pagination(tmp_path):
    def h(p, n):
        s = -(-p["startTime"] // H) * H  # API returns prints at/after startTime
        rows = [{"time": t, "fundingRate": "0.0001"} for t in range(s, min(s + 500 * H, 1200 * H), H)]
        return Resp(200, rows)

    d = HyperliquidData(cache_dir=tmp_path, session=FakeSession(h))
    f = d.funding("BTC", 0, 1200 * H)
    assert len(f) == 1200 and f[0] == (0, 0.0001)
    assert funding_in_window(f, 10 * H, 13 * H) == [(10 * H, 1e-4), (11 * H, 1e-4), (12 * H, 1e-4)]


def test_sync_writes_and_reuses_cache(tmp_path):
    def h(p, n):
        if p["type"] == "candleSnapshot":
            s, e = p["req"]["startTime"], p["req"]["endTime"]
            return Resp(200, [candle(t) for t in range(s, e, H)])
        return Resp(200, [])

    sess = FakeSession(h)
    d = HyperliquidData(cache_dir=tmp_path, session=sess)
    now = 1000 * 86_400_000 + 5
    c1, _ = d.sync("BTC", "1h", 2, now_ms=now)
    assert len(c1) == 48 and (tmp_path / "BTC_1h_candles.csv").exists()
    cached, _ = d.load_cached("BTC", "1h")
    assert cached == c1
    n_before = len(sess.calls)
    c2, _ = d.sync("BTC", "1h", 2, now_ms=now + H)  # one new bar
    assert len(c2) == 48 and c2[-1].t == c1[-1].t + H
    assert sess.calls[n_before]["req"]["startTime"] == c1[-1].t + H  # incremental fetch


def test_size_decimals(tmp_path):
    d = HyperliquidData(
        cache_dir=tmp_path,
        session=FakeSession(lambda p, n: Resp(200, {"universe": [{"name": "BTC", "szDecimals": 5}]})),
    )
    assert d.size_decimals() == {"BTC": 5}


def test_sync_backfills_older_history_and_never_shrinks_cache(tmp_path):
    def h(p, n):
        if p["type"] == "candleSnapshot":
            s, e = p["req"]["startTime"], p["req"]["endTime"]
            return Resp(200, [candle(t) for t in range(s, e, H)])
        return Resp(200, [])

    d = HyperliquidData(cache_dir=tmp_path, session=FakeSession(h))
    now = 1000 * 86_400_000
    short, _ = d.sync("BTC", "1h", 2, now_ms=now)
    long_, _ = d.sync("BTC", "1h", 5, now_ms=now)  # asks for more history than cached
    assert len(short) == 48 and len(long_) == 120 and long_[0].t == now - 5 * 86_400_000
    again, _ = d.sync("BTC", "1h", 2, now_ms=now)  # shorter window afterwards
    assert len(again) == 48
    assert len(d.load_cached("BTC", "1h")[0]) == 120  # older data kept
