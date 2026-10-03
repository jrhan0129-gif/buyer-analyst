"""
insider_tracker.py — Insider transaction tracker for buy-side research

Tracks director/officer/major-shareholder buy and sell activity.

Sources:
  SEC (US tickers): EDGAR submissions API + Form 4 XML parsing.
    Provides: insider name, role, transaction date, shares, price,
    transaction type (buy/sell/grant/exercise), post-transaction holdings.
  HKEx (HK tickers): HKExNews title search for "Next Day Disclosure Return"
    and connected-person announcement filings.
    Limitation: Full per-insider beneficial interest data requires the HKEx DI
    portal (di.hkex.com.hk), which may be inaccessible outside HK. When
    inaccessible, the script falls back to HKExNews filing listings only.

Usage:
    python3 skills/insider_tracker.py --ticker TSLA --years 1
    python3 skills/insider_tracker.py --ticker TSLA --years 2 --open-market-only
    python3 skills/insider_tracker.py --ticker 00700 --exchange hkex --years 1

Arguments:
    --ticker            Company ticker (US: e.g. TSLA; HK: e.g. 00700)
    --exchange          sec (default) | hkex
    --years             Look-back window in years (default: 1, max: 5)
    --open-market-only  SEC only: exclude grants (G), tax withholdings (F),
                        option exercises (M/X) — show only open-market buys (P)
                        and open-market sales (S)
    --hits              Max transactions to return (default: 50)

Output: JSON with keys:
    source, ticker, cik, exchange, date_range,
    transactions, insider_summary, cluster_signals,
    result_count, warnings, retrieved_at, governance_note

SEC transaction codes:
    P = open-market purchase  S = open-market sale
    A = grant/award           D = disposition (gift/other)
    M = option exercise (into shares)  F = tax withholding
    G = gift  J = other acquisition  X = exercise of derivative

Governance (CLAUDE.md §11.8):
    Insider data is a directional signal, not a primary valuation input.
    Form 4 data has a 2-business-day reporting lag; some transactions report late.
    Verify large or unusual transactions against primary filings before citing.
    Cannot anchor PM Verdict without corroborating evidence from other lines.
"""

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import urlencode, urljoin

import requests

EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_clean}/{primary_doc}"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
HKEX_SEARCH_URL = "https://www1.hkexnews.hk/search/titleSearchServlet.do"

EDGAR_HEADERS = {
    "User-Agent": "BuyerAnalyst research@local.dev",
    "Accept": "application/json",
}

# Open-market transaction codes (buy/sell only)
OPEN_MARKET_CODES = {"P", "S"}
# All codes that represent disposals (net sellers)
SELL_CODES = {"S", "D", "G", "F"}
# All codes that represent acquisitions
BUY_CODES = {"P", "A", "M", "X", "J"}

# Cluster detection: flag if N+ insiders trade within this window
CLUSTER_WINDOW_DAYS = 30
CLUSTER_MIN_INSIDERS = 3

DEFAULT_YEARS = 1
DEFAULT_HITS = 50


def build_session():
    s = requests.Session()
    s.trust_env = False
    s.headers.update(EDGAR_HEADERS)
    return s


# ─── SEC / EDGAR ──────────────────────────────────────────────────────────────

def resolve_cik(session, ticker):
    """Return zero-padded 10-digit CIK string, or None."""
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


