#!/usr/bin/env python3
"""
Tavily Public Web Recon
Role: Retrieval layer for public-web sources — filings, IR pages, news, regulatory disclosures.
      NOT a truth engine. Results are retrieval candidates only, not verified facts.

TIERING RULES — always start at L1, escalate only when necessary:

  L1  Precision Search (DEFAULT)
      Use for: finding public filings, IR pages, regulatory announcements, mainstream news.
      Rules:  domain-filtered, exact_match=True by default, raw content off, max 5 results (hard cap 10).
              topic="news" defaults to time_range="month" (last 30 days).
              topic="finance" or "general": no time filter — include year in query instead.
      Never escalate to L2 without a confirmed target URL from L1 output.

  L2  Targeted Extract (only after L1 identifies a specific target URL)
      Use for: extracting content from a single confirmed page.
      Rules:  one URL at a time; do not bulk-extract all L1 results.
              Check content_length in result metadata before relying on output.

  L3  Crawl (site-level research, EXPLICIT TRIGGER ONLY)
      Preconditions — ALL must be true before invoking L3:
        1. Local files and sell-side reports do not contain the needed information.
        2. L1 has been run and a specific root site has been identified.
        3. L2 on a single page is insufficient (multiple linked pages needed).
        4. A clear site boundary (IR portal, regulator page) has been scoped.
      Hard limits: max_depth <= 2, max_breadth <= 10 (enforced at runtime).
      L3 is expensive — treat as a scarce quota resource (~1000 free calls/month total).

HARD CONSTRAINTS:
  - Tavily output must NEVER feed directly into valuation inputs.
  - All results are tagged with retrieval timestamp — treat content as potentially stale.
  - For real-time market data, use live_market_fetcher.py (yfinance). Tavily supplements context only.

Setup:
    Add to ~/.zshrc: export TAVILY_API_KEY=your_key
    Never write the key value into any project file.

Usage:
    python3 skills/tavily_search.py l1 "Kuaishou 2025 annual report" [--domains hkexnews.hk]
    python3 skills/tavily_search.py l2 "https://www.hkexnews.hk/..."
    python3 skills/tavily_search.py l3 "https://ir.kuaishou.com" [--max-depth 2] [--max-breadth 5]
    python3 skills/tavily_search.py l1 "query" --output json
"""

import sys
import os
import argparse
import json
from urllib.parse import urlparse
from datetime import datetime, timezone
from typing import List, Optional, Tuple

# Trusted domains — L1 defaults to these when no --domains specified
DEFAULT_TRUSTED_DOMAINS = [
    "hkexnews.hk",
    "sec.gov",
    "hkex.com.hk",
    "cninfo.com.cn",
    "sse.com.cn",
    "szse.cn",
    "bloomberg.com",
    "reuters.com",
    "ft.com",
    "wsj.com",
    "scmp.com",
    "caixin.com",
    "theinformation.com",
]

# High-noise domains — excluded from L1 by default
DEFAULT_EXCLUDED_DOMAINS = [
    "reddit.com",
    "quora.com",
    "zhihu.com",
    "weibo.com",
    "twitter.com",
    "x.com",
]

# Domain-based source classification (checked before path patterns to prevent misclassification)
_PRIMARY_FILING_DOMAINS = [
    "hkexnews.hk", "sec.gov", "cninfo.com.cn",
    "sse.com.cn", "szse.cn", "hkex.com.hk",
]
_REPUTABLE_MEDIA_DOMAINS = [
    "bloomberg.com", "reuters.com", "ft.com", "wsj.com",
    "scmp.com", "caixin.com", "theinformation.com",
    "nytimes.com", "economist.com",
]
# IR subdomain prefixes (e.g. ir.kuaishou.com, investor.apple.com)
_IR_SUBDOMAIN_PREFIXES = ["ir.", "investor.", "investors."]
# IR path patterns — only applied after domain checks to avoid false positives
_IR_PATH_PATTERNS = ["/ir/", "/investor-relations/", "/investors/"]

# Hard limits
L1_MAX_RESULTS_LIMIT = 10
L3_MAX_DEPTH_LIMIT = 2
L3_MAX_BREADTH_LIMIT = 10
L2_TEXT_CHAR_LIMIT = 50000  # chars printed in text mode; use --output json for full content

