"""FastMCP server for Robinhood portfolio research."""

import sys
import threading
import time
from typing import Literal

from dotenv import load_dotenv
from fastmcp import FastMCP

from .auth import AuthenticationError, EnvironmentVariablesError, is_logged_in, login
from .tools import (
    RobinhoodError,
    get_dividends,
    get_earnings,
    get_fundamentals,
    get_historicals,
    get_news,
    get_options_positions,
    get_portfolio,
    get_position,
    get_positions,
    get_quote,
    get_ratings,
    get_watchlist,
    search_symbols,
)
from .futures_tools import (
    get_futures_positions,
    get_futures_orders,
    get_futures_quote,
    get_futures_pnl,
    get_futures_contract,
)

# Load environment variables
load_dotenv()

# Initialize FastMCP server (older versions don't accept description kwarg).
try:
    mcp = FastMCP(
        "robinhood-mcp",
        description="Read-only research tools for Robinhood portfolio data (stocks, options, and futures)",
    )
except TypeError:
    mcp = FastMCP("robinhood-mcp")

# Track login state
_login_attempted = False
_login_error: str | None = None
_login_lock = threading.Lock()
_cached_login_status: bool | None = None
_cached_login_status_ts = 0.0
_LOGIN_STATUS_TTL_SECONDS = 5.0


def _is_session_valid_cached() -> bool:
    """Return cached login status when fresh, otherwise probe Robinhood once."""
    global _cached_login_status, _cached_login_status_ts

    now = time.monotonic()
    if (
        _cached_login_status is not None
        and (now - _cached_login_status_ts) < _LOGIN_STATUS_TTL_SECONDS
    ):
        return _cached_login_status

    status = is_logged_in()
    _cached_login_status = status
    _cached_login_status_ts = now
    return status


def _ensure_logged_in() -> None:
    """Ensure we're logged in before API calls, re-attempting if session expired."""
    global _login_attempted, _login_error, _cached_login_status, _cached_login_status_ts

    with _login_lock:
        # Only explicit credential/config errors are treated as permanent.
        if _login_error:
            raise RobinhoodError(f"Not logged in: {_login_error}")

        session_valid = _is_session_valid_cached() if _login_attempted else False
        if not _login_attempted or not session_valid:
            _login_attempted = True
            _login_error = None
            try:
                login()
                _cached_login_status = True
                _cached_login_status_ts = time.monotonic()
                print("[robinhood-mcp] Logged in to Robinhood", file=sys.stderr)
            except EnvironmentVariablesError as e:
                _cached_login_status = False
                _cached_login_status_ts = time.monotonic()
                _login_error = str(e)
                print(f"[robinhood-mcp] Login failed: {e}", file=sys.stderr)
                raise RobinhoodError(f"Not logged in: {_login_error}") from e
            except AuthenticationError as e:
                _cached_login_status = False
                _cached_login_status_ts = time.monotonic()
                message = str(e)
                print(f"[robinhood-mcp] Login failed: {e}", file=sys.stderr)
                raise RobinhoodError(f"Not logged in: {message}") from e


# ---------------------------------------------------------------------------
# Stock / Portfolio tools (original)
# ---------------------------------------------------------------------------


@mcp.tool()
def robinhood_get_portfolio() -> dict:
    """Get current portfolio value and performance metrics.

    Returns portfolio profile with equity, extended hours equity,
    withdrawable amount, and other account details.
    """
    _ensure_logged_in()
    return get_portfolio()


@mcp.tool()
def robinhood_get_positions() -> dict:
    """Get all current stock positions with details.

    Returns a dict mapping stock symbols to position details including
    price, quantity, average buy price, equity, and percent change.
    """
    _ensure_logged_in()
    return get_positions()


@mcp.tool()
def robinhood_get_position(symbol: str) -> dict:
    """Get one current stock position with a faster single-symbol lookup.

    Args:
        symbol: Stock ticker symbol (e.g., "HIMS", "AAPL")

    Returns a dict with held=False if absent, otherwise the position details
    for that symbol including quantity, price, average buy price, and P&L.
    """
    _ensure_logged_in()
    return get_position(symbol)


@mcp.tool()
def robinhood_get_watchlist(name: str = "Default") -> list:
    """Get stocks in a watchlist.

    Args:
        name: Watchlist name (default: "Default")

    Returns list of watchlist items with instrument details.
    """
    _ensure_logged_in()
    return get_watchlist(name)


@mcp.tool()
def robinhood_get_quote(symbol: str) -> dict:
    """Get real-time quote for a stock symbol.

    Args:
        symbol: Stock ticker symbol (e.g., "AAPL", "TSLA")

    Returns quote data including last trade price, bid, ask,
    previous close, and trading status.
    """
    _ensure_logged_in()
    return get_quote(symbol)


@mcp.tool()
def robinhood_get_fundamentals(symbol: str) -> dict:
    """Get fundamental data for a stock.

    Args:
        symbol: Stock ticker symbol

    Returns fundamentals including P/E ratio, market cap,
    dividend yield, 52-week high/low, and more.
    """
    _ensure_logged_in()
    return get_fundamentals(symbol)