def get_form4_filings(session, cik, date_from_str, date_to_str, max_filings=200):
    """
    Fetch Form 4 filing metadata from EDGAR submissions API.
    Returns (list of filing dicts, company_name, warnings).
    Note: only reads the 'recent' batch from submissions API. For prolific filers
    with multi-year lookback, older filings in paginated batches may be missed.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    r = session.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()
    warnings = []

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    report_dates = recent.get("reportDate", [])

    results = []
    date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
    date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()

    # Track whether we found any Form 4 that fell before our date window
    # (indicates the recent batch covers our full window)
    saw_before_window = False

    for i, form in enumerate(forms):
        if form not in ("4", "4/A"):
            continue
        filing_date_str = dates[i] if i < len(dates) else ""
        try:
            filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if filing_date < date_from:
            saw_before_window = True
            continue
        if filing_date > date_to:
            continue

        results.append({
            "filing_date": filing_date_str,
            "report_date": report_dates[i] if i < len(report_dates) else "",
            "accession_number": accessions[i] if i < len(accessions) else "",
            "primary_document": primary_docs[i] if i < len(primary_docs) else "",
        })

        if len(results) >= max_filings:
            break

    # If we never saw a Form 4 before our date window AND there are pagination files,
    # the recent batch may not fully cover the lookback window
    older_files = data.get("filings", {}).get("files", [])
    if not saw_before_window and older_files and results:
        warnings.append(
            f"EDGAR 'recent' batch may not cover the full lookback window ({date_from_str} to {date_to_str}). "
            f"{len(older_files)} older filing batch(es) exist but were not fetched. "
            "Older Form 4 filings may be missing from results."
        )

    return results, data.get("name", ""), warnings


def xml_val(element, path):
    """Find a field in Form 4 XML, unwrapping <value> if present."""
    node = element.find(".//" + path)
    if node is None:
        return ""
    value_node = node.find("value")
    if value_node is not None and value_node.text:
        return value_node.text.strip()
    return node.text.strip() if node.text else ""


def parse_form4_xml(xml_text, filing_date, accession_number, cik):
    """
    Parse Form 4 XML into a list of transaction dicts.
    Handles both nonDerivativeTransaction and derivativeTransaction.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return [], {}

    def find_text(path):
        return xml_val(root, path)

    insider_info = {
        "name": find_text("rptOwnerName"),
        "cik": find_text("rptOwnerCik"),
        "is_director": find_text("isDirector") == "1",
        "is_officer": find_text("isOfficer") == "1",
        "is_10pct_owner": find_text("isTenPercentOwner") == "1",
        "title": find_text("officerTitle"),
    }

    transactions = []

    def parse_tx(tx_el, is_derivative=False):
        code = xml_val(tx_el, "transactionCode")
        if not code:
            return None

        try:
            shares = float(xml_val(tx_el, "transactionShares") or 0)
        except ValueError:
            shares = 0.0

        try:
            price = float(xml_val(tx_el, "transactionPricePerShare") or 0)
        except ValueError:
            price = 0.0

        acq_disp = xml_val(tx_el, "transactionAcquiredDisposedCode")

        try:
            post_shares = float(xml_val(tx_el, "sharesOwnedFollowingTransaction") or 0)
        except ValueError:
            post_shares = 0.0

        tx_date = xml_val(tx_el, "transactionDate") or filing_date
        security = xml_val(tx_el, "securityTitle")
        direct_indirect = xml_val(tx_el, "directOrIndirectOwnership")

        value_usd = shares * price if price > 0 else None

        return {
            "insider_name": insider_info["name"],
            "insider_title": insider_info["title"],
            "is_director": insider_info["is_director"],
            "is_officer": insider_info["is_officer"],
            "is_10pct_owner": insider_info["is_10pct_owner"],
            "transaction_date": tx_date,
            "filing_date": filing_date,
            "transaction_code": code,
            "transaction_type": _code_label(code),
            "shares": shares,
            "price_per_share": price,
            "value_usd": round(value_usd, 2) if value_usd is not None else None,
            "acquired_disposed": acq_disp,
            "post_transaction_shares": post_shares,
            "security_title": security,
            "ownership_type": "direct" if direct_indirect == "D" else "indirect" if direct_indirect == "I" else direct_indirect,
            "is_derivative": is_derivative,
            "accession_number": accession_number,
            "filing_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_number.replace('-','')}/{accession_number}-index.htm",
        }

    for tx_el in root.findall(".//nonDerivativeTransaction"):
        tx = parse_tx(tx_el, is_derivative=False)
        if tx:
            transactions.append(tx)

    for tx_el in root.findall(".//derivativeTransaction"):
        tx = parse_tx(tx_el, is_derivative=True)
        if tx:
            transactions.append(tx)

    return transactions, insider_info


def _code_label(code):
    labels = {
        "P": "open-market purchase",
        "S": "open-market sale",
        "A": "grant/award",
        "D": "disposition",
        "M": "option exercise",
        "F": "tax withholding/sale",
        "G": "gift",
        "J": "other acquisition",
        "X": "derivative exercise",
        "C": "conversion",
        "W": "will/inheritance",
        "Z": "voting trust deposit",
        "I": "discretionary transaction",
        "U": "tender of shares",
    }
    return labels.get(code, code)


