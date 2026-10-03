"""
cme_fetcher.py — Commodity / futures price fetcher for buy-side research
Usage:
    python3 skills/cme_fetcher.py --symbol LE GF HE
    python3 skills/cme_fetcher.py --symbol CL GC SI NG
    python3 skills/cme_fetcher.py --commodity "live cattle" "gold" "crude oil"
    python3 skills/cme_fetcher.py --list

Source: yfinance continuous futures contracts (e.g. LE=F for Live Cattle).
Per §2.1 falsification symmetry rule: these prices qualify as primary-source
commodity verification evidence for use in PM Verdict and falsification arguments.

Common symbols:
    LE   Live Cattle (USD/cwt)
    GF   Feeder Cattle (USD/cwt)
    HE   Lean Hogs (USD/cwt)
    ZC   Corn (USc/bu)
    ZW   Wheat (USc/bu)
    ZS   Soybeans (USc/bu)
    CL   WTI Crude Oil (USD/bbl)
    BZ   Brent Crude (USD/bbl)
    NG   Natural Gas (USD/MMBtu)
    GC   Gold (USD/troy oz)
    SI   Silver (USD/troy oz)
    HG   Copper (USD/lb)
    PL   Platinum (USD/troy oz)
"""

import argparse
import json
import sys
from datetime import datetime, timedelta

try:
    import yfinance as yf
except ImportError:
    print(json.dumps({"error": "yfinance not installed", "source": "cme_fetcher"}))
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    pd = None

SYMBOL_MAP = {
    "LE": {"name": "Live Cattle",    "unit": "USD/cwt",     "exchange": "CME"},
    "GF": {"name": "Feeder Cattle",  "unit": "USD/cwt",     "exchange": "CME"},
    "HE": {"name": "Lean Hogs",      "unit": "USD/cwt",     "exchange": "CME"},
    "ZC": {"name": "Corn",           "unit": "USc/bu",      "exchange": "CBOT"},
    "ZW": {"name": "Wheat",          "unit": "USc/bu",      "exchange": "CBOT"},
    "ZS": {"name": "Soybeans",       "unit": "USc/bu",      "exchange": "CBOT"},
    "CL": {"name": "WTI Crude Oil",  "unit": "USD/bbl",     "exchange": "NYMEX"},
    "BZ": {"name": "Brent Crude",    "unit": "USD/bbl",     "exchange": "ICE"},
    "NG": {"name": "Natural Gas",    "unit": "USD/MMBtu",   "exchange": "NYMEX"},
    "GC": {"name": "Gold",           "unit": "USD/troy oz", "exchange": "COMEX"},
    "SI": {"name": "Silver",         "unit": "USD/troy oz", "exchange": "COMEX"},
    "HG": {"name": "Copper",         "unit": "USD/lb",      "exchange": "COMEX"},
    "PL": {"name": "Platinum",       "unit": "USD/troy oz", "exchange": "NYMEX"},
    "PA": {"name": "Palladium",      "unit": "USD/troy oz", "exchange": "NYMEX"},
    "KC": {"name": "Coffee",         "unit": "USc/lb",      "exchange": "ICE"},
    "CT": {"name": "Cotton",         "unit": "USc/lb",      "exchange": "ICE"},
    "SB": {"name": "Sugar No.11",    "unit": "USc/lb",      "exchange": "ICE"},
}

COMMODITY_ALIASES = {
    "live cattle":  "LE",
    "feeder cattle":"GF",
    "lean hogs":    "HE",
    "hogs":         "HE",
    "beef":         "LE",
    "corn":         "ZC",
    "wheat":        "ZW",
    "soybeans":     "ZS",
    "soy":          "ZS",
    "crude oil":    "CL",
    "wti":          "CL",
    "wti crude":    "CL",
    "wti crude oil":"CL",
    "oil":          "CL",
    "brent":        "BZ",
    "brent crude":  "BZ",
    "brent crude oil": "BZ",
    "natural gas":  "NG",
    "gas":          "NG",
    "gold":         "GC",
    "silver":       "SI",
    "copper":       "HG",
    "platinum":     "PL",
    "palladium":    "PA",
    "coffee":       "KC",
    "cotton":       "CT",
    "sugar":        "SB",
}


