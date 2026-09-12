"""
Layer 3 - Backtesting Engine (Hard Gate)
Nothing reaches a live alert without passing through here first.
Reports win rate, avg R:R, max drawdown, and sample size - never a single
headline accuracy number, per the spec.
"""
import pandas as pd
import numpy as np
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis_engine.confluence import confluence_score


def compute_expectancy(win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> float:
    """
    Expectancy = (Win% x Avg Win) - (Loss% x Avg Loss).
    For a 1:10 R:R strategy, this is the right measure - not win rate alone.
    A strategy can win as little as ~15-20% of the time and still be
    strongly profitable if the payout per win is large enough.
    """
    loss_rate = 1 - win_rate
    return (win_rate * avg_win_pct) - (loss_rate * abs(avg_loss_pct))


def run_backtest(df: pd.DataFrame, score_threshold: int = 1, atr_stop_mult: float = 1.5, rr_multiple: float = 2.0, max_hold_bars: int = 20, fee_pct: float = 0.0015) -> dict:
    """
    Real stop/target backtest with fee/slippage modeling: enter when
    confluence_score >= threshold, stop = entry - (ATR * atr_stop_mult),
    target = entry + (risk * rr_multiple). Walks forward bar-by-bar checking
    which is hit first. fee_pct models combined entry+exit taker fees and
    slippage (default 0.15%) so results reflect realistic net returns,
    not gross backtest numbers.
    """
    df = confluence_score(df).reset_index(drop=True)
    trades = []

    for i in range(len(df) - 1):
        row = df.iloc[i]
        if row["confluence_score"] < score_threshold:
            continue
        if pd.isna(row["atr"]) or row["atr"] <= 0:
            continue

        entry_price = row["close"]
        risk = row["atr"] * atr_stop_mult
        stop_price = entry_price - risk
        target_price = entry_price + (risk * rr_multiple)

        outcome = None
        exit_price = entry_price
        bars_held = 0

        for j in range(i + 1, min(i + 1 + max_hold_bars, len(df))):
            bar = df.iloc[j]
            bars_held = j - i
            # Conservative: if both stop and target are within the same bar's range,
            # assume stop hit first (worst-case assumption, not optimistic).
            hit_stop = bar["low"] <= stop_price
            hit_target = bar["high"] >= target_price
            if hit_stop and hit_target:
                outcome = "stop"
                exit_price = stop_price
                break
            elif hit_stop:
                outcome = "stop"
                exit_price = stop_price
                break
            elif hit_target:
                outcome = "target"
                exit_price = target_price
                break

        if outcome is None:
            # time-stop: exit at close of the last bar in the hold window
            last_idx = min(i + max_hold_bars, len(df) - 1)
            exit_price = df.iloc[last_idx]["close"]
            outcome = "time_stop"

        pnl_pct = (exit_price - entry_price) / entry_price
        pnl_pct_net = pnl_pct - fee_pct  # combined entry+exit fees/slippage
        trades.append({
            "entry_time": row["timestamp"],
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "exit_price": exit_price,
            "outcome": outcome,
            "bars_held": bars_held,
            "pnl_pct": pnl_pct_net,
            "pnl_pct_gross": pnl_pct,
            "win": pnl_pct_net > 0,
        })

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return {"sample_size": 0, "note": "No trades triggered at this threshold - lower threshold or check data"}

    win_rate = trades_df["win"].mean()
    avg_win = trades_df.loc[trades_df["win"], "pnl_pct"].mean() if trades_df["win"].any() else 0
    avg_loss = trades_df.loc[~trades_df["win"], "pnl_pct"].mean() if (~trades_df["win"]).any() else 0
    avg_rr = abs(avg_win / avg_loss) if avg_loss != 0 else float("nan")

    cumulative = (1 + trades_df["pnl_pct"]).cumprod()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    max_drawdown = drawdown.min()

    equity_curve = [round(float(v), 4) for v in cumulative.tolist()]

    target_hits = (trades_df["outcome"] == "target").sum()
    stop_hits = (trades_df["outcome"] == "stop").sum()
    time_stops = (trades_df["outcome"] == "time_stop").sum()

    expectancy = compute_expectancy(win_rate, avg_win, avg_loss)

    return {
        "sample_size": len(trades_df),
        "win_rate": round(win_rate, 4),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "avg_rr": round(avg_rr, 2) if not np.isnan(avg_rr) else None,
        "max_drawdown": round(max_drawdown, 4),
        "target_hits": int(target_hits),
        "stop_hits": int(stop_hits),
        "time_stops": int(time_stops),
        "expectancy_pct": round(expectancy, 4),
        "fee_pct_applied": fee_pct,
        "equity_curve": equity_curve,
    }


def walk_forward_split(df: pd.DataFrame, split_ratio: float = 0.7):
    """Split data into in-sample (tune) and out-of-sample (test) - never
    trust in-sample results alone, per the spec's gate requirement."""
    split_idx = int(len(df) * split_ratio)
    return df.iloc[:split_idx].reset_index(drop=True), df.iloc[split_idx:].reset_index(drop=True)


if __name__ == "__main__":
    # Smoke test with synthetic data - replace with data_layer output for real run
    dates = pd.date_range("2022-01-01", periods=500, freq="D")
    np.random.seed(7)
    price = 40000 + np.cumsum(np.random.randn(500) * 200)
    df = pd.DataFrame({
        "timestamp": dates,
        "open": price,
        "high": price + np.random.rand(500) * 100,
        "low": price - np.random.rand(500) * 100,
        "close": price + np.random.randn(500) * 50,
        "volume": np.random.rand(500) * 1000,
    })

    in_sample, out_sample = walk_forward_split(df)

    print("=== IN-SAMPLE ===")
    print(run_backtest(in_sample))
    print("\n=== OUT-OF-SAMPLE ===")
    print(run_backtest(out_sample))
    print("\nGATE CHECK: compare in-sample vs out-of-sample win rate.")
    print("If out-of-sample collapses relative to in-sample, this config is overfit")
    print("and must go back to Layer 2 - per Section 5 of the spec.")
