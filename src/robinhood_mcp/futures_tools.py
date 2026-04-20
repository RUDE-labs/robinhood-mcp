"""Futures tools for Robinhood MCP.

Since robin_stocks doesn't support futures yet, this module hits the
Robinhood futures API directly using robin_stocks' session infrastructure.

API endpoints discovered via robin_stocks PR #1641:
- Contracts: api.robinhood.com/arsenal/v1/futures/contracts/symbol/{symbol}
- Quotes:    api.robinhood.com/marketdata/futures/quotes/v1/
- Orders:    api.robinhood.com/ceres/v1/accounts/{account_id}/orders
- Accounts:  api.robinhood.com/pathfinder/user_machine/ (account discovery)

Positions are DERIVED from filled order history since the positions
endpoint hasn't been discovered yet.
"""

import threading
import time
from collections import defaultdict
from typing import Any

import robin_stocks.robinhood as rh
from robin_stocks.robinhood.helper import request_get, update_session

from .tools import RobinhoodError


# ---------------------------------------------------------------------------
# Session / helpers
# ---------------------------------------------------------------------------

def _update_session_for_futures() -> None:
    """Futures endpoints require the Rh-Contract-Protected header."""
    update_session("Rh-Contract-Protected", "true")


def _extract_amount(field: Any) -> float:
    """Extract numeric amount from nested futures amount structure."""
    if field is None:
        return 0.0
    if isinstance(field, (int, float)):
        return float(field)
    if isinstance(field, str):
        return float(field) if field else 0.0
    if isinstance(field, dict) and "amount" in field:
        amount = field["amount"]
        return float(amount) if amount else 0.0
    return 0.0


# ---------------------------------------------------------------------------
# Futures account discovery
# ---------------------------------------------------------------------------

_futures_account_id: str | None = None
_account_lock = threading.Lock()


def _get_futures_account_id() -> str:
    """Discover the futures account ID by filtering for accountType=FUTURES."""
    global _futures_account_id

    with _account_lock:
        if _futures_account_id is not None:
            return _futures_account_id

    _update_session_for_futures()

    # Try the ceres accounts endpoint first
    url = "https://api.robinhood.com/ceres/v1/accounts/"
    data = request_get(url)

    accounts = []
    if isinstance(data, dict) and "results" in data:
        accounts = data["results"]
    elif isinstance(data, list):
        accounts = data

    for account in accounts:
        if isinstance(account, dict) and account.get("accountType") == "FUTURES":
            with _account_lock:
                _futures_account_id = account["id"]
            return _futures_account_id

    raise RobinhoodError(
        "No futures account found. Make sure futures trading is enabled on your Robinhood account."
    )


# ---------------------------------------------------------------------------
# Contract lookup
# ---------------------------------------------------------------------------

def get_futures_contract(symbol: str) -> dict[str, Any] | None:
    """Get futures contract details by symbol (e.g., 'ESH26', '/ESH26')."""
    symbol = symbol.upper().strip().lstrip("/")
    url = f"https://api.robinhood.com/arsenal/v1/futures/contracts/symbol/{symbol}"
    _update_session_for_futures()
    data = request_get(url)

    if data and isinstance(data, dict) and "result" in data:
        return data["result"]
    return None


def _id_for_futures_contract(symbol: str) -> str | None:
    """Resolve a futures symbol to its instrument ID."""
    contract = get_futures_contract(symbol)
    if contract and isinstance(contract, dict):
        return contract.get("id")
    return None


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------

def get_futures_quote(symbol: str) -> dict[str, Any]:
    """Get real-time quote for a futures contract.

    Args:
        symbol: Futures symbol (e.g., 'ESM26', '/ESM26', 'NQM26')

    Returns:
        Quote data with bid/ask, last trade price, state, etc.
    """
    symbol = symbol.upper().strip().lstrip("/")
    contract_id = _id_for_futures_contract(symbol)
    if not contract_id:
        raise RobinhoodError(f"No futures contract found for symbol: {symbol}")

    url = "https://api.robinhood.com/marketdata/futures/quotes/v1/"
    payload = {"ids": contract_id}
    _update_session_for_futures()
    data = request_get(url, payload=payload)

    if data and isinstance(data, dict) and "data" in data:
        items = data["data"]
        if items and isinstance(items, list) and len(items) > 0:
            quote_data = items[0].get("data") if isinstance(items[0], dict) else None
            if quote_data:
                return quote_data

    raise RobinhoodError(f"No quote data returned for futures symbol: {symbol}")


