#!/usr/bin/env python3
"""
alpha_screener.py — Second/Third-Order Effect Stock Screener

Given a market event and its first-order affected sector, finds stocks affected
through supply chain, cost transmission, or indirect factor exposure that are
NOT prominently covered in news (i.e., potential alpha).

Design principle: Alpha = impact × (1 - news_saturation)
  - High impact + low coverage = high alpha candidate
  - High impact + high coverage = no alpha (already priced)

Usage:
    python3 skills/alpha_screener.py --event "oil price surge to $100" \
        --first-order-sector Energy \
        --direction up \
        [--exclude XOM CVX] \
        [--json]
"""

import argparse
import json
import sys
from datetime import datetime

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

SCRIPT_VERSION = "1.0"

# ── Factor-to-Industry Chain Mapping ─────────────────────────────────────────
# Each event type maps to affected factors, and each factor maps to industries
# with exposure direction (positive/negative) and transmission order (2nd/3rd)

FACTOR_CHAINS = {
    "oil_price_up": {
        "description": "Oil/energy price increase",
        "second_order": [
            {"industry": "Airlines", "direction": "negative", "mechanism": "fuel cost 25-35% of opex",
             "screen_tickers": ["DAL", "UAL", "LUV", "AAL", "RYAAY"]},
            {"industry": "Trucking/Logistics", "direction": "negative", "mechanism": "diesel cost transmission",
             "screen_tickers": ["ODFL", "XPO", "SAIA", "JBHT"]},
            {"industry": "Chemicals/Fertilizer", "direction": "negative", "mechanism": "petrochemical feedstock cost",
             "screen_tickers": ["LYB", "DOW", "CE", "NTR", "MOS", "CF"]},
            {"industry": "Shipping", "direction": "mixed", "mechanism": "bunker fuel cost up but tanker rates up",
             "screen_tickers": ["FRO", "STNG", "INSW", "TNK"]},
        ],
        "third_order": [
            {"industry": "Consumer Discretionary (low-margin)", "direction": "negative",
             "mechanism": "gasoline price → consumer spending squeeze → discretionary cut",
             "screen_tickers": ["DG", "DLTR", "FIVE", "COST"]},
            {"industry": "Food/Agriculture", "direction": "negative",
             "mechanism": "fertilizer + transport cost → food price inflation → margin squeeze for processors",
             "screen_tickers": ["INGR", "DAR", "THS", "CALM"]},
            {"industry": "India/EM importers", "direction": "negative",
             "mechanism": "oil import bill → current account pressure → currency weakness",
             "screen_tickers": ["INDA", "EPI", "IBN", "HDB"]},
            {"industry": "Renewable Energy", "direction": "positive",
             "mechanism": "high oil price → accelerated energy transition narrative",
             "screen_tickers": ["ENPH", "SEDG", "FSLR", "RUN"]},
        ],
    },
    "oil_price_down": {
        "description": "Oil/energy price decrease",
        "second_order": [
            {"industry": "Airlines", "direction": "positive", "mechanism": "fuel cost relief",
             "screen_tickers": ["DAL", "UAL", "LUV", "AAL"]},
            {"industry": "Consumer Discretionary", "direction": "positive",
             "mechanism": "gasoline savings → consumer spending boost",
             "screen_tickers": ["TJX", "ROST", "DG", "DLTR"]},
        ],
        "third_order": [
            {"industry": "E&P/Oilfield Services", "direction": "negative",
             "mechanism": "capex cuts cascade to service companies",
             "screen_tickers": ["SLB", "HAL", "BKR", "NOV"]},
            {"industry": "EM Oil Exporters", "direction": "negative",
             "mechanism": "fiscal pressure on petro-states → sovereign risk",
             "screen_tickers": ["EWZ", "EWW", "KSA"]},
        ],
    },
    "rate_hike_expectation": {
        "description": "Rising interest rate expectations",
        "second_order": [
            {"industry": "REITs", "direction": "negative", "mechanism": "cap rate expansion → NAV compression",
             "screen_tickers": ["O", "AMT", "SPG", "VICI", "PSA"]},
            {"industry": "Homebuilders", "direction": "negative", "mechanism": "mortgage rate → demand destruction",
             "screen_tickers": ["LEN", "DHI", "NVR", "TOL", "PHM"]},
            {"industry": "Regional Banks", "direction": "mixed", "mechanism": "NIM benefit vs deposit flight/CRE exposure",
             "screen_tickers": ["ZION", "KEY", "CFG", "HBAN", "RF"]},
        ],
        "third_order": [
            {"industry": "Growth/Unprofitable Tech", "direction": "negative",
             "mechanism": "DCF terminal value discount → valuation compression",
             "screen_tickers": ["SNOW", "DDOG", "NET", "CRWD", "MDB"]},
            {"industry": "Utilities", "direction": "negative",
             "mechanism": "bond proxy → yield competition from treasuries",
             "screen_tickers": ["NEE", "SO", "DUK", "D", "AEP"]},
        ],
    },
    "ai_disruption_software": {
        "description": "AI replacing traditional software",
        "second_order": [
            {"industry": "IT Consulting/Outsourcing", "direction": "negative",
             "mechanism": "AI agents reduce need for human implementation/customization services",
             "screen_tickers": ["ACN", "CTSH", "INFY", "WIT", "EPAM"]},
            {"industry": "Low-Code/No-Code Platforms", "direction": "negative",
             "mechanism": "AI coding makes low-code value prop obsolete",
             "screen_tickers": ["APPF", "MNDY", "FROG"]},
        ],
        "third_order": [
            {"industry": "Commercial Real Estate (Tech Hubs)", "direction": "negative",
             "mechanism": "software layoffs → office vacancy in tech corridors",
             "screen_tickers": ["BXP", "VNO", "SLG", "CBRE"]},
            {"industry": "Technical Recruiting/Staffing", "direction": "negative",
             "mechanism": "fewer junior dev hires needed → staffing revenue decline",
             "screen_tickers": ["RHI", "HAYS.L", "KFRC"]},
            {"industry": "AI Infrastructure Beneficiaries", "direction": "positive",
             "mechanism": "more AI usage = more compute/data demand",
             "screen_tickers": ["NVDA", "AMD", "SMCI", "VRT", "ANET"]},
        ],
    },
    "semiconductor_capex_surge": {
        "description": "Semiconductor industry capex expansion",
        "second_order": [
            {"industry": "Semiconductor Equipment", "direction": "positive",
             "mechanism": "direct beneficiary of fab buildout",
             "screen_tickers": ["ASML", "AMAT", "LRCX", "KLAC", "TER"]},
            {"industry": "Specialty Gases/Materials", "direction": "positive",
             "mechanism": "fab expansion → increased consumption of process chemicals",
             "screen_tickers": ["APD", "LIN", "ENTG", "AMKR"]},
        ],
        "third_order": [
            {"industry": "Industrial Construction", "direction": "positive",
             "mechanism": "new fab construction → multi-year building contracts",
             "screen_tickers": ["EME", "MTZ", "PWR", "FIX"]},
            {"industry": "Power/Utilities near fab clusters", "direction": "positive",
             "mechanism": "fabs consume massive electricity → local utility demand surge",
             "screen_tickers": ["VST", "CEG", "NRG", "TLN"]},
            {"industry": "Memory/Storage downstream", "direction": "mixed",
             "mechanism": "more supply eventually = price pressure on memory ASPs",
             "screen_tickers": ["WDC", "STX", "NXPI"]},
        ],
    },
    "china_geopolitical_tension": {
        "description": "US-China geopolitical escalation",
        "second_order": [
            {"industry": "China-exposed Tech", "direction": "negative",
             "mechanism": "export restriction risk, revenue loss",
             "screen_tickers": ["NVDA", "QCOM", "TXN", "AVGO", "MRVL"]},
            {"industry": "China Consumer Brands", "direction": "negative",
             "mechanism": "boycott risk, regulatory retaliation",
             "screen_tickers": ["SBUX", "NKE", "AAPL", "TSLA"]},
        ],
        "third_order": [
            {"industry": "Vietnam/India Manufacturing", "direction": "positive",
             "mechanism": "supply chain diversification beneficiary",
             "screen_tickers": ["VNM", "INDA", "EPI"]},
            {"industry": "Domestic China Substitutes", "direction": "positive",
             "mechanism": "import substitution policy acceleration",
             "screen_tickers": ["BIDU", "BABA", "PDD", "JD"]},
        ],
    },
    "pharma_patent_cliff": {
        "description": "Major drug patent expiration wave",
        "second_order": [
            {"industry": "Generic/Biosimilar Manufacturers", "direction": "positive",
             "mechanism": "patent expiry opens market for generic competition",
             "screen_tickers": ["TEVA", "MYL", "INCY", "BMRN"]},
            {"industry": "PBMs/Pharmacy Chains", "direction": "positive",
             "mechanism": "generic substitution improves pharmacy margin",
             "screen_tickers": ["CI", "CVS", "WBA"]},
        ],
        "third_order": [
            {"industry": "CRO/CDMO", "direction": "mixed",
             "mechanism": "innovator companies accelerate pipeline → CRO demand up; but lost revenue pressures R&D budgets",
             "screen_tickers": ["CRL", "TMO", "WST", "CTLT"]},
            {"industry": "Health Insurers", "direction": "positive",
             "mechanism": "lower drug costs → improved medical loss ratios",
             "screen_tickers": ["UNH", "ELV", "HUM", "CNC"]},
        ],
    },
    "ev_adoption_acceleration": {
        "description": "EV adoption accelerating / ICE displacement",
        "second_order": [
            {"industry": "Battery Materials/Lithium", "direction": "positive",
             "mechanism": "EV volume → lithium/nickel/cobalt demand",
             "screen_tickers": ["ALB", "SQM", "LAC", "LTHM"]},
            {"industry": "Charging Infrastructure", "direction": "positive",
             "mechanism": "EV fleet growth → charging network buildout",
             "screen_tickers": ["CHPT", "BLNK", "EVGO"]},
        ],
        "third_order": [
            {"industry": "Auto Parts (ICE-specific)", "direction": "negative",
             "mechanism": "transmission, exhaust, fuel injection parts demand decline",
             "screen_tickers": ["DORM", "MOD", "BWA", "APTV"]},
            {"industry": "Gas Stations/Convenience", "direction": "negative",
             "mechanism": "fuel volume decline → foot traffic loss → convenience store revenue hit",
             "screen_tickers": ["CASY", "MUSA"]},
            {"industry": "Rare Earth/Magnets", "direction": "positive",
             "mechanism": "EV motors require permanent magnets → rare earth demand surge",
             "screen_tickers": ["MP", "UUUU"]},
        ],
    },
    "interest_rate_cut": {
        "description": "Central bank rate cuts / dovish pivot",
        "second_order": [
            {"industry": "Homebuilders", "direction": "positive",
             "mechanism": "mortgage rate decline → housing demand recovery",
             "screen_tickers": ["LEN", "DHI", "NVR", "TOL", "PHM"]},
            {"industry": "REITs", "direction": "positive",
             "mechanism": "cap rate compression → NAV expansion",
             "screen_tickers": ["O", "AMT", "SPG", "PSA", "VICI"]},
        ],
        "third_order": [
            {"industry": "Home Improvement", "direction": "positive",
             "mechanism": "housing turnover recovery → renovation spending",
             "screen_tickers": ["HD", "LOW", "TREX", "FBHS"]},
            {"industry": "Mortgage Servicers/Originators", "direction": "positive",
             "mechanism": "refi wave + new origination volume",
             "screen_tickers": ["RKT", "UWMC", "PFSI"]},
            {"industry": "Growth/Unprofitable Tech", "direction": "positive",
             "mechanism": "lower discount rates → DCF terminal value expansion → multiple re-rate",
             "screen_tickers": ["SNOW", "DDOG", "NET", "MDB", "CRWD"]},
        ],
    },
    "war_escalation": {
        "description": "Military conflict escalation",
        "second_order": [
            {"industry": "Defense/Aerospace", "direction": "positive",
             "mechanism": "defense budget acceleration",
             "screen_tickers": ["LMT", "RTX", "NOC", "GD", "HII"]},
            {"industry": "Cybersecurity", "direction": "positive",
             "mechanism": "state-sponsored cyber threat escalation",
             "screen_tickers": ["PANW", "CRWD", "FTNT", "ZS"]},
        ],
        "third_order": [
            {"industry": "Agricultural Commodities", "direction": "positive",
             "mechanism": "supply disruption from conflict zones → grain/fertilizer shortage",
             "screen_tickers": ["ADM", "BG", "CTVA", "FMC"]},
            {"industry": "Gold/Safe Haven", "direction": "positive",
             "mechanism": "risk-off flows → precious metals bid",
             "screen_tickers": ["GLD", "NEM", "GOLD", "AEM"]},
            {"industry": "Insurance/Reinsurance", "direction": "negative",
             "mechanism": "war risk exclusion repricing, potential claims",
             "screen_tickers": ["RNR", "ACGL", "MKL"]},
        ],
    },
}


