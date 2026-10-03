#!/usr/bin/env python3
"""
akshare_fetcher.py — AKShare wrapper for Chinese market data
Part of the Buyer Analyst evidence pipeline (§11.6 Optional External Data Skills).

Role: On-demand retrieval of Chinese-specific data not available via hkex_fetcher,
      edgar_fetcher, or Tavily. Outputs are leads requiring verification — never direct
      valuation inputs.

Trigger conditions (§11.6) — activate only when analysis requires:
  1. Chinese macro indicators (CPI, PPI, PMI, M2/money supply)
  2. A-share / HK Connect market structure data (northbound/southbound flow)
  3. Regulatory risk signals for A-share listed companies (ST status)
     IMPORTANT: akshare has NO direct CSRC administrative enforcement database API.
     ST check is a risk-warning proxy only, not an enforcement record.
     For actual CSRC enforcement orders (行政处罚决定书), use:
       python3 skills/tavily_search.py l1 'COMPANY 证监会 行政处罚' --domains csrc.gov.cn

Do NOT activate by default for all Chinese companies — most analyses are served by
hkex_fetcher + edgar_fetcher + Tavily. Trigger only when one of the above data types
is specifically needed and primary pipeline tools are insufficient.

Modes:
    macro       -- Chinese macro indicators (PMI, PPI, CPI, M2)
    market      -- A-share / HK Connect market structure (northbound flow)
    regulatory  -- A-share regulatory risk signals (ST status proxy only)

Usage:
    python3 skills/akshare_fetcher.py --mode macro --indicators pmi ppi m2 [--periods N]
    python3 skills/akshare_fetcher.py --mode macro --indicators cpi [--periods N]
    python3 skills/akshare_fetcher.py --mode market --indicator northbound
    python3 skills/akshare_fetcher.py --mode regulatory --ticker 000001
    python3 skills/akshare_fetcher.py --list

Output: JSON. All results annotated with retrieval timestamp and source classification.
        Treat as secondary data requiring primary-source verification before PM Verdict.
"""

import argparse
import json
import sys
from datetime import datetime

try:
    import akshare as ak
except ImportError:
    print(json.dumps({
        "error": "akshare not installed",
        "fix": "pip install akshare",
        "source": "akshare_fetcher"
    }))
    sys.exit(1)

SCRIPT_VERSION = "1.0"

# ── Available indicators catalogue ────────────────────────────────────────────

MACRO_INDICATORS = {
    "pmi":    "NBS Manufacturing + Non-manufacturing PMI (官方制造业/非制造业PMI) — ak.macro_china_pmi()",
    "pmi-cx": "Caixin Manufacturing PMI (财新制造业PMI) — ak.index_pmi_man_cx()",
    "ppi":    "PPI monthly — current month and YoY (工业生产者出厂价格指数) — ak.macro_china_ppi()",
    "cpi":    "CPI monthly (消费者价格指数) — ak.macro_china_cpi_monthly() — jin10 upstream, may be unstable",
    "m2":     "Money supply M0/M1/M2 (货币供应量) — ak.macro_china_money_supply()",
}

MARKET_INDICATORS = {
    "northbound": (
        "HK Connect daily fund flow summary — 4 channels: 沪股通/深股通/港股通(沪)/港股通(深). "
        "Current session summary only, not a historical time series. "
        "ak.stock_hsgt_fund_flow_summary_em()"
    ),
}

REGULATORY_CAPABILITIES = {
    "st-check": (
        "ST/risk-warning status for an A-share ticker. "
        "Proxy for regulatory action or financial distress — NOT a direct CSRC enforcement record. "
        "ak.stock_zh_a_st_em()"
    ),
    "csrc-enforcement": (
        "NOT AVAILABLE via akshare. "
        "Use: python3 skills/tavily_search.py l1 'COMPANY 证监会 行政处罚' --domains csrc.gov.cn"
    ),
}

# ── Helpers ───────────────────────────────────────────────────────────────────