def resolve_symbol(s):
    s_upper = s.upper()
    if s_upper in SYMBOL_MAP:
        return s_upper
    s_lower = s.lower()
    if s_lower in COMMODITY_ALIASES:
        return COMMODITY_ALIASES[s_lower]
    return s_upper


def fetch_commodity(symbol):
    yf_ticker = symbol + "=F"
    try:
        t = yf.Ticker(yf_ticker)
        info = t.info

        hist = t.history(period="3mo")
        if hist.empty:
            hist = t.history(period="1mo")

        current = info.get("regularMarketPrice") or info.get("previousClose")
        prev_close = info.get("regularMarketPreviousClose") or info.get("previousClose")

        day_change_pct = None
        if current and prev_close and prev_close != 0:
            day_change_pct = round((current - prev_close) / prev_close * 100, 2)

        year_ago_price = None
        yoy_change_pct = None
        if not hist.empty and pd is not None:
            hist = hist.sort_index()
            if len(hist) >= 20:
                year_ago_price = round(float(hist["Close"].iloc[0]), 4)
                current_hist   = round(float(hist["Close"].iloc[-1]), 4)
                if year_ago_price and year_ago_price != 0:
                    yoy_change_pct = round((current_hist - year_ago_price) / year_ago_price * 100, 2)

        meta = SYMBOL_MAP.get(symbol, {})
        result = {
            "symbol":          symbol,
            "yf_ticker":       yf_ticker,
            "name":            meta.get("name", info.get("shortName", symbol)),
            "exchange":        meta.get("exchange", info.get("exchange", "")),
            "unit":            meta.get("unit", ""),
            "current_price":   round(float(current), 4) if current else None,
            "prev_close":      round(float(prev_close), 4) if prev_close else None,
            "day_change_pct":  day_change_pct,
            "price_3mo_ago":   year_ago_price,
            "change_vs_3mo_pct": yoy_change_pct,
            "market_time":     info.get("regularMarketTime", ""),
            "currency":        info.get("currency", "USD"),
        }
        return result, None
    except Exception as e:
        meta = SYMBOL_MAP.get(symbol, {})
        return {"symbol": symbol, "yf_ticker": yf_ticker, "name": meta.get("name", symbol),
                "unit": meta.get("unit", ""), "current_price": None}, str(e)


def main():
    parser = argparse.ArgumentParser(description="Commodity futures price fetcher")
    parser.add_argument("--symbol",    nargs="+", default=None,
                        help="Futures symbol(s) e.g. LE GF HE CL GC")
    parser.add_argument("--commodity", nargs="+", default=None,
                        help="Commodity name(s) e.g. 'live cattle' 'gold' 'crude oil'")
    parser.add_argument("--list",      action="store_true",
                        help="List all supported symbols and exit")
    args = parser.parse_args()

    if args.list:
        print(json.dumps({"supported_symbols": SYMBOL_MAP}, indent=2))
        return

    symbols = []
    if args.symbol:
        symbols += [resolve_symbol(s) for s in args.symbol]
    if args.commodity:
        for c in args.commodity:
            sym = resolve_symbol(c)
            if sym not in symbols:
                symbols.append(sym)

    if not symbols:
        print(json.dumps({
            "error": "No symbol specified. Use --symbol LE GF or --list.",
            "source": "cme_fetcher"
        }))
        sys.exit(1)

    results = []
    warnings = []

    for sym in symbols:
        data, err = fetch_commodity(sym)
        if err:
            warnings.append(f"{sym}: {err}")
        results.append(data)

    output = {
        "source":       "cme_yfinance",
        "retrieved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note":         (
            "Prices via yfinance continuous futures (e.g. LE=F). "
            "Per §2.1: qualifies as primary-source commodity verification for PM Verdict. "
            "Day change uses previous close; 3-month change uses 3-month history start."
        ),
        "commodities":  results,
        "warnings":     warnings,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
