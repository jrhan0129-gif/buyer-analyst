#!/usr/bin/env python3
"""
sector_classifier.py — Map yfinance sector/industry to CLAUDE.md §8.x addenda.

Given a ticker, fetches sector+industry from yfinance and returns which §8 addenda
apply, what valuation routing governs, and which falsification triggers are active.

Usage:
    python3 skills/sector_classifier.py NVDA
    python3 skills/sector_classifier.py --sector "Technology" --industry "Semiconductors"
    python3 skills/sector_classifier.py NVDA --json
"""

import argparse
import json
import sys

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

# ── §8 Addendum Mapping ──────────────────────────────────────────────────────
# Each rule: (match_type, match_string, addendum_id, addendum_name)
# match_type: "sector" matches yf sector, "industry" matches yf industry
# Rules are evaluated in order; ALL matching rules apply (multi-addenda possible)

ADDENDUM_RULES = [
    # §8.1 AI / Compute-Heavy
    ("industry", "semiconductors", "§8.1", "AI/Compute-Heavy"),
    ("industry", "semiconductor equipment", "§8.1", "AI/Compute-Heavy"),
    ("industry", "computer hardware", "§8.1", "AI/Compute-Heavy"),
    ("industry", "scientific & technical instruments", "§8.1", "AI/Compute-Heavy"),

    # §8.2 Platform / Developer Ecosystem
    ("industry", "internet content", "§8.2", "Platform/Developer Ecosystem"),
    ("industry", "internet retail", "§8.2", "Platform/Developer Ecosystem"),
    ("industry", "software - application", "§8.2", "Platform/Developer Ecosystem"),
    ("industry", "software - infrastructure", "§8.2", "Platform/Developer Ecosystem"),
    ("industry", "electronic gaming", "§8.2", "Platform/Developer Ecosystem"),
    ("industry", "information technology services", "§8.2", "Platform/Developer Ecosystem"),

    # §8.3 Regulated / Capital-Intensive
    ("sector", "utilities", "§8.3", "Regulated/Capital-Intensive"),
    ("industry", "aerospace & defense", "§8.3", "Regulated/Capital-Intensive"),
    ("industry", "railroads", "§8.3", "Regulated/Capital-Intensive"),
    ("industry", "oil & gas", "§8.3", "Regulated/Capital-Intensive"),
    ("industry", "telecom", "§8.3", "Regulated/Capital-Intensive"),
    ("industry", "airports", "§8.3", "Regulated/Capital-Intensive"),

    # §8.4 Financials / Balance-Sheet-Driven
    ("sector", "financial services", "§8.4", "Financials/Balance-Sheet-Driven"),
    ("industry", "banks", "§8.4", "Financials/Balance-Sheet-Driven"),
    ("industry", "insurance", "§8.4", "Financials/Balance-Sheet-Driven"),
    ("industry", "capital markets", "§8.4", "Financials/Balance-Sheet-Driven"),
    ("industry", "financial data", "§8.4", "Financials/Balance-Sheet-Driven"),
    ("sector", "real estate", "§8.4", "Financials/Balance-Sheet-Driven"),

    # §8.5 Consumer / Retail / Brand
    ("industry", "apparel", "§8.5", "Consumer/Retail/Brand"),
    ("industry", "footwear", "§8.5", "Consumer/Retail/Brand"),
    ("industry", "luxury goods", "§8.5", "Consumer/Retail/Brand"),
    ("industry", "restaurants", "§8.5", "Consumer/Retail/Brand"),
    ("industry", "packaged foods", "§8.5", "Consumer/Retail/Brand"),
    ("industry", "beverages", "§8.5", "Consumer/Retail/Brand"),
    ("industry", "household products", "§8.5", "Consumer/Retail/Brand"),
    ("industry", "discount stores", "§8.5", "Consumer/Retail/Brand"),
    ("industry", "grocery stores", "§8.5", "Consumer/Retail/Brand"),
    ("industry", "specialty retail", "§8.5", "Consumer/Retail/Brand"),
    ("industry", "department stores", "§8.5", "Consumer/Retail/Brand"),
    ("industry", "home improvement", "§8.5", "Consumer/Retail/Brand"),

    # §8.6 Healthcare / Pharma / Biotech
    ("industry", "drug manufacturers", "§8.6", "Healthcare/Pharma/Biotech"),
    ("industry", "biotechnology", "§8.6", "Healthcare/Pharma/Biotech"),
    ("industry", "medical devices", "§8.6", "Healthcare/Pharma/Biotech"),
    ("industry", "diagnostics", "§8.6", "Healthcare/Pharma/Biotech"),
    ("industry", "health care plans", "§8.6", "Healthcare/Pharma/Biotech"),
    ("industry", "health care providers", "§8.6", "Healthcare/Pharma/Biotech"),

    # §8.7 Auto / EV / Mobility
    ("industry", "auto manufacturers", "§8.7", "Auto/EV/Mobility"),
    ("industry", "auto parts", "§8.7", "Auto/EV/Mobility"),
    ("industry", "recreational vehicles", "§8.7", "Auto/EV/Mobility"),

    # §8.3 also applies to asset-heavy
    ("sector", "real estate", "§8.3", "Regulated/Capital-Intensive"),
    ("industry", "farm & heavy construction machinery", "§8.3", "Regulated/Capital-Intensive"),
    ("industry", "mining", "§8.3", "Regulated/Capital-Intensive"),
    ("industry", "steel", "§8.3", "Regulated/Capital-Intensive"),
    ("industry", "thermal coal", "§8.3", "Regulated/Capital-Intensive"),
]

