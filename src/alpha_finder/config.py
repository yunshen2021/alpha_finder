"""Project-wide paths and defaults."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = PROJECT_ROOT / "data_cache"

DEFAULT_START = "2010-01-01"
BENCHMARK_TICKER = "QQQ"
