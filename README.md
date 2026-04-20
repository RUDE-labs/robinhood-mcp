<!-- mcp-name: io.github.verygoodplugins/robinhood-mcp -->

# robinhood-mcp (RUDE-labs fork)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![MCP](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)

A read-only MCP server for Robinhood portfolio research. Wraps [robin_stocks](https://github.com/jmfernandes/robin_stocks) to give AI assistants access to your portfolio data for analysis.

**This fork adds futures support** — positions, quotes, order history, and P&L — on top of the original stocks and options tools from [verygoodplugins/robinhood-mcp](https://github.com/verygoodplugins/robinhood-mcp).

> **⚠️ Research Tool Only** - This server provides read-only access. No trading functionality is exposed.

> **⚠️ Unofficial API** - Uses robin_stocks unofficial API + direct Robinhood futures endpoints. May break without notice. Use at your own risk.

## What's New in This Fork

### Futures Support

Since `robin_stocks` doesn't support futures yet ([PR #1641](https://github.com/jmfernandes/robin_stocks/pull/1641) is open but unmerged), this fork hits Robinhood's futures API directly using the session infrastructure from `robin_stocks`.

**Open positions are derived from filled order history** — the futures positions endpoint hasn't been publicly discovered, so we net all filled buys and sells per contract to determine what's currently open, calculate weighted average entry price, and fetch live quotes for unrealized P&L.

**New tools:**

| Tool | Description |
|------|-------------|
| `robinhood_get_futures_positions` | Open futures positions (derived from order history) with side, qty, avg entry, current price, uPnL |
| `robinhood_get_futures_orders` | Full futures order history with cursor-based pagination. Filterable by state (FILLED, CANCELLED, etc.) |
| `robinhood_get_futures_quote` | Real-time bid/ask/last for any futures contract (e.g., ESM26, NQM26, GCM26) |
| `robinhood_get_futures_pnl` | Aggregate realized P&L from all closed futures trades |
| `robinhood_get_futures_contract` | Contract details — multiplier, expiration, tradability, state |

**Example conversations:**

- "What futures positions do I have open right now?"
- "Show me my realized P&L on futures this month"
- "Get me a quote on /ESM26"
- "Am I long or short gold futures, and what's my average entry?"

**Technical notes:**

- Futures endpoints require the `Rh-Contract-Protected: true` header (handled automatically)
- Futures accounts are auto-discovered by filtering for `accountType=FUTURES`
- Order pagination uses cursor-based pagination (different from stocks/options)
- Position derivation only counts filled orders and nets buys vs sells per symbol

## Installation (from this fork)

```bash
pip install git+https://github.com/RUDE-labs/robinhood-mcp.git
```

## Configuration

### Environment Variables

```bash
export ROBINHOOD_USERNAME="your_email"
export ROBINHOOD_PASSWORD="your_password"
export ROBINHOOD_TOTP_SECRET="your_2fa_secret"  # Only if you use authenticator app
```

**Note:** If you use Face ID, Touch ID, or passcode login on Robinhood (no authenticator app), you don't need `ROBINHOOD_TOTP_SECRET`.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "robinhood": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/RUDE-labs/robinhood-mcp.git", "robinhood-mcp"],
      "env": {
        "ROBINHOOD_USERNAME": "your_email",
        "ROBINHOOD_PASSWORD": "your_password"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add robinhood -- uvx --from "git+https://github.com/RUDE-labs/robinhood-mcp.git" robinhood-mcp
```

## All Available Tools

### Stocks & Portfolio

| Tool | Description |
|------|-------------|
| `robinhood_get_portfolio` | Portfolio value, equity, buying power, day change |
| `robinhood_get_positions` | All holdings with cost basis, current value, P&L |
| `robinhood_get_position` | One holding by ticker with quantity, value, and P&L |
| `robinhood_get_watchlist` | Stocks in your watchlists |
| `robinhood_get_quote` | Real-time price, bid/ask, volume |
| `robinhood_get_fundamentals` | P/E ratio, market cap, dividend yield, 52-week range |
| `robinhood_get_historicals` | OHLCV price history (day/week/month/year) |
| `robinhood_get_news` | Recent news articles for a symbol |
| `robinhood_get_earnings` | Earnings dates, EPS estimates, actuals |
| `robinhood_get_ratings` | Analyst buy/hold/sell ratings |
| `robinhood_get_dividends` | Dividend payment history |
| `robinhood_get_options_positions` | Current options positions |
| `robinhood_search_symbols` | Search stocks by name or ticker |

### Futures (this fork)

| Tool | Description |
|------|-------------|
| `robinhood_get_futures_positions` | Open positions derived from filled orders |
| `robinhood_get_futures_orders` | Order history with pagination |
| `robinhood_get_futures_quote` | Real-time futures quotes |
| `robinhood_get_futures_pnl` | Aggregate realized P&L |
| `robinhood_get_futures_contract` | Contract details and metadata |

## Limitations

- **Read-only**: Cannot place trades, modify watchlists, or change account settings
- **Unofficial API**: Robinhood may change their API at any time
- **Futures positions are derived**: Since the positions endpoint isn't known, positions are calculated from order history. This should be accurate but may have edge cases with partial fills or transferred positions
- **No real-time streaming**: Quotes are point-in-time, not live feeds
- **Session expiry**: You may need to re-authenticate periodically
- **Rate limits**: Heavy usage may trigger Robinhood's rate limiting

## Security Notes

- Credentials are only used locally to authenticate with Robinhood
- Session tokens are cached in `~/.tokens/robinhood.pickle` by robin_stocks
- Never commit your `.env` file or expose credentials
- This tool cannot execute trades — it's read-only by design

## Credits

Original MCP by [Jack Arturo](https://drunk.support) at [Very Good Plugins](https://verygoodplugins.com).
Futures support added by RUDE-labs, based on API endpoint discovery from [robin_stocks PR #1641](https://github.com/jmfernandes/robin_stocks/pull/1641).

Powered by [robin_stocks](https://github.com/jmfernandes/robin_stocks) and [FastMCP](https://github.com/jlowin/fastmcp).

## License

MIT
