"""
All database read/write functions for the Kalshi weather bot.
"""

from datetime import date

from config import STARTING_BANKROLL_USD
from database.schema import get_connection
from weather.bias import temp_range_bucket


# --- Signals -----------------------------------------------------------

def log_signal(
    city: str,
    target_date: date,
    bracket_label: str,
    model_prob: float,
    adj_prob: float,
    kalshi_price: int,
    edge: float,
    ensemble_spread: float = None,
    bias_correction: float = None,
    confidence: float = None,
    lead_hours: int = None,
    action: str = "NO_TRADE",
    reject_reason: str = None,
) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO signals (
                city, target_date, bracket_label, model_prob, bias_correction,
                adj_prob, kalshi_price, edge, confidence, lead_hours,
                ensemble_spread, action, reject_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                city, str(target_date), bracket_label, model_prob, bias_correction,
                adj_prob, kalshi_price, edge, confidence, lead_hours,
                ensemble_spread, action, reject_reason,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# --- Trades --------------------------------------------------------------

def log_trade(
    signal_id: int,
    city: str,
    target_date: date,
    ticker: str,
    side: str,
    price_cents: int,
    num_contracts: int,
    spend_usd: float,
    paper_trade: bool,
    order_id: str = None,
    status: str = "pending",
) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO trades (
                signal_id, city, target_date, ticker, side, price_cents,
                num_contracts, spend_usd, paper_trade, order_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id, city, str(target_date), ticker, side, price_cents,
                num_contracts, spend_usd, int(paper_trade), order_id, status,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_daily_spend(trade_date: date = None) -> float:
    """Total spend across all trades placed today (or given date)."""
    trade_date = trade_date or date.today()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(spend_usd), 0) AS total FROM trades "
            "WHERE date(created_at) = ?",
            (str(trade_date),),
        ).fetchone()
        return float(row["total"])
    finally:
        conn.close()


def get_city_spend_today(city: str, trade_date: date = None) -> float:
    """Total spend in a single city today (or given date)."""
    trade_date = trade_date or date.today()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(spend_usd), 0) AS total FROM trades "
            "WHERE city = ? AND date(created_at) = ?",
            (city, str(trade_date)),
        ).fetchone()
        return float(row["total"])
    finally:
        conn.close()


def get_trades_today_count(trade_date: date = None) -> int:
    trade_date = trade_date or date.today()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM trades WHERE date(created_at) = ?",
            (str(trade_date),),
        ).fetchone()
        return int(row["n"])
    finally:
        conn.close()


# --- Balance / drawdown tracking ----------------------------------------

def get_current_balance() -> float:
    """
    Most recent ending_balance from daily_summary, or the starting bankroll
    if no days have been closed out yet.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT ending_balance FROM daily_summary "
            "WHERE ending_balance IS NOT NULL "
            "ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
        return float(row["ending_balance"]) if row else STARTING_BANKROLL_USD
    finally:
        conn.close()


def get_peak_balance() -> float:
    """Highest ending_balance ever recorded, or the current balance."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT MAX(ending_balance) AS peak FROM daily_summary "
            "WHERE ending_balance IS NOT NULL"
        ).fetchone()
        if row and row["peak"] is not None:
            return float(row["peak"])
        return get_current_balance()
    finally:
        conn.close()


