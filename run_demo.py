"""
Demo entry point - runs the full pipeline end-to-end:
Data Layer -> Analysis Engine -> Backtest Gate -> (if passed) Signal Alert

Usage:
    python run_demo.py
"""
from data_layer.fetch_candles import fetch_candles
from analysis_engine.confluence import confluence_score
from backtest.backtest_engine import run_backtest, walk_forward_split
from signals.telegram_alert import send_alert, format_signal_message

# --- Config (fix these before running backtests, per spec Section 4) ---
SYMBOL = "BTCUSDT"
TIMEFRAME = "1d"
SCORE_THRESHOLD = 1          # confluence score needed to trigger a signal
MIN_WIN_RATE = 0.55          # decided BEFORE seeing results, per spec Section 5
MIN_SAMPLE_SIZE = 30         # don't trust a backtest with too few trades


def main():
    print(f"Step 1: Fetching {SYMBOL} {TIMEFRAME} candles...")
    df = fetch_candles(SYMBOL, TIMEFRAME, limit=500)
    print(f"  Got {len(df)} candles")

    print("\nStep 2: Running backtest gate (in-sample vs out-of-sample)...")
    in_sample, out_sample = walk_forward_split(df)
    is_result = run_backtest(in_sample, score_threshold=SCORE_THRESHOLD)
    oos_result = run_backtest(out_sample, score_threshold=SCORE_THRESHOLD)
    print(f"  In-sample:     {is_result}")
    print(f"  Out-of-sample: {oos_result}")

    oos_win_rate = oos_result.get("win_rate", 0) or 0
    oos_sample = oos_result.get("sample_size", 0)

    if oos_sample < MIN_SAMPLE_SIZE:
        print(f"\n  GATE FAILED: only {oos_sample} out-of-sample trades (need {MIN_SAMPLE_SIZE}+). Not enough data to trust this.")
        return
    if oos_win_rate < MIN_WIN_RATE:
        print(f"\n  GATE FAILED: out-of-sample win rate {oos_win_rate} is below threshold {MIN_WIN_RATE}. Config needs redesign.")
        return

    print(f"\n  GATE PASSED: {oos_win_rate} win rate on {oos_sample} out-of-sample trades.")

    print("\nStep 3: Scoring latest candle and sending signal...")
    scored = confluence_score(df)
    latest = scored.iloc[-1]
    if latest["confluence_score"] >= SCORE_THRESHOLD:
        msg = format_signal_message(SYMBOL, TIMEFRAME, latest["confluence_score"], latest["component_breakdown"])
        send_alert(msg)
    else:
        print(f"  Latest score {latest['confluence_score']} below threshold {SCORE_THRESHOLD} - no signal today.")


if __name__ == "__main__":
    main()
