"""Performance statistics for a run."""

from __future__ import annotations

import math

from orb.models import EquityPoint, Trade


def max_drawdown(equity: list[float]) -> float:
    peak, mdd = -math.inf, 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = max(mdd, 1 - e / peak)
    return mdd


def drawdown_series(equity: list[float]) -> list[float]:
    peak, out = -math.inf, []
    for e in equity:
        peak = max(peak, e)
        out.append(1 - e / peak if peak > 0 else 0.0)
    return out


def summarize(
    trades: list[Trade],
    curve: list[EquityPoint],
    starting_equity: float,
    bar_seconds: int,
    n_coins: int = 1,
) -> dict:
    eq = [p.equity for p in curve] or [starting_equity]
    end = eq[-1]
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] > 0]
    bars_per_year = 365 * 86_400 / bar_seconds
    sharpe = 0.0
    if len(rets) > 1:
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        sharpe = mu / sd * math.sqrt(bars_per_year) if sd > 0 else 0.0
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    span_ms = (curve[-1].t - curve[0].t) if len(curve) > 1 else 0
    held_ms = sum(t.closed_at - t.opened_at + bar_seconds * 1000 for t in trades)
    return {
        "start_equity": starting_equity,
        "end_equity": end,
        "total_return": end / starting_equity - 1 if starting_equity else 0.0,
        "max_drawdown": max_drawdown(eq),
        "sharpe": sharpe,
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (math.inf if gross_win > 0 else 0.0),
        "avg_r": sum(t.r_multiple for t in trades) / len(trades) if trades else 0.0,
        "expectancy": sum(t.pnl for t in trades) / len(trades) if trades else 0.0,
        "fees": sum(t.fees for t in trades),
        "funding": sum(t.funding for t in trades),
        "time_in_market": held_ms / (span_ms * n_coins) if span_ms > 0 else 0.0,
        "days": span_ms / 86_400_000,
    }
