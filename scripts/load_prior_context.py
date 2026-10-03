#!/usr/bin/env python3
"""
load_prior_context.py — Reverse-flow loader for /分析.

When PM (Opus) starts /分析 on a ticker, this script gathers ALL prior context
so PM doesn't start from zero. Returns structured JSON for PM to read at Step 0.5.

Sources gathered:
1. Prior structured PM Verdicts (Buyer_Analyst/verdicts/<ticker>_*.json)
2. RRB lineage history for this ticker (calls Research_Report_Benchmark/scripts/resolver.py lineage)
3. Watchlist current state (Buyer_Analyst/watchlist.json entry for this ticker)
4. Optional: cached info_delta windows since last verdict

Anti-trap-1 mitigation: closes the loop so production has memory across analyses.

Usage:
    python3 scripts/load_prior_context.py 02513.HK
    python3 scripts/load_prior_context.py 02513.HK --output /tmp/prior_ctx.json
    python3 scripts/load_prior_context.py NVDA --pretty   # human-readable

Output schema:
    {
      "ticker": "...",
      "loaded_at": "ISO-8601",
      "has_prior_context": true|false,
      "prior_verdicts": [...],
      "rrb_lineages": {<lineage_id>: [...members...]},
      "watchlist_entry": {...},
      "info_delta_since_last_verdict": [...]
    }
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BUYER_ANALYST_ROOT = Path(__file__).resolve().parents[1]
# Optional external benchmark integration; not bundled in this public snapshot.
RRB_ROOT = Path(os.environ.get(
    "RRB_ROOT", str(BUYER_ANALYST_ROOT / "external" / "Research_Report_Benchmark")
))

VERDICTS_DIR = BUYER_ANALYST_ROOT / "verdicts"
WATCHLIST = BUYER_ANALYST_ROOT / "watchlist.json"
RRB_RESOLVER = RRB_ROOT / "scripts" / "resolver.py"
RRB_INFO_DELTA = RRB_ROOT / "scripts" / "info_delta_logger.py"
RRB_INFO_DELTA_CACHE = RRB_ROOT / "info_delta"
RRB_SIDECAR = RRB_ROOT / "scorecards" / "resolutions_v03.csv"

SUPPORTED_VERDICT_VERSIONS = ["v0.1"]


def normalize_ticker(t: str) -> list:
    """Return list of ticker variants to search.
    HKEx: 2513.HK == 02513.HK == 2513 == 02513 — all equivalent.
    Generates 4-digit AND 5-digit padded forms (HKEx codes vary 1-5 digits)."""
    t = t.upper().strip()
    variants = {t}
    if ".HK" in t:
        code = t.split(".")[0]
        n = int(code)
        for pad in (4, 5):
            variants.add(f"{n:0{pad}d}.HK")
            variants.add(f"{n:0{pad}d}")
        variants.add(f"{n}.HK")
        variants.add(f"{n}")
    return list(variants)


def load_prior_verdicts(ticker_variants):
    """Find structured verdicts for this ticker, sorted by as_of_timestamp ascending."""
    if not VERDICTS_DIR.exists():
        return []
    verdicts = []
    for f in VERDICTS_DIR.glob("*.json"):
        try:
            v = json.load(open(f))
        except Exception:
            continue
        if v.get("ticker", "").upper() in [t.upper() for t in ticker_variants]:
            # Schema-version gate (anti-trap-3)
            sv = v.get("schema_version")
            if sv not in SUPPORTED_VERDICT_VERSIONS:
                v["_warning"] = f"unsupported schema_version={sv}"
            verdicts.append(v)
    verdicts.sort(key=lambda v: v.get("as_of_timestamp", ""))
    return verdicts


def load_rrb_lineages(ticker_variants):
    """Query RRB sidecar for any lineages for this ticker (under any subject_id)."""
    if not RRB_SIDECAR.exists():
        return {}
    import csv
    lineages = {}
    with open(RRB_SIDECAR) as f:
        for row in csv.DictReader(f):
            lid = row.get("prediction_lineage_id", "")
            if not lid:
                continue
            # Match ticker by checking if any variant appears in lineage_id
            for tv in ticker_variants:
                if tv in lid:
                    lineages.setdefault(lid, []).append({
                        "prediction_id": row.get("prediction_id"),
                        "as_of_timestamp": row.get("as_of_timestamp"),
                        "lineage_position": row.get("lineage_position"),
                        "dimension_leaf": row.get("dimension_leaf"),
                        "scoring_mode": row.get("scoring_mode"),
                        "subject_id": row.get("subject_id"),
                    })
                    break
    # Sort each lineage by timestamp
    for lid, members in lineages.items():
        members.sort(key=lambda m: m.get("as_of_timestamp", ""))
    return lineages


def load_watchlist_entry(ticker_variants):
    """Find watchlist entry under any ticker variant."""
    if not WATCHLIST.exists():
        return None
    try:
        wl = json.load(open(WATCHLIST))
    except Exception:
        return None
    tickers = wl.get("tickers", [])
    matches = []
    for entry in tickers:
        et = entry.get("ticker", "").upper()
        if et in [t.upper() for t in ticker_variants]:
            matches.append(entry)
    if len(matches) > 1:
        # Multiple matches under different ticker formats — duplicate flag
        return {"_duplicate_warning": f"{len(matches)} entries under different ticker formats",
                "entries": matches}
    return matches[0] if matches else None


def load_info_delta_since(ticker_variants, since_iso):
    """Find cached info_delta files covering window from since_iso to now."""
    if not RRB_INFO_DELTA_CACHE.exists() or not since_iso:
        return []
    since_date = since_iso[:10] if len(since_iso) >= 10 else since_iso
    matches = []
    for f in RRB_INFO_DELTA_CACHE.glob("*.json"):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        d_ticker = d.get("ticker", "").upper()
        if d_ticker not in [t.upper() for t in ticker_variants]:
            continue
        window_end = d.get("window", {}).get("end", "")
        window_start = d.get("window", {}).get("start", "")
        # Include if window overlaps [since_date, today]
        if window_end >= since_date:
            matches.append({
                "file": f.name,
                "window": d.get("window"),
                "event_count": len(d.get("events", [])),
                "event_types": d.get("event_counts_by_type", {}),
                "events": d.get("events", []),
            })
    return matches


def main():
    p = argparse.ArgumentParser(description="Load prior context for /分析 reverse-flow")
    p.add_argument("ticker", help="e.g. 02513.HK or NVDA — auto-normalizes variants")
    p.add_argument("--output", "-o", help="JSON output file (default: stdout)")
    p.add_argument("--pretty", action="store_true", help="human-readable summary instead of JSON")
    args = p.parse_args()

    variants = normalize_ticker(args.ticker)

    prior_verdicts = load_prior_verdicts(variants)
    rrb_lineages = load_rrb_lineages(variants)
    watchlist_entry = load_watchlist_entry(variants)

    # Info-delta since most recent verdict
    most_recent_verdict_ts = None
    if prior_verdicts:
        most_recent_verdict_ts = prior_verdicts[-1].get("as_of_timestamp")
    info_delta_since = load_info_delta_since(variants, most_recent_verdict_ts)

    out = {
        "ticker": args.ticker,
        "ticker_variants_searched": variants,
        "loaded_at": datetime.now().isoformat(timespec="seconds"),
        "has_prior_context": bool(prior_verdicts) or bool(watchlist_entry),
        "prior_verdicts": prior_verdicts,
        "prior_verdicts_count": len(prior_verdicts),
        "most_recent_verdict_id": prior_verdicts[-1].get("verdict_id") if prior_verdicts else None,
        "most_recent_verdict_as_of": most_recent_verdict_ts,
        "rrb_lineages": rrb_lineages,
        "rrb_lineages_count": len(rrb_lineages),
        "watchlist_entry": watchlist_entry,
        "info_delta_since_last_verdict": info_delta_since,
        "info_delta_event_count": sum(d["event_count"] for d in info_delta_since),
        "supported_verdict_schema_versions": SUPPORTED_VERDICT_VERSIONS,
    }

    if args.pretty:
        print(f"=== Prior Context for {args.ticker} ===")
        print(f"Variants searched: {variants}")
        print(f"Loaded at: {out['loaded_at']}")
        print()
        if not out["has_prior_context"]:
            print("⚠ No prior context found — this is the first analysis on this ticker.")
            return
        print(f"Prior verdicts ({out['prior_verdicts_count']}):")
        for v in prior_verdicts:
            warn = v.get("_warning", "")
            sup = v.get("supersedes") or "—"
            stance = v.get("stance") or "?"
            ts = v.get("as_of_timestamp") or "?"
            print(f"  {v.get('verdict_id', '?'):<60s}  {ts[:10]}  stance={stance:<15s}  supersedes={sup}  {warn}")
        print()
        print(f"RRB lineages ({out['rrb_lineages_count']}):")
        for lid, members in rrb_lineages.items():
            print(f"  {lid}")
            for m in members:
                print(f"    {m.get('as_of_timestamp', '?')[:10]}  pos={m.get('lineage_position', '?'):<8s}  leaf={m.get('dimension_leaf', '?')}  pid={m.get('prediction_id')}")
        print()
        if watchlist_entry:
            if "_duplicate_warning" in watchlist_entry:
                print(f"⚠ Watchlist DUPLICATE: {watchlist_entry['_duplicate_warning']}")
                for e in watchlist_entry["entries"]:
                    print(f"  - {e.get('ticker')}: stance={e.get('stance')}, last={e.get('last_analysis_date')}")
            else:
                print(f"Watchlist entry: {watchlist_entry.get('ticker')} stance={watchlist_entry.get('stance')} last={watchlist_entry.get('last_analysis_date')}")
                print(f"  breaks: {watchlist_entry.get('thesis_break_conditions', [])}")
                print(f"  entry_zone: {watchlist_entry.get('entry_zone')}")
        else:
            print("No watchlist entry.")
        print()
        print(f"Info-delta since last verdict ({out['info_delta_event_count']} events):")
        for d in info_delta_since:
            print(f"  {d['file']}  window={d['window']}  events={d['event_types']}")
        return

    json_out = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(json_out)
        print(f"wrote {args.output}  ({len(prior_verdicts)} prior verdicts, {len(rrb_lineages)} lineages)")
    else:
        print(json_out)


if __name__ == "__main__":
    main()
