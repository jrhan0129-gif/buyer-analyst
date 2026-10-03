#!/usr/bin/env python3
"""
performance_tracker.py — Track watchlist stance accuracy vs actual market performance.

Compares PM stance (Buy/Watch/Avoid) and entry zone against actual price movement
since the analysis date. Generates a scorecard showing which calls were right/wrong.

Usage:
    python3 skills/performance_tracker.py [--json]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

WATCHLIST_PATH = Path(__file__).parent.parent / "watchlist.json"


def load_watchlist():
    with open(WATCHLIST_PATH) as f:
        return json.load(f)


def get_price_at_date(ticker, date_str):
    """Get closing price on or near a specific date."""
    if not YF_AVAILABLE:
        return None
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(start=date_str, period="5d")
        if not hist.empty:
            return float(hist["Close"].iloc[0])
    except Exception:
        pass
    return None


def get_current_price(ticker):
    """Get current price."""
    if not YF_AVAILABLE:
        return None
    try:
        info = yf.Ticker(ticker).info or {}
        return info.get("currentPrice") or info.get("regularMarketPrice")
    except Exception:
        return None


def parse_entry_zone(entry_str):
    """Extract numeric range from entry zone string."""
    import re
    numbers = re.findall(r'[\d,]+\.?\d*', entry_str.replace(",", ""))
    if len(numbers) >= 2:
        return float(numbers[0]), float(numbers[1])
    elif len(numbers) == 1:
        return float(numbers[0]), float(numbers[0])
    return None, None


def evaluate_stance(stance, entry_low, entry_high, price_at_analysis, current_price):
    """Evaluate if the stance call was directionally correct."""
    if price_at_analysis is None or current_price is None:
        return "DATA_UNAVAILABLE", 0

    pct_change = (current_price - price_at_analysis) / price_at_analysis * 100

    if stance == "Buy":
        if pct_change > 5:
            return "CORRECT", pct_change
        elif pct_change < -10:
            return "WRONG", pct_change
        else:
            return "NEUTRAL", pct_change

    elif stance == "Avoid":
        if pct_change < -5:
            return "CORRECT", pct_change  # Avoided a decline
        elif pct_change > 15:
            return "WRONG", pct_change  # Missed a rally
        else:
            return "NEUTRAL", pct_change

    elif stance == "Watch":
        # Watch is correct if price moved toward entry zone
        if entry_low and current_price <= entry_high:
            return "ENTRY_REACHED", pct_change
        else:
            return "WAITING", pct_change

    return "UNKNOWN", pct_change


def main():
    parser = argparse.ArgumentParser(description="Track watchlist performance vs stance")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    data = load_watchlist()
    results = []

    for t in data["tickers"]:
        ticker = t["ticker"]
        stance = t.get("stance", "Unknown")
        entry_zone = t.get("entry_zone", "")
        analysis_date = t.get("last_analysis_date", "")

        entry_low, entry_high = parse_entry_zone(entry_zone)
        price_at_analysis = get_price_at_date(ticker, analysis_date) if analysis_date else None
        current_price = get_current_price(ticker)

        verdict, pct_change = evaluate_stance(
            stance, entry_low, entry_high, price_at_analysis, current_price
        )

        results.append({
            "ticker": ticker,
            "name": t.get("name", ""),
            "stance": stance,
            "entry_zone": entry_zone,
            "analysis_date": analysis_date,
            "price_at_analysis": round(price_at_analysis, 2) if price_at_analysis else None,
            "current_price": round(current_price, 2) if current_price else None,
            "pct_change": round(pct_change, 2),
            "verdict": verdict,
        })

    # Score
    correct = sum(1 for r in results if r["verdict"] == "CORRECT")
    wrong = sum(1 for r in results if r["verdict"] == "WRONG")
    neutral = sum(1 for r in results if r["verdict"] in ("NEUTRAL", "WAITING"))
    entry_reached = sum(1 for r in results if r["verdict"] == "ENTRY_REACHED")
    total = len(results)

    if args.json:
        print(json.dumps({"results": results, "score": {"correct": correct, "wrong": wrong,
                          "neutral": neutral, "entry_reached": entry_reached, "total": total}},
                         indent=2, default=str))
        return

    print(f"\n{'='*70}")
    print(f"  PERFORMANCE TRACKER — {datetime.now().strftime('%Y-%m-%d')}")
    print(f"  Score: {correct} correct / {wrong} wrong / {neutral} pending / {entry_reached} entry reached")
    print(f"{'='*70}\n")

    for r in results:
        icon = {"CORRECT": "✅", "WRONG": "❌", "NEUTRAL": "⏳", "WAITING": "⏳",
                "ENTRY_REACHED": "🎯", "DATA_UNAVAILABLE": "❓"}.get(r["verdict"], "?")

        chg_str = f"{r['pct_change']:+.1f}%" if r['pct_change'] else "N/A"
        price_str = f"${r['current_price']}" if r['current_price'] else "N/A"
        analysis_price = f"${r['price_at_analysis']}" if r['price_at_analysis'] else "N/A"

        print(f"  {icon} {r['ticker']:10s} {r['stance']:8s} | "
              f"Analysis: {analysis_price} → Now: {price_str} ({chg_str}) | "
              f"{r['verdict']}")
        if r["entry_zone"]:
            print(f"     Entry zone: {r['entry_zone']}")

    print(f"\n{'─'*70}")
    print(f"  Framework accuracy: {correct}/{total} correct calls")
    if wrong > 0:
        wrong_tickers = [r["ticker"] for r in results if r["verdict"] == "WRONG"]
        print(f"  Wrong calls: {', '.join(wrong_tickers)} — review thesis break conditions")
    print()


if __name__ == "__main__":
    main()
