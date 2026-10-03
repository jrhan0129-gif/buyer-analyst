#!/usr/bin/env python3
"""
pipeline_runner.py — One-command /分析 pipeline (方案C).

Runs Steps 1-7 serially, outputs a unified data_recon_packet.json
for Opus main thread to consume for Steps 8-13 (judgment).

Usage:
    python3 skills/pipeline_runner.py NVDA
    python3 skills/pipeline_runner.py 1109.HK --skip-filing --skip-forensic
    python3 skills/pipeline_runner.py DAL --output /tmp/dal_packet.json

Steps executed:
    1. Sector classification → §8.x addenda routing
    2. Live market data (price, mcap, multiples, momentum)
    3. Health check (risk flags, tier candidate)
    4. Primary filing fetch + key financial extraction
    5. Forensic accounting (if triggered by health flags or --force-forensic)
    6. Falsification recon via Tavily L1 (overnight news + risks)
    7. Peer comparables (auto-discovered)
    8. Assemble data_recon_packet.json

Does NOT do: valuation, Bull/Bear, PM verdict — those are Opus judgment tasks.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# Add skills to path for data_utils import
sys.path.insert(0, SCRIPT_DIR)
from data_utils import normalize_ticker, detect_exchange, get_yf_info, fmt_billions, retry
from analysis_profiles import get_profile, print_profile


def run_skill(script_name, args_list, timeout=90):
    """Run a skill script, return (stdout, stderr, returncode)."""
    cmd = [sys.executable, os.path.join(SCRIPT_DIR, script_name)] + args_list
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", f"TIMEOUT after {timeout}s", -1
    except Exception as e:
        return "", str(e), -1


def try_json_parse(text):
    """Try to parse JSON from stdout, return dict or None."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def step_sector(ticker):
    """Step 1: Sector classification."""
    print(f"  [1/8] Sector classification...", flush=True)
    out, err, rc = run_skill("sector_classifier.py", [ticker])
    parsed = try_json_parse(out)
    if parsed:
        return parsed

    # Fallback: get sector from yfinance directly
    info = get_yf_info(ticker)
    return {
        "ticker": ticker,
        "sector": info.get("sector", "Unknown"),
        "industry": info.get("industry", "Unknown"),
        "addenda": [],
        "source": "yfinance_fallback",
    }


def step_market_data(ticker):
    """Step 2: Live market data."""
    print(f"  [2/8] Live market data...", flush=True)
    out, err, rc = run_skill("live_market_fetcher.py", [ticker])

    # Also get structured data via yfinance directly for the packet
    info = get_yf_info(ticker)
    return {
        "price": info.get("regularMarketPrice") or info.get("currentPrice"),
        "currency": info.get("currency", "USD"),
        "market_cap": info.get("marketCap"),
        "market_cap_fmt": fmt_billions(info.get("marketCap")),
        "forward_pe": info.get("forwardPE"),
        "trailing_pe": info.get("trailingPE"),
        "ps_ratio": info.get("priceToSalesTrailing12Months"),
        "ev_ebitda": info.get("enterpriseToEbitda"),
        "ev_revenue": info.get("enterpriseToRevenue"),
        "pb_ratio": info.get("priceToBook"),
        "beta": info.get("beta"),
        "short_pct": info.get("shortPercentOfFloat"),
        "inst_pct": info.get("heldPercentInstitutions"),
        "fifty_two_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_low": info.get("fiftyTwoWeekLow"),
        "gross_margin": info.get("grossMargins"),
        "operating_margin": info.get("operatingMargins"),
        "revenue_growth": info.get("revenueGrowth"),
        "roe": info.get("returnOnEquity"),
        "display_text": out.strip() if rc == 0 else f"[fetch failed: {err.strip()[:100]}]",
    }


def step_health_check(ticker):
    """Step 3: Health check → risk flags."""
    print(f"  [3/8] Health check...", flush=True)
    out, err, rc = run_skill("health_checker.py", [ticker])
    # health_checker outputs human-readable, also try --json if available
    json_out, _, json_rc = run_skill("health_checker.py", [ticker, "--json"])
    parsed = try_json_parse(json_out)
    return {
        "parsed": parsed,
        "display_text": out.strip() if rc == 0 else f"[health check failed: {err.strip()[:100]}]",
        "flags": parsed.get("active_flags", []) if parsed else [],
        "candidate_tier": parsed.get("candidate_tier", "unknown") if parsed else "unknown",
    }


def step_filing(ticker, exchange):
    """Step 4: Primary filing fetch + extraction."""
    print(f"  [4/8] Primary filing ({exchange.upper()})...", flush=True)
    filing_type = "10-K" if exchange == "us" else "annual_results"
    out, err, rc = run_skill("filing_extractor.py", [
        "--ticker", ticker.replace(".HK", ""),
        "--exchange", exchange,
        "--filing-type", filing_type,
        "--json",
    ], timeout=120)
    parsed = try_json_parse(out)
    if parsed and parsed.get("status") == "ok":
        return {
            "status": "ok",
            "filing_date": parsed.get("filing_metadata", {}).get("filing_date", ""),
            "fields_found": parsed.get("fields_found", []),
            "fields_missing": parsed.get("fields_missing", []),
            "key_financials": parsed.get("key_financials", {}),
            "local_path": parsed.get("local_path", ""),
        }
    return {
        "status": "failed",
        "error": err.strip()[:200] if err else (parsed or {}).get("status", "unknown"),
        "warnings": (parsed or {}).get("warnings", []),
    }


