"""
fred_fetcher.py — FRED macro data fetcher for buy-side research
Usage:
    python3 skills/fred_fetcher.py --series CPIAUCSL
    python3 skills/fred_fetcher.py --series CPIAUCSL UNRATE GS10 --observations 24
    python3 skills/fred_fetcher.py --list-common

Requires: FRED_API_KEY environment variable
    Free key at: https://fredaccount.stlouisfed.org/login/secure/

Common series IDs:
    CPIAUCSL    CPI All Urban Consumers (YoY inflation proxy)
    PCEPI       PCE Price Index
    UNRATE      Unemployment Rate
    GS10        10-Year Treasury Constant Maturity Rate
    GS2         2-Year Treasury Rate
    T10Y2Y      10Y-2Y Treasury Spread (yield curve)
    FEDFUNDS    Federal Funds Effective Rate
    DCOILWTICO  WTI Crude Oil Price (Cushing, OK)
    DHHNGSP     Natural Gas Price (Henry Hub)
    GVZCLS      CBOE Gold ETF Volatility Index
    (Gold spot no longer on FRED — use cme_fetcher.py --commodity gold)

Output: JSON with series metadata, recent observations, and YoY change.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import requests

FRED_BASE = "https://api.stlouisfed.org/fred"

COMMON_SERIES = {
    "CPIAUCSL":  "CPI All Urban Consumers (SA)",
    "PCEPI":     "PCE Price Index",
    "UNRATE":    "Unemployment Rate (%)",
    "GS10":      "10-Year Treasury Rate (%)",
    "GS2":       "2-Year Treasury Rate (%)",
    "T10Y2Y":    "10Y-2Y Yield Spread (%)",
    "FEDFUNDS":  "Fed Funds Effective Rate (%)",
    "DCOILWTICO":"WTI Crude Oil (USD/bbl)",
    "DHHNGSP":   "Natural Gas, Henry Hub (USD/MMBtu)",
    "GVZCLS":    "CBOE Gold ETF Volatility Index (spot gold: use cme_fetcher --commodity gold)",
    "UMCSENT":   "U Michigan Consumer Sentiment",
    "RETAILSMNSA": "Retail Sales (NSA, USD mn)",
    "MORTGAGE30US": "30-Year Fixed Mortgage Rate (%)",
}


def fred_get(endpoint, params, api_key):
    params["api_key"] = api_key
    params["file_type"] = "json"
    r = requests.get(f"{FRED_BASE}/{endpoint}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_series(api_key, series_id, n_obs=24):
    try:
        info = fred_get("series", {"series_id": series_id}, api_key)
        series_info = info.get("seriess", [{}])[0]
    except Exception as e:
        return None, f"series metadata fetch failed: {e}"

    try:
        obs_data = fred_get("series/observations", {
            "series_id":    series_id,
            "sort_order":   "desc",
            "limit":        str(n_obs + 1),
        }, api_key)
        observations = obs_data.get("observations", [])
    except Exception as e:
        return None, f"observations fetch failed: {e}"

    cleaned = []
    for o in observations:
        val = o.get("value", ".")
        cleaned.append({
            "date":  o["date"],
            "value": float(val) if val != "." else None,
        })

    yoy_change = None
    if len(cleaned) >= 13:
        latest = cleaned[0].get("value")
        year_ago = cleaned[12].get("value")
        if latest is not None and year_ago and year_ago != 0:
            yoy_change = round((latest - year_ago) / abs(year_ago) * 100, 2)

    result = {
        "series_id":    series_id,
        "title":        series_info.get("title", ""),
        "units":        series_info.get("units_short", series_info.get("units", "")),
        "frequency":    series_info.get("frequency_short", ""),
        "seasonal_adj": series_info.get("seasonal_adjustment_short", ""),
        "last_updated": series_info.get("last_updated", ""),
        "latest_value": cleaned[0]["value"] if cleaned else None,
        "latest_date":  cleaned[0]["date"] if cleaned else None,
        "yoy_change_pct": yoy_change,
        "observations": cleaned[:n_obs],
    }
    return result, None


def main():
    parser = argparse.ArgumentParser(description="FRED macro data fetcher")
    parser.add_argument("--series",       nargs="+", default=None,
                        help="FRED series ID(s) (e.g. CPIAUCSL UNRATE GS10)")
    parser.add_argument("--observations", type=int, default=24,
                        help="Number of recent observations to return (default: 24)")
    parser.add_argument("--list-common",  action="store_true",
                        help="Print common series IDs and exit")
    args = parser.parse_args()

    if args.list_common:
        print(json.dumps({"common_series": COMMON_SERIES}, indent=2))
        return

    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        print(json.dumps({
            "error": "FRED_API_KEY environment variable not set",
            "instructions": "Get a free key at https://fredaccount.stlouisfed.org/login/secure/ and run: export FRED_API_KEY=your_key",
            "source": "fred_fetcher"
        }))
        sys.exit(1)

    if not args.series:
        print(json.dumps({
            "error": "No series specified. Use --series CPIAUCSL or --list-common to see options.",
            "source": "fred_fetcher"
        }))
        sys.exit(1)

    results = []
    warnings = []

    for sid in args.series:
        data, err = fetch_series(api_key, sid.upper(), args.observations)
        if err:
            warnings.append(f"{sid}: {err}")
        else:
            results.append(data)

    output = {
        "source":       "fred_primary",
        "retrieved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note":         "FRED data is primary-source macro. Verify units before use in analysis.",
        "series":       results,
        "warnings":     warnings,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
