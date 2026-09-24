"""Fund holdings from SEC Form N-PORT filings: free, historical, one snapshot per fiscal quarter.

Why N-PORT: it is the only free source of *past* ETF holdings and weights. The SEC publishes the
last month of each fiscal quarter, so snapshots are quarterly.

The SEC requires automated downloads to identify a contact. The identity is read from the
environment variable SEC_USER_AGENT (for example "Jane Doe jane@example.com") and is never stored
in this repository.
"""
from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"
FILING_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/primary_doc.xml"
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
SEC_PAUSE = 0.15  # SEC allows at most 10 requests a second


def sec_headers() -> dict[str, str]:
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua:
        raise RuntimeError(
            "Set SEC_USER_AGENT to your name and email, e.g. export SEC_USER_AGENT='Jane Doe jane@example.com'. "
            "The SEC requires automated downloads to identify a contact; nothing is stored in the repo."
        )
    return {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}


# --- listing and downloading -------------------------------------------------

def list_nport_filings(cik: int, first_report: str, last_report: str) -> pd.DataFrame:
    """N-PORT-P filings of a registrant with report date in [first_report, last_report]."""
    headers = sec_headers()
    root = requests.get(SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json"), headers=headers, timeout=30).json()
    pages = [root["filings"]["recent"]]
    for extra in root["filings"].get("files", []):
        time.sleep(SEC_PAUSE)
        pages.append(requests.get(SUBMISSIONS_URL.format(name=extra["name"]), headers=headers, timeout=30).json())
    rows = []
    for page in pages:
        for i, form in enumerate(page["form"]):
            if form == "NPORT-P":
                rows.append({"accession": page["accessionNumber"][i], "report_date": page["reportDate"][i],
                             "filing_date": page["filingDate"][i]})
    df = pd.DataFrame(rows)
    keep = (df["report_date"] >= first_report) & (df["report_date"] <= last_report)
    return df[keep].sort_values("report_date").reset_index(drop=True)


RETRY_WAITS = (2, 5, 15, 30, 60)  # seconds; the SEC sometimes answers 503 under load


def _get(cik: int, accession: str, stream: bool = False) -> requests.Response:
    url = FILING_URL.format(cik=cik, acc=accession.replace("-", ""))
    for wait in (*RETRY_WAITS, None):
        time.sleep(SEC_PAUSE)
        try:
            r = requests.get(url, headers=sec_headers(), timeout=90, stream=stream)
        except requests.RequestException:
            if wait is None:
                raise
            time.sleep(wait)
            continue
        if r.status_code in (429, 500, 502, 503, 504) and wait is not None:
            r.close()
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError("unreachable")


def read_header(cik: int, accession: str, max_bytes: int = 262_144) -> dict[str, str]:
    """Series name/id and report date, reading only as much of the filing as needed.

    Keeps reading until the series name has been seen (or `max_bytes`), so a short first
    network chunk can never make a filing look like it belongs to no series.
    """
    r = _get(cik, accession, stream=True)
    head = ""
    for chunk in r.iter_content(16_384):
        head += chunk.decode("utf-8", "ignore")
        if re.search(r"<(?:\w+:)?repPdDate>", head) or len(head) >= max_bytes:
            break
    r.close()

    def grab(tag):
        m = re.search(rf"<(?:\w+:)?{tag}>([^<]*)<", head)
        return m.group(1).strip() if m else ""
    out = {"series_name": grab("seriesName"), "series_id": grab("seriesId"), "report_date": grab("repPdDate")}
    if not out["series_name"]:
        raise RuntimeError(f"could not read the series name of filing {accession}")
    return out


def download_filing(cik: int, accession: str, dest: Path) -> Path:
    """Save a filing's XML (skipped if already downloaded)."""
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(_get(cik, accession).content)
    return dest


# --- parsing ---------------------------------------------------------------

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el: ET.Element, name: str) -> str:
    for child in el:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _identifier(el: ET.Element, kind: str) -> str:
    for ids in el:
        if _local(ids.tag) == "identifiers":
            for ident in ids:
                if _local(ident.tag) == kind:
                    return ident.attrib.get("value", "").strip()
    return ""


