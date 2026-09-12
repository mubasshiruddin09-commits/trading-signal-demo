# Trading Signal Demo (Phase 0-3 of full architecture)

Minimal working demo of the confluence-based signal engine: Data Layer →
Analysis Engine → Backtest Gate → Telegram Alert. No auto-execution, no
multi-user platform — this proves the core logic works before building
the rest.

## What this does
1. Pulls real BTC/USDT daily candles from Binance's free public API
2. Runs a codified confluence scorer (RSI + swing structure)
3. Backtests it with an in-sample/out-of-sample split — a signal only
   fires if it clears a pre-defined win rate on unseen data (the "gate"
   from the spec, Section 5)
4. If it passes, sends a Telegram alert with the score breakdown

## Setup

```bash
# 1. Clone and enter the repo
git clone <your-repo-url>
cd trading-demo

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional but recommended) Set up Telegram alerts
cp .env.example .env
# Edit .env with your bot token and chat ID (see signals/telegram_alert.py
# for the 5-minute setup steps)

# 4. Run the demo
python run_demo.py
```

Without a `.env` file, alerts print to console instead of Telegram (dry run) —
so you can test the whole pipeline before setting up the bot.

## Project structure

```
data_layer/       - Binance candle fetching (Layer 1)
analysis_engine/  - Swing structure, RSI, confluence scoring (Layer 2)
backtest/         - In-sample/out-of-sample validation gate (Layer 3)
signals/          - Telegram alert formatting + sending (Layer 4)
run_demo.py        - Ties it all together
```

## What's NOT in this demo (by design)
- Auto-execution (Layer 6) — too much regulatory/security surface for a demo
- Multi-user platform/billing (Layer 9) — not needed to prove the concept
- Full 3-5yr backtest history — using last 500 daily candles for now

## Config (edit in run_demo.py before running real backtests)
- `SCORE_THRESHOLD` - confluence score needed to trigger a signal
- `MIN_WIN_RATE` - decided *before* seeing results, not tuned after
- `MIN_SAMPLE_SIZE` - minimum trades before trusting a backtest result

## Web dashboard

A live dashboard lives in `webapp/` — dark trading-terminal UI showing the
confluence score, component breakdown, and the backtest gate result, all
pulled from real Binance data via the API endpoints below.

Run locally:
```bash
python webapp/app.py
```
Then open http://localhost:5000

Endpoints:
- `/` — the dashboard page
- `/api/signal` — latest confluence score + component breakdown (JSON)
- `/api/backtest` — in-sample/out-of-sample gate result (JSON)

## Deploying free (for a live demo)

**Render.com (recommended, easiest free URL):**
1. Push this repo to GitHub
2. Go to render.com → New → Web Service → connect your repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn --pythonpath . webapp.app:app`
5. Deploy — you'll get a free URL like `your-app.onrender.com`

**Railway.app (alternative):**
1. Push to GitHub, then railway.app → New Project → Deploy from GitHub
2. It auto-detects the `Procfile` and deploys
3. Free tier gives you a `your-app.up.railway.app` URL

Both platforms' free tiers can sleep after inactivity — the first request
after idle may take 10-30 seconds to wake up. That's normal, not a bug.

SQLite is fine for demo scale; swap for TimescaleDB per the original
spec once this goes to production.
