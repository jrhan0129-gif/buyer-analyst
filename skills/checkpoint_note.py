#!/usr/bin/env python3
"""
checkpoint_note.py — Generate structured checkpoint_notes for watchlist tickers.

Two modes:
  (1) live      — pull current market data, compare to watchlist entry, save note
  (2) backfill  — extract note from an existing daily_logs/*_packet.json

Output: daily_logs/{ticker_slug}_checkpoint_{YYYY-MM-DD}.json

Schema used by agents/harness_editor.md for temporal contrast.
"""

import json
import argparse
import sys
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
WATCHLIST = ROOT / "watchlist.json"
DAILY_LOGS = ROOT / "daily_logs"


# ── Utilities ──────────────────────────────────────────────────────────────

def _ticker_slug(ticker: str) -> str:
    """1913.HK → 1913_HK (safe for filenames)"""
    return ticker.upper().replace(".", "_")


def _load_watchlist():
    if not WATCHLIST.exists():
        return {"tickers": []}
    with open(WATCHLIST) as f:
        return json.load(f)


def _find_entry(watchlist, ticker):
    for t in watchlist["tickers"]:
        if t["ticker"].upper() == ticker.upper():
            return t
    return None


def _checkpoint_path(ticker: str, date_str: str) -> Path:
    return DAILY_LOGS / f"{_ticker_slug(ticker)}_checkpoint_{date_str}.json"


# ── Commodity fetch (harness_editor Bug #2 fix, 2026-04-22) ────────────────
# Closes the gap where H005 only added commodity fetch to /分析 pipeline but
# not to checkpoint_note's own path. Without this, cron-refreshed checkpoints
# kept commodity break conditions UNMEASURABLE forever.

COMMODITY_KEYWORDS = {
    # Keyword found in condition → commodity name passed to cme_fetcher
    "gold":        "gold",
    "silver":      "silver",
    "copper":      "copper",
    "platinum":    "platinum",
    "palladium":   "palladium",
    "brent":       "brent crude",
    "wti":         "wti crude oil",
    "crude":       "wti crude oil",
    "natural gas": "natural gas",
    "natgas":      "natural gas",
    "corn":        "corn",
    "wheat":       "wheat",
    "soybean":     "soybeans",
    "coffee":      "coffee",
    "cotton":      "cotton",
    "sugar":       "sugar",
}


def _extract_commodity_refs(entry) -> set:
    """Scan watchlist entry's break conditions for commodity references."""
    commodities = set()
    if not entry:
        return commodities
    for cond in entry.get("thesis_break_conditions", []):
        c_lower = cond.lower()
        for kw, commodity in COMMODITY_KEYWORDS.items():
            if kw in c_lower:
                commodities.add(commodity)
    return commodities