# L2 routing rule — exchange/regulator PDF sources where Tavily extract is structurally unreliable.
# Tavily cannot access these PDFs directly and silently returns unrelated documents instead of
# raising a failure. This is a permanent routing decision, not a temporary patch:
# for these domains, skip Tavily L2 entirely and use the dedicated fetcher + local pdftotext.
_L2_UNRELIABLE_DOMAINS = [
    "hkexnews.hk",   # HKEx listed-company news PDFs — gated, returns wrong docs silently
    "hkex.com.hk",   # HKEx main site PDFs — same issue
]
L2_TEXT_CHAR_LIMIT = 50000  # chars printed in text mode; use --output json for full content

# Domains where Tavily L2 is known to silently return wrong content (PDF gating, anti-scrape).
# For these, skip Tavily L2 and route to the dedicated fetcher + local pdftotext instead.
_L2_UNRELIABLE_DOMAINS = [
    "hkexnews.hk",
    "hkex.com.hk",
]

FRESHNESS_NOTE = (
    "Tavily retrieval — content date may not reflect current state. "
    "Do not use as direct valuation input. Verify figures against primary source."
)


def _classify_source(url: str) -> str:
    """
    Classify a URL into a source tier for downstream L2 escalation decisions.

    Priority order (domain-first to prevent path-pattern false positives):
      1. primary_filing  — known exchange/regulator domains
      2. reputable_media — known financial media domains
      3. company_ir      — IR subdomains (ir.xxx.com), then IR path patterns
      4. other
    """
    parsed = urlparse(url)
    netloc = parsed.netloc.lower().lstrip("www.")
    path = parsed.path.lower()

    # 1. Primary filing domains (exact or subdomain match)
    for d in _PRIMARY_FILING_DOMAINS:
        if netloc == d or netloc.endswith("." + d):
            return "primary_filing"

    # 2. Reputable media domains (exact or subdomain match)
    for d in _REPUTABLE_MEDIA_DOMAINS:
        if netloc == d or netloc.endswith("." + d):
            return "reputable_media"

    # 3a. IR subdomains — checked on netloc before path to avoid bloomberg.com/investors/ misfire
    for prefix in _IR_SUBDOMAIN_PREFIXES:
        if netloc.startswith(prefix):
            return "company_ir"

    # 3b. IR path patterns — last resort, only for unclassified domains
    for pattern in _IR_PATH_PATTERNS:
        if pattern in path:
            return "company_ir"

    return "other"


def _l1_quality_warning(results: list) -> Optional[str]:
    """Return a warning if all results are low-quality or no results were returned."""
    if len(results) == 0:
        return (
            "No results returned. "
            "Try broadening the query, disabling exact_match (--no-exact-match), or expanding domains."
        )
    if all(r.get("source_class") == "other" for r in results):
        return (
            "All results classified as 'other' — no primary_filing, company_ir, or reputable_media hits. "
            "Do not escalate to L2 blindly. Refine query or expand trusted domains before extracting."
        )
    return None


def _retrieved_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_client():
    """Return a TavilyClient or raise a clear, actionable error."""
    try:
        from tavily import TavilyClient
    except ImportError:
        raise RuntimeError("tavily-python not installed. Run: pip install tavily-python")
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError(
            "TAVILY_API_KEY not set. Add to ~/.zshrc: export TAVILY_API_KEY=your_key\n"
            "Never write the key value into any project file."
        )
    from tavily import TavilyClient
    return TavilyClient(api_key=api_key)


