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
.venv\Scripts\python.exe run.py --universe          # US stocks above $1B
.venv\Scripts\python.exe run.py --bars              # daily bars (resumable)
.venv\Scripts\python.exe run.py --scan              # indicators + patterns
.venv\Scripts\python.exe run.py --serve             # the website, http://127.0.0.1:8050/
.venv\Scripts\python.exe run.py --live              # live prices during the session, daily update after
```

## Status

Built:

- the TradingView connection;
- the stock universe, with daily bars;
- the scan: indicators, candlesticks and chart patterns;
- the local website: screener, chart per stock with the pattern drawn and its rule
  checklist, pattern glossary, and data status;
- live prices every few minutes during the session (delayed), with patterns still
  forming whose breakout level the price is crossing. Such a crossing is not final
  until the close.

Still to come: more Claude Code skills and agents.

## Not financial advice

Nothing here is investment advice.
