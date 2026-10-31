"""Live Hyperliquid broker.

Every entry is sent as ONE atomic bulk order (grouping="normalTpsl"):
  1. IOC limit entry (a bounded-slippage market order)
  2. reduce-only take-profit trigger
  3. reduce-only stop-loss trigger
so a position never exists on the exchange without its stop. Reduce-only
means the exits can never flip or enlarge the position.

Optional builder code: when enabled, every order carries {"b": address, "f": fee}
so the builder address earns a per-fill fee (user must approve it once with
`orb approve-builder`).

The SDK objects are injected, so this class is unit-tested against fakes; run
on testnet before mainnet.
"""

from __future__ import annotations

import logging
import math
import uuid
from typing import Any

from orb.broker.base import Broker
from orb.config import BuilderCfg, ExecutionCfg
from orb.models import Candle, Position, Side, Signal, Trade
from orb.risk import Budget, RiskManager

log = logging.getLogger(__name__)
MAX_PERP_DECIMALS = 6


def round_size(sz: float, sz_decimals: int) -> float:
    q = 10**sz_decimals
    return math.floor(sz * q + 1e-9) / q


def round_px(px: float, sz_decimals: int) -> float:
    """Hyperliquid perp price rules: <= 5 significant figures and <= 6 - szDecimals decimals."""
    if px <= 0:
        raise ValueError("price must be positive")
    if px >= 100_000:
        return float(round(px))
    return round(float(f"{px:.5g}"), MAX_PERP_DECIMALS - sz_decimals)