# Common date/period column names in AKShare DataFrames, checked in order
_DATE_COLS = ["日期", "月份", "交易日", "年份", "date", "Date", "时间"]


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_df_to_records(df, n=12):
    """
    Return the most recent n rows as a list of dicts.
    Sorts descending by the first recognised date/period column before slicing,
    since many AKShare macro tables are ordered oldest-first.
    Falls back to the first column if no standard date column is found.
    """
    if df is None or df.empty:
        return []
    try:
        sort_col = next((c for c in _DATE_COLS if c in df.columns), df.columns[0])
        sorted_df = df.sort_values(sort_col, ascending=False)
        subset = sorted_df.head(n).copy()
        for col in subset.columns:
            subset[col] = subset[col].astype(str)
        return subset.to_dict("records")
    except Exception:
        try:
            return df.head(n).astype(str).to_dict("records")
        except Exception:
            return []


def _result(mode, indicator, data, warnings=None, notes=None):
    return {
        "source":       "akshare_secondary",
        "retrieved_at": _now(),
        "mode":         mode,
        "indicator":    indicator,
        "governance":   (
            "AKShare data is secondary — treat as retrieval lead, not primary evidence. "
            "Verify material figures against primary sources (NBS, PBoC, CSRC official releases) "
            "before use in analysis. Do not use as direct valuation input."
        ),
        "data":     data,
        "warnings": warnings or [],
        "notes":    notes or [],
    }


def _error(indicator, message):
    return {
        "source":       "akshare_secondary",
        "retrieved_at": _now(),
        "indicator":    indicator,
        "error":        message,
        "warnings":     [],
    }


# ── Macro ─────────────────────────────────────────────────────────────────────

def fetch_pmi_official(periods=12):
    """NBS official PMI — manufacturing + non-manufacturing. Confirmed: ak.macro_china_pmi()."""
    try:
        df = ak.macro_china_pmi()
        # Columns: 月份, 制造业-指数, 制造业-同比增长, 非制造业-指数, 非制造业-同比增长
        records = _safe_df_to_records(df, periods)
        latest = records[0] if records else {}
        return _result("macro", "pmi", {
            "series":      "NBS PMI (National Bureau of Statistics)",
            "description": "Manufacturing and Non-manufacturing PMI, monthly",
            "columns":     list(df.columns),
            "latest":      latest,
            "periods":     len(records),
            "records":     records,
        })
    except Exception as e:
        return _error("pmi", str(e))


def fetch_pmi_caixin(periods=12):
    """Caixin manufacturing PMI. Confirmed: ak.index_pmi_man_cx()."""
    try:
        df = ak.index_pmi_man_cx()
        # Columns: 日期, 制造业PMI, 变化值
        records = _safe_df_to_records(df, periods)
        latest = records[0] if records else {}
        return _result("macro", "pmi-cx", {
            "series":      "Caixin Manufacturing PMI",
            "description": "Caixin/S&P Global manufacturing PMI, monthly",
            "columns":     list(df.columns),
            "latest":      latest,
            "periods":     len(records),
            "records":     records,
        })
    except Exception as e:
        return _error("pmi-cx", str(e))


def fetch_ppi(periods=12):
    """PPI monthly. Confirmed: ak.macro_china_ppi()."""
    try:
        df = ak.macro_china_ppi()
        # Columns: 月份, 当月, 当月同比增长, 累计
        records = _safe_df_to_records(df, periods)
        latest = records[0] if records else {}
        return _result("macro", "ppi", {
            "series":      "China PPI (NBS)",
            "description": "Producer Price Index — current month YoY and cumulative",
            "columns":     list(df.columns),
            "latest":      latest,
            "periods":     len(records),
            "records":     records,
        })
    except Exception as e:
        return _error("ppi", str(e))


def fetch_cpi(periods=12):
    """CPI monthly. Source: ak.macro_china_cpi_monthly() via jin10 — may exhibit SSL instability."""
    try:
        df = ak.macro_china_cpi_monthly()
        records = _safe_df_to_records(df, periods)
        latest = records[0] if records else {}
        return _result("macro", "cpi", {
            "series":      "China CPI monthly",
            "description": "Consumer Price Index monthly rate",
            "columns":     list(df.columns),
            "latest":      latest,
            "periods":     len(records),
            "records":     records,
        }, warnings=[
            "Upstream source is jin10.com (datacenter-api.jin10.com). "
            "SSL errors are intermittent — if this call fails, retry once or use NBS website directly. "
            "Do not treat a single failed attempt as data unavailability."
        ])
    except Exception as e:
        return _error("cpi",
                      f"{e} — jin10 upstream instability is a known issue. "
                      "Retry or fetch from NBS website (stats.gov.cn) directly.")


