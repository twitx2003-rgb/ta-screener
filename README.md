# ta-screener

A local, Hebrew-language website for screening US stocks above $1B market cap by
technical analysis. It filters on:

- chart patterns and candlestick patterns, following Thomas Bulkowski's encyclopedias;
- indicators.

The data comes from [TradingView's MCP server](https://mcp.tradingview.com/mcp), which
needs your own paid TradingView plan.

Personal, non-commercial use. This repository holds code only: no market data, and no
text from the books. See [LICENSES.md](LICENSES.md).

## Setup (Windows, Python 3.14)

```
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe run.py --auth-tradingview    # sign in to tradingview.com in your browser first
.venv\Scripts\python.exe run.py --discover
```

## Status

Phase 0 is done: the project skeleton and the TradingView connection. Still to come:

- the stock universe and daily bars;
- the pattern scan;
- the website.

## Not financial advice

Nothing here is investment advice.