class LiveBroker(Broker):
    def __init__(
        self,
        exchange: Any,
        info: Any,
        account: str,
        sz_decimals: dict[str, int],
        exec_cfg: ExecutionCfg,
        builder_cfg: BuilderCfg,
        min_notional: float = 10.0,
    ):
        super().__init__()
        self.ex = exchange
        self.info = info
        self.account = account
        self.szd = sz_decimals
        self.cfg = exec_cfg
        self.bcfg = builder_cfg
        self.min_notional = min_notional
        self._equity_cache: float | None = None

    @property
    def builder(self) -> dict | None:
        if not self.bcfg.enabled:
            return None
        return {"b": self.bcfg.address.lower(), "f": int(self.bcfg.fee_tenths_bps)}

    # ----------------------------------------------------------------- entry
    def build_orders(self, signal: Signal, mid: float, budget: Budget) -> list[dict] | None:
        coin, d = signal.coin, self.szd[signal.coin]
        is_buy = signal.side is Side.LONG
        valid = signal.stop < mid < signal.take_profit if is_buy else signal.take_profit < mid < signal.stop
        if not valid:
            return None
        size = round_size(RiskManager.size(mid, signal.stop, budget), d)
        if size * mid < self.min_notional:
            return None
        slip = self.cfg.market_slippage
        entry_px = round_px(mid * (1 + slip) if is_buy else mid * (1 - slip), d)
        exit_buy = not is_buy

        def trig(px: float, kind: str) -> dict:
            bound = px * (1 + slip) if exit_buy else px * (1 - slip)  # worst acceptable fill
            return {
                "coin": coin,
                "is_buy": exit_buy,
                "sz": size,
                "limit_px": round_px(bound, d),
                "order_type": {"trigger": {"triggerPx": round_px(px, d), "isMarket": True, "tpsl": kind}},
                "reduce_only": True,
            }

        return [
            {
                "coin": coin,
                "is_buy": is_buy,
                "sz": size,
                "limit_px": entry_px,
                "order_type": {"limit": {"tif": "Ioc"}},
                "reduce_only": False,
            },
            trig(signal.take_profit, "tp"),
            trig(signal.stop, "sl"),
        ]

    def submit(self, signal: Signal, budget: Budget) -> str:
        mid = float(self.info.all_mids()[signal.coin])
        orders = self.build_orders(signal, mid, budget)
        if orders is None:
            return f"skip {signal.coin}: invalid levels or below min notional at mid {mid}"
        try:
            from hyperliquid.utils.types import Cloid

            orders[0]["cloid"] = Cloid.from_str("0x" + uuid.uuid4().hex)
        except Exception:  # pragma: no cover - SDK always present in prod
            pass
        resp = self.ex.bulk_orders(orders, builder=self.builder, grouping="normalTpsl")
        statuses = _statuses(resp)
        first = statuses[0] if statuses else {}
        if "filled" in first:
            f = first["filled"]
            sz, px = float(f["totalSz"]), float(f["avgPx"])
            self.positions[signal.coin] = Position(
                signal.coin,
                signal.side,
                sz,
                px,
                signal.stop,
                signal.take_profit,
                signal.bar_time,
                oid=str(f.get("oid", "")),
            )
            lv = f"stop {signal.stop:.6g}, tp {signal.take_profit:.6g}"
            return f"FILLED {signal.side.value} {sz} {signal.coin} @ {px} ({lv})"
        log.error("entry not filled: %s", resp)
        return f"entry {signal.coin} not filled: {first or resp}"

    # ------------------------------------------------------------- reconcile
    def _exchange_sizes(self) -> dict[str, float]:
        st = self.info.user_state(self.account)
        self._equity_cache = float(st.get("marginSummary", {}).get("accountValue", 0.0))
        out = {}
        for ap in st.get("assetPositions", []):
            p = ap.get("position", {})
            out[p.get("coin")] = float(p.get("szi", 0))
        return out

    def process_bar(self, coin: str, bar: Candle, funding: list[tuple[int, float]]) -> list[Trade]:
        pos = self.positions.get(coin)
        if pos is None:
            return []
        if abs(self._exchange_sizes().get(coin, 0.0)) > 0:
            return []
        # position is gone on the exchange: a TP or SL fired
        fills = [f for f in self.info.user_fills_by_time(self.account, pos.opened_at) or [] if f.get("coin") == coin]
        gross = sum(float(f.get("closedPnl", 0)) for f in fills)
        fees = sum(float(f.get("fee", 0)) for f in fills)
        exit_px = float(fills[-1]["px"]) if fills else bar.c
        reason = "take_profit" if pos.side.sign * (exit_px - pos.entry) > 0 else "stop"
        self._cancel_leftovers(coin)
        del self.positions[coin]
        risk = abs(pos.entry - pos.stop) * pos.size
        pnl = gross - fees
        return [
            Trade(
                coin,
                pos.side.value,
                pos.size,
                pos.entry,
                exit_px,
                pos.opened_at,
                bar.t,
                pnl,
                fees,
                0.0,
                reason,
                r_multiple=pnl / risk if risk > 0 else 0.0,
            )
        ]

    def _cancel_leftovers(self, coin: str) -> None:
        for o in self.info.open_orders(self.account) or []:
            if o.get("coin") == coin:
                try:
                    self.ex.cancel(coin, int(o["oid"]))
                except Exception as e:  # keep going; next reconcile retries
                    log.warning("cancel %s %s failed: %s", coin, o.get("oid"), e)

    def equity(self, marks: dict[str, float]) -> float:
        if self._equity_cache is None:
            self._exchange_sizes()
        return float(self._equity_cache or 0.0)

    def close_all(self, marks: dict[str, float], t: int, reason: str) -> list[Trade]:
        for coin in list(self.positions):
            self.ex.market_close(coin, slippage=self.cfg.market_slippage, builder=self.builder)
            self._cancel_leftovers(coin)
        return []  # realised trades are picked up by the next reconcile

    # ---------------------------------------------------- start-up + state
    def reconcile_start(self) -> list[str]:
        """Compare local state with the exchange. Unknown exchange positions block that coin."""
        notes = []
        sizes = self._exchange_sizes()
        for coin in list(self.positions):
            if abs(sizes.get(coin, 0.0)) == 0:
                notes.append(f"{coin}: local position not on exchange; will reconcile as closed next bar")
        self.foreign = {c for c, s in sizes.items() if abs(s) > 0 and c not in self.positions}
        for c in sorted(self.foreign):
            notes.append(f"{c}: exchange position not opened by this bot; bot will not trade {c}")
        return notes

    def open_coins(self) -> set[str]:
        return set(self.positions) | getattr(self, "foreign", set())

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return {"positions": {c: {**asdict(p), "side": p.side.value} for c, p in self.positions.items()}}

    def load_dict(self, d: dict) -> None:
        self.positions = {c: Position(**{**p, "side": Side(p["side"])}) for c, p in d.get("positions", {}).items()}

    def approve_builder(self) -> Any:
        if not self.bcfg.enabled:
            raise RuntimeError("builder.enabled is false in config")
        return self.ex.approve_builder_fee(self.bcfg.address.lower(), self.bcfg.max_fee_rate_str)


def _statuses(resp: Any) -> list[dict]:
    try:
        if resp.get("status") != "ok":
            return [{"error": resp}]
        return list(resp["response"]["data"]["statuses"])
    except (AttributeError, KeyError, TypeError):
        return [{"error": resp}]
