"""
peer_comps.py — Peer comparison table builder for buy-side research
Usage:
    python3 skills/peer_comps.py --ticker AAPL --peers MSFT GOOGL META AMZN
    python3 skills/peer_comps.py --ticker 02601.HK --peers 02318.HK 02628.HK 01336.HK

Metrics pulled (where available via yfinance):
    Market cap, Price, P/B, P/E, Forward P/E, P/S,
    EV/EBITDA, EV/Revenue, Gross Margin, Operating Margin,
    ROE, ROIC, Revenue Growth, Beta

Output: JSON with ranked comparison table. Source: yfinance (point-in-time).
"""

import argparse
import json
import sys
from datetime import datetime

try:
    import yfinance as yf
except ImportError:
    print(json.dumps({"error": "yfinance not installed", "source": "peer_comps"}))
    sys.exit(1)


METRIC_KEYS = {
    "market_cap":          "marketCap",
    "price":               "currentPrice",
    "pb_ratio":            "priceToBook",
    "pe_ratio":            "trailingPE",
    "forward_pe":          "forwardPE",
    "ps_ratio":            "priceToSalesTrailing12Months",
    "ev_ebitda":           "enterpriseToEbitda",
    "ev_revenue":          "enterpriseToRevenue",
    "gross_margin":        "grossMargins",
    "operating_margin":    "operatingMargins",
    "profit_margin":       "profitMargins",
    "roe":                 "returnOnEquity",
    "revenue_growth_yoy":  "revenueGrowth",
    "earnings_growth_yoy": "earningsGrowth",
    "beta":                "beta",
    "dividend_yield":      "dividendYield",
}

PCT_FIELDS = {
    "gross_margin", "operating_margin", "profit_margin",
    "roe", "revenue_growth_yoy", "earnings_growth_yoy", "dividend_yield"
}


def fetch_metrics(ticker):
    try:
        info = yf.Ticker(ticker).info
        row = {"ticker": ticker, "name": info.get("shortName", ticker)}
        for key, yf_key in METRIC_KEYS.items():
            val = info.get(yf_key)
            if val is not None:
                if key in PCT_FIELDS:
                    row[key] = round(float(val) * 100, 2)
                elif isinstance(val, float):
                    row[key] = round(val, 3)
                else:
                    row[key] = val
            else:
                row[key] = None
        row["currency"] = info.get("currency", "")
        row["sector"]   = info.get("sector", "")
        return row, None
    except Exception as e:
        return {"ticker": ticker, "name": ticker, **{k: None for k in METRIC_KEYS}}, str(e)


def fmt_market_cap(v):
    if v is None:
        return "N/A"
    if v >= 1e12:
        return f"{v/1e12:.1f}T"
    if v >= 1e9:
        return f"{v/1e9:.1f}B"
    if v >= 1e6:
        return f"{v/1e6:.1f}M"
    return str(v)


# ── Auto-peer discovery ──────────────────────────────────────────────────────