def parse_nport(xml: bytes) -> tuple[dict, pd.DataFrame]:
    """(fund info, holdings) from an N-PORT XML file. Namespaces are ignored on purpose."""
    root = ET.fromstring(xml)
    meta = {"series_name": "", "series_id": "", "report_date": "", "net_assets": float("nan")}
    rows = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "seriesName":
            meta["series_name"] = (el.text or "").strip()
        elif name == "seriesId":
            meta["series_id"] = (el.text or "").strip()
        elif name == "repPdDate":
            meta["report_date"] = (el.text or "").strip()
        elif name == "netAssets" and meta["net_assets"] != meta["net_assets"]:
            meta["net_assets"] = float(el.text or "nan")
        elif name == "invstOrSec":
            rows.append({
                "name": _text(el, "name"), "title": _text(el, "title"), "cusip": _text(el, "cusip"),
                "isin": _identifier(el, "isin"), "ticker": _identifier(el, "ticker"),
                "balance": float(_text(el, "balance") or "nan"), "units": _text(el, "units"),
                "val_usd": float(_text(el, "valUSD") or "nan"), "pct_val": float(_text(el, "pctVal") or "nan"),
                "asset_cat": _text(el, "assetCat"), "country": _text(el, "invCountry"),
            })
    return meta, pd.DataFrame(rows)


def equity_holdings(df: pd.DataFrame) -> pd.DataFrame:
    """Common stock only (drops cash, futures, money-market funds)."""
    return df[(df["asset_cat"] == "EC") & (df["pct_val"] > 0)].reset_index(drop=True)


# --- identifiers -> tickers --------------------------------------------------
#
# OpenFIGI, asked by ISIN, sometimes returns only foreign listings of a US stock (Honeywell comes
# back as "HONGBP"). Asking by CUSIP restricted to the US composite exchange avoids that, so jobs
# are tried in order: CUSIP on "US", then ISIN on "US", then ISIN on any exchange.

US_COMPOSITE = "US"
US_EXCHANGES = {"UN", "UQ", "UW", "UR", "UA", "UP"}


def pick_us_ticker(figi_rows: list[dict]) -> str | None:
    """Choose the US common-stock ticker from OpenFIGI results (Yahoo style: BRK/B -> BRK-B)."""
    common = [r for r in figi_rows if r.get("marketSector") == "Equity"
              and r.get("securityType") in ("Common Stock", "REIT", "ADR", "Depositary Receipt", "Ltd Part",
                                            "NY Reg Shrs", "MLP")]
    pool = common or figi_rows
    for pref in ([US_COMPOSITE], US_EXCHANGES):
        for r in pool:
            if r.get("exchCode") in pref and r.get("ticker"):
                return clean_ticker(r["ticker"])
    return None


def clean_ticker(raw: str) -> str:
    """Filing tickers come in several styles ('AAPL', 'AAPL US', 'BRK/B'): return Yahoo style."""
    if not raw or raw.strip().upper() in {"N/A", "NA", "NONE", "0"}:
        return ""
    return raw.strip().split()[0].upper().replace("/", "-").replace(".", "-")


def _valid_cusip(c: str) -> bool:
    return len(c) == 9 and c.isalnum() and c != "000000000"


def lookup_jobs(cusip: str, isin: str) -> list[dict]:
    jobs = []
    if _valid_cusip(cusip):
        jobs.append({"idType": "ID_CUSIP", "idValue": cusip, "exchCode": US_COMPOSITE})
    if isin:
        jobs.append({"idType": "ID_ISIN", "idValue": isin, "exchCode": US_COMPOSITE})
        jobs.append({"idType": "ID_ISIN", "idValue": isin})
    return jobs


def figi_query(jobs: list[dict]) -> list[dict]:
    """POST up to 10 jobs to OpenFIGI (no API key), respecting its 25-requests-a-minute limit."""
    for _ in range(6):
        resp = requests.post(OPENFIGI_URL, json=jobs, timeout=60)
        if resp.status_code == 429:
            time.sleep(20)
            continue
        resp.raise_for_status()
        time.sleep(2.6)
        return resp.json()
    raise RuntimeError("OpenFIGI kept rate-limiting; try again in a minute")


def resolve_tickers(securities: list[tuple[str, str]], query=figi_query) -> dict[tuple[str, str], str]:
    """{(cusip, isin): ticker or ""} trying each security's jobs in order until one resolves."""
    pending = {sec: lookup_jobs(*sec) for sec in securities}
    found = {sec: "" for sec in securities}
    while any(pending.values()):
        batch = [(sec, jobs.pop(0)) for sec, jobs in pending.items() if jobs]
        for i in range(0, len(batch), 10):
            part = batch[i:i + 10]
            for (sec, _), result in zip(part, query([job for _, job in part])):
                ticker = pick_us_ticker(result.get("data", [])) if "data" in result else None
                if ticker:
                    found[sec] = ticker
                    pending[sec] = []
    return found


