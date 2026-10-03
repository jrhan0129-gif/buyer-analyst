"""
data_utils.py — Unified data layer for all skills.

Provides:
  - yfinance session with retry + rate limiting
  - Ticker normalization (GOLD→ABX, HK ticker formatting)
  - Retry decorator for any callable
  - Standard HTTP headers for SEC/HKEx
  - Fallback chain for data fetching
"""

import time
import functools
import warnings
import sys

# Suppress LibreSSL warnings from urllib3
warnings.filterwarnings("ignore", message=".*LibreSSL.*")

# ── Retry decorator ──────────────────────────────────────────────────────────

def retry(max_attempts=3, backoff_base=2, exceptions=(Exception,), on_fail=None):
    """Decorator: retry with exponential backoff.

    Usage:
        @retry(max_attempts=3)
        def fetch_something():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        wait = backoff_base ** attempt
                        print(f"[RETRY] {func.__name__} attempt {attempt}/{max_attempts} "
                              f"failed: {e}. Retrying in {wait}s...", file=sys.stderr)
                        time.sleep(wait)
            if on_fail is not None:
                return on_fail
            raise last_exc
        return wrapper
    return decorator


# ── Ticker normalization ─────────────────────────────────────────────────────

# Known ambiguous tickers: map to correct yfinance symbol
TICKER_ALIASES = {
    "GOLD": "GOLD",       # Barrick Gold on NYSE is actually GOLD (was ABX pre-2019)
    "BRK": "BRK-B",
    "BRK.A": "BRK-A",
    "BRK.B": "BRK-B",
}

# HK ticker normalization: ensure 4-digit + .HK format
def normalize_ticker(ticker: str) -> str:
    """Normalize ticker symbol for yfinance compatibility.

    - HK tickers: '1109' → '1109.HK', '02318' → '2318.HK'
    - Aliases: 'BRK' → 'BRK-B'
    - US tickers: unchanged
    """
    t = ticker.strip().upper()

    # Check alias table
    if t in TICKER_ALIASES:
        return TICKER_ALIASES[t]

    # HK ticker: already has .HK suffix
    if t.endswith(".HK"):
        # Strip leading zeros: 02318.HK → 2318.HK
        code = t.replace(".HK", "").lstrip("0") or "0"
        return f"{code}.HK"

    # HK ticker: pure digits, likely HK stock
    if t.isdigit() and len(t) <= 5:
        code = t.lstrip("0") or "0"
        return f"{code}.HK"

    return t


def detect_exchange(ticker: str) -> str:
    """Detect exchange from ticker format. Returns 'hk', 'us', or 'unknown'."""
    t = normalize_ticker(ticker)
    if t.endswith(".HK"):
        return "hk"
    if t.endswith((".SS", ".SZ")):
        return "cn"
    return "us"


# ── yfinance session with retry ──────────────────────────────────────────────

_YF_CACHE = {}
_YF_CACHE_TTL = 300  # 5 minutes

def get_yf_info(ticker: str, use_cache=True) -> dict:
    """Fetch yfinance .info with caching and retry.

    Returns info dict or empty dict on failure.
    """
    t = normalize_ticker(ticker)
    now = time.time()

    if use_cache and t in _YF_CACHE:
        cached_time, cached_data = _YF_CACHE[t]
        if now - cached_time < _YF_CACHE_TTL:
            return cached_data

    try:
        info = _fetch_yf_info_with_retry(t)
        _YF_CACHE[t] = (now, info)
        return info
    except Exception as e:
        print(f"[DATA_UTILS] yfinance failed for {t}: {e}", file=sys.stderr)
        return {}


@retry(max_attempts=2, backoff_base=3, exceptions=(Exception,))
def _fetch_yf_info_with_retry(ticker: str) -> dict:
    import yfinance as yf
    return yf.Ticker(ticker).info


def get_yf_financials(ticker: str):
    """Fetch income_stmt, balance_sheet, cashflow with retry.

    Returns (income_stmt, balance_sheet, cashflow) DataFrames, or (None, None, None) on failure.
    """
    t = normalize_ticker(ticker)
    try:
        return _fetch_yf_financials_with_retry(t)
    except Exception as e:
        print(f"[DATA_UTILS] yfinance financials failed for {t}: {e}", file=sys.stderr)
        return None, None, None


@retry(max_attempts=2, backoff_base=3, exceptions=(Exception,))
def _fetch_yf_financials_with_retry(ticker: str):
    import yfinance as yf
    stock = yf.Ticker(ticker)
    return stock.income_stmt, stock.balance_sheet, stock.cashflow


# ── Standard HTTP config ─────────────────────────────────────────────────────

SEC_USER_AGENT = "BuyerAnalyst/1.0 (research@buyeranalyst.com)"
SEC_HEADERS = {
    "User-Agent": SEC_USER_AGENT,
    "Accept-Encoding": "identity",
}

HKEX_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) BuyerAnalyst/1.0",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}

TAVILY_TIMEOUT = 30  # seconds


# ── Formatting helpers ───────────────────────────────────────────────────────

def fmt_billions(val, decimals=2):
    """Format number as XB/XM string."""
    if val is None:
        return "N/A"
    if abs(val) >= 1e12:
        return f"{val/1e12:.{decimals}f}T"
    if abs(val) >= 1e9:
        return f"{val/1e9:.{decimals}f}B"
    if abs(val) >= 1e6:
        return f"{val/1e6:.{decimals}f}M"
    return f"{val:,.0f}"


def fmt_pct(val, decimals=1):
    """Format as percentage string."""
    if val is None:
        return "N/A"
    return f"{val*100:.{decimals}f}%" if abs(val) < 10 else f"{val:.{decimals}f}%"


def safe_div(a, b, default=None):
    """Safe division, returns default if b is 0/None."""
    if b is None or b == 0 or a is None:
        return default
    return a / b