# ---------------------------------------------------------------------------
# Orders (with cursor-based pagination)
# ---------------------------------------------------------------------------

def get_futures_orders(
    order_state: str | None = None,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """Get futures order history with automatic pagination.

    Args:
        order_state: Filter by state (e.g., 'FILLED', 'CANCELLED'). None = all.
        max_pages: Safety limit on pagination (default 20).

    Returns:
        List of order dicts with orderId, orderState, orderLegs,
        quantity, averagePrice, realizedPnl, fees, timestamps.
    """
    account_id = _get_futures_account_id()
    url = f"https://api.robinhood.com/ceres/v1/accounts/{account_id}/orders"

    all_orders: list[dict[str, Any]] = []
    cursor = None
    _update_session_for_futures()

    for page in range(max_pages):
        payload: dict[str, str] = {"contractType": "OUTRIGHT"}
        if order_state:
            payload["orderState"] = order_state
        if cursor:
            payload["cursor"] = cursor

        data = request_get(url, payload=payload)

        if not isinstance(data, dict) or "results" not in data:
            break

        all_orders.extend(data["results"])
        cursor = data.get("next")
        if not cursor:
            break

    return all_orders


# ---------------------------------------------------------------------------
# Derived positions from filled orders
# ---------------------------------------------------------------------------

def _parse_display_symbol(order: dict) -> str | None:
    """Extract the display symbol (e.g., '/ESM26') from an order's legs."""
    legs = order.get("orderLegs", [])
    if not legs or not isinstance(legs, list):
        return None
    leg = legs[0]
    if not isinstance(leg, dict):
        return None
    # Try multiple possible field names
    return (
        leg.get("displaySymbol")
        or leg.get("futuresDisplaySymbol")
        or leg.get("symbol")
    )


def _get_contract_multiplier(order: dict) -> float:
    """Extract the contract multiplier from order leg metadata."""
    legs = order.get("orderLegs", [])
    if legs and isinstance(legs, list) and isinstance(legs[0], dict):
        mult = legs[0].get("multiplier")
        if mult:
            try:
                return float(mult)
            except (TypeError, ValueError):
                pass
    # Common defaults
    return 1.0


def get_futures_positions() -> list[dict[str, Any]]:
    """Derive current open futures positions from filled order history.

    Nets buy/sell quantities per contract symbol to determine open positions.
    Calculates average entry price and attempts to fetch current quote for
    unrealized P&L estimation.

    Returns:
        List of position dicts with:
        - symbol: Display symbol (e.g., '/ESM26')
        - side: 'long' or 'short'
        - quantity: Net open contracts (absolute value)
        - avg_entry_price: Weighted average fill price
        - current_price: Latest quote (if available)
        - unrealized_pnl: Estimated uPnL (if quote available)
        - multiplier: Contract multiplier
        - total_fees: Fees paid on fills for this symbol
    """
    filled_orders = get_futures_orders(order_state="FILLED")

    if not filled_orders:
        return []

    # Group by symbol, track net position and avg entry
    positions: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "buy_qty": 0.0,
        "sell_qty": 0.0,
        "buy_cost": 0.0,  # total $ spent on buys (qty * price)
        "sell_cost": 0.0,  # total $ received on sells
        "total_fees": 0.0,
        "multiplier": 1.0,
        "raw_symbol": None,
    })

    for order in filled_orders:
        symbol = _parse_display_symbol(order)
        if not symbol:
            continue

        # Clean symbol for grouping
        clean_sym = symbol.upper().strip().lstrip("/")

        qty = float(order.get("filledQuantity", 0) or order.get("quantity", 0) or 0)
        avg_price = float(order.get("averagePrice", 0) or 0)
        position_effect = order.get("positionEffectAtPlacementTime", "")
        side = order.get("side", "").upper()

        pos = positions[clean_sym]
        pos["raw_symbol"] = symbol
        pos["multiplier"] = _get_contract_multiplier(order)

        # Accumulate fees
        fee = _extract_amount(order.get("totalFee", 0))
        commission = _extract_amount(order.get("totalCommission", 0))
        pos["total_fees"] += fee + commission

        if side == "BUY":
            pos["buy_qty"] += qty
            pos["buy_cost"] += qty * avg_price
        elif side == "SELL":
            pos["sell_qty"] += qty
            pos["sell_cost"] += qty * avg_price

    # Build result — only include symbols with net open position
    result: list[dict[str, Any]] = []

    for clean_sym, pos in positions.items():
        net_qty = pos["buy_qty"] - pos["sell_qty"]

        if abs(net_qty) < 0.0001:
            continue  # Position is flat

        if net_qty > 0:
            side = "long"
            abs_qty = net_qty
            # Avg entry for longs = total buy cost / buy qty
            avg_entry = pos["buy_cost"] / pos["buy_qty"] if pos["buy_qty"] > 0 else 0
        else:
            side = "short"
            abs_qty = abs(net_qty)
            # Avg entry for shorts = total sell cost / sell qty
            avg_entry = pos["sell_cost"] / pos["sell_qty"] if pos["sell_qty"] > 0 else 0

        # Try to get current quote for uPnL
        current_price = None
        unrealized_pnl = None
        try:
            quote = get_futures_quote(clean_sym)
            last_price = quote.get("last_trade_price") or quote.get("mark_price")
            if last_price:
                current_price = float(last_price)
                multiplier = pos["multiplier"]
                if side == "long":
                    unrealized_pnl = (current_price - avg_entry) * abs_qty * multiplier
                else:
                    unrealized_pnl = (avg_entry - current_price) * abs_qty * multiplier
        except Exception:
            pass  # Quote unavailable — still return position without uPnL

        result.append({
            "symbol": pos["raw_symbol"] or f"/{clean_sym}",
            "side": side,
            "quantity": abs_qty,
            "avg_entry_price": round(avg_entry, 4),
            "current_price": current_price,
            "unrealized_pnl": round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
            "multiplier": pos["multiplier"],
            "total_fees": round(pos["total_fees"], 2),
        })

    return result


