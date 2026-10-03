"""
hkex_fetcher.py — HKEx filing fetcher for buy-side research
Usage:
    python3 skills/hkex_fetcher.py --ticker 02601 --filing-type annual_results
    python3 skills/hkex_fetcher.py --ticker 02601 --filing-type annual_results --download-dir /tmp/filings
    python3 skills/hkex_fetcher.py --ticker 02601 --title-keyword "results" --years 2
    python3 skills/hkex_fetcher.py --ticker 02601 --list-all --years 1

Filing type shortcuts (with fallback keywords):
    annual_results      -> "annual results" | "results for the financial year"
    interim_results     -> "interim results" | "results for the six months"
    annual_report       -> "annual report"
    circular            -> "circular"
    proxy               -> "proxy"
    esg                 -> "environmental, social"
    all                 -> (no title filter)

Output: JSON with keys: source, ticker, stock_name, query_params, filings, warnings
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

BASE_URL = "https://www1.hkexnews.hk"
STOCK_JSON = "/ncms/script/eds/activestock_sehk_e.json"
SEARCH_API = "/search/titleSearchServlet.do"

FILING_TYPE_MAP = {
    "annual_results":  ["annual results", "results for the financial year"],
    "interim_results": ["interim results", "results for the six months"],
    "annual_report":   ["annual report"],
    "circular":        ["circular"],
    "proxy":           ["proxy"],
    "esg":             ["environmental, social"],
    "all":             [""],
}


def build_session():
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, */*",
        "Referer": urljoin(BASE_URL, "/search/titlesearch.xhtml"),
    })
    return s


def load_stock_list(session):
    r = session.get(urljoin(BASE_URL, STOCK_JSON), timeout=15)
    r.raise_for_status()
    return r.json()


def find_stock(stocks, ticker):
    ticker = ticker.upper().lstrip("0")
    padded = ticker.zfill(5)
    for s in stocks:
        code = s.get("c", "")
        if code == padded or code.lstrip("0") == ticker:
            return s
        if s.get("n", "").upper() == ticker or s.get("s", "") == ticker:
            return s
    return None


def search_filings(session, stock_id, title_keyword, from_date, to_date, row_range=50):
    params = {
        "lang":          "E",
        "category":      "0",
        "market":        "SEHK",
        "searchType":    "1",
        "stockId":       str(stock_id),
        "t1code":        "",
        "t2Gcode":       "",
        "t2code":        "",
        "documentType":  "",
        "title":         title_keyword,
        "fromDate":      from_date,
        "toDate":        to_date,
        "sortDir":       "0",
        "sortByOptions": "DateTime",
        "rowRange":      str(row_range),
    }
    r = session.get(urljoin(BASE_URL, SEARCH_API), params=params, timeout=25)
    r.raise_for_status()
    data = r.json()
    raw = data.get("result", "[]")
    items = json.loads(raw) if isinstance(raw, str) else raw
    return items, params


def parse_filings(items):
    filings = []
    for item in items:
        file_link = item.get("FILE_LINK", "")
        url = urljoin(BASE_URL, file_link) if file_link else None
        filings.append({
            "news_id":    item.get("NEWS_ID", ""),
            "date":       item.get("DATE_TIME", "")[:10],
            "title":      item.get("TITLE", ""),
            "category":   item.get("SHORT_TEXT", "").replace("<br/>", "").strip(),
            "file_type":  item.get("FILE_TYPE", ""),
            "file_size":  item.get("FILE_INFO", ""),
            "url":        url,
            "stock_code": item.get("STOCK_CODE", ""),
            "stock_name": item.get("STOCK_NAME", ""),
        })
    return filings


def download_filing(session, filing, download_dir):
    url = filing.get("url")
    if not url:
        return None, "no_url"
    date_str = filing["date"].replace("/", "")
    safe_title = filing["title"][:60].replace("/", "_").replace(" ", "_")
    filename = f"{date_str}_{filing['news_id']}_{safe_title}.pdf"
    dest = Path(download_dir) / filename
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
    parser = argparse.ArgumentParser(description="HKEx filing fetcher")
    parser.add_argument("--ticker",         required=True, help="Stock code (e.g. 02601 or CPIC)")
    parser.add_argument("--filing-type",    default="all",
                        choices=list(FILING_TYPE_MAP.keys()),
                        help="Filing type shortcut")
    parser.add_argument("--title-keyword",  default=None,
                        help="Custom title keyword (overrides --filing-type)")
    parser.add_argument("--years",          type=float, default=3,
                        help="Years of history to fetch (default: 3)")
    parser.add_argument("--max-results",    type=int, default=50,
                        help="Max filings to return (default: 50)")
    parser.add_argument("--download-dir",   default=None,
                        help="Directory to download PDFs into")
    parser.add_argument("--list-all",       action="store_true",
                        help="Fetch all filings without title filter")
    args = parser.parse_args()

    warnings = []
    session = build_session()

    try:
        stocks = load_stock_list(session)
    except Exception as e:
        print(json.dumps({"error": f"Failed to load stock list: {e}", "source": "hkex_primary"}))
        sys.exit(1)

    entry = find_stock(stocks, args.ticker)
    if not entry:
        print(json.dumps({
            "error": f"Ticker '{args.ticker}' not found in HKEx active stock list",
            "source": "hkex_primary",
            "warnings": [f"Check ticker format. HKEx codes are 5-digit (e.g. 02601)."]
        }))
        sys.exit(1)

    stock_id = entry["i"]
    stock_code = entry["c"]
    stock_name = entry["n"]

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=int(args.years * 365))).strftime("%Y%m%d")

    if args.list_all:
        keywords = [""]
    elif args.title_keyword is not None:
        keywords = [args.title_keyword]
    else:
        keywords = FILING_TYPE_MAP.get(args.filing_type, [""])

    items = []
    title_kw = keywords[0]
    for kw in keywords:
        try:
            candidate_items, query_params = search_filings(
                session, stock_id, kw, start_date, end_date, args.max_results
            )
        except Exception as e:
            warnings.append(f"Search with keyword '{kw}' failed: {e}")
            continue
        if candidate_items:
            items = candidate_items
            title_kw = kw
            break
        warnings.append(f"No results for keyword '{kw}', trying next variant...")

    if not items and len(keywords) > 1:
        warnings.append(
            f"All keyword variants exhausted ({keywords}). "
            f"Try --title-keyword with a custom phrase or --list-all."
        )

    total_count = int(items[0].get("TOTAL_COUNT", 0)) if items else 0
    filings = parse_filings(items)

    if total_count > args.max_results:
        warnings.append(
            f"Total filings matching query: {total_count}. "
            f"Returned: {len(filings)} (use --max-results to increase)."
        )

    if args.download_dir:
        Path(args.download_dir).mkdir(parents=True, exist_ok=True)
        for f in filings:
            if f["file_type"] == "PDF":
                path, status = download_filing(session, f, args.download_dir)
                f["local_path"] = path
                f["download_status"] = status
            else:
                f["local_path"] = None
                f["download_status"] = f"skipped ({f['file_type']})"

    output = {
        "source":         "hkex_primary",
        "retrieved_at":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker":         stock_code,
        "stock_name":     stock_name,
        "total_count":    total_count,
        "returned_count": len(filings),
        "query_params": {
            "title_keyword": title_kw or "(all)",
            "from_date":     start_date,
            "to_date":       end_date,
            "years":         args.years,
        },
        "filings":  filings,
        "warnings": warnings,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
