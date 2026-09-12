"""
Layer 2 - Analysis Engine (Core)
Codified, reproducible rules only - no hand-drawn judgment calls, so every
result here can be backtested exactly.
"""
import pandas as pd
import numpy as np


# ---- Swing structure detector (N-bar fractal pivots) ----
def detect_swing_points(df: pd.DataFrame, lookback: int = 3) -> pd.DataFrame:
    """
    Identify HH/HL/LH/LL using a fixed N-bar fractal pivot rule.
    A high pivot: high[i] is the max in the window [i-lookback, i+lookback].
    A low pivot: low[i] is the min in the same window.
    lookback is a config value - documented here, version-controlled in real use.
    """
    df = df.copy()
    df["pivot_high"] = df["high"] == df["high"].rolling(
        window=2 * lookback + 1, center=True
    ).max()
    df["pivot_low"] = df["low"] == df["low"].rolling(
        window=2 * lookback + 1, center=True
    ).min()
    return df


# ---- RSI (standard calculation, used as a filter, not a standalone trigger) ----
def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


# ---- ATR-based range/consolidation detector ----
def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ---- Volume profile module (POC/HVN/LVN per rolling window) ----
def compute_volume_profile(df: pd.DataFrame, window: int = 50, bins: int = 24) -> dict:
    """
    Computes Point of Control (POC - price level with most volume), High
    Volume Nodes (HVN), and Low Volume Nodes (LVN) over the most recent
    `window` candles. Fixed bin count so results are reproducible per spec.
    """
    recent = df.tail(window)
    if recent.empty or recent["volume"].sum() == 0:
        return {"poc": None, "hvn_levels": [], "lvn_levels": []}

    price_min = recent["low"].min()
    price_max = recent["high"].max()
    if price_max <= price_min:
        return {"poc": None, "hvn_levels": [], "lvn_levels": []}

    bin_edges = np.linspace(price_min, price_max, bins + 1)
    bin_volumes = np.zeros(bins)

    for _, row in recent.iterrows():
        # distribute each candle's volume across the bins it overlaps (simple, reproducible)
        low, high, vol = row["low"], row["high"], row["volume"]
        overlap_bins = np.where((bin_edges[:-1] < high) & (bin_edges[1:] > low))[0]
        if len(overlap_bins) > 0:
            bin_volumes[overlap_bins] += vol / len(overlap_bins)

    # Smooth with a simple moving average to reduce artificial spikes from
    # wide-range candles (evenly-spread volume can create false peaks) -
    # per review feedback, this softens noise without changing the method.
    smooth_window = 3
    kernel = np.ones(smooth_window) / smooth_window
    bin_volumes = np.convolve(bin_volumes, kernel, mode="same")

    poc_idx = int(np.argmax(bin_volumes))
    poc_price = (bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2

    mean_vol = bin_volumes.mean()
    std_vol = bin_volumes.std()
    hvn_idx = np.where(bin_volumes > mean_vol + 0.5 * std_vol)[0]
    lvn_idx = np.where(bin_volumes < mean_vol - 0.5 * std_vol)[0]

    hvn_levels = [round((bin_edges[i] + bin_edges[i + 1]) / 2, 2) for i in hvn_idx]
    lvn_levels = [round((bin_edges[i] + bin_edges[i + 1]) / 2, 2) for i in lvn_idx]

    return {
        "poc": round(poc_price, 2),
        "hvn_levels": hvn_levels[:5],
        "lvn_levels": lvn_levels[:5],
    }


# ---- Trendline module (auto-fit via pivot-based linear regression) ----
def fit_trendlines(df: pd.DataFrame, lookback: int = 3, min_pivots: int = 3) -> dict:
    """
    Auto-fits trendlines through recent swing highs and swing lows using
    linear regression on the pivot points - never hand-drawn, so the same
    input always produces the same trendline, per spec.
    """
    scored = detect_swing_points(df, lookback=lookback)

    highs = scored[scored["pivot_high"] == True].tail(min_pivots + 2)
    lows = scored[scored["pivot_low"] == True].tail(min_pivots + 2)

    result = {"resistance": None, "support": None}

    if len(highs) >= min_pivots:
        x = np.arange(len(highs))
        y = highs["high"].values
        slope, intercept = np.polyfit(x, y, 1)
        result["resistance"] = {
            "slope": round(float(slope), 4),
            "intercept": round(float(intercept), 2),
            "direction": "descending" if slope < 0 else "ascending",
            "current_level": round(float(slope * (len(highs) - 1) + intercept), 2),
        }

    if len(lows) >= min_pivots:
        x = np.arange(len(lows))
        y = lows["low"].values
        slope, intercept = np.polyfit(x, y, 1)
        result["support"] = {
            "slope": round(float(slope), 4),
            "intercept": round(float(intercept), 2),
            "direction": "ascending" if slope > 0 else "descending",
            "current_level": round(float(slope * (len(lows) - 1) + intercept), 2),
        }

    return result


# ---- Confluence scorer ----
def confluence_score(df: pd.DataFrame, rsi_period: int = 14, swing_lookback: int = 3) -> pd.DataFrame:
    """
    Combines swing structure + RSI + ATR regime into a weighted score with a
    visible breakdown. Never returns a bare buy/sell verdict - always the
    components that fired, per the spec.
    """
    df = detect_swing_points(df, lookback=swing_lookback)
    df["rsi"] = compute_rsi(df, period=rsi_period)
    df["atr"] = compute_atr(df, period=rsi_period)

    components = []
    for i, row in df.iterrows():
        breakdown = {}
        score = 0

        # Component 1: RSI filter (not standalone trigger, contributes to score)
        if pd.notna(row["rsi"]):
            if row["rsi"] < 30:
                breakdown["rsi_oversold"] = True
                score += 1
            elif row["rsi"] > 70:
                breakdown["rsi_overbought"] = True
                score -= 1

        # Component 2: recent swing structure
        if row.get("pivot_low"):
            breakdown["at_swing_low"] = True
            score += 1
        if row.get("pivot_high"):
            breakdown["at_swing_high"] = True
            score -= 1

        components.append({"score": score, "breakdown": breakdown})

    df["confluence_score"] = [c["score"] for c in components]
    df["component_breakdown"] = [c["breakdown"] for c in components]
    return df


if __name__ == "__main__":
    # Quick smoke test with synthetic data (real run uses data_layer output)
    import numpy as np
    dates = pd.date_range("2024-01-01", periods=100, freq="D")
    np.random.seed(42)
    price = 40000 + np.cumsum(np.random.randn(100) * 200)
    df = pd.DataFrame({
        "timestamp": dates,
        "open": price,
        "high": price + np.random.rand(100) * 100,
        "low": price - np.random.rand(100) * 100,
        "close": price + np.random.randn(100) * 50,
        "volume": np.random.rand(100) * 1000,
    })
    result = confluence_score(df)
    print(result[["timestamp", "close", "rsi", "confluence_score"]].tail(10))
