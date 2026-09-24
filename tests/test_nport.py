import pandas as pd
import pytest

from alpha_finder.data.nport import equity_holdings, parse_nport, pick_us_ticker, map_to_tickers

SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport" xmlns:com="http://www.sec.gov/edgar/common">
  <formData>
    <genInfo>
      <seriesName>Vanguard Information Technology Index Fund</seriesName>
      <seriesId>S000000001</seriesId>
      <repPdEnd>2024-10-31</repPdEnd>
      <repPdDate>2024-07-31</repPdDate>
    </genInfo>
    <fundInfo><totAssets>1000</totAssets><netAssets>990.5</netAssets></fundInfo>
    <invstOrSecs>
      <invstOrSec>
        <name>APPLE INC</name><lei>X</lei><title>APPLE INC</title><cusip>037833100</cusip>
        <identifiers><isin value="US0378331005"/></identifiers>
        <balance>10</balance><units>NS</units><curCd>USD</curCd><valUSD>200.5</valUSD><pctVal>20.25</pctVal>
        <assetCat>EC</assetCat><issuerCat>CORP</issuerCat><invCountry>US</invCountry>
      </invstOrSec>
      <invstOrSec>
        <name>NVIDIA CORP</name><title>NVIDIA CORP</title><cusip>67066G104</cusip>
        <identifiers><ticker value="NVDA"/></identifiers>
        <valUSD>150</valUSD><pctVal>15.1</pctVal><assetCat>EC</assetCat><invCountry>US</invCountry>
      </invstOrSec>
      <invstOrSec>
        <name>E-MINI FUTURE</name><title>E-MINI</title><cusip>000000000</cusip>
        <valUSD>5</valUSD><pctVal>0.5</pctVal><assetCat>DE</assetCat>
      </invstOrSec>
      <invstOrSec>
        <name>SHORT TERM CASH FUND</name><title>MMF</title>
        <valUSD>9</valUSD><pctVal>0.9</pctVal><assetCat>STIV</assetCat>
      </invstOrSec>
    </invstOrSecs>
  </formData>