def fetch_m2(periods=12):
    """Money supply M0/M1/M2. Confirmed: ak.macro_china_money_supply()."""
    try:
        df = ak.macro_china_money_supply()
        # Columns: 月份, M2-数量(亿元), M2-同比增长, M2-环比增长, M1-..., M0-...
        records = _safe_df_to_records(df, periods)
        latest = records[0] if records else {}
        return _result("macro", "m2", {
            "series":      "China Money Supply (NBS / PBoC)",
            "description": "M0, M1, M2 — monthly stock (亿元) and YoY/MoM growth rates",
            "columns":     list(df.columns),
            "latest":      latest,
            "periods":     len(records),
            "records":     records,
        })
    except Exception as e:
        return _error("m2", str(e))


# ── Market structure ──────────────────────────────────────────────────────────

def fetch_northbound():
    """
    HK Connect daily fund flow summary.
    Returns current session summary for all 4 HK Connect channels:
    沪股通 / 深股通 / 港股通(沪) / 港股通(深).
    This is a session snapshot, NOT a historical time series.
    """
    try:
        df = ak.stock_hsgt_fund_flow_summary_em()
        # All rows (typically 4 — one per channel)
        records = _safe_df_to_records(df, n=len(df))
        return _result("market", "northbound", {
            "series":      "HK Connect Fund Flow Summary (East Money)",
            "description": (
                "Current session summary for all 4 HK Connect channels. "
                "成交净买额 = net buy amount; 资金净流入 = net fund inflow. "
                "Rows represent channels, not days."
            ),
            "columns":     list(df.columns),
            "channels":    len(records),
            "records":     records,
        }, warnings=[
            "This is a current-session snapshot, not a historical time series. "
            "For multi-day northbound flow history, a different akshare function with "
            "date range parameters is required (not implemented in this version)."
        ])
    except Exception as e:
        return _error("northbound", str(e))


# ── Regulatory ────────────────────────────────────────────────────────────────

def fetch_regulatory(ticker):
    """
    Regulatory risk signals for an A-share ticker.
    Capability: ST/risk-warning status check (proxy for prior regulatory action or distress).
    Limitation: Does NOT provide CSRC administrative enforcement orders.
                For enforcement records, use Tavily L1 with csrc.gov.cn domain.
    """
    results = {}

    try:
        st_df = ak.stock_zh_a_st_em()
        if st_df is not None and not st_df.empty:
            code_col = next((c for c in st_df.columns if "代码" in c or c.lower() == "code"), None)
            if code_col:
                # Exact match on ticker code to avoid partial-string false positives
                match = st_df[st_df[code_col].astype(str).str.strip() == str(ticker).strip()]
                if not match.empty:
                    results["st_status"] = {
                        "is_st":          True,
                        "detail":         match.head(3).astype(str).to_dict("records"),
                        "interpretation": (
                            "Ticker is currently ST/ST* — indicates regulatory warning, "
                            "financial distress, or prior enforcement action. "
                            "Verify specific cause in latest annual report or CSRC notices."
                        ),
                    }
                else:
                    results["st_status"] = {
                        "is_st":  False,
                        "detail": f"Ticker {ticker} not found in current ST/risk-warning list.",
                    }
            else:
                results["st_status"] = {
                    "error": f"Code column not identified in ST DataFrame. "
                             f"Columns found: {list(st_df.columns)}"
                }
    except Exception as e:
        results["st_status"] = {"error": str(e)}

    return _result("regulatory", f"st-check:{ticker}", results,
        warnings=[
            "ST status is a lagging regulatory proxy, not a real-time enforcement signal. "
            "A company may face active CSRC investigation without yet being ST-flagged.",
            "CSRC administrative enforcement records are NOT available via akshare. "
            "For 行政处罚决定书, use: "
            "python3 skills/tavily_search.py l1 'COMPANY 证监会 行政处罚' --domains csrc.gov.cn",
        ])


