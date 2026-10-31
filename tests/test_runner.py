import json

import pytest

from orb.broker.paper import PaperBroker
from orb.config import Config, load_config
from orb.engine import Engine
from orb.notify import Notifier
from orb.risk import RiskManager
from orb.runner import Runner
from orb.state import StateStore
from orb.strategies import build_strategy

from .conftest import T0, H, wave

BARS = wave(1500)


class FakeData:
    """Serves the synthetic wave as if it were the live API, bar-by-bar as time advances."""

    def closed_candles(self, coin, interval, bars, now_ms):
        closed = [b for b in BARS if b.t + H <= now_ms]
        return closed[-bars:]

    def funding(self, coin, start, end):
        return []


def make_runner(tmp_path, clock):
    cfg = Config()
    cfg.market.coins = ["TEST"]
    cfg.strategy.params = {"ema_len": 50, "touch_atr": 1.0}
    engine = Engine(
        build_strategy(cfg.strategy.name, cfg.strategy.params),
        PaperBroker(cfg.execution, 1000),
        RiskManager(cfg.risk),
    )
    return Runner(
        cfg,
        FakeData(),
        engine,
        StateStore(tmp_path / "s.json"),
        Notifier(False),
        "paper",
        now_ms=lambda: clock["now"],
        sleep=lambda s: None,
    )


def test_runner_matches_backtest_and_resumes(tmp_path):
    clock = {"now": T0 + 300 * H}
    r = make_runner(tmp_path, clock)
    r.start()
    assert r.last_bar["TEST"] == T0 + 299 * H  # bars closed before start are never traded
    assert r.tick() == 0

    for _ in range(400):  # advance the clock one bar at a time
        clock["now"] += H
        assert r.tick() == 1
    trades_a = list(r.engine.trades)
    events_a = len(r.engine.events)

    # restart mid-stream from the saved state: no duplicate processing
    r2 = make_runner(tmp_path, clock)
    r2.start()
    assert r2.last_bar == r.last_bar
    assert r2.engine.broker.cash == pytest.approx(r.engine.broker.cash)
    for _ in range(100):
        clock["now"] += H
        r2.tick()
    assert events_a > 0 and trades_a

    st = json.loads((tmp_path / "s.json").read_text())
    assert st["mode"] == "paper" and st["last_bar"]["TEST"] == T0 + 799 * H


def test_runner_refuses_foreign_state(tmp_path):
    StateStore(tmp_path / "s.json").save({"mode": "live"})
    with pytest.raises(RuntimeError):
        make_runner(tmp_path, {"now": T0 + 300 * H}).start()


def test_run_once_survives_errors(tmp_path):
    clock = {"now": T0 + 300 * H}
    r = make_runner(tmp_path, clock)

    def boom():
        raise RuntimeError("network down")

    r.start()
    r.tick = boom
    r.start = lambda: None
    r.run(once=True)  # must not raise


def test_config_loading(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("market:\n  coins: [btc, eth]\n  interval: 4h\nrisk:\n  risk_per_trade: 0.02\n")
    c = load_config(p)
    assert c.market.coins == ["BTC", "ETH"] and c.market.interval_seconds == 14400
    assert c.risk.risk_per_trade == 0.02 and c.api_url.endswith("hyperliquid.xyz")
    p.write_text("market:\n  interval: 2h\n")
    with pytest.raises(ValueError):
        load_config(p)
    p.write_text("risk:\n  risk_per_trade: 0.5\n")  # >5% per trade is rejected
    with pytest.raises(ValueError):
        load_config(p)


def test_notifier_disabled_without_creds():
    assert Notifier(True, token="", chat_id="").send("hi") is False


def test_paper_runner_reproduces_backtest_exactly(tmp_path):
    """The live loop and the backtester share one engine, so they must produce identical trades."""
    from orb.backtest import run_backtest

    clock = {"now": T0 + 300 * H}
    r = make_runner(tmp_path, clock)
    r.start()
    while clock["now"] < BARS[-1].t + H:
        clock["now"] += H
        r.tick()
    live = r.engine.trades

    # same history the runner warmed on (its last `warm_bars` closed bars before start)
    history = BARS[max(0, 300 - r.warm_bars) :]
    bt = run_backtest(r.cfg, {"TEST": (history, [])}, trade_from=T0 + 300 * H).result.trades
    bt_closed = [t for t in bt if t.exit_reason != "end_of_test"]
    assert live and live == bt_closed
