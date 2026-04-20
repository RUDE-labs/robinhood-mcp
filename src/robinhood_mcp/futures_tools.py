"""Futures tools for Robinhood MCP.

Since robin_stocks doesn't support futures yet, this module hits the
Robinhood futures API directly using robin_stocks' session infrastructure.

API endpoints discovered via robin_stocks PR #1641:
- Contracts: api.robinhood.com/arsenal/v1/futures/contracts/symbol/{symbol}
- Contract by ID: api.robinhood.com/arsenal/v1/futures/contracts/{id}
- Quotes:    api.robinhood.com/marketdata/futures/quotes/v1/
- Orders:    api.robinhood.com/ceres/v1/accounts/{account_id}/orders
- Accounts:  api.robinhood.com/ceres/v1/accounts/

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


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Safely parse a value to float (handles strings, None, etc.)."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
# Contract lookup (by symbol and by ID)
# ---------------------------------------------------------------------------

# Cache: contractId -> contract details dict
_contract_cache: dict[str, dict[str, Any]] = {}
_contract_cache_lock = threading.Lock()


def get_futures_contract(symbol: str) -> dict[str, Any] | None:
    """Get futures contract details by symbol (e.g., 'ESH26', '/ESH26')."""
    symbol = symbol.upper().strip().lstrip("/")
    url = f"https://api.robinhood.com/arsenal/v1/futures/contracts/symbol/{symbol}"
    _update_session_for_futures()
    data = request_get(url)

    if data and isinstance(data, dict) and "result" in data:
        result = data["result"]
        # Cache by ID for later lookups
        if isinstance(result, dict) and "id" in result:
            with _contract_cache_lock:
                _contract_cache[result["id"]] = result
        return result
    return None


def _get_contract_by_id(contract_id: str) -> dict[str, Any] | None:
    """Get futures contract details by contract UUID.

    Checks cache first, then hits the API.
    """
    with _contract_cache_lock:
        if contract_id in _contract_cache:
            return _contract_cache[contract_id]

    url = f"https://api.robinhood.com/arsenal/v1/futures/contracts/{contract_id}"
    _update_session_for_futures()
    data = request_get(url)

    result = None
    if data and isinstance(data, dict):
        # API may return the contract directly or wrapped in "result"
        if "result" in data:
            result = data["result"]
        elif "id" in data:
            result = data

    if result and isinstance(result, dict):
        with _contract_cache_lock:
            _contract_cache[contract_id] = result
        return result

    return None


def _resolve_contract_symbol(contract_id: str) -> str | None:
    """Resolve a contractId UUID to a display symbol like '/MGCQ26'."""
    contract = _get_contract_by_id(contract_id)
    if not contract:
        return None
    # Try displaySymbol first (e.g., '/ESH26'), then symbol
    return (
        contract.get("displaySymbol")
        or contract.get("symbol")
        or contract.get("futuresSymbol")
    )


def _resolve_contract_multiplier(contract_id: str) -> float:
    """Get the contract multiplier from contract details."""
    contract = _get_contract_by_id(contract_id)
    if contract and isinstance(contract, dict):
        mult = contract.get("multiplier")
        if mult:
            return _safe_float(mult, 1.0)
    return 1.0


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

    return _get_futures_quote_by_id(contract_id)


def _get_futures_quote_by_id(contract_id: str) -> dict[str, Any]:
    """Get real-time quote by contract ID."""
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

    raise RobinhoodError(f"No quote data returned for contract ID: {contract_id}")


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

def get_futures_positions() -> list[dict[str, Any]]:
    """Derive current open futures positions from filled order history.

    Groups orders by contractId (UUID) from orderLegs, nets buy/sell
    quantities to determine open positions. Resolves contractId to
    human-readable symbol via contract API. Fetches live quotes for
    unrealized P&L.

    Returns:
        List of position dicts with:
        - symbol: Display symbol (e.g., '/MGCQ26')
        - contract_id: Robinhood contract UUID
        - side: 'long' or 'short'
        - quantity: Net open contracts (absolute value)
        - avg_entry_price: Weighted average fill price
        - current_price: Latest quote (if available)
        - unrealized_pnl: Estimated uPnL (if quote available)
        - multiplier: Contract multiplier
        - total_fees: Fees paid on fills for this contract
    """
    filled_orders = get_futures_orders(order_state="FILLED")

    if not filled_orders:
        return []

    # Group by contractId from orderLegs[0]
    positions: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "buy_qty": 0.0,
        "sell_qty": 0.0,
        "buy_cost": 0.0,
        "sell_cost": 0.0,
        "total_fees": 0.0,
    })

    for order in filled_orders:
        legs = order.get("orderLegs")
        if not legs or not isinstance(legs, list) or not isinstance(legs[0], dict):
            continue

        leg = legs[0]
        contract_id = leg.get("contractId")
        if not contract_id:
            continue

        # Side is in orderLegs[0].orderSide, NOT order.side
        side = (leg.get("orderSide") or "").upper()
        if side not in ("BUY", "SELL"):
            continue

        # filledQuantity and averagePrice are strings
        qty = _safe_float(order.get("filledQuantity") or order.get("quantity"))
        avg_price = _safe_float(order.get("averagePrice") or leg.get("averagePrice"))

        if qty <= 0:
            continue

        pos = positions[contract_id]

        # Accumulate fees
        fee = _extract_amount(order.get("totalFee", 0))
        commission = _extract_amount(order.get("totalCommission", 0))
        pos["total_fees"] += fee + commission

        if side == "BUY":
            pos["buy_qty"] += qty
            pos["buy_cost"] += qty * avg_price
        else:
            pos["sell_qty"] += qty
            pos["sell_cost"] += qty * avg_price

    # Build result — only include contracts with net open position
    result: list[dict[str, Any]] = []

    for contract_id, pos in positions.items():
        net_qty = pos["buy_qty"] - pos["sell_qty"]

        if abs(net_qty) < 0.0001:
            continue  # Position is flat

        if net_qty > 0:
            side = "long"
            abs_qty = net_qty
            avg_entry = pos["buy_cost"] / pos["buy_qty"] if pos["buy_qty"] > 0 else 0
        else:
            side = "short"
            abs_qty = abs(net_qty)
            avg_entry = pos["sell_cost"] / pos["sell_qty"] if pos["sell_qty"] > 0 else 0

        # Resolve contractId to human-readable symbol
        display_symbol = _resolve_contract_symbol(contract_id) or contract_id
        multiplier = _resolve_contract_multiplier(contract_id)

        # Try to get current quote for uPnL
        current_price = None
        unrealized_pnl = None
        try:
            quote = _get_futures_quote_by_id(contract_id)
            last_price = quote.get("last_trade_price") or quote.get("mark_price")
            if last_price:
                current_price = _safe_float(last_price)
                if side == "long":
                    unrealized_pnl = (current_price - avg_entry) * abs_qty * multiplier
                else:
                    unrealized_pnl = (avg_entry - current_price) * abs_qty * multiplier
        except Exception:
            pass  # Quote unavailable — still return position without uPnL

        result.append({
            "symbol": display_symbol,
            "contract_id": contract_id,
            "side": side,
            "quantity": abs_qty,
            "avg_entry_price": round(avg_entry, 4),
            "current_price": current_price,
            "unrealized_pnl": round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
            "multiplier": multiplier,
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