# Multi-addenda valuation routing from CLAUDE.md §8
ARBITRATION_MATRIX = {
    ("§8.1", "§8.2"): ("§8.1", "EV/GP over P/S — compute cost discipline overrides platform premium"),
    ("§8.1", "§8.3"): ("§8.3", "DCF/capex discipline — infrastructure economics dominate"),
    ("§8.3", "§8.4"): ("§8.4", "P/B, ROE/ROTCE vs CoE — balance-sheet methods override"),
    ("§8.3", "§8.5"): ("§8.5 if GM>50%, else §8.3", "Consumer margin profile dictates"),
    ("§8.2", "§8.5"): ("§8.5", "Consumer unit economics override platform premium"),
    ("§8.3", "§8.7"): ("§8.7", "Auto unit economics override generic capital-intensive DCF"),
    ("§8.6", "§8.1"): ("§8.6", "Drug pipeline risk dominates over compute-cost logic"),
}


def classify(sector, industry):
    """Return list of matching addenda and valuation routing."""
    s = (sector or "").lower()
    i = (industry or "").lower()

    matched = []
    seen_ids = set()
    for match_type, match_str, addendum_id, addendum_name in ADDENDUM_RULES:
        target = s if match_type == "sector" else i
        if match_str in target and addendum_id not in seen_ids:
            matched.append({"id": addendum_id, "name": addendum_name})
            seen_ids.add(addendum_id)

    # Determine valuation routing
    routing = None
    if len(matched) >= 2:
        ids = tuple(sorted([m["id"] for m in matched[:2]]))
        if ids in ARBITRATION_MATRIX:
            governs, rationale = ARBITRATION_MATRIX[ids]
            routing = {"governs": governs, "rationale": rationale, "pair": ids}
        else:
            routing = {"governs": matched[0]["id"],
                       "rationale": f"Unlisted pair {ids} — {matched[0]['name']} governs (most specific to primary revenue)",
                       "pair": ids}
    elif len(matched) == 1:
        routing = {"governs": matched[0]["id"], "rationale": "Single addendum", "pair": None}

    return {
        "sector": sector,
        "industry": industry,
        "addenda": matched,
        "addenda_count": len(matched),
        "valuation_routing": routing,
        "fallback": "No §8 addendum matched — use general framework rules" if not matched else None,
    }


def main():
    parser = argparse.ArgumentParser(description="Map ticker to §8.x addenda")
    parser.add_argument("ticker", nargs="?", help="Ticker symbol")
    parser.add_argument("--sector", default=None, help="Override sector")
    parser.add_argument("--industry", default=None, help="Override industry")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sector = args.sector
    industry = args.industry

    if args.ticker and not (sector and industry):
        if not YF_AVAILABLE:
            print("yfinance not available", file=sys.stderr)
            sys.exit(1)
        try:
            info = yf.Ticker(args.ticker).info or {}
            sector = sector or info.get("sector", "")
            industry = industry or info.get("industry", "")
        except Exception as e:
            print(f"Failed to fetch {args.ticker}: {e}", file=sys.stderr)
            sys.exit(1)

    result = classify(sector, industry)
    result["ticker"] = args.ticker

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"  SECTOR CLASSIFIER: {args.ticker or 'manual'}")
        print(f"  Sector: {sector} | Industry: {industry}")
        print(f"{'='*50}\n")

        if result["addenda"]:
            print("  Applicable addenda:")
            for a in result["addenda"]:
                print(f"    {a['id']} — {a['name']}")
        else:
            print(f"  {result['fallback']}")

        if result["valuation_routing"]:
            r = result["valuation_routing"]
            print(f"\n  Valuation routing: {r['governs']}")
            print(f"  Rationale: {r['rationale']}")
            if r.get("pair"):
                print(f"  Active pair: {r['pair']}")
        print()


if __name__ == "__main__":
    main()