def _fetch_commodity_context(commodities: set) -> dict:
    """
    Shell out to cme_fetcher for each detected commodity.
    Returns {commodity_name_lower: {current_price, prev_close, day_change_pct,
                                    price_3mo_ago, change_vs_3mo_pct, unit}}.
    """
    if not commodities:
        return {}
    import subprocess
    result = {}
    for commodity in commodities:
        try:
            r = subprocess.run(
                ["python3", str(ROOT / "skills/cme_fetcher.py"),
                 "--commodity", commodity],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode != 0:
                continue
            data = json.loads(r.stdout)
            for c in data.get("commodities", []):
                result[c["name"].lower()] = {
                    "symbol": c.get("symbol"),
                    "current_price": c.get("current_price"),
                    "prev_close": c.get("prev_close"),
                    "day_change_pct": c.get("day_change_pct"),
                    "price_3mo_ago": c.get("price_3mo_ago"),
                    "change_vs_3mo_pct": c.get("change_vs_3mo_pct"),
                    "unit": c.get("unit"),
                }
        except Exception:
            continue
    return result


def _lookup_commodity(condition_lower: str, commodity_context: dict):
    """Given a condition keyword match, find the commodity price dict."""
    if not commodity_context:
        return None, None
    # Map keyword → canonical name used in cme_fetcher output
    keyword_to_name = {
        "gold": "gold", "silver": "silver", "copper": "copper",
        "platinum": "platinum", "palladium": "palladium",
        "brent": "brent crude", "wti": "wti crude oil",
        "crude": "wti crude oil", "oil": "wti crude oil",
        "natural gas": "natural gas", "natgas": "natural gas",
        "corn": "corn", "wheat": "wheat", "soybean": "soybeans",
        "coffee": "coffee", "cotton": "cotton", "sugar": "sugar",
    }
    for kw, name in keyword_to_name.items():
        if kw in condition_lower and name in commodity_context:
            return name, commodity_context[name]
    return None, None


# ── Break condition status evaluator ───────────────────────────────────────

def _evaluate_break_condition(condition: str, market: dict,
                              commodity_context: dict = None) -> dict:
    """
    Best-effort classification of break condition status.
    Returns {status: TRIGGERED|APPROACHING|MONITORED|UNMEASURABLE, note: str}
    Conservative — default to MONITORED unless we have a clear quantitative match.

    commodity_context: optional dict from _fetch_commodity_context; when present
                       enables resolution of brent/wti/gold/etc threshold conditions.
    """
    c = condition.lower()
    price = market.get("price")
    ccx = commodity_context or {}

    # ── Commodity threshold resolution (Bug #2 fix) ────────────────────────
    # Match patterns: "Gold below USD2200", "Brent sustained above 95",
    # "WTI <90", "Brent reaches USD90", etc.
    commodity_name, commodity_data = _lookup_commodity(c, ccx)
    if commodity_data and commodity_data.get("current_price") is not None:
        # Extract numeric threshold (with optional direction)
        m = re.search(r"(<|>|below|above|under|over|reaches?)\s*(?:usd)?\s*\$?\s*(\d+(?:\.\d+)?)", c)
        if m:
            direction = m.group(1).lower()
            threshold = float(m.group(2))
            current = commodity_data["current_price"]
            # Normalize direction
            is_below_trigger = direction in ("<", "below", "under")
            is_above_trigger = direction in (">", "above", "over")
            # Bug #5 fix (2026-04-22): "reaches X" is directionally ambiguous in
            # financial context. "Brent reaches $90" can mean "drops to $90"
            # (in a short-oil thesis) or "rises to $90" (in a bullish thesis).
            # Treat as ambiguous — show both scenarios, flag MONITORED, let PM decide.
            is_reaches = "reach" in direction

            if is_reaches:
                # Emit both interpretations, let user read the thesis context
                distance_pct = abs(current - threshold) / threshold * 100
                if distance_pct < 10:
                    status = "APPROACHING"
                    side = "above" if current > threshold else "below"
                    note = (f"{commodity_name.title()} ${current:.2f}, within 10% of ${threshold:.0f} "
                            f"(currently {side}). Direction of 'reaches' is ambiguous — "
                            f"if thesis expects FALL to {threshold}, status = APPROACHING from above; "
                            f"if thesis expects RISE to {threshold}, also APPROACHING. Manual review.")
                else:
                    status = "MONITORED"
                    side = "above" if current > threshold else "below"
                    note = (f"{commodity_name.title()} ${current:.2f}, {distance_pct:.0f}% {side} ${threshold:.0f}. "
                            f"'reaches' is directionally ambiguous — check thesis context.")
                return {"condition": condition, "status": status, "note": note,
                        "commodity": commodity_name, "current": current, "threshold": threshold,
                        "direction_ambiguous": True}

            if is_below_trigger:
                if current < threshold:
                    status = "TRIGGERED"
                    note = f"{commodity_name.title()} ${current:.2f} < threshold ${threshold:.0f}"
                elif current < threshold * 1.1:
                    status = "APPROACHING"
                    note = f"{commodity_name.title()} ${current:.2f} within 10% of ${threshold:.0f} threshold"
                else:
                    status = "MONITORED"
                    margin_pct = (current / threshold - 1) * 100
                    note = (f"{commodity_name.title()} ${current:.2f}, "
                            f"{margin_pct:.0f}% above ${threshold:.0f} threshold — safe")
                return {"condition": condition, "status": status, "note": note,
                        "commodity": commodity_name, "current": current, "threshold": threshold}
            elif is_above_trigger:
                if current > threshold:
                    status = "TRIGGERED"
                    note = f"{commodity_name.title()} ${current:.2f} > threshold ${threshold:.0f}"
                elif current > threshold * 0.9:
                    status = "APPROACHING"
                    note = f"{commodity_name.title()} ${current:.2f} within 10% of ${threshold:.0f} threshold"
                else:
                    status = "MONITORED"
                    margin_pct = (1 - current / threshold) * 100
                    note = (f"{commodity_name.title()} ${current:.2f}, "
                            f"{margin_pct:.0f}% below ${threshold:.0f} threshold — safe")
                return {"condition": condition, "status": status, "note": note,
                        "commodity": commodity_name, "current": current, "threshold": threshold}

    # Fallback: keyword match but no numeric threshold extracted
    if commodity_data and commodity_data.get("current_price") is not None:
        return {"condition": condition, "status": "MONITORED",
                "note": f"{commodity_name.title()} current ${commodity_data['current_price']:.2f}; "
                        f"no numeric threshold parsed — manual review"}
    if commodity_data:  # matched but price is None (yfinance fetch failed)
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": f"{commodity_name.title()} keyword matched but price unavailable — retry"}

    # If condition mentions commodity but we failed to fetch context
    for kw in COMMODITY_KEYWORDS:
        if kw in c:
            return {"condition": condition, "status": "UNMEASURABLE",
                    "note": f"{kw.title()} price fetch failed — retry cme_fetcher manually"}

    # Gross margin thresholds (e.g., "GM <35% for 2Q")
    m = re.search(r"gm\s*<\s*(\d+)", c)
    if m and market.get("gross_margin") is not None:
        threshold = float(m.group(1)) / 100
        actual = market["gross_margin"]
        if actual < threshold:
            return {"condition": condition, "status": "TRIGGERED",
                    "note": f"GM {actual*100:.1f}% < threshold {threshold*100:.0f}%"}
        elif actual < threshold * 1.1:
            return {"condition": condition, "status": "APPROACHING",
                    "note": f"GM {actual*100:.1f}% within 10% of {threshold*100:.0f}% threshold"}
        else:
            return {"condition": condition, "status": "MONITORED",
                    "note": f"GM {actual*100:.1f}% vs threshold {threshold*100:.0f}% — safe"}

    # Net debt / leverage thresholds (qualitative check)
    if "net debt" in c or "leverage" in c or "d/e" in c:
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "Requires balance sheet detail not in checkpoint"}

    # Anything mentioning YoY / QoQ / specific quarter (English + Chinese)
    if any(k in c for k in ["yoy", "qoq", "quarter", "q1", "q2", "q3", "q4", "2q", "for 2"]):
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "Requires quarterly filing data — check on next earnings"}
    # Chinese quarterly / annual patterns (harness_editor P1 #3, 2026-04-22)
    if any(k in condition for k in ["连续", "季度", "季", "年度", "半年", "全年",
                                     "月", "H1", "H2", "上半年", "下半年"]):
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "Requires quarterly/annual filing data (CN)"}

    # Chinese financial ratio thresholds
    if re.search(r"(收益率|增速|毛利率|净利率|负债率|偿付能力|资本充足率|现金流|派息|分红).*[<>《》]?\s*\d", condition):
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "Requires filing data for the named financial ratio (CN)"}

    # Insurance-specific metrics (§8.4) — English and Chinese
    if any(k in c for k in ["nbv", "cor", "solvency", "ev ", "embedded value", "vif", "rbc"]):
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "Insurance metric — requires EV disclosure (§8.4)"}
    if any(k in condition for k in ["NBV", "COR", "投资收益率", "偿付能力", "内含价值", "新业务价值", "综合成本率"]):
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "Insurance metric — requires EV disclosure (§8.4, CN)"}

    # Banking metrics
    if any(k in c for k in ["nim", "npl", "tier 1", "car", "capital ratio"]):
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "Banking metric — requires regulatory filing (§8.4)"}

    # Order / backlog / delivery / guidance / capex (English)
    if any(k in c for k in ["order", "backlog", "delivery", "guidance", "capex"]):
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "Requires filing or company disclosure"}
    # Same, CN
    if any(k in condition for k in ["订单", "在手", "交付", "指引", "资本开支", "加盟", "门店", "签约"]):
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "Requires filing or company disclosure (CN)"}

    # Revenue / sales conditions with number
    if re.search(r"(revenue|sales|ssg|sss).*[<>]\s*\d", c):
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "Requires quarterly/annual filing"}
    if re.search(r"(收入|营收|销售|同店).*[<>]?\s*\d", condition):
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "Requires quarterly/annual filing (CN)"}

    # Regulatory / legal
    if any(k in c for k in ["fda", "doj", "sec ", "nhtsa", "recall", "investigation",
                            "csrc", "pboc", "cbirc", "hkma"]):
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "Requires regulatory news check (tavily/court)"}

    # ASP trends
    if "asp" in c or "ASP" in condition:
        return {"condition": condition, "status": "UNMEASURABLE",
                "note": "ASP requires segment-level disclosure"}

    # Default
    return {"condition": condition, "status": "MONITORED",
            "note": "No automated check; manual review needed"}


