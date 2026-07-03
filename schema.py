"""
SQLite schema creation for the Kalshi weather bot.
"""

import sqlite3

from config import DB_PATH

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    city            TEXT NOT NULL,
    target_date     DATE NOT NULL,
    bracket_label   TEXT NOT NULL,
    model_prob      REAL NOT NULL,
    bias_correction REAL,
    adj_prob        REAL NOT NULL,
    kalshi_price    INTEGER NOT NULL,
    edge            REAL NOT NULL,
    confidence      REAL,
    lead_hours      INTEGER,
    ensemble_spread REAL,
    action          TEXT,         -- 'BUY_YES', 'BUY_NO', 'NO_TRADE'
    reject_reason   TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    signal_id       INTEGER REFERENCES signals(id),
    city            TEXT NOT NULL,
    target_date     DATE NOT NULL,
    ticker          TEXT NOT NULL,
    side            TEXT NOT NULL,    -- 'yes' or 'no'
    price_cents     INTEGER NOT NULL,
    num_contracts   INTEGER NOT NULL,
    spend_usd       REAL NOT NULL,
    paper_trade     BOOLEAN NOT NULL DEFAULT 1,
    order_id        TEXT,             -- Kalshi order ID if live trade
    status          TEXT DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS outcomes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    trade_id        INTEGER REFERENCES trades(id),
    ticker          TEXT NOT NULL,
    settled_temp_f  REAL,
    winning_bracket TEXT,
    pnl_usd         REAL,
    result          TEXT              -- 'win', 'loss', 'push'
);

CREATE TABLE IF NOT EXISTS bias_corrections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    city            TEXT NOT NULL,
    nws_station     TEXT NOT NULL,
    season          TEXT NOT NULL,    -- 'winter', 'spring', 'summer', 'fall'
    temp_range      TEXT NOT NULL,    -- e.g. '70-80'
    bias_f          REAL NOT NULL,    -- station runs X°F warmer than ECMWF
    std_f           REAL,             -- sample std dev of (observed - forecast)
    p_value         REAL,             -- one-sample t-test vs H0: bias_f = 0
    sample_count    INTEGER NOT NULL,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(city, season, temp_range)
);

CREATE TABLE IF NOT EXISTS daily_summary (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      DATE NOT NULL UNIQUE,
    trades_placed   INTEGER DEFAULT 0,
    total_spend_usd REAL DEFAULT 0.0,
    total_pnl_usd   REAL DEFAULT 0.0,
    win_count       INTEGER DEFAULT 0,
    loss_count      INTEGER DEFAULT 0,
    ending_balance  REAL
);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate_bias_corrections_columns(conn: sqlite3.Connection) -> None:
    """Add std_f/p_value columns to bias_corrections if missing (pre-existing DBs)."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(bias_corrections)")}
    for column in ("std_f", "p_value"):
        if column not in existing:
            conn.execute(f"ALTER TABLE bias_corrections ADD COLUMN {column} REAL")


def init_db() -> None:
    """Create all tables if they don't already exist."""
    conn = get_connection()
    try:
        conn.executescript(CREATE_TABLES)
        _migrate_bias_corrections_columns(conn)
        conn.commit()
    finally:
        conn.close()