# ---------------------------------------------------------------------------
# P&L summary
# ---------------------------------------------------------------------------

def get_futures_pnl() -> dict[str, Any]:
    """Calculate aggregate realized P&L from all filled futures orders.

    Only counts CLOSING orders to avoid double-counting.

    Returns:
        Dict with total_pnl, total_pnl_without_fees, total_fees,
        total_commissions, num_closing_orders.
    """
    filled_orders = get_futures_orders(order_state="FILLED")

    totals = {
        "total_pnl": 0.0,
        "total_pnl_without_fees": 0.0,
        "total_fees": 0.0,
        "total_commissions": 0.0,
        "total_gold_savings": 0.0,
        "num_closing_orders": 0,
    }

    for order in filled_orders:
        # Only count CLOSING orders to avoid double-counting
        position_effect = order.get("positionEffectAtPlacementTime", "")
        if position_effect != "CLOSING":
            continue

        # Extract nested P&L (double-nested structure)
        realized_pnl_obj = order.get("realizedPnl")
        if isinstance(realized_pnl_obj, dict):
            if "realizedPnl" in realized_pnl_obj:
                totals["total_pnl"] += _extract_amount(realized_pnl_obj["realizedPnl"])
            if "realizedPnlWithoutFees" in realized_pnl_obj:
                totals["total_pnl_without_fees"] += _extract_amount(
                    realized_pnl_obj["realizedPnlWithoutFees"]
                )

        totals["total_fees"] += _extract_amount(order.get("totalFee", 0))
        totals["total_commissions"] += _extract_amount(order.get("totalCommission", 0))
        totals["total_gold_savings"] += _extract_amount(order.get("totalGoldSavings", 0))
        totals["num_closing_orders"] += 1

    # Round everything
    for key in totals:
        if isinstance(totals[key], float):
            totals[key] = round(totals[key], 2)

    return totals
