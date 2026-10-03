"""
Buyer Analyst Skills — Modular investment research toolkit.

Each skill is a standalone CLI tool:
    python3 skills/<script>.py --help

Skills are grouped by function. All DATA_FETCHERS output JSON and
require no configuration beyond pip dependencies (except where noted).

Usage by external systems:
    import subprocess, json
    result = subprocess.run(
        ["python3", "skills/live_market_fetcher.py", "NVDA"],
        capture_output=True, text=True
    )
    # Or for JSON-outputting skills:
    data = json.loads(subprocess.run(
        ["python3", "skills/health_checker.py", "NVDA", "--json"],
        capture_output=True, text=True
    ).stdout)
"""

# ── Data Fetchers ─────────────────────────────────────────────────────────────
# Input: ticker / query / URL → Output: JSON stdout
# Dependencies: yfinance, requests (pip install yfinance requests)

DATA_FETCHERS = [
    "live_market_fetcher",           # Real-time price, mcap, PE, momentum
    "edgar_fetcher",                 # SEC EDGAR filing index (US)
    "hkex_fetcher",                  # HKEx filing index (HK)
    "filing_extractor",              # One-command filing download + text extraction + key financial search
    "edgar_fulltext_search",         # Cross-filing keyword search (US)
    "earnings_transcript_fetcher",   # Earnings call transcripts  [requires FMP_API_KEY]
    "insider_tracker",               # SEC Form 4 + HKEx insider disclosures
    "short_interest",                # Short interest / institutional ownership
    "fred_fetcher",                  # US macro data from FRED  [requires FRED_API_KEY]
    "cme_fetcher",                   # CME futures / commodity prices
    "akshare_fetcher",               # China macro (NBS/PBoC), HK Connect flow  [requires akshare]
    "tavily_search",                 # Web search L1/L2/L3  [requires TAVILY_API_KEY]
    "court_fetcher",                 # CourtListener + DOJ press releases
    "github_repo_scraper",           # Open-source repo vitality metrics
]

# ── Analysis Tools ────────────────────────────────────────────────────────────
# Input: ticker or data → Output: structured risk/valuation signals
# Dependencies: yfinance

ANALYSIS = [
    "sector_classifier",             # Ticker → sector/industry + §8.x addenda routing
    "health_checker",                # Financial risk triage (flags: HIGH/WARNING/OK)
    "forensic_accounting",           # Beneish M-Score, accruals quality, working capital, capex/D&A
    "valuation_matrix",              # DCF / P·S / EV·GP / SOTP with exit multiple fallback
    "peer_comps",                    # Peer comparison table (auto-discovery by industry + mcap)
]

# ── Orchestration ─────────────────────────────────────────────────────────────
# Higher-level tools that compose fetchers + analysis
# These are specific to the Buyer Analyst workflow

ORCHESTRATION = [
    "pipeline_runner",               # One-command /分析: runs Steps 1-8, outputs data_recon_packet.json
    "alpha_screener",                # Event → 2nd/3rd-order stock candidates (preset + free-text)
    "watchlist_manager",             # Add/remove/list/update tracked positions
    "performance_tracker",           # Stance accuracy vs actual price (weekly scorecard)
]

# ── Infrastructure ────────────────────────────────────────────────────────────

INFRA = [
    "data_utils",                    # Retry decorator, ticker normalization, yfinance cache, HTTP headers
]

# ── External API Keys ─────────────────────────────────────────────────────────
# Most skills need NO API key. These are the exceptions:

API_KEYS_REQUIRED = {
    "tavily_search":                 "TAVILY_API_KEY",
    "earnings_transcript_fetcher":   "FMP_API_KEY",
    "fred_fetcher":                  "FRED_API_KEY",
}

# All other skills use free/unauthenticated APIs (yfinance, SEC EDGAR, HKEx, CourtListener).
