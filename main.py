import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd


def load_bars(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns.str.lower())
    if missing:
        raise ValueError(f"CSV missing columns: {sorted(missing)}")
    df.columns = [c.lower().strip() for c in df.columns]
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="raise")
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="raise")
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive.")
    if (df["volume"] < 0).any():
        raise ValueError("Volume cannot be negative.")
    return df


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    # Session VWAP resets each calendar date represented in the input.
    x["session"] = x["timestamp"].dt.date
    typical = (x["high"] + x["low"] + x["close"]) / 3.0
    x["pv"] = typical * x["volume"]
    x["vwap"] = x.groupby("session")["pv"].cumsum() / x.groupby("session")["volume"].cumsum().replace(0, np.nan)
    x["ema9"] = x["close"].ewm(span=9, adjust=False).mean()
    x["ema21"] = x["close"].ewm(span=21, adjust=False).mean()
    prev_close = x["close"].shift(1)
    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev_close).abs(),
        (x["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    x["atr14"] = tr.rolling(14).mean()
    x["vol_avg20"] = x["volume"].rolling(20).mean()
    x["signal"] = (
        (x["ema9"] > x["ema21"]) &
        (x["close"] > x["vwap"]) &
        (x["volume"] > 1.2 * x["vol_avg20"]) &
        x["atr14"].notna()
    )
    return x


def backtest(df: pd.DataFrame, symbol: str, starting_equity: float = 10_000.0,
             risk_fraction: float = 0.0025, daily_loss_fraction: float = 0.01,
             max_trades_per_day: int = 3, fee_per_share: float = 0.005,
             slippage_bps: float = 5.0) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    x = prepare(df)
    equity = starting_equity
    trades = []
    curve = []
    current_day = None
    day_start_equity = equity
    daily_realized = 0.0
    trades_today = 0
    position = None
    last_i = len(x) - 1

    def slipped(price, side):
        # side='buy' pays up; side='sell' receives less
        bump = price * slippage_bps / 10_000.0
        return price + bump if side == "buy" else price - bump

    for i, row in x.iterrows():
        day = row["session"]
        if day != current_day:
            # Force-close at prior session's last close before resetting daily controls.
            if position is not None:
                prev = x.iloc[i - 1]
                exit_price = slipped(float(prev["close"]), "sell")
                qty = position["qty"]
                pnl = (exit_price - position["entry"]) * qty - fee_per_share * qty * 2
                equity += pnl
                daily_realized += pnl
                trades.append({**position, "exit_time": prev["timestamp"], "exit_price": exit_price,
                               "exit_reason": "session_end", "pnl": pnl, "equity_after": equity})
                position = None
            current_day = day
            day_start_equity = equity
            daily_realized = 0.0
            trades_today = 0

        if position is not None:
            # Conservative bar rule: if stop and target are both touched in one bar, assume stop first.
            stop_hit = float(row["low"]) <= position["stop"]
            target_hit = float(row["high"]) >= position["target"]
            reason = None
            raw_exit = None
            if stop_hit:
                reason, raw_exit = "stop", position["stop"]
            elif target_hit:
                reason, raw_exit = "target", position["target"]
            elif i == last_i or (i + 1 < len(x) and x.iloc[i + 1]["session"] != day):
                reason, raw_exit = "session_end", float(row["close"])
            if reason:
                exit_price = slipped(float(raw_exit), "sell")
                qty = position["qty"]
                pnl = (exit_price - position["entry"]) * qty - fee_per_share * qty * 2
                equity += pnl
                daily_realized += pnl
                trades.append({**position, "exit_time": row["timestamp"], "exit_price": exit_price,
                               "exit_reason": reason, "pnl": pnl, "equity_after": equity})
                position = None

        # Entry is on next bar open after current bar's signal (avoids same-bar look-ahead).
        if position is None and i < last_i and x.iloc[i + 1]["session"] == day:
            t = row["timestamp"]
            time_text = t.strftime("%H:%M")
            next_open = float(x.iloc[i + 1]["open"])
            next_atr = float(row["atr14"]) if pd.notna(row["atr14"]) else np.nan
            daily_locked = daily_realized <= -daily_loss_fraction * day_start_equity
            if (bool(row["signal"]) and not daily_locked and trades_today < max_trades_per_day
                    and time_text < "15:00" and np.isfinite(next_atr) and next_atr > 0):
                entry = slipped(next_open, "buy")
                stop = entry - next_atr
                risk_per_share = entry - stop
                risk_budget = day_start_equity * risk_fraction
                qty_by_risk = int(risk_budget / risk_per_share)
                qty_by_cash = int(equity / entry)  # no leverage in starter
                qty = max(0, min(qty_by_risk, qty_by_cash))
                if qty > 0:
                    position = {
                        "symbol": symbol,
                        "entry_time": x.iloc[i + 1]["timestamp"],
                        "signal_time": t,
                        "entry": entry,
                        "qty": qty,
                        "stop": stop,
                        "target": entry + 1.5 * risk_per_share,
                        "risk_budget": risk_budget,
                    }
                    trades_today += 1
        curve.append({"timestamp": row["timestamp"], "equity": equity,
                      "in_position": position is not None, "daily_realized_pnl": daily_realized})

    trades_df = pd.DataFrame(trades)
    curve_df = pd.DataFrame(curve)
    if not trades_df.empty:
        wins = int((trades_df["pnl"] > 0).sum())
        total = len(trades_df)
        gross_profit = float(trades_df.loc[trades_df["pnl"] > 0, "pnl"].sum())
        gross_loss = float(-trades_df.loc[trades_df["pnl"] < 0, "pnl"].sum())
        win_rate = wins / total
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
        pnl_total = float(trades_df["pnl"].sum())
    else:
        wins = total = 0
        win_rate = 0.0
        profit_factor = None
        pnl_total = 0.0
    eq = curve_df["equity"] if not curve_df.empty else pd.Series([starting_equity])
    peak = eq.cummax()
    drawdown = (eq - peak) / peak
    summary = {
        "symbol": symbol,
        "starting_equity": starting_equity,
        "ending_equity": round(starting_equity + pnl_total, 2),
        "net_pnl": round(pnl_total, 2),
        "closed_trades": total,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "max_drawdown_pct": round(float(drawdown.min()) * 100, 2),
        "risk_fraction_per_trade": risk_fraction,
        "daily_loss_limit_fraction": daily_loss_fraction,
        "max_trades_per_day": max_trades_per_day,
        "fee_per_share_each_side": fee_per_share,
        "slippage_bps_each_side": slippage_bps,
        "note": "Simulation only; results depend on data quality and assumptions. Not a profitability guarantee."
    }
    return trades_df, curve_df, summary


def main():
    parser = argparse.ArgumentParser(description="Paper/backtest-only AI trading agent starter")
    parser.add_argument("--csv", required=True, help="CSV containing timestamp,open,high,low,close,volume")
    parser.add_argument("--symbol", default="UNKNOWN")
    parser.add_argument("--equity", type=float, default=10_000)
    parser.add_argument("--risk", type=float, default=0.0025)
    parser.add_argument("--daily-loss", type=float, default=0.01)
    parser.add_argument("--max-trades", type=int, default=3)
    parser.add_argument("--fee-per-share", type=float, default=0.005)
    parser.add_argument("--slippage-bps", type=float, default=5)
    args = parser.parse_args()

    df = load_bars(args.csv)
    if len(df) < 50:
        raise SystemExit("Need at least 50 bars for warm-up and a meaningful initial test.")
    trades, curve, summary = backtest(
        df, args.symbol, args.equity, args.risk, args.daily_loss,
        args.max_trades, args.fee_per_share, args.slippage_bps
    )
    os.makedirs("output", exist_ok=True)
    trades.to_csv("output/trades.csv", index=False)
    curve.to_csv("output/equity_curve.csv", index=False)
    with open("output/summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    print(json.dumps(summary, indent=2, default=str))
    print("\nWrote output/trades.csv, output/equity_curve.csv, output/summary.json")


if __name__ == "__main__":
    main()
