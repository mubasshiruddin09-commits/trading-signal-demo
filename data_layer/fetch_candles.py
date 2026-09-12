"""
Layer 1 - Data Layer
Fetches OHLCV candle data from Binance's public REST API (no API key required
for market data). Stores to a local CSV for now; swap for a real DB later.
"""
import requests
import pandas as pd
from datetime import datetime

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

INTERVAL_MAP = {
    "1d": "1d",
    "4h": "4h",
    "1h": "1h",
}


def fetch_candles(symbol: str = "BTCUSDT", interval: str = "1d", limit: int = 500) -> pd.DataFrame:
    """
    Fetch OHLCV candles from Binance public API.

    symbol: e.g. "BTCUSDT"
    interval: "1d" or "4h" (matches the swing-trade timeframe requirement)
    limit: number of candles to pull (Binance max per request is 1000)
    """
    params = {
        "symbol": symbol,
        "interval": INTERVAL_MAP.get(interval, interval),
        "limit": limit,
    }
    resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=15)
    resp.raise_for_status()
    raw = resp.json()

    cols = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "num_trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ]
    df = pd.DataFrame(raw, columns=cols)

    # Types
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    df["symbol"] = symbol
    df["timeframe"] = interval

    return df[["symbol", "timeframe", "timestamp", "open", "high", "low", "close", "volume"]]


if __name__ == "__main__":
    df = fetch_candles("BTCUSDT", "1d", limit=500)
    print(f"Fetched {len(df)} candles for BTCUSDT 1D")
    print(df.tail())
    df.to_csv("data_layer/btcusdt_1d.csv", index=False)
    print("Saved to data_layer/btcusdt_1d.csv")
