import pytest

from orb.broker.live import LiveBroker, round_px, round_size
from orb.config import BuilderCfg, ExecutionCfg
from orb.models import Side, Signal
from orb.risk import Budget

from .conftest import T0, bar

ADDR = "0x" + "ab" * 20


class FakeExchange:
    def __init__(self, fill=True):
        self.calls = []
        self.fill = fill

    def bulk_orders(self, orders, builder=None, grouping="na"):
        self.calls.append(("bulk", orders, builder, grouping))
        st = {"filled": {"totalSz": str(orders[0]["sz"]), "avgPx": "100.1", "oid": 7}} if self.fill else {"error": "x"}
        return {
            "status": "ok",
            "response": {"data": {"statuses": [st, {"resting": {"oid": 8}}, {"resting": {"oid": 9}}]}},
        }

    def cancel(self, coin, oid):
        self.calls.append(("cancel", coin, oid))

    def market_close(self, coin, slippage=0.05, builder=None):
        self.calls.append(("close", coin, builder))

    def approve_builder_fee(self, builder, max_fee_rate):
        self.calls.append(("approve", builder, max_fee_rate))
        return {"status": "ok"}


class FakeInfo:
    def __init__(self):
        self.szi = {}
        self.fills = []
        self.orders = []

    def all_mids(self):
        return {"BTC": "100.0"}

    def user_state(self, addr):
        return {
            "marginSummary": {"accountValue": "1234.5"},
            "assetPositions": [{"position": {"coin": c, "szi": str(s)}} for c, s in self.szi.items()],
        }

    def user_fills_by_time(self, addr, start):
        return self.fills

    def open_orders(self, addr):
        return self.orders


def make(builder=False, fill=True):
    ex, info = FakeExchange(fill), FakeInfo()
    b = LiveBroker(
        ex,
        info,
        ADDR,
        {"BTC": 3},
        ExecutionCfg(market_slippage=0.01),
        BuilderCfg(enabled=builder, address=ADDR if builder else "", fee_tenths_bps=25),
    )
    return b, ex, info


def test_round_helpers():
    assert round_size(1.23456, 3) == 1.234
    assert round_px(123456.7, 2) == 123457.0
    assert round_px(2368.744, 4) == 2368.7
    assert round_px(0.0123456, 0) == 0.012346
    with pytest.raises(ValueError):
        round_px(0, 1)


def test_entry_is_atomic_bulk_with_reduce_only_exits_and_builder():
    b, ex, _ = make(builder=True)
    msg = b.submit(Signal("BTC", Side.LONG, 95, 110, "t", T0), Budget(10, 10_000))
    assert msg.startswith("FILLED")
    kind, orders, builder, grouping = ex.calls[0]
    assert grouping == "normalTpsl" and builder == {"b": ADDR, "f": 25}
    entry, tp, sl = orders
    assert entry["is_buy"] and not entry["reduce_only"] and entry["order_type"] == {"limit": {"tif": "Ioc"}}
    assert entry["sz"] == 2.0 and entry["limit_px"] == 101.0  # mid + 1% slippage bound
    assert tp["reduce_only"] and not tp["is_buy"] and tp["order_type"]["trigger"]["tpsl"] == "tp"
    assert sl["reduce_only"] and sl["order_type"]["trigger"] == {
        "triggerPx": 95.0,
        "isMarket": True,
        "tpsl": "sl",
    }
    assert sl["limit_px"] < 95  # worst acceptable fill below the stop
    assert b.positions["BTC"].entry == pytest.approx(100.1)


def test_no_builder_when_disabled_and_skip_invalid_levels():
    b, ex, _ = make(builder=False)
    b.submit(Signal("BTC", Side.LONG, 95, 110, "t", T0), Budget(10, 10_000))
    assert ex.calls[0][2] is None
    msg = b.submit(Signal("BTC", Side.LONG, 101, 110, "t", T0), Budget(10, 10_000))  # stop above mid
    assert msg.startswith("skip") and len(ex.calls) == 1


def test_below_min_notional_skips():
    b, ex, _ = make()
    assert b.submit(Signal("BTC", Side.LONG, 95, 110, "t", T0), Budget(0.01, 10_000)).startswith("skip")
    assert not ex.calls


def test_unfilled_entry_records_nothing():
    b, _, _ = make(fill=False)
    assert "not filled" in b.submit(Signal("BTC", Side.SHORT, 105, 90, "t", T0), Budget(10, 10_000))
    assert not b.positions


def test_reconcile_detects_exchange_close_and_cancels_leftovers():
    b, ex, info = make()
    b.submit(Signal("BTC", Side.LONG, 95, 110, "t", T0), Budget(10, 10_000))
    info.szi = {"BTC": 2.0}
    assert b.process_bar("BTC", bar(1, 100, 101, 99, 100), []) == []
    info.szi = {}
    info.fills = [
        {"coin": "BTC", "closedPnl": "0", "fee": "0.09", "px": "100.1"},
        {"coin": "BTC", "closedPnl": "19.8", "fee": "0.10", "px": "110"},
    ]
    info.orders = [{"coin": "BTC", "oid": 9}]
    (t,) = b.process_bar("BTC", bar(2, 100, 111, 99, 110), [])
    assert t.exit_reason == "take_profit" and t.pnl == pytest.approx(19.61)
    assert ("cancel", "BTC", 9) in ex.calls and not b.positions
    assert b.equity({}) == 1234.5


def test_reconcile_start_blocks_foreign_positions_and_state_roundtrip():
    b, ex, info = make(builder=True)
    info.szi = {"ETH": -1.0}
    notes = b.reconcile_start()
    assert "ETH" in b.open_coins() and any("not opened by this bot" in n for n in notes)
    b.submit(Signal("BTC", Side.LONG, 95, 110, "t", T0), Budget(10, 10_000))
    b2, _, _ = make()
    b2.load_dict(b.to_dict())
    assert b2.positions["BTC"].side is Side.LONG
    b.close_all({}, T0, "kill")
    assert ("close", "BTC", {"b": ADDR, "f": 25}) in ex.calls
    assert b.approve_builder() == {"status": "ok"} and ("approve", ADDR, "0.025%") in ex.calls


def test_builder_config_validation():
    with pytest.raises(ValueError):
        BuilderCfg(enabled=True, address="nope")
    assert BuilderCfg(fee_tenths_bps=10).max_fee_rate_str == "0.01%"
