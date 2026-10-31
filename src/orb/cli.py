"""Command line: orb {fetch,backtest,sweep,paper,live,approve-builder,status}."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path

from orb import __version__
from orb.backtest import parse_grid, run_backtest, sweep
from orb.config import Config, load_config, load_dotenv
from orb.data import HyperliquidData
from orb.engine import Engine
from orb.notify import Notifier
from orb.report import render
from orb.risk import RiskManager
from orb.state import StateStore
from orb.strategies import build_strategy

log = logging.getLogger("orb")


def setup_logging(cfg: Config, verbose: bool) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    lf = Path(cfg.storage.log_file)
    lf.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(lf, maxBytes=5_000_000, backupCount=3)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _data(cfg: Config) -> HyperliquidData:
    return HyperliquidData(cfg.api_url, cfg.storage.data_dir)


def _market(cfg: Config, days: int, offline: bool):
    d = _data(cfg)
    out = {}
    for coin in cfg.market.coins:
        if offline:
            out[coin] = d.load_cached(coin, cfg.market.interval)
        else:
            out[coin] = d.sync(coin, cfg.market.interval, days)
        log.info("%s: %d candles, %d funding prints", coin, len(out[coin][0]), len(out[coin][1]))
    return out


# --------------------------------------------------------------- commands
def cmd_fetch(cfg: Config, a) -> int:
    _market(cfg, a.days, offline=False)
    print(f"cached in {cfg.storage.data_dir}/")
    return 0


def cmd_backtest(cfg: Config, a) -> int:
    market = _market(cfg, a.days, a.offline)
    rep = run_backtest(cfg, market)
    s = rep.stats
    print(f"\n{cfg.strategy.name} on {', '.join(rep.coins)} {cfg.market.interval}, {s['days']:.0f} days")
    for k in ["total_return", "max_drawdown", "win_rate", "time_in_market"]:
        print(f"  {k:<16} {s[k] * 100:8.2f}%")
    for k in ["sharpe", "profit_factor", "avg_r", "expectancy", "fees", "funding"]:
        print(f"  {k:<16} {s[k]:8.2f}")
    print(f"  {'trades':<16} {s['trades']:8d}")
    print(f"  {'buy_and_hold':<16} {rep.benchmark_return * 100:8.2f}%")
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(render(rep, cfg.strategy.name, cfg.market.interval))
        print(f"\nreport: {a.report}")
    if a.json:
        Path(a.json).write_text(
            json.dumps(
                {
                    "stats": s,
                    "benchmark": rep.benchmark_return,
                    "params": rep.params,
                    "trades": [t.to_dict() for t in rep.result.trades],
                },
                indent=2,
                default=str,
            )
        )
        print(f"json:   {a.json}")
    return 0


def cmd_sweep(cfg: Config, a) -> int:
    market = _market(cfg, a.days, a.offline)
    grid = parse_grid(a.grid)
    rows = sweep(cfg, market, grid, a.split)
    keys = list(grid)
    head = keys + ["is_ret%", "is_shp", "is_n", "oos_ret%", "oos_shp", "oos_n", "oos_dd%"]
    print("  ".join(f"{h:>9}" for h in head))
    for r in rows:
        vals = [str(r[k]) for k in keys] + [
            f"{r['is_return'] * 100:.1f}",
            f"{r['is_sharpe']:.2f}",
            str(r["is_trades"]),
            f"{r['oos_return'] * 100:.1f}",
            f"{r['oos_sharpe']:.2f}",
            str(r["oos_trades"]),
            f"{r['oos_max_dd'] * 100:.1f}",
        ]
        print("  ".join(f"{v:>9}" for v in vals))
    best = rows[0]
    print(
        f"\nIn-sample best: {{{', '.join(f'{k}={best[k]}' for k in keys)}}} -> out-of-sample "
        f"{best['oos_return'] * 100:+.1f}% (Sharpe {best['oos_sharpe']:.2f}). "
        "If OOS is much worse than IS, the edge is probably curve-fit."
    )
    return 0


def _engine(cfg: Config, broker, notifier: Notifier) -> Engine:
    return Engine(build_strategy(cfg.strategy.name, cfg.strategy.params), broker, RiskManager(cfg.risk))


def cmd_paper(cfg: Config, a) -> int:
    from orb.broker.paper import PaperBroker
    from orb.runner import Runner

    notifier = Notifier(cfg.notifications.telegram, prefix="orb paper")
    broker = PaperBroker(cfg.execution, cfg.risk.starting_equity)
    Runner(cfg, _data(cfg), _engine(cfg, broker, notifier), StateStore(cfg.storage.state_file), notifier, "paper").run(
        once=a.once
    )
    return 0


def _live_broker(cfg: Config):
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info

    from orb.broker.live import LiveBroker

    key, account = os.getenv("HL_PRIVATE_KEY", ""), os.getenv("HL_ACCOUNT_ADDRESS", "")
    if not key or not account:
        raise SystemExit("HL_PRIVATE_KEY and HL_ACCOUNT_ADDRESS must be set (see .env.example)")
    wallet = Account.from_key(key)
    info = Info(cfg.api_url, skip_ws=True)
    ex = Exchange(wallet, cfg.api_url, account_address=account)
    return LiveBroker(ex, info, account, _data(cfg).size_decimals(), cfg.execution, cfg.builder)


def cmd_live(cfg: Config, a) -> int:
    from orb.runner import Runner

    if not a.confirm_live:
        raise SystemExit("live trading places real orders. Re-run with --confirm-live to proceed.")
    broker = _live_broker(cfg)
    for note in broker.reconcile_start():
        log.warning(note)
    log.warning("LIVE on %s in 5 seconds (Ctrl-C to abort)...", cfg.network)
    time.sleep(5)
    notifier = Notifier(cfg.notifications.telegram, prefix=f"orb LIVE {cfg.network}")
    store = StateStore(cfg.storage.state_file.replace(".json", f".live-{cfg.network}.json"))
    Runner(cfg, _data(cfg), _engine(cfg, broker, notifier), store, notifier, "live").run(once=a.once)
    return 0


def cmd_approve_builder(cfg: Config, a) -> int:
    if not a.confirm_live:
        raise SystemExit("this signs an on-chain approval. Re-run with --confirm-live.")
    print(json.dumps(_live_broker(cfg).approve_builder(), indent=2))
    return 0


def cmd_selftest(cfg: Config, a) -> int:
    """Prove the live order path end to end with a ~$12 trade that is closed immediately."""
    if not a.confirm_live:
        raise SystemExit("selftest places a small real order. Re-run with --confirm-live.")
    if cfg.network != "testnet" and not a.allow_mainnet:
        raise SystemExit("selftest runs on testnet. Set network: testnet, or pass --allow-mainnet.")
    rows = _live_broker(cfg).self_test(a.coin)
    print(f"\nself-test on {cfg.network}, {a.coin}")
    for step, ok, detail in rows:
        print(f"  {'PASS' if ok else 'FAIL'}  {step:<30} {detail}")
    passed = all(ok for _, ok, _ in rows)
    print("\nALL CHECKS PASSED" if passed else "\nSELF-TEST FAILED")
    return 0 if passed else 1


def cmd_status(cfg: Config, a) -> int:
    paths = [Path(cfg.storage.state_file)] + sorted(Path(cfg.storage.state_file).parent.glob("*.live-*.json"))
    found = False
    for p in paths:
        if p.exists():
            found = True
            st = json.loads(p.read_text())
            print(f"== {p} ({st.get('mode')})")
            print(f"equity ${st.get('equity', 0):,.2f}  halted={st.get('risk', {}).get('halted')}")
            print(f"positions: {list(st.get('broker', {}).get('positions', {}))}")
            for ev in st.get("recent_events", [])[-10:]:
                print("  " + ev)
    if not found:
        print("no state yet")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orb", description="Risk-managed Hyperliquid perps bot")
    p.add_argument("--version", action="version", version=f"orb {__version__}")
    p.add_argument("-c", "--config", default="config.yaml", help="YAML config (default: config.yaml)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download candles + funding into the cache")
    f.add_argument("--days", type=int, default=800)

    for name, helptext in [
        ("backtest", "run a backtest"),
        ("sweep", "in-sample / out-of-sample parameter sweep"),
    ]:
        b = sub.add_parser(name, help=helptext)
        b.add_argument("--days", type=int, default=800)
        b.add_argument("--offline", action="store_true", help="use cached data only")
        if name == "backtest":
            b.add_argument("--report", default="reports/backtest.html")
            b.add_argument("--json", default=None)
        else:
            b.add_argument("--grid", action="append", required=True, help="name=v1,v2 (repeatable)")
            b.add_argument("--split", type=float, default=0.7)

    pp = sub.add_parser("paper", help="paper-trade live market data")
    pp.add_argument("--once", action="store_true", help="start, process new bars once, exit")

    lv = sub.add_parser("live", help="trade real money")
    lv.add_argument("--confirm-live", action="store_true")
    lv.add_argument("--once", action="store_true")

    ab = sub.add_parser("approve-builder", help="approve the configured builder fee for your account")
    ab.add_argument("--confirm-live", action="store_true")

    st = sub.add_parser("selftest", help="place and close a ~$12 bracketed order to prove the live path (testnet)")
    st.add_argument("--confirm-live", action="store_true")
    st.add_argument("--allow-mainnet", action="store_true")
    st.add_argument("--coin", default="BTC")

    sub.add_parser("status", help="show saved bot state")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    load_dotenv()
    cfg = load_config(a.config if Path(a.config).exists() else None)
    if not Path(a.config).exists():
        print(f"note: {a.config} not found, using defaults (see config.example.yaml)", file=sys.stderr)
    setup_logging(cfg, a.verbose)
    cmds = {
        "fetch": cmd_fetch,
        "backtest": cmd_backtest,
        "sweep": cmd_sweep,
        "paper": cmd_paper,
        "live": cmd_live,
        "approve-builder": cmd_approve_builder,
        "selftest": cmd_selftest,
        "status": cmd_status,
    }
    return cmds[a.cmd](cfg, a)


if __name__ == "__main__":
    raise SystemExit(main())