# Industry → candidate pool (checked FIRST, higher precision than sector).
# Key = yfinance industry substring (lowercased). Value = list of tickers.
INDUSTRY_PEER_POOL = {
    # Agriculture / Commodity Trading
    "farm products": ["ADM", "BG", "INGR", "DAR", "CTVA", "FMC", "CF", "MOS", "NTR"],
    "agricultural": ["ADM", "BG", "INGR", "DAR", "CTVA", "FMC", "CF", "MOS", "NTR"],
    # Airlines
    "airlines": ["DAL", "UAL", "LUV", "AAL", "JBLU", "ALK", "SAVE", "HA"],
    # Gold / Precious Metals
    "gold": ["NEM", "AEM", "GFI", "KGC", "WPM", "FNV", "RGLD", "AGI", "AU"],
    "silver": ["WPM", "PAAS", "HL", "CDE", "AG", "MAG", "SSRM"],
    # Homebuilders
    "residential construction": ["DHI", "LEN", "PHM", "NVR", "TOL", "KBH", "MDC", "TMHC", "MHO"],
    # Cybersecurity
    "software - infrastructure": ["PANW", "CRWD", "FTNT", "ZS", "NET", "S", "OKTA", "CYBR", "QLYS"],
    # Semiconductors
    "semiconductors": ["NVDA", "AMD", "INTC", "QCOM", "TXN", "AVGO", "AMAT", "MU", "LRCX", "KLAC"],
    # Auto OEMs
    "auto manufacturers": ["TSLA", "F", "GM", "TM", "HMC", "STLA", "RIVN", "LCID"],
    # Oil E&P
    "oil & gas e&p": ["COP", "EOG", "PXD", "DVN", "FANG", "OXY", "MRO", "APA"],
    "oil & gas integrated": ["XOM", "CVX", "SHEL", "TTE", "BP"],
    "oil & gas equipment": ["SLB", "HAL", "BKR", "NOV", "FTI", "CHX", "TDW", "WFRD", "WHD"],
    "oil & gas services": ["SLB", "HAL", "BKR", "NOV", "FTI", "CHX", "TDW", "WFRD"],
    # Insurance
    "insurance - life": ["MET", "PRU", "AFL", "PFG", "VOYA", "LNC", "GL"],
    "insurance - property": ["AIG", "CB", "TRV", "ALL", "PGR", "HIG", "CNA", "WRB"],
    "insurance - diversified": ["AIG", "MET", "PRU", "CB", "TRV", "ALL", "AFL", "BRK-B"],
    "insurance - specialty": ["ACGL", "RNR", "KNSL", "HCI", "PLMR"],
    "insurance - reinsurance": ["RNR", "ACGL", "EG"],
    # Banks
    "banks - diversified": ["JPM", "BAC", "WFC", "C", "USB", "PNC", "TFC"],
    "banks - regional": ["FITB", "RF", "HBAN", "KEY", "CFG", "MTB", "ZION"],
    # Asset management
    "asset management": ["BLK", "BX", "KKR", "APO", "ARES", "OWL", "CG", "BAM"],
    "capital markets": ["GS", "MS", "SCHW", "RJF", "IBKR", "HOOD", "LPLA"],
    # Pharma / Biotech
    "drug manufacturers": ["LLY", "JNJ", "MRK", "PFE", "ABBV", "BMY", "NVS", "AZN", "SNY", "GSK"],
    "biotechnology": ["AMGN", "GILD", "VRTX", "REGN", "MRNA", "BIIB", "ALNY", "BMRN"],
    # Defense
    "aerospace & defense": ["LMT", "RTX", "NOC", "GD", "BA", "LHX", "HII", "TDG"],
    # REITs (by sub-type)
    "reit - office": ["BXP", "VNO", "SLG", "KRC", "HIW", "DEI", "CUZ"],
    "reit - industrial": ["PLD", "REXR", "FR", "STAG", "EGP"],
    "reit - retail": ["SPG", "O", "NNN", "KIM", "REG", "FRT", "BRX"],
    "reit - residential": ["AVB", "EQR", "MAA", "UDR", "CPT", "ESS"],
    "reit - specialty": ["AMT", "CCI", "EQIX", "DLR", "SBAC", "VICI"],
    # Trucking / Freight
    "trucking": ["ODFL", "XPO", "SAIA", "WERN", "KNX", "SNDR", "JBHT"],
    "integrated freight": ["FDX", "UPS", "XPO", "CHRW", "EXPD"],
    # Luxury
    "luxury goods": ["1913.HK", "LVMUY", "CFRUY", "CPRI", "TPR", "RL"],
    # Retail
    "home improvement retail": ["HD", "LOW"],
    "discount stores": ["WMT", "COST", "DG", "DLTR", "TGT", "BJ"],
    # Restaurants / Food & Beverage
    "restaurants": ["MCD", "SBUX", "QSR", "CMG", "YUM", "DPZ", "WING",
                    "9987.HK", "6862.HK", "9869.HK", "2150.HK"],
    "beverages - non-alcoholic": ["KO", "PEP", "MNST", "CELH"],
    "packaged foods": ["GIS", "K", "CAG", "SJM", "HSY", "MDLZ"],
}

