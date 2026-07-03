"""
Standalone backtest runner.

Two modes:
  1. (default) Report on the bot's own settled paper/live trade history —
     reads trades + outcomes already logged to SQLite by run_daily.py and a
     settlement-fetch job, and prints a performance report.
  2. --history <path> — replay the strategy against an external historical
     dataset (JSON list matching the shape documented in
     backtest/engine.py::run_backtest) that pairs ensemble forecasts with
     historical Kalshi bracket prices and settled temperatures.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from database.schema import get_connection, init_db
from backtest.engine import run_backtest
from backtest.report import print_report
from config import STARTING_BANKROLL_USD


def report_from_logged_trades() -> None:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT t.city, t.spend_usd, o.pnl_usd, o.result
            FROM trades t
            JOIN outcomes o ON o.trade_id = t.id
            ORDER BY t.created_at
            """
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        print("No settled trades logged yet — nothing to report.")
        print("Run scripts/run_daily.py in paper mode for a few weeks first.")
        return

    trades = [
        {"city": r["city"], "spend_usd": r["spend_usd"], "pnl_usd": r["pnl_usd"], "result": r["result"]}
        for r in rows
    ]
    print_report(trades, STARTING_BANKROLL_USD)


def report_from_history_file(path: str) -> None:
    with open(path) as f:
        history = json.load(f)
    result = run_backtest(history, starting_bankroll=STARTING_BANKROLL_USD)
    print_report(result["trades"], STARTING_BANKROLL_USD)
    print(f"Ending bankroll: ${result['ending_bankroll']:.2f}")


def run():
    parser = argparse.ArgumentParser(description="Run a backtest of the weather trading strategy")
    parser.add_argument(
        "--history",
        help="Path to a JSON file of historical ensemble/price/outcome data "
        "(see backtest/engine.py docstring for the expected shape)",
    )
    args = parser.parse_args()

    init_db()

    if args.history:
        report_from_history_file(args.history)
    else:
        report_from_logged_trades()


if __name__ == "__main__":
    run()
