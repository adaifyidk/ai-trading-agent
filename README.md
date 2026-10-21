# AI Trading Agent — Starter Prototype

This starter is deliberately **paper/backtest only**. It does not submit live orders.

## What it does
- Reads historical OHLCV bars from a CSV.
- Calculates VWAP and 9/21 EMAs.
- Generates long-only intraday signals.
- Applies a fixed fractional-risk position size, stop, target, max trades/day, and daily loss lockout.
- Writes a trade journal CSV and a summary.
- Includes an optional Alpaca paper-account connectivity check (no order submission).

## Important limitations
- A backtest is not proof of future profitability.
- This simplified engine uses bar data and conservative assumptions; it is not suitable for live trading as-is.
- Paper fills differ from live execution due to latency, slippage, liquidity, queue position, and data-feed differences.
- Do not put API keys in source code or share them. Use paper-only keys.

## Setup
Python 3.11+ recommended.

```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
```

## Prepare data
Create `data/bars.csv` with columns:
`timestamp,open,high,low,close,volume`

Use 5-minute bars, timestamps in ISO format, ideally in US/Eastern. The backtester groups bars by calendar date in the timestamp's own timezone/representation. Include regular-session bars only for the first test.

## Run
```bash
python main.py --csv data/bars.csv --symbol AAPL
```

The program writes:
- `output/trades.csv`
- `output/equity_curve.csv`
- `output/summary.json`

## Strategy (starter hypothesis, not a recommendation)
- Long entry: 9 EMA > 21 EMA, close > session VWAP, and volume > 1.2x the rolling 20-bar average.
- Entry is assumed at the next bar's open after a signal to reduce look-ahead bias.
- Initial stop: 1.0 ATR below entry.
- Target: 1.5R.
- No new positions after 15:00 US/Eastern; close any open position on the final bar.
- One position at a time.
- Risk per trade: 0.25% of starting equity.
- Daily stop: stop initiating trades after realized P&L reaches -1% of starting equity.
- Maximum 3 entries per day.
- Trading costs/slippage are configurable and should be calibrated before interpreting results.

This is a toy baseline. Test across many symbols and periods, include out-of-sample data, and compare to a simple benchmark before considering automation.

## Alpaca paper account
1. Create an Alpaca Paper Only account and generate **paper** API keys.
2. Copy `.env.example` to `.env` and fill in paper credentials.
3. Install requirements and run:
   `python paper_account_check.py`

The check only reads account status and buying power. It does not place orders.
Do not switch this starter to live trading without independent review, additional safeguards, and extended paper testing.
