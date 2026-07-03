"""
Market discovery, order sizing, and order placement for Kalshi weather markets.
"""

import re

from kalshi.client import KalshiClient


def get_weather_markets(client: KalshiClient, series_ticker: str) -> list:
    """
    Fetch all open markets for a given weather series (e.g. KXHIGHCHI).
    Returns the raw market objects from Kalshi — use yes_bid_cents()/
    yes_ask_cents() below to read prices (Kalshi returns them as
    yes_bid_dollars/yes_ask_dollars string fields, e.g. "0.1500", not
    integer cents).
    """
    data = client.get(
        "/markets",
        params={"series_ticker": series_ticker, "status": "open", "limit": 50},
    )
    return data.get("markets", [])


def _cents_from_dollars(dollar_str, default: int = 0) -> int:
    """Convert a Kalshi dollar-string price field (e.g. "0.1500") to cents."""
    if dollar_str is None:
        return default
    try:
        return round(float(dollar_str) * 100)
    except (TypeError, ValueError):
        return default


def yes_bid_cents(market: dict) -> int:
    return _cents_from_dollars(market.get("yes_bid_dollars"), default=0)


def yes_ask_cents(market: dict) -> int:
    return _cents_from_dollars(market.get("yes_ask_dollars"), default=100)


def parse_bracket_from_market(market: dict) -> dict:
    """
    Parse the temperature bracket bounds from a Kalshi market object.

    Live subtitles from Kalshi look like "97° or above", "88° or below", and
    "95° to 96°" (confirmed against the demo API) — not the "82+"/"below 60"/
    "78-80" shorthand originally assumed. Both forms are handled here.
    High bounds are exclusive (matching signals/probability.py's low <= t <
    high convention), so a stated upper degree of N becomes high=N+1 to
    include that whole degree.

    Not every market populates `subtitle` — some (confirmed: Dallas/Houston)
    have `subtitle: None` and instead carry the same parseable text in
    `yes_sub_title`. The full-sentence `title` field ("Will the maximum
    temperature be <95° on Jul 3, 2026?") never matches these patterns and
    is only kept as a last-resort fallback.

    Returns {"low": float, "high": float} or None if unparseable.
    """
    title = market.get("subtitle") or market.get("yes_sub_title") or market.get("title", "")
    num = r"(\d+(?:\.\d+)?)"

    above_match = re.search(rf"{num}\s*°?\s*or\s+above", title, re.IGNORECASE)
    if above_match:
        return {"low": float(above_match.group(1)), "high": None}

    below_match = re.search(rf"{num}\s*°?\s*or\s+below", title, re.IGNORECASE)
    if below_match:
        return {"low": None, "high": float(below_match.group(1)) + 1}

    to_match = re.search(rf"{num}\s*°?\s*to\s*{num}\s*°?", title, re.IGNORECASE)
    if to_match:
        return {"low": float(to_match.group(1)), "high": float(to_match.group(2)) + 1}

    range_match = re.search(rf"{num}\s*-\s*{num}", title)
    if range_match:
        return {"low": float(range_match.group(1)), "high": float(range_match.group(2))}

    legacy_below_match = re.search(rf"\bbelow\s+{num}", title, re.IGNORECASE)
    if legacy_below_match:
        return {"low": None, "high": float(legacy_below_match.group(1))}

    legacy_above_match = re.search(rf"{num}\s*\+", title)
    if legacy_above_match:
        return {"low": float(legacy_above_match.group(1)), "high": None}

    return None


def get_market(client: KalshiClient, ticker: str) -> dict:
    """
    Fetch a single market by ticker. Used to check whether a market has
    settled (status == "finalized") and, if so, its result ("yes"/"no")
    and settlement_value_dollars (the per-contract YES payout: "1.0000" if
    YES won, "0.0000" if NO won) — confirmed against the live demo API.
    """
    data = client.get(f"/markets/{ticker}")
    return data.get("market", {})


def is_market_finalized(market: dict) -> bool:
    return market.get("status") == "finalized" and market.get("result") in ("yes", "no")


def settle_trade_pnl(
    side: str,
    num_contracts: int,
    spend_usd: float,
    result: str,
    settlement_value_dollars,
) -> tuple:
    """
    Compute realized P&L for a settled trade.

    side: "yes" or "no" — the side the trade bought.
    result: "yes" or "no" — the market's actual settlement result.
    settlement_value_dollars: per-contract YES payout as returned by Kalshi
        (e.g. "1.0000" if YES won, "0.0000" if NO won).

    Returns (pnl_usd: float, outcome: "win"|"loss").
    """
    yes_payout_per_contract = float(settlement_value_dollars)
    payout_per_contract = (
        yes_payout_per_contract if side == "yes" else (1.0 - yes_payout_per_contract)
    )
    payout = num_contracts * payout_per_contract
    pnl_usd = payout - spend_usd
    outcome = "win" if side == result else "loss"
    return pnl_usd, outcome


def contracts_for_spend(price_cents: int, spend_usd: float) -> int:
    """
    Calculate number of contracts for a given dollar spend.
    Each contract costs price_cents / 100 dollars.
    """
    cost_per_contract = price_cents / 100.0
    if cost_per_contract <= 0:
        return 0
    return max(1, int(spend_usd / cost_per_contract))


def place_order(
    client: KalshiClient,
    ticker: str,
    side: str,  # "yes" or "no"
    price_cents: int,  # limit price in cents (1-99)
    num_contracts: int,
    paper_trade: bool = True,
) -> dict:
    """
    Place a limit order on Kalshi.

    In paper trade mode, no HTTP request is made — the order is logged and a
    simulated response returned. In live mode, the client (already routed to
    demo or production per PAPER_TRADING) submits a real order.

    price is in cents: 62 means $0.62 per contract. Each contract pays $1.00
    if you win.
    """
    if paper_trade:
        print(f"[PAPER] Would place: {side.upper()} {num_contracts}x {ticker} @ {price_cents}c")
        return {
            "order_id": f"paper_{ticker}_{price_cents}",
            "status": "paper_resting",
            "paper_trade": True,
        }

    order_payload = {
        "ticker": ticker,
        "action": "buy",
        "side": side,
        "type": "limit",
        "count": str(num_contracts),
        "yes_price": str(price_cents) if side == "yes" else str(100 - price_cents),
    }

    result = client.post("/portfolio/events/orders", order_payload)
    return result.get("order", {})
