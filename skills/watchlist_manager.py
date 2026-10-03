#!/usr/bin/env python3
"""
watchlist_manager.py — Manage the investment watchlist.

Auto-adds tickers after /分析 with thesis break conditions and stance.
Supports add, remove, list, and update operations.

Usage:
    python3 skills/watchlist_manager.py add --ticker 1109.HK --stance Watch \
        --breaks "Net debt ratio >45%" "GM <18% for 2 consecutive quarters" \
        --entry-zone "HK$28-32" --last-analysis "2026-03-31" --notes "CR Land"
    python3 skills/watchlist_manager.py remove --ticker 1109.HK
    python3 skills/watchlist_manager.py list [--json]
    python3 skills/watchlist_manager.py update --ticker 1109.HK --stance Buy
"""

import json
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

WATCHLIST_PATH = Path(__file__).parent.parent / "watchlist.json"


# ─── Entry validation (harness_editor P1 #5, 2026-04-22) ─────
# Detects template corruption from shell $-escaping. Issues warnings;
# does not block saves (to avoid breaking automated pipelines).
def _validate_entry(entry):
    warnings = []
    name = entry.get("name", "") or ""
    zone = entry.get("entry_zone", "") or ""

    # Name truncated after "at" or similar transitional words
    if re.search(r"\b(at|@)\s*$", name.strip()):
        warnings.append(f"name truncated after 'at': '{name}'")
    # Name ends with currency code, value missing
    if re.search(r"\b(USD|HKD|EUR|CNY|JPY|GBP|RMB)\s*$", name.rstrip()):
        warnings.append(f"name ends with orphan currency code: '{name}'")
    # Double space in name (often indicates missing $ that was stripped)
    if "  " in name:
        warnings.append(f"name has double space (shell-escape artifact?): '{name}'")

    # Entry zone starts with "-" (missing leading value)
    if re.match(r"^\s*[-–]\s*\d", zone):
        warnings.append(f"entry_zone starts with dash (missing lower bound?): '{zone}'")
    # Entry zone has "HK-" or "USD-" — currency code fused with dash
    if re.search(r"\b(HK|USD|EUR|CNY|RMB)\s*[-–]\s*\d", zone):
        warnings.append(f"entry_zone fuses currency with dash (missing price?): '{zone}'")

    for w in warnings:
        print(f"[watchlist WARN] {entry.get('ticker', '?')}: {w}", file=sys.stderr)
    return warnings


def _load():
    if not WATCHLIST_PATH.exists():
        return {"meta": {"description": "Auto-managed watchlist", "last_updated": None}, "tickers": []}
    with open(WATCHLIST_PATH, "r") as f:
        return json.load(f)


def _save(data):
    # Validate all entries before save (warnings only, non-blocking)
    for entry in data.get("tickers", []):
        _validate_entry(entry)
    data["meta"]["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(WATCHLIST_PATH, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _find(data, ticker):
    for i, t in enumerate(data["tickers"]):
        if t["ticker"].upper() == ticker.upper():
            return i
    return -1


def cmd_add(args):
    data = _load()
    idx = _find(data, args.ticker)
    entry = {
        "ticker": args.ticker.upper(),
        "name": args.notes or "",
        "sector": args.sector or "",
        "stance": args.stance or "Watch",
        "thesis_break_conditions": args.breaks or [],
        "entry_zone": args.entry_zone or "",
        "last_analysis_date": args.last_analysis or datetime.now().strftime("%Y-%m-%d"),
        "added_date": datetime.now().strftime("%Y-%m-%d"),
        "last_health_flags": args.health_flags or [],
    }
    if idx >= 0:
        # Preserve added_date, update everything else
        entry["added_date"] = data["tickers"][idx].get("added_date", entry["added_date"])
        data["tickers"][idx] = entry
        action = "updated"
    else:
        data["tickers"].append(entry)
        action = "added"
    _save(data)
    print(f"[watchlist] {action}: {args.ticker.upper()} — stance={entry['stance']}, "
          f"breaks={len(entry['thesis_break_conditions'])}")


def cmd_remove(args):
    data = _load()
    idx = _find(data, args.ticker)
    if idx < 0:
        print(f"[watchlist] {args.ticker} not found", file=sys.stderr)
        sys.exit(1)
    removed = data["tickers"].pop(idx)
    _save(data)
    print(f"[watchlist] removed: {removed['ticker']}")


def cmd_list(args):
    data = _load()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"Watchlist — {len(data['tickers'])} tickers (updated: {data['meta'].get('last_updated', 'never')})")
    print(f"{'─' * 70}")
    for t in data["tickers"]:
        breaks = "; ".join(t.get("thesis_break_conditions", []))
        print(f"  {t['ticker']:12s} {t.get('stance','?'):15s} {t.get('name',''):20s} "
              f"last={t.get('last_analysis_date','?')}")
        if breaks:
            print(f"  {'':12s} breaks: {breaks}")
        if t.get("entry_zone"):
            print(f"  {'':12s} entry: {t['entry_zone']}")


def cmd_update(args):
    data = _load()
    idx = _find(data, args.ticker)
    if idx < 0:
        print(f"[watchlist] {args.ticker} not found", file=sys.stderr)
        sys.exit(1)
    if args.stance:
        data["tickers"][idx]["stance"] = args.stance
    if args.breaks:
        data["tickers"][idx]["thesis_break_conditions"] = args.breaks
    if args.entry_zone:
        data["tickers"][idx]["entry_zone"] = args.entry_zone
    if args.notes:
        data["tickers"][idx]["name"] = args.notes
    if hasattr(args, 'health_flags') and args.health_flags is not None:
        data["tickers"][idx]["last_health_flags"] = args.health_flags
    if hasattr(args, 'sector') and args.sector:
        data["tickers"][idx]["sector"] = args.sector
    data["tickers"][idx]["last_analysis_date"] = datetime.now().strftime("%Y-%m-%d")
    _save(data)
    print(f"[watchlist] updated: {args.ticker.upper()}")


def main():
    parser = argparse.ArgumentParser(description="Watchlist manager")
    sub = parser.add_subparsers(dest="command")

    p_add = sub.add_parser("add")
    p_add.add_argument("--ticker", required=True)
    p_add.add_argument("--stance", default="Watch")
    p_add.add_argument("--breaks", nargs="*", default=[])
    p_add.add_argument("--entry-zone", default="")
    p_add.add_argument("--last-analysis", default=None)
    p_add.add_argument("--notes", default="")
    p_add.add_argument("--sector", default="")
    p_add.add_argument("--health-flags", nargs="*", default=[])

    p_rm = sub.add_parser("remove")
    p_rm.add_argument("--ticker", required=True)

    p_ls = sub.add_parser("list")
    p_ls.add_argument("--json", action="store_true")

    p_up = sub.add_parser("update")
    p_up.add_argument("--ticker", required=True)
    p_up.add_argument("--stance", default=None)
    p_up.add_argument("--breaks", nargs="*", default=None)
    p_up.add_argument("--entry-zone", default=None)
    p_up.add_argument("--notes", default=None)
    p_up.add_argument("--health-flags", nargs="*", default=None)
    p_up.add_argument("--sector", default=None)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    {"add": cmd_add, "remove": cmd_remove, "list": cmd_list, "update": cmd_update}[args.command](args)


if __name__ == "__main__":
    main()