# Sector → candidate pool (fallback if industry pool empty or insufficient)
SECTOR_PEER_POOL = {
    "Technology": [
        "AAPL", "MSFT", "GOOGL", "META", "NVDA", "AVGO", "ORCL", "CRM", "ADBE",
        "AMD", "INTC", "QCOM", "TXN", "NOW", "INTU", "AMAT", "MU", "LRCX", "KLAC",
        "SNPS", "CDNS", "PANW", "CRWD", "FTNT", "DDOG", "SNOW", "NET", "ZS",
    ],
    "Communication Services": [
        "GOOGL", "META", "NFLX", "DIS", "CMCSA", "TMUS", "VZ", "T", "CHTR", "SPOT",
    ],
    "Consumer Cyclical": [
        "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "MAR",
        "CMG", "ORLY", "AZO", "ROST", "DG", "DLTR", "F", "GM", "DHI", "LEN",
    ],
    "Consumer Defensive": [
        "WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "CL", "KMB", "GIS",
        "K", "HSY", "SJM", "CAG", "MDLZ", "MNST",
    ],
    "Healthcare": [
        "LLY", "UNH", "JNJ", "MRK", "ABBV", "TMO", "ABT", "PFE", "AMGN", "BMY",
        "GILD", "VRTX", "REGN", "ISRG", "MDT", "SYK", "BDX", "ZTS", "CI", "ELV",
    ],
    "Financial Services": [
        "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "SCHW", "BLK",
        "SPGI", "CME", "ICE", "AON", "MMC", "AXP", "COF", "TFC", "USB", "PNC",
    ],
    "Industrials": [
        "CAT", "DE", "UNP", "UPS", "HON", "RTX", "LMT", "BA", "GE", "MMM",
        "EMR", "ETN", "ITW", "PH", "ROK", "FAST", "WM", "RSG", "DAL", "UAL",
        "LUV", "AAL", "JBLU", "FDX", "ODFL", "XPO", "CHRW",
    ],
    "Energy": [
        "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "PXD",
        "DVN", "HAL", "BKR", "FANG", "HES",
    ],
    "Basic Materials": [
        "LIN", "APD", "SHW", "ECL", "DD", "NEM", "FCX", "NUE", "STLD", "CF",
        "MOS", "ALB", "AEM", "GFI", "KGC", "WPM", "FNV", "RGLD", "BHP", "RIO",
    ],
    "Real Estate": [
        "PLD", "AMT", "EQIX", "CCI", "SPG", "O", "DLR", "WELL", "PSA", "BXP",
        "VICI", "ARE", "AVB", "EQR", "MAA",
    ],
    "Utilities": [
        "NEE", "SO", "DUK", "D", "AEP", "SRE", "EXC", "XEL", "ED", "WEC",
        "ES", "AEE", "CMS", "CEG", "VST",
    ],
}


def _find_industry_pool(industry: str) -> list:
    """Match yfinance industry string against INDUSTRY_PEER_POOL keys (substring match)."""
    ind_lower = industry.lower() if industry else ""
    for key, pool in INDUSTRY_PEER_POOL.items():
        if key in ind_lower or ind_lower in key:
            return pool
    return []


def auto_discover_peers(ticker, max_peers=5):
    """Find peers by industry (preferred) or sector, filtered by market cap 0.3x-3x."""
    try:
        info = yf.Ticker(ticker).info
        sector = info.get("sector", "")
        industry = info.get("industry", "")
        mcap = info.get("marketCap", 0)

        if not mcap:
            return [], f"Cannot auto-discover: mcap={mcap}"

        # Step 1: Try industry-level pool first (more precise)
        pool = _find_industry_pool(industry)
        pool_source = "industry"

        # Step 2: Fallback to sector pool if industry pool too small
        if len(pool) < 3:
            sector_pool = SECTOR_PEER_POOL.get(sector, [])
            # Merge: industry pool first, then sector pool (deduplicated)
            seen = set(t.upper() for t in pool)
            for t in sector_pool:
                if t.upper() not in seen:
                    pool.append(t)
                    seen.add(t.upper())
            pool_source = "industry+sector" if pool else "sector"

        if not pool:
            return [], f"No peer pool for industry='{industry}', sector='{sector}'"

        # Remove subject ticker
        pool = [t for t in pool if t.upper() != ticker.upper()]

        # Filter by market cap range: 0.3x to 3x
        mcap_lo, mcap_hi = mcap * 0.3, mcap * 3.0
        candidates = []
        for t in pool:
            try:
                t_info = yf.Ticker(t).info
                t_mcap = t_info.get("marketCap", 0)
                t_industry = t_info.get("industry", "")
                if t_mcap and mcap_lo <= t_mcap <= mcap_hi:
                    same_ind = 2 if t_industry == industry else 0
                    mcap_dist = abs(t_mcap - mcap) / mcap
                    candidates.append((t, same_ind, mcap_dist, t_mcap))
            except Exception:
                continue

        # If mcap filter too strict and <2 candidates, widen to 0.1x-10x
        if len(candidates) < 2:
            mcap_lo_wide, mcap_hi_wide = mcap * 0.1, mcap * 10.0
            for t in pool:
                if any(c[0] == t for c in candidates):
                    continue
                try:
                    t_info = yf.Ticker(t).info
                    t_mcap = t_info.get("marketCap", 0)
                    t_industry = t_info.get("industry", "")
                    if t_mcap and mcap_lo_wide <= t_mcap <= mcap_hi_wide:
                        same_ind = 2 if t_industry == industry else 0
                        mcap_dist = abs(t_mcap - mcap) / mcap
                        candidates.append((t, same_ind, mcap_dist, t_mcap))
                except Exception:
                    continue

        # Sort: same industry first (score=2), then by mcap proximity
        candidates.sort(key=lambda x: (-x[1], x[2]))
        peers = [c[0] for c in candidates[:max_peers]]

        note = (f"Auto-discovered {len(peers)} peers for {ticker} "
                f"(pool={pool_source}, industry={industry}, sector={sector}, "
                f"mcap={fmt_market_cap(mcap)}, range={fmt_market_cap(mcap_lo)}-{fmt_market_cap(mcap_hi)})")
        return peers, note

    except Exception as e:
        return [], f"Auto-discovery failed: {e}"


