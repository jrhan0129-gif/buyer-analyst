"""
court_fetcher.py — US Court & Regulatory Enforcement Document Fetcher

Sources:
  1. CourtListener REST API (free, no key required) — federal court dockets & opinions
  2. DOJ (--mode doj): currently disabled — justice.gov blocks programmatic access.
     Returns structured warning with Tavily fallback path.

Usage:
  python3 skills/court_fetcher.py --mode all --query "Google antitrust" [--hits 10]
  python3 skills/court_fetcher.py --mode courtlistener --query "Google antitrust" [--court dcd] [--hits 10]
  python3 skills/court_fetcher.py --mode doj --query "google search monopoly"
  python3 skills/court_fetcher.py --mode all --query "google antitrust" --text

Output (default JSON):
  {
    "retrieved_at": "...",
    "query": "...",
    "results": [
      {
        "source": "CourtListener",
        "source_class": "court_record_aggregator",
        "title": "...",
        "date": "...",
        "url": "...",
        "snippet": "...",
        "court": "...",
        "docket_number": "..."
      }
    ],
    "warnings": [...],
    "errors": [...]
  }

Governance (see also §11.9):
  CourtListener — court_record_aggregator:
    Treat as a high-confidence litigation lead, not as final dispositive evidence.
    Thesis-critical conclusions must be checked against the underlying court opinion,
    docket, or official court source where available.
  DOJ: direct fetch currently disabled due to bot-protection.
    Use: python3 skills/tavily_search.py l1 '<query> site:justice.gov/atr'
  Neither source may directly anchor PM Verdict without content verification.
"""

import argparse
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

COURTLISTENER_SEARCH = "https://www.courtlistener.com/api/rest/v4/search/"

# DOJ website is behind Akamai bot-protection; RSS/search endpoints return 404/503.
# Direct fetch is not viable. Redirect to Tavily L1 with site:justice.gov/atr.
_DOJ_UNAVAILABLE = (
    "DOJ justice.gov is behind bot-protection and cannot be fetched programmatically. "
    "Use: python3 skills/tavily_search.py l1 '<query> site:justice.gov/atr'"
)

HEADERS = {
    "User-Agent": "BuyerAnalyst/1.0 (investment research)",
    "Accept": "application/json",
}

TIMESTAMP = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── CourtListener ─────────────────────────────────────────────────────────────

def fetch_courtlistener(
    query: str, court: Optional[str], hits: int
) -> Tuple[List[Dict], List[str], List[str]]:
    """Return (results, warnings, errors) from CourtListener opinion search.
    Falls back to docket search if opinion search returns nothing."""
    results: List[Dict] = []
    warnings: List[str] = []
    errors: List[str] = []

    def _search(search_type: str) -> list:
        params = {
            "q": query,
            "type": search_type,
            "order_by": "score desc",
            "count": hits,
        }
        if court:
            params["court"] = court
        try:
            resp = requests.get(
                COURTLISTENER_SEARCH, params=params, headers=HEADERS, timeout=15
            )
            resp.raise_for_status()
            return resp.json().get("results", [])
        except requests.RequestException as exc:
            errors.append(f"CourtListener {search_type} request failed: {exc}")
            return []

    raw = _search("o")  # opinions first
    if not raw:
        warnings.append("No opinions found; falling back to docket search.")
        raw = _search("d")

    if not raw:
        warnings.append("CourtListener returned no results for this query.")
        return results, warnings, errors

    for r in raw[:hits]:
        case_name  = r.get("caseName") or r.get("case_name", "")
        court_id   = r.get("court_id") or r.get("court", "")
        date_filed = r.get("dateFiled") or r.get("date_filed", "")
        docket_num = r.get("docketNumber") or r.get("docket_number", "")
        snippet    = (r.get("snippet") or "")[:300].replace("\n", " ").strip()
        abs_url    = r.get("absolute_url", "")
        full_url   = f"https://www.courtlistener.com{abs_url}" if abs_url else ""

        results.append({
            "source": "CourtListener",
            "source_class": "court_record_aggregator",
            "title": case_name,
            "date": date_filed,
            "url": full_url,
            "court": court_id,
            "docket_number": docket_num,
            "snippet": snippet,
        })

    return results, warnings, errors


# ── DOJ stub ──────────────────────────────────────────────────────────────────

def doj_stub(query: str, hits: int) -> Tuple[List[Dict], List[str], List[str]]:
    """DOJ direct fetch currently disabled due to bot-protection.
    Returns structured warning with the correct fallback command."""
    return [], [_DOJ_UNAVAILABLE], []


# ── Output helpers ────────────────────────────────────────────────────────────

def _print_text(payload: dict) -> None:
    print(f"=== court_fetcher | {payload['retrieved_at']} ===")
    print(f"Query: {payload['query']!r}\n")

    if payload["errors"]:
        print("ERRORS:")
        for e in payload["errors"]:
            print(f"  ✗ {e}")
        print()

    if payload["warnings"]:
        print("WARNINGS:")
        for w in payload["warnings"]:
            print(f"  ⚠ {w}")
        print()

    if not payload["results"]:
        print("[NO RESULTS]")
        return

    for i, r in enumerate(payload["results"], 1):
        print(f"[{i}] [{r['source_class']}] {r['title']}")
        print(f"     Source: {r['source']} | Date: {r.get('date', 'N/A')}")
        if r.get("court"):
            print(f"     Court: {r['court']} | Docket: {r.get('docket_number', 'N/A')}")
        print(f"     URL: {r.get('url', 'N/A')}")
        if r.get("snippet"):
            print(f"     Snippet: {r['snippet']}...")
        print()

    print("[GOVERNANCE]")
    print("  court_record_aggregator: High-confidence litigation lead.")
    print("    Verify against underlying court opinion/docket for thesis-critical use.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch US federal court records and DOJ enforcement disclosures."
    )
    parser.add_argument(
        "--mode", choices=["courtlistener", "doj", "all"], default="all"
    )
    parser.add_argument("--query", required=True, help="Search keywords (company, case)")
    parser.add_argument(
        "--court", default=None,
        help="CourtListener court slug (e.g. dcd=DC District, ca9=9th Circuit, scotus)"
    )
    parser.add_argument("--hits", type=int, default=10, help="Max results per source")
    parser.add_argument("--text", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    all_results: List[Dict] = []
    all_warnings: List[str] = []
    all_errors: List[str] = []

    if args.mode in ("courtlistener", "all"):
        r, w, e = fetch_courtlistener(args.query, args.court, args.hits)
        all_results.extend(r)
        all_warnings.extend(w)
        all_errors.extend(e)

    if args.mode in ("doj", "all"):
        r, w, e = doj_stub(args.query, args.hits)
        all_results.extend(r)
        all_warnings.extend(w)
        all_errors.extend(e)

    payload = {
        "retrieved_at": TIMESTAMP,
        "query": args.query,
        "results": all_results,
        "warnings": all_warnings,
        "errors": all_errors,
    }

    if args.text:
        _print_text(payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