def step_forensic(ticker):
    """Step 5: Forensic accounting screen."""
    print(f"  [5/8] Forensic accounting...", flush=True)
    out, err, rc = run_skill("forensic_accounting.py", [ticker, "--json"])
    parsed = try_json_parse(out)
    if parsed and parsed.get("status") != "error":
        flags = parsed.get("forensic_summary", {}).get("active_flags", [])
        return {
            "status": parsed.get("status", "ok"),
            "m_score": parsed.get("m_score", {}).get("value"),
            "m_signal": parsed.get("m_score", {}).get("signal"),
            "accruals_signal": (parsed.get("accruals") or {}).get("signal"),
            "accruals_ratio": (parsed.get("accruals") or {}).get("ratio_pct"),
            "active_flags": flags,
            "flag_ids": [f["id"] for f in flags],
        }
    return {"status": "failed", "error": err.strip()[:200] if err else "unknown"}


def step_tavily_recon(ticker, company_name):
    """Step 6: Falsification recon via Tavily L1."""
    print(f"  [6/8] Falsification recon (Tavily)...", flush=True)
    queries = [
        f"{company_name} {ticker} risk lawsuit investigation regulatory",
        f"{company_name} {ticker} latest news earnings",
    ]
    results = []
    for q in queries:
        out, err, rc = run_skill("tavily_search.py", [
            "l1", q, "--topic", "news", "--time-range", "week", "--max-results", "5"
        ], timeout=45)
        if rc == 0 and out.strip():
            results.append({"query": q, "raw": out.strip()[:3000]})
        else:
            results.append({"query": q, "raw": f"[tavily failed: {err.strip()[:100]}]"})
    return results


def step_peer_comps(ticker):
    """Step 7: Peer comparables (auto-discovered)."""
    print(f"  [7/8] Peer comparables...", flush=True)
    out, err, rc = run_skill("peer_comps.py", ["--ticker", ticker, "--max-peers", "5"], timeout=120)
    parsed = try_json_parse(out)
    if parsed:
        return parsed
    return {"error": err.strip()[:200] if err else "unknown", "comparison": []}


def step_insider(ticker):
    """Step 7b: Insider activity (Form 4 / HKEx disclosure)."""
    print(f"  [7b/8] Insider activity...", flush=True)
    out, err, rc = run_skill("insider_tracker.py", [ticker, "--json"], timeout=60)
    parsed = try_json_parse(out)
    if parsed:
        return parsed
    # Non-JSON output or error — return raw summary
    if rc == 0 and out.strip():
        return {"status": "ok", "raw": out.strip()[:2000]}
    return {"status": "no_data", "note": err.strip()[:200] if err else "no insider data"}


def step_valuation(ticker, market, financials, sector_info, peers):
    """Step 7c: Auto-run valuation_matrix.py with available data."""
    print(f"  [7c/8] Valuation matrix...", flush=True)

    rev = financials.get("total_revenue")
    gp = financials.get("gross_profit")
    fcf = financials.get("free_cash_flow")
    growth = market.get("revenue_growth")

    if not rev or not fcf:
        return {"status": "skipped", "reason": "missing revenue or FCF"}

    gm_pct = (gp / rev * 100) if gp and rev else 20.0
    growth_pct = (growth * 100) if growth else 3.0

    args = [
        str(round(rev / 1e6)) if rev > 1e8 else str(round(rev)),
        str(round(gm_pct, 1)),
        str(round(fcf / 1e6)) if abs(fcf) > 1e8 else str(round(fcf)),
        str(round(growth_pct, 1)),
        "--ticker", ticker,
        "--explicit-fcf", str(round(fcf / 1e6)) if abs(fcf) > 1e8 else str(round(fcf)),
    ]

    # Add sector if available
    sector = sector_info.get("sector", "").lower().replace(" ", "_")
    if sector:
        args.extend(["--sector", sector])

    # Add net debt if available
    debt = financials.get("total_debt", 0) or 0
    cash = financials.get("cash_and_cash_equivalents", 0) or 0
    if debt or cash:
        net_debt = debt - cash
        args.extend(["--net-debt", str(round(net_debt / 1e6)) if abs(net_debt) > 1e8 else str(round(net_debt))])

    # Add shares + price from market data
    price = market.get("price")
    mcap = market.get("market_cap")
    if price and mcap and price > 0:
        shares = mcap / price
        args.extend(["--shares-outstanding", str(round(shares / 1e6))])
        args.extend(["--current-price", str(price)])

    out, err, rc = run_skill("valuation_matrix.py", args, timeout=60)
    if rc == 0:
        # Combine stdout (main output) + stderr (warnings/debug) for full picture
        combined = out.strip()
        warnings = [l for l in (err or "").strip().split("\n")
                    if l.strip() and "Note: this version" not in l]
        return {
            "status": "ok",
            "output": combined[:8000],
            "warnings": warnings[:20],
        }
    return {"status": "failed", "error": err.strip()[:500]}


def should_run_forensic(health_flags, force=False):
    """Determine if forensic gate is triggered per §2.3."""
    if force:
        return True
    trigger_flags = {
        "BENEISH_MANIPULATION_RISK", "BENEISH_WATCH",
        "ACCRUALS_HIGH", "WORKING_CAPITAL_DETERIORATION",
        "MARGIN_FRAGILITY", "MARGIN_DATA_ANOMALY",
    }
    for f in health_flags:
        fid = f.get("id", "") if isinstance(f, dict) else str(f)
        if fid in trigger_flags:
            return True
    return False


