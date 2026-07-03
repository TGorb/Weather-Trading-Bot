"""
Fetch settlement results for trades that don't have a recorded outcome yet,
compute realized P&L, and roll the results into daily_summary.

Run this once per day, before run_daily.py (e.g. 6:45am, ahead of the 7am
signal run) so that yesterday's trades are settled and the running bankroll
used for today's Kelly sizing / drawdown checks reflects reality.
"""

import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from kalshi.client import KalshiClient
from kalshi.markets import get_market, is_market_finalized, settle_trade_pnl
from database.schema import init_db
from database.queries import (
    get_current_balance,
    get_daily_summary_inputs,
    get_unsettled_trades,
    log_outcome,
    upsert_daily_summary,
)


def run():
    print(f"\n{'=' * 60}")
    print(f"Fetching settlements — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 60}\n")

    init_db()
    client = KalshiClient()

    unsettled = get_unsettled_trades()
    if not unsettled:
        print("No unsettled trades to check.\n")
        return

    print(f"Checking {len(unsettled)} unsettled trade(s)...\n")

    affected_dates = set()
    settled_count = 0

    for trade in unsettled:
        ticker = trade["ticker"]
        try:
            market = get_market(client, ticker)
        except Exception as e:
            print(f"  {ticker}: ERROR fetching market — {e}")
            continue

        if not market or not is_market_finalized(market):
            print(f"  {ticker}: not yet settled (status={market.get('status')})")
            continue

        result = market["result"]
        pnl_usd, outcome = settle_trade_pnl(
            side=trade["side"],
            num_contracts=trade["num_contracts"],
            spend_usd=trade["spend_usd"],
            result=result,
            settlement_value_dollars=market.get("settlement_value_dollars", "0.0000"),
        )

        log_outcome(
            trade_id=trade["id"],
            ticker=ticker,
            settled_temp_f=None,  # Kalshi doesn't expose the raw settled temp via this endpoint
            winning_bracket=market.get("subtitle"),
            pnl_usd=pnl_usd,
            result=outcome,
        )

        print(
            f"  {ticker}: {trade['side'].upper()} settled {result.upper()} — "
            f"{outcome} (${pnl_usd:+.2f})"
        )

        affected_dates.add(trade["created_at"][:10])
        settled_count += 1

    if settled_count:
        # Roll settled days into daily_summary, chaining ending_balance day over day.
        running_balance = None
        for trade_date_str in sorted(affected_dates):
            trade_date = date.fromisoformat(trade_date_str)
            if running_balance is None:
                # Balance carried in from before this batch of days.
                running_balance = get_current_balance()
            inputs = get_daily_summary_inputs(trade_date)
            running_balance += inputs["total_pnl_usd"]
            upsert_daily_summary(
                trade_date=trade_date,
                trades_placed=inputs["trades_placed"],
                total_spend_usd=inputs["total_spend_usd"],
                total_pnl_usd=inputs["total_pnl_usd"],
                win_count=inputs["win_count"],
                loss_count=inputs["loss_count"],
                ending_balance=running_balance,
            )

    print(f"\n{settled_count} trade(s) settled this run.")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    run()
