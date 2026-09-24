"""Get QQQ and VGT holdings snapshots from SEC N-PORT filings and save them as small CSV files.

Two steps:
  download  needs a contact for the SEC (their rule for automated downloads), read from the environment:
                export SEC_USER_AGENT="Your Name your.email@example.com"
            Saves the raw XML filings in data/etf_holdings_raw/<ETF>/ (not tracked in git).
  build     parses the saved filings, maps each holding to a ticker (OpenFIGI), and writes
            data/etf_holdings/<ETF>_snapshots.csv (tracked, small). No SEC access needed.

    python version_3_etf_topn/fetch_holdings.py            # both steps
    python version_3_etf_topn/fetch_holdings.py --build    # build only, from saved filings
"""
import argparse
import json
import sys

import pandas as pd

from alpha_finder.config import DATA_DIR
from alpha_finder.data.nport import (
    download_filing, equity_holdings, list_nport_filings, map_to_tickers, match_by_name, parse_nport,
    read_header,
)

RAW = DATA_DIR / "etf_holdings_raw"
OUT = DATA_DIR / "etf_holdings"
SEC_LIST = OUT / "sec_company_tickers.json"  # SEC's list of current registrants (name -> ticker)

# Holdings whose CUSIP no longer maps anywhere (delisted, or retired) and that have no match in the SEC's
# current list either. Each entry was checked by hand; the price audit in run.py re-verifies every one.
MANUAL_TICKERS = {
    "Thomson Reuters Corp.": "TRI",          # SEC list names it "THOMSON REUTERS CORP /CAN/"
    "Electronic Arts Inc.": "EA",            # taken private in 2026, so its CUSIP no longer maps
    "Splunk Inc.": "SPLK",                   # acquired by Cisco, March 2024
    "ANSYS, Inc.": "ANSS",                   # acquired by Synopsys, July 2025
    "Seagen Inc.": "SGEN",                   # acquired by Pfizer, December 2023
    "Juniper Networks Inc": "JNPR",          # acquired by HPE, July 2025
    "Confluent Inc": "CFLT",                 # acquired by IBM in 2026
    "Aspen Technology Inc": "AZPN",          # bought out by Emerson, March 2025
    "Aspentech Corp": "AZPN",                # same company, later filing name
    "Altair Engineering Inc": "ALTR",        # acquired by Siemens, March 2025
    # 2019-2023 holdings whose CUSIPs are retired
    "Activision Blizzard, Inc.": "ATVI",     # acquired by Microsoft, October 2023
    "Xilinx, Inc.": "XLNX",                  # acquired by AMD (all stock), February 2022
    "Xilinx Inc": "XLNX",
    "Seattle Genetics, Inc.": "SGEN",        # renamed Seagen in 2020; acquired by Pfizer, December 2023
    "Cerner Corp.": "CERN",                  # acquired by Oracle, June 2022
    "Marvell Technology Group Ltd.": "MRVL", # moved from Bermuda to Delaware in 2021, same ticker
    "Marvell Technology Group Ltd": "MRVL",
    "Citrix Systems, Inc.": "CTXS",          # taken private, September 2022
    "Citrix Systems Inc": "CTXS",
    "Maxim Integrated Products Inc": "MXIM", # acquired by Analog Devices (all stock), August 2021
    "Maxim Integrated Products, Inc.": "MXIM",
    "VMware Inc": "VMW",                     # acquired by Broadcom (cash and stock), November 2023
    "VMware Inc Class A": "VMW",
    "Nuance Communications Inc": "NUAN",     # acquired by Microsoft, March 2022
    "Alexion Pharmaceuticals, Inc.": "ALXN", # acquired by AstraZeneca (cash and ADSs), July 2021
    "FleetCor Technologies Inc.": "CPAY",    # renamed Corpay (CPAY) in 2024
    "FleetCor Technologies Inc": "CPAY",
    "Coupa Software Inc": "COUP",            # taken private, February 2023
    "Coupa Software Inc.": "COUP",
    "Zendesk Inc": "ZEN",                    # taken private, November 2022
    "Zendesk Inc.": "ZEN",
}

# Need the snapshot in force on the first purchase (early Jan 2024) through the last (Dec 31, 2025).
FIRST_REPORT, LAST_REPORT = "2019-06-01", "2025-12-31"

ETFS = {
    "QQQ": {"cik": 1067839, "series_contains": None},  # Invesco QQQ Trust: one fund
    "VGT": {"cik": 52848, "series_contains": "information technology index"},  # one of ~30 Vanguard World Fund series
}


def download(etf: str, cik: int, series_contains: str | None) -> None:
    folder = RAW / etf
    folder.mkdir(parents=True, exist_ok=True)
    index_path = folder / "_series_index.json"  # accession -> series name, so headers are read once
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    filings = list_nport_filings(cik, FIRST_REPORT, LAST_REPORT)
    print(f"{etf}: {len(filings)} N-PORT filings by the registrant in range", flush=True)
    kept = 0
    for _, f in filings.iterrows():
        if series_contains is not None:
            if f.accession not in index:
                index[f.accession] = read_header(cik, f.accession)["series_name"]
                index_path.write_text(json.dumps(index, indent=1))
            if series_contains not in index[f.accession].lower():
                continue
        download_filing(cik, f.accession, folder / f"{f.report_date}.xml")
        kept += 1
    print(f"{etf}: {kept} filings saved", flush=True)


def build(etf: str) -> pd.DataFrame:
    frames = []
    for path in sorted((RAW / etf).glob("*.xml")):
        meta, df = parse_nport(path.read_bytes())
        eq = equity_holdings(df)
        eq.insert(0, "snapshot_date", meta["report_date"] or path.stem)
        frames.append(eq)
    if not frames:
        sys.exit(f"no saved filings for {etf}; run the download step first")
    snaps = pd.concat(frames, ignore_index=True)
    snaps = map_to_tickers(snaps, OUT / "id_to_ticker.csv")
    sec_titles = {v["title"]: v["ticker"] for v in json.loads(SEC_LIST.read_text()).values()} if SEC_LIST.exists() else {}
    snaps = match_by_name(snaps, sec_titles, MANUAL_TICKERS)
    ns = (snaps["units"] == "NS") & (snaps["balance"] > 0)
    snaps["price"] = (snaps["val_usd"] / snaps["balance"]).where(ns)  # the fund's own valuation per share
    per_snap = snaps.groupby("snapshot_date").agg(holdings=("name", "size"), weight=("pct_val", "sum"))
    unmapped = snaps[snaps.symbol == ""]
    print(f"\n{etf}: {len(per_snap)} snapshots")
    print(per_snap.round(2).to_string())
    print(f"{etf}: unmapped holdings: {len(unmapped)} rows, "
          f"{unmapped.pct_val.sum() / len(per_snap):.3f}% average weight per snapshot")
    if len(unmapped):
        print(unmapped.groupby("name").pct_val.max().sort_values(ascending=False).head(10).to_string())
    print(f"{etf}: how rows were mapped:", snaps.symbol_source.replace("", "UNMAPPED").value_counts().to_dict())
    keep = ["snapshot_date", "symbol", "symbol_source", "name", "pct_val", "val_usd", "balance", "price", "isin", "cusip"]
    OUT.mkdir(parents=True, exist_ok=True)
    snaps[keep].to_csv(OUT / f"{etf}_snapshots.csv", index=False)
    return snaps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="skip the download step")
    args = ap.parse_args()
    for etf, spec in ETFS.items():
        if not args.build:
            download(etf, spec["cik"], spec["series_contains"])
        build(etf)


if __name__ == "__main__":
    main()
