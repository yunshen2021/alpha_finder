"""Point-in-time S&P 500 membership rebuilt from Wikipedia.

Start from today's constituents and undo every index change dated after the
requested day (drop what was added, restore what was removed). The raw tables
are snapshotted to CSV so results can be reproduced after Wikipedia changes.

Known limits (reported in the backtest report, not hidden):
- A ticker rename that Wikipedia does not list as a change can leave a company
  in the membership too early. `reconstruction_diagnostics` counts these.
- Removed companies with no Yahoo price history (bankrupt, acquired) cannot be
  traded, so the tradable universe still has some survivorship bias.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests

from alpha_finder.config import PROJECT_ROOT

SNAPSHOT_DIR = PROJECT_ROOT / "snapshots"
CURRENT_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
CHANGES_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"
_HEADERS = {"User-Agent": "alpha-finder-research/0.1 (personal research project)"}


def fetch_snapshot(dest: Path = SNAPSHOT_DIR) -> None:
    """Download the current list and the change history and save them as CSV."""
    dest.mkdir(parents=True, exist_ok=True)

    html = requests.get(CURRENT_URL, headers=_HEADERS, timeout=60).text
    current = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})[0]
    current = current.rename(columns={"Symbol": "ticker", "Security": "name", "GICS Sector": "sector"})
    current[["ticker", "name", "sector"]].to_csv(dest / "sp500_current.csv", index=False)

    html = requests.get(CHANGES_URL, headers=_HEADERS, timeout=60).text
    raw = pd.read_html(io.StringIO(html))[0]
    raw.columns = [
        "date", "added_ticker", "added_name", "removed_ticker", "removed_name", "reason", "refs",
    ]
    raw = raw.iloc[1:] if str(raw.iloc[0]["date"]) == "Effective Date" else raw
    raw[["date", "added_ticker", "added_name", "removed_ticker", "removed_name", "reason"]].to_csv(
        dest / "sp500_changes.csv", index=False
    )


# Companies added under a ticker they later changed. Undoing the addition by the
# old ticker would leave the current ticker in the index years too early, so
# map (effective date, ticker as listed) to the ticker Yahoo knows today.
ADD_ALIASES = {
    ("2018-06-20", "FLT"): "CPAY",   # FleetCor -> Corpay
    ("2017-06-19", "RE"): "EG",      # Everest Re -> Everest Group
    ("2014-05-01", "UA"): "UAA",     # Under Armour class A (class C later took UA)
    ("2013-12-23", "FB"): "META",    # Facebook -> Meta
    ("2013-11-13", "KORS"): "CPRI",  # Michael Kors -> Capri
    ("2012-12-21", "DLPH"): "APTV",  # Delphi Automotive -> Aptiv
}


def _clean_ticker(value) -> str | None:
    if pd.isna(value):
        return None
    text = re.sub(r"[^A-Z0-9.\-]", "", str(value).strip().upper())
    return text or None


def _is_ticker(value) -> bool:
    return isinstance(value, str) and bool(value)


@dataclass
class SP500History:
    current: pd.DataFrame  # ticker, name, sector
    changes: pd.DataFrame  # date, added_ticker, removed_ticker (+ names), sorted newest first

    @classmethod
    def load(cls, snapshot_dir: Path = SNAPSHOT_DIR) -> "SP500History":
        current = pd.read_csv(snapshot_dir / "sp500_current.csv")
        current["ticker"] = current["ticker"].map(_clean_ticker)
        changes = pd.read_csv(snapshot_dir / "sp500_changes.csv")
        changes["date"] = pd.to_datetime(changes["date"], errors="coerce")
        changes = changes.dropna(subset=["date"])
        changes["added_ticker"] = changes["added_ticker"].map(_clean_ticker).astype(object)
        changes["removed_ticker"] = changes["removed_ticker"].map(_clean_ticker).astype(object)
        for (day, listed), current_ticker in ADD_ALIASES.items():
            hit = (changes["date"] == pd.Timestamp(day)) & (changes["added_ticker"] == listed)
            changes.loc[hit, "added_ticker"] = current_ticker
        changes = changes.sort_values("date", ascending=False, kind="stable").reset_index(drop=True)
        return cls(current, changes)

    def members(self, on: str | pd.Timestamp) -> set[str]:
        """Tickers in the index at the close of `on`."""
        on = pd.Timestamp(on)
        members = set(self.current["ticker"])
        for row in self.changes[self.changes["date"] > on].itertuples():
            if _is_ticker(row.added_ticker):
                members.discard(row.added_ticker)
            if _is_ticker(row.removed_ticker):
                members.add(row.removed_ticker)
        return members

    def all_tickers(self, start: str | pd.Timestamp, end: str | pd.Timestamp) -> set[str]:
        """Every ticker that was a member at any point in [start, end]."""
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        seen = self.members(end)
        members = set(seen)
        for row in self.changes[(self.changes["date"] > start) & (self.changes["date"] <= end)].itertuples():
            if _is_ticker(row.added_ticker):
                members.discard(row.added_ticker)
            if _is_ticker(row.removed_ticker):
                members.add(row.removed_ticker)
            seen |= members
        return seen | self.members(start)

    def reconstruction_diagnostics(self, dates: list[pd.Timestamp]) -> pd.DataFrame:
        """Membership size per date; it should stay near 500."""
        return pd.DataFrame({"date": dates, "n_members": [len(self.members(d)) for d in dates]})

    def unmatched_adds(self, since: str | pd.Timestamp) -> list[tuple[str, str]]:
        """Additions since `since` whose ticker is not in the reconstructed set right
        after the addition: usually a ticker rename or later removal we cannot see.
        Returned as (date, ticker) for manual review."""
        since = pd.Timestamp(since)
        out = []
        for row in self.changes[self.changes["date"] > since].itertuples():
            if _is_ticker(row.added_ticker) and row.added_ticker not in self.members(row.date):
                out.append((row.date.date().isoformat(), row.added_ticker))
        return out