def assemble_packet(ticker, sector, market, health, filing, forensic, recon, peers, profile=None):
    """Step 8: Assemble data_recon_packet."""
    info = get_yf_info(ticker)
    company_name = info.get("longName") or info.get("shortName") or ticker

    if profile is None:
        from analysis_profiles import get_profile as _gp
        profile = _gp(sector.get("sector", ""), sector.get("industry", ""))

    # Derive key financials for valuation
    inc, bs, cf = None, None, None
    try:
        from data_utils import get_yf_financials
        inc, bs, cf = get_yf_financials(ticker)
    except Exception:
        pass

    # Determine which fields are primary-filing-verified vs yfinance-only
    filing_verified_fields = set()
    if filing and filing.get("status") == "ok":
        filing_verified_fields = set(filing.get("fields_found", []))

    financials = {}
    financials_provenance = {}  # Track source tier for each field

    if inc is not None and not inc.empty:
        col = inc.columns[0]
        for key in ["Total Revenue", "Gross Profit", "Operating Income", "Net Income", "EBITDA"]:
            if key in inc.index:
                field_name = key.lower().replace(" ", "_")
                financials[field_name] = float(inc.loc[key, col])
                # Check if filing also found this field
                filing_key_map = {
                    "total_revenue": "revenue", "gross_profit": "gross_margin",
                    "net_income": "net_income", "operating_income": "operating_income",
                }
                if filing_key_map.get(field_name, field_name) in filing_verified_fields:
                    financials_provenance[field_name] = "primary_filing_corroborated"
                else:
                    financials_provenance[field_name] = "yfinance_only [unverified secondary]"

    if bs is not None and not bs.empty:
        col = bs.columns[0]
        for key in ["Total Debt", "Cash And Cash Equivalents", "Total Assets",
                     "Current Assets", "Current Liabilities"]:
            if key in bs.index:
                field_name = key.lower().replace(" ", "_")
                financials[field_name] = float(bs.loc[key, col])
                filing_key_map = {
                    "total_debt": "total_debt", "cash_and_cash_equivalents": "cash",
                    "total_assets": "total_assets",
                }
                if filing_key_map.get(field_name, field_name) in filing_verified_fields:
                    financials_provenance[field_name] = "primary_filing_corroborated"
                else:
                    financials_provenance[field_name] = "yfinance_only [unverified secondary]"

    if cf is not None and not cf.empty:
        col = cf.columns[0]
        for key in ["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow"]:
            if key in cf.index:
                field_name = key.lower().replace(" ", "_")
                financials[field_name] = float(cf.loc[key, col])
                if "fcf" in filing_verified_fields and field_name == "free_cash_flow":
                    financials_provenance[field_name] = "primary_filing_corroborated"
                else:
                    financials_provenance[field_name] = "yfinance_only [unverified secondary]"

    # ── Cross-source conflict detection ────────────────────────────────────────
    # Compare yfinance numbers vs filing-extracted text for key fields.
    # Flag divergence >15% as SOURCE_CONFLICT per §3.4.
    source_conflicts = []
    filing_kf = (filing or {}).get("key_financials", {})

    def _extract_numbers(text_list):
        """Extract financial numbers from filing text snippets.
        Only extracts HIGH-CONFIDENCE numbers: requires $ prefix, billion/million unit,
        or comma-formatted large numbers in clearly financial context.
        Returns list of (raw_value, [candidate_scaled_values]) tuples.
        """
        import re
        results = []
        for t in (text_list or []):
            # Skip XBRL tags, metadata — broad filter
            if any(x in t for x in [
                "Member", "us-gaap:", "srt:", "xbrli:", "0000",
                # Skip ticker-specific XBRL prefixes (3-4 letter codes followed by colon)
            ]):
                # Check if it's pure XBRL or has readable text mixed in
                readable_ratio = sum(1 for c in t if c.isalpha() and c.islower()) / max(len(t), 1)
                if readable_ratio < 0.3:  # Mostly tags, not readable text
                    continue

            # Skip risk factor / disclaimer text (no actual numbers)
            if any(x in t.lower() for x in [
                "cannot make any assurances", "subject to risks", "no assurance",
                "forward-looking statements", "trading price of our",
            ]):
                continue

            # PATTERN 1: Dollar amounts — "$2.6 billion", "$ 5,005"
            for m in re.finditer(r'\$\s*([\d,]+\.?\d*)\s*(billion|million|B|M|bn|mn)?', t, re.IGNORECASE):
                raw = m.group(1).replace(",", "")
                try:
                    val = float(raw)
                except ValueError:
                    continue
                if val == 0 or (2000 <= val <= 2035):
                    continue
                unit = (m.group(2) or "").lower()
                candidates = []
                if unit in ("billion", "b", "bn"):
                    candidates.append(val * 1e9)
                elif unit in ("million", "m", "mn"):
                    candidates.append(val * 1e6)
                elif val > 100:
                    # No unit after $ — likely "in millions" (US filing convention)
                    candidates.append(val * 1e6)
                    candidates.append(val)  # or raw
                else:
                    candidates.append(val * 1e9)  # "$2.6" likely "$2.6 billion"
                    candidates.append(val * 1e6)
                if candidates:
                    results.append((val, candidates))

            # PATTERN 2: RMB/¥ amounts — "RMB 430.81 billion", "¥535亿"
            for m in re.finditer(r'(?:RMB|¥|￥)\s*([\d,]+\.?\d*)\s*(billion|million|亿|万|B|M)?', t, re.IGNORECASE):
                raw = m.group(1).replace(",", "")
                try:
                    val = float(raw)
                except ValueError:
                    continue
                if val == 0:
                    continue
                unit = (m.group(2) or "").lower()
                candidates = []
                if unit in ("billion", "b"):
                    candidates.append(val * 1e9)
                elif unit in ("million", "m"):
                    candidates.append(val * 1e6)
                elif unit == "亿":
                    candidates.append(val * 1e8)
                elif unit == "万":
                    candidates.append(val * 1e4)
                else:
                    candidates.append(val * 1e8)  # Default RMB in 亿
                    candidates.append(val * 1e6)
                if candidates:
                    results.append((val, candidates))

            # PATTERN 3: Large comma-formatted numbers without $ (e.g., "3,994,754")
            # Only if they look like financial statement line items (>100,000)
            for m in re.finditer(r'(?<!\$)\b([\d]{1,3}(?:,\d{3}){2,})\b', t):
                raw = m.group(1).replace(",", "")
                try:
                    val = float(raw)
                except ValueError:
                    continue
                if val > 100000:
                    # Could be in thousands (US filing) or raw
                    results.append((val, [val, val * 1e3]))

        return results

    # Fields to cross-check: yfinance field → filing key_financials key
    cross_check_map = {
        "total_revenue": "revenue",
        "net_income": "net_income",
        "free_cash_flow": "fcf",
    }

    for yf_field, filing_field in cross_check_map.items():
        yf_val = financials.get(yf_field)
        filing_texts = filing_kf.get(filing_field)
        if yf_val and filing_texts and yf_val != 0:
            filing_nums = _extract_numbers(filing_texts)
            if not filing_nums:
                continue
            # Find the candidate scaling that best matches yfinance
            best_match = None
            best_ratio = float("inf")
            for raw_val, candidates in filing_nums:
                for scaled in candidates:
                    ratio = abs(scaled - yf_val) / abs(yf_val)
                    if ratio < best_ratio:
                        best_ratio = ratio
                        best_match = (raw_val, scaled, ratio)

            # If best match is within 5%, sources agree — confirmed
            if best_match and best_match[2] <= 0.05:
                if yf_field in financials_provenance:
                    financials_provenance[yf_field] = "cross_source_confirmed"
            elif best_match and 0.05 < best_match[2] <= 0.15:
                # Minor divergence — likely rounding or timing difference
                pass
            elif best_match and 0.15 < best_match[2] <= 0.50:
                # Material divergence — likely different accounting period or definition
                raw_val, scaled, ratio = best_match
                source_conflicts.append({
                    "field": yf_field,
                    "yfinance_value": yf_val,
                    "filing_value_raw": raw_val,
                    "filing_value_best_scaled": scaled,
                    "divergence_pct": round(ratio * 100, 1),
                    "severity": "MEDIUM",
                    "action": "Likely definition/period mismatch. Verify which figure is appropriate for analysis. (§3.4)"
                })
            elif best_match and best_match[2] > 0.50:
                # Extreme divergence — filing snippet likely refers to a different metric
                # (e.g., segment revenue, 9-month figure, transaction value)
                # Do NOT flag as conflict — mark as inconclusive
                raw_val, scaled, ratio = best_match
                source_conflicts.append({
                    "field": yf_field,
                    "yfinance_value": yf_val,
                    "filing_value_raw": raw_val,
                    "filing_value_best_scaled": scaled,
                    "divergence_pct": round(ratio * 100, 1),
                    "severity": "INCONCLUSIVE",
                    "action": "Filing snippet likely refers to a different metric (segment, partial year, transaction). Not a true conflict — verify manually."
                })

    # Compute derived metrics
    rev = financials.get("total_revenue")
    gp = financials.get("gross_profit")
    debt = financials.get("total_debt", 0)
    cash = financials.get("cash_and_cash_equivalents", 0)

    packet = {
        "meta": {
            "ticker": ticker,
            "company": company_name,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "pipeline_version": "2.0-cognitive",
        },
        "sector": sector,
        "analysis_profile": {
            "name": profile.name,
            "valuation_primary": profile.valuation_primary,
            "valuation_notes": profile.valuation_notes,
            "key_metrics": profile.key_metrics,
            "bear_focus": profile.bear_focus,
            "bull_focus": profile.bull_focus,
            "forensic_skipped": not profile.run_forensic,
            "claude_md_section": profile.claude_md_section,
        },
        "market_data": market,
        "health_check": {
            "flags": health.get("flags", []),
            "candidate_tier": health.get("candidate_tier"),
            "display": health.get("display_text", ""),
        },
        "primary_filing": filing,
        "forensic": forensic,
        "falsification_recon": recon,
        "peer_comps": peers,
        "financials": financials,
        "financials_provenance": financials_provenance,
        "source_conflicts": source_conflicts,
        "data_quality": {
            "primary_filing_status": filing.get("status") if filing else "not_run",
            "fields_verified_by_filing": list(filing_verified_fields),
            "unverified_count": sum(1 for v in financials_provenance.values() if "unverified" in v),
            "total_fields": len(financials_provenance),
            "warning": (
                "⚠️ Most financials are [unverified secondary] — primary filing did not corroborate. "
                "Do not use as primary valuation input without cross-check."
                if sum(1 for v in financials_provenance.values() if "unverified" in v) > len(financials_provenance) * 0.5
                else None
            ),
        },
        "derived": {
            "gross_margin_pct": round(gp / rev * 100, 1) if gp and rev else None,
            "net_debt": debt - cash if debt and cash else None,
            "net_debt_fmt": fmt_billions(debt - cash) if debt and cash else "N/A",
            "fcf": financials.get("free_cash_flow"),
            "fcf_fmt": fmt_billions(financials.get("free_cash_flow")),
        },
        "pm_instructions": {
            "next_steps": [
                "Review valuation output (included in packet if auto-run)",
                "Execute Bear-first → Bull-second adversarial (§5 Step 8)",
                "PM Verdict with pricing scenario table per §6.3",
                "Verdict Validator self-check",
                "watchlist_manager.py add",
            ],
            "blocking_conditions": [],
        },
    }

    # Add blocking conditions
    if forensic and "BENEISH_MANIPULATION_RISK" in (forensic.get("flag_ids") or []):
        packet["pm_instructions"]["blocking_conditions"].append(
            "BENEISH_MANIPULATION_RISK: audit primary filing before valuation (§2.3)"
        )
    if filing and filing.get("status") == "failed":
        packet["pm_instructions"]["blocking_conditions"].append(
            "Primary filing fetch failed: label all financials [unverified secondary] (§5 Step 3.5)"
        )

    # Block if >50% of financials are unverified
    unverified = sum(1 for v in financials_provenance.values() if "unverified" in v)
    if financials_provenance and unverified > len(financials_provenance) * 0.5:
        packet["pm_instructions"]["blocking_conditions"].append(
            f"DATA QUALITY: {unverified}/{len(financials_provenance)} financial fields are "
            f"[unverified secondary]. Cross-check against primary filing before valuation."
        )

    # Block only on MEDIUM source conflicts (true definition/period mismatch)
    # INCONCLUSIVE = likely different metric, not a real conflict
    for sc in source_conflicts:
        sev = sc.get("severity", "")
        if sev == "MEDIUM":
            field = sc.get("field", "?")
            div = sc.get("divergence_pct", 0)
            packet["pm_instructions"]["blocking_conditions"].append(
                f"SOURCE_CONFLICT ({sev}): {field} diverges {div}% between yfinance and filing. "
                f"Verify definition. Use sensitivity range. (§3.4)"
            )

    return packet