@mcp.tool()
def robinhood_get_historicals(
    symbol: str,
    interval: Literal["5minute", "10minute", "hour", "day", "week"] = "day",
    span: Literal["day", "week", "month", "3month", "year", "5year"] = "month",
) -> list:
    """Get historical price data for a stock.

    Args:
        symbol: Stock ticker symbol
        interval: Time interval (5minute, 10minute, hour, day, week)
        span: Time span (day, week, month, 3month, year, 5year)

    Returns list of OHLCV data points (open, high, low, close, volume).
    """
    _ensure_logged_in()
    return get_historicals(symbol, interval, span)


@mcp.tool()
def robinhood_get_news(symbol: str) -> list:
    """Get recent news articles for a stock.

    Args:
        symbol: Stock ticker symbol

    Returns list of news articles with title, URL, source,
    and publication date.
    """
    _ensure_logged_in()
    return get_news(symbol)


@mcp.tool()
def robinhood_get_earnings(symbol: str) -> list:
    """Get earnings data for a stock.

    Args:
        symbol: Stock ticker symbol

    Returns list of earnings reports with EPS, report date,
    analyst estimates, and actual vs expected.
    """
    _ensure_logged_in()
    return get_earnings(symbol)


@mcp.tool()
def robinhood_get_ratings(symbol: str) -> dict:
    """Get analyst ratings summary for a stock.

    Args:
        symbol: Stock ticker symbol

    Returns ratings summary with buy, hold, sell counts,
    and overall recommendation.
    """
    _ensure_logged_in()
    return get_ratings(symbol)


@mcp.tool()
def robinhood_get_dividends() -> list:
    """Get all dividend payments received.

    Returns list of dividend payments with amount, payable date,
    record date, and instrument details.
    """
    _ensure_logged_in()
    return get_dividends()


@mcp.tool()
def robinhood_get_options_positions() -> list:
    """Get all current options positions (read-only).

    Returns list of options positions with chain symbol, type,
    strike price, expiration, and quantity.
    """
    _ensure_logged_in()
    return get_options_positions()


@mcp.tool()
def robinhood_search_symbols(query: str) -> list:
    """Search for stock symbols by company name or ticker.

    Args:
        query: Search query (company name or partial ticker)

    Returns list of matching instruments with symbol, name,
    and other details.
    """
    _ensure_logged_in()
    return search_symbols(query)


# ---------------------------------------------------------------------------
# Futures tools (new)
# ---------------------------------------------------------------------------


@mcp.tool()
def robinhood_get_futures_positions() -> list:
    """Get current futures positions (derived from filled order history).

    Since Robinhood's futures positions endpoint hasn't been publicly
    discovered, this derives open positions by netting all filled buy
    and sell orders per contract symbol.

    Returns list of positions with symbol, side (long/short), quantity,
    avg_entry_price, current_price, unrealized_pnl, multiplier, and fees.
    """
    _ensure_logged_in()
    return get_futures_positions()


@mcp.tool()
def robinhood_get_futures_orders(order_state: str = "") -> list:
    """Get futures order history with automatic pagination.

    Args:
        order_state: Filter by state (FILLED, CANCELLED, REJECTED, etc.).
                     Empty string = all orders.

    Returns list of order dicts with orderId, orderState, orderLegs,
    quantity, averagePrice, realizedPnl, fees, and timestamps.
    """
    _ensure_logged_in()
    state = order_state if order_state else None
    return get_futures_orders(order_state=state)


@mcp.tool()
def robinhood_get_futures_quote(symbol: str) -> dict:
    """Get real-time quote for a futures contract.

    Args:
        symbol: Futures symbol (e.g., 'ESM26', '/ESM26', 'NQM26', 'GCG26')

    Returns quote data with bid/ask prices and sizes, last trade price,
    market state, and update timestamp.
    """
    _ensure_logged_in()
    return get_futures_quote(symbol)


@mcp.tool()
def robinhood_get_futures_pnl() -> dict:
    """Get aggregate realized P&L from all futures trades.

    Calculates total P&L from CLOSING orders only (to avoid double-counting).

    Returns dict with total_pnl, total_pnl_without_fees, total_fees,
    total_commissions, total_gold_savings, and num_closing_orders.
    """
    _ensure_logged_in()
    return get_futures_pnl()


@mcp.tool()
def robinhood_get_futures_contract(symbol: str) -> dict:
    """Get futures contract details by symbol.

    Args:
        symbol: Futures symbol (e.g., 'ESM26', '/ESM26')

    Returns contract details including id, symbol, description,
    multiplier, expiration, tradability, and state.
    """
    _ensure_logged_in()
    result = get_futures_contract(symbol)
    if result is None:
        raise RobinhoodError(f"No futures contract found for symbol: {symbol}")
    return result


def main() -> None:
    """Run the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
