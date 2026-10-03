"""
edgar_fulltext_search.py — SEC EDGAR Full-Text Search (EFTS) for buy-side research

Uses the free SEC EFTS API (efts.sec.gov) to search across SEC filings by keyword.
Useful for: finding specific disclosures, risk factor language, litigation mentions,
related-party terms, accounting policy language, or any phrase across a filing history.

Usage:
    python3 skills/edgar_fulltext_search.py --query "going concern" --ticker AAPL
    python3 skills/edgar_fulltext_search.py --query "related party" --ticker MSFT --forms 10-K 10-Q
    python3 skills/edgar_fulltext_search.py --query "channel stuffing" --years 3
    python3 skills/edgar_fulltext_search.py --query "material weakness" --ticker GE --forms 10-K --hits 20

Arguments:
    --query     Exact phrase to search. EFTS matches the literal phrase only — it does NOT support
                boolean operators (AND/OR/NOT), field-level syntax (e.g. "section:risk_factors ..."),
                wildcards, or proximity operators. One phrase per call.
    --ticker    Company ticker to scope results by CIK (client-side filter on top 100 scored hits).
                Note: for very common phrases, company filings may not rank in top 100 — omit --ticker
                and review results manually, or use a more specific phrase.
    --forms     Filing types to search (default: 10-K 10-Q 8-K). Space-separated.
    --years     Look-back window in years (default: 3, max: 10).
    --hits      Max results to return after filtering (default: 10).

Output: JSON with keys:
    source, query, ticker, cik, forms_searched, date_range,
    results, result_count, total_matched, warnings, retrieved_at, governance_note

Each result:
    filing_date, period_ending, form_type, company_name, cik,
    accession_number, filing_url

Governance (CLAUDE.md §11.7):
    EFTS is a retrieval tool. Results are leads — not verified evidence.
    Verify material claims against the primary filing before use in analysis.
    Cannot anchor PM Verdict without primary filing confirmation.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_HEADERS = {
    "User-Agent": "BuyerAnalyst research@local.dev",
    "Accept": "application/json",
}

MAX_HITS_LIMIT = 40   # max to return in output
EFTS_FETCH_SIZE = 100  # EFTS always returns 100; we post-filter
DEFAULT_HITS = 10
DEFAULT_YEARS = 3
DEFAULT_FORMS = ["10-K", "10-Q", "8-K"]


def build_session():
    s = requests.Session()
    s.trust_env = False
    s.headers.update(EDGAR_HEADERS)
    return s


def resolve_cik(session, ticker):
    """Return zero-padded 10-digit CIK string for a ticker, or None."""
    try:
        r = session.get(TICKERS_URL, timeout=15)
        r.raise_for_status()
        data = r.json()
        ticker_upper = ticker.upper()
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker_upper:
                return str(entry["cik_str"]).zfill(10)
    except Exception:
        pass
    return None


def date_range_str(years):
    end = datetime.utcnow()
    start = end - timedelta(days=365 * years)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def accession_url(cik, adsh):
    """Build canonical EDGAR filing index URL from CIK and accession number (adsh).
    Correct format: /Archives/edgar/data/{cik_stripped}/{acc_clean}/{adsh}-index.htm
    """
    if not cik or not adsh:
        return ""
    cik_stripped = str(int(cik))  # strip leading zeros for the data/ path
    acc_clean = adsh.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_stripped}/{acc_clean}/{adsh}-index.htm"


def run_search(session, query, forms, date_from, date_to):
    """
    Call EFTS with a phrase query. Returns the raw response dict.
    EFTS always returns up to 100 results sorted by relevance score.
    Size param is accepted but EFTS caps at ~100 in practice.
    """
    params = {
        "q": f'"{query}"',
        "dateRange": "custom",
        "startdt": date_from,
        "enddt": date_to,
        "forms": ",".join(forms),
        "from": 0,
        "size": EFTS_FETCH_SIZE,
    }
    url = EFTS_URL + "?" + urlencode(params)
    r = session.get(url, timeout=25)
    r.raise_for_status()
    return r.json()


def parse_results(raw, cik_filter=None, max_hits=DEFAULT_HITS):
    """
    Extract and optionally CIK-filter hits from EFTS response.
    Returns (results_list, total_matched, warnings).
    """
    warnings = []
    hits_data = raw.get("hits", {})
    total_matched = hits_data.get("total", {}).get("value", 0)
    hit_list = hits_data.get("hits", [])

    results = []
    for hit in hit_list:
        src = hit.get("_source", {})
        adsh = src.get("adsh", "")
        ciks = src.get("ciks", [])

        # CIK-scoped filter
        if cik_filter and cik_filter not in ciks:
            continue

        # Pick the primary CIK (first in list)
        primary_cik = ciks[0] if ciks else ""

        display_names = src.get("display_names") or []
        company_name = display_names[0] if display_names else src.get("entity_name", "")

        entry = {
            "filing_date": src.get("file_date", ""),
            "period_ending": src.get("period_ending", ""),
            "form_type": src.get("form", ""),
            "company_name": company_name,
            "cik": primary_cik,
            "accession_number": adsh,
            "filing_url": accession_url(primary_cik, adsh),
        }
        results.append(entry)

        if len(results) >= max_hits:
            break

    cik_filter_exhausted = False
    if cik_filter and not results:
        cik_filter_exhausted = True
        warnings.append(
            f"No results matched CIK {cik_filter} in the top {len(hit_list)} scored hits. "
            "For common search phrases, the company's filings may rank beyond the top 100. "
            "Try a more specific phrase, or run without --ticker and filter results manually."
        )

    return results, total_matched, warnings, cik_filter_exhausted


def main():
    parser = argparse.ArgumentParser(
        description="SEC EDGAR full-text search across filings (EFTS API)"
    )
    parser.add_argument("--query", required=True,
                        help=(
                            "Exact phrase to search. EFTS matches the literal phrase only. "
                            "No boolean operators, field-level syntax, wildcards, or proximity operators. "
                            "One phrase per call."
                        ))
    parser.add_argument("--ticker", default=None,
                        help="Company ticker to scope results (post-filtered by CIK from top 100 hits).")
    parser.add_argument("--forms", nargs="+", default=DEFAULT_FORMS,
                        help=f"Filing types to search (default: {' '.join(DEFAULT_FORMS)}).")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS,
                        help=f"Look-back window in years (default: {DEFAULT_YEARS}, max: 10).")
    parser.add_argument("--hits", type=int, default=DEFAULT_HITS,
                        help=f"Max results to return (default: {DEFAULT_HITS}).")
    args = parser.parse_args()

    years = min(args.years, 10)
    max_hits = min(args.hits, MAX_HITS_LIMIT)
    date_from, date_to = date_range_str(years)

    session = build_session()
    warnings = []
    cik = None

    # Resolve CIK from ticker
    if args.ticker:
        cik = resolve_cik(session, args.ticker)
        if not cik:
            warnings.append(
                f"CIK not found for ticker '{args.ticker}'. "
                "Search will return unscoped top results — review company names manually."
            )

    # Run EFTS search
    try:
        raw = run_search(session, args.query, args.forms, date_from, date_to)
    except requests.HTTPError as e:
        err_output(args, cik, date_from, date_to, [f"EFTS HTTP error {e.response.status_code}: {e}"])
        sys.exit(1)
    except Exception as e:
        err_output(args, cik, date_from, date_to, [f"EFTS request failed: {e}"])
        sys.exit(1)

    # Parse and filter
    cik_filter = cik if args.ticker else None
    results, total_matched, parse_warnings, cik_filter_exhausted = parse_results(raw, cik_filter, max_hits)
    warnings.extend(parse_warnings)

    output = {
        "source": "SEC EDGAR EFTS",
        "query": args.query,
        "ticker": args.ticker,
        "cik": cik,
        "forms_searched": args.forms,
        "date_range": {"from": date_from, "to": date_to},
        "results": results,
        "result_count": len(results),
        "total_matched": total_matched,
        "cik_filter_exhausted": cik_filter_exhausted,
        "warnings": warnings,
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
        "governance_note": (
            "EFTS is a retrieval tool — results are leads, not verified evidence. "
            "Verify material claims against the primary filing before use in analysis. "
            "Cannot anchor PM Verdict without primary filing confirmation (CLAUDE.md §11.7)."
        ),
    }

    print(json.dumps(output, indent=2, ensure_ascii=False))


def err_output(args, cik, date_from, date_to, errors):
    output = {
        "source": "SEC EDGAR EFTS",
        "query": args.query,
        "ticker": args.ticker,
        "cik": cik,
        "forms_searched": args.forms,
        "date_range": {"from": date_from, "to": date_to},
        "results": [],
        "result_count": 0,
        "total_matched": 0,
        "warnings": errors,
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
        "governance_note": (
            "EFTS is a retrieval tool — results are leads, not verified evidence. "
            "Verify material claims against the primary filing before use in analysis. "
            "Cannot anchor PM Verdict without primary filing confirmation (CLAUDE.md §11.7)."
        ),
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