# ── Entry zone parser + stance pressure (harness_editor P1 #4, 2026-04-22) ──

def _parse_entry_zone(zone_str: str):
    """
    Parse various entry zone formats into (low, high) numeric tuple.
    Returns None if unparseable.

    Supported:
      "HK$34-38"        → (34, 38)
      "$550-600"        → (550, 600)
      "$70-80"          → (70, 80)
      "HKD 0.80-0.90"   → (0.80, 0.90)
      "HK$28-32"        → (28, 32)
    """
    if not zone_str:
        return None
    # Strip currency codes and symbols, keep digits/decimal/dash
    cleaned = re.sub(r"[A-Z$€¥£]+\s*", "", zone_str, flags=re.IGNORECASE)
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)", cleaned)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return (lo, hi)
    # Single number (e.g. "Short @ 70")
    m = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None


def compute_stance_pressure(entry: dict, price: float):
    """
    Compare current price vs entry_zone and stance.
    Returns a dict with status + recommended action.
    Returns None if entry is missing or unparseable.
    """
    if not entry or not price:
        return None
    zone_str = entry.get("entry_zone", "")
    stance = (entry.get("stance") or "").strip()
    zone = _parse_entry_zone(zone_str)

    if zone is None:
        return {
            "status": "UNPARSEABLE",
            "action": "NONE",
            "note": f"entry_zone '{zone_str}' cannot be parsed — watchlist entry may be corrupted",
            "zone_parsed": None,
            "price": price,
            "stance": stance,
        }

    lo, hi = zone
    inside = lo <= price <= hi
    above = price > hi
    below = price < lo

    # Default
    result = {
        "zone_parsed": {"low": lo, "high": hi},
        "price": price,
        "stance": stance,
    }

    # Watch stance + inside zone → upgrade candidate
    if stance.lower() == "watch" and inside:
        result.update({
            "status": "INSIDE_ZONE",
            "action": "UPGRADE_CANDIDATE",
            "note": (f"Price {price} inside entry zone [{lo}, {hi}]. "
                     f"Stance is Watch — PM should either upgrade to Buy or "
                     f"document why Watch persists."),
        })
    elif stance.lower() == "buy" and above:
        result.update({
            "status": "ABOVE_ZONE",
            "action": "REVIEW_CANDIDATE",
            "note": (f"Price {price} above Buy entry zone [{lo}, {hi}] "
                     f"({(price/hi - 1)*100:.1f}% above top). "
                     f"Fresh capital deployment needs catalyst revalidation."),
        })
    elif stance.lower() == "avoid" and inside:
        result.update({
            "status": "INSIDE_ZONE",
            "action": "REVIEW_CANDIDATE",
            "note": (f"Price {price} inside zone [{lo}, {hi}] while stance is Avoid. "
                     f"If bear conditions have eased, consider downgrading to Watch."),
        })
    elif stance.lower() in ("short-bias", "shortbias", "short") and inside:
        result.update({
            "status": "ENTRY_EXECUTABLE",
            "action": "DOWNGRADE_CANDIDATE",
            "note": (f"Short entry zone [{lo}, {hi}] encompasses current price {price}. "
                     f"Position-sizing decision window."),
        })
    elif stance.lower() in ("short-bias", "shortbias", "short") and below:
        result.update({
            "status": "BELOW_ZONE",
            "action": "REVIEW_CANDIDATE",
            "note": (f"Price {price} below short entry [{lo}, {hi}]. "
                     f"Short thesis may have played out — consider closing/reviewing."),
        })
    elif inside:
        result.update({"status": "INSIDE_ZONE", "action": "MONITOR", "note": "Price inside zone, stance holds."})
    elif above:
        result.update({"status": "ABOVE_ZONE", "action": "MONITOR",
                       "note": f"Price {(price/hi - 1)*100:.1f}% above zone top."})
    else:
        result.update({"status": "BELOW_ZONE", "action": "MONITOR",
                       "note": f"Price {(1 - price/lo)*100:.1f}% below zone bottom."})

    return result