def main():
    parser = argparse.ArgumentParser(description="Peer comparison table builder")
    parser.add_argument("--ticker",  required=True, help="Primary ticker (subject company)")
    parser.add_argument("--peers",   nargs="*", default=None,
                        help="Peer tickers (space-separated). If omitted, auto-discovers by sector+mcap.")
    parser.add_argument("--sector",  default=None,
                        help="Override sector label (e.g. 'Insurance' for financials context)")
    parser.add_argument("--max-peers", type=int, default=5,
                        help="Max peers for auto-discovery (default 5)")
    args = parser.parse_args()

    discovery_note = None
    if not args.peers:
        peers, discovery_note = auto_discover_peers(args.ticker, args.max_peers)
        if not peers:
            print(json.dumps({
                "error": f"No peers found: {discovery_note}",
                "source": "peer_comps",
                "suggestion": "Provide --peers manually"
            }))
            sys.exit(1)
        args.peers = peers
        print(f"[AUTO-PEER] {discovery_note}", file=sys.stderr)

    all_tickers = [args.ticker] + args.peers
    warnings = []
    rows = []

    for t in all_tickers:
        row, err = fetch_metrics(t)
        if err:
            warnings.append(f"{t}: fetch error — {err}")
        rows.append(row)

    if not rows:
        print(json.dumps({"error": "No data fetched", "source": "peer_comps"}))
        sys.exit(1)

    summary = []
    for r in rows:
        summary.append({
            "ticker":           r["ticker"],
            "name":             r.get("name", ""),
            "market_cap":       fmt_market_cap(r.get("market_cap")),
            "price":            r.get("price"),
            "pb_ratio":         r.get("pb_ratio"),
            "pe_ratio":         r.get("pe_ratio"),
            "forward_pe":       r.get("forward_pe"),
            "ps_ratio":         r.get("ps_ratio"),
            "ev_ebitda":        r.get("ev_ebitda"),
            "ev_revenue":       r.get("ev_revenue"),
            "gross_margin_pct": r.get("gross_margin"),
            "operating_margin_pct": r.get("operating_margin"),
            "roe_pct":          r.get("roe"),
            "revenue_growth_pct": r.get("revenue_growth_yoy"),
            "beta":             r.get("beta"),
            "dividend_yield_pct": r.get("dividend_yield"),
            "currency":         r.get("currency", ""),
            "sector":           r.get("sector", ""),
        })

    subject = summary[0] if summary else {}

    output = {
        "source":       "peer_comps_yfinance",
        "retrieved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "subject":      args.ticker.upper(),
        "peer_set":     [t.upper() for t in args.peers],
        "auto_discovered": discovery_note is not None,
        "discovery_note": discovery_note,
        "note":         "All metrics point-in-time from yfinance. PCT fields are percentages. Verify against primary filings before use as valuation input.",
        "comparison":   summary,
        "warnings":     warnings,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
