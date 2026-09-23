# Licences and usage limits

This project is for **personal, non-commercial research**. It is published as source
code only: no market data, and no text from the books it follows.

| Component | Licence | Practical limits |
|---|---|---|
| **TradingView MCP** (hosted service) | TradingView Terms of Use | Requires a paid TradingView plan (Essential or higher). Public beta: tool names and schemas may change. OAuth 2.1, no API key. The data it returns is for the account holder's own use. This repository holds none of it: `data/` and `logs/` are gitignored. The website binds to 127.0.0.1. **The owner chose (2026-09-23) to expose the site publicly through a VS Code tunnel, knowing this conflicts with that limit and puts the TradingView account at risk**; closing the tunnel ends it. |
| **mcp** (Python SDK) 2.2.0 | MIT | Client library only; TradingView's terms govern the data. |
| **Claude Code** (the owner's subscription) | Anthropic's terms | Writes the channel posts of the simulated members, which the site labels as AI agents; run from a normal terminal (`run.py --channels`). No API key is used. |
| **pandas**, **numpy**, **pyarrow**, **PyYAML** | BSD-3-Clause / BSD-3-Clause / Apache-2.0 / MIT | — |
| **Code taken from market-research-pipeline** (TradingView client, contracts, market calendar) | Same author | Copied, not a dependency. |
| **FastAPI**, **Starlette**, **uvicorn**, **Jinja2** / **MarkupSafe**, **httpx** (tests) | MIT / BSD-3-Clause / BSD-3-Clause / BSD-3-Clause / BSD-3-Clause | The website. |
| **TradingView Lightweight Charts™** 5.2.1 | Apache-2.0 | Vendored unchanged in `tascreen/web/static/vendor/`, with its `LICENSE` and `NOTICE`. The licence requires the NOTICE line and a link to https://www.tradingview.com/ on the page: every page footer carries both, and the chart keeps its TradingView logo (`attributionLogo`). |
| **Fonts**: Frank Ruhl Libre, IBM Plex Sans Hebrew, IBM Plex Mono, Amatic SC (the handwriting on channel charts) | SIL Open Font License 1.1 | Loaded from Google Fonts by the browser, not stored in the repo. |


## Bulkowski's books

The pattern definitions follow Thomas N. Bulkowski:

- *Encyclopedia of Chart Patterns*
- *Encyclopedia of Candlestick Charts*

His website, thepatternsite.com, is also used as a reference. Both books are under
copyright. This repository therefore contains **only our own wording** of each
identification rule and its parameters, with a reference to the chapter or page. It
contains **no quoted text and none of the books' performance statistics**. Parameters
not yet checked against the book are flagged `confirmed_from_book: false`.

## Not financial advice

Everything the screener shows is a mechanical reading of past prices. A measure-rule
target is the book's rule of thumb applied to a pattern's height, not a forecast. A
detected pattern is not a recommendation to buy or sell anything.
