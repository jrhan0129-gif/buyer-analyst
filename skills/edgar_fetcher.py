"""
edgar_fetcher.py — SEC EDGAR filing fetcher for buy-side research
Usage:
    python3 skills/edgar_fetcher.py --ticker AAPL --filing-type 10-K
    python3 skills/edgar_fetcher.py --ticker AAPL --filing-type 10-K --download-dir /tmp/filings
    python3 skills/edgar_fetcher.py --ticker MSFT --filing-type 10-Q --years 2

Filing type shortcuts:
    10-K    annual report
    10-Q    quarterly report
    8-K     current report (material events)
    DEF14A  proxy statement
    20-F    annual report (foreign private issuer)
    6-K     current report (foreign private issuer)
    all     no filter

Output: JSON with keys: source, ticker, cik, company_name, query_params, filings, warnings
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests

BASE_URL = "https://data.sec.gov"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar"
EDGAR_HEADERS = {
    "User-Agent": "BuyerAnalyst research@local.dev",
    "Accept": "application/json",
}

FORM_TYPE_MAP = {
    "annual":    "10-K",
    "quarterly": "10-Q",
    "current":   "8-K",
    "proxy":     "DEF 14A",
    "foreign_annual":  "20-F",
    "foreign_current": "6-K",
    "all":       "",
}


def build_session():
    s = requests.Session()
    s.trust_env = False
    s.headers.update(EDGAR_HEADERS)
    return s


def load_cik(session, ticker):
    r = session.get(TICKERS_URL, timeout=15)
    r.raise_for_status()
    data = r.json()
    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            return str(entry["cik_str"]), entry.get("title", "")
    return None, None


def load_submissions(session, cik):
    padded = cik.zfill(10)
    url = f"{BASE_URL}/submissions/CIK{padded}.json"
    r = session.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def filter_filings(submissions, form_type, from_date, to_date):
    recent = submissions.get("filings", {}).get("recent", {})
    forms       = recent.get("form", [])
    accessions  = recent.get("accessionNumber", [])
    dates_filed = recent.get("filingDate", [])
    primary_doc = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])
    cik = submissions.get("cik", "")

    results = []
    for form, acc, date_str, doc, desc in zip(forms, accessions, dates_filed, primary_doc, descriptions):
        if form_type and form.upper() != form_type.upper():
            continue
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if date_obj < from_date or date_obj > to_date:
            continue
        acc_clean = acc.replace("-", "")
        filing_url = f"{ARCHIVES_URL}/data/{int(cik)}/{acc_clean}/{doc}"
        index_url  = f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form}&dateb=&owner=include&count=1"
        results.append({
            "form":            form,
            "filing_date":     date_str,
            "accession":       acc,
            "primary_doc":     doc,
            "description":     desc,
            "url":             filing_url,
            "index_url":       f"{ARCHIVES_URL}/data/{int(cik)}/{acc_clean}/",
        })

    return results


def download_filing(session, filing, download_dir, cik):
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


def main():
    parser = argparse.ArgumentParser(description="SEC EDGAR filing fetcher")
    parser.add_argument("--ticker",       required=True, help="US ticker symbol (e.g. AAPL)")
    parser.add_argument("--filing-type",  default="10-K",
                        help="Form type: 10-K, 10-Q, 8-K, DEF14A, 20-F, 6-K, all")
    parser.add_argument("--years",        type=float, default=3,
                        help="Years of history (default: 3)")
    parser.add_argument("--max-results",  type=int, default=20,
                        help="Max filings to return (default: 20)")
    parser.add_argument("--download-dir", default=None,
                        help="Directory to download filings into")
    args = parser.parse_args()

    warnings = []
    session = build_session()

    cik, company_name = load_cik(session, args.ticker)
    if not cik:
        print(json.dumps({
            "error": f"Ticker '{args.ticker}' not found in SEC company tickers list",
            "source": "edgar_primary",
            "warnings": ["Verify the ticker is US-listed. For foreign issuers, check 20-F/6-K filings."]
        }))
        sys.exit(1)

    try:
        submissions = load_submissions(session, cik)
    except Exception as e:
        print(json.dumps({"error": f"Failed to load submissions for CIK {cik}: {e}", "source": "edgar_primary"}))
        sys.exit(1)

    company_name = company_name or submissions.get("name", "")
    to_date   = datetime.now()
    from_date = to_date - timedelta(days=int(args.years * 365))

    form_type = args.filing_type.upper()
    if form_type == "ALL":
        form_type = ""
    form_type_mapped = FORM_TYPE_MAP.get(args.filing_type.lower(), form_type)
    if form_type_mapped:
        form_type = form_type_mapped

    filings = filter_filings(submissions, form_type, from_date, to_date)

    if len(filings) > args.max_results:
        warnings.append(
            f"Found {len(filings)} matching filings; returning first {args.max_results}. Use --max-results to increase."
        )
        filings = filings[:args.max_results]

    if args.download_dir:
        Path(args.download_dir).mkdir(parents=True, exist_ok=True)
        for f in filings:
            path, status = download_filing(session, f, args.download_dir, cik)
            f["local_path"] = path
            f["download_status"] = status

    output = {
        "source":         "edgar_primary",
        "retrieved_at":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker":         args.ticker.upper(),
        "cik":            cik,
        "company_name":   company_name,
        "returned_count": len(filings),
        "query_params": {
            "form_type": form_type or "(all)",
            "from_date": from_date.strftime("%Y-%m-%d"),
            "to_date":   to_date.strftime("%Y-%m-%d"),
            "years":     args.years,
        },
        "filings":  filings,
        "warnings": warnings,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
