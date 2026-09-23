"""The local website: a Hebrew (RTL) screener over the newest scan.

It only reads what `run.py --scan` wrote to data/ and never calls TradingView,
so it can stay open while bars are being fetched. It listens on 127.0.0.1 only.
"""