def fetch_sec_transactions(session, cik, date_from, date_to,
                           open_market_only=False, max_hits=DEFAULT_HITS):
    """
    Fetch and parse all Form 4 transactions for a company within the date range.
    Returns list of transaction dicts.
    """
    filings, _, pagination_warnings = get_form4_filings(session, cik, date_from, date_to, max_filings=300)
    warnings = list(pagination_warnings)

    if not filings:
        return [], warnings

    all_transactions = []
    cik_stripped = str(int(cik))
    rate_limit_delay = 0.15  # SEC rate limit: ~10 req/s
    total_filings = len(filings)

    for idx, filing in enumerate(filings):
        accn = filing["accession_number"]
        primary_doc = filing["primary_document"]
        if not primary_doc or not accn:
            continue

        # Progress to stderr every 25 filings
        if idx > 0 and idx % 25 == 0:
            print(f"Fetching Form 4 filings: {idx}/{total_filings}...", file=sys.stderr)

        acc_clean = accn.replace("-", "")

        # primaryDocument may include XSL stylesheet prefix like "xslF345X05/filename.xml".
        # The prefixed URL returns HTML (XSL-rendered), not raw XML.
        # Always try the stripped filename first to get raw XML.
        if "/" in primary_doc:
            stripped_doc = primary_doc.split("/")[-1]
        else:
            stripped_doc = primary_doc
        xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_stripped}/{acc_clean}/{stripped_doc}"
        r = None
        try:
            time.sleep(rate_limit_delay)
            r = session.get(xml_url, timeout=15)
            if r.status_code == 404 and stripped_doc != primary_doc:
                # Fallback: try with the original prefix path
                xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_stripped}/{acc_clean}/{primary_doc}"
                time.sleep(rate_limit_delay)
                r = session.get(xml_url, timeout=15)
            r.raise_for_status()
        except requests.HTTPError as e:
            warnings.append(f"Failed to fetch Form 4 XML for {accn}: {e}")
            continue
        except Exception as e:
            warnings.append(f"Request error for {accn}: {e}")
            continue

        transactions, _ = parse_form4_xml(r.text, filing["filing_date"], accn, cik)
        all_transactions.extend(transactions)

        if len(all_transactions) >= max_hits * 3:
            break

    # Filter open-market only if requested
    if open_market_only:
        all_transactions = [t for t in all_transactions if t["transaction_code"] in OPEN_MARKET_CODES]

    # Sort by transaction date descending
    def sort_key(t):
        d = t.get("transaction_date") or t.get("filing_date") or ""
        return d

    all_transactions.sort(key=sort_key, reverse=True)
    return all_transactions[:max_hits], warnings


def build_insider_summary(transactions):
    """Aggregate transactions by insider name, separating open-market from compensation."""
    summary = {}
    for tx in transactions:
        name = tx["insider_name"] or "Unknown"
        code = tx["transaction_code"]
        shares = tx["shares"] or 0
        value = tx["value_usd"] or 0

        if name not in summary:
            summary[name] = {
                "title": tx["insider_title"],
                "is_director": tx["is_director"],
                "is_officer": tx["is_officer"],
                "is_10pct_owner": tx["is_10pct_owner"],
                # Open-market activity (P/S) — conviction signal
                "open_market_buys_shares": 0.0,
                "open_market_buys_value_usd": 0.0,
                "open_market_sells_shares": 0.0,
                "open_market_sells_value_usd": 0.0,
                # Compensation activity (A/M/X/F/G/etc) — mechanistic, not conviction
                "compensation_acquisitions_shares": 0.0,
                "compensation_dispositions_shares": 0.0,
                "transaction_count": 0,
                "latest_transaction_date": "",
            }

        s = summary[name]
        s["transaction_count"] += 1
        if s["latest_transaction_date"] < (tx.get("transaction_date") or ""):
            s["latest_transaction_date"] = tx.get("transaction_date") or ""

        if code == "P":
            s["open_market_buys_shares"] += shares
            s["open_market_buys_value_usd"] += value
        elif code == "S":
            s["open_market_sells_shares"] += shares
            s["open_market_sells_value_usd"] += value
        elif code in BUY_CODES:
            s["compensation_acquisitions_shares"] += shares
        elif code in SELL_CODES:
            s["compensation_dispositions_shares"] += shares

    for s in summary.values():
        for k in ("open_market_buys_shares", "open_market_sells_shares",
                   "compensation_acquisitions_shares", "compensation_dispositions_shares"):
            s[k] = round(s[k], 1)
        for k in ("open_market_buys_value_usd", "open_market_sells_value_usd"):
            s[k] = round(s[k], 2)

    return summary