# ── Main ──────────────────────────────────────────────────────────────────────

MACRO_DISPATCH = {
    "pmi":    fetch_pmi_official,
    "pmi-cx": fetch_pmi_caixin,
    "ppi":    fetch_ppi,
    "cpi":    fetch_cpi,
    "m2":     fetch_m2,
}


def main():
    parser = argparse.ArgumentParser(
        description="AKShare fetcher: Chinese macro indicators, HK Connect flow, A-share regulatory signals"
    )
    parser.add_argument("--mode", choices=["macro", "market", "regulatory"],
                        help="Data mode")
    parser.add_argument("--indicators", nargs="+",
                        help="Macro indicators (--mode macro): pmi pmi-cx ppi cpi m2")
    parser.add_argument("--indicator",
                        help="Market indicator (--mode market): northbound")
    parser.add_argument("--ticker",
                        help="A-share ticker code (--mode regulatory), e.g. 000001")
    parser.add_argument("--periods", type=int, default=None,
                        help="Number of recent periods for macro data (default: 12). "
                             "Not applicable to market mode.")
    parser.add_argument("--list", action="store_true",
                        help="Print available indicators and exit")
    args = parser.parse_args()

    if args.list:
        print(json.dumps({
            "script_version":       SCRIPT_VERSION,
            "macro_indicators":     MACRO_INDICATORS,
            "market_indicators":    MARKET_INDICATORS,
            "regulatory":           REGULATORY_CAPABILITIES,
            "governance": (
                "Trigger only when analysis requires Chinese-specific data not available via "
                "hkex_fetcher / edgar_fetcher / Tavily. Do not activate by default for all "
                "Chinese companies. All outputs are secondary data requiring verification."
            ),
        }, ensure_ascii=False, indent=2))
        return

    if not args.mode:
        parser.print_help()
        sys.exit(1)

    output = {
        "source":         "akshare_secondary",
        "script_version": SCRIPT_VERSION,
        "retrieved_at":   _now(),
        "mode":           args.mode,
        "governance":     (
            "AKShare outputs are secondary data. Verify material figures against primary "
            "sources (NBS, PBoC, CSRC) before use in analysis. Never use as direct valuation input."
        ),
        "results":  [],
        "warnings": [],
        "errors":   [],
    }

    if args.mode == "macro":
        periods = args.periods if args.periods is not None else 12
        if not args.indicators:
            output["errors"].append(
                f"--indicators required for macro mode. Available: {list(MACRO_DISPATCH)}"
            )
        else:
            for ind in args.indicators:
                ind = ind.lower()
                if ind not in MACRO_DISPATCH:
                    output["errors"].append(
                        f"Unknown indicator '{ind}'. Available: {list(MACRO_DISPATCH)}"
                    )
                    continue
                result = MACRO_DISPATCH[ind](periods=periods)
                if "error" in result:
                    output["errors"].append(result)
                else:
                    output["warnings"].extend(result.pop("warnings", []))
                    output["results"].append(result)

    elif args.mode == "market":
        if args.periods is not None:
            output["warnings"].append(
                "--periods is not applicable to market mode; "
                "the northbound summary table returns all channels (typically 4 rows)."
            )
        ind = (args.indicator or "").lower()
        if not ind:
            output["errors"].append(
                f"--indicator required for market mode. Available: {list(MARKET_INDICATORS)}"
            )
        elif ind == "northbound":
            result = fetch_northbound()
            if "error" in result:
                output["errors"].append(result)
            else:
                output["warnings"].extend(result.pop("warnings", []))
                output["results"].append(result)
        else:
            output["errors"].append(
                f"Unknown market indicator '{ind}'. Available: {list(MARKET_INDICATORS)}"
            )

    elif args.mode == "regulatory":
        if not args.ticker:
            output["errors"].append(
                "--ticker required for regulatory mode (e.g. --ticker 000001)"
            )
        else:
            result = fetch_regulatory(args.ticker)
            if "error" in result:
                output["errors"].append(result)
            else:
                output["warnings"].extend(result.pop("warnings", []))
                output["results"].append(result)

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
