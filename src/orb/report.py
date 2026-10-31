"""Self-contained HTML backtest report (no external assets, works offline, light/dark)."""

from __future__ import annotations

import html
import json
import math
from datetime import datetime, timezone

from orb.backtest import BacktestReport
from orb.metrics import drawdown_series


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def _pct(x: float) -> str:
    return f"{x * 100:+.2f}%"


def _num(x: float, nd: int = 2) -> str:
    return "∞" if math.isinf(x) else f"{x:.{nd}f}"


def _paths(values: list[float], w: int, h: int, pad: int, lo: float, hi: float) -> str:
    if len(values) < 2:
        return ""
    span = hi - lo or 1.0
    step = (w - 2 * pad) / (len(values) - 1)
    pts = [f"{pad + i * step:.1f},{pad + (h - 2 * pad) * (1 - (v - lo) / span):.1f}" for i, v in enumerate(values)]
    return "M" + " L".join(pts)


def _thin(values: list, n: int = 1200) -> list:
    if len(values) <= n:
        return values
    k = len(values) / n
    return [values[int(i * k)] for i in range(n)] + [values[-1]]


def render(rep: BacktestReport, strategy_name: str, interval: str, source: str = "Hyperliquid public API") -> str:
    s = rep.stats
    full_eq = [p.equity for p in rep.result.equity]
    full_dd = drawdown_series(full_eq)
    eq, dd = _thin(full_eq), _thin(full_dd)  # thin for drawing only; labels use full series
    w, h, pad = 960, 260, 8
    lo, hi = (min(full_eq), max(full_eq)) if full_eq else (0, 1)
    eq_path = _paths(eq, w, h, pad, lo, hi)
    base_y = pad + (h - 2 * pad) * (1 - (rep.result.starting_equity - lo) / ((hi - lo) or 1))
    dd_h = 110
    dd_max = max(full_dd) if full_dd else 0
    dd_path = _paths([-x for x in dd], w, dd_h, pad, -(dd_max or 0.01), 0)
    dd_area = f"{dd_path} L{w - pad},{pad} L{pad},{pad} Z" if dd_path else ""

    cards = [
        ("Total return", _pct(s["total_return"]), "pos" if s["total_return"] >= 0 else "neg"),
        ("Buy & hold", _pct(rep.benchmark_return), ""),
        ("Max drawdown", f"{s['max_drawdown'] * 100:.2f}%", "neg"),
        ("Sharpe (ann.)", _num(s["sharpe"]), ""),
        ("Trades", str(s["trades"]), ""),
        ("Win rate", f"{s['win_rate'] * 100:.1f}%", ""),
        ("Profit factor", _num(s["profit_factor"]), ""),
        ("Avg R", f"{s['avg_r']:+.2f}", ""),
        ("Fees paid", f"${s['fees']:.2f}", ""),
        ("Funding paid", f"${s['funding']:.2f}", ""),
        ("Time in market", f"{s['time_in_market'] * 100:.1f}%", ""),
        ("End equity", f"${s['end_equity']:,.2f}", ""),
    ]
    card_html = "".join(
        f'<div class="card"><div class="k">{k}</div><div class="v {cls}">{v}</div></div>' for k, v, cls in cards
    )
    rows = []
    for t in rep.result.trades[-300:][::-1]:
        cls = "pos" if t.pnl > 0 else "neg"
        rows.append(
            f"<tr><td>{_d(t.opened_at)}</td><td>{t.coin}</td><td>{t.side}</td><td>{t.entry:.6g}</td>"
            f"<td>{t.exit:.6g}</td><td>{html.escape(t.exit_reason)}</td>"
            f'<td class="{cls}">{t.pnl:+.2f}</td><td class="{cls}">{t.r_multiple:+.2f}</td></tr>'
        )
    params = html.escape(json.dumps(rep.params, indent=2))
    title = f"{strategy_name} · {', '.join(rep.coins)} · {interval}"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backtest report</title>
<style>
:root{{--bg:#f7f7f5;--panel:#fff;--ink:#16181d;--mute:#6b7280;--line:#e5e7eb;--acc:#2563eb;--pos:#0f8a5f;--neg:#c2410c;--dd:rgba(194,65,12,.18)}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0e1013;--panel:#161a20;--ink:#e8eaed;--mute:#9aa3ad;--line:#262c35;--acc:#60a5fa;--pos:#34d399;--neg:#fb923c;--dd:rgba(251,146,60,.2)}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1040px;margin:0 auto;padding:28px 16px 48px}}
h1{{font-size:22px;margin:0 0 4px;letter-spacing:-.01em}}.sub{{color:var(--mute);margin:0 0 22px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:18px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.k{{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.04em}}.v{{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums;margin-top:2px}}
.pos{{color:var(--pos)}}.neg{{color:var(--neg)}}
section{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 14px 10px;margin-bottom:18px}}
h2{{font-size:14px;margin:0 0 8px}}svg{{width:100%;height:auto;display:block}}
table{{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:13px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}}th{{color:var(--mute);font-weight:500}}
.scroll{{overflow-x:auto;max-height:520px}}pre{{margin:0;font-size:12px;color:var(--mute);white-space:pre-wrap}}
.note{{color:var(--mute);font-size:12px}}
</style></head><body><main>
<h1>Backtest report</h1>
<p class="sub">{html.escape(title)} · {_d(rep.window[0])} → {_d(rep.window[1])} UTC · {s["days"]:.0f} days · data: {html.escape(source)}</p>
<div class="grid">{card_html}</div>
<section><h2>Equity</h2>
<svg viewBox="0 0 {w} {h}" role="img" aria-label="Equity curve">
<line x1="{pad}" x2="{w - pad}" y1="{base_y:.1f}" y2="{base_y:.1f}" stroke="var(--line)" stroke-dasharray="4 4"/>
<path d="{eq_path}" fill="none" stroke="var(--acc)" stroke-width="1.8"/></svg>
<div class="note">Dashed line = starting equity ${rep.result.starting_equity:,.0f}. Range ${lo:,.2f} – ${hi:,.2f}.</div></section>
<section><h2>Drawdown</h2>
<svg viewBox="0 0 {w} {dd_h}" role="img" aria-label="Drawdown"><path d="{dd_area}" fill="var(--dd)" stroke="var(--neg)" stroke-width="1"/></svg>
<div class="note">Deepest: {dd_max * 100:.2f}%</div></section>
<section><h2>Trades (newest first)</h2><div class="scroll"><table>
<thead><tr><th>Opened (UTC)</th><th>Coin</th><th>Side</th><th>Entry</th><th>Exit</th><th>Exit reason</th><th>PnL $</th><th>R</th></tr></thead>
<tbody>{"".join(rows) or '<tr><td colspan="8">No trades</td></tr>'}</tbody></table></div></section>
<section><h2>Strategy parameters</h2><pre>{params}</pre></section>
<p class="note">Fill model: signals fill at the next bar's open with slippage; stop assumed before target when both are hit in one bar;
taker fees on both sides; hourly funding applied. A backtest is evidence about the past, not a forecast. Not financial advice.</p>
</main></body></html>"""