</edgarSubmission>"""


def test_parse_reads_header_and_holdings_ignoring_namespaces():
    meta, df = parse_nport(SAMPLE)
    assert meta["series_name"] == "Vanguard Information Technology Index Fund"
    assert meta["report_date"] == "2024-07-31"  # the as-of date, not the fiscal year end
    assert meta["net_assets"] == pytest.approx(990.5)
    assert len(df) == 4
    apple = df[df.name == "APPLE INC"].iloc[0]
    assert apple["isin"] == "US0378331005" and apple["pct_val"] == pytest.approx(20.25)
    assert df[df.name == "NVIDIA CORP"].iloc[0]["ticker"] == "NVDA"


def test_equity_filter_drops_futures_and_cash():
    _, df = parse_nport(SAMPLE)
    eq = equity_holdings(df)
    assert set(eq.name) == {"APPLE INC", "NVIDIA CORP"}


def test_pick_us_ticker_prefers_us_composite_common_stock():
    rows = [
        {"ticker": "AAPL", "exchCode": "UW", "marketSector": "Equity", "securityType": "Common Stock"},
        {"ticker": "AAPL", "exchCode": "US", "marketSector": "Equity", "securityType": "Common Stock"},
        {"ticker": "APC", "exchCode": "GR", "marketSector": "Equity", "securityType": "Common Stock"},
    ]
    assert pick_us_ticker(rows) == "AAPL"
    assert pick_us_ticker([{"ticker": "BRK/B", "exchCode": "US", "marketSector": "Equity",
                            "securityType": "Common Stock"}]) == "BRK-B"
    assert pick_us_ticker([{"ticker": "X", "exchCode": "GR", "marketSector": "Equity",
                            "securityType": "Common Stock"}]) is None
    assert pick_us_ticker([]) is None


def test_mapping_uses_filing_ticker_and_cache_without_network(tmp_path):
    _, df = parse_nport(SAMPLE)
    eq = equity_holdings(df)
    cache = tmp_path / "map.csv"
    pd.DataFrame([{"key": "037833100|US0378331005", "symbol": "AAPL"}]).to_csv(cache, index=False)

    def no_network(jobs):
        raise AssertionError("should not query")

    out = map_to_tickers(eq, cache, query=no_network)  # NVDA has its own ticker, AAPL is cached
    assert dict(zip(out.name, out.symbol)) == {"APPLE INC": "AAPL", "NVIDIA CORP": "NVDA"}


def test_parse_keeps_share_balance():
    _, df = parse_nport(SAMPLE)
    apple = df[df.name == "APPLE INC"].iloc[0]
    assert apple["balance"] == 10 and apple["units"] == "NS"


def test_lookup_tries_cusip_on_us_first_then_isin():
    from alpha_finder.data.nport import resolve_tickers

    calls = []

    def fake(jobs):
        calls.append(jobs)
        out = []
        for j in jobs:
            if j["idType"] == "ID_CUSIP":  # CUSIP lookup finds nothing for this security
                out.append({"warning": "No identifier found."})
            elif j.get("exchCode") == "US":
                out.append({"data": [{"ticker": "HON", "exchCode": "US", "marketSector": "Equity",
                                      "securityType": "Common Stock"}]})
            else:
                out.append({"data": [{"ticker": "HONGBP", "exchCode": "X1", "marketSector": "Equity",
                                      "securityType": "Common Stock"}]})
        return out

    got = resolve_tickers([("438516106", "US4385161066")], query=fake)
    assert got == {("438516106", "US4385161066"): "HON"}
    assert [j[0]["idType"] for j in calls] == ["ID_CUSIP", "ID_ISIN"]  # stopped once resolved
    assert calls[1][0]["exchCode"] == "US"


def test_foreign_only_listing_is_not_accepted():
    from alpha_finder.data.nport import resolve_tickers

    def foreign_only(jobs):
        return [{"data": [{"ticker": "LRCXEUR", "exchCode": "X1", "marketSector": "Equity",
                           "securityType": "Common Stock"}]} for _ in jobs]

    assert resolve_tickers([("512807108", "US5128071082")], query=foreign_only) == {("512807108", "US5128071082"): ""}


def test_clean_ticker_styles():
    from alpha_finder.data.nport import clean_ticker
    assert clean_ticker("AAPL") == "AAPL"
    assert clean_ticker("aapl US") == "AAPL"
    assert clean_ticker("BRK/B") == "BRK-B"
    assert clean_ticker("BRK.B") == "BRK-B"
    assert clean_ticker("N/A") == "" and clean_ticker("") == ""


def _rows(entries):
    return pd.DataFrame([{"snapshot_date": d, "name": n, "cusip": c, "symbol": sym} for d, n, c, sym in entries])


def test_name_match_uses_same_company_in_other_snapshot():
    from alpha_finder.data.nport import match_by_name
    df = _rows([("2024-06-30", "Lam Research Corp.", "512807108", ""),      # pre-split CUSIP
                ("2024-12-31", "Lam Research Corp", "512807306", "LRCX")])  # post-split CUSIP
    out = match_by_name(df)
    assert list(out.symbol) == ["LRCX", "LRCX"]
    assert out.symbol_source.iloc[0] == "same company, other snapshot"


def test_name_match_sec_list_and_manual_table():
    from alpha_finder.data.nport import match_by_name
    df = _rows([("2024-06-30", "Honeywell International Inc.", "438516106", ""),
                ("2024-06-30", "Splunk Inc", "848637104", "")])
    out = match_by_name(df, sec_titles={"HONEYWELL INTERNATIONAL INC": "HON"}, manual={"Splunk Inc.": "SPLK"})
    assert dict(zip(out.name, out.symbol)) == {"Honeywell International Inc.": "HON", "Splunk Inc": "SPLK"}
    assert list(out.symbol_source) == ["SEC company list", "manual table"]


def test_name_match_never_guesses_between_share_classes():
    from alpha_finder.data.nport import match_by_name
    df = _rows([("2024-06-30", "Alphabet Inc.", "02079K305", ""),
                ("2024-06-30", "Alphabet Inc.", "02079K107", "GOOG")])
    out = match_by_name(df, sec_titles={"Alphabet Inc.": "GOOGL"})
    assert out.symbol.iloc[0] == ""  # two classes share the name: leave it for a human to decide
