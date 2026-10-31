import json

from orb.cli import main
from orb.data import HyperliquidData

from .conftest import wave


def _seed_cache(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "market:\n  coins: [TEST]\n  interval: 1h\n"
        "strategy:\n  name: ema_retest\n  params: {ema_len: 50, touch_atr: 1.0}\n"
        f"storage:\n  data_dir: {tmp_path}/data\n  state_file: {tmp_path}/state/state.json\n"
        f"  log_file: {tmp_path}/logs/x.log\n"
    )
    d = HyperliquidData(cache_dir=tmp_path / "data")
    d.cache_dir.mkdir(parents=True)
    with d._cpath("TEST", "1h").open("w") as f:
        f.write("t,o,h,l,c,v\n")
        for c in wave(1500):
            f.write(f"{c.t},{c.o},{c.h},{c.l},{c.c},{c.v}\n")
    return cfg


def test_cli_backtest_offline_writes_report_and_json(tmp_path, capsys):
    cfg = _seed_cache(tmp_path)
    rpt, js = tmp_path / "r.html", tmp_path / "r.json"
    assert main(["-c", str(cfg), "backtest", "--offline", "--report", str(rpt), "--json", str(js)]) == 0
    out = capsys.readouterr().out
    assert "total_return" in out and rpt.read_text().startswith("<!doctype html>")
    assert json.loads(js.read_text())["stats"]["trades"] > 0


def test_cli_sweep_and_status(tmp_path, capsys):
    cfg = _seed_cache(tmp_path)
    assert main(["-c", str(cfg), "sweep", "--offline", "--grid", "touch_atr=0.5,1.0"]) == 0
    assert "out-of-sample" in capsys.readouterr().out
    assert main(["-c", str(cfg), "status"]) == 0
    assert "no state yet" in capsys.readouterr().out


def test_cli_live_requires_confirmation(tmp_path):
    cfg = _seed_cache(tmp_path)
    import pytest

    with pytest.raises(SystemExit, match="confirm-live"):
        main(["-c", str(cfg), "live"])
    with pytest.raises(SystemExit, match="confirm-live"):
        main(["-c", str(cfg), "approve-builder"])
