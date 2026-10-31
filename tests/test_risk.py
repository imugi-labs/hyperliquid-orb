import pytest

from orb.config import RiskCfg
from orb.risk import Budget, RiskManager

DAY = 86_400_000


def test_size_is_risk_over_distance_capped_by_leverage():
    b = Budget(risk_amount=10, max_notional=3000)
    assert RiskManager.size(100, 95, b) == pytest.approx(2.0)  # $10 / $5
    assert RiskManager.size(100, 99.9, b) == pytest.approx(30.0)  # capped: 3000 / 100
    assert RiskManager.size(100, 100, b) == 0.0


def test_budget_scales_with_equity():
    r = RiskManager(RiskCfg(risk_per_trade=0.01, max_leverage=3))
    assert r.budget(2000) == Budget(20, 6000)


def test_position_limits():
    r = RiskManager(RiskCfg(max_open_positions=2))
    r.update(0, 1000)
    assert r.check("BTC", set(), 1000) is None
    assert r.check("BTC", {"BTC"}, 1000) == "already in position"
    assert r.check("SOL", {"BTC", "ETH"}, 1000) == "max open positions"


def test_daily_loss_limit_resets_next_utc_day():
    r = RiskManager(RiskCfg(daily_loss_limit=0.03, max_drawdown_kill=0.5))
    r.update(10, 1000)
    r.update(20, 969)
    assert r.check("BTC", set(), 969) == "daily loss limit"
    r.update(DAY + 1, 969)  # new day: new baseline
    assert r.check("BTC", set(), 969) is None


def test_kill_switch_fires_once_and_halts():
    r = RiskManager(RiskCfg(max_drawdown_kill=0.2))
    assert r.update(0, 1000) is None
    assert r.update(1, 1200) is None
    assert "drawdown" in r.update(2, 950)
    assert r.update(3, 900) is None  # already halted, no second alert
    assert r.check("BTC", set(), 2000).startswith("halted")