# ── Live mode ──────────────────────────────────────────────────────────────

def generate_live(ticker: str) -> dict:
    """Pull current market data and compose a checkpoint note."""
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance not installed", file=sys.stderr)
        sys.exit(1)

    watchlist = _load_watchlist()
    entry = _find_entry(watchlist, ticker)

    # Fetch market data
    yf_ticker = ticker
    # HKEx: 02601.HK → 2601.HK (strip leading zeros, known yfinance quirk)
    if yf_ticker.endswith(".HK"):
        code = yf_ticker.split(".")[0]
        if code.startswith("0") and len(code) > 4:
            yf_ticker = f"{int(code):04d}.HK"

    # Retry wrapper for TLS flakiness (known yfinance issue)
    import time as _time
    info, hist = {}, None
    last_err = None
    for attempt in range(3):
        try:
            tk = yf.Ticker(yf_ticker)
            info = tk.info or {}
            hist = tk.history(period="5d")
            break
        except Exception as e:
            last_err = e
            if attempt < 2:
                _time.sleep(2 ** attempt)  # 1s, 2s, 4s backoff
                continue
    if not info and hist is None:
        print(f"[WARN] yfinance failed for {yf_ticker} after 3 retries: {last_err}", file=sys.stderr)

    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if price is None and not hist.empty:
        price = float(hist["Close"].iloc[-1])

    market = {
        "price": price,
        "currency": info.get("currency"),
        "market_cap": info.get("marketCap"),
        "forward_pe": info.get("forwardPE"),
        "trailing_pe": info.get("trailingPE"),
        "ps_ratio": info.get("priceToSalesTrailing12Months"),
        "pb_ratio": info.get("priceToBook"),
        "ev_ebitda": info.get("enterpriseToEbitda"),
        "gross_margin": info.get("grossMargins"),
        "operating_margin": info.get("operatingMargins"),
        "fifty_two_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_low": info.get("fiftyTwoWeekLow"),
        "inst_pct": info.get("heldPercentInstitutions"),
        "short_pct": info.get("shortPercentOfFloat"),
        "beta": info.get("beta"),
    }

    # Evidence state — auto-generated observations
    evidence_state = []
    if price:
        ev = f"Price {price:.2f}"
        if market["currency"]:
            ev += f" {market['currency']}"
        if market["fifty_two_low"] and market["fifty_two_high"]:
            lo, hi = market["fifty_two_low"], market["fifty_two_high"]
            pct = (price - lo) / (hi - lo) * 100 if hi > lo else 0
            ev += f" ({pct:.0f}% of 52w range [{lo:.2f}, {hi:.2f}])"
        evidence_state.append(ev)

    if market["gross_margin"] is not None:
        evidence_state.append(f"Gross margin {market['gross_margin']*100:.1f}% (ttm)")
    if market["operating_margin"] is not None:
        evidence_state.append(f"Operating margin {market['operating_margin']*100:.1f}% (ttm)")
    if market["forward_pe"]:
        evidence_state.append(f"Forward P/E {market['forward_pe']:.1f}x")
    if market["ps_ratio"]:
        evidence_state.append(f"P/S {market['ps_ratio']:.2f}x")
    if market["pb_ratio"]:
        evidence_state.append(f"P/B {market['pb_ratio']:.2f}x")
    if market["short_pct"] is not None:
        evidence_state.append(f"Short % of float {market['short_pct']*100:.1f}%")

    # Fetch commodity context if entry's break conditions reference commodities
    commodities = _extract_commodity_refs(entry)
    commodity_context = _fetch_commodity_context(commodities) if commodities else {}

    # Break condition status (now commodity-aware)
    break_status = []
    if entry:
        for cond in entry.get("thesis_break_conditions", []):
            break_status.append(_evaluate_break_condition(cond, market, commodity_context))

    # Add commodity prices to evidence_state for visibility
    for cname, cdata in commodity_context.items():
        cp = cdata.get("current_price")
        chg = cdata.get("day_change_pct")
        chg_3mo = cdata.get("change_vs_3mo_pct")
        if cp is not None:
            parts = [f"{cname.title()}: ${cp:.2f}"]
            if chg is not None:
                parts.append(f"{chg:+.2f}% d/d")
            if chg_3mo is not None:
                parts.append(f"{chg_3mo:+.2f}% 3mo")
            evidence_state.append(" ".join(parts))

    # Entry zone comparison
    if entry and entry.get("entry_zone") and price:
        zone = entry["entry_zone"]
        evidence_state.append(f"Entry zone per watchlist: {zone} (current {price:.2f})")

    # Reasoning trajectory (from watchlist only)
    reasoning = {
        "current_stance": entry["stance"] if entry else None,
        "stance_since": entry.get("last_analysis_date") if entry else None,
        "watchlist_notes": entry.get("name") if entry else None,
    }

    # Unresolved concerns — derived from UNMEASURABLE conditions
    unresolved = [
        f"[{bs['condition']}] {bs['note']}"
        for bs in break_status
        if bs["status"] == "UNMEASURABLE"
    ]

    # Stance pressure (harness_editor P1 #4)
    stance_pressure = compute_stance_pressure(entry, price) if entry else None

    now = datetime.now()
    return {
        "meta": {
            "ticker": ticker.upper(),
            "checkpoint_date": now.strftime("%Y-%m-%d"),
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "live",
            "yf_ticker_used": yf_ticker,
        },
        "market_snapshot": market,
        "commodity_context": commodity_context,  # Bug #2 fix: empty {} if no commodity refs
        "evidence_state": evidence_state,
        "reasoning_trajectory": reasoning,
        "break_condition_status": break_status,
        "unresolved_concerns": unresolved,
        "stance_pressure": stance_pressure,
    }


