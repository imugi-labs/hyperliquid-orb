import pytest

from orb.backtest import parse_grid, run_backtest, sweep
from orb.models import Candle
from orb.report import render

from .conftest import wave


def market(n=1500):
    return {"TEST": (wave(n), [])}


def test_backtest_runs_and_accounts_consistently(cfg):
    rep = run_backtest(cfg, market())
    s = rep.stats
    assert s["trades"] > 0
    # equity change == sum of trade PnL (all positions closed at end_of_test)
    assert s["end_equity"] - cfg.risk.starting_equity == pytest.approx(sum(t.pnl for t in rep.result.trades), abs=1e-6)
    assert 0 <= s["max_drawdown"] < 1 and 0 <= s["win_rate"] <= 1
    assert all(t.closed_at >= t.opened_at for t in rep.result.trades)


def test_backtest_is_deterministic(cfg):
    a, b = run_backtest(cfg, market()), run_backtest(cfg, market())
    assert a.stats == b.stats


def test_no_lookahead_future_bars_do_not_change_past_trades(cfg):
    full = run_backtest(cfg, market(1500)).result.trades
    cut = run_backtest(cfg, market(1000)).result.trades
    closed_early = [t for t in cut if t.exit_reason != "end_of_test"]
    assert closed_early and closed_early == full[: len(closed_early)]


def test_one_position_per_coin(cfg):
    trades = run_backtest(cfg, market()).result.trades
    for a, b in zip(trades, trades[1:]):
        assert b.opened_at >= a.closed_at


def test_kill_switch_flattens_and_halts(cfg):
    """A strategy that keeps buying a falling market must be stopped by the kill switch."""
    from orb.broker.paper import PaperBroker
    from orb.engine import Engine
    from orb.models import Side, Signal
    from orb.risk import RiskManager
    from orb.strategies.base import Strategy

    from .conftest import bar

    class AlwaysLong(Strategy):
        name, defaults = "always_long", {}

        def new_state(self):
            return None

        def warmup_bars(self):
            return 0

        def on_bar(self, coin, b, ctx):
            return Signal(coin, Side.LONG, b.c * 0.99, b.c * 1.5, "always", b.t)

    cfg.risk.max_drawdown_kill = 0.05
    cfg.risk.daily_loss_limit = 0.99
    eng = Engine(AlwaysLong(), PaperBroker(cfg.execution, 1000), RiskManager(cfg.risk))
    px = 100.0
    for i in range(60):
        nxt = px * 0.985
        eng.step("TEST", bar(i, px, px * 1.001, nxt * 0.999, nxt))
        px = nxt
    kills = [i for i, e in enumerate(eng.events) if "KILL SWITCH" in e]
    assert len(kills) == 1
    assert not any("SIGNAL" in e for e in eng.events[kills[0] + 1 :])
    assert not eng.broker.positions and eng.risk.s.halted
    assert 0.04 < 1 - eng.broker.equity(eng.marks) / 1000 < 0.08


def test_trade_window_and_warmup(cfg):
    m = market()
    times = [c.t for c in m["TEST"][0]]
    cut = times[1000]
    ins = run_backtest(cfg, m, trade_until=cut)
    oos = run_backtest(cfg, m, trade_from=cut)
    assert all(t.opened_at < cut for t in ins.result.trades)
    assert all(t.opened_at >= cut for t in oos.result.trades)
    assert oos.result.trades, "OOS should trade immediately because indicators were warmed in-sample"


def test_sweep_and_grid(cfg):
    g = parse_grid(["touch_atr=0.5,1.0", "reward_r=2"])
    assert g == {"touch_atr": [0.5, 1.0], "reward_r": [2]}
    rows = sweep(cfg, market(), g)
    assert len(rows) == 2 and rows[0]["is_sharpe"] >= rows[1]["is_sharpe"]
    with pytest.raises(ValueError):
        parse_grid(["bad"])
    with pytest.raises(ValueError):
        sweep(cfg, {"TEST": ([Candle(0, 1, 1, 1, 1, 1)], [])}, g)


def test_report_renders(cfg):
    rep = run_backtest(cfg, market())
    page = render(rep, "ema_retest", "1h")
    assert page.startswith("<!doctype html>") and "Equity" in page and '<path d="M' in page