def detect_cluster_signals(transactions):
    """
    Flag clusters of unusual insider activity within a rolling window.
    Returns list of signal dicts.
    """
    signals = []

    # Filter to open-market buys and sales only for cluster analysis
    om_txs = [t for t in transactions if t["transaction_code"] in OPEN_MARKET_CODES]
    if not om_txs:
        return signals

    # Group sells and buys separately by date
    for tx_type, code_set, label in [
        ("sell", SELL_CODES & OPEN_MARKET_CODES, "coordinated open-market selling"),
        ("buy", BUY_CODES & OPEN_MARKET_CODES, "coordinated open-market buying"),
    ]:
        relevant = [t for t in om_txs if t["transaction_code"] in code_set]
        if len(relevant) < CLUSTER_MIN_INSIDERS:
            continue

        # Sliding window: scan for all non-overlapping clusters
        relevant.sort(key=lambda t: t.get("transaction_date") or "")
        skip_until = None  # advance past reported clusters to avoid duplicates
        for i, anchor in enumerate(relevant):
            anchor_date_str = anchor.get("transaction_date") or ""
            if not anchor_date_str:
                continue
            try:
                anchor_date = datetime.strptime(anchor_date_str, "%Y-%m-%d").date()
            except ValueError:
                continue

            if skip_until and anchor_date <= skip_until:
                continue

            window_end = anchor_date + timedelta(days=CLUSTER_WINDOW_DAYS)
            window_txs = []
            for tx in relevant[i:]:
                tx_date_str = tx.get("transaction_date") or ""
                try:
                    tx_date = datetime.strptime(tx_date_str, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if tx_date > window_end:
                    break
                window_txs.append(tx)

            unique_insiders = set(t["insider_name"] for t in window_txs)
            if len(unique_insiders) >= CLUSTER_MIN_INSIDERS:
                total_value = sum(t.get("value_usd") or 0 for t in window_txs)
                total_shares = sum(t.get("shares") or 0 for t in window_txs)
                signals.append({
                    "signal_type": label,
                    "window_start": anchor_date_str,
                    "window_end": window_end.strftime("%Y-%m-%d"),
                    "insider_count": len(unique_insiders),
                    "insiders": sorted(unique_insiders),
                    "transaction_count": len(window_txs),
                    "total_shares": round(total_shares, 1),
                    "total_value_usd": round(total_value, 2),
                    "interpretation": (
                        f"{len(unique_insiders)} insiders {tx_type}ing within {CLUSTER_WINDOW_DAYS}-day window — "
                        "monitor for information asymmetry or sentiment shift."
                    ),
                })
                # Skip past this cluster window to find next non-overlapping cluster
                skip_until = window_end

    # Flag large single transactions (>$500K open-market)
    for tx in om_txs:
        val = tx.get("value_usd") or 0
        if val >= 500_000:
            signals.append({
                "signal_type": "large_open_market_transaction",
                "insider": tx["insider_name"],
                "title": tx["insider_title"],
                "transaction_date": tx["transaction_date"],
                "transaction_type": tx["transaction_type"],
                "shares": tx["shares"],
                "value_usd": val,
                "interpretation": (
                    f"Large open-market {'purchase' if tx['transaction_code'] == 'P' else 'sale'} "
                    f"≥$500K — directionally significant."
                ),
            })

    return signals


# ─── HKEx ─────────────────────────────────────────────────────────────────────

HKEX_BASE = "https://www1.hkexnews.hk"
HKEX_STOCK_JSON = "/ncms/script/eds/activestock_sehk_e.json"
HKEX_SEARCH_API = "/search/titleSearchServlet.do"


def _hkex_build_session():
    s = requests.Session()
    s.trust_env = False
    s.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, */*",
        "Referer": urljoin(HKEX_BASE, "/search/titlesearch.xhtml"),
    })
    return s


def _hkex_resolve_stock_id(session, ticker):
    """Resolve ticker to HKEx internal stock ID from the active stock list."""
    r = session.get(urljoin(HKEX_BASE, HKEX_STOCK_JSON), timeout=15)
    r.raise_for_status()
    stocks = r.json()
    ticker_stripped = ticker.upper().lstrip("0")
    padded = ticker_stripped.zfill(5)
    for s in stocks:
        code = s.get("c", "")
        if code == padded or code.lstrip("0") == ticker_stripped:
            return s["i"], s.get("n", ""), s.get("c", "")
    return None, None, None


def _hkex_search(session, stock_id, keyword, from_date, to_date, row_range=50):
    """Search HKExNews using the same param format as hkex_fetcher.py."""
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
        "title":         keyword,
        "fromDate":      from_date,
        "toDate":        to_date,
        "sortDir":       "0",
        "sortByOptions": "DateTime",
        "rowRange":      str(row_range),
    }
    r = session.get(urljoin(HKEX_BASE, HKEX_SEARCH_API), params=params, timeout=25)
    r.raise_for_status()
    data = r.json()
    raw = data.get("result", "[]")
    items = json.loads(raw) if isinstance(raw, str) else raw
    return items if isinstance(items, list) else []


def fetch_hkex_disclosures(ticker, date_from, date_to, max_hits=DEFAULT_HITS):
    """
    Fetch HKEx disclosure-related filings via HKExNews title search.
    Uses the same API format as hkex_fetcher.py (verified working).
    Returns disclosure filings and a limitation warning about the DI portal.
    """
    warnings = []
    results = []

    hkex_session = _hkex_build_session()

    # Resolve internal stock ID
    try:
        stock_id, stock_name, stock_code = _hkex_resolve_stock_id(hkex_session, ticker)
    except Exception as e:
        warnings.append(f"Failed to load HKEx stock list: {e}")
        return results, warnings

    if stock_id is None:
        warnings.append(f"Ticker '{ticker}' not found in HKEx active stock list.")
        return results, warnings

    # Convert dates to HKEx format (YYYYMMDD)
    try:
        from_hkex = datetime.strptime(date_from, "%Y-%m-%d").strftime("%Y%m%d")
        to_hkex = datetime.strptime(date_to, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        warnings.append("Invalid date format for HKEx search.")
        return results, warnings

    for keyword in ["Next Day Disclosure", "connected person", "director dealing"]:
        try:
            items = _hkex_search(hkex_session, stock_id, keyword, from_hkex, to_hkex)
            for item in items:
                file_link = item.get("FILE_LINK", "")
                results.append({
                    "date": item.get("DATE_TIME", "")[:10],
                    "title": item.get("TITLE", ""),
                    "url": urljoin(HKEX_BASE, file_link) if file_link else "",
                    "keyword_matched": keyword,
                })
        except Exception as e:
            warnings.append(f"HKExNews search failed for keyword '{keyword}': {e}")

    # Deduplicate by title+date
    seen = set()
    unique = []
    for item in results:
        key = (item["date"], item["title"][:40])
        if key not in seen:
            seen.add(key)
            unique.append(item)

    unique.sort(key=lambda x: x.get("date", ""), reverse=True)

    # Always add DI portal limitation note
    warnings.append(
        "Full per-insider beneficial interest data requires the HKEx Disclosure of Interests portal "
        "(di.hkex.com.hk), which may not be accessible from all environments. "
        "For director-level shareholding changes, use Tavily L1 with site:di.hkex.com.hk "
        "or search HKEx SEHK for 'disclosure of interests' + company name."
    )

    return unique[:max_hits], warnings


# ─── Main ─────────────────────────────────────────────────────────────────────

def date_range_str(years):
    end = datetime.utcnow()
    start = end - timedelta(days=365 * years)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def main():
    parser = argparse.ArgumentParser(
        description="Insider transaction tracker: SEC Form 4 + HKEx disclosures"
    )
    parser.add_argument("--ticker", required=True, help="Company ticker (US: TSLA, HK: 00700)")
    parser.add_argument("--exchange", default="sec", choices=["sec", "hkex"],
                        help="Exchange: sec (default) or hkex")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS,
                        help=f"Look-back window in years (default: {DEFAULT_YEARS}, max: 5)")
    parser.add_argument("--open-market-only", action="store_true",
                        help="SEC only: show only open-market buys (P) and sales (S)")
    parser.add_argument("--hits", type=int, default=DEFAULT_HITS,
                        help=f"Max transactions to return (default: {DEFAULT_HITS})")
    args = parser.parse_args()

    years = min(args.years, 5)
    date_from, date_to = date_range_str(years)

    session = build_session()
    warnings = []

    if args.exchange == "sec":
        # Resolve CIK
        cik = resolve_cik(session, args.ticker)
        if not cik:
            output = build_error_output(args, None, date_from, date_to,
                                        [f"CIK not found for ticker '{args.ticker}'. "
                                         "Verify ticker is a US-listed company."])
            print(json.dumps(output, indent=2, ensure_ascii=False))
            sys.exit(1)

        # Fetch transactions
        try:
            transactions, fetch_warnings = fetch_sec_transactions(
                session, cik, date_from, date_to,
                open_market_only=args.open_market_only,
                max_hits=args.hits,
            )
        except Exception as e:
            output = build_error_output(args, cik, date_from, date_to,
                                        [f"EDGAR fetch failed: {e}"])
            print(json.dumps(output, indent=2, ensure_ascii=False))
            sys.exit(1)

        warnings.extend(fetch_warnings)
        insider_summary = build_insider_summary(transactions)
        cluster_signals = detect_cluster_signals(transactions)

        output = {
            "source": "SEC EDGAR Form 4",
            "ticker": args.ticker.upper(),
            "cik": cik,
            "exchange": "sec",
            "date_range": {"from": date_from, "to": date_to},
            "open_market_only": args.open_market_only,
            "transactions": transactions,
            "insider_summary": insider_summary,
            "cluster_signals": cluster_signals,
            "result_count": len(transactions),
            "warnings": warnings,
            "retrieved_at": datetime.utcnow().isoformat() + "Z",
            "governance_note": (
                "Form 4 data has a 2-business-day reporting lag. "
                "Use as directional signal only. Verify large or unusual transactions "
                "against primary filings before citing in analysis (CLAUDE.md §11.8)."
            ),
        }

    else:  # hkex
        disclosures, fetch_warnings = fetch_hkex_disclosures(
            args.ticker, date_from, date_to, max_hits=args.hits
        )
        warnings.extend(fetch_warnings)

        output = {
            "source": "HKExNews filing search",
            "ticker": args.ticker,
            "cik": None,
            "exchange": "hkex",
            "date_range": {"from": date_from, "to": date_to},
            "disclosures": disclosures,
            "insider_summary": {},
            "cluster_signals": [],
            "result_count": len(disclosures),
            "warnings": warnings,
            "retrieved_at": datetime.utcnow().isoformat() + "Z",
            "governance_note": (
                "HKEx mode returns filing listings only — not parsed transaction-level data. "
                "Full beneficial interest data requires the HKEx DI portal. "
                "Use as retrieval leads; verify before use in analysis (CLAUDE.md §11.8)."
            ),
        }

    print(json.dumps(output, indent=2, ensure_ascii=False))


def build_error_output(args, cik, date_from, date_to, errors):
    return {
        "source": f"{'SEC EDGAR Form 4' if args.exchange == 'sec' else 'HKExNews'}",
        "ticker": args.ticker,
        "cik": cik,
        "exchange": args.exchange,
        "date_range": {"from": date_from, "to": date_to},
        "transactions": [],
        "insider_summary": {},
        "cluster_signals": [],
        "result_count": 0,
        "warnings": errors,
        "retrieved_at": datetime.utcnow().isoformat() + "Z",
        "governance_note": (
            "Insider data is a directional signal only. "
            "Cannot anchor PM Verdict without corroborating evidence (CLAUDE.md §11.8)."
        ),
    }


if __name__ == "__main__":
    main()
