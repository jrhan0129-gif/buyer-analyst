#!/usr/bin/env python3
"""
filing_extractor.py — One-command primary filing retrieval + extraction.

Combines edgar_fetcher/hkex_fetcher → download → text extraction → key financial search
into a single script so Group B agent doesn't need separate curl/pdftotext permissions.

Usage:
    python3 skills/filing_extractor.py --ticker NVDA --exchange us --filing-type 10-K
    python3 skills/filing_extractor.py --ticker 1109 --exchange hk --filing-type annual_results
    python3 skills/filing_extractor.py --ticker NVDA --exchange us --filing-type 10-K --json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from html.parser import HTMLParser

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)


class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip = True

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


def run_fetcher(ticker, exchange, filing_type, years=1):
    """Run the appropriate fetcher and return filing metadata."""
    if exchange == "hk":
        cmd = [sys.executable, os.path.join(SCRIPT_DIR, "hkex_fetcher.py"),
               "--ticker", ticker, "--filing-type", filing_type, "--years", str(years)]
    else:
        cmd = [sys.executable, os.path.join(SCRIPT_DIR, "edgar_fetcher.py"),
               "--ticker", ticker, "--filing-type", filing_type, "--years", str(years)]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        filings = data.get("filings", [])
        if not filings:
            return None, data.get("warnings", ["No filings found"])
        return filings[0], []
    except Exception as e:
        return None, [f"Fetcher error: {e}"]


def download_filing(url, output_path, max_retries=3):
    """Download filing via urllib with retry, streaming, and size validation."""
    import urllib.request
    import time

    SEC_USER_AGENT = "BuyerAnalyst/1.0 (research@buyeranalyst.com)"
    MIN_FILE_SIZE = 10_000  # 10KB — any real filing is larger

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": SEC_USER_AGENT,
                "Accept-Encoding": "identity",
            })
            with urllib.request.urlopen(req, timeout=90) as resp:
                content_length = resp.headers.get("Content-Length")
                expected = int(content_length) if content_length else None

                with open(output_path, "wb") as f:
                    total = 0
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        total += len(chunk)

            # Validate download completeness
            if expected and total < expected * 0.95:
                raise IOError(
                    f"Incomplete download: got {total:,} of {expected:,} bytes "
                    f"({total/expected*100:.0f}%)"
                )
            if total < MIN_FILE_SIZE:
                raise IOError(
                    f"File too small: {total:,} bytes (min {MIN_FILE_SIZE:,}). "
                    f"Likely truncated or error page."
                )

            return True, None

        except Exception as e:
            if attempt < max_retries:
                wait = 2 ** attempt  # exponential backoff: 2, 4, 8 sec
                time.sleep(wait)
                continue
            return False, f"Failed after {max_retries} attempts: {e}"


def extract_text(filepath):
    """Extract text from HTML or PDF."""
    if filepath.endswith(".htm") or filepath.endswith(".html"):
        with open(filepath, "r", errors="ignore") as f:
            html = f.read()
        parser = HTMLTextExtractor()
        parser.feed(html)
        text = " ".join(parser.text)
        return re.sub(r'\s+', ' ', text)
    elif filepath.endswith(".pdf"):
        try:
            result = subprocess.run(["pdftotext", filepath, "-"],
                                    capture_output=True, text=True, timeout=30)
            return result.stdout
        except Exception as e:
            return f"[pdftotext failed: {e}]"
    else:
        with open(filepath, "r", errors="ignore") as f:
            return f.read()


def search_financials(text):
    """Search extracted text for key financial figures."""
    findings = {}
    searches = {
        "revenue": r'(?i)(total revenue|net revenue|revenue|net sales).{0,300}',
        "gross_margin": r'(?i)(gross profit|gross margin).{0,300}',
        "net_income": r'(?i)(net income|net profit|profit attributable).{0,300}',
        "operating_income": r'(?i)(operating income|operating profit|EBIT).{0,300}',
        "eps": r'(?i)(earnings per share|diluted.*per share|EPS).{0,200}',
        "cash": r'(?i)(cash.{0,20}(?:equivalent|and short)).{0,300}',
        "total_debt": r'(?i)(total debt|long.term debt|borrowings).{0,300}',
        "shares": r'(?i)(shares.{0,15}outstanding).{0,200}',
        "fcf": r'(?i)(free cash flow|net cash.{0,20}operat).{0,300}',
        "segments": r'(?i)(segment|reportable).{0,10}(revenue|sales|results).{0,400}',
        "customer_concentration": r'(?i)(customer.{0,30}(?:concentration|10%|accounted)).{0,400}',
        "book_value": r'(?i)(book value|total equity|shareholders.{0,10}equity).{0,300}',
    }

    for key, pattern in searches.items():
        matches = []
        for m in re.finditer(pattern, text):
            s = m.group()[:250]
            if any(c.isdigit() for c in s):
                matches.append(s.strip())
            if len(matches) >= 3:
                break
        if matches:
            findings[key] = matches

    return findings


def main():
    parser = argparse.ArgumentParser(description="One-command filing retrieval + extraction")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--exchange", required=True, choices=["us", "hk"])
    parser.add_argument("--filing-type", default="10-K",
                        help="US: 10-K, 10-Q, 8-K, 20-F. HK: annual_results, interim_results, all")
    parser.add_argument("--years", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    output = {
        "ticker": args.ticker,
        "exchange": args.exchange,
        "filing_type": args.filing_type,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status": "ok",
        "warnings": [],
    }

    # Step 1: Fetch filing metadata
    filing, warnings = run_fetcher(args.ticker, args.exchange, args.filing_type, args.years)
    output["warnings"].extend(warnings)

    if not filing:
        output["status"] = "no_filing_found"
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            print(f"[FILING_EXTRACTOR] No filing found for {args.ticker} ({args.filing_type})")
            for w in warnings:
                print(f"  WARNING: {w}")
        return

    url = filing.get("url", "")
    output["filing_metadata"] = filing

    # Step 2: Download
    ext = ".htm" if url.endswith(".htm") or url.endswith(".html") else ".pdf"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False, dir="/tmp",
                                      prefix=f"{args.ticker}_filing_") as tmp:
        tmp_path = tmp.name

    success, err = download_filing(url, tmp_path)
    if not success:
        output["status"] = "download_failed"
        output["warnings"].append(f"Download failed: {err}")
        output["filing_url"] = url
        if args.json:
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            print(f"[FILING_EXTRACTOR] Download failed: {err}")
            print(f"  URL: {url}")
            print(f"  → PM can manually: curl -sL -o /tmp/filing{ext} \"{url}\"")
        return

    output["local_path"] = tmp_path
    file_size = os.path.getsize(tmp_path)
    output["file_size_bytes"] = file_size

    # Step 3: Extract text
    text = extract_text(tmp_path)
    text_len = len(text)
    output["text_length"] = text_len

    # Step 4: Search for key financials
    findings = search_financials(text)
    output["key_financials"] = findings
    output["fields_found"] = list(findings.keys())
    output["fields_missing"] = [k for k in ["revenue", "net_income", "eps", "cash", "book_value"]
                                 if k not in findings]

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"\n{'='*60}")
        print(f"  FILING EXTRACTOR: {args.ticker} ({args.exchange.upper()})")
        print(f"  Filing: {filing.get('form', filing.get('title', ''))}")
        print(f"  Date: {filing.get('filing_date', filing.get('date', ''))}")
        print(f"  Size: {file_size:,} bytes | Text: {text_len:,} chars")
        print(f"{'='*60}\n")

        for key, matches in findings.items():
            print(f"  [{key.upper()}]")
            for m in matches:
                print(f"    {m[:200]}")
            print()

        if output["fields_missing"]:
            print(f"  MISSING: {', '.join(output['fields_missing'])}")

        if output["warnings"]:
            print(f"\n  WARNINGS:")
            for w in output["warnings"]:
                print(f"    {w}")

        print(f"\n  Local file: {tmp_path}")
        print(f"  Full text available for further extraction")


if __name__ == "__main__":
    main()