# ── Backfill mode ──────────────────────────────────────────────────────────

def backfill_from_packet(packet_path: Path) -> dict:
    """Derive a checkpoint note from an existing data_recon_packet JSON."""
    packet_path = packet_path.resolve()
    with open(packet_path) as f:
        pkt = json.load(f)

    ticker = pkt["meta"]["ticker"]
    generated = pkt["meta"]["generated_at"]  # e.g. "2026-04-03 18:20:18"
    date_str = generated.split(" ")[0]

    market = pkt.get("market_data", {})
    analysis = pkt.get("analysis_profile", {})
    health = pkt.get("health_check", {}) or {}

    watchlist = _load_watchlist()
    entry = _find_entry(watchlist, ticker)

    # Evidence state
    evidence_state = []
    if market.get("price"):
        ev = f"Price {market['price']}"
        if market.get("currency"):
            ev += f" {market['currency']}"
        if market.get("fifty_two_low") and market.get("fifty_two_high"):
            lo, hi = market["fifty_two_low"], market["fifty_two_high"]
            evidence_state.append(f"{ev} (52w range [{lo}, {hi}])")
        else:
            evidence_state.append(ev)
    if market.get("gross_margin") is not None:
        evidence_state.append(f"Gross margin {market['gross_margin']*100:.1f}%")
    if market.get("forward_pe"):
        evidence_state.append(f"Forward P/E {market['forward_pe']:.1f}x")
    if market.get("ps_ratio"):
        evidence_state.append(f"P/S {market['ps_ratio']:.2f}x")
    if market.get("pb_ratio"):
        evidence_state.append(f"P/B {market['pb_ratio']:.2f}x")
    if market.get("ev_ebitda") is not None:
        evidence_state.append(f"EV/EBITDA {market['ev_ebitda']:.1f}x")

    # Health flags from packet
    flags = health.get("flags", []) if isinstance(health, dict) else []
    if flags:
        evidence_state.append("Health flags: " + ", ".join(
            f.get("id", str(f)) if isinstance(f, dict) else str(f) for f in flags
        ))

    # Analysis profile signals
    if analysis.get("name"):
        evidence_state.append(f"Analysis profile: {analysis['name']}")
    if analysis.get("valuation_primary"):
        evidence_state.append(f"Primary valuation: {analysis['valuation_primary'].upper()}")

    # Reasoning trajectory
    reasoning = {
        "current_stance": entry["stance"] if entry else None,
        "stance_since": entry.get("last_analysis_date") if entry else None,
        "watchlist_notes": entry.get("name") if entry else None,
        "bull_focus": analysis.get("bull_focus", []),
        "bear_focus": analysis.get("bear_focus", []),
    }

    # Commodity context (Bug #2 fix — also applies to backfill path)
    commodities = _extract_commodity_refs(entry)
    commodity_context = _fetch_commodity_context(commodities) if commodities else {}

    # Break condition status (using packet's market data + live commodity context)
    break_status = []
    if entry:
        for cond in entry.get("thesis_break_conditions", []):
            break_status.append(_evaluate_break_condition(cond, market, commodity_context))

    # Add commodity prices to evidence_state
    for cname, cdata in commodity_context.items():
        cp = cdata.get("current_price")
        if cp is not None:
            evidence_state.append(f"{cname.title()}: ${cp:.2f} (live fetch)")

    unresolved = [
        f"[{bs['condition']}] {bs['note']}"
        for bs in break_status
        if bs["status"] == "UNMEASURABLE"
    ]

    # Stance pressure (harness_editor P1 #4)
    stance_pressure = compute_stance_pressure(entry, market.get("price")) if entry else None

    return {
        "meta": {
            "ticker": ticker.upper(),
            "checkpoint_date": date_str,
            "generated_at": generated,
            "source": "backfill_from_packet",
            "source_ref": str(packet_path.relative_to(ROOT)),
        },
        "market_snapshot": {
            "price": market.get("price"),
            "currency": market.get("currency"),
            "market_cap": market.get("market_cap"),
            "forward_pe": market.get("forward_pe"),
            "trailing_pe": market.get("trailing_pe"),
            "ps_ratio": market.get("ps_ratio"),
            "pb_ratio": market.get("pb_ratio"),
            "ev_ebitda": market.get("ev_ebitda"),
            "gross_margin": market.get("gross_margin"),
            "operating_margin": market.get("operating_margin"),
            "fifty_two_high": market.get("fifty_two_high"),
            "fifty_two_low": market.get("fifty_two_low"),
        },
        "evidence_state": evidence_state,
        "reasoning_trajectory": reasoning,
        "break_condition_status": break_status,
        "unresolved_concerns": unresolved,
        "stance_pressure": stance_pressure,
        "sector_classification": pkt.get("sector"),
        "analysis_profile_ref": analysis.get("claude_md_section"),
    }


