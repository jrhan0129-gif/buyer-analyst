#!/usr/bin/env python3
"""
earnings_transcript_fetcher.py — Earnings call transcript & press release fetcher

Retrieves earnings-related disclosures for buy-side Text Evidence Line analysis.
NOT a truth engine — transcript content requires cross-check against primary filings.

Sources (tried in priority order by exchange):
  US:  FMP transcript (requires FMP_API_KEY) → EDGAR 8-K → Tavily query hints
  HK:  HKEx results announcements → Tavily query hints

Usage:
    python3 skills/earnings_transcript_fetcher.py --ticker AAPL
    python3 skills/earnings_transcript_fetcher.py --ticker AAPL --quarter 1 --year 2025
    python3 skills/earnings_transcript_fetcher.py --ticker AAPL --download-dir /tmp/transcripts
    python3 skills/earnings_transcript_fetcher.py --ticker 02601 --exchange hkex
    python3 skills/earnings_transcript_fetcher.py --ticker MSFT --source edgar --years 1

Source selection:
    auto    (default) try all sources for detected exchange
    fmp     FMP transcripts only (US)
    edgar   EDGAR 8-K filings only (US)
    hkex    HKEx results announcements only (HK)

Output: JSON with keys: source, ticker, exchange, company_name, transcripts, filings,
        tavily_fallback, summary, warnings

Setup:
    FMP (optional): export FMP_API_KEY=your_key  (free at financialmodelingprep.com, 250 calls/day)
    No key required for EDGAR or HKEx sources.

Design notes:
    - FMP transcripts: content_preview (first 3000 chars) in JSON; full text saved when --download-dir set
    - EDGAR 8-K: returns filing URLs with likely_earnings flag; download via edgar_fetcher.py or --download-dir
    - HKEx: reuses hkex_fetcher.py search pattern (stockId resolution via activestock_sehk_e.json)
    - Tavily fallback: outputs ready-to-use search queries, does NOT auto-call tavily_search.py
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests

# ─── Constants ───────────────────────────────────────────────

EDGAR_HEADERS = {
    "User-Agent": "BuyerAnalyst research@local.dev",
    "Accept": "application/json",
}
EDGAR_BASE = "https://data.sec.gov"
EDGAR_ARCHIVES = "https://www.sec.gov/Archives/edgar"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

FMP_BASE = "https://financialmodelingprep.com/api"

HKEX_BASE = "https://www1.hkexnews.hk"
HKEX_STOCK_JSON = "/ncms/script/eds/activestock_sehk_e.json"
HKEX_SEARCH_API = "/search/titleSearchServlet.do"

PREVIEW_CHARS = 3000


# ─── Helpers ─────────────────────────────────────────────────

def build_session(headers=None):
    s = requests.Session()
    s.trust_env = False
    if headers:
        s.headers.update(headers)
    return s


def detect_exchange(ticker, exchange_arg):
    if exchange_arg:
        return exchange_arg.lower()
    if re.match(r"^\d{4,5}$", str(ticker)):
        return "hkex"
    return "us"


def log(msg):
    print(f"[earnings] {msg}", file=sys.stderr)


# ─── FMP Transcript Source ───────────────────────────────────

def fetch_fmp_transcripts(ticker, quarter=None, year=None, limit=4):
    """Fetch earnings call transcripts from Financial Modeling Prep API."""
    api_key = os.environ.get("FMP_API_KEY")
    if not api_key:
        return [], None, ["FMP_API_KEY not set — skipping. Free key: financialmodelingprep.com"]

    warnings = []
    results = []
    company_name = None

    try:
        # If specific quarter requested, fetch directly
        if quarter and year:
            url = f"{FMP_BASE}/v3/earning_call_transcript/{ticker}?year={year}&quarter={quarter}&apikey={api_key}"
            r = requests.get(url, timeout=20)
            if r.status_code in (403, 401):
                return [], None, ["FMP_API_KEY invalid or quota exceeded"]
            if r.status_code == 429:
                return [], None, ["FMP rate limit hit — retry later"]
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):
                results = data[:limit]
        else:
            # List available transcripts, then fetch most recent ones
            list_url = f"{FMP_BASE}/v4/earning_call_transcript?symbol={ticker}&apikey={api_key}"
            r = requests.get(list_url, timeout=20)
            if r.status_code in (403, 401):
                return [], None, ["FMP_API_KEY invalid or quota exceeded"]
            if r.status_code == 429:
                return [], None, ["FMP rate limit hit — retry later"]
            r.raise_for_status()
            listing = r.json()

            if not listing:
                return [], None, [f"No FMP transcripts available for {ticker}"]

            # Fetch individual transcripts for recent quarters
            entries = listing[:limit]
            for entry in entries:
                q = entry.get("quarter")
                y = entry.get("year")
                if not (q and y):
                    continue
                detail_url = f"{FMP_BASE}/v3/earning_call_transcript/{ticker}?year={y}&quarter={q}&apikey={api_key}"
                try:
                    dr = requests.get(detail_url, timeout=20)
                    dr.raise_for_status()
                    detail = dr.json()
                    if isinstance(detail, list) and detail:
                        results.extend(detail)
                    time.sleep(0.3)  # respect rate limits
                except Exception as e:
                    warnings.append(f"FMP Q{q} {y} fetch failed: {e}")

        if not results:
            return [], None, [f"No FMP transcripts found for {ticker}"]

        # Clean up results
        cleaned = []
        for item in results:
            content = item.get("content", "")
            cleaned.append({
                "quarter": item.get("quarter"),
                "year": item.get("year"),
                "date": item.get("date", ""),
                "content_length": len(content),
                "content_preview": content[:PREVIEW_CHARS] + ("..." if len(content) > PREVIEW_CHARS else ""),
                "_full_content": content,  # stripped before JSON output unless saving
            })

        return cleaned, company_name, warnings

    except Exception as e:
        return [], None, [f"FMP error: {e}"]


# ─── EDGAR 8-K Source ────────────────────────────────────────

EARNINGS_KEYWORDS = [
    "result", "earnings", "financial result", "quarter", "annual result",
    "revenue", "press release", "income", "operating result",
]


def check_8k_items(session, index_url):
    """Check 8-K filing index for Item 2.02 (Results of Operations) reference."""
    try:
        r = session.get(index_url, timeout=10)
        if r.status_code != 200:
            return None
        text = r.text.lower()
        # Item 2.02 = Results of Operations and Financial Condition (earnings)
        if "2.02" in text or "results of operations" in text:
            return True
        # Item 7.01 / 8.01 = Regulation FD / Other Events (sometimes earnings)
        if "regulation fd" in text and ("earning" in text or "result" in text):
            return True
        return False
    except Exception:
        return None


def fetch_edgar_earnings(ticker, years=2, max_results=10):
    """Fetch recent 8-K filings (earnings press releases) from EDGAR."""
    session = build_session(EDGAR_HEADERS)
    warnings = []

    # Resolve CIK
    try:
        r = session.get(SEC_TICKERS_URL, timeout=15)
        r.raise_for_status()
        data = r.json()
        cik, company_name = None, None
        for entry in data.values():
            if entry.get("ticker", "").upper() == ticker.upper():
                cik = str(entry["cik_str"])
                company_name = entry.get("title", "")
                break
        if not cik:
            return [], None, [f"Ticker '{ticker}' not found in SEC tickers"]
    except Exception as e:
        return [], None, [f"SEC ticker lookup failed: {e}"]

    time.sleep(0.2)

    # Fetch submissions
    try:
        padded = cik.zfill(10)
        url = f"{EDGAR_BASE}/submissions/CIK{padded}.json"
        r = session.get(url, timeout=15)
        r.raise_for_status()
        submissions = r.json()
    except Exception as e:
        return [], company_name, [f"EDGAR submissions failed: {e}"]

    # Also check items field for Item 2.02 detection
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates_filed = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])
    items_list = recent.get("items", [])  # EDGAR includes items for 8-K

    cutoff = datetime.now() - timedelta(days=int(years * 365))
    results = []

    for idx, (form, acc, date_str, doc, desc) in enumerate(
        zip(forms, accessions, dates_filed, primary_docs, descriptions)
    ):
        if form != "8-K":
            continue
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if date_obj < cutoff:
            continue

        # Check earnings likelihood from description
        desc_lower = (desc or "").lower()
        is_earnings = any(kw in desc_lower for kw in EARNINGS_KEYWORDS)

        # Check items field (Item 2.02 = Results of Operations)
        items_str = items_list[idx] if idx < len(items_list) else ""
        if "2.02" in str(items_str):
            is_earnings = True

        acc_clean = acc.replace("-", "")
        filing_url = f"{EDGAR_ARCHIVES}/data/{int(cik)}/{acc_clean}/{doc}"
        index_url = f"{EDGAR_ARCHIVES}/data/{int(cik)}/{acc_clean}/"

        results.append({
            "form": form,
            "filing_date": date_str,
            "accession": acc,
            "primary_doc": doc,
            "description": desc,
            "items": items_str,
            "url": filing_url,
            "index_url": index_url,
            "likely_earnings": is_earnings,
        })

    # Sort: most recent first, earnings-likely prioritized
    results.sort(key=lambda x: x["filing_date"], reverse=True)
    earnings = [r for r in results if r["likely_earnings"]]
    others = [r for r in results if not r["likely_earnings"]]
    combined = (earnings + others)[:max_results]

    if len(results) > max_results:
        warnings.append(f"Found {len(results)} 8-K filings; returning {max_results} (earnings-flagged first)")

    return combined, company_name, warnings


def download_edgar_filing(session, filing, download_dir, cik=None):
    url = filing.get("url")
    if not url:
        return None, "no_url"
    safe = filing["accession"].replace("-", "") + "_" + (filing["primary_doc"] or "filing.htm")
    dest = Path(download_dir) / safe
    if dest.exists():
        return str(dest), "cached"
    try:
        r = session.get(url, timeout=30, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
        time.sleep(0.5)
        return str(dest), "downloaded"
    except Exception as e:
        return None, str(e)


# ─── HKEx Results Source ─────────────────────────────────────

def fetch_hkex_results(ticker_code, years=2, max_results=10):
    """Fetch results announcements from HKEx."""
    session = build_session({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, */*",
        "Referer": f"{HKEX_BASE}/search/titlesearch.xhtml",
    })
    warnings = []

    # Resolve stock ID via active stock list (same as hkex_fetcher.py)
    try:
        r = session.get(urljoin(HKEX_BASE, HKEX_STOCK_JSON), timeout=15)
        r.raise_for_status()
        stocks = r.json()
    except Exception as e:
        return [], None, [f"HKEx stock list failed: {e}"]

    ticker_clean = str(ticker_code).upper().lstrip("0")
    padded = ticker_clean.zfill(5)
    stock_entry = None
    for s in stocks:
        code = s.get("c", "")
        if code == padded or code.lstrip("0") == ticker_clean:
            stock_entry = s
            break
        if s.get("n", "").upper() == ticker_clean or s.get("s", "") == ticker_clean:
            stock_entry = s
            break

    if not stock_entry:
        return [], None, [f"Ticker '{ticker_code}' not found in HKEx active stock list"]

    stock_id = stock_entry["i"]
    stock_code = stock_entry["c"]
    stock_name = stock_entry["n"]

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=int(years * 365))).strftime("%Y%m%d")

    # Search for results announcements
    params = {
        "lang": "E",
        "category": "0",
        "market": "SEHK",
        "searchType": "1",
        "stockId": str(stock_id),
        "t1code": "",
        "t2Gcode": "",
        "t2code": "",
        "documentType": "",
        "title": "results",
        "fromDate": start_date,
        "toDate": end_date,
        "sortDir": "0",
        "sortByOptions": "DateTime",
        "rowRange": str(max_results),
    }

    try:
        r = session.get(urljoin(HKEX_BASE, HKEX_SEARCH_API), params=params, timeout=25)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], stock_name, [f"HKEx search failed: {e}"]

    raw = data.get("result", "[]")
    items = json.loads(raw) if isinstance(raw, str) else raw

    filings = []
    for item in items:
        file_link = item.get("FILE_LINK", "")
        url = urljoin(HKEX_BASE, file_link) if file_link else None
        filings.append({
            "date": item.get("DATE_TIME", "")[:10],
            "title": item.get("TITLE", ""),
            "url": url,
            "file_type": item.get("FILE_TYPE", ""),
            "file_size": item.get("FILE_INFO", ""),
            "stock_code": item.get("STOCK_CODE", ""),
            "stock_name": item.get("STOCK_NAME", ""),
            "source": "hkex",
        })

    return filings, stock_name, warnings


# ─── Tavily Fallback Queries ─────────────────────────────────

def generate_tavily_queries(company_name, ticker, exchange, quarter=None, year=None):
    """Generate ready-to-use Tavily L1 search queries for transcript discovery."""
    name = company_name or ticker
    queries = []

    if quarter and year:
        queries.append(f'"{name}" Q{quarter} {year} earnings call transcript')
        queries.append(f'"{name}" Q{quarter} {year} conference call transcript')
    else:
        current_year = datetime.now().year
        queries.append(f'"{name}" {current_year} earnings call transcript')
        queries.append(f'"{name}" latest quarterly earnings conference call')

    if exchange == "hkex":
        domains = ["hkexnews.hk", "reuters.com", "bloomberg.com"]
    else:
        domains = ["seekingalpha.com", "fool.com", "nasdaq.com"]

    domain_str = ",".join(domains)
    # Use first query without outer quotes to avoid nested escaping
    hint_query = queries[0].replace('"', '')
    return {
        "queries": queries,
        "suggested_domains": domains,
        "usage_hint": f'python3 skills/tavily_search.py l1 "{hint_query}" --domains {domain_str}',
    }


# ─── Main ────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Earnings transcript & press release fetcher")
    parser.add_argument("--ticker", required=True, help="Ticker symbol (e.g. AAPL, MSFT, 02601)")
    parser.add_argument("--exchange", default=None, choices=["us", "hkex"],
                        help="Exchange (auto-detected from ticker format if omitted)")
    parser.add_argument("--quarter", type=int, default=None, choices=[1, 2, 3, 4],
                        help="Fiscal quarter (1-4)")
    parser.add_argument("--year", type=int, default=None, help="Fiscal year (e.g. 2025)")
    parser.add_argument("--years", type=float, default=2,
                        help="Years of history to search (default: 2)")
    parser.add_argument("--max-results", type=int, default=8,
                        help="Max results per source (default: 8)")
    parser.add_argument("--source", default="auto", choices=["auto", "fmp", "edgar", "hkex"],
                        help="Source selection (default: auto)")
    parser.add_argument("--download-dir", default=None,
                        help="Save full transcripts/filings to this directory")
    args = parser.parse_args()

    exchange = detect_exchange(args.ticker, args.exchange)
    warnings = []
    transcripts = []
    filings = []
    company_name = None
    transcript_source = None

    # ── US path ──
    if exchange == "us":

        # FMP: full verbatim transcripts
        if args.source in ("auto", "fmp"):
            log(f"Trying FMP transcripts for {args.ticker}...")
            fmp_results, fmp_name, fmp_warnings = fetch_fmp_transcripts(
                args.ticker.upper(), args.quarter, args.year, limit=args.max_results
            )
            warnings.extend(fmp_warnings)
            if fmp_results:
                transcripts = fmp_results
                transcript_source = "fmp"
                if fmp_name:
                    company_name = fmp_name
                log(f"FMP: {len(fmp_results)} transcript(s) found")

                # Save full content if download-dir set
                if args.download_dir:
                    Path(args.download_dir).mkdir(parents=True, exist_ok=True)
                    for t in transcripts:
                        full = t.pop("_full_content", "")
                        if full:
                            q = t.get("quarter", "")
                            y = t.get("year", "")
                            fname = f"{args.ticker.upper()}_Q{q}_{y}_transcript.txt"
                            dest = Path(args.download_dir) / fname
                            dest.write_text(full, encoding="utf-8")
                            t["local_path"] = str(dest)
                            t["download_status"] = "saved"
                else:
                    # Strip full content from output to keep JSON manageable
                    for t in transcripts:
                        t.pop("_full_content", None)

        # EDGAR: 8-K earnings press releases
        if args.source in ("auto", "edgar"):
            log(f"Fetching EDGAR 8-K for {args.ticker}...")
            edgar_results, edgar_name, edgar_warnings = fetch_edgar_earnings(
                args.ticker, years=args.years, max_results=args.max_results
            )
            warnings.extend(edgar_warnings)
            if edgar_results:
                filings = edgar_results
            if edgar_name and not company_name:
                company_name = edgar_name

            # Download EDGAR filings if requested
            if args.download_dir and filings:
                session = build_session(EDGAR_HEADERS)
                Path(args.download_dir).mkdir(parents=True, exist_ok=True)
                for f in filings:
                    path, status = download_edgar_filing(session, f, args.download_dir)
                    f["local_path"] = path
                    f["download_status"] = status

            log(f"EDGAR: {len(filings)} 8-K filing(s) found")

    # ── HK path ──
    elif exchange == "hkex":

        if args.source in ("auto", "hkex"):
            log(f"Fetching HKEx results for {args.ticker}...")
            hkex_results, hkex_name, hkex_warnings = fetch_hkex_results(
                args.ticker, years=args.years, max_results=args.max_results
            )
            warnings.extend(hkex_warnings)
            if hkex_results:
                filings = hkex_results
            if hkex_name:
                company_name = hkex_name
            log(f"HKEx: {len(filings)} results filing(s) found")

    # ── Tavily fallback (always generate) ──
    tavily_fallback = generate_tavily_queries(
        company_name, args.ticker, exchange, args.quarter, args.year
    )

    # ── Build output ──
    ticker_display = args.ticker.upper() if exchange == "us" else args.ticker
    output = {
        "source": "earnings_transcript_fetcher",
        "retrieved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker": ticker_display,
        "exchange": exchange,
        "company_name": company_name,
        "summary": {
            "transcripts_found": len(transcripts),
            "transcript_source": transcript_source,
            "filings_found": len(filings),
            "has_tavily_fallback": True,
        },
        "transcripts": transcripts,
        "filings": filings,
        "tavily_fallback": tavily_fallback,
        "warnings": warnings,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
