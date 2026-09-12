"""
Web dashboard for the trading signal demo.
Serves live confluence + backtest data honestly - no inflated numbers,
per the spec's own rule against bare accuracy claims (Section 12).
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, render_template, redirect, url_for, session, request
from authlib.integrations.flask_client import OAuth
from data_layer.fetch_candles import fetch_candles
from data_layer.news_feed import fetch_news
from analysis_engine.confluence import confluence_score, compute_volume_profile, fit_trendlines
from backtest.backtest_engine import run_backtest, walk_forward_split
from webapp import models

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-in-production")
app.config["PREFERRED_URL_SCHEME"] = "https"

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

models.init_db()

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def current_user():
    return session.get("user")


def login_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            return jsonify({"ok": False, "error": "Not logged in"}), 401
        return f(*args, **kwargs)
    return wrapper


@app.route("/login")
def login():
    redirect_uri = url_for("auth_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/auth/callback")
def auth_callback():
    token = google.authorize_access_token()
    userinfo = token.get("userinfo")
    if not userinfo:
        return redirect(url_for("dashboard"))

    user_id = userinfo["sub"]
    email = userinfo.get("email", "")
    name = userinfo.get("name", "")
    picture = userinfo.get("picture", "")

    models.get_or_create_user(user_id, email, name, picture)

    session["user"] = {"id": user_id, "email": email, "name": name, "picture": picture}
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("dashboard"))

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
DEFAULT_SYMBOL = "BTCUSDT"
TIMEFRAMES = ["1d", "4h", "1h"]
DEFAULT_TIMEFRAME = "1d"
SCORE_THRESHOLD = 1
MIN_WIN_RATE = 0.55
MIN_SAMPLE_SIZE = 30

# Client-directed strategy update: fewer, more selective trades targeting
# 1:10 risk/reward instead of frequent 2:1 trades. Score=2 requires BOTH
# RSI extreme AND swing structure to align (max possible score is +-2).
STRATEGY_SCORE_THRESHOLD = 2
STRATEGY_RR_MULTIPLE = 10.0
STRATEGY_MAX_HOLD_BARS = 60
MIN_SAMPLE_SIZE_STRATEGY = 10


@app.route("/")
def dashboard():
    user = current_user()
    demo_balance = models.get_demo_balance(user["id"]) if user else None
    return render_template(
        "dashboard.html",
        symbols=SYMBOLS,
        default_symbol=DEFAULT_SYMBOL,
        timeframes=TIMEFRAMES,
        default_timeframe=DEFAULT_TIMEFRAME,
        user=user,
        demo_balance=demo_balance,
    )


@app.route("/api/signal")
def api_signal():
    symbol = request.args.get("symbol", DEFAULT_SYMBOL)
    timeframe = request.args.get("timeframe", DEFAULT_TIMEFRAME)
    if symbol not in SYMBOLS:
        return jsonify({"ok": False, "error": "Unsupported symbol"}), 400
    if timeframe not in TIMEFRAMES:
        return jsonify({"ok": False, "error": "Unsupported timeframe"}), 400
    try:
        df = fetch_candles(symbol, timeframe, limit=1000)
        scored = confluence_score(df)

        # Determine if the most recent candle has actually closed yet.
        from datetime import datetime, timezone, timedelta
        tf_minutes = {"1h": 60, "4h": 240, "1d": 1440}.get(timeframe, 1440)
        very_latest = scored.iloc[-1]
        candle_close_time = very_latest["timestamp"] + timedelta(minutes=tf_minutes)
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        latest_is_confirmed = bool(now_utc >= candle_close_time)

        # The OFFICIAL buy/sell call always comes from the last CLOSED candle,
        # never the live/forming one - this is what makes the signal genuinely
        # non-repainting: once posted, it cannot change, because it's locked to
        # historical data that is already final. The still-forming candle is
        # shown separately, clearly labeled, for context only.
        latest = scored.iloc[-1] if latest_is_confirmed else scored.iloc[-2]
        is_confirmed = True  # by construction, "latest" here is always a closed candle
        forming_price = round(float(very_latest["close"]), 2) if not latest_is_confirmed else None

        vol_profile = compute_volume_profile(df, window=50)
        trendlines = fit_trendlines(df)

        recent = scored.tail(60)[["timestamp", "open", "high", "low", "close", "volume", "rsi", "confluence_score", "pivot_high", "pivot_low"]].copy()
        recent["timestamp"] = recent["timestamp"].astype(str)
        recent["pivot_high"] = recent["pivot_high"].fillna(False).astype(bool)
        recent["pivot_low"] = recent["pivot_low"].fillna(False).astype(bool)

        # Label swing points as HH/HL/LH/LL by comparing each pivot to the prior
        # pivot of the same type (higher high, lower high, higher low, lower low).
        swing_labels = []
        last_high = None
        last_low = None
        for _, r in recent.iterrows():
            label = None
            if r["pivot_high"]:
                if last_high is not None:
                    label = "HH" if r["high"] > last_high else "LH"
                last_high = r["high"]
            elif r["pivot_low"]:
                if last_low is not None:
                    label = "HL" if r["low"] > last_low else "LL"
                last_low = r["low"]
            swing_labels.append(label)
        recent["swing_label"] = swing_labels
        recent_list = recent.drop(columns=["pivot_high", "pivot_low"]).to_dict(orient="records")

        return jsonify({
            "ok": True,
            "symbol": symbol,
            "timeframe": timeframe,
            "latest_price": round(float(latest["close"]), 2),
            "latest_rsi": round(float(latest["rsi"]), 2) if pd_notna(latest["rsi"]) else None,
            "latest_score": int(latest["confluence_score"]),
            "breakdown": latest["component_breakdown"],
            "threshold": SCORE_THRESHOLD,
            "is_confirmed": is_confirmed,
            "forming_price": forming_price,
            "signal_locked": True,
            "signal_active": bool(latest["confluence_score"] >= SCORE_THRESHOLD),
            "history": recent_list,
            "volume_profile": vol_profile,
            "trendlines": trendlines,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/demo/account")
@login_required
def api_demo_account():
    user = current_user()
    balance = models.get_demo_balance(user["id"])
    open_trades = models.get_open_trades(user["id"])
    history = models.get_trade_history(user["id"])
    return jsonify({"ok": True, "balance": round(balance, 2), "open_trades": open_trades, "history": history})


@app.route("/api/demo/open-trade", methods=["POST"])
@login_required
def api_demo_open_trade():
    user = current_user()
    data = request.get_json()
    try:
        symbol = data.get("symbol")
        direction = data.get("direction")
        entry = float(data.get("entry"))
        stop = float(data.get("stop"))
        take_profit = float(data.get("take_profit"))
        units = float(data.get("units"))

        if symbol not in SYMBOLS or direction not in ("long", "short"):
            return jsonify({"ok": False, "error": "Invalid symbol or direction"}), 400
        if entry <= 0 or stop <= 0 or units <= 0:
            return jsonify({"ok": False, "error": "Invalid trade parameters"}), 400

        trade_id = models.open_trade(user["id"], symbol, direction, entry, stop, take_profit, units)
        return jsonify({"ok": True, "trade_id": trade_id})
    except (ValueError, TypeError, KeyError) as e:
        return jsonify({"ok": False, "error": f"Invalid input: {e}"}), 400


@app.route("/api/demo/close-trade", methods=["POST"])
@login_required
def api_demo_close_trade():
    user = current_user()
    data = request.get_json()
    try:
        trade_id = int(data.get("trade_id"))
        exit_price = float(data.get("exit_price"))
        pnl = models.close_trade(user["id"], trade_id, exit_price)
        if pnl is None:
            return jsonify({"ok": False, "error": "Trade not found or already closed"}), 404
        new_balance = models.get_demo_balance(user["id"])
        return jsonify({"ok": True, "pnl": round(pnl, 2), "new_balance": round(new_balance, 2)})
    except (ValueError, TypeError, KeyError) as e:
        return jsonify({"ok": False, "error": f"Invalid input: {e}"}), 400


@app.route("/api/scanner")
def api_scanner():
    results = []
    for symbol in SYMBOLS:
        try:
            df = fetch_candles(symbol, TIMEFRAMES[0], limit=200)
            scored = confluence_score(df)
            latest = scored.iloc[-1]
            results.append({
                "symbol": symbol,
                "price": round(float(latest["close"]), 2),
                "score": int(latest["confluence_score"]),
                "rsi": round(float(latest["rsi"]), 2) if pd_notna(latest["rsi"]) else None,
                "breakdown": latest["component_breakdown"],
            })
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e)})

    results.sort(key=lambda r: r.get("score", -99), reverse=True)
    return jsonify({"ok": True, "results": results})


@app.route("/api/chart-assistant", methods=["POST"])
def api_chart_assistant():
    data = request.get_json()
    symbol = data.get("symbol", DEFAULT_SYMBOL)
    timeframe = data.get("timeframe", DEFAULT_TIMEFRAME)
    balance = data.get("balance")
    risk_pct = data.get("risk_pct", 1.0)

    if symbol not in SYMBOLS or timeframe not in TIMEFRAMES:
        return jsonify({"ok": False, "error": "Unsupported symbol or timeframe"}), 400

    try:
        df = fetch_candles(symbol, timeframe, limit=1000)
        scored = confluence_score(df)
        latest = scored.iloc[-1]
        vol_profile = compute_volume_profile(df, window=50)
        trendlines = fit_trendlines(df)

        price = round(float(latest["close"]), 2)
        score = int(latest["confluence_score"])
        breakdown = latest["component_breakdown"]
        rsi = round(float(latest["rsi"]), 2) if pd_notna(latest["rsi"]) else None

        summary_lines = []
        summary_lines.append(f"{symbol} is trading at ${price:,.2f} on the {timeframe} chart.")

        if score >= SCORE_THRESHOLD:
            summary_lines.append(f"Confluence score is +{score} - components fired: {', '.join(breakdown.keys()) if breakdown else 'none'}.")
        elif score < 0:
            summary_lines.append(f"Confluence score is {score} - bearish components fired: {', '.join(breakdown.keys()) if breakdown else 'none'}.")
        else:
            summary_lines.append("Confluence score is neutral (0) - no strong signal components fired right now.")

        if rsi is not None:
            if rsi < 30:
                summary_lines.append(f"RSI at {rsi} is in oversold territory.")
            elif rsi > 70:
                summary_lines.append(f"RSI at {rsi} is in overbought territory.")
            else:
                summary_lines.append(f"RSI at {rsi} is in neutral range.")

        if vol_profile.get("poc"):
            summary_lines.append(f"Volume point of control (POC) is near ${vol_profile['poc']:,.2f} over the last 50 candles.")

        if trendlines.get("resistance"):
            r = trendlines["resistance"]
            summary_lines.append(f"Resistance trendline is {r['direction']}, currently near ${r['current_level']:,.2f}.")
        if trendlines.get("support"):
            s = trendlines["support"]
            summary_lines.append(f"Support trendline is {s['direction']}, currently near ${s['current_level']:,.2f}.")

        position_sizing = None
        if balance:
            try:
                balance = float(balance)
                risk_pct = float(risk_pct)
                atr_estimate = df["high"].tail(14).max() - df["low"].tail(14).min()
                stop_distance = atr_estimate * 0.3  # rough estimate for the assistant's summary only
                max_dollar_risk = balance * (risk_pct / 100)
                units = max_dollar_risk / stop_distance if stop_distance > 0 else 0
                position_sizing = {
                    "max_dollar_risk": round(max_dollar_risk, 2),
                    "estimated_units": round(units, 6),
                    "note": "Rough estimate for discussion only - use the Risk Sizer panel with a real stop price for an exact position size.",
                }
                summary_lines.append(f"With ${balance:,.0f} balance at {risk_pct}% risk, max dollar risk is ${max_dollar_risk:,.2f}.")
            except (ValueError, TypeError):
                pass

        summary_lines.append("This is analysis only, not financial advice - always confirm with your own risk rules before trading.")

        news = fetch_news(symbol, limit=3)
        if news:
            summary_lines.append(f"Recent news that could affect this setup: \"{news[0]['title']}\"")

        return jsonify({
            "ok": True,
            "symbol": symbol,
            "timeframe": timeframe,
            "price": price,
            "confluence_score": score,
            "breakdown": breakdown,
            "rsi": rsi,
            "volume_profile": vol_profile,
            "trendlines": trendlines,
            "position_sizing": position_sizing,
            "news": news,
            "summary": summary_lines,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/position-size", methods=["POST"])
def api_position_size():
    from flask import request
    try:
        data = request.get_json()
        balance = float(data.get("balance", 0))
        risk_pct = float(data.get("risk_pct", 1.0))
        entry = float(data.get("entry", 0))
        stop = float(data.get("stop", 0))

        if balance <= 0 or entry <= 0 or stop <= 0 or entry == stop:
            return jsonify({"ok": False, "error": "Enter valid balance, entry, and stop values"}), 400

        max_dollar_risk = balance * (risk_pct / 100)
        risk_per_unit = abs(entry - stop)
        position_units = max_dollar_risk / risk_per_unit
        notional_value = position_units * entry

        is_long = stop < entry
        rr_multiple = 10.0
        if is_long:
            take_profit = entry + (risk_per_unit * rr_multiple)
        else:
            take_profit = entry - (risk_per_unit * rr_multiple)

        return jsonify({
            "ok": True,
            "direction": "long" if is_long else "short",
            "max_dollar_risk": round(max_dollar_risk, 2),
            "position_units": round(position_units, 6),
            "notional_value": round(notional_value, 2),
            "take_profit": round(take_profit, 2),
            "risk_reward_multiple": rr_multiple,
            "note": "Calculator only - no order is placed. Position sizing math per Layer 6 spec (1% max risk rule, 1:10 R:R target per updated strategy direction).",
        })
    except (ValueError, TypeError) as e:
        return jsonify({"ok": False, "error": f"Invalid input: {e}"}), 400


@app.route("/api/backtest/export")
def api_backtest_export():
    import csv
    import io
    from flask import Response

    symbol = request.args.get("symbol", DEFAULT_SYMBOL)
    timeframe = request.args.get("timeframe", DEFAULT_TIMEFRAME)
    if symbol not in SYMBOLS or timeframe not in TIMEFRAMES:
        return jsonify({"ok": False, "error": "Unsupported symbol or timeframe"}), 400

    try:
        df = fetch_candles(symbol, timeframe, limit=1000)
        in_sample, out_sample = walk_forward_split(df)
        is_result = run_backtest(in_sample, score_threshold=STRATEGY_SCORE_THRESHOLD, rr_multiple=STRATEGY_RR_MULTIPLE, max_hold_bars=STRATEGY_MAX_HOLD_BARS)
        oos_result = run_backtest(out_sample, score_threshold=STRATEGY_SCORE_THRESHOLD, rr_multiple=STRATEGY_RR_MULTIPLE, max_hold_bars=STRATEGY_MAX_HOLD_BARS)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Confluence Signal Engine - Backtest Export"])
        writer.writerow(["Symbol", symbol, "Timeframe", timeframe])
        writer.writerow(["Strategy", "Score >= 2, ATR stop, 1:10 R:R, fees 0.15% modeled"])
        writer.writerow([])
        writer.writerow(["Metric", "In-Sample", "Out-of-Sample"])
        for key in ["sample_size", "win_rate", "avg_win_pct", "avg_loss_pct", "avg_rr", "max_drawdown", "expectancy_pct", "target_hits", "stop_hits", "time_stops"]:
            writer.writerow([key, is_result.get(key, ""), oos_result.get(key, "")])

        csv_data = output.getvalue()
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={symbol}_{timeframe}_backtest.csv"},
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/preset/highest-expectancy")
def api_preset_highest_expectancy():
    symbol = request.args.get("symbol", DEFAULT_SYMBOL)
    if symbol not in SYMBOLS:
        return jsonify({"ok": False, "error": "Unsupported symbol"}), 400

    best = None
    for tf in TIMEFRAMES:
        try:
            df = fetch_candles(symbol, tf, limit=1000)
            _, out_sample = walk_forward_split(df)
            result = run_backtest(out_sample, score_threshold=STRATEGY_SCORE_THRESHOLD, rr_multiple=STRATEGY_RR_MULTIPLE, max_hold_bars=STRATEGY_MAX_HOLD_BARS)
            exp = result.get("expectancy_pct")
            sample = result.get("sample_size", 0)
            if exp is not None and sample > 0:
                if best is None or exp > best["expectancy_pct"]:
                    best = {"timeframe": tf, "expectancy_pct": exp, "sample_size": sample}
        except Exception:
            continue

    if best is None:
        return jsonify({"ok": False, "error": "No valid setups found across timeframes"}), 404

    return jsonify({"ok": True, "symbol": symbol, "best": best})


@app.route("/api/backtest")
def api_backtest():
    symbol = request.args.get("symbol", DEFAULT_SYMBOL)
    timeframe = request.args.get("timeframe", DEFAULT_TIMEFRAME)
    score_threshold = request.args.get("score_threshold", STRATEGY_SCORE_THRESHOLD, type=int)
    if symbol not in SYMBOLS:
        return jsonify({"ok": False, "error": "Unsupported symbol"}), 400
    if timeframe not in TIMEFRAMES:
        return jsonify({"ok": False, "error": "Unsupported timeframe"}), 400
    try:
        df = fetch_candles(symbol, timeframe, limit=1000)
        in_sample, out_sample = walk_forward_split(df)
        # High-selectivity strategy: score_threshold=2 requires BOTH RSI extreme
        # AND swing structure to align (not just one) - fewer, higher-conviction
        # trades. rr_multiple=10 and a longer hold window give the trade room
        # to reach a 1:10 target rather than a quick 2:1 flip.
        is_result = run_backtest(in_sample, score_threshold=score_threshold, rr_multiple=STRATEGY_RR_MULTIPLE, max_hold_bars=STRATEGY_MAX_HOLD_BARS)
        oos_result = run_backtest(out_sample, score_threshold=score_threshold, rr_multiple=STRATEGY_RR_MULTIPLE, max_hold_bars=STRATEGY_MAX_HOLD_BARS)

        oos_sample = oos_result.get("sample_size", 0)
        oos_expectancy = oos_result.get("expectancy_pct", None)

        # For a 1:10 strategy, expectancy (not win rate) is the real pass/fail
        # measure - a low win rate can still be strongly profitable here.
        gate_passed = bool(oos_sample >= MIN_SAMPLE_SIZE_STRATEGY and oos_expectancy is not None and oos_expectancy > 0)
        gate_reason = None
        if oos_sample < MIN_SAMPLE_SIZE_STRATEGY:
            gate_reason = f"Sample too small ({oos_sample} trades, need {MIN_SAMPLE_SIZE_STRATEGY}+) - this is a low-frequency, high-selectivity strategy by design"
        elif oos_expectancy is None:
            gate_reason = "No trades triggered in out-of-sample window"
        elif oos_expectancy <= 0:
            gate_reason = f"Expectancy is negative ({oos_expectancy:.2%} per trade) - strategy does not have a statistical edge yet"

        return jsonify({
            "ok": True,
            "strategy": {
                "name": "High-selectivity 1:10 risk/reward",
                "entry_rule": f"Confluence score >= {score_threshold} ({'RSI extreme AND swing structure aligned' if score_threshold >= 2 else 'RSI extreme OR swing structure touch'})",
                "exit_rule": f"ATR-based stop, target = {STRATEGY_RR_MULTIPLE}x risk",
                "max_hold_bars": STRATEGY_MAX_HOLD_BARS,
            },
            "in_sample": clean_nans(is_result),
            "out_of_sample": clean_nans(oos_result),
            "gate_passed": gate_passed,
            "gate_reason": gate_reason,
            "min_sample_size": MIN_SAMPLE_SIZE_STRATEGY,
            "note": "This strategy targets a 1:10 risk/reward ratio with highly selective entries - fewer trades, but each win is sized to significantly outweigh a string of losses. Pass/fail is based on expectancy (average $ result per trade), not raw win rate, since a 1:10 strategy can be profitable even below a 20% win rate.",
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def pd_notna(val):
    import pandas as pd
    return pd.notna(val)


def clean_nans(d):
    import math
    import numpy as np
    out = {}
    for k, v in d.items():
        if isinstance(v, (np.bool_, bool)):
            out[k] = bool(v)
        elif isinstance(v, (np.integer,)):
            out[k] = int(v)
        elif isinstance(v, (np.floating, float)):
            out[k] = None if math.isnan(v) else float(v)
        else:
            out[k] = v
    return out


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