# ── Save & CLI ─────────────────────────────────────────────────────────────

def save_checkpoint(note: dict) -> Path:
    ticker = note["meta"]["ticker"]
    date_str = note["meta"]["checkpoint_date"]
    path = _checkpoint_path(ticker, date_str)
    DAILY_LOGS.mkdir(exist_ok=True)
    with open(path, "w") as f:
        json.dump(note, f, ensure_ascii=False, indent=2)
    return path


def cmd_live(args):
    note = generate_live(args.ticker)
    path = save_checkpoint(note)
    print(f"[checkpoint:live] {args.ticker} → {path.relative_to(ROOT)}")
    print(f"  price: {note['market_snapshot']['price']}  "
          f"stance: {note['reasoning_trajectory']['current_stance']}  "
          f"evidence_points: {len(note['evidence_state'])}  "
          f"unresolved: {len(note['unresolved_concerns'])}")


def cmd_backfill(args):
    path_in = Path(args.packet)
    if not path_in.exists():
        print(f"Packet not found: {args.packet}", file=sys.stderr)
        sys.exit(1)
    note = backfill_from_packet(path_in)
    path = save_checkpoint(note)
    print(f"[checkpoint:backfill] {note['meta']['ticker']} ({note['meta']['checkpoint_date']}) "
          f"→ {path.relative_to(ROOT)}")