def map_to_tickers(holdings: pd.DataFrame, cache_csv: Path, query=figi_query) -> pd.DataFrame:
    """Add a `symbol` column. Uses the filing's own ticker if present, else OpenFIGI.

    Lookups are cached in `cache_csv` (key: cusip|isin). Securities that previously resolved to
    nothing are looked up again, so an improved lookup can fill them in.
    """
    cache = pd.read_csv(cache_csv, dtype=str).fillna("") if cache_csv.exists() else \
        pd.DataFrame(columns=["key", "symbol"])
    if "key" in cache and cache["key"].str.contains(r"^(?:ISIN|CUSIP):", regex=True).any():
        cache = pd.DataFrame(columns=["key", "symbol"])  # old cache format
    known = {k: v for k, v in zip(cache["key"], cache["symbol"]) if v}

    def key(r) -> str:
        return f"{r['cusip']}|{r['isin']}"

    todo = sorted({(r["cusip"], r["isin"]) for _, r in holdings.iterrows()
                   if not clean_ticker(r["ticker"]) and f"{r['cusip']}|{r['isin']}" not in known})
    if todo:
        known.update({f"{c}|{i}": t for (c, i), t in resolve_tickers(todo, query).items()})
        cache_csv.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(sorted(known.items()), columns=["key", "symbol"]).to_csv(cache_csv, index=False)
    out = holdings.copy()
    out["symbol"] = [clean_ticker(r["ticker"]) or known.get(key(r), "") for _, r in holdings.iterrows()]
    return out


# --- name matching (for CUSIPs that changed or were retired) ------------------------------
#
# A stock split, re-domicile or delisting can retire a CUSIP; OpenFIGI then no longer maps it.
# Such rows are matched by issuer name: first against rows that did resolve (the same company in
# another snapshot), then against the SEC's list of current registrants, then against a short
# hand-written table. Names shared by several share classes (Alphabet, Fox, News Corp) are never
# matched by name, because the name cannot tell the classes apart.

_SUFFIXES = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC",
             "HOLDINGS", "HOLDING", "NV", "SA", "AG", "SE", "LP", "THE", "CLASS", "A", "B", "C", "COM", "NEW",
             "ADR", "SPONSORED", "ORD", "SHS", "REG"}


def normalize_name(name: str) -> str:
    words = re.sub(r"[^A-Z0-9 ]", " ", name.upper().replace("&", " AND ")).split()
    return " ".join(w for w in words if w not in _SUFFIXES)


def match_by_name(holdings: pd.DataFrame, sec_titles: dict[str, str] | None = None,
                  manual: dict[str, str] | None = None) -> pd.DataFrame:
    """Fill empty `symbol`s by issuer name. Adds `symbol_source` saying how each row was mapped."""
    out = holdings.copy()
    out["norm"] = out["name"].map(normalize_name)
    out["symbol_source"] = out["symbol"].map(lambda v: "identifier" if v else "")
    classes = out.groupby(["snapshot_date", "norm"])["cusip"].nunique()
    multi_class = set(classes[classes > 1].index.get_level_values("norm"))
    resolved = out[out.symbol != ""].groupby("norm")["symbol"].unique()
    internal = {n: syms[0] for n, syms in resolved.items() if len(syms) == 1 and n not in multi_class}

    sec_map: dict[str, str] = {}
    if sec_titles:
        by_name: dict[str, set] = {}
        for title, ticker in sec_titles.items():
            by_name.setdefault(normalize_name(title), set()).add(ticker)
        sec_map = {n: next(iter(t)) for n, t in by_name.items() if len(t) == 1}
    manual_norm = {normalize_name(k): v for k, v in (manual or {}).items()}

    for i, r in out[out.symbol == ""].iterrows():
        n = r["norm"]
        if n in multi_class:
            continue
        for source, table in (("same company, other snapshot", internal), ("SEC company list", sec_map),
                              ("manual table", manual_norm)):
            if n in table:
                out.at[i, "symbol"] = clean_ticker(table[n])
                out.at[i, "symbol_source"] = source
                break
    return out.drop(columns="norm")
