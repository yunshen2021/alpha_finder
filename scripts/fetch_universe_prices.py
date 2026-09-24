"""Download prices for every ticker that was ever an S&P 500 member since 2010, plus QQQ."""
import logging
import time

from alpha_finder.data.prices import load_prices
from alpha_finder.universe.sp500 import SP500History

logging.basicConfig(level=logging.WARNING)

START, END = "2010-01-01", "2026-09-23"

if __name__ == "__main__":
    hist = SP500History.load()
    tickers = sorted(hist.all_tickers("2010-12-31", END)) + ["QQQ"]
    print(f"{len(tickers)} tickers", flush=True)
    t0 = time.time()
    prices = load_prices(tickers, START, END)
    print(f"loaded {len(prices)} of {len(tickers)} in {time.time() - t0:.0f}s", flush=True)
