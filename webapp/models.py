"""
User accounts + paper trading (demo money only - never real funds).
SQLite for simplicity; swap for Postgres if this scales up.
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.db")

STARTING_BALANCE = 1000.0


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            picture TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS demo_accounts (
            user_id TEXT PRIMARY KEY,
            balance REAL NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS demo_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_price REAL NOT NULL,
            take_profit REAL NOT NULL,
            units REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            exit_price REAL,
            pnl REAL,
            opened_at TEXT NOT NULL,
            closed_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    conn.close()


def get_or_create_user(user_id: str, email: str, name: str, picture: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO users (id, email, name, picture, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, email, name, picture, datetime.utcnow().isoformat()),
        )
        conn.execute(
            "INSERT INTO demo_accounts (user_id, balance) VALUES (?, ?)",
            (user_id, STARTING_BALANCE),
        )
        conn.commit()
    conn.close()


def get_demo_balance(user_id: str) -> float:
    conn = get_db()
    row = conn.execute("SELECT balance FROM demo_accounts WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row["balance"] if row else STARTING_BALANCE


def open_trade(user_id: str, symbol: str, direction: str, entry: float, stop: float, take_profit: float, units: float):
    conn = get_db()
    conn.execute("""
        INSERT INTO demo_trades (user_id, symbol, direction, entry_price, stop_price, take_profit, units, opened_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, symbol, direction, entry, stop, take_profit, units, datetime.utcnow().isoformat()))
    conn.commit()
    trade_id = conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]
    conn.close()
    return trade_id


def close_trade(user_id: str, trade_id: int, exit_price: float):
    conn = get_db()
    trade = conn.execute(
        "SELECT * FROM demo_trades WHERE id = ? AND user_id = ? AND status = 'open'",
        (trade_id, user_id),
    ).fetchone()
    if not trade:
        conn.close()
        return None

    if trade["direction"] == "long":
        pnl = (exit_price - trade["entry_price"]) * trade["units"]
    else:
        pnl = (trade["entry_price"] - exit_price) * trade["units"]

    conn.execute("""
        UPDATE demo_trades SET status = 'closed', exit_price = ?, pnl = ?, closed_at = ?
        WHERE id = ?
    """, (exit_price, pnl, datetime.utcnow().isoformat(), trade_id))

    conn.execute("""
        UPDATE demo_accounts SET balance = balance + ? WHERE user_id = ?
    """, (pnl, user_id))

    conn.commit()
    conn.close()
    return pnl


def get_open_trades(user_id: str):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM demo_trades WHERE user_id = ? AND status = 'open' ORDER BY opened_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_trade_history(user_id: str, limit: int = 20):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM demo_trades WHERE user_id = ? AND status = 'closed' ORDER BY closed_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
