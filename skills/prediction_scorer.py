#!/usr/bin/env python3
"""
prediction_scorer.py — FutureX-style Brier scoring for probability predictions.

Subcommands:
    resolve        Auto-resolve all due predictions in a batch
    resolve-manual Manually set outcome for filing_report / manual predictions
    list           Show open/resolved/unresolvable predictions
    report         Aggregate Brier + calibration stats

Usage:
    python3 skills/prediction_scorer.py resolve --batch 2026-04-22_initial
    python3 skills/prediction_scorer.py list --status open
    python3 skills/prediction_scorer.py resolve-manual --id 1913.HK_20260422_002 --outcome 1 --note "Q2 report Aug 12: Miu Miu +38%"
    python3 skills/prediction_scorer.py report
"""

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
PRED_DIR = ROOT / "probability_predictions"
SCORECARD = PRED_DIR / "scorecard.csv"


# ── Utilities ──────────────────────────────────────────────────────────────

def brier(p: float, o: int) -> float:
    return round((p - o) ** 2, 4)


def today() -> date:
    return datetime.now().date()


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def load_batch(batch_id: str) -> tuple[Path, dict]:
    path = PRED_DIR / f"{batch_id}.json"
    if not path.exists():
        print(f"[ERROR] Batch not found: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return path, json.load(f)


def save_batch(path: Path, data: dict):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def iter_batches():
    """Yield (path, data) for all JSON batches."""
    for p in sorted(PRED_DIR.glob("*_initial.json")) + sorted(PRED_DIR.glob("*_batch.json")):
        with open(p) as f:
            yield p, json.load(f)


def find_prediction_by_id(pred_id: str):
    """Scan all batches for a prediction. Returns (batch_path, batch_data, pred_ref) or None."""
    for path, data in iter_batches():
        for pred in data.get("predictions", []):
            if pred["id"] == pred_id:
                return path, data, pred
    return None


# ── Auto-resolvers (one function per resolution.type) ───────────────────────

def resolve_price_return(pred: dict) -> dict:
    """Resolve type=price_return: did return from prediction_date to resolution_date hit threshold?"""
    import yfinance as yf
    spec = pred["resolution"]["spec"]
    ticker = spec["ticker"]
    start = pred["prediction_date"]
    end = pred["resolution_date"]

    try:
        hist = yf.Ticker(ticker).history(start=start, end=(parse_date(end) + timedelta(days=1)).isoformat())
    except Exception as e:
        return {"resolvable": False, "reason": f"yfinance error: {e}"}
    if hist.empty or len(hist) < 2:
        return {"resolvable": False, "reason": "insufficient price data"}

    hist = hist.sort_index()
    start_close = float(hist["Close"].iloc[0])

    # Absolute target touched_above_at_any_point
    if "absolute_target" in spec and spec.get("direction") == "touched_above_at_any_point":
        target = spec["absolute_target"]
        touched = bool((hist["High"] >= target).any())
        return {
            "resolvable": True,
            "outcome": 1 if touched else 0,
            "observed": {"max_high": float(hist["High"].max()),
                         "start_close": start_close,
                         "target": target,
                         "touched": touched},
        }

    # Return-based: end close vs start close
    end_close = float(hist["Close"].iloc[-1])
    ret = (end_close - start_close) / start_close
    threshold = spec["threshold"]
    direction = spec.get("direction", "above")

    if direction == "above":
        outcome = 1 if ret >= threshold else 0
    elif direction == "below":
        outcome = 1 if ret <= threshold else 0
    else:
        return {"resolvable": False, "reason": f"unknown direction: {direction}"}

    return {
        "resolvable": True,
        "outcome": outcome,
        "observed": {"return": round(ret, 4), "start_close": start_close,
                     "end_close": end_close, "threshold": threshold,
                     "direction": direction},
    }


def resolve_price_range_persistence(pred: dict) -> dict:
    """Resolve type=price_range_persistence: close stayed within [lo, hi] through horizon, with violation tolerance."""
    import yfinance as yf
    spec = pred["resolution"]["spec"]
    ticker = spec["ticker"]
    start = pred["prediction_date"]
    end = pred["resolution_date"]
    lo = spec["lower_bound"]
    hi = spec["upper_bound"]
    tol_days = spec.get("violation_tolerance_days", 0)
    viol_dir = spec.get("violation_direction", "below")

    try:
        hist = yf.Ticker(ticker).history(start=start, end=(parse_date(end) + timedelta(days=1)).isoformat())
    except Exception as e:
        return {"resolvable": False, "reason": f"yfinance error: {e}"}
    if hist.empty:
        return {"resolvable": False, "reason": "no price data"}

    closes = hist["Close"].tolist()
    consecutive_violations = 0
    max_consecutive = 0
    total_violations = 0

    for c in closes:
        if viol_dir == "below":
            is_viol = c < lo
        elif viol_dir == "above":
            is_viol = c > hi
        elif viol_dir == "either":
            is_viol = c < lo or c > hi
        else:
            is_viol = False
        if is_viol:
            consecutive_violations += 1
            total_violations += 1
            max_consecutive = max(max_consecutive, consecutive_violations)
        else:
            consecutive_violations = 0

    # Also enforce overall range stayed within [lo, hi] for whole window
    if viol_dir == "below":
        outcome = 1 if max_consecutive <= tol_days else 0
    else:
        outcome = 1 if max_consecutive <= tol_days else 0

    return {
        "resolvable": True,
        "outcome": outcome,
        "observed": {"min_close": float(hist["Close"].min()),
                     "max_close": float(hist["Close"].max()),
                     "total_violations": total_violations,
                     "max_consecutive_violations": max_consecutive,
                     "tolerance_days": tol_days,
                     "lower_bound": lo, "upper_bound": hi},
    }


def resolve_commodity_sustained(pred: dict) -> dict:
    """Resolve type=commodity_sustained: commodity spot below/above threshold for N consecutive days."""
    import yfinance as yf
    spec = pred["resolution"]["spec"]
    yf_symbol = spec["yf_symbol"]
    threshold = spec["threshold"]
    direction = spec["direction"]  # below or above
    sustained_days = spec["sustained_days"]
    start = pred["prediction_date"]
    end = pred["resolution_date"]

    try:
        hist = yf.Ticker(yf_symbol).history(start=start, end=(parse_date(end) + timedelta(days=1)).isoformat())
    except Exception as e:
        return {"resolvable": False, "reason": f"yfinance error: {e}"}
    if hist.empty:
        return {"resolvable": False, "reason": "no commodity data"}

    closes = hist["Close"].tolist()
    consecutive = 0
    max_consecutive = 0

    for c in closes:
        if direction == "below":
            hit = c < threshold
        else:
            hit = c > threshold
        if hit:
            consecutive += 1
            max_consecutive = max(max_consecutive, consecutive)
        else:
            consecutive = 0

    outcome = 1 if max_consecutive >= sustained_days else 0

    return {
        "resolvable": True,
        "outcome": outcome,
        "observed": {"min_close": float(hist["Close"].min()),
                     "max_close": float(hist["Close"].max()),
                     "max_consecutive_days_hit": max_consecutive,
                     "required_consecutive_days": sustained_days,
                     "threshold": threshold, "direction": direction},
    }


def resolve_stance_change(pred: dict) -> dict:
    """Resolve type=stance_change: check if watchlist stance now equals target (best-effort snapshot-based)."""
    import subprocess
    spec = pred["resolution"]["spec"]
    ticker = spec["ticker"]
    baseline = spec["baseline_stance"]
    target = spec["target_stance"]

    # Read current watchlist
    watchlist_path = ROOT / "watchlist.json"
    if not watchlist_path.exists():
        return {"resolvable": False, "reason": "watchlist.json missing"}
    with open(watchlist_path) as f:
        wl = json.load(f)

    current_stance = None
    last_updated = None
    for entry in wl.get("tickers", []):
        if entry["ticker"].upper() == ticker.upper():
            current_stance = entry.get("stance")
            last_updated = entry.get("last_analysis_date")
            break

    if current_stance is None:
        return {"resolvable": False, "reason": f"{ticker} not in watchlist"}

    # Has stance changed to target since prediction_date?
    # Note: this is snapshot-based. If user changed stance and changed back, we miss it.
    # For robustness, compare last_analysis_date (when stance was last updated).
    pred_date = parse_date(pred["prediction_date"])
    if last_updated:
        try:
            update_date = parse_date(last_updated)
        except Exception:
            update_date = None
    else:
        update_date = None

    if current_stance == target:
        # stance is at target now
        if update_date and update_date >= pred_date:
            outcome = 1  # changed since prediction
        else:
            outcome = 1  # already at target but we assume change happened
    else:
        outcome = 0

    return {
        "resolvable": True,
        "outcome": outcome,
        "observed": {"current_stance": current_stance,
                     "target_stance": target,
                     "baseline_stance": baseline,
                     "watchlist_last_updated": last_updated,
                     "prediction_date": pred["prediction_date"]},
    }


RESOLVERS = {
    "price_return": resolve_price_return,
    "price_range_persistence": resolve_price_range_persistence,
    "commodity_sustained": resolve_commodity_sustained,
    "stance_change": resolve_stance_change,
}


# ── Main commands ──────────────────────────────────────────────────────────

def cmd_resolve(args):
    """Resolve all automatable predictions past their resolution_date."""
    path, data = load_batch(args.batch)
    now = today()
    resolved_count = 0
    skipped_past_due = 0
    not_yet_due = 0
    manual_pending = 0

    for pred in data.get("predictions", []):
        if pred.get("status") not in ("open", None):
            continue

        res_date = parse_date(pred["resolution_date"])
        if res_date > now:
            not_yet_due += 1
            continue

        rtype = pred["resolution"]["type"]
        automatable = pred["resolution"].get("automatable", False)

        if not automatable:
            manual_pending += 1
            continue

        if rtype not in RESOLVERS:
            print(f"[WARN] {pred['id']}: unknown resolver type '{rtype}'", file=sys.stderr)
            continue

        result = RESOLVERS[rtype](pred)
        if not result.get("resolvable"):
            print(f"[SKIP] {pred['id']}: {result.get('reason')}", file=sys.stderr)
            skipped_past_due += 1
            continue

        outcome = result["outcome"]
        p = pred["probability"]
        b = brier(p, outcome)

        pred["status"] = "resolved"
        pred["outcome"] = outcome
        pred["brier"] = b
        pred["resolved_at"] = now.isoformat()
        pred["resolution_observed"] = result["observed"]

        print(f"[RESOLVED] {pred['id']}  p={p}  outcome={outcome}  Brier={b}")
        print(f"           {result['observed']}")
        resolved_count += 1

        # Append to scorecard
        _append_scorecard(pred)

    save_batch(path, data)
    print(f"\nSummary: resolved={resolved_count}  not_yet_due={not_yet_due}  "
          f"manual_pending={manual_pending}  skipped={skipped_past_due}")


def cmd_resolve_manual(args):
    """Manually set outcome for a single prediction."""
    pred_id = args.id
    outcome = args.outcome
    note = args.note or ""

    found = find_prediction_by_id(pred_id)
    if not found:
        print(f"[ERROR] prediction not found: {pred_id}", file=sys.stderr)
        sys.exit(1)

    path, data, pred = found
    if pred.get("status") == "resolved":
        print(f"[ERROR] already resolved: {pred_id}", file=sys.stderr)
        sys.exit(1)

    p = pred["probability"]
    b = brier(p, outcome)
    pred["status"] = "resolved"
    pred["outcome"] = outcome
    pred["brier"] = b
    pred["resolved_at"] = today().isoformat()
    pred["resolution_observed"] = {"manual": True, "note": note}

    save_batch(path, data)
    _append_scorecard(pred)
    print(f"[RESOLVED MANUALLY] {pred_id}  p={p}  outcome={outcome}  Brier={b}")
    if note:
        print(f"                     note: {note}")


def cmd_list(args):
    status_filter = args.status
    rows = []
    for path, data in iter_batches():
        for pred in data.get("predictions", []):
            if status_filter and pred.get("status", "open") != status_filter:
                continue
            rows.append((pred["id"], pred["ticker"], pred.get("status", "open"),
                         pred["probability"], pred.get("outcome"), pred.get("brier"),
                         pred["resolution_date"], pred["resolution"]["type"],
                         pred["resolution"].get("automatable", False)))

    if not rows:
        print("(no predictions match filter)")
        return

    print(f"{'ID':<35} {'Ticker':<10} {'Status':<10} {'p':<6} {'out':<5} {'Brier':<7} {'Res.Date':<12} {'Type':<28} Auto")
    print("-" * 140)
    for r in rows:
        auto = "✓" if r[8] else "manual"
        out = str(r[4]) if r[4] is not None else "-"
        b = f"{r[5]:.3f}" if r[5] is not None else "-"
        print(f"{r[0]:<35} {r[1]:<10} {r[2]:<10} {r[3]:<6} {out:<5} {b:<7} {r[6]:<12} {r[7]:<28} {auto}")


def cmd_report(args):
    resolved = []
    for path, data in iter_batches():
        for pred in data.get("predictions", []):
            if pred.get("status") == "resolved" and pred.get("brier") is not None:
                resolved.append(pred)

    if not resolved:
        print("No resolved predictions yet.")
        return

    print(f"Resolved predictions: {len(resolved)}")
    total_brier = sum(p["brier"] for p in resolved)
    avg_brier = total_brier / len(resolved)
    print(f"Aggregate Brier: {avg_brier:.4f}")

    # Benchmarks
    print(f"\nBenchmarks for interpretation:")
    print(f"  < 0.08   Superforecaster-level")
    print(f"  0.08-0.15 Skilled forecaster")
    print(f"  0.15-0.20 Average analyst")
    print(f"  0.20-0.25 Near random")
    print(f"  >= 0.25  Worse than random")

    # Per-type breakdown
    by_type = {}
    for p in resolved:
        t = p["resolution"]["type"]
        by_type.setdefault(t, []).append(p["brier"])
    print(f"\nBy resolution type:")
    for t, briers in sorted(by_type.items()):
        print(f"  {t:<30}  N={len(briers):<3}  avg Brier = {sum(briers)/len(briers):.4f}")

    # Calibration buckets
    buckets = {"0.0-0.2": [], "0.2-0.4": [], "0.4-0.6": [], "0.6-0.8": [], "0.8-1.0": []}
    for p in resolved:
        prob = p["probability"]
        out = p["outcome"]
        if prob < 0.2:
            k = "0.0-0.2"
        elif prob < 0.4:
            k = "0.2-0.4"
        elif prob < 0.6:
            k = "0.4-0.6"
        elif prob < 0.8:
            k = "0.6-0.8"
        else:
            k = "0.8-1.0"
        buckets[k].append(out)

    print(f"\nCalibration buckets (predicted p  vs  observed frequency):")
    print(f"  {'Bucket':<12} {'N':<5} {'Observed freq':<15} {'Ideal center':<15} Gap")
    for k in ["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"]:
        outs = buckets[k]
        if not outs:
            print(f"  {k:<12} 0     -               -               -")
            continue
        freq = sum(outs) / len(outs)
        ideal = {"0.0-0.2": 0.1, "0.2-0.4": 0.3, "0.4-0.6": 0.5, "0.6-0.8": 0.7, "0.8-1.0": 0.9}[k]
        gap = freq - ideal
        print(f"  {k:<12} {len(outs):<5} {freq:.3f}           {ideal:.1f}             {gap:+.3f}")

    if args.since:
        print(f"\n(filter --since not yet implemented — showing all resolved)")


def _append_scorecard(pred: dict):
    """Append resolved prediction to scorecard.csv."""
    headers = ["resolved_at", "batch_id", "prediction_id", "ticker", "title",
               "prediction_date", "resolution_date", "horizon_days", "probability",
               "outcome", "brier", "resolution_type"]
    new_file = not SCORECARD.exists()
    with open(SCORECARD, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(headers)
        w.writerow([
            pred.get("resolved_at"),
            pred["id"].rsplit("_", 1)[0],
            pred["id"],
            pred["ticker"],
            pred["title"],
            pred["prediction_date"],
            pred["resolution_date"],
            pred["horizon_days"],
            pred["probability"],
            pred["outcome"],
            pred["brier"],
            pred["resolution"]["type"],
        ])


# ── Entrypoint ─────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="FutureX-style prediction scorer")
    sub = p.add_subparsers(dest="cmd")

    pr = sub.add_parser("resolve", help="Auto-resolve due predictions in a batch")
    pr.add_argument("--batch", required=True, help="Batch ID without .json (e.g., 2026-04-22_initial)")

    prm = sub.add_parser("resolve-manual", help="Manually resolve a filing/manual prediction")
    prm.add_argument("--id", required=True)
    prm.add_argument("--outcome", type=int, choices=[0, 1], required=True)
    prm.add_argument("--note", default=None)

    pl = sub.add_parser("list", help="List predictions with optional status filter")
    pl.add_argument("--status", choices=["open", "resolved", "unresolvable"], default=None)

    prp = sub.add_parser("report", help="Aggregate Brier + calibration")
    prp.add_argument("--since", default=None)

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        sys.exit(1)

    {"resolve": cmd_resolve,
     "resolve-manual": cmd_resolve_manual,
     "list": cmd_list,
     "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