def l1_search(
    query: str,
    domains: Optional[List[str]] = None,
    max_results: int = 5,
    topic: str = "general",
    exact_match: bool = True,
    time_range: Optional[str] = None,
) -> dict:
    """
    L1 Precision Search — find public sources matching a query.

    Args:
        query:       Search query. Include company name, filing type, and year for best results.
        domains:     Allowlist of domains. Defaults to DEFAULT_TRUSTED_DOMAINS.
        max_results: Cap on results (default 5; hard cap L1_MAX_RESULTS_LIMIT=10).
        topic:       'general', 'news', or 'finance'.
        exact_match: Require exact phrase matching (default True — reduces noise significantly).
        time_range:  'day', 'week', 'month', 'year', or None.
                     topic='news' defaults to 'month' when time_range is not set.
    """
    if max_results > L1_MAX_RESULTS_LIMIT:
        raise ValueError(
            f"L1 max_results={max_results} exceeds hard limit of {L1_MAX_RESULTS_LIMIT}. "
            "Keep results capped to limit noise."
        )

    client = _get_client()
    include_domains = domains if domains else DEFAULT_TRUSTED_DOMAINS

    resolved_time_range = time_range
    if topic == "news" and time_range is None:
        resolved_time_range = "month"

    kwargs = dict(
        query=query,
        search_depth="basic",
        topic=topic,
        max_results=max_results,
        include_domains=include_domains,
        exclude_domains=DEFAULT_EXCLUDED_DOMAINS,
        include_raw_content=False,
        include_answer=False,
        exact_match=exact_match,
    )
    if resolved_time_range:
        kwargs["time_range"] = resolved_time_range

    response = client.search(**kwargs)

    results = []
    for r in response.get("results", []):
        url = r.get("url", "")
        results.append({
            "title": r.get("title", ""),
            "url": url,
            "snippet": r.get("content", ""),
            "score": r.get("score"),
            "published_date": r.get("published_date"),
            "source_class": _classify_source(url),
        })

    quality_warning = _l1_quality_warning(results)

    metadata = {
        "domains_searched": include_domains,
        "exact_match": exact_match,
        "time_range_applied": resolved_time_range or "none",
        "topic": topic,
        "max_results": max_results,
        "retrieved_at": _retrieved_at(),
        "freshness_note": FRESHNESS_NOTE,
        "next_step": (
            "Review source_class on each result. "
            "Escalate to L2 only after identifying a specific target URL — "
            "prefer primary_filing > company_ir > reputable_media > other."
        ),
    }
    if quality_warning:
        metadata["warning"] = quality_warning

    return {
        "tier": "L1",
        "query": query,
        "results": results,
        "result_count": len(results),
        "metadata": metadata,
    }


def l2_extract(url: str) -> dict:
    """
    L2 Targeted Extract — extract content from a single confirmed URL.

    Only call after L1 has identified a specific, relevant URL.
    Do not bulk-extract all L1 results — one URL per L2 call.

    Domains in _L2_UNRELIABLE_DOMAINS are blocked before the API call: Tavily cannot
    reliably access these PDFs and silently returns unrelated documents. Use the
    dedicated fetcher (e.g. hkex_fetcher.py) + local pdftotext for those sources.
    """
    # Routing rule: block unreliable domains before hitting the API
    parsed = urlparse(url)
    netloc = parsed.netloc.lower().lstrip("www.")
    for blocked in _L2_UNRELIABLE_DOMAINS:
        if netloc == blocked or netloc.endswith("." + blocked):
            block_error = {
                "url": url,
                "error": (
                    f"L2 blocked — '{blocked}' is a known unreliable Tavily L2 source "
                    "(exchange PDF gating causes silent wrong-document returns). "
                    "Use hkex_fetcher.py to locate the filing, then extract locally with pdftotext."
                ),
            }
            return {
                "tier": "L2",
                "url": url,
                "extracted": [],
                "failed_urls": [block_error],  # unified failure surface — downstream sees this
                "metadata": {
                    "retrieved_at": _retrieved_at(),
                    "freshness_note": FRESHNESS_NOTE,
                    "source_class": _classify_source(url),
                    "routing_block": block_error["error"],
                },
            }

    client = _get_client()
    response = client.extract(urls=[url])

    extracted = []
    # Collect invalid extracts separately so they can be surfaced in failed_urls too
    invalid_extracts = []
    for r in response.get("results", []):
        returned_url = r.get("url", url)
        content = r.get("raw_content", "")
        # Detect silent URL substitution: Tavily may return a different page when it cannot
        # access the requested URL, without marking it as a failure.
        url_mismatch = (
            returned_url.rstrip("/").lower() != url.rstrip("/").lower()
            and returned_url != ""
        )
        entry = {
            "url": returned_url,
            "raw_content": content if not url_mismatch else "",
            "content_length": len(content),
            "truncated_in_text_mode": len(content) > L2_TEXT_CHAR_LIMIT,
            "url_mismatch": url_mismatch,
            "status": "invalid_extract" if url_mismatch else "ok",
        }
        extracted.append(entry)
        if url_mismatch:
            invalid_extracts.append({
                "url": returned_url,
                "error": (
                    f"URL mismatch: requested '{url}' but Tavily returned '{returned_url}'. "
                    "Content suppressed — treat as failed extraction."
                ),
            })

    # Merge API-reported failures with URL-mismatch invalids into one list
    # so downstream code checking failed_urls catches both failure modes
    failed = response.get("failed_results", []) + invalid_extracts

    return {
        "tier": "L2",
        "url": url,
        "extracted": extracted,
        "failed_urls": failed,
        "metadata": {
            "retrieved_at": _retrieved_at(),
            "freshness_note": FRESHNESS_NOTE,
            "source_class": _classify_source(url),
            "warning": (
                "Verify extracted figures against primary source before use in analysis. "
                "Check content_length and truncated_in_text_mode on each result."
            ),
        }
    }


