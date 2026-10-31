# Proposal template (Hyperliquid / crypto trading bot jobs)

Edit the bracketed parts. Keep it under ~150 words; clients skim.

---

Hi [name],

I've already built a working Hyperliquid perps bot that covers most of what you listed: [API data + funding / risk management / backtest + paper + live / Telegram alerts].

Repo + report: [GitHub link]

A few things that are usually missing from bot code, and are in mine:
- Backtest, paper and live run through the same engine, and a test proves paper reproduces the backtest trade for trade.
- No look-ahead: signals fill at the next bar's open; fees, slippage and real hourly funding are charged.
- Live entries go out with their stop and target in one atomic order, reduce-only, so a crash never leaves a naked position.
- Risk-based sizing, daily loss limit and a drawdown kill switch.

For your project I would [plug in your strategy / add X], deliver it on testnet first, and hand over docs + a 20-minute walkthrough.

Fixed price: $[X] for [scope], [N] days.

[your name]

---

## Pricing guide (from current Upwork listings, Oct 2026)

| Job | Typical budget | Your angle |
|---|---|---|
| "Simple Hyperliquid bot with API data" | $100–300 | This repo as-is, configured to their coins/strategy |
| Bot with risk mgmt, backtest, paper/live, config file | $1,000–1,500 | Exactly this repo + their strategy |
| Hourly bot work | $15–80/hr | Bid fixed-price instead; show the repo |
| Telegram alert/trading bot | $275–400 packages | Paper mode + Telegram already built |

## Before you send

- Don't promise profits. You sell reliable machinery; the strategy and its results are theirs.
- Deliver on testnet first; never ask for a client's main private key (API wallet only).
- Scope in writing: which strategy, which coins, what "done" means.
