"""Backtester and parameter sweep, built on the same Engine the live bot uses."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from orb.broker.paper import PaperBroker
from orb.config import Config
from orb.data import funding_in_window
from orb.engine import Engine
from orb.metrics import summarize
from orb.models import Candle, EquityPoint, RunResult
from orb.risk import RiskManager
from orb.strategies import build_strategy

Market = dict[str, tuple[list[Candle], list[tuple[int, float]]]]


@dataclass
class BacktestReport:
    result: RunResult
    stats: dict
    benchmark_return: float
    params: dict[str, Any]
    window: tuple[int, int]
    coins: list[str] = field(default_factory=list)


def run_backtest(
    cfg: Config,
    market: Market,
    params: dict[str, Any] | None = None,
    trade_from: int | None = None,
    trade_until: int | None = None,
) -> BacktestReport:
    """Replay `market` through the engine.

    Bars before `trade_from` only warm up indicators; bars at/after `trade_until` are ignored.
    Any position still open at the end is closed at the last close ("end_of_test").
    """
    params = {**cfg.strategy.params, **(params or {})}
    strategy = build_strategy(cfg.strategy.name, params)
    broker = PaperBroker(cfg.execution, cfg.risk.starting_equity)
    risk = RiskManager(cfg.risk)
    engine = Engine(strategy, broker, risk)
    step_ms = cfg.market.interval_seconds * 1000

    coins = [c for c in cfg.market.coins if c in market and market[c][0]]
    by_coin = {c: {k.t: k for k in market[c][0]} for c in coins}
    timeline = sorted({t for c in coins for t in by_coin[c]})
    if trade_until is not None:
        timeline = [t for t in timeline if t < trade_until]
    start = trade_from if trade_from is not None else (timeline[0] if timeline else 0)

    curve: list[EquityPoint] = []
    first_close: dict[str, float] = {}
    last_close: dict[str, float] = {}
    for t in timeline:
        for c in coins:
            bar = by_coin[c].get(t)
            if bar is None:
                continue
            fund = funding_in_window(market[c][1], t, t + step_ms)
            if t < start:
                engine.warm(c, bar, fund)
                continue
            first_close.setdefault(c, bar.o)
            last_close[c] = bar.c
            engine.step(c, bar, fund)
        if t >= start:
            curve.append(EquityPoint(t + step_ms, broker.equity(engine.marks)))

    if curve:
        for tr in broker.close_all(engine.marks, curve[-1].t, "end_of_test"):
            engine.trades.append(tr)
        curve[-1] = EquityPoint(curve[-1].t, broker.equity(engine.marks))

    bench = sum(last_close[c] / first_close[c] - 1 for c in first_close) / len(first_close) if first_close else 0.0
    result = RunResult(engine.trades, curve, cfg.risk.starting_equity, engine.events)
    stats = summarize(engine.trades, curve, cfg.risk.starting_equity, cfg.market.interval_seconds, len(coins))
    window = (curve[0].t - step_ms, curve[-1].t) if curve else (0, 0)
    return BacktestReport(result, stats, bench, params, window, coins)


def parse_grid(items: list[str]) -> dict[str, list[Any]]:
    """['touch_atr=0.3,0.5', 'reward_r=2,3'] -> {'touch_atr': [0.3, 0.5], 'reward_r': [2.0, 3.0]}"""
    grid: dict[str, list[Any]] = {}
    for it in items:
        if "=" not in it:
            raise ValueError(f"bad grid item {it!r}; expected name=v1,v2")
        k, vs = it.split("=", 1)
        vals: list[Any] = []
        for v in vs.split(","):
            v = v.strip()
            try:
                vals.append(int(v) if v.lstrip("-").isdigit() else float(v))
            except ValueError:
                vals.append(v)
        grid[k.strip()] = vals
    return grid


def sweep(cfg: Config, market: Market, grid: dict[str, list[Any]], split: float = 0.7) -> list[dict]:
    """In-sample / out-of-sample sweep.

    The timeline is cut at `split`. Each combination is scored on the in-sample part, and
    the SAME combination is then run on the unseen out-of-sample part (indicators warmed on
    in-sample bars). Rows are sorted by in-sample Sharpe so you can see whether the
    in-sample winner holds up out of sample.
    """
    times = sorted({k.t for c in cfg.market.coins if c in market for k in market[c][0]})
    if len(times) < 10:
        raise ValueError("not enough data to sweep")
    cut = times[int(len(times) * split)]
    keys = list(grid)
    rows = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        p = dict(zip(keys, combo))
        ins = run_backtest(cfg, market, p, trade_until=cut).stats
        oos = run_backtest(cfg, market, p, trade_from=cut).stats
        rows.append(
            {
                **p,
                "is_return": ins["total_return"],
                "is_sharpe": ins["sharpe"],
                "is_trades": ins["trades"],
                "oos_return": oos["total_return"],
                "oos_sharpe": oos["sharpe"],
                "oos_trades": oos["trades"],
                "oos_max_dd": oos["max_drawdown"],
            }
        )
    rows.sort(key=lambda r: r["is_sharpe"], reverse=True)
    return rows