def upsert_daily_summary(
    trade_date: date,
    trades_placed: int,
    total_spend_usd: float,
    total_pnl_usd: float,
    win_count: int,
    loss_count: int,
    ending_balance: float,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO daily_summary (
                trade_date, trades_placed, total_spend_usd, total_pnl_usd,
                win_count, loss_count, ending_balance
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                trades_placed = excluded.trades_placed,
                total_spend_usd = excluded.total_spend_usd,
                total_pnl_usd = excluded.total_pnl_usd,
                win_count = excluded.win_count,
                loss_count = excluded.loss_count,
                ending_balance = excluded.ending_balance
            """,
            (
                str(trade_date), trades_placed, total_spend_usd, total_pnl_usd,
                win_count, loss_count, ending_balance,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# --- Bias corrections ------------------------------------------------------

# A bias correction is only trusted if it's statistically distinguishable
# from zero (one-sample t-test vs H0: bias = 0) with a reasonable sample
# size. Below these thresholds, get_bias_correction() falls back to 0.0
# rather than applying what could just be noise.
BIAS_SIGNIFICANCE_P_MAX = 0.05
BIAS_MIN_SAMPLE_COUNT = 5


def get_bias_correction(city: str, season: str, bracket_center_f: float) -> float:
    """
    Look up the historical bias (°F, station observed minus ECMWF forecast)
    for a city/season/temperature-range bucket. Returns 0.0 if no data has
    been collected yet (Phase 1 default — table is empty until
    scripts/build_bias_table.py has been run), or if the bucket's bias is
    not statistically significant (p >= BIAS_SIGNIFICANCE_P_MAX or
    sample_count < BIAS_MIN_SAMPLE_COUNT) — i.e. likely noise, not a real
    forecast/station discrepancy.
    """
    temp_range = temp_range_bucket(bracket_center_f)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT bias_f, p_value, sample_count FROM bias_corrections "
            "WHERE city = ? AND season = ? AND temp_range = ?",
            (city, season, temp_range),
        ).fetchone()
        if not row:
            return 0.0
        if row["sample_count"] < BIAS_MIN_SAMPLE_COUNT:
            return 0.0
        if row["p_value"] is None or row["p_value"] >= BIAS_SIGNIFICANCE_P_MAX:
            return 0.0
        return float(row["bias_f"])
    finally:
        conn.close()


def upsert_bias_correction(
    city: str, nws_station: str, season: str, temp_range: str,
    bias_f: float, sample_count: int,
    std_f: float = None, p_value: float = None,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO bias_corrections (
                city, nws_station, season, temp_range, bias_f, std_f,
                p_value, sample_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(city, season, temp_range) DO UPDATE SET
                nws_station = excluded.nws_station,
                bias_f = excluded.bias_f,
                std_f = excluded.std_f,
                p_value = excluded.p_value,
                sample_count = excluded.sample_count,
                updated_at = CURRENT_TIMESTAMP
            """,
            (city, nws_station, season, temp_range, bias_f, std_f, p_value, sample_count),
        )
        conn.commit()
    finally:
        conn.close()


# --- Outcomes --------------------------------------------------------------

def log_outcome(
    trade_id: int,
    ticker: str,
    settled_temp_f: float,
    winning_bracket: str,
    pnl_usd: float,
    result: str,
) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO outcomes (
                trade_id, ticker, settled_temp_f, winning_bracket, pnl_usd, result
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (trade_id, ticker, settled_temp_f, winning_bracket, pnl_usd, result),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_open_paper_trades() -> list:
    """Paper trades that don't yet have a recorded outcome."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT t.* FROM trades t
            LEFT JOIN outcomes o ON o.trade_id = t.id
            WHERE t.paper_trade = 1 AND o.id IS NULL
            ORDER BY t.created_at DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_unsettled_trades() -> list:
    """All trades (paper or live) that don't yet have a recorded outcome."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT t.* FROM trades t
            LEFT JOIN outcomes o ON o.trade_id = t.id
            WHERE o.id IS NULL
            ORDER BY t.created_at ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_daily_summary_inputs(trade_date: date) -> dict:
    """
    Aggregate trades + outcomes for a given date, for building a
    daily_summary row: trades placed, total spend, total pnl, win/loss counts.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT
                COUNT(t.id) AS trades_placed,
                COALESCE(SUM(t.spend_usd), 0) AS total_spend_usd,
                COALESCE(SUM(o.pnl_usd), 0) AS total_pnl_usd,
                COALESCE(SUM(CASE WHEN o.result = 'win' THEN 1 ELSE 0 END), 0) AS win_count,
                COALESCE(SUM(CASE WHEN o.result = 'loss' THEN 1 ELSE 0 END), 0) AS loss_count
            FROM trades t
            LEFT JOIN outcomes o ON o.trade_id = t.id
            WHERE date(t.created_at) = ?
            """,
            (str(trade_date),),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()