def l3_crawl(
    url: str,
    max_depth: int = 1,
    max_breadth: int = 5,
) -> dict:
    """
    L3 Crawl — collect multiple linked pages within a bounded site.

    ONLY invoke when all L3 preconditions are met (see module docstring).
    Hard limits enforced at runtime: max_depth <= 2, max_breadth <= 10.
    """
    if max_depth > L3_MAX_DEPTH_LIMIT:
        raise ValueError(
            f"L3 max_depth={max_depth} exceeds hard limit of {L3_MAX_DEPTH_LIMIT}."
        )
    if max_breadth > L3_MAX_BREADTH_LIMIT:
        raise ValueError(
            f"L3 max_breadth={max_breadth} exceeds hard limit of {L3_MAX_BREADTH_LIMIT}."
        )

    client = _get_client()

    response = client.crawl(
        url=url,
        max_depth=max_depth,
        max_breadth=max_breadth,
        extract_depth="basic",
    )

    pages = []
    for r in response.get("results", []):
        content = r.get("raw_content", "")
        pages.append({
            "url": r.get("url", ""),
            "raw_content": content,
            "content_length": len(content),
            "source_class": _classify_source(r.get("url", "")),
        })

    return {
        "tier": "L3",
        "root_url": url,
        "pages": pages,
        "page_count": len(pages),
        "metadata": {
            "max_depth": max_depth,
            "max_breadth": max_breadth,
            "retrieved_at": _retrieved_at(),
            "freshness_note": FRESHNESS_NOTE,
            "cost_warning": (
                "L3 crawl consumes significant API quota. "
                "Use only for bounded site-level collection with explicit trigger justification."
            ),
        }
    }