def get_stock_data(tickers):
    """Fetch basic market data for a list of tickers."""
    results = []
    if not YF_AVAILABLE:
        return results
    for t in tickers:
        try:
            info = yf.Ticker(t).info or {}
            results.append({
                "ticker": t,
                "name": info.get("shortName", ""),
                "price": info.get("currentPrice"),
                "day_change_pct": info.get("regularMarketChangePercent"),
                "market_cap": info.get("marketCap"),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "pe_forward": info.get("forwardPE"),
                "short_pct": info.get("shortPercentOfFloat"),
            })
        except Exception:
            results.append({"ticker": t, "error": "fetch_failed"})
    return results


def screen_event(event_type, direction="up", exclude=None):
    """Screen for second/third-order affected stocks given an event type."""
    exclude = set(t.upper() for t in (exclude or []))

    chain = FACTOR_CHAINS.get(event_type)
    if not chain:
        return {"error": f"Unknown event type: {event_type}",
                "available_types": list(FACTOR_CHAINS.keys())}

    candidates = []
    for order in ["second_order", "third_order"]:
        for link in chain.get(order, []):
            for ticker in link["screen_tickers"]:
                if ticker.upper() not in exclude:
                    candidates.append({
                        "ticker": ticker,
                        "order": order.replace("_", " "),
                        "industry": link["industry"],
                        "direction": link["direction"],
                        "mechanism": link["mechanism"],
                    })

    # Deduplicate by ticker, keep first (higher order priority)
    seen = set()
    unique = []
    for c in candidates:
        if c["ticker"] not in seen:
            seen.add(c["ticker"])
            unique.append(c)

    return {
        "event_type": event_type,
        "description": chain["description"],
        "candidates": unique,
        "excluded": list(exclude),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _rank_and_diversify(candidates, top_n=6):
    """Rank: third-order first (more alpha), diversify by industry, guarantee mix."""
    third = [c for c in candidates if "third" in c.get("order", "")]
    second = [c for c in candidates if "second" in c.get("order", "")]

    def diversify(lst, max_per_ind=2):
        ic = {}
        out = []
        for c in lst:
            ind = c.get("industry", "unknown")
            ic[ind] = ic.get(ind, 0) + 1
            if ic[ind] <= max_per_ind:
                out.append(c)
        return out

    third_div = diversify(third)
    second_div = diversify(second)

    half = max(top_n // 2, 1)
    result = third_div[:half]
    result += second_div[:top_n - len(result)]
    if len(result) < top_n:
        backfill = [c for c in third_div + second_div if c not in result]
        result.extend(backfill[:top_n - len(result)])
    return result[:top_n]


def enrich_candidates(candidates, top_n=6):
    """Fetch live market data then rank and diversify."""
    tickers = [c["ticker"] for c in candidates[:top_n * 3]]
    stock_data = {d["ticker"]: d for d in get_stock_data(tickers) if "error" not in d}

    for c in candidates:
        t = c["ticker"]
        if t in stock_data:
            sd = stock_data[t]
            c.update({"price": sd.get("price"), "day_change_pct": sd.get("day_change_pct"),
                       "market_cap": sd.get("market_cap"), "pe_forward": sd.get("pe_forward"),
                       "name": sd.get("name", "")})
        else:
            c.update({"price": None, "day_change_pct": None})

    return _rank_and_diversify(candidates, top_n)


def print_human_readable(result, enriched):
    print(f"\n{'='*65}")
    print(f"  ALPHA SCREENER: {result['description']}")
    print(f"  {result['timestamp']} | Excluded: {result['excluded'] or 'none'}")
    print(f"{'='*65}\n")

    if "error" in result:
        print(f"ERROR: {result['error']}")
        print(f"Available event types: {', '.join(result.get('available_types', []))}")
        return

    for i, c in enumerate(enriched, 1):
        order_tag = "[3rd]" if "third" in c.get("order", "") else "[2nd]"
        dir_icon = {"positive": "+", "negative": "-", "mixed": "~"}.get(c["direction"], "?")
        chg = f"{c['day_change_pct']:+.2f}%" if c.get("day_change_pct") is not None else "N/A"
        price = f"${c['price']:.2f}" if c.get("price") else "N/A"
        mcap = f"${c['market_cap']/1e9:.0f}B" if c.get("market_cap") else ""
        name = c.get("name", "")

        print(f"  {i}. {order_tag} {c['ticker']:6s} ({dir_icon}) {name}")
        print(f"     Price: {price} | Day: {chg} | MCap: {mcap}")
        print(f"     Industry: {c['industry']}")
        print(f"     Mechanism: {c['mechanism']}")
        # Generate specific validation point based on mechanism
        mech = c['mechanism']
        if '→' in mech:
            parts = mech.split('→')
            cause = parts[0].strip()
            effect = parts[-1].strip()
            print(f"     Validation point: Check 10-K/10-Q — does {cause} appear as a risk factor or cost driver? Is {effect} quantifiable in segment data?")
        else:
            print(f"     Validation point: Verify mechanism in primary filing: {mech}")
        print()

    print(f"{'─'*65}")
    print(f"  To track: python3 skills/watchlist_manager.py add --ticker [T] \\")
    print(f"    --stance Watch --breaks \"[validation point]\" --sector \"[sector]\"")
    print(f"{'─'*65}\n")


# ── Free-text event matching ─────────────────────────────────────────────────

# Keywords that map to event types (fuzzy matching)
EVENT_KEYWORDS = {
    "oil_price_up": ["oil", "crude", "brent", "wti", "gasoline", "fuel", "petroleum",
                     "opec", "hormuz", "energy price", "gas price", "oil surge"],
    "oil_price_down": ["oil crash", "oil drop", "crude down", "oil glut", "opec cut fail"],
    "war_escalation": ["war", "conflict", "military", "bombing", "invasion", "missile",
                       "iran", "russia", "escalation", "geopolitical", "defense", "strait"],
    "rate_hike_expectation": ["rate hike", "rate rise", "fed hawk", "inflation", "cpi",
                              "treasury yield", "bond yield", "tighten", "rate up", "interest rate"],
    "interest_rate_cut": ["rate cut", "fed dove", "easing", "rate down", "dovish", "pivot"],
    "china_geopolitical_tension": ["china", "taiwan", "tariff", "trade war", "decoupling",
                                   "sanctions china", "export controls", "us-china"],
    "semiconductor_capex_surge": ["semiconductor", "chip", "fab", "foundry", "tsmc",
                                  "gpu", "ai chip", "capex surge"],
    "ai_disruption_software": ["ai disruption", "ai replace", "copilot", "automation",
                               "software layoff", "saas disruption", "ai productivity"],
    "pharma_patent_cliff": ["patent cliff", "generic", "biosimilar", "patent expiry",
                            "drug pricing", "pharma tariff", "pharma", "drug tariff",
                            "pharmaceutical", "100% tariff drug", "imported drugs"],
    "ev_adoption_acceleration": ["ev", "electric vehicle", "battery", "charging",
                                 "ev mandate", "ice ban", "ev adoption"],
}


def match_events_from_text(text: str, max_matches=3) -> list:
    """Match free-text event description to preset event types via keyword scoring."""
    text_lower = text.lower()
    scores = {}
    for event_type, keywords in EVENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[event_type] = score
    # Sort by score descending
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return [r[0] for r in ranked[:max_matches]]


def screen_multi_events(event_types: list, exclude=None, top=8):
    """Screen multiple events, merge and deduplicate candidates."""
    all_candidates = []
    descriptions = []
    for et in event_types:
        result = screen_event(et, exclude=exclude)
        if "error" not in result:
            all_candidates.extend(result["candidates"])
            descriptions.append(result["description"])

    # Deduplicate by ticker, keep first occurrence
    seen = set()
    unique = []
    for c in all_candidates:
        if c["ticker"] not in seen:
            seen.add(c["ticker"])
            unique.append(c)

    return {
        "event_types": event_types,
        "description": " + ".join(descriptions),
        "candidates": unique,
        "excluded": list(set(t.upper() for t in (exclude or []))),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def main():
    parser = argparse.ArgumentParser(description="Alpha screener: find non-obvious affected stocks")
    parser.add_argument("--event", required=True,
                        help="Event type (preset or free-text description). "
                             "Presets: " + ", ".join(FACTOR_CHAINS.keys()))
    parser.add_argument("--exclude", nargs="*", default=[],
                        help="Tickers to exclude (first-order obvious plays)")
    parser.add_argument("--top", type=int, default=6, help="Number of candidates to return")
    parser.add_argument("--no-enrich", action="store_true",
                        help="Skip live market data fetch (faster, offline)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    # Try exact match first, then free-text matching
    if args.event in FACTOR_CHAINS:
        result = screen_event(args.event, exclude=args.exclude)
    else:
        matched = match_events_from_text(args.event)
        if not matched:
            result = {"error": f"No event match for: '{args.event}'",
                      "available_types": list(FACTOR_CHAINS.keys()),
                      "hint": "Use preset names or include keywords like: oil, war, rate, china, ai, ev, pharma"}
        else:
            print(f"[FREE-TEXT] Matched events: {matched}", file=sys.stderr)
            result = screen_multi_events(matched, exclude=args.exclude, top=args.top)

    if "error" in result:
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"ERROR: {result['error']}")
            print(f"Available: {', '.join(result.get('available_types', []))}")
            if "hint" in result:
                print(f"Hint: {result['hint']}")
        sys.exit(1)

    if args.no_enrich:
        enriched = _rank_and_diversify(result["candidates"], args.top)
    else:
        enriched = enrich_candidates(result["candidates"], args.top)

    if args.json:
        result["top_candidates"] = enriched
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print_human_readable(result, enriched)


if __name__ == "__main__":
    main()
