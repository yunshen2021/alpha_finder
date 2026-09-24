"""Project-wide paths and defaults."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"          # every data file lives here, shared by all versions
CACHE_DIR = DATA_DIR                       # the price loader writes into data/prices/
UNIVERSE_DIR = DATA_DIR / "universe"       # S&P 500 membership snapshots (tracked in git)

DEFAULT_START = "2010-01-01"
BENCHMARK_TICKER = "QQQ"