def main():
    parser = argparse.ArgumentParser(
        description="Tavily Public Web Recon — tiered retrieval for financial research.",
        epilog=(
            "Rule: Tavily is a retrieval tool, not a truth engine. "
            "Default L1. Escalate to L2 only after target URL confirmed. "
            "L3 requires all 4 preconditions (see module docstring)."
        )
    )
    subparsers = parser.add_subparsers(dest="tier", required=True)

    # L1
    p_l1 = subparsers.add_parser("l1", help="Precision Search (default tier)")
    p_l1.add_argument("query")
    p_l1.add_argument("--domains", nargs="+", metavar="DOMAIN",
                      help="Allowlist domains (default: trusted financial domains)")
    p_l1.add_argument("--max-results", type=int, default=5,
                      help=f"Max results (default 5; hard cap {L1_MAX_RESULTS_LIMIT})")
    p_l1.add_argument("--topic", default="general", choices=["general", "news", "finance"])
    p_l1.add_argument("--no-exact-match", action="store_true",
                      help="Disable exact match (more results, more noise)")
    p_l1.add_argument("--time-range", choices=["day", "week", "month", "year"], default=None,
                      help="Recency filter. topic=news defaults to 'month' if not set.")
    p_l1.add_argument("--output", choices=["text", "json"], default="text")

    # L2
    p_l2 = subparsers.add_parser("l2", help="Targeted Extract (requires confirmed URL from L1)")
    p_l2.add_argument("url")
    p_l2.add_argument("--output", choices=["text", "json"], default="text")

    # L3
    p_l3 = subparsers.add_parser("l3", help="Crawl (site-level; explicit trigger only)")
    p_l3.add_argument("url")
    p_l3.add_argument("--max-depth", type=int, default=1,
                      help=f"Link depth (default 1; hard limit {L3_MAX_DEPTH_LIMIT})")
    p_l3.add_argument("--max-breadth", type=int, default=5,
                      help=f"Pages per level (default 5; hard limit {L3_MAX_BREADTH_LIMIT})")
    p_l3.add_argument("--output", choices=["text", "json"], default="text")

    args = parser.parse_args()

    try:
        if args.tier == "l1":
            result = l1_search(
                query=args.query,
                domains=args.domains,
                max_results=args.max_results,
                topic=args.topic,
                exact_match=not args.no_exact_match,
                time_range=args.time_range,
            )
        elif args.tier == "l2":
            result = l2_extract(url=args.url)
        elif args.tier == "l3":
            result = l3_crawl(
                url=args.url,
                max_depth=args.max_depth,
                max_breadth=args.max_breadth,
            )
    except (RuntimeError, ValueError) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    tier = result["tier"]
    meta = result.get("metadata", {})
    print(f"=== Tavily {tier} | {meta.get('retrieved_at', '')} ===")
    print(f"[FRESHNESS] {meta.get('freshness_note', '')}\n")

    if tier == "L1":
        if meta.get("warning"):
            print(f"[WARN] {meta['warning']}\n")
        print(f"Query: {result['query']}")
        print(f"Results: {result['result_count']} | exact_match: {meta.get('exact_match')} | time_range: {meta.get('time_range_applied')}\n")
        for i, r in enumerate(result["results"], 1):
            print(f"{i}. [{r['source_class']}] {r['title']}")
            print(f"   URL: {r['url']}")
            if r.get("published_date"):
                print(f"   Date: {r['published_date']}")
            print(f"   {r['snippet'][:280]}...")
            print()
        print(f"[NEXT] {meta.get('next_step', '')}")

    elif tier == "L2":
        print(f"URL: {result['url']} [{meta.get('source_class', '')}]")
        # Routing block (unreliable domain) — surface prominently before anything else
        if meta.get("routing_block"):
            print(f"\n[BLOCKED] {meta['routing_block']}\n", file=sys.stderr)
        if meta.get("warning"):
            print(f"[WARN] {meta['warning']}\n")
        for e in result["extracted"]:
            # URL mismatch — surface as hard warning; do not print substituted content
            if e.get("url_mismatch"):
                print(
                    f"[INVALID EXTRACT] Tavily returned '{e['url']}' instead of the requested URL. "
                    f"Content suppressed (content_length={e['content_length']} chars). "
                    "This is a silent substitution — treat as failed extraction.",
                    file=sys.stderr,
                )
                continue
            content = e.get("raw_content", "")
            # Always show content_length so reader knows how much exists vs. what is printed
            cl = e["content_length"]
            limit = L2_TEXT_CHAR_LIMIT
            if cl > limit:
                print(f"[content_length: {cl} chars | showing first {limit} chars — use --output json for full content]\n")
            else:
                print(f"[content_length: {cl} chars]\n")
            print(content[:limit])
        if result.get("failed_urls"):
            print(f"\n[WARN] Failed: {result['failed_urls']}", file=sys.stderr)

    elif tier == "L3":
        print(f"Root: {result['root_url']} | Pages: {result['page_count']}")
        print(f"[COST] {meta.get('cost_warning', '')}\n")
        for i, p in enumerate(result["pages"], 1):
            print(f"--- Page {i} [{p['source_class']}]: {p['url']} (content_length: {p['content_length']}) ---")
            content = p.get("raw_content", "")
            print(content[:800])
            if len(content) > 800:
                print("[... truncated. Use --output json for full content ...]")
            print()


if __name__ == "__main__":
    main()