# ── v0.3 (2026-04-27): structured PM Verdict template emitter ─────────────
# Closes the bridge to RRB by giving PM a pre-filled scaffold + auto-loading prior context.

VERDICTS_DIR = os.path.join(PROJECT_ROOT, "verdicts")


def _load_prior_context(ticker):
    """Call scripts/load_prior_context.py and return parsed JSON, or {} on failure."""
    loader = os.path.join(PROJECT_ROOT, "scripts", "load_prior_context.py")
    if not os.path.exists(loader):
        return {}
    try:
        r = subprocess.run([sys.executable, loader, ticker], capture_output=True,
                           text=True, timeout=30)
        if r.returncode == 0:
            return json.loads(r.stdout)
    except Exception:
        pass
    return {}


def _next_verdict_version(ticker, today_iso):
    """Return next available v<N> on a given date for a ticker (handles same-day reruns)."""
    if not os.path.exists(VERDICTS_DIR):
        return 1
    n = 1
    while True:
        path = os.path.join(VERDICTS_DIR, f"{ticker}_{today_iso}_v{n}.json")
        if not os.path.exists(path):
            return n
        n += 1


def emit_verdict_template(ticker, packet, prior_context):
    """
    Generate a structured PM Verdict template (schema v0.1) from recon packet.
    Auto-fills what's known; leaves PM-judgment fields as placeholders with TODO markers.
    Sets rrb_export.ready=false — PM must finalize and flip to true before bridging.

    Returns (path_written, verdict_id).
    """
    os.makedirs(VERDICTS_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    n = _next_verdict_version(ticker, today)
    verdict_id = f"buyer_analyst_{ticker}_{today}_v{n}"
    path = os.path.join(VERDICTS_DIR, f"{ticker}_{today}_v{n}.json")

    market = packet.get("market_data", {})
    sector_info = packet.get("sector", {})
    health_flags = [f.get("id") for f in packet.get("health_check", {}).get("flags", []) if isinstance(f, dict)]

    # Prior context: pull most recent verdict_id for supersedes
    supersedes = None
    prior_watchlist = None
    if prior_context.get("most_recent_verdict_id"):
        supersedes = prior_context["most_recent_verdict_id"]
    if prior_context.get("watchlist_entry"):
        we = prior_context["watchlist_entry"]
        if "_duplicate_warning" not in we:
            prior_watchlist = {
                "stance": we.get("stance"),
                "added_date": we.get("added_date"),
                "breaks_intact_count": len(we.get("thesis_break_conditions", [])),
                "breaks_broken_count": 0,  # PM to update during analysis
            }

    template = {
        "schema_version": "v0.1",
        "verdict_id": verdict_id,
        "supersedes": supersedes,
        "ticker": ticker,
        "subject": "buyer_analyst",
        "subject_version": packet.get("meta", {}).get("pipeline_version", "unknown"),
        "as_of_timestamp": datetime.now().isoformat(timespec="seconds"),
        "company_name": packet.get("meta", {}).get("company", ""),
        "sector": sector_info.get("sector", ""),
        "addenda_applied": sector_info.get("addenda", []),

        "stance": "TODO_pm_fill_one_of: buy | watch | re-underwrite | avoid | short-bias",

        "current_market_state": {
            "price": market.get("price"),
            "currency": market.get("currency"),
            "market_cap_local": market.get("market_cap"),
            "as_of": today,
        },

        "pricing_anchor": {
            "primary_method": "TODO_pm_fill: EV/GP | DCF | P/S | SOTP | P/B | rNPV | EV/EBITDA",
            "secondary_method": None,
            "deprioritized_method": None,
            "scenarios": [
                {
                    "name": "bear",
                    "probability": 0.30,
                    "valuation_multiple": {"type": "TODO", "value": None},
                    "fair_value_per_share": None,
                    "currency": market.get("currency"),
                    "rationale": "TODO_pm_fill",
                    "implied_return_pct": None,
                },
                {
                    "name": "base",
                    "probability": 0.50,
                    "valuation_multiple": {"type": "TODO", "value": None},
                    "fair_value_per_share": None,
                    "currency": market.get("currency"),
                    "rationale": "TODO_pm_fill",
                    "implied_return_pct": None,
                },
                {
                    "name": "bull",
                    "probability": 0.20,
                    "valuation_multiple": {"type": "TODO", "value": None},
                    "fair_value_per_share": None,
                    "currency": market.get("currency"),
                    "rationale": "TODO_pm_fill",
                    "implied_return_pct": None,
                },
            ],
            "expected_value": None,
            "current_implied_multiple": {
                "type": "P/S",
                "value": market.get("ps_ratio"),
            },
        },

        "entry_zone": {
            "low": None,
            "high": None,
            "currency": market.get("currency"),
            "preconditions": [],
        },

        "thesis_break_conditions": [
            {
                "id": "TODO_pm_fill_descriptive_id",
                "description": "TODO PM fill — one structured break per critical risk",
                "metric": "TODO",
                "operator": "<",
                "threshold": None,
                "consecutive_periods": 1,
                "current_value": None,
                "data_source": "primary_filing | next_quarterly_disclosure | yfinance | tavily",
                "horizon_months": 12,
                "status": "intact",
                "implied_probability_within_horizon": 0.30,
                "links_to_scenario": "bear",
            }
        ],

        "re_underwrite_triggers": [],

        "evidence_chain": [
            {
                "line": "text",
                "source_type": "primary_filing",
                "source_ref": "TODO PM fill",
                "claim": "TODO PM fill",
                "tier": "T?",
            },
            {
                "line": "valuation",
                "source_type": "computed",
                "source_ref": "valuation_matrix.py output",
                "method": "TODO",
                "claim": "TODO PM fill — current implied multiple vs peer norm",
                "tier": "T3",
            }
        ],

        "execution_friction": [],

        "rrb_export": {
            "ready": False,
            "lineage_strategy": "stable_per_leaf",
            "leaves_emitted": ["fa.target_price.direction", "ri.recall"],
            "predictions_exported_count": None,
        },

        "narrative": {
            "executive_summary_md": "TODO PM fill",
            "variant_perception_md": "TODO PM fill",
            "long_thesis_md": "TODO PM fill (Bull agent output goes here, steel-manned)",
            "falsification_case_md": "TODO PM fill (Bear agent output goes here)",
            "vs_prior_verdict_md": "TODO PM fill — explicit comparison vs prior verdict (REQUIRED if supersedes != null)" if supersedes else "First verdict on this ticker (no prior).",
        },

        "prior_context_loaded": {
            "loaded_at": datetime.now().isoformat(timespec="seconds"),
            "prior_verdict_id": supersedes,
            "rrb_lineage_members_seen": prior_context.get("rrb_lineages_count", 0),
            "watchlist_state_at_load": prior_watchlist,
            "info_delta_event_count_since_last": prior_context.get("info_delta_event_count", 0),
        },

        "_template_meta": {
            "emitted_by": "pipeline_runner.py emit_verdict_template",
            "emitted_at": datetime.now().isoformat(timespec="seconds"),
            "next_steps": [
                "1. PM fills all TODO_pm_fill markers",
                "2. PM sets stance, scenarios with probs (must sum to 1.0)",
                "3. PM ensures vs_prior_verdict_md addresses supersedes diff",
                "4. PM sets rrb_export.ready = true",
                '5. Optional RRB export (requires separate installation): python3 "${RRB_ROOT:?Set RRB_ROOT}/scripts/buyer_analyst_to_rrb.py" '
                + path,
                "6. Run from repository root: python3 scripts/verdict_to_markdown.py "
                + path,
            ],
            "health_flags_at_emit": health_flags,
        },
    }

    with open(path, "w") as f:
        json.dump(template, f, ensure_ascii=False, indent=2)
    return path, verdict_id


def main():
    parser = argparse.ArgumentParser(
        description="One-command /分析 data pipeline (Steps 1-7)")
    parser.add_argument("ticker", help="Ticker symbol (e.g. NVDA, 1109.HK, DAL)")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path for data_recon_packet.json (default: stdout)")
    parser.add_argument("--skip-filing", action="store_true",
                        help="Skip primary filing fetch (use yfinance only)")
    parser.add_argument("--skip-forensic", action="store_true",
                        help="Skip forensic accounting even if triggered")
    parser.add_argument("--force-forensic", action="store_true",
                        help="Force forensic accounting regardless of triggers")
    parser.add_argument("--skip-tavily", action="store_true",
                        help="Skip Tavily recon (offline mode)")
    parser.add_argument("--skip-peers", action="store_true",
                        help="Skip peer comparables")
    parser.add_argument("--skip-insider", action="store_true",
                        help="Skip insider activity check")
    parser.add_argument("--skip-valuation", action="store_true",
                        help="Skip auto valuation_matrix run")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON only (no progress messages)")
    parser.add_argument("--skip-verdict-template", action="store_true",
                        help="Skip emitting structured verdict template (default: emit)")
    parser.add_argument("--skip-prior-context", action="store_true",
                        help="Skip auto-loading prior context (default: load)")
    args = parser.parse_args()

    ticker = normalize_ticker(args.ticker)
    exchange = detect_exchange(ticker)
    quiet = args.json

    if not quiet:
        print(f"\n{'='*60}")
        print(f"  PIPELINE RUNNER: {ticker} ({exchange.upper()})")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}\n")

    start = time.time()

    # Step 1: Sector + Profile
    sector = step_sector(ticker)
    profile = get_profile(sector.get("sector", ""), sector.get("industry", ""))
    if not quiet:
        print(f"    → Sector: {sector.get('sector')} | Industry: {sector.get('industry')}")
        print(f"    → Profile: {profile.name} | Valuation: {profile.valuation_primary}")
        if profile.key_metrics:
            print(f"    → Key metrics: {', '.join(profile.key_metrics[:4])}")

    # Step 2: Market data
    market = step_market_data(ticker)
    if not quiet:
        print(f"    → Price: {market.get('price')} {market.get('currency')} | "
              f"MCap: {market.get('market_cap_fmt')} | Fwd PE: {market.get('forward_pe')}")

    # Step 3: Health check
    health = step_health_check(ticker)
    flags = health.get("flags", [])
    if not quiet:
        n_flags = len(flags)
        high = sum(1 for f in flags if isinstance(f, dict) and f.get("severity") == "HIGH")
        print(f"    → Flags: {n_flags} total ({high} high) | Tier: {health.get('candidate_tier')}")

    # Step 4: Filing (profile may override)
    filing = None
    do_filing = profile.run_filing and not args.skip_filing
    if do_filing:
        filing = step_filing(ticker, exchange)
        if not quiet:
            if filing.get("status") == "ok":
                print(f"    → Filing: {filing.get('filing_date')} | "
                      f"Found: {', '.join(filing.get('fields_found', []))}")
            else:
                print(f"    → Filing: FAILED — {filing.get('error', 'unknown')[:80]}")
    else:
        filing = {"status": "skipped"}
        if not quiet:
            print(f"    → Filing: skipped")

    # Step 5: Forensic (profile-aware — skip for insurance/REIT/bank)
    forensic = None
    do_forensic = profile.run_forensic and not args.skip_forensic
    if do_forensic and should_run_forensic(flags, args.force_forensic):
        forensic = step_forensic(ticker)
        if not quiet:
            if forensic.get("status") != "failed":
                print(f"    → Forensic: M={forensic.get('m_signal')} | "
                      f"Accruals={forensic.get('accruals_signal')} | "
                      f"Flags: {forensic.get('flag_ids', [])}")
            else:
                print(f"    → Forensic: FAILED — {forensic.get('error', '')[:80]}")
    elif not do_forensic:
        forensic = {"status": "skipped_by_profile", "reason": f"Profile '{profile.name}' skips forensic"}
        if not quiet:
            print(f"    → Forensic: skipped (profile: {profile.name})")
    else:
        forensic = {"status": "not_triggered"}
        if not quiet:
            print(f"    → Forensic: not triggered")

    # Step 6: Tavily recon (with profile-specific extra queries)
    recon = []
    if not args.skip_tavily:
        info = get_yf_info(ticker)
        company_name = info.get("longName") or info.get("shortName") or ticker
        recon = step_tavily_recon(ticker, company_name)

        # Run profile-specific extra recon queries
        if profile.extra_recon_queries:
            for q_template in profile.extra_recon_queries:
                q = q_template.replace("{company}", company_name).replace("{ticker}", ticker)
                out, err, rc = run_skill("tavily_search.py", [
                    "l1", q, "--topic", "news", "--time-range", "month", "--max-results", "3"
                ], timeout=45)
                if rc == 0 and out.strip():
                    recon.append({"query": q, "raw": out.strip()[:2000], "source": "profile_recon"})
                else:
                    recon.append({"query": q, "raw": f"[tavily failed]", "source": "profile_recon"})

        if not quiet:
            ok = sum(1 for r in recon if "[tavily failed" not in r.get("raw", ""))
            total = len(recon)
            profile_q = len(profile.extra_recon_queries)
            print(f"    → Tavily: {ok}/{total} queries OK (incl. {profile_q} profile-specific)")
    else:
        if not quiet:
            print(f"    → Tavily: skipped")

    # Step 7: Peer comps
    peers = {}
    if not args.skip_peers:
        peers = step_peer_comps(ticker)
        if not quiet:
            peer_list = peers.get("peer_set", [])
            auto = peers.get("auto_discovered", False)
            print(f"    → Peers: {peer_list} {'(auto)' if auto else '(manual)'}")
    else:
        if not quiet:
            print(f"    → Peers: skipped")

    # Step 7b: Insider activity
    insider = {}
    if not args.skip_insider:
        insider = step_insider(ticker)
        if not quiet:
            status = insider.get("status", "unknown")
            print(f"    → Insider: {status}")
    else:
        if not quiet:
            print(f"    → Insider: skipped")

    # Step 7c: Valuation (auto-run with available data)
    valuation = {}
    if not args.skip_valuation:
        # Need financials first — get from yfinance
        info = get_yf_info(ticker)
        inc_t, bs_t, cf_t = None, None, None
        try:
            from data_utils import get_yf_financials
            inc_t, bs_t, cf_t = get_yf_financials(ticker)
        except Exception:
            pass
        fin_for_val = {}
        if inc_t is not None and not inc_t.empty:
            col = inc_t.columns[0]
            for key in ["Total Revenue", "Gross Profit", "Operating Income", "Net Income"]:
                if key in inc_t.index:
                    fin_for_val[key.lower().replace(" ", "_")] = float(inc_t.loc[key, col])
        if bs_t is not None and not bs_t.empty:
            col = bs_t.columns[0]
            for key in ["Total Debt", "Cash And Cash Equivalents"]:
                if key in bs_t.index:
                    fin_for_val[key.lower().replace(" ", "_")] = float(bs_t.loc[key, col])
        if cf_t is not None and not cf_t.empty:
            col = cf_t.columns[0]
            if "Free Cash Flow" in cf_t.index:
                fin_for_val["free_cash_flow"] = float(cf_t.loc["Free Cash Flow", col])

        valuation = step_valuation(ticker, market, fin_for_val, sector, peers)
        if not quiet:
            if valuation.get("status") == "ok":
                # Extract key line from output
                out = valuation.get("output", "")
                for line in out.split("\n"):
                    if "主方法" in line or "primary" in line.lower():
                        print(f"    → Valuation: {line.strip()}")
                        break
                else:
                    print(f"    → Valuation: completed")
            else:
                print(f"    → Valuation: {valuation.get('status')} — {valuation.get('reason', valuation.get('error', ''))[:60]}")
    else:
        if not quiet:
            print(f"    → Valuation: skipped")

    # Step 8: Assemble packet
    if not quiet:
        print(f"\n  [8/8] Assembling data_recon_packet...")

    packet = assemble_packet(ticker, sector, market, health, filing, forensic, recon, peers, profile)
    packet["insider"] = insider
    packet["valuation"] = valuation

    elapsed = time.time() - start
    packet["meta"]["elapsed_seconds"] = round(elapsed, 1)

    if not quiet:
        print(f"\n{'='*60}")
        print(f"  PIPELINE COMPLETE: {ticker}")
        print(f"  Elapsed: {elapsed:.1f}s")
        conflicts = packet.get("source_conflicts", [])
        if conflicts:
            print(f"  ⚠️  SOURCE CONFLICTS:")
            for sc in conflicts:
                print(f"    - {sc['field']}: yfinance={sc['yfinance_value']:.0f} vs "
                      f"filing={sc['filing_value_raw']} (÷{sc['divergence_pct']}%)")
        blocking = packet["pm_instructions"]["blocking_conditions"]
        if blocking:
            print(f"  ⚠️  BLOCKING CONDITIONS:")
            for b in blocking:
                print(f"    - {b}")
        print(f"{'='*60}\n")

    # Output
    output_json = json.dumps(packet, ensure_ascii=False, indent=2, default=str)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output_json)
        if not quiet:
            print(f"  Saved: {args.output}")
    else:
        if args.json:
            print(output_json)
        else:
            # Save to default location
            default_path = os.path.join(
                PROJECT_ROOT, "daily_logs",
                f"{datetime.now().strftime('%Y-%m-%d')}_{ticker.replace('.', '_')}_packet.json"
            )
            os.makedirs(os.path.dirname(default_path), exist_ok=True)
            with open(default_path, "w") as f:
                f.write(output_json)
            print(f"  Saved: {default_path}")

    # ── v0.3 (2026-04-27): Reverse flow + verdict template ──────────────
    if not args.skip_prior_context:
        prior_ctx = _load_prior_context(ticker)
        if not quiet:
            n_prior = prior_ctx.get("prior_verdicts_count", 0)
            n_lin = prior_ctx.get("rrb_lineages_count", 0)
            n_evts = prior_ctx.get("info_delta_event_count", 0)
            wl_dup = (prior_ctx.get("watchlist_entry") or {}).get("_duplicate_warning")
            print(f"\n  PRIOR CONTEXT: {n_prior} prior verdict(s), {n_lin} RRB lineage(s), "
                  f"{n_evts} info-delta event(s) since last")
            if prior_ctx.get("most_recent_verdict_id"):
                print(f"    most recent: {prior_ctx['most_recent_verdict_id']}  "
                      f"({prior_ctx.get('most_recent_verdict_as_of', '?')[:10]})")
            if wl_dup:
                print(f"    ⚠ watchlist DUPLICATE: {wl_dup}")
    else:
        prior_ctx = {}
        if not quiet:
            print(f"\n  PRIOR CONTEXT: skipped (--skip-prior-context)")

    if not args.skip_verdict_template:
        try:
            vpath, vid = emit_verdict_template(ticker, packet, prior_ctx)
            if not quiet:
                print(f"\n  VERDICT TEMPLATE: {vpath}")
                print(f"    verdict_id: {vid}")
                print(f"    schema: v0.1  ·  ready=false  ·  PM fills TODO markers + sets ready=true")
                print(f"    next: edit verdict, then run buyer_analyst_to_rrb.py to bridge to RRB")
        except Exception as e:
            if not quiet:
                print(f"\n  VERDICT TEMPLATE: FAILED — {e}")


if __name__ == "__main__":
    main()