def cmd_list(args):
    """List all checkpoint notes for a ticker."""
    if args.ticker:
        slug = _ticker_slug(args.ticker)
        pattern = f"{slug}_checkpoint_*.json"
    else:
        pattern = "*_checkpoint_*.json"

    files = sorted(DAILY_LOGS.glob(pattern))
    if not files:
        print("No checkpoint notes found")
        return

    for f in files:
        with open(f) as fp:
            note = json.load(fp)
        m = note["meta"]
        print(f"  {m['ticker']:10s} {m['checkpoint_date']}  "
              f"[{m['source']}]  {len(note['evidence_state'])} evidence, "
              f"{len(note['unresolved_concerns'])} unresolved")


def main():
    p = argparse.ArgumentParser(description="Checkpoint note generator")
    sub = p.add_subparsers(dest="cmd")

    pl = sub.add_parser("live", help="Generate checkpoint from live market data")
    pl.add_argument("--ticker", required=True)

    pb = sub.add_parser("backfill", help="Extract checkpoint from existing packet JSON")
    pb.add_argument("--packet", required=True)

    pL = sub.add_parser("list", help="List existing checkpoint notes")
    pL.add_argument("--ticker", default=None)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        sys.exit(1)

    {"live": cmd_live, "backfill": cmd_backfill, "list": cmd_list}[args.cmd](args)


if __name__ == "__main__":
    main()
